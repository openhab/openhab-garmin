"""Harness configuration: locations, version pins and the device matrix.

Everything here is read-only static data. Values the operator may override per
run (openHAB URL, shim URL, device) belong on the CLI, not in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

import yaml

from .paths import repo_root, tests_root


class ConfigError(RuntimeError):
    """Raised when a configuration file is missing or malformed."""


CONFIG_DIR = tests_root() / "config"
FIXTURE_DIR = tests_root() / "fixtures"
SCENARIO_DIR = tests_root() / "scenarios"
COMPOSE_DIR = tests_root() / "compose"
SHIM_DIR = tests_root() / "shim"
DOCS_DIR = tests_root() / "docs"

SDK_VERSION_FILE = CONFIG_DIR / "sdk.version"
OPENHAB_VERSION_FILE = CONFIG_DIR / "openhab.version"
DEVICES_FILE = CONFIG_DIR / "devices.yaml"
SCOPE_FILE = CONFIG_DIR / "scope.yaml"
NETWORK_FILE = CONFIG_DIR / "network.yaml"
APP_SETTINGS_DIR = FIXTURE_DIR / "app" / "settings"


def read_key_value_file(path: Path) -> Dict[str, str]:
    """Read a ``key = value`` file with ``#`` comments.

    Deliberately not YAML: these files are also read by shell snippets in the
    documentation, and the format must stay trivial.
    """
    if not path.is_file():
        raise ConfigError(f"missing configuration file: {path}")
    values: Dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ConfigError(f"{path}:{number}: expected 'key = value', got {stripped!r}")
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def read_sdk_version(path: Path = SDK_VERSION_FILE) -> str:
    """Return the pinned Connect IQ SDK version, e.g. ``9.3.0``."""
    if not path.is_file():
        raise ConfigError(f"missing SDK version file: {path}")
    version = path.read_text(encoding="utf-8").strip()
    if not version:
        raise ConfigError(f"{path} is empty")
    return version


@dataclass(frozen=True)
class ImagePin:
    """A container image pinned to a tag and a multi-arch manifest digest."""

    repository: str
    tag: str
    digest: Optional[str]

    @property
    def tagged(self) -> str:
        return f"{self.repository}:{self.tag}"

    @property
    def reference(self) -> str:
        """Full ``repo:tag@sha256:...`` reference.

        Raises when the digest has not been resolved yet, which is the point:
        an unpinned image must not reach a run.
        """
        if not self.digest:
            raise ConfigError(
                f"{self.tagged} has no pinned digest; "
                "run `python -m e2e pin-images --write`"
            )
        return f"{self.tagged}@{self.digest}"


def _pin(values: Mapping[str, str], prefix: str, source: Path) -> ImagePin:
    try:
        repository = values[f"{prefix}_repository"]
        tag = values[f"{prefix}_tag"]
    except KeyError as error:
        raise ConfigError(f"{source}: missing key {error.args[0]}") from None
    return ImagePin(repository=repository, tag=tag, digest=values.get(f"{prefix}_digest"))


def openhab_images(path: Path = OPENHAB_VERSION_FILE) -> Dict[str, ImagePin]:
    """Return the pinned openHAB and base images keyed by role."""
    values = read_key_value_file(path)
    return {
        "openhab_stable": _pin(values, "stable", path),
        "openhab_dev": _pin(values, "dev", path),
        "runner_base": _pin(values, "runner_base", path),
        "shim_base": _pin(values, "shim_base", path),
    }


@dataclass(frozen=True)
class Device:
    """One entry of the device matrix."""

    id: str
    product: str
    family: str
    input: str
    screen_shape: str
    screen_width: int
    screen_height: int
    tier: str
    notes: str = ""
    variants: Tuple[str, ...] = ()
    tiers: Tuple[str, ...] = ()

    @property
    def supports_touch(self) -> bool:
        return self.input in ("touch", "touch+button")

    @property
    def supports_buttons(self) -> bool:
        return self.input in ("button", "touch+button")

    @property
    def tiers_run(self) -> Tuple[str, ...]:
        """Tiers this device runs, e.g. ``("protocol", "ui")``.

        ``tiers`` is the current field; ``tier`` remains the first entry for
        callers that predate the list and for files that still use one tier.
        """
        return self.tiers or (self.tier,)


_DEVICE_INPUTS = ("button", "touch", "touch+button")
_DEVICE_TIERS = ("protocol", "ui")

#: Optional ``dimension:value`` tags describing the resolved build variant.
#: Dimensions and the coverage check are documented in ``tests/docs/devices.md``.
_VARIANT_TAG = re.compile(r"^[a-z0-9_]+:[a-z0-9+_-]+$")


def load_devices(path: Path = DEVICES_FILE) -> Tuple[Device, ...]:
    """Load and validate the device matrix."""
    if not path.is_file():
        raise ConfigError(f"missing device matrix: {path}")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    raw_devices = document.get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        raise ConfigError(f"{path}: 'devices' must be a non-empty list")

    devices = []
    seen = set()
    for index, raw in enumerate(raw_devices):
        where = f"{path}: devices[{index}]"
        if not isinstance(raw, Mapping):
            raise ConfigError(f"{where}: expected a mapping")
        raw_variants = raw.get("variants", [])
        if isinstance(raw_variants, (str, bytes)) or not isinstance(raw_variants, (list, tuple)):
            raise ConfigError(f"{where}: 'variants' must be a list of 'dimension:value' tags")
        raw_tiers = raw.get("tiers")
        if raw_tiers is None:
            tiers: Tuple[str, ...] = (str(raw.get("tier", "ui")),)
        elif isinstance(raw_tiers, (str, bytes)) or not isinstance(raw_tiers, (list, tuple)):
            raise ConfigError(f"{where}: 'tiers' must be a list of 'tier' values")
        else:
            tiers = tuple(str(tier) for tier in raw_tiers)
            if not tiers:
                raise ConfigError(f"{where}: 'tiers' must not be empty")
            if len(set(tiers)) != len(tiers):
                raise ConfigError(f"{where}: 'tiers' must not repeat a tier")
        try:
            device_id = str(raw["id"])
            screen = raw["screen"]
            device = Device(
                id=device_id,
                product=str(raw["product"]),
                family=str(raw.get("family", "")),
                input=str(raw["input"]),
                screen_shape=str(screen["shape"]),
                screen_width=int(screen["width"]),
                screen_height=int(screen["height"]),
                tier=tiers[0],
                notes=str(raw.get("notes", "")),
                variants=tuple(str(tag) for tag in raw_variants),
                tiers=tiers,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ConfigError(f"{where}: {error}") from None
        for tag in device.variants:
            if not _VARIANT_TAG.match(tag):
                raise ConfigError(
                    f"{where}: variant tag {tag!r} must look like 'dimension:value'"
                )
        dimensions = [tag.partition(":")[0] for tag in device.variants]
        if len(set(dimensions)) != len(dimensions):
            raise ConfigError(f"{where}: 'variants' names a dimension more than once")
        if device.id in seen:
            raise ConfigError(f"{where}: duplicate device id {device.id!r}")
        seen.add(device.id)
        if device.input not in _DEVICE_INPUTS:
            raise ConfigError(f"{where}: input must be one of {_DEVICE_INPUTS}")
        for tier in device.tiers:
            if tier not in _DEVICE_TIERS:
                raise ConfigError(f"{where}: tier must be one of {_DEVICE_TIERS}")
        devices.append(device)
    return tuple(devices)


def find_device(device_id: str, path: Path = DEVICES_FILE) -> Device:
    """Return the device with this id or raise a helpful error."""
    devices = load_devices(path)
    for device in devices:
        if device.id == device_id:
            return device
    known = ", ".join(device.id for device in devices)
    raise ConfigError(f"unknown device {device_id!r}; known devices: {known}")


def default_device_id(path: Path = DEVICES_FILE) -> str:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return str(document.get("default", load_devices(path)[0].id))


def jungle_path() -> Path:
    """The Monkey Jungle of the app under test."""
    return repo_root() / "monkey.jungle"
