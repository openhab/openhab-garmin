"""Compose stack validation and image pinning.

Docker is not required to validate the stack. ``validate_compose`` reads the
Compose files and checks the properties the harness depends on: the service
set, profiles, the read-only fixture mounts and the staging step that copies
them into the conf volume, the dedicated volumes, the shim's upstream wiring,
the runner's SDK mount, the writable artifacts mount, that no image can be
pulled without a digest, and that every published port is bound to the loopback
address.

Two rules exist because the first live run on a real host broke on them, and
neither needs Docker to check:

- no mount target may sit inside a read-only mount of the same service (Docker
  has to create the mountpoint, which fails with "read-only file system");
- no read-only mount may sit under ``/openhab`` (the image entrypoint runs
  ``chown -R openhab:openhab /openhab`` under ``set -e`` and aborts).

There are two deployment profiles:

- the default bridge stack (``compose.yaml``), which publishes loopback-only
  host ports so it stays usable on a developer machine;
- the host-network overlay (``compose.host.yaml``, plus
  ``compose.host.dev.yaml`` for the dev profile), which runs every service in
  the host network namespace and addresses peers as ``http://127.0.0.1:<port>``.

Ports and URLs are never hardcoded here: they come from
``tests/config/network.yaml`` through the generated block in
``tests/compose/.env.example``.

``pin-images`` is the one command that needs the network: it resolves
multi-architecture digests from Docker Hub and writes them into
``tests/compose/.env``. ``--check`` verifies the pins without writing.
"""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

from .config import COMPOSE_DIR, ConfigError, ImagePin, openhab_images
from .network import (
    ENV_LOOPBACK_HOST,
    ENV_PORTS,
    Network,
    check_env_block,
    load_network,
)

COMPOSE_FILE = COMPOSE_DIR / "compose.yaml"
DEV_OVERRIDE_FILE = COMPOSE_DIR / "compose.dev.yaml"
HOST_OVERRIDE_FILE = COMPOSE_DIR / "compose.host.yaml"
HOST_DEV_OVERRIDE_FILE = COMPOSE_DIR / "compose.host.dev.yaml"
ENV_FILE = COMPOSE_DIR / ".env"
ENV_EXAMPLE = COMPOSE_DIR / ".env.example"

REQUIRED_SERVICES = ("openhab-stable", "openhab-dev", "shim", "runner")

#: One-shot services that stage the fixture tree into an openHAB conf volume.
#: openHAB's entrypoint runs `chown -R openhab:openhab /openhab` under `set -e`,
#: so a read-only mount anywhere underneath /openhab aborts the container. The
#: fixtures therefore have to be copied into the volume, not mounted into it.
FIXTURE_STAGING = {
    "openhab-stable": "openhab-stable-init",
    "openhab-dev": "openhab-dev-init",
}

#: Marker that identifies a mount source as this repository's fixtures.
FIXTURE_SOURCE_MARKER = "fixtures/"

#: openHAB's home directory; the image entrypoint chowns it recursively.
OPENHAB_HOME = "/openhab"

#: Container path the runner writes its artifacts to, and the env var naming it.
ARTIFACTS_ENV = "E2E_ARTIFACTS"
ARTIFACTS_VOLUME = "e2e-artifacts"

#: Source of the optional device-definitions mount; an empty named volume when
#: the operator has not set ``CIQ_DEVICES_HOST_PATH``.
DEVICES_MOUNT_TARGET = "/opt/ciq-devices"

REQUIRED_VOLUMES = (
    "openhab-stable-conf",
    "openhab-stable-userdata",
    "openhab-dev-conf",
    "openhab-dev-userdata",
    ARTIFACTS_VOLUME,
    "e2e-empty-devices",
)
REQUIRED_ENV_KEYS = (
    "COMPOSE_PROFILES",
    "OPENHAB_STABLE_IMAGE",
    "OPENHAB_DEV_IMAGE",
    "RUNNER_BASE_IMAGE",
    "SHIM_BASE_IMAGE",
    "CIQ_SDK_HOST_PATH",
    "CIQ_DEVICES_HOST_PATH",
    ENV_LOOPBACK_HOST,
    *ENV_PORTS.values(),
)

IMAGE_ENV_KEYS = {
    "openhab_stable": "OPENHAB_STABLE_IMAGE",
    "openhab_dev": "OPENHAB_DEV_IMAGE",
    "runner_base": "RUNNER_BASE_IMAGE",
    "shim_base": "SHIM_BASE_IMAGE",
}

DOCKER_HUB_TAG_URL = "https://hub.docker.com/v2/repositories/{repository}/tags/{tag}"

#: Compose variable interpolation: ``$$``, ``${VAR}``, ``${VAR:-default}``,
#: ``${VAR-default}``, ``${VAR:?message}`` and ``${VAR?message}``.
_INTERPOLATION = re.compile(
    r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?])([^}]*))?\}"
)

#: openHAB binds all interfaces by default. The host overlay narrows it.
LISTENING_ADDRESS_PROPERTY = "org.ops4j.pax.web.listening.address"


class ComposeError(RuntimeError):
    """Raised when the Compose stack cannot be read or is misconfigured."""


class ComposeLoader(yaml.SafeLoader):
    """A ``SafeLoader`` that understands Compose's merge tags.

    ``!reset`` removes an inherited value (a list or a mapping) and
    ``!override`` replaces it instead of merging. Both appear in the host
    overlay, which must drop the bridge profile's published ports and network
    attachments. Docker Compose 2.24+ interprets the tags itself; this loader
    exists so ``python -m e2e validate-compose`` and the unit tests can read
    the same documents without Docker.
    """


def _construct_reset(loader: ComposeLoader, node: yaml.Node) -> object:
    if isinstance(node, yaml.SequenceNode):
        return []
    if isinstance(node, yaml.MappingNode):
        return {}
    return None


def _construct_override(loader: ComposeLoader, node: yaml.Node) -> object:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


ComposeLoader.add_constructor("!reset", _construct_reset)
ComposeLoader.add_constructor("!override", _construct_override)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def load_compose(path: Path = COMPOSE_FILE) -> Mapping[str, object]:
    if not path.is_file():
        raise ComposeError(f"compose file not found: {path}")
    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=ComposeLoader)
    except yaml.YAMLError as error:
        raise ComposeError(f"{path}: invalid YAML: {error}") from None
    if not isinstance(document, Mapping):
        raise ComposeError(f"{path}: expected a mapping at the top level")
    return document


def read_env_file(path: Path) -> Dict[str, str]:
    """Read a ``KEY=value`` file, ignoring comments and blank lines."""
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def interpolate(text: str, env: Mapping[str, str]) -> str:
    """Apply Compose-style variable interpolation to ``text``."""
    def replace(match: "re.Match[str]") -> str:
        token = match.group(0)
        if token == "$$":
            return "$"
        name = match.group(1)
        operator = match.group(2)
        argument = match.group(3) or ""
        value = env.get(name)
        if operator == ":-":
            return value if value else argument
        if operator == "-":
            return value if value is not None else argument
        if operator == ":?":
            if not value:
                raise ComposeError(f"{name} is required: {argument or 'set it in tests/compose/.env'}")
            return value
        if operator == "?":
            if value is None:
                raise ComposeError(f"{name} is required: {argument or 'set it in tests/compose/.env'}")
            return value
        return value or ""

    return _INTERPOLATION.sub(replace, text)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: object) -> Sequence[object]:
    return value if isinstance(value, list) else []


def _mounts(service: Mapping[str, object]) -> List[str]:
    return [str(mount) for mount in _as_list(service.get("volumes"))]


@dataclass(frozen=True)
class Mount:
    """One entry of a service's ``volumes`` list, parsed.

    Docker cannot create a nested mountpoint on a read-only file system, and
    openHAB's entrypoint cannot chown a read-only bind mount, so the validator
    needs the source, target and mode of every mount: those two properties are
    what the live stack failed on.
    """

    source: str
    target: str
    read_only: bool


#: ``${VAR}``/``${VAR:-default}``/``${VAR:?message}``; masked before the mount
#: string is split, because the default itself may contain colons.
_MOUNT_VARIABLE = re.compile(r"\$\{[^}]*\}")


def _split_mount(entry: str) -> Tuple[str, str, str]:
    """Split ``[SOURCE:]TARGET[:MODE]`` without tripping over interpolation."""
    masked = _MOUNT_VARIABLE.sub(lambda match: "\x00" * len(match.group(0)), entry)
    positions = [index for index, character in enumerate(masked) if character == ":"]
    if not positions:
        return "", entry, ""
    if len(positions) == 1:
        return entry[: positions[0]], entry[positions[0] + 1 :], ""
    target_start, mode_start = positions[-2], positions[-1]
    return entry[:target_start], entry[target_start + 1 : mode_start], entry[mode_start + 1 :]


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().strip('"').lower() in ("true", "1", "yes")


def service_mounts(service: Mapping[str, object]) -> List[Mount]:
    """Parse a service's mounts, both the short and the long syntax."""
    mounts: List[Mount] = []
    for raw in _as_list(service.get("volumes")):
        if isinstance(raw, Mapping):
            mounts.append(
                Mount(
                    source=str(raw.get("source") or ""),
                    target=str(raw.get("target") or ""),
                    read_only=_as_bool(raw.get("read_only")),
                )
            )
            continue
        source, target, mode = _split_mount(str(raw))
        flags = {flag.strip().lower() for flag in mode.split(",") if flag.strip()}
        mounts.append(Mount(source=source, target=target, read_only="ro" in flags))
    return mounts


def _normalise_target(path: str) -> str:
    text = str(path).strip()
    if not text:
        return ""
    if not text.startswith("/"):
        text = "/" + text
    stripped = text.rstrip("/")
    return stripped or "/"


def _is_under(path: str, parent: str) -> bool:
    """True when ``path`` equals ``parent`` or lives below it."""
    child = _normalise_target(path)
    root = _normalise_target(parent)
    if not child or not root:
        return False
    if root == "/":
        return child != "/"
    return child == root or child.startswith(root + "/")


def _is_strictly_under(path: str, parent: str) -> bool:
    child = _normalise_target(path)
    root = _normalise_target(parent)
    return bool(child) and child != root and _is_under(child, root)


def mount_problems(
    services: Mapping[str, object],
    env: Mapping[str, str],
) -> List[str]:
    """Mount rules the live stack broke, checked without Docker.

    - No mount target may sit inside a read-only mount of the same service:
      Docker has to create the mountpoint, which fails with
      ``mkdirat ... /work/artifacts: read-only file system``.
    - Every fixture mount must be read-only: the checkout is never written.
    - No read-only mount may sit under /openhab: the image entrypoint runs
      ``chown -R openhab:openhab /openhab`` under ``set -e`` and aborts.
    """
    problems: List[str] = []
    for name, raw in services.items():
        service = _as_mapping(raw)
        mounts = service_mounts(service)
        read_only_targets = [mount.target for mount in mounts if mount.read_only]
        for mount in mounts:
            for read_only in read_only_targets:
                if _is_strictly_under(mount.target, read_only):
                    problems.append(
                        f"services.{name}: volume target {mount.target} is inside the "
                        f"read-only mount {read_only}; Docker cannot create the "
                        "mountpoint on a read-only file system"
                    )
        for mount in mounts:
            if FIXTURE_SOURCE_MARKER in mount.source and not mount.read_only:
                problems.append(
                    f"services.{name}: fixture mount {mount.source} must be read-only "
                    "(:ro); fixtures are the source of truth and the checkout is never "
                    "written by the stack"
                )
        if name in FIXTURE_STAGING:
            for mount in mounts:
                if mount.read_only and _is_under(mount.target, OPENHAB_HOME):
                    problems.append(
                        f"services.{name}: {mount.target} is a read-only mount under "
                        f"{OPENHAB_HOME}; the image's entrypoint runs "
                        "`chown -R openhab:openhab /openhab` and aborts with "
                        "\"Read-only file system\". Stage the fixtures into the conf "
                        f"volume with {FIXTURE_STAGING[name]} instead"
                    )
    return problems


def artifacts_mount_problems(
    services: Mapping[str, object],
    env: Mapping[str, str],
) -> List[str]:
    """``E2E_ARTIFACTS`` must point at a writable mount of the runner."""
    runner = _as_mapping(services.get("runner"))
    environment = _as_mapping(runner.get("environment"))
    raw = environment.get(ARTIFACTS_ENV)
    if raw is None:
        return [f"services.runner must set {ARTIFACTS_ENV}"]

    problems: List[str] = []
    try:
        value = interpolate(str(raw), env).strip()
    except ComposeError as error:
        return [f"services.runner.{ARTIFACTS_ENV} cannot be resolved: {error}"]
    if not value.startswith("/"):
        return [
            f"services.runner.{ARTIFACTS_ENV} must be an absolute container path, "
            f"got {value!r}"
        ]

    mounts = service_mounts(runner)
    for mount in mounts:
        if mount.read_only and _is_under(value, mount.target):
            problems.append(
                f"services.runner.{ARTIFACTS_ENV}={value} is inside the read-only "
                f"mount {mount.target}; the runner cannot write its artifacts there"
            )
    writable = [
        mount.target
        for mount in mounts
        if not mount.read_only and _is_under(value, mount.target)
    ]
    if not writable:
        problems.append(
            f"services.runner.{ARTIFACTS_ENV}={value} is not covered by a writable "
            "volume; mount the artifacts volume at that path"
        )
    if not any(mount.source == ARTIFACTS_VOLUME for mount in mounts):
        problems.append(
            f"services.runner must mount the {ARTIFACTS_VOLUME} volume"
        )
    return problems


def staging_problems(services: Mapping[str, object]) -> List[str]:
    """Every openHAB instance needs its fixture staging step in front of it."""
    problems: List[str] = []
    for openhab, init in FIXTURE_STAGING.items():
        service = _as_mapping(services.get(openhab))
        staging = _as_mapping(services.get(init))
        if not staging:
            problems.append(
                f"services.{init} is missing; {openhab} cannot mount the fixture tree "
                "read-only, so the fixtures have to be staged into its conf volume"
            )
            continue

        profiles = [str(profile) for profile in _as_list(service.get("profiles"))]
        staging_profiles = [str(profile) for profile in _as_list(staging.get("profiles"))]
        if staging_profiles != profiles:
            problems.append(
                f"services.{init}.profiles must match services.{openhab}.profiles "
                f"{profiles}, got {staging_profiles}"
            )

        expected_image = (
            "OPENHAB_STABLE_IMAGE" if openhab == "openhab-stable" else "OPENHAB_DEV_IMAGE"
        )
        image = _image(staging)
        if f"${{{expected_image}" not in image:
            problems.append(
                f"services.{init}.image must reference ${{{expected_image}}} so the "
                f"staging step runs the same pinned image as {openhab}"
            )

        restart = str(staging.get("restart", "no")).strip().strip('"').lower()
        if restart not in ("no", "false"):
            problems.append(
                f"services.{init}.restart must be \"no\": it is a one-shot step"
            )

        mounts = service_mounts(staging)
        conf_volume = f"{openhab}-conf"
        if not any(
            mount.source == conf_volume
            and _normalise_target(mount.target) == "/openhab/conf"
            for mount in mounts
        ):
            problems.append(
                f"services.{init} must mount the {conf_volume} volume at /openhab/conf"
            )
        if not any(
            FIXTURE_SOURCE_MARKER in mount.source and mount.read_only
            for mount in mounts
        ):
            problems.append(
                f"services.{init} must mount the fixture tree read-only at /fixtures"
            )
        if not any(
            "seed-openhab-conf" in mount.source and mount.read_only
            for mount in mounts
        ):
            problems.append(
                f"services.{init} must mount seed-openhab-conf.sh read-only"
            )

        depends = _as_mapping(service.get("depends_on"))
        entry = depends.get(init)
        condition = _as_mapping(entry).get("condition") if entry is not None else None
        if condition != "service_completed_successfully":
            problems.append(
                f"services.{openhab} must depend on services.{init} with "
                "`condition: service_completed_successfully`; otherwise openHAB starts "
                "before its fixtures exist and its entrypoint aborts"
            )
    return problems


def _image(service: Mapping[str, object]) -> str:
    return str(service.get("image", ""))


def _is_loopback(host: str) -> bool:
    text = host.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if text == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _port_binding(entry: object, env: Mapping[str, str]) -> "tuple[str, str]":
    """Return ``(host_ip, published_port)`` for one ``ports`` entry.

    Accepts both the short string form (``[ip:]published:target[/proto]``) and
    the long mapping form (``host_ip``/``published``). An empty host IP means
    Docker's default wildcard bind.
    """
    if isinstance(entry, Mapping):
        return str(entry.get("host_ip") or ""), str(entry.get("published") or "")
    spec = interpolate(str(entry), env).split("/", 1)[0]
    parts = spec.rsplit(":", 2)
    if len(parts) == 3:
        return parts[0], parts[1]
    if len(parts) == 2:
        return "", parts[0]
    return "", parts[0] if parts else ""


def published_port_problems(
    services: Mapping[str, object],
    env: Mapping[str, str],
) -> List[str]:
    """Every published port must bind a loopback address explicitly."""
    problems: List[str] = []
    for name, raw in services.items():
        service = _as_mapping(raw)
        for entry in _as_list(service.get("ports")):
            try:
                host_ip, published = _port_binding(entry, env)
            except ComposeError as error:
                problems.append(f"services.{name}: cannot resolve published port {entry!r}: {error}")
                continue
            if not host_ip:
                problems.append(
                    f"services.{name}: published port {published!r} has no host IP; "
                    "bind the loopback address from tests/config/network.yaml"
                )
            elif not _is_loopback(host_ip):
                problems.append(
                    f"services.{name}: published port {published!r} binds {host_ip!r}, "
                    "not loopback"
                )
    return problems


def validate_compose_document(
    document: Mapping[str, object],
    env: Optional[Mapping[str, str]] = None,
) -> List[str]:
    """Return a list of problems; an empty list means the stack is valid."""
    problems: List[str] = []
    services = _as_mapping(document.get("services"))
    volumes = _as_mapping(document.get("volumes"))
    env = env or {}

    for name in REQUIRED_SERVICES:
        if name not in services:
            problems.append(f"services.{name} is missing")

    if problems:
        return problems

    stable = _as_mapping(services["openhab-stable"])
    dev = _as_mapping(services["openhab-dev"])
    shim = _as_mapping(services["shim"])
    runner = _as_mapping(services["runner"])

    # -- openHAB ---------------------------------------------------------
    for name, service, expected_profile, env_key, conf_volume, userdata_volume in (
        ("openhab-stable", stable, "stable", "OPENHAB_STABLE_IMAGE",
         "openhab-stable-conf", "openhab-stable-userdata"),
        ("openhab-dev", dev, "dev", "OPENHAB_DEV_IMAGE",
         "openhab-dev-conf", "openhab-dev-userdata"),
    ):
        profiles = [str(profile) for profile in _as_list(service.get("profiles"))]
        if profiles != [expected_profile]:
            problems.append(
                f"services.{name}.profiles must be exactly ['{expected_profile}'], got {profiles}"
            )
        image = _image(service)
        if f"${{{env_key}" not in image:
            problems.append(
                f"services.{name}.image must reference ${{{env_key}}} so the pin is not inline"
            )
        mounts = _mounts(service)
        if not any(mount.startswith(conf_volume + ":") for mount in mounts):
            problems.append(
                f"services.{name} must mount the writable conf volume {conf_volume} "
                "(openHAB creates files under conf)"
            )
        if not any(mount.startswith(userdata_volume + ":") for mount in mounts):
            problems.append(f"services.{name} must mount its own userdata volume {userdata_volume}")
        for fixture in ("fixtures/openhab/conf/items", "fixtures/openhab/conf/sitemaps"):
            matching = [mount for mount in mounts if fixture in mount]
            if matching:
                problems.append(
                    f"services.{name} must not mount {fixture}: the image's entrypoint "
                    "runs `chown -R openhab:openhab /openhab` and aborts on a read-only "
                    f"mount; {FIXTURE_STAGING[name]} stages the fixtures into the conf "
                    "volume before this service starts"
                )
        if service.get("network_mode") == "host":
            problems.append(f"services.{name} must not use host networking")
        healthcheck = _as_mapping(service.get("healthcheck"))
        test = healthcheck.get("test")
        if not test:
            problems.append(f"services.{name} needs a healthcheck")
        else:
            text = " ".join(str(part) for part in test) if isinstance(test, list) else str(test)
            if "curl" not in text:
                problems.append(
                    f"services.{name}.healthcheck must use curl: it is present in the "
                    "openHAB image, while wget is not"
                )
        if str(_mounts(service)) == str(_mounts(dev if name == "openhab-stable" else stable)):
            problems.append("openhab-stable and openhab-dev must not share volumes")

    # -- shim ------------------------------------------------------------
    shim_build = _as_mapping(shim.get("build"))
    if not str(shim_build.get("context", "")).endswith(".."):
        problems.append("services.shim.build.context must be the tests/ directory (..)")
    shim_env = _as_mapping(shim.get("environment"))
    if "OPENHAB_UPSTREAM" not in shim_env:
        problems.append("services.shim must set OPENHAB_UPSTREAM")
    elif "openhab-stable" not in str(shim_env["OPENHAB_UPSTREAM"]):
        problems.append(
            "services.shim.OPENHAB_UPSTREAM must default to the openhab-stable service"
        )
    if not shim_env.get("SHIM_VERSION"):
        problems.append("services.shim must set SHIM_VERSION")

    # -- runner ----------------------------------------------------------
    runner_build = _as_mapping(runner.get("build"))
    if str(runner_build.get("dockerfile", "")) != "runner/Dockerfile":
        problems.append("services.runner.build.dockerfile must be runner/Dockerfile")
    sdk_mounts = [mount for mount in _mounts(runner) if "ciq-sdk" in mount]
    if not sdk_mounts:
        problems.append("services.runner must mount the Connect IQ SDK at /opt/ciq-sdk")
    elif not all(mount.endswith(":ro") for mount in sdk_mounts):
        problems.append("services.runner must mount the Connect IQ SDK read-only (:ro)")
    devices_mounts = [
        mount
        for mount in service_mounts(runner)
        if _normalise_target(mount.target) == DEVICES_MOUNT_TARGET
    ]
    if not devices_mounts:
        problems.append(
            f"services.runner must mount device definitions at {DEVICES_MOUNT_TARGET}; "
            "the optional CIQ_DEVICES_HOST_PATH mount falls back to an empty volume"
        )
    elif not all(mount.read_only for mount in devices_mounts):
        problems.append(
            f"services.runner must mount {DEVICES_MOUNT_TARGET} read-only (:ro)"
        )
    problems.extend(artifacts_mount_problems(services, env))
    runner_env = _as_mapping(runner.get("environment"))
    for key in ("CIQ_SDK_MOUNT", "E2E_OPENHAB_URL", "E2E_SHIM_URL"):
        if key not in runner_env:
            problems.append(f"services.runner must set {key}")

    # -- fixtures, staging and mount safety ------------------------------
    problems.extend(mount_problems(services, env))
    problems.extend(staging_problems(services))

    # -- networking and hygiene ------------------------------------------
    problems.extend(published_port_problems(services, env))

    networks = _as_mapping(document.get("networks"))
    if not networks:
        problems.append("a named bridge network is required for the stack")
    else:
        any_bridge = any(
            _as_mapping(network).get("driver", "bridge") == "bridge"
            for network in networks.values()
        )
        if not any_bridge:
            problems.append("at least one network must use the bridge driver")

    for name, service in (_as_mapping(services)).items():
        if _as_mapping(service).get("privileged"):
            problems.append(f"services.{name} must not be privileged")
        if "docker.sock" in str(_as_list(_as_mapping(service).get("volumes"))):
            problems.append(f"services.{name} must not mount the Docker socket")

    for name in REQUIRED_VOLUMES:
        if name not in volumes:
            problems.append(f"volumes.{name} is missing")

    # -- environment file ------------------------------------------------
    for key in REQUIRED_ENV_KEYS:
        if key not in env:
            problems.append(f".env.example is missing {key}")
    if env.get("COMPOSE_PROFILES") != "stable":
        problems.append(".env.example must default COMPOSE_PROFILES to stable")

    return problems


def validate_dev_override(path: Path = DEV_OVERRIDE_FILE) -> List[str]:
    """Validate the ``dev`` profile override file.

    The override exists so the shim cannot silently proxy to a service that the
    ``dev`` profile does not start.
    """
    problems: List[str] = []
    if not path.is_file():
        return [f"missing dev override file: {path}"]
    document = load_compose(path)
    services = _as_mapping(document.get("services"))
    if not services:
        return [f"{path}: no services defined"]

    shim_env = _as_mapping(_as_mapping(services.get("shim")).get("environment"))
    upstream = str(shim_env.get("OPENHAB_UPSTREAM", ""))
    if "openhab-dev" not in upstream:
        problems.append(
            f"{path}: services.shim.environment.OPENHAB_UPSTREAM must point at openhab-dev "
            f"(got {upstream!r}); otherwise the dev stack proxies to a service that is not running"
        )

    runner_env = _as_mapping(_as_mapping(services.get("runner")).get("environment"))
    runner_url = str(runner_env.get("E2E_OPENHAB_URL", ""))
    if "openhab-dev" not in runner_url:
        problems.append(f"{path}: services.runner.environment.E2E_OPENHAB_URL must point at openhab-dev")
    return problems


def _network_or_problem() -> "Network | List[str]":
    try:
        return load_network()
    except ConfigError as error:
        return [str(error)]


def _host_service_problems(
    path: Path,
    services: Mapping[str, object],
) -> List[str]:
    """Structural checks shared by the host overlays."""
    problems: List[str] = []
    for name in REQUIRED_SERVICES:
        service = _as_mapping(services.get(name))
        if not service:
            problems.append(f"{path}: services.{name} is missing")
            continue
        if service.get("network_mode") != "host":
            problems.append(
                f"{path}: services.{name} must set network_mode: host; the dedicated "
                "host addresses every service on loopback"
            )
        if _as_list(service.get("ports")):
            problems.append(
                f"{path}: services.{name} must not publish ports under host networking; "
                "reset the inherited bridge publications"
            )
        if _as_mapping(service.get("networks")):
            problems.append(
                f"{path}: services.{name} must not attach a bridge network under host "
                "networking; reset the inherited attachments"
            )
    return problems


def validate_host_override(
    path: Path = HOST_OVERRIDE_FILE,
    env: Optional[Mapping[str, str]] = None,
) -> List[str]:
    """Validate ``compose.host.yaml``, the host-network overlay.

    The overlay must bind nothing to a non-loopback address and must address
    every peer through ``127.0.0.1`` at the ports from ``tests/config/network.yaml``.
    """
    problems: List[str] = []
    if not path.is_file():
        return [f"missing host-network override file: {path}"]
    document = load_compose(path)
    services = _as_mapping(document.get("services"))
    if not services:
        return [f"{path}: no services defined"]
    env = dict(env if env is not None else read_env_file(path.parent / ".env.example"))

    network = _network_or_problem()
    if isinstance(network, list):
        return problems + network

    problems.extend(_host_service_problems(path, services))

    def resolved(service: str, key: str) -> Optional[str]:
        value = _as_mapping(_as_mapping(services.get(service)).get("environment")).get(key)
        if value is None:
            return None
        try:
            return interpolate(str(value), env)
        except ComposeError as error:
            problems.append(f"{path}: services.{service}.{key} cannot be resolved: {error}")
            return None

    for name, port in (
        ("openhab-stable", network.openhab_stable_port),
        ("openhab-dev", network.openhab_dev_port),
    ):
        environment = _as_mapping(_as_mapping(services.get(name)).get("environment"))
        extra = str(environment.get("EXTRA_JAVA_OPTS", ""))
        if LISTENING_ADDRESS_PROPERTY not in extra:
            problems.append(
                f"{path}: services.{name} must set -D{LISTENING_ADDRESS_PROPERTY} so openHAB "
                "does not bind all interfaces"
            )
        elif f"${{{ENV_LOOPBACK_HOST}" not in extra:
            problems.append(
                f"{path}: services.{name} listening address must come from "
                f"${{{ENV_LOOPBACK_HOST}}}, not a literal hostname"
            )
        http_port = resolved(name, "OPENHAB_HTTP_PORT")
        if http_port != str(port):
            problems.append(
                f"{path}: services.{name}.OPENHAB_HTTP_PORT resolves to {http_port!r}, "
                f"expected {port} from tests/config/network.yaml"
            )

    shim_bind = resolved("shim", "SHIM_BIND")
    if shim_bind != network.loopback_host:
        problems.append(
            f"{path}: services.shim.SHIM_BIND resolves to {shim_bind!r}, expected the "
            f"loopback host {network.loopback_host!r}"
        )
    shim_port = resolved("shim", "SHIM_PORT")
    if shim_port != str(network.shim_port):
        problems.append(
            f"{path}: services.shim.SHIM_PORT resolves to {shim_port!r}, expected "
            f"{network.shim_port} from tests/config/network.yaml"
        )
    upstream = resolved("shim", "OPENHAB_UPSTREAM")
    if upstream != network.openhab_url("stable"):
        problems.append(
            f"{path}: services.shim.OPENHAB_UPSTREAM resolves to {upstream!r}, expected "
            f"{network.openhab_url('stable')!r}"
        )
    runner_openhab = resolved("runner", "E2E_OPENHAB_URL")
    if runner_openhab != network.openhab_url("stable"):
        problems.append(
            f"{path}: services.runner.E2E_OPENHAB_URL resolves to {runner_openhab!r}, "
            f"expected {network.openhab_url('stable')!r}"
        )
    runner_shim = resolved("runner", "E2E_SHIM_URL")
    if runner_shim != network.shim_url:
        problems.append(
            f"{path}: services.runner.E2E_SHIM_URL resolves to {runner_shim!r}, expected "
            f"{network.shim_url!r}"
        )
    return problems


def validate_host_dev_override(
    path: Path = HOST_DEV_OVERRIDE_FILE,
    env: Optional[Mapping[str, str]] = None,
) -> List[str]:
    """Validate ``compose.host.dev.yaml``, the host overlay for the dev profile."""
    problems: List[str] = []
    if not path.is_file():
        return [f"missing host-network dev override file: {path}"]
    document = load_compose(path)
    services = _as_mapping(document.get("services"))
    if not services:
        return [f"{path}: no services defined"]
    env = dict(env if env is not None else read_env_file(path.parent / ".env.example"))

    network = _network_or_problem()
    if isinstance(network, list):
        return problems + network

    expected = network.openhab_url("dev")
    for service, key in (
        ("shim", "OPENHAB_UPSTREAM"),
        ("runner", "E2E_OPENHAB_URL"),
    ):
        value = _as_mapping(_as_mapping(services.get(service)).get("environment")).get(key)
        if value is None:
            problems.append(f"{path}: services.{service} must set {key}")
            continue
        try:
            actual = interpolate(str(value), env)
        except ComposeError as error:
            problems.append(f"{path}: services.{service}.{key} cannot be resolved: {error}")
            continue
        if actual != expected:
            problems.append(
                f"{path}: services.{service}.{key} resolves to {actual!r}, expected "
                f"{expected!r} from tests/config/network.yaml"
            )
    return problems


def validate_compose(
    path: Path = COMPOSE_FILE,
    env_path: Optional[Path] = None,
) -> List[str]:
    """Validate every compose file, the environment example and the network wiring."""
    document = load_compose(path)
    if env_path is not None:
        env = read_env_file(env_path)
    else:
        example = path.parent / ".env.example"
        env = read_env_file(example)
    return (
        validate_compose_document(document, env)
        + validate_dev_override(path.parent / "compose.dev.yaml")
        + check_env_block(path.parent / ".env.example")
        + validate_host_override(path.parent / "compose.host.yaml", env=env)
        + validate_host_dev_override(path.parent / "compose.host.dev.yaml", env=env)
    )


# ---------------------------------------------------------------------------
# image pinning
# ---------------------------------------------------------------------------


def _hub_repository(repository: str) -> str:
    """Docker Hub names official images ``library/<name>``."""
    return repository if "/" in repository else f"library/{repository}"


def resolve_digest(repository: str, tag: str, timeout: float = 25.0) -> str:
    """Resolve a tag's multi-architecture manifest digest from Docker Hub."""
    url = DOCKER_HUB_TAG_URL.format(repository=_hub_repository(repository), tag=tag)
    request = urllib.request.Request(url, headers={"User-Agent": "openhab-garmin-e2e"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            document = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ComposeError(f"Docker Hub has no tag {repository}:{tag} ({error.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ComposeError(f"cannot reach Docker Hub to pin {repository}:{tag}: {error}") from None

    digest = document.get("digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ComposeError(f"Docker Hub returned no digest for {repository}:{tag}")
    return digest


def image_variables(pins: Mapping[str, ImagePin], pinned: bool = True) -> Dict[str, str]:
    """Map roles to ``KEY=image`` lines for the Compose environment."""
    variables: Dict[str, str] = {}
    for role, key in IMAGE_ENV_KEYS.items():
        pin = pins[role]
        variables[key] = pin.reference if pinned else pin.tagged
    return variables


def _rewrite_env(source: Path, values: Mapping[str, str], destination: Path) -> Path:
    """Write ``destination`` with image keys replaced in ``source``."""
    lines: List[str] = []
    remaining = dict(values)
    if source.is_file():
        for line in source.read_text(encoding="utf-8").splitlines():
            key = line.split("=", 1)[0].strip()
            if "=" in line and key in remaining:
                lines.append(f"{key}={remaining.pop(key)}")
            else:
                lines.append(line)
    else:
        lines.append("# Generated by `python -m e2e pin-images --write`.")
    for key, value in remaining.items():
        lines.append(f"{key}={value}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return destination


def pin_images(
    *,
    write: bool = True,
    refresh: bool = False,
    env_path: Path = ENV_FILE,
    version_file: Optional[Path] = None,
    resolver=resolve_digest,
) -> Dict[str, str]:
    """Resolve all image digests and (optionally) write ``tests/compose/.env``.

    The committed pins in ``tests/config/openhab.version`` are authoritative, so
    this works offline: Docker Hub is contacted only for a pin that has no digest
    yet, or when ``refresh`` is set. ``resolver`` is injectable so tests can run
    without network access.

    Returns the image variables that were written (or would be written).
    """
    from .config import OPENHAB_VERSION_FILE

    pins = openhab_images(version_file or OPENHAB_VERSION_FILE)
    resolved: Dict[str, str] = {}
    for role, key in IMAGE_ENV_KEYS.items():
        pin = pins[role]
        digest = pin.digest
        if refresh or not digest:
            digest = resolver(pin.repository, pin.tag)
        resolved[key] = f"{pin.tagged}@{digest}"
    if write:
        _rewrite_env(ENV_EXAMPLE, resolved, env_path)
    return resolved


def check_pins(
    env_path: Path = ENV_FILE,
    version_file: Optional[Path] = None,
) -> List[str]:
    """Return problems with the pinned images in ``env_path``."""
    from .config import OPENHAB_VERSION_FILE

    problems: List[str] = []
    if not env_path.is_file():
        return [
            f"{env_path} does not exist; run `python -m e2e pin-images --write` "
            "before starting the stack"
        ]
    env = read_env_file(env_path)
    pins = openhab_images(version_file or OPENHAB_VERSION_FILE)

    for role, key in IMAGE_ENV_KEYS.items():
        value = env.get(key)
        if not value:
            problems.append(f"{env_path} is missing {key}")
            continue
        if "@sha256:" not in value:
            problems.append(f"{key} is not pinned to a digest: {value}")
            continue
        expected = pins[role]
        if expected.digest and value != f"{expected.tagged}@{expected.digest}":
            problems.append(
                f"{key}={value} does not match tests/config/openhab.version "
                f"({expected.tagged}@{expected.digest})"
            )
    return problems
