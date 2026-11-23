"""Dry-run engine and reporting tests.

These exercise the engine end to end without Docker, openHAB, the shim or the
Connect IQ SDK: the null driver records the interaction, network-dependent
steps are skipped, and the reports are written. This is the coverage that keeps
scenario wiring and reporting honest while the Wine driver is still a skeleton.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from e2e import engine
from e2e.config import default_device_id, find_device
from e2e.contract import FAIL, PASS, run_contract, summary
from e2e.engine import run_scenario
from e2e.reporting import Report
from e2e.scenarios import find_scenario
from e2e.sim.null import NullDriver, png_bytes


def _run_dry(tmp_path: Path, scenario_name: str = "smoke/launch-and-poll"):
    scenario = find_scenario(scenario_name)
    device = find_device(default_device_id())
    messages = []
    result = run_scenario(
        scenario,
        device,
        openhab_url="http://openhab.invalid:8080",
        shim_url="http://shim.invalid:8080",
        artifacts_dir=tmp_path / "artifacts",
        goldens_dir=tmp_path / "goldens",
        dry_run=True,
        log=messages.append,
    )
    return scenario, result, messages


def test_dry_run_completes_without_network(tmp_path: Path) -> None:
    scenario, result, messages = _run_dry(tmp_path)
    assert result.status == "passed", result.failures
    assert result.dry_run is True
    assert result.requests == 0
    assert result.steps[0].action == "launch"
    assert any("dry-run" in message for message in messages)
    assert result.skipped_steps, "network-dependent steps must be reported as skipped, not passed"


def test_dry_run_records_a_screenshot_artifact(tmp_path: Path) -> None:
    _scenario, result, _messages = _run_dry(tmp_path)
    screenshots = [path for step in result.steps for path in step.artifacts]
    assert screenshots, "the screenshot step must produce an artifact"
    assert Path(screenshots[0]).is_file()


def test_dry_run_does_not_claim_a_golden_match(tmp_path: Path) -> None:
    _scenario, result, _messages = _run_dry(tmp_path)
    golden_steps = [step for step in result.steps if step.action == "assert"]
    assert golden_steps
    assert all(step.status != "failed" for step in golden_steps)
    assert any(step.status == "skipped" for step in golden_steps)


def test_report_writes_json_junit_and_markdown(tmp_path: Path) -> None:
    _scenario, result, _messages = _run_dry(tmp_path)
    report = Report()
    report.add(result)
    written = report.write_artifacts(tmp_path / "artifacts")

    document = json.loads(written["json"].read_text(encoding="utf-8"))
    assert document["counts"]["passed"] == 1
    assert document["results"][0]["scenario"] == "smoke/launch-and-poll"
    assert document["results"][0]["skipped_steps"]

    tree = ET.parse(written["junit"])
    root = tree.getroot()
    assert root.tag == "testsuites"
    assert root.find(".//testcase").get("name") == "launch-and-poll"

    markdown = written["markdown"].read_text(encoding="utf-8")
    assert "smoke/launch-and-poll" in markdown


def test_report_bundle_contains_the_result(tmp_path: Path) -> None:
    _scenario, result, _messages = _run_dry(tmp_path)
    report = Report()
    report.add(result)
    written = report.write_artifacts(tmp_path / "artifacts")
    bundle = written["bundle:smoke/launch-and-poll"]
    assert bundle.is_file()
    import zipfile

    with zipfile.ZipFile(bundle) as archive:
        assert "result.json" in archive.namelist()


def test_dry_run_passes_the_scenario_language_to_the_driver(
    tmp_path: Path, monkeypatch
) -> None:
    seen = {}

    class RecordingDriver(NullDriver):
        def start(self, device, settings, mode="ble", language="eng") -> None:
            seen["language"] = language
            super().start(device, settings, mode=mode, language=language)

    monkeypatch.setattr(engine, "create_driver", lambda *args, **kwargs: RecordingDriver())
    scenario = find_scenario("extended/localisation-sitemap-deu")
    result = run_scenario(
        scenario,
        find_device(default_device_id()),
        openhab_url="http://openhab.invalid:8080",
        shim_url="http://shim.invalid:8080",
        artifacts_dir=tmp_path / "artifacts",
        dry_run=True,
    )
    assert result.status == "passed", result.failures
    assert seen["language"] == "deu"


def test_null_driver_contract_passes(tmp_path: Path) -> None:
    cases = run_contract(NullDriver(), tmp_path / "contract")
    counts = summary(cases)
    assert counts[FAIL] == 0
    assert counts[PASS] >= 10


def test_png_helper_produces_a_valid_header() -> None:
    data = png_bytes(2, 2)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert int.from_bytes(data[16:20], "big") == 2
    assert int.from_bytes(data[20:24], "big") == 2


def test_failure_is_reported_for_an_unknown_settings_fixture(tmp_path: Path) -> None:
    scenario = find_scenario("smoke/launch-and-poll")
    # A scenario object is frozen; rebuild it with a broken fixture reference to
    # assert the engine reports the problem instead of crashing.
    from dataclasses import replace

    broken = replace(scenario, fixture="does-not-exist")
    result = run_scenario(
        broken,
        find_device(default_device_id()),
        openhab_url="http://openhab.invalid:8080",
        shim_url="http://shim.invalid:8080",
        artifacts_dir=tmp_path / "artifacts",
        dry_run=True,
    )
    assert result.status == "failed"
    assert any("unknown settings fixture" in failure for failure in result.failures)
