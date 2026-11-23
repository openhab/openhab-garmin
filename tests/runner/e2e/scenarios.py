"""Scenario loading, validation and discovery.

A scenario is a declarative YAML file. The harness executes it; nothing in a
scenario file is code. The schema is validated eagerly and strictly -- unknown
keys are errors, not warnings -- because a silently ignored typo turns an
intentional failure test into a happy-path test that still passes.

Top level (all required unless noted)::

    name            unique, kebab-case
    description     one line
    tier            protocol | ui   (protocol = assertable from traces only)
    suite           smoke | regression | extended
    device          device id from tests/config/devices.yaml, or "any"
    mode            ble | wifi | offline
    fixture         app settings fixture in tests/fixtures/app/settings/
    openhab         {profile: stable|dev, sitemap: <name>, items: {item: state}}
    language        eng | deu | fre | ita | spa | pol
                    (or `languages: [...]` for a matrix, see below)
    timeout_s       whole-scenario budget, 1..1800
    steps           non-empty list, see ACTIONS below

Optional::

    settings        app settings overrides merged onto `fixture`
    shim            {rules: [...]} fault script, see tests/shim/faults.py
    notes           free-form operator note
    covers          scope ids from tests/config/scope.yaml, see e2e/scope.py
    manual_checks   behaviours the scenario exercises but cannot assert yet

A scenario declares either one `language` or a `languages` list. The matrix
form expands into one scenario per language, with ``-<language>`` appended to
the scenario name, so artifacts and reports stay separate. Step parameters may
use the ``${language}`` placeholder; it is replaced with the expanded language.
That keeps a single file assertable against ``E2E:LANGUAGE;code=<language>``.

Steps are validated by the per-action functions below. Each returns canonical
parameters, so the engine never re-parses YAML shapes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml

from shim.faults import ShimScriptError, parse_script

from .config import SCENARIO_DIR, ConfigError, load_devices
from .scope import find_unknown_ids, load_scope
from .settings import KNOWN_PROPERTIES
from .trace import EVENT_FIELDS, validate_match_fields

TIERS = ("protocol", "ui")
SUITES = ("smoke", "regression", "extended")
MODES = ("ble", "wifi", "offline")
LANGUAGES = ("eng", "deu", "fre", "ita", "spa", "pol")
OPENHAB_PROFILES = ("stable", "dev")
BUTTONS = ("START", "UP", "DOWN", "ENTER", "BACK", "MENU")
COMMAND_TRANSPORTS = ("rest", "shim")

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SCREENSHOT_NAME = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


class ScenarioError(ConfigError):
    """Raised when a scenario file does not follow the schema."""


# ---------------------------------------------------------------------------
# small validators
# ---------------------------------------------------------------------------


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScenarioError(f"{where}: expected a mapping, got {type(value).__name__}")
    return value


def _reject_unknown(mapping: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise ScenarioError(f"{where}: unknown key(s): {', '.join(unknown)}")


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ScenarioError(f"{where}: expected a string, got {type(value).__name__}")
    return value


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ScenarioError(f"{where}: expected a boolean, got {type(value).__name__}")
    return value


def _int(value: Any, where: str, minimum: int, maximum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioError(f"{where}: expected an integer, got {type(value).__name__}")
    if value < minimum:
        raise ScenarioError(f"{where}: must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ScenarioError(f"{where}: must be <= {maximum}, got {value}")
    return value


def _ratio(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioError(f"{where}: expected a number, got {type(value).__name__}")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ScenarioError(f"{where}: must be between 0 and 1, got {value}")
    return number


def _enum(value: Any, where: str, allowed: Sequence[str]) -> str:
    text = _string(value, where)
    if text not in allowed:
        raise ScenarioError(f"{where}: must be one of {', '.join(allowed)}; got {text!r}")
    return text


def _match_text(value: Any, where: str) -> str:
    text = _string(value, where)
    if not text:
        raise ScenarioError(f"{where}: must not be empty")
    return text


LANGUAGE_PLACEHOLDER = "${language}"


def _languages(mapping: Mapping[str, Any], where: str) -> Tuple[Tuple[str, ...], bool]:
    """Return ``(languages, from_matrix)`` for the scenario document.

    Exactly one of ``language`` (one run) and ``languages`` (a matrix) is
    allowed. A matrix always appends the language to the scenario name, so a
    one-element matrix is still distinct from a plain ``language``.
    """
    if "language" in mapping and "languages" in mapping:
        raise ScenarioError(f"{where}: set either 'language' or 'languages', not both")
    if "languages" not in mapping:
        return (
            (_enum(mapping["language"], f"{where}: language", LANGUAGES),),
            False,
        )
    raw = mapping["languages"]
    if isinstance(raw, (str, bytes)) or not isinstance(raw, list) or not raw:
        raise ScenarioError(f"{where}: 'languages' must be a non-empty list")
    languages: List[str] = []
    for index, value in enumerate(raw):
        language = _enum(value, f"{where}: languages[{index}]", LANGUAGES)
        if language in languages:
            raise ScenarioError(f"{where}: languages[{index}] repeats {language!r}")
        languages.append(language)
    return tuple(languages), True


def _string_list(value: Any, where: str) -> Tuple[str, ...]:
    """Validate a non-empty, unique list of non-empty strings."""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ScenarioError(f"{where}: expected a list of strings")
    if not value:
        raise ScenarioError(f"{where}: must not be empty")
    items: List[str] = []
    for index, raw in enumerate(value):
        text = _match_text(raw, f"{where}[{index}]")
        if text in items:
            raise ScenarioError(f"{where}[{index}]: repeats {text!r}")
        items.append(text)
    return tuple(items)


def _covers(value: Any, where: str) -> Tuple[str, ...]:
    """Validate scope ids against the checked-in scope definition."""
    ids = _string_list(value, where)
    unknown = find_unknown_ids(load_scope(), ids)
    if unknown:
        raise ScenarioError(
            f"{where}: unknown scope id(s): {', '.join(unknown)}; "
            "see tests/config/scope.yaml"
        )
    return ids


def _substitute_language(value: Any, language: str) -> Any:
    """Replace ``${language}`` in canonical step parameters."""
    if isinstance(value, str):
        return value.replace(LANGUAGE_PLACEHOLDER, language)
    if isinstance(value, Mapping):
        return {
            _substitute_language(key, language): _substitute_language(item, language)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_substitute_language(item, language) for item in value]
    if isinstance(value, tuple):
        return tuple(_substitute_language(item, language) for item in value)
    return value


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One canonical, validated step."""

    index: int
    action: str
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def description(self) -> str:
        text = self.params.get("description")
        return str(text) if text else self.action

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


def _step_launch(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    _reject_unknown(raw, {"action", "wait_ready"}, where)
    return {"wait_ready": _bool(raw.get("wait_ready", True), f"{where}.wait_ready")}


def _step_stop(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    _reject_unknown(raw, {"action"}, where)
    return {}


def _step_wait(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    _reject_unknown(raw, {"action", "ms"}, where)
    if "ms" not in raw:
        raise ScenarioError(f"{where}: 'ms' is required")
    return {"ms": _int(raw["ms"], f"{where}.ms", 1, 600_000)}


def _step_reset_shim(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    _reject_unknown(raw, {"action"}, where)
    return {}


def _request_match(raw: Mapping[str, Any], where: str, require_path: bool = True) -> Dict[str, Any]:
    allowed = {"method", "path", "path_prefix", "body_contains", "body_equals", "status", "reset"}
    _reject_unknown(raw, allowed, where)
    params: Dict[str, Any] = {}
    if "method" in raw:
        params["method"] = _enum(raw["method"], f"{where}.method",
                                 ("GET", "POST", "PUT", "PATCH", "DELETE"))
    if "path" in raw:
        params["path"] = _match_text(raw["path"], f"{where}.path")
    if "path_prefix" in raw:
        params["path_prefix"] = _match_text(raw["path_prefix"], f"{where}.path_prefix")
    if "path" in params and "path_prefix" in params:
        raise ScenarioError(f"{where}: set either 'path' or 'path_prefix', not both")
    if "body_contains" in raw:
        params["body_contains"] = _string(raw["body_contains"], f"{where}.body_contains")
    if "body_equals" in raw:
        params["body_equals"] = _string(raw["body_equals"], f"{where}.body_equals")
    if "status" in raw:
        params["status"] = _int(raw["status"], f"{where}.status", 0, 599)
    if "reset" in raw:
        params["reset"] = _bool(raw["reset"], f"{where}.reset")
    if require_path and not any(key in params for key in ("path", "path_prefix")):
        raise ScenarioError(f"{where}: needs 'path' or 'path_prefix'")
    return params


def _step_wait_for_request(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    _reject_unknown(raw, {"action", "description", "method", "path", "path_prefix",
                          "min_count", "timeout_ms"}, where)
    params: Dict[str, Any] = {
        "min_count": _int(raw.get("min_count", 1), f"{where}.min_count", 1)
    }
    for key in ("method", "path", "path_prefix"):
        if key in raw:
            params[key] = raw[key]
    if "method" in params:
        params["method"] = _enum(params["method"], f"{where}.method",
                                 ("GET", "POST", "PUT", "PATCH", "DELETE"))
    for key in ("path", "path_prefix"):
        if key in params:
            params[key] = _match_text(params[key], f"{where}.{key}")
    if "path" in params and "path_prefix" in params:
        raise ScenarioError(f"{where}: set either 'path' or 'path_prefix', not both")
    if not any(key in params for key in ("path", "path_prefix")):
        raise ScenarioError(f"{where}: needs 'path' or 'path_prefix'")
    params["timeout_ms"] = _int(raw.get("timeout_ms", 30_000), f"{where}.timeout_ms", 1, 600_000)
    if "description" in raw:
        params["description"] = _string(raw["description"], f"{where}.description")
    return params


def _step_press(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    _reject_unknown(raw, {"action", "button", "count"}, where)
    if "button" not in raw:
        raise ScenarioError(f"{where}: 'button' is required")
    return {
        "button": _enum(raw["button"], f"{where}.button", BUTTONS),
        "count": _int(raw.get("count", 1), f"{where}.count", 1, 100),
    }


def _step_tap(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    _reject_unknown(raw, {"action", "x", "y", "count"}, where)
    for key in ("x", "y"):
        if key not in raw:
            raise ScenarioError(f"{where}: '{key}' is required")
    return {
        "x": _int(raw["x"], f"{where}.x", 0),
        "y": _int(raw["y"], f"{where}.y", 0),
        "count": _int(raw.get("count", 1), f"{where}.count", 1, 100),
    }


def _step_screenshot(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    _reject_unknown(raw, {"action", "name"}, where)
    if "name" not in raw:
        raise ScenarioError(f"{where}: 'name' is required")
    name = _string(raw["name"], f"{where}.name")
    if not _SCREENSHOT_NAME.match(name):
        raise ScenarioError(
            f"{where}.name: use lower-case letters, digits, '-' or '_'; got {name!r}"
        )
    return {"name": name}


def _step_command(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    _reject_unknown(raw, {"action", "item", "value", "via"}, where)
    for key in ("item", "value"):
        if key not in raw:
            raise ScenarioError(f"{where}: '{key}' is required")
    return {
        "item": _match_text(raw["item"], f"{where}.item"),
        "value": _match_text(raw["value"], f"{where}.value"),
        "via": _enum(raw.get("via", "rest"), f"{where}.via", COMMAND_TRANSPORTS),
    }


def _step_wait_for_event(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    _reject_unknown(raw, {"action", "event", "fields", "contains", "timeout_ms",
                          "description"}, where)
    params: Dict[str, Any] = {}

    if "event" in raw:
        params["event"] = _match_text(raw["event"], f"{where}.event").upper()

    if "fields" in raw:
        mapping = _require_mapping(raw["fields"], f"{where}.fields")
        if not mapping:
            raise ScenarioError(f"{where}.fields: must not be empty")
        fields: Dict[str, str] = {}
        for key, value in mapping.items():
            name = _match_text(key, f"{where}.fields key")
            if isinstance(value, bool) or value is None:
                raise ScenarioError(
                    f"{where}.fields.{name}: value must be a string (quote it); got {value!r}"
                )
            if not isinstance(value, (str, int, float)):
                raise ScenarioError(
                    f"{where}.fields.{name}: unsupported value type {type(value).__name__}"
                )
            fields[name] = str(value)
        params["fields"] = fields

    if "contains" in raw:
        params["contains"] = _match_text(raw["contains"], f"{where}.contains")

    if "event" not in params and "contains" not in params:
        raise ScenarioError(f"{where}: needs 'event' (preferred) or 'contains'")

    unknown = validate_match_fields(params.get("event"), params.get("fields", {}))
    if unknown:
        known = EVENT_FIELDS.get(params["event"], ())
        raise ScenarioError(
            f"{where}.fields: {', '.join(unknown)} not emitted by {params['event']}; "
            f"valid fields: {', '.join(known)}"
        )

    params["timeout_ms"] = _int(raw.get("timeout_ms", 30_000), f"{where}.timeout_ms", 1, 600_000)
    if "description" in raw:
        params["description"] = _string(raw["description"], f"{where}.description")
    return params


def _assertion_params(raw: Mapping[str, Any], where: str) -> Dict[str, Any]:
    _reject_unknown(raw, {"action", "description", "shim_requests", "golden",
                          "no_error_view", "never_error_view"}, where)
    params: Dict[str, Any] = {}
    if "description" in raw:
        params["description"] = _string(raw["description"], f"{where}.description")

    if "shim_requests" in raw:
        spec_where = f"{where}.shim_requests"
        spec = _require_mapping(raw["shim_requests"], spec_where)
        _reject_unknown(spec, {"method", "path", "path_prefix", "body_contains",
                               "body_equals", "status", "reset", "min_count",
                               "max_count"}, spec_where)
        built: Dict[str, Any] = _request_match(
            {key: value for key, value in spec.items()
             if key in {"method", "path", "path_prefix", "body_contains",
                        "body_equals", "status", "reset"}},
            spec_where,
            require_path=False,
        )
        if "min_count" in spec:
            built["min_count"] = _int(spec["min_count"], f"{spec_where}.min_count", 0)
        if "max_count" in spec:
            built["max_count"] = _int(spec["max_count"], f"{spec_where}.max_count", 0)
        if "min_count" in built and "max_count" in built and built["min_count"] > built["max_count"]:
            raise ScenarioError(f"{spec_where}: min_count must not exceed max_count")
        if not any(key in built for key in ("min_count", "max_count")):
            # A bare match would always pass for any trace that has a request;
            # require an explicit count so the assertion means something.
            raise ScenarioError(f"{spec_where}: needs 'min_count' or 'max_count'")
        params["shim_requests"] = built

    if "golden" in raw:
        spec_where = f"{where}.golden"
        spec = _require_mapping(raw["golden"], spec_where)
        _reject_unknown(spec, {"name", "max_diff_ratio"}, spec_where)
        if "name" not in spec:
            raise ScenarioError(f"{spec_where}: 'name' is required")
        name = _string(spec["name"], f"{spec_where}.name")
        if not _SCREENSHOT_NAME.match(name):
            raise ScenarioError(f"{spec_where}.name: invalid screenshot name {name!r}")
        params["golden"] = {
            "name": name,
            "max_diff_ratio": _ratio(spec.get("max_diff_ratio", 0.02),
                                      f"{spec_where}.max_diff_ratio"),
        }

    if "no_error_view" in raw:
        params["no_error_view"] = _bool(raw["no_error_view"], f"{where}.no_error_view")

    if "never_error_view" in raw:
        value = _bool(raw["never_error_view"], f"{where}.never_error_view")
        if not value:
            raise ScenarioError(
                f"{where}.never_error_view: only 'true' is meaningful; use "
                "'no_error_view' to assert on the view that is shown now"
            )
        params["never_error_view"] = True

    if not any(key in params for key in
               ("shim_requests", "golden", "no_error_view", "never_error_view")):
        raise ScenarioError(
            f"{where}: an assert step needs 'shim_requests', 'golden', "
            "'no_error_view' or 'never_error_view'"
        )
    return params


_STEP_HANDLERS = {
    "launch": _step_launch,
    "stop": _step_stop,
    "wait": _step_wait,
    "reset_shim": _step_reset_shim,
    "wait_for_request": _step_wait_for_request,
    "press": _step_press,
    "tap": _step_tap,
    "screenshot": _step_screenshot,
    "command": _step_command,
    "wait_for_event": _step_wait_for_event,
    "assert": _assertion_params,
}


def parse_steps(raw_steps: Any, where: str = "steps") -> Tuple[Step, ...]:
    """Validate and canonicalise the ``steps`` list."""
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ScenarioError(f"{where}: expected a non-empty list")
    steps: List[Step] = []
    for index, raw in enumerate(raw_steps):
        step_where = f"{where}[{index}]"
        mapping = _require_mapping(raw, step_where)
        if "action" not in mapping:
            raise ScenarioError(f"{step_where}: 'action' is required")
        action = _string(mapping["action"], f"{step_where}.action")
        handler = _STEP_HANDLERS.get(action)
        if handler is None:
            raise ScenarioError(
                f"{step_where}.action: unknown action {action!r}; "
                f"known actions: {', '.join(sorted(_STEP_HANDLERS))}"
            )
        steps.append(Step(index=index, action=action, params=handler(mapping, step_where)))

    launches = [step for step in steps if step.action == "launch"]
    if len(launches) != 1:
        raise ScenarioError(f"{where}: exactly one 'launch' step is required, found {len(launches)}")
    if steps[0].action != "launch":
        raise ScenarioError(f"{where}: the first step must be 'launch'")
    return tuple(steps)


# ---------------------------------------------------------------------------
# scenario
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenHabSpec:
    """Where the app should point and what state openHAB starts in."""

    sitemap: str
    profile: str = "stable"
    items: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    """A validated scenario."""

    name: str
    base_name: str
    description: str
    tier: str
    suite: str
    device: str
    mode: str
    fixture: str
    openhab: OpenHabSpec
    language: str
    timeout_s: int
    steps: Tuple[Step, ...]
    source: Path
    settings: Mapping[str, Any] = field(default_factory=dict)
    shim_rules: Tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    notes: str = ""
    covers: Tuple[str, ...] = field(default_factory=tuple)
    manual_checks: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def scenario_id(self) -> str:
        return f"{self.suite}/{self.name}"

    @property
    def requires_trace_events(self) -> bool:
        """True when the scenario asserts on app console events."""
        return any(step.action == "wait_for_event" for step in self.steps)

    @property
    def shim_script(self) -> Dict[str, Any]:
        """The fault script document installed before the run."""
        return {"version": 1, "rules": [dict(rule) for rule in self.shim_rules]}


def _parse_openhab(raw: Any, where: str) -> OpenHabSpec:
    mapping = _require_mapping(raw, where)
    _reject_unknown(mapping, {"profile", "sitemap", "items"}, where)
    if "sitemap" not in mapping:
        raise ScenarioError(f"{where}: 'sitemap' is required")
    sitemap = _match_text(mapping["sitemap"], f"{where}.sitemap")

    profile = _enum(mapping.get("profile", "stable"), f"{where}.profile", OPENHAB_PROFILES)

    raw_items = mapping.get("items", {})
    if not isinstance(raw_items, Mapping):
        raise ScenarioError(f"{where}.items: expected a mapping of item name to state")
    items: Dict[str, str] = {}
    for name, value in raw_items.items():
        item_name = _match_text(name, f"{where}.items key")
        if isinstance(value, bool) or value is None:
            raise ScenarioError(
                f"{where}.items.{item_name}: state must be a string (quote ON/OFF); "
                f"got {value!r}"
            )
        if not isinstance(value, (str, int, float)):
            raise ScenarioError(
                f"{where}.items.{item_name}: unsupported state type {type(value).__name__}"
            )
        items[item_name] = str(value)
    return OpenHabSpec(sitemap=sitemap, profile=profile, items=items)


def _parse_settings(raw: Any, where: str) -> Dict[str, Any]:
    mapping = _require_mapping(raw, where)
    if not mapping:
        raise ScenarioError(f"{where}: must not be empty")
    unknown = sorted(set(mapping) - set(KNOWN_PROPERTIES))
    if unknown:
        raise ScenarioError(f"{where}: unknown application property/properties: {', '.join(unknown)}")
    for key, value in mapping.items():
        expected = KNOWN_PROPERTIES[key]
        if expected is bool and not isinstance(value, bool):
            raise ScenarioError(f"{where}.{key}: expected a boolean, got {value!r}")
        if expected is int and (isinstance(value, bool) or not isinstance(value, int)):
            raise ScenarioError(f"{where}.{key}: expected an integer, got {value!r}")
        if expected is str and not isinstance(value, str):
            raise ScenarioError(f"{where}.{key}: expected a string, got {value!r}")
    return dict(mapping)


def load_scenarios(path: Path, known_devices: Optional[Sequence[str]] = None) -> List[Scenario]:
    """Load and validate one scenario file into one scenario per language."""
    if not path.is_file():
        raise ScenarioError(f"scenario not found: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ScenarioError(f"{path}: invalid YAML: {error}") from None
    mapping = _require_mapping(document, str(path))

    _reject_unknown(
        mapping,
        {"name", "description", "tier", "suite", "device", "mode", "fixture",
         "openhab", "language", "languages", "timeout_s", "steps", "settings",
         "shim", "notes", "covers", "manual_checks"},
        str(path),
    )

    for key in ("name", "description", "tier", "suite", "device", "mode", "fixture",
                "openhab", "timeout_s", "steps"):
        if key not in mapping:
            raise ScenarioError(f"{path}: missing required key {key!r}")
    if "language" not in mapping and "languages" not in mapping:
        raise ScenarioError(f"{path}: missing required key 'language' (or 'languages')")

    languages, from_matrix = _languages(mapping, str(path))

    name = _match_text(mapping["name"], f"{path}: name")
    if not _SLUG.match(name):
        raise ScenarioError(f"{path}: name must be kebab-case, got {name!r}")

    suite = _enum(mapping["suite"], f"{path}: suite", SUITES)
    if path.parent.name in SUITES and path.parent.name != suite:
        raise ScenarioError(
            f"{path}: suite {suite!r} does not match directory {path.parent.name!r}"
        )

    device = _string(mapping["device"], f"{path}: device")
    if device != "any":
        known = tuple(known_devices) if known_devices is not None else tuple(
            item.id for item in load_devices()
        )
        if device not in known:
            raise ScenarioError(
                f"{path}: unknown device {device!r}; known: {', '.join(known)}, or 'any'"
            )

    fixture = _match_text(mapping["fixture"], f"{path}: fixture")
    if Path(fixture).name != fixture:
        raise ScenarioError(f"{path}: fixture must be a settings fixture name, not a path")

    rules = ()
    if "shim" in mapping:
        shim_mapping = _require_mapping(mapping["shim"], f"{path}: shim")
        _reject_unknown(shim_mapping, {"rules"}, f"{path}: shim")
        raw_rules = shim_mapping.get("rules", [])
        if not isinstance(raw_rules, list):
            raise ScenarioError(f"{path}: shim.rules must be a list")
        try:
            parse_script({"version": 1, "rules": raw_rules})
        except ShimScriptError as error:
            raise ScenarioError(f"{path}: shim.rules: {error}") from None
        rules = tuple(raw_rules)

    base = Scenario(
        name=name,
        base_name=name,
        description=_string(mapping["description"], f"{path}: description"),
        tier=_enum(mapping["tier"], f"{path}: tier", TIERS),
        suite=suite,
        device=device,
        mode=_enum(mapping["mode"], f"{path}: mode", MODES),
        fixture=fixture,
        openhab=_parse_openhab(mapping["openhab"], f"{path}: openhab"),
        language=languages[0],
        timeout_s=_int(mapping["timeout_s"], f"{path}: timeout_s", 1, 1800),
        steps=parse_steps(mapping["steps"], f"{path}: steps"),
        source=path,
        settings=({} if "settings" not in mapping
                  else _parse_settings(mapping["settings"], f"{path}: settings")),
        shim_rules=rules,
        notes=("" if "notes" not in mapping
               else _string(mapping["notes"], f"{path}: notes")),
        covers=(() if "covers" not in mapping
                else _covers(mapping["covers"], f"{path}: covers")),
        manual_checks=(() if "manual_checks" not in mapping
                       else _string_list(mapping["manual_checks"], f"{path}: manual_checks")),
    )

    if not from_matrix:
        return [base]

    scenarios: List[Scenario] = []
    for language in languages:
        steps = tuple(
            replace(step, params=_substitute_language(step.params, language))
            for step in base.steps
        )
        scenarios.append(
            replace(base, name=f"{name}-{language}", language=language, steps=steps)
        )
    return scenarios


def load_scenario(path: Path, known_devices: Optional[Sequence[str]] = None) -> Scenario:
    """Load one language of a scenario file.

    A language matrix expands into several scenarios; use
    :func:`load_scenarios` for those, or resolve one expanded id with
    :func:`find_scenario`.
    """
    scenarios = load_scenarios(path, known_devices=known_devices)
    if len(scenarios) != 1:
        ids = ", ".join(scenario.scenario_id for scenario in scenarios)
        raise ScenarioError(
            f"{path}: defines {len(scenarios)} language(s); use find_scenario with one of: {ids}"
        )
    return scenarios[0]


def discover_scenarios(
    suite: Optional[str] = None,
    directory: Path = SCENARIO_DIR,
) -> List[Scenario]:
    """Load every scenario, optionally restricted to one suite."""
    if not directory.is_dir():
        raise ScenarioError(f"scenario directory not found: {directory}")
    suites = (suite,) if suite else SUITES

    scenarios: List[Scenario] = []
    for suite_name in suites:
        if suite_name not in SUITES:
            raise ScenarioError(f"unknown suite {suite_name!r}; known: {', '.join(SUITES)}")
        suite_dir = directory / suite_name
        if not suite_dir.is_dir():
            continue
        for path in sorted(suite_dir.glob("*.yaml")):
            scenarios.extend(load_scenarios(path))

    seen: Dict[str, Path] = {}
    for scenario in scenarios:
        previous = seen.get(scenario.name)
        if previous is not None:
            raise ScenarioError(
                f"duplicate scenario name {scenario.name!r} in {previous} and {scenario.source}"
            )
        seen[scenario.name] = scenario.source
    return scenarios


def find_scenarios(identifier: str, directory: Path = SCENARIO_DIR) -> List[Scenario]:
    """Resolve a scenario id, base name or path to every expanded language."""
    candidate = Path(identifier)
    if candidate.suffix == ".yaml" and candidate.is_file():
        return load_scenarios(candidate)

    scenarios = discover_scenarios(directory=directory)
    matches = [
        scenario for scenario in scenarios
        if identifier in (scenario.scenario_id, scenario.name)
    ]
    if not matches:
        # A base name selects the whole matrix, e.g. `localisation-sitemap`.
        matches = [
            scenario for scenario in scenarios
            if identifier in (scenario.base_name, f"{scenario.suite}/{scenario.base_name}")
        ]
    if matches:
        return matches

    known = ", ".join(scenario.scenario_id for scenario in scenarios)
    raise ScenarioError(f"unknown scenario {identifier!r}; known: {known}")


def find_scenario(identifier: str, directory: Path = SCENARIO_DIR) -> Scenario:
    """Resolve ``suite/name``, ``name`` or a path to exactly one scenario."""
    scenarios = find_scenarios(identifier, directory=directory)
    if len(scenarios) != 1:
        ids = ", ".join(scenario.scenario_id for scenario in scenarios)
        raise ScenarioError(
            f"{identifier!r} expands into {len(scenarios)} scenarios; choose one of: {ids}"
        )
    return scenarios[0]
