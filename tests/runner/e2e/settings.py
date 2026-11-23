"""App settings fixtures.

A settings fixture is the Connect IQ property set the app reads at startup
(``resources/base/settings.xml``). Keeping the raw ``*_0`` property ids means a
fixture can be handed to a simulator driver without a translation layer, and it
keeps the fixture honest: the property names are the app's, not an invented
schema.

Only the properties that exist in ``resources/base/settings.xml`` are accepted,
so a typo fails when the scenario loads rather than when a run behaves oddly.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Mapping, Tuple

import yaml

from .config import APP_SETTINGS_DIR, ConfigError

#: Property id -> expected Python type, mirrored from
#: ``resources/base/settings.xml``. Update both together.
KNOWN_PROPERTIES: Mapping[str, type] = {
    "url_0": str,
    "sitemap_0": str,
    "restapi_0": bool,
    "webhook_0": str,
    "user_0": str,
    "password_0": str,
    "suppressEmptyResponseExceptions_0": bool,
    "pollingInterval_0": int,
    "postCommandHoldTime_0": int,
}

#: Properties a fixture must always define. The others have usable defaults in
#: the app's settings.xml, but a fixture that omits them is usually a mistake.
REQUIRED_PROPERTIES: Tuple[str, ...] = (
    "url_0",
    "sitemap_0",
    "restapi_0",
    "pollingInterval_0",
)


class SettingsError(ConfigError):
    """Raised when an app settings fixture is invalid."""


@dataclass(frozen=True)
class AppSettings:
    """A validated settings fixture."""

    name: str
    properties: Mapping[str, Any]
    description: str = ""
    source: Path = Path()

    def with_overrides(self, overrides: Mapping[str, Any]) -> "AppSettings":
        merged: Dict[str, Any] = dict(self.properties)
        merged.update(overrides)
        validate_properties(merged, where=f"{self.name} + scenario overrides")
        return replace(self, properties=MappingProxyType(merged))

    @property
    def sitemap(self) -> str:
        return str(self.properties["sitemap_0"])

    @property
    def url(self) -> str:
        return str(self.properties["url_0"])


def validate_properties(properties: Mapping[str, Any], where: str = "settings") -> None:
    """Validate property ids, types and value ranges."""
    if not isinstance(properties, Mapping):
        raise SettingsError(f"{where}: 'properties' must be a mapping")

    unknown = sorted(set(properties) - set(KNOWN_PROPERTIES))
    if unknown:
        raise SettingsError(
            f"{where}: unknown application property/properties: {', '.join(unknown)} "
            "(see resources/base/settings.xml)"
        )

    missing = [key for key in REQUIRED_PROPERTIES if key not in properties]
    if missing:
        raise SettingsError(f"{where}: missing required property/properties: {', '.join(missing)}")

    for key, value in properties.items():
        expected = KNOWN_PROPERTIES[key]
        # bool is an int subclass; `restapi_0: 1` is a fixture bug, not a truthy value.
        if expected is bool:
            if not isinstance(value, bool):
                raise SettingsError(f"{where}: {key} must be a boolean, got {value!r}")
        elif expected is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise SettingsError(f"{where}: {key} must be an integer, got {value!r}")
        elif not isinstance(value, str):
            raise SettingsError(f"{where}: {key} must be a string, got {value!r}")

    polling = properties.get("pollingInterval_0")
    if isinstance(polling, int) and not 0 <= polling <= 10000:
        raise SettingsError(f"{where}: pollingInterval_0 must be 0..10000, got {polling}")
    hold = properties.get("postCommandHoldTime_0")
    if isinstance(hold, int) and not 0 <= hold <= 10000:
        raise SettingsError(f"{where}: postCommandHoldTime_0 must be 0..10000, got {hold}")


def load_settings(name: str, directory: Path = APP_SETTINGS_DIR) -> AppSettings:
    """Load ``fixtures/app/settings/<name>.yaml``."""
    path = directory / f"{name}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in directory.glob("*.yaml")) if directory.is_dir() else []
        raise SettingsError(
            f"unknown settings fixture {name!r}; expected {path}"
            + (f"; available: {', '.join(available)}" if available else "")
        )
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise SettingsError(f"{path}: expected a mapping at the top level")
    properties = document.get("properties")
    if not isinstance(properties, Mapping):
        raise SettingsError(f"{path}: 'properties' must be a mapping")
    validate_properties(properties, where=str(path))
    return AppSettings(
        name=str(document.get("name", name)),
        properties=MappingProxyType(dict(properties)),
        description=str(document.get("description", "")),
        source=path,
    )
