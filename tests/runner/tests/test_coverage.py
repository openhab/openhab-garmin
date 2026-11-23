"""Tests for the #140 scope definition and the coverage report.

These are host-free by design: the ``harness`` CI job must fail on a scope gap
without Docker, the SDK or the simulator. The tests cover the scope schema, the
join with scenario ``covers`` keys, the generated document and the issue-level
completeness check.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from e2e.coverage import (
    COVERAGE_DOC,
    build_report,
    document_is_current,
    render_markdown,
)
from e2e.scenarios import discover_scenarios, find_scenario
from e2e.scope import ScopeError, load_scope, scope_ids


def _scope_document(**overrides: object) -> dict:
    document = {
        "version": 1,
        "issue": 140,
        "source": "https://github.com/openhab/openhab-garmin/issues/140",
        "sections": [
            {"id": "errors", "title": "Error handling", "number": "7"},
        ],
        "entries": [
            {"id": "errors.temporary", "section": "errors", "title": "Temporary errors"},
            {"id": "errors.critical", "section": "errors", "title": "Critical errors"},
        ],
        "waivers": [],
    }
    document.update(overrides)
    return document


def _write_scope(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "scope.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# repository scope
# ---------------------------------------------------------------------------


def test_repository_scope_loads() -> None:
    scope = load_scope()
    assert scope.issue == 140
    assert scope.source.endswith("/issues/140")
    assert scope.entries
    assert scope.sections
    assert {section.id for section in scope.sections} >= {
        "sitemap",
        "communication",
        "commands",
        "settings",
        "lifecycle",
        "localisation",
        "errors",
        "limits",
        "resilience",
        "devices",
    }


def test_scope_ids_are_unique_and_stable_looking() -> None:
    ids = scope_ids()
    assert len(ids) == len(set(ids))
    for entry_id in ids:
        assert entry_id == entry_id.lower()
        assert "." in entry_id


def test_every_scope_id_is_covered_or_waived() -> None:
    report = build_report()
    assert report.problems == ()
    assert report.empty == ()
    assert report.covered
    assert report.waived


def test_repository_scenarios_declare_scope_coverage() -> None:
    scenarios = discover_scenarios()
    assert scenarios
    for scenario in scenarios:
        assert scenario.covers, f"{scenario.scenario_id} has no covers entry"


def test_generated_coverage_document_is_current() -> None:
    report = build_report()
    assert document_is_current(report), (
        "tests/docs/coverage.md is stale; run `python -m e2e coverage --write`"
    )


def test_coverage_document_states_the_host_only_status() -> None:
    text = COVERAGE_DOC.read_text(encoding="utf-8")
    assert "Host-only execution" in text
    assert "Docker" in text
    assert "Connect IQ SDK" in text


def test_coverage_document_lists_every_scope_id() -> None:
    text = COVERAGE_DOC.read_text(encoding="utf-8")
    for entry_id in scope_ids():
        assert f"`{entry_id}`" in text


# ---------------------------------------------------------------------------
# scope schema
# ---------------------------------------------------------------------------


def test_scope_loads_a_minimal_document(tmp_path: Path) -> None:
    scope = load_scope(_write_scope(tmp_path, _scope_document()))
    assert [entry.id for entry in scope.entries] == [
        "errors.temporary",
        "errors.critical",
    ]


def test_scope_rejects_an_unknown_top_level_key(tmp_path: Path) -> None:
    document = _scope_document(extra=True)
    with pytest.raises(ScopeError) as error:
        load_scope(_write_scope(tmp_path, document))
    assert "extra" in str(error.value)


def test_scope_rejects_a_duplicate_entry_id(tmp_path: Path) -> None:
    document = _scope_document(
        entries=[
            {"id": "errors.temporary", "section": "errors", "title": "One"},
            {"id": "errors.temporary", "section": "errors", "title": "Two"},
        ]
    )
    with pytest.raises(ScopeError) as error:
        load_scope(_write_scope(tmp_path, document))
    assert "duplicate scope id" in str(error.value)


def test_scope_rejects_an_unknown_section(tmp_path: Path) -> None:
    document = _scope_document(
        entries=[
            {"id": "errors.temporary", "section": "nope", "title": "One"},
        ]
    )
    with pytest.raises(ScopeError) as error:
        load_scope(_write_scope(tmp_path, document))
    assert "unknown section" in str(error.value)


def test_scope_rejects_a_waiver_for_an_unknown_id(tmp_path: Path) -> None:
    document = _scope_document(
        waivers=[{"id": "errors.nope", "reason": "Because."}]
    )
    with pytest.raises(ScopeError) as error:
        load_scope(_write_scope(tmp_path, document))
    assert "unknown scope id" in str(error.value)


def test_scope_rejects_a_waiver_without_a_reason(tmp_path: Path) -> None:
    document = _scope_document(waivers=[{"id": "errors.critical", "reason": " "}])
    with pytest.raises(ScopeError) as error:
        load_scope(_write_scope(tmp_path, document))
    assert "reason" in str(error.value)


def test_scope_rejects_a_duplicate_waiver(tmp_path: Path) -> None:
    document = _scope_document(
        waivers=[
            {"id": "errors.critical", "reason": "One."},
            {"id": "errors.critical", "reason": "Two."},
        ]
    )
    with pytest.raises(ScopeError) as error:
        load_scope(_write_scope(tmp_path, document))
    assert "duplicate waiver" in str(error.value)


@pytest.mark.parametrize("version", [0, 2, "1"])
def test_scope_rejects_an_unknown_version(tmp_path: Path, version: object) -> None:
    with pytest.raises(ScopeError) as error:
        load_scope(_write_scope(tmp_path, _scope_document(version=version)))
    assert "version" in str(error.value)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_report_flags_an_uncovered_entry(tmp_path: Path) -> None:
    scope = load_scope(_write_scope(tmp_path, _scope_document()))
    scenario = find_scenario("smoke/launch-and-poll")
    report = build_report(
        scenarios=[replace(scenario, covers=("errors.temporary",))],
        scope=scope,
        check_device_matrix=False,
    )
    assert [problem.kind for problem in report.problems] == ["uncovered"]
    assert "errors.critical" in report.problems[0].detail
    assert [entry.entry.id for entry in report.empty] == ["errors.critical"]


def test_report_flags_a_redundant_waiver(tmp_path: Path) -> None:
    document = _scope_document(
        waivers=[{"id": "errors.temporary", "reason": "Not needed."}]
    )
    scope = load_scope(_write_scope(tmp_path, document))
    scenario = find_scenario("smoke/launch-and-poll")
    report = build_report(
        scenarios=[
            replace(scenario, covers=("errors.temporary", "errors.critical"))
        ],
        scope=scope,
        check_device_matrix=False,
    )
    assert [problem.kind for problem in report.problems] == ["redundant-waiver"]


def test_report_flags_an_unknown_scenario_scope(tmp_path: Path) -> None:
    scope = load_scope(_write_scope(tmp_path, _scope_document()))
    scenario = find_scenario("smoke/launch-and-poll")
    report = build_report(
        scenarios=[
            replace(
                scenario,
                covers=("errors.temporary", "errors.critical", "errors.unknown"),
            )
        ],
        scope=scope,
        check_device_matrix=False,
    )
    assert "unknown-scope" in [problem.kind for problem in report.problems]


def test_report_counts_cover_waived_and_empty(tmp_path: Path) -> None:
    document = _scope_document(
        waivers=[{"id": "errors.critical", "reason": "Deliberate."}]
    )
    scope = load_scope(_write_scope(tmp_path, document))
    scenario = find_scenario("smoke/launch-and-poll")
    report = build_report(
        scenarios=[replace(scenario, covers=("errors.temporary",))],
        scope=scope,
        check_device_matrix=False,
    )
    assert report.problems == ()
    assert len(report.covered) == 1
    assert len(report.waived) == 1
    assert report.empty == ()


def test_markdown_shows_waiver_reason_and_manual_checks(tmp_path: Path) -> None:
    document = _scope_document(
        waivers=[{"id": "errors.critical", "reason": "Needs a physical device."}]
    )
    scope = load_scope(_write_scope(tmp_path, document))
    scenario = find_scenario("smoke/launch-and-poll")
    report = build_report(
        scenarios=[
            replace(
                scenario,
                covers=("errors.temporary",),
                manual_checks=("Review the toast by hand.",),
            )
        ],
        scope=scope,
        check_device_matrix=False,
    )
    text = render_markdown(report)
    assert "Needs a physical device." in text
    assert "Review the toast by hand." in text
    assert "`smoke/launch-and-poll`" in text
