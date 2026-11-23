"""Scenario loader tests.

The loader is the gate between "someone wrote a YAML file" and "the harness
runs it", so these tests are about rejection: every malformed scenario must
fail at load time with a message that names the file and the key, and every
rule in the repository must load.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from e2e.config import SCENARIO_DIR
from e2e.scenarios import (
    ScenarioError,
    discover_scenarios,
    find_scenario,
    load_scenario,
)

BASE = {
    "name": "sample-scenario",
    "description": "A minimal valid scenario.",
    "tier": "protocol",
    "suite": "smoke",
    "device": "fenix7",
    "mode": "ble",
    "fixture": "default",
    "openhab": {"profile": "stable", "sitemap": "garmin_e2e", "items": {"E2E_Light": "ON"}},
    "language": "eng",
    "timeout_s": 60,
    "steps": [{"action": "launch"}],
}


def _write(tmp_path: Path, document: dict, name: str = "scenario.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _with(**overrides):
    document = yaml.safe_load(yaml.safe_dump(BASE))
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# repository scenarios
# ---------------------------------------------------------------------------


def test_repository_scenarios_load() -> None:
    scenarios = discover_scenarios()
    assert scenarios, "the repository must contain executable scenarios"
    suites = {scenario.suite for scenario in scenarios}
    assert {"smoke", "regression", "extended"} <= suites


def test_repository_scenarios_are_unique_and_named_for_their_suite() -> None:
    scenarios = discover_scenarios()
    names = [scenario.name for scenario in scenarios]
    assert len(names) == len(set(names))
    for scenario in scenarios:
        assert scenario.scenario_id.startswith(scenario.suite + "/")
        assert scenario.source.parent.name == scenario.suite


def test_smoke_suite_has_a_protocol_and_a_ui_scenario() -> None:
    tiers = {scenario.tier for scenario in discover_scenarios(suite="smoke")}
    assert "ui" in tiers


def test_fault_scenarios_reference_a_shim_rule() -> None:
    for scenario in discover_scenarios(suite="regression") + discover_scenarios(suite="extended"):
        document = yaml.safe_load(scenario.source.read_text(encoding="utf-8"))
        if "shim" in document:
            assert scenario.shim_rules, f"{scenario.scenario_id}: shim block without rules"


def test_find_scenario_accepts_suite_name_and_path() -> None:
    by_name = find_scenario("smoke/launch-and-poll")
    by_short_name = find_scenario("launch-and-poll")
    by_path = find_scenario(str(by_name.source))
    assert by_name.name == by_short_name.name == by_path.name == "launch-and-poll"


def test_find_scenario_reports_unknown_names() -> None:
    with pytest.raises(ScenarioError) as error:
        find_scenario("does-not-exist")
    assert "unknown scenario" in str(error.value)


# ---------------------------------------------------------------------------
# top-level validation
# ---------------------------------------------------------------------------


def test_minimal_scenario_loads(tmp_path: Path) -> None:
    scenario = load_scenario(_write(tmp_path, BASE))
    assert scenario.name == "sample-scenario"
    assert scenario.openhab.sitemap == "garmin_e2e"
    assert scenario.steps[0].action == "launch"
    assert not scenario.requires_trace_events


def test_requires_trace_events_is_derived_from_steps(tmp_path: Path) -> None:
    document = _with(
        steps=[
            {"action": "launch"},
            {"action": "wait_for_event", "contains": "S:EMRES", "timeout_ms": 1000},
        ]
    )
    assert load_scenario(_write(tmp_path, document)).requires_trace_events


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    document = _with(unexpected=True)
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "unexpected" in str(error.value)


def test_missing_required_key_is_rejected(tmp_path: Path) -> None:
    document = _with()
    del document["timeout_s"]
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "timeout_s" in str(error.value)


@pytest.mark.parametrize("value", ["smoke", "regression", "extended"])
def test_known_suites_are_accepted(tmp_path: Path, value: str) -> None:
    assert load_scenario(_write(tmp_path, _with(suite=value))).suite == value


def test_unknown_suite_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError):
        load_scenario(_write(tmp_path, _with(suite="nightly")))


def test_suite_must_match_directory(tmp_path: Path) -> None:
    suite_dir = tmp_path / "regression"
    suite_dir.mkdir()
    path = _write(suite_dir, _with(suite="smoke"))
    with pytest.raises(ScenarioError) as error:
        load_scenario(path)
    assert "does not match directory" in str(error.value)


def test_invalid_name_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError):
        load_scenario(_write(tmp_path, _with(name="Not Kebab Case")))


def test_unknown_device_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, _with(device="nokia3310")))
    assert "unknown device" in str(error.value)


def test_any_device_is_accepted(tmp_path: Path) -> None:
    assert load_scenario(_write(tmp_path, _with(device="any"))).device == "any"


@pytest.mark.parametrize("field,value", [("tier", "visual"), ("mode", "lte")])
def test_invalid_enums_are_rejected(tmp_path: Path, field: str, value: str) -> None:
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, _with(**{field: value})))
    assert field in str(error.value)


@pytest.mark.parametrize("value", [0, 1801])
def test_timeout_bounds(tmp_path: Path, value: int) -> None:
    with pytest.raises(ScenarioError):
        load_scenario(_write(tmp_path, _with(timeout_s=value)))


def test_unknown_language_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError):
        load_scenario(_write(tmp_path, _with(language="jpn")))


def test_fixture_must_be_a_name_not_a_path(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, _with(fixture="../secrets")))
    assert "fixture must be" in str(error.value)


# ---------------------------------------------------------------------------
# openhab block
# ---------------------------------------------------------------------------


def test_openhab_profile_defaults_to_stable(tmp_path: Path) -> None:
    document = _with(openhab={"sitemap": "garmin_e2e"})
    assert load_scenario(_write(tmp_path, document)).openhab.profile == "stable"


def test_unknown_openhab_profile_is_rejected(tmp_path: Path) -> None:
    document = _with(openhab={"profile": "nightly", "sitemap": "garmin_e2e"})
    with pytest.raises(ScenarioError):
        load_scenario(_write(tmp_path, document))


def test_unquoted_on_is_rejected_as_a_state(tmp_path: Path) -> None:
    # YAML 1.1 parses a bare ON as a boolean; that is a fixture bug worth
    # catching at load time rather than seeding "True" into an item.
    path = tmp_path / "scenario.yaml"
    path.write_text(
        yaml.safe_dump(BASE, sort_keys=False).replace("E2E_Light: 'ON'", "E2E_Light: ON"),
        encoding="utf-8",
    )
    with pytest.raises(ScenarioError) as error:
        load_scenario(path)
    assert "state must be a string" in str(error.value)


def test_numeric_state_is_coerced_to_string(tmp_path: Path) -> None:
    document = _with(openhab={"sitemap": "garmin_e2e", "items": {"E2E_Temperature": 21.5}})
    scenario = load_scenario(_write(tmp_path, document))
    assert scenario.openhab.items["E2E_Temperature"] == "21.5"


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


def test_steps_must_start_with_launch(tmp_path: Path) -> None:
    document = _with(steps=[{"action": "wait", "ms": 10}, {"action": "launch"}])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "first step must be 'launch'" in str(error.value)


def test_exactly_one_launch_is_required(tmp_path: Path) -> None:
    document = _with(steps=[{"action": "launch"}, {"action": "launch"}])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "exactly one 'launch'" in str(error.value)


def test_unknown_action_is_rejected(tmp_path: Path) -> None:
    document = _with(steps=[{"action": "launch"}, {"action": "swipe"}])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "unknown action" in str(error.value)


def test_unknown_step_key_is_rejected(tmp_path: Path) -> None:
    document = _with(steps=[{"action": "launch", "timeout": 5}])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "unknown key" in str(error.value)


def test_press_requires_a_known_button(tmp_path: Path) -> None:
    document = _with(steps=[{"action": "launch"}, {"action": "press", "button": "TAP"}])
    with pytest.raises(ScenarioError):
        load_scenario(_write(tmp_path, document))


def test_wait_requires_ms(tmp_path: Path) -> None:
    document = _with(steps=[{"action": "launch"}, {"action": "wait"}])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "'ms' is required" in str(error.value)


def test_wait_for_request_needs_a_target(tmp_path: Path) -> None:
    document = _with(steps=[{"action": "launch"}, {"action": "wait_for_request"}])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "needs 'path' or 'path_prefix'" in str(error.value)


def test_wait_for_event_requires_a_target(tmp_path: Path) -> None:
    document = _with(steps=[{"action": "launch"}, {"action": "wait_for_event"}])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "needs 'event'" in str(error.value)


def test_wait_for_event_rejects_fields_a_known_event_never_emits(tmp_path: Path) -> None:
    document = _with(
        steps=[
            {"action": "launch"},
            {"action": "wait_for_event", "event": "ERROR", "fields": {"kindd": "fatal"}},
        ]
    )
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    message = str(error.value)
    assert "kindd" in message and "valid fields" in message


def test_wait_for_event_accepts_the_documented_fields(tmp_path: Path) -> None:
    document = _with(
        steps=[
            {"action": "launch"},
            {
                "action": "wait_for_event",
                "event": "ERROR",
                "fields": {"kind": "critical", "source": "sitemap"},
            },
        ]
    )
    step = load_scenario(_write(tmp_path, document)).steps[1]
    assert step.get("event") == "ERROR"
    assert step.get("fields") == {"kind": "critical", "source": "sitemap"}


def test_wait_for_event_allows_unknown_events(tmp_path: Path) -> None:
    document = _with(
        steps=[
            {"action": "launch"},
            {"action": "wait_for_event", "event": "FUTURE", "fields": {"anything": "goes"}},
        ]
    )
    assert load_scenario(_write(tmp_path, document)).steps[1].get("event") == "FUTURE"


def test_wait_for_request_rejects_both_path_forms(tmp_path: Path) -> None:
    document = _with(
        steps=[
            {"action": "launch"},
            {"action": "wait_for_request", "path": "/x", "path_prefix": "/x"},
        ]
    )
    with pytest.raises(ScenarioError):
        load_scenario(_write(tmp_path, document))


def test_assert_needs_a_condition(tmp_path: Path) -> None:
    document = _with(steps=[{"action": "launch"}, {"action": "assert"}])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "needs 'shim_requests'" in str(error.value)


def test_assert_request_spec_needs_an_explicit_count(tmp_path: Path) -> None:
    document = _with(
        steps=[
            {"action": "launch"},
            {"action": "assert", "shim_requests": {"method": "GET", "path_prefix": "/rest/"}},
        ]
    )
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "min_count" in str(error.value)


def test_assert_golden_ratio_bounds(tmp_path: Path) -> None:
    document = _with(
        steps=[
            {"action": "launch"},
            {"action": "screenshot", "name": "homepage"},
            {"action": "assert", "golden": {"name": "homepage", "max_diff_ratio": 2}},
        ]
    )
    with pytest.raises(ScenarioError):
        load_scenario(_write(tmp_path, document))


def test_screenshot_name_is_a_slug(tmp_path: Path) -> None:
    document = _with(
        steps=[{"action": "launch"}, {"action": "screenshot", "name": "Home Page"}]
    )
    with pytest.raises(ScenarioError):
        load_scenario(_write(tmp_path, document))


def test_settings_overrides_reject_unknown_properties(tmp_path: Path) -> None:
    document = _with(settings={"pollingIntervall_0": 500})
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "unknown application property" in str(error.value)


def test_settings_overrides_accept_known_properties(tmp_path: Path) -> None:
    document = _with(settings={"pollingInterval_0": 250})
    assert load_scenario(_write(tmp_path, document)).settings == {"pollingInterval_0": 250}


# ---------------------------------------------------------------------------
# shim rules are validated by the shared grammar
# ---------------------------------------------------------------------------


def test_shim_rule_errors_surface_at_load_time(tmp_path: Path) -> None:
    document = _with(
        shim={"rules": [{"name": "broken", "match": {}, "response": {"status": 9999}}]}
    )
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "status" in str(error.value)


def test_shim_rule_is_kept_verbatim(tmp_path: Path) -> None:
    rule = {
        "name": "unavailable",
        "match": {"method": "GET", "path_prefix": "/rest/sitemaps/"},
        "times": 2,
        "response": {"status": 503},
    }
    scenario = load_scenario(_write(tmp_path, _with(shim={"rules": [rule]})))
    assert scenario.shim_script == {"version": 1, "rules": [rule]}


def test_shim_must_only_contain_rules(tmp_path: Path) -> None:
    document = _with(shim={"script": []})
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "shim" in str(error.value)


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_discovery_rejects_duplicate_names(tmp_path: Path) -> None:
    for suite in ("smoke", "regression"):
        directory = tmp_path / suite
        directory.mkdir()
        _write(directory, _with(suite=suite, name="duplicate-name"))
    with pytest.raises(ScenarioError) as error:
        discover_scenarios(directory=tmp_path)
    assert "duplicate scenario name" in str(error.value)


def test_discovery_can_be_limited_to_one_suite(tmp_path: Path) -> None:
    for suite in ("smoke", "regression"):
        directory = tmp_path / suite
        directory.mkdir()
        _write(directory, _with(suite=suite, name=f"{suite}-one"))
    scenarios = discover_scenarios(suite="smoke", directory=tmp_path)
    assert [scenario.name for scenario in scenarios] == ["smoke-one"]


def test_scenario_directory_constant_points_at_the_repository() -> None:
    assert SCENARIO_DIR.is_dir()
    assert (SCENARIO_DIR / "smoke").is_dir()
