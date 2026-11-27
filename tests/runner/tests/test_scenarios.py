"""Scenario loader tests.

The loader is the gate between "someone wrote a YAML file" and "the harness
runs it", so these tests are about rejection: every malformed scenario must
fail at load time with a message that names the file and the key, and every
rule in the repository must load.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from e2e.config import SCENARIO_DIR
from e2e.scenarios import (
    LAUNCH_SCENARIO_MINIMUM_S,
    LAUNCH_SETTLE_WORST_CASE_S,
    LAUNCH_RETRY_WORST_CASE_S,
    LAUNCH_STEP_WORST_CASE_S,
    ScenarioError,
    discover_scenarios,
    find_scenario,
    find_scenarios,
    launch_budget_problems,
    load_scenario,
    load_scenarios,
    parse_steps,
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


def test_localisation_scenarios_expand_to_every_shipped_language() -> None:
    scenarios = discover_scenarios(suite="extended")
    localisation = [scenario for scenario in scenarios
                    if scenario.source.name.startswith("localisation-")]
    assert localisation, "the localisation scenarios must exist"
    languages = {language for language in ("eng", "deu", "fre", "ita", "spa", "pol")}
    for source in {scenario.source for scenario in localisation}:
        expanded = [scenario for scenario in localisation if scenario.source == source]
        assert {scenario.language for scenario in expanded} == languages
        assert len(expanded) == len(languages)
    assert all(scenario.tier == "ui" for scenario in localisation)


def test_find_scenarios_resolves_a_language_matrix_by_base_name() -> None:
    scenarios = find_scenarios("localisation-sitemap")
    assert [scenario.language for scenario in scenarios] == [
        "eng", "deu", "fre", "ita", "spa", "pol",
    ]
    with pytest.raises(ScenarioError) as error:
        find_scenario("localisation-sitemap")
    assert "expands into 6 scenarios" in str(error.value)


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


def test_covers_accepts_known_scope_ids(tmp_path: Path) -> None:
    document = _with(covers=["errors.temporary", "commands.types"])
    scenario = load_scenario(_write(tmp_path, document))
    assert scenario.covers == ("errors.temporary", "commands.types")


def test_covers_rejects_an_unknown_scope_id(tmp_path: Path) -> None:
    document = _with(covers=["errors.temporary", "made.up"])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "unknown scope id" in str(error.value)
    assert "made.up" in str(error.value)


def test_covers_rejects_a_duplicate_scope_id(tmp_path: Path) -> None:
    document = _with(covers=["errors.temporary", "errors.temporary"])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "repeats" in str(error.value)


@pytest.mark.parametrize("value", ["errors.temporary", [], [""], [1]])
def test_covers_must_be_a_non_empty_list_of_strings(tmp_path: Path, value) -> None:
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, _with(covers=value)))
    assert "covers" in str(error.value)


def test_manual_checks_accept_a_list_of_strings(tmp_path: Path) -> None:
    document = _with(manual_checks=["Check the toggle by hand."])
    scenario = load_scenario(_write(tmp_path, document))
    assert scenario.manual_checks == ("Check the toggle by hand.",)


@pytest.mark.parametrize("value", ["one check", [], [""]])
def test_manual_checks_must_be_a_non_empty_list_of_strings(tmp_path: Path, value) -> None:
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, _with(manual_checks=value)))
    assert "manual_checks" in str(error.value)


def test_covers_survives_language_matrix_expansion(tmp_path: Path) -> None:
    scenarios = load_scenarios(_write(tmp_path, _matrix(covers=["errors.temporary"])))
    assert [scenario.covers for scenario in scenarios] == [
        ("errors.temporary",),
        ("errors.temporary",),
    ]


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


def _matrix(**overrides):
    document = _with(**overrides)
    del document["language"]
    document.setdefault("languages", ["eng", "deu"])
    return document


def test_language_matrix_expands_one_scenario_per_language(tmp_path: Path) -> None:
    scenarios = load_scenarios(_write(tmp_path, _matrix()))
    assert [scenario.name for scenario in scenarios] == [
        "sample-scenario-eng",
        "sample-scenario-deu",
    ]
    assert [scenario.language for scenario in scenarios] == ["eng", "deu"]
    assert {scenario.base_name for scenario in scenarios} == {"sample-scenario"}
    assert {scenario.source for scenario in scenarios} == {tmp_path / "scenario.yaml"}


def test_language_matrix_substitutes_the_language_placeholder(tmp_path: Path) -> None:
    document = _matrix(
        steps=[
            {"action": "launch"},
            {"action": "wait_for_event", "event": "LANGUAGE", "fields": {"code": "${language}"}},
        ]
    )
    scenarios = load_scenarios(_write(tmp_path, document))
    codes = [scenario.steps[1].get("fields")["code"] for scenario in scenarios]
    assert codes == ["eng", "deu"]


def test_language_and_languages_are_mutually_exclusive(tmp_path: Path) -> None:
    document = _with(languages=["eng"])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "not both" in str(error.value)


@pytest.mark.parametrize(
    "languages,message",
    [(["eng", "eng"], "repeats"), (["eng", "jpn"], "languages[1]")],
)
def test_language_matrix_rejects_bad_lists(
    tmp_path: Path, languages: list, message: str
) -> None:
    document = _matrix(languages=languages)
    with pytest.raises(ScenarioError) as error:
        load_scenarios(_write(tmp_path, document))
    assert message in str(error.value)


def test_load_scenario_refuses_to_pick_a_matrix_language(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, _matrix()))
    assert "defines 2 language(s)" in str(error.value)


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


def test_assert_never_error_view_is_a_history_condition(tmp_path: Path) -> None:
    document = _with(
        steps=[{"action": "launch"}, {"action": "assert", "never_error_view": True}]
    )
    scenario = load_scenario(_write(tmp_path, document))
    assert scenario.steps[-1].get("never_error_view") is True


def test_assert_never_error_view_rejects_false(tmp_path: Path) -> None:
    document = _with(
        steps=[{"action": "launch"}, {"action": "assert", "never_error_view": False}]
    )
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "no_error_view" in str(error.value)


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


def test_install_faults_is_validated_by_the_shared_grammar(tmp_path: Path) -> None:
    document = _with(steps=[
        {"action": "launch"},
        {"action": "install_faults",
         "rules": [{"name": "broken", "match": {}, "response": {"status": 9999}}]},
    ])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "status" in str(error.value)


def test_install_faults_needs_rules(tmp_path: Path) -> None:
    document = _with(steps=[{"action": "launch"}, {"action": "install_faults"}])
    with pytest.raises(ScenarioError) as error:
        load_scenario(_write(tmp_path, document))
    assert "rules" in str(error.value)


def test_install_faults_keeps_the_document_the_shim_accepts(tmp_path: Path) -> None:
    document = _with(steps=[
        {"action": "launch"},
        {"action": "install_faults", "rules": [
            {"name": "unavailable",
             "match": {"method": "GET", "path_prefix": "/rest/sitemaps/"},
             "times": 3,
             "response": {"status": 503}},
        ]},
    ])

    step = load_scenario(_write(tmp_path, document)).steps[1]
    rules = step.get("rules")

    assert rules[0]["name"] == "unavailable"
    assert rules[0]["times"] == 3
    assert rules[0]["response"]["status"] == 503


def test_request_match_accepts_the_fault_selector(tmp_path: Path) -> None:
    document = _with(steps=[
        {"action": "launch"},
        {"action": "assert",
         "shim_requests": {"path_prefix": "/rest/sitemaps/", "matched_rule": "truncated",
                           "faulted": True, "min_count": 2}},
    ])

    spec = load_scenario(_write(tmp_path, document)).steps[1].get("shim_requests")

    assert spec["matched_rule"] == "truncated"
    assert spec["faulted"] is True


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


# ---------------------------------------------------------------------------
# whole-scenario budget versus the launch step
# ---------------------------------------------------------------------------

def _load_document(tmp_path: Path, name: str, **overrides):
    document = dict(BASE)
    document.update({"name": name})
    document.update(overrides)
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return load_scenario(path)


def test_every_launch_scenario_budgets_for_the_launch_worst_case() -> None:
    """A legitimate slow launch must not present itself as an app failure."""
    problems = launch_budget_problems(discover_scenarios())
    assert not problems, (
        "these scenarios launch the app but budget less than "
        f"{LAUNCH_SCENARIO_MINIMUM_S}s, which cannot fit the launch worst case "
        f"({LAUNCH_STEP_WORST_CASE_S}s of settle plus retries) and their own "
        f"steps: {problems}"
    )


def test_the_launch_minimum_mirrors_the_driver() -> None:
    """The invariant is only honest while its numbers match the driver."""
    from e2e.sim import wine

    assert LAUNCH_SETTLE_WORST_CASE_S == int(wine.LAUNCH_SETTLE_MAX_S)
    assert LAUNCH_RETRY_WORST_CASE_S == int(wine.LAUNCH_RETRY_BUDGET_S)


def test_an_undersized_launch_budget_is_reported(tmp_path: Path) -> None:
    scenario = _load_document(tmp_path, "short-launch", timeout_s=90)
    assert launch_budget_problems([scenario]) == [("smoke/short-launch", 90)]


def test_a_scenario_without_launch_is_left_alone() -> None:
    """The invariant is about launching; the schema requires one today."""
    stub = SimpleNamespace(
        scenario_id="smoke/no-launch",
        timeout_s=30,
        steps=[SimpleNamespace(action="screenshot")],
    )
    assert launch_budget_problems([stub]) == []

# ---------------------------------------------------------------------------
# golden state guard: a wrong-state capture must never become a baseline
# ---------------------------------------------------------------------------

def _golden(**overrides):
    spec = {
        "name": "homepage",
        "max_diff_ratio": 0.002,
        "expect": {"category": "sitemap", "view": "HomepageMenu"},
        "min_ink_components": 40,
    }
    spec.update(overrides)
    document = dict(BASE)
    document["steps"] = [{"action": "launch"}, {"action": "assert", "golden": spec}]
    return document


def _parse(document):
    return parse_steps(document["steps"])


def test_golden_requires_an_expected_view() -> None:
    document = _golden()
    del document["steps"][1]["golden"]["expect"]
    with pytest.raises(ScenarioError) as error:
        _parse(document)
    assert "'expect' is required" in str(error.value)


def test_golden_expect_needs_a_category_or_a_view() -> None:
    with pytest.raises(ScenarioError) as error:
        _parse(_golden(expect={}))
    assert "needs 'category' or 'view'" in str(error.value)


def test_golden_requires_a_measured_ink_floor() -> None:
    document = _golden()
    del document["steps"][1]["golden"]["min_ink_components"]
    with pytest.raises(ScenarioError) as error:
        _parse(document)
    assert "min_ink_components" in str(error.value)


def test_golden_default_ratio_tolerates_the_status_strip_only() -> None:
    document = _golden()
    del document["steps"][1]["golden"]["max_diff_ratio"]
    steps = _parse(document)
    golden = [step for step in steps if step.action == "assert"][0].params["golden"]
    from e2e.assertions import DEFAULT_MAX_DIFF_RATIO

    assert golden["max_diff_ratio"] == DEFAULT_MAX_DIFF_RATIO
    assert DEFAULT_MAX_DIFF_RATIO <= 0.005, (
        "the default must stay below the ~1.6% a wrong view differs by, or a "
        "glance baseline would pass"
    )


def test_wait_for_view_needs_a_category_or_a_view() -> None:
    document = dict(BASE)
    document["steps"] = [{"action": "launch"}, {"action": "wait_for_view"}]
    with pytest.raises(ScenarioError) as error:
        _parse(document)
    assert "needs 'category' or 'view'" in str(error.value)


def test_wait_for_view_accepts_the_menu_and_defaults_the_timeout() -> None:
    document = dict(BASE)
    document["steps"] = [
        {"action": "launch"},
        {"action": "wait_for_view", "category": "sitemap", "view": "HomepageMenu"},
    ]
    step = _parse(document)[1]
    assert step.action == "wait_for_view"
    assert step.params["category"] == "sitemap"
    assert step.params["view"] == "HomepageMenu"
    assert step.params["timeout_ms"] == 30_000

# ---------------------------------------------------------------------------
# measured presses
# ---------------------------------------------------------------------------


def test_press_accepts_a_measured_expectation() -> None:
    document = dict(BASE)
    document["steps"] = [
        {"action": "launch"},
        {"action": "press", "button": "ENTER", "expect": "effect"},
    ]
    step = _parse(document)[1]
    assert step.params["expect"] == "effect"
    assert step.params["timeout_ms"] == 4000


def test_press_rejects_an_unknown_expectation() -> None:
    document = dict(BASE)
    document["steps"] = [
        {"action": "launch"},
        {"action": "press", "button": "ENTER", "expect": "maybe"},
    ]
    with pytest.raises(ScenarioError) as error:
        _parse(document)
    assert "expect" in str(error.value)


def test_press_rejects_a_timeout_without_an_expectation() -> None:
    """An unmeasured press has nothing to wait for, so the option is a typo."""
    document = dict(BASE)
    document["steps"] = [
        {"action": "launch"},
        {"action": "press", "button": "ENTER", "timeout_ms": 5000},
    ]
    with pytest.raises(ScenarioError) as error:
        _parse(document)
    assert "timeout_ms" in str(error.value)