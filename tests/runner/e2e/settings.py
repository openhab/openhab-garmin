"""App settings fixtures.

A settings fixture is the Connect IQ property set the app reads at startup
(``resources/settings/settings.xml``). Keeping the raw ``*_0`` property ids means a
fixture can be handed to a simulator driver without a translation layer, and it
keeps the fixture honest: the property names are the app's, not an invented
schema.

Only the properties that exist in ``resources/settings/settings.xml`` are accepted,
so a typo fails when the scenario loads rather than when a run behaves oddly.

``url_0`` is written as ``${E2E_SHIM_URL}/``. That is the same variable the
Compose stack passes to the runner, so the app cannot point somewhere other
than the shim the runner resets and inspects: in the bridge profile it resolves
to ``http://shim:8080`` and in the host profile to
``http://127.0.0.1:8081``. Outside a stack (dry runs, unit tests) it falls back
to the loopback URL derived from ``tests/config/network.yaml``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple

import yaml

from .config import APP_SETTINGS_DIR, ConfigError
from .network import NetworkError, load_network

#: Property id -> expected Python type, mirrored from
#: ``resources/settings/settings.xml``. Update both together.
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


#: ``${VAR}`` placeholders resolved from the stack environment or network.yaml.
_URL_TOKEN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _network_defaults() -> Mapping[str, str]:
    """Loopback URLs to use when the stack environment is not present."""
    try:
        network = load_network()
    except NetworkError:
        return {}
    return {
        "E2E_SHIM_URL": network.shim_url,
        "E2E_OPENHAB_URL": network.openhab_url("stable"),
    }


def resolve_tokens(value: Any, environ: Optional[Mapping[str, str]] = None) -> Any:
    """Replace ``${VAR}`` placeholders with the stack environment's values.

    The environment wins because that is what Compose passes to the runner in
    the active profile. Without it, the loopback defaults from
    ``tests/config/network.yaml`` apply. An unknown placeholder fails closed
    instead of reaching the simulator as a literal.
    """
    if not isinstance(value, str) or "${" not in value:
        return value
    env = os.environ if environ is None else environ
    defaults = _network_defaults()

    def replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        resolved = env.get(name) or defaults.get(name)
        if not resolved:
            raise SettingsError(
                f"unresolved settings token ${{{name}}}; set it in the environment "
                "or define it in tests/config/network.yaml"
            )
        return resolved

    return _URL_TOKEN.sub(replace, value)


def resolve_properties(
    properties: Mapping[str, Any],
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    return {key: resolve_tokens(value, environ) for key, value in properties.items()}


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
        merged = resolve_properties(merged)
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
            "(see resources/settings/settings.xml)"
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


def scenario_settings(
    scenario: "Scenario",
    *,
    shim_url: str,
    directory: Path = APP_SETTINGS_DIR,
) -> AppSettings:
    """The properties a scenario actually runs with, composed in one place.

    The fixture supplies the behavioural defaults (polling interval, suppress
    empty responses, webhook, …) and the scenario overrides them. Two values are
    **not** the fixture's to decide:

    - ``sitemap_0`` comes from ``scenario.openhab.sitemap``, the page the
      scenario asserts against. A fixture default left in place compiles a
      different sitemap into the PRG than the scenario waits for, and the run
      fails looking for a request the app will never make.
    - ``url_0`` comes from the harness's shim URL, so a scenario cannot compile
      an endpoint the runner is not inspecting.

    Composing here means the build cache key, the compiled PRG and the driver
    all see the same values.
    """
    overrides: Dict[str, Any] = dict(scenario.settings or {})
    overrides["sitemap_0"] = scenario.openhab.sitemap
    overrides["url_0"] = shim_url if shim_url.endswith("/") else f"{shim_url}/"
    return load_settings(scenario.fixture, directory).with_overrides(overrides)


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
    properties = resolve_properties(properties)
    validate_properties(properties, where=str(path))
    return AppSettings(
        name=str(document.get("name", name)),
        properties=MappingProxyType(dict(properties)),
        description=str(document.get("description", "")),
        source=path,
    )
