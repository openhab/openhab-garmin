"""Compose stack validation and image pinning.

Docker is not required to validate the stack. ``validate_compose`` reads
``tests/compose/compose.yaml`` and checks the properties that the issue
actually depends on: the service set, profiles, the read-only fixture mount,
the dedicated volumes, the shim's upstream wiring, the runner's SDK mount and
that no image can be pulled without a digest.

``pin-images`` is the one command that needs the network: it resolves
multi-architecture digests from Docker Hub and writes them into
``tests/compose/.env``. ``--check`` verifies the pins without writing.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

import yaml

from .config import COMPOSE_DIR, ImagePin, openhab_images

COMPOSE_FILE = COMPOSE_DIR / "compose.yaml"
DEV_OVERRIDE_FILE = COMPOSE_DIR / "compose.dev.yaml"
ENV_FILE = COMPOSE_DIR / ".env"
ENV_EXAMPLE = COMPOSE_DIR / ".env.example"

REQUIRED_SERVICES = ("openhab-stable", "openhab-dev", "shim", "runner")
REQUIRED_VOLUMES = (
    "openhab-stable-conf",
    "openhab-stable-userdata",
    "openhab-dev-conf",
    "openhab-dev-userdata",
    "e2e-artifacts",
)
REQUIRED_ENV_KEYS = (
    "COMPOSE_PROFILES",
    "OPENHAB_STABLE_IMAGE",
    "OPENHAB_DEV_IMAGE",
    "RUNNER_BASE_IMAGE",
    "SHIM_BASE_IMAGE",
    "CIQ_SDK_HOST_PATH",
)

IMAGE_ENV_KEYS = {
    "openhab_stable": "OPENHAB_STABLE_IMAGE",
    "openhab_dev": "OPENHAB_DEV_IMAGE",
    "runner_base": "RUNNER_BASE_IMAGE",
    "shim_base": "SHIM_BASE_IMAGE",
}

DOCKER_HUB_TAG_URL = "https://hub.docker.com/v2/repositories/{repository}/tags/{tag}"


class ComposeError(RuntimeError):
    """Raised when the Compose stack cannot be read or is misconfigured."""


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def load_compose(path: Path = COMPOSE_FILE) -> Mapping[str, object]:
    if not path.is_file():
        raise ComposeError(f"compose file not found: {path}")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
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


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: object) -> Sequence[object]:
    return value if isinstance(value, list) else []


def _mounts(service: Mapping[str, object]) -> List[str]:
    return [str(mount) for mount in _as_list(service.get("volumes"))]


def _image(service: Mapping[str, object]) -> str:
    return str(service.get("image", ""))


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
            if not matching:
                problems.append(f"services.{name} must bind-mount the fixture directory {fixture}")
            elif not all(mount.rstrip().endswith(":ro") for mount in matching):
                problems.append(
                    f"services.{name} must mount {fixture} read-only (:ro); "
                    "fixtures are the source of truth"
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
    if not any("e2e-artifacts" in mount for mount in _mounts(runner)):
        problems.append("services.runner must mount the artifacts volume")
    runner_env = _as_mapping(runner.get("environment"))
    for key in ("CIQ_SDK_ROOT", "E2E_OPENHAB_URL", "E2E_SHIM_URL"):
        if key not in runner_env:
            problems.append(f"services.runner must set {key}")

    # -- networking and hygiene ------------------------------------------
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


def validate_compose(
    path: Path = COMPOSE_FILE,
    env_path: Optional[Path] = None,
) -> List[str]:
    """Validate the compose file, its example environment and the dev override."""
    document = load_compose(path)
    if env_path is not None:
        env = read_env_file(env_path)
    else:
        example = path.parent / ".env.example"
        env = read_env_file(example)
    return validate_compose_document(document, env) + validate_dev_override(
        path.parent / "compose.dev.yaml"
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
