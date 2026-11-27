"""Dry-run engine and reporting tests.

These exercise the engine end to end without Docker, openHAB, the shim or the
Connect IQ SDK: the null driver records the interaction, network-dependent
steps are skipped, and the reports are written. This is the coverage that keeps
scenario wiring and reporting honest without needing Wine or a display.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from typing import ClassVar, List, Sequence

import pytest

from e2e import cli
from e2e import engine
from e2e.config import default_device_id, find_device
from e2e.contract import FAIL, PASS, run_contract, summary
from e2e.engine import run_scenario
from e2e.reporting import Report
from e2e.scenarios import find_scenario, parse_steps
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


def test_a_driver_that_provisions_the_language_runs_it(
    tmp_path: Path, monkeypatch
) -> None:
    """Provisioning is per language: a measured index runs, an unmeasured one skips."""
    seen = {}

    class IndexDriver(NullDriver):
        name = "wine"

        def can_provision_language(self, language: str) -> bool:
            return language in ("eng", "deu")

        def start(self, device, settings, mode="ble", language="eng") -> None:
            seen["language"] = language
            super().start(device, settings, mode=mode, language=language)

    monkeypatch.setattr(engine, "create_driver", lambda *args, **kwargs: IndexDriver())
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

    blocked = run_scenario(
        find_scenario("extended/localisation-sitemap-pol"),
        find_device(default_device_id()),
        openhab_url="http://openhab.invalid:8080",
        shim_url="http://shim.invalid:8080",
        artifacts_dir=tmp_path / "artifacts",
        dry_run=True,
    )
    assert blocked.status == "skipped"
    assert "language-scan" in blocked.skip_reason


def test_language_that_cannot_be_provisioned_is_reported_as_skipped(
    tmp_path: Path, monkeypatch
) -> None:
    class NoLanguageDriver(NullDriver):
        name = "wine"
        provisions_language = False

    monkeypatch.setattr(engine, "create_driver", lambda *args, **kwargs: NoLanguageDriver())
    scenario = find_scenario("extended/localisation-sitemap-deu")
    messages = []
    result = run_scenario(
        scenario,
        find_device(default_device_id()),
        openhab_url="http://openhab.invalid:8080",
        shim_url="http://shim.invalid:8080",
        artifacts_dir=tmp_path / "artifacts",
        dry_run=True,
        log=messages.append,
    )
    assert result.status == "skipped"
    assert "cannot be provisioned" in result.skip_reason
    assert any("SKIPPED" in message for message in messages)
    assert result.steps == [], "a blocked run must not pretend to have executed steps"


def test_runtime_errors_from_the_simulator_log_reach_the_report(
    tmp_path: Path, monkeypatch
) -> None:
    class Entry:
        def __init__(self, kind: str, label: str) -> None:
            self.kind = kind
            self.label = label

        def to_document(self) -> dict:
            return {"kind": self.kind, "label": self.label, "message": ""}

    class RuntimeErrorDriver(NullDriver):
        name = "wine"

        def runtime_errors(self):
            return (Entry("symbol-not-found", "build/device mismatch"),)

    monkeypatch.setattr(engine, "create_driver", lambda *args, **kwargs: RuntimeErrorDriver())
    scenario = find_scenario("smoke/launch-and-poll")
    result = run_scenario(
        scenario,
        find_device(default_device_id()),
        openhab_url="http://openhab.invalid:8080",
        shim_url="http://shim.invalid:8080",
        artifacts_dir=tmp_path / "artifacts",
        dry_run=True,
    )
    assert result.status == "passed"
    assert result.runtime_errors == [
        {"kind": "symbol-not-found", "label": "build/device mismatch", "message": ""}
    ]

    report = Report()
    report.add(result)
    written = report.write_artifacts(tmp_path / "report")
    markdown = written["markdown"].read_text(encoding="utf-8")
    assert "simulator log: symbol-not-found" in markdown
    junit = written["junit"].read_text(encoding="utf-8")
    assert "symbol-not-found" in junit
    document = json.loads(written["json"].read_text(encoding="utf-8"))
    assert document["results"][0]["runtime_errors"][0]["kind"] == "symbol-not-found"


def test_null_driver_contract_passes(tmp_path: Path) -> None:
    cases = run_contract(NullDriver(), tmp_path / "contract")
    counts = summary(cases)
    assert counts[FAIL] == 0
    assert counts[PASS] >= 10


class _ScriptedEventsDriver(NullDriver):
    """Null driver with a fixed console stream, for assertion-plumbing tests."""

    supports_events: ClassVar[bool] = True

    def __init__(self, lines: Sequence[str], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._lines: List[str] = list(lines)

    def events(self) -> Sequence[str]:
        return list(self._lines)


#: A critical error was raised and presented, then dismissed by the recovery.
DISMISSED_ERROR_VIEW = (
    "E2E:LANGUAGE;code=eng",
    "E2E:VIEW;category=sitemap;view=LoadingView;depth=1",
    "E2E:ERROR;kind=critical;id=communication.sitemap;code=404;source=sitemap",
    "E2E:ERRORVIEW;id=S:-104;code=-",
    "E2E:VIEW;category=error;view=ErrorView;depth=1",
    "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1",
)

#: The error view is still on screen; polling continues behind it.
UNDISMISSED_ERROR_VIEW = (
    "E2E:VIEW;category=sitemap;view=LoadingView;depth=1",
    "E2E:ERRORVIEW;id=S:-104;code=-",
    "E2E:VIEW;category=error;view=ErrorView;depth=1",
)

#: A healthy run: no error view at all.
CLEAN_TRACE = ("E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1",)


def _run_assert(tmp_path: Path, monkeypatch, lines: Sequence[str], **params: object):
    """Run a launch plus one assert step against a scripted trace."""
    scenario = find_scenario("smoke/launch-and-poll")
    steps = parse_steps(
        [{"action": "launch"}, {"action": "assert", **params}],
        "test",
    )
    monkeypatch.setattr(
        engine, "create_driver", lambda *args, **kwargs: _ScriptedEventsDriver(lines)
    )
    return run_scenario(
        replace(scenario, steps=steps),
        find_device(default_device_id()),
        openhab_url="http://openhab.invalid:8080",
        shim_url="http://shim.invalid:8080",
        artifacts_dir=tmp_path / "artifacts",
        dry_run=True,
    )


def test_recovery_assertion_passes_when_the_error_view_was_dismissed(
    tmp_path: Path, monkeypatch
) -> None:
    result = _run_assert(tmp_path, monkeypatch, DISMISSED_ERROR_VIEW, no_error_view=True)
    assert result.status == "passed", result.failures
    assert result.steps[-1].status == "passed"
    assert "current view: sitemap" in result.steps[-1].message


def test_recovery_assertion_fails_when_the_error_view_is_still_shown(
    tmp_path: Path, monkeypatch
) -> None:
    result = _run_assert(tmp_path, monkeypatch, UNDISMISSED_ERROR_VIEW, no_error_view=True)
    assert result.status == "failed"
    assert any("category=error" in failure for failure in result.failures)


def test_current_error_view_assertion_passes_while_it_is_shown(
    tmp_path: Path, monkeypatch
) -> None:
    result = _run_assert(tmp_path, monkeypatch, UNDISMISSED_ERROR_VIEW, no_error_view=False)
    assert result.status == "passed", result.failures


def test_current_error_view_assertion_fails_when_it_never_appeared(
    tmp_path: Path, monkeypatch
) -> None:
    result = _run_assert(tmp_path, monkeypatch, CLEAN_TRACE, no_error_view=False)
    assert result.status == "failed"
    assert any(
        "expected the current view to be the error view" in failure
        for failure in result.failures
    )


def test_history_assertion_fails_for_a_dismissed_error_view(
    tmp_path: Path, monkeypatch
) -> None:
    result = _run_assert(tmp_path, monkeypatch, DISMISSED_ERROR_VIEW, never_error_view=True)
    assert result.status == "failed"
    assert any("ERRORVIEW" in failure for failure in result.failures)


def test_history_assertion_passes_without_an_error_view(tmp_path: Path, monkeypatch) -> None:
    result = _run_assert(tmp_path, monkeypatch, CLEAN_TRACE, never_error_view=True)
    assert result.status == "passed", result.failures


def test_png_helper_produces_a_valid_header() -> None:
    data = png_bytes(2, 2)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert int.from_bytes(data[16:20], "big") == 2
    assert int.from_bytes(data[20:24], "big") == 2


def test_failure_is_reported_for_an_unknown_settings_fixture(tmp_path: Path) -> None:
    scenario = find_scenario("smoke/launch-and-poll")
    # A scenario object is frozen; rebuild it with a broken fixture reference to
    # assert the engine reports the problem instead of crashing.
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


# ---------------------------------------------------------------------------
# golden capture capability (decided by the font pack, not the driver name)
# ---------------------------------------------------------------------------

def _font_pack(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "FNT_TEST_0.cft").write_text("face", encoding="utf-8")
    (path / "FNT_TEST_0.md5").write_text("hash", encoding="utf-8")
    return path


def test_update_goldens_is_refused_without_device_fonts(tmp_path, monkeypatch, capsys) -> None:
    """A capture from a crashed render must never become the baseline."""
    absent = tmp_path / "absent"
    monkeypatch.setenv("GARMIN_FONTS_DIR", str(absent))

    code = cli.main(["run", "smoke/launch-and-poll", "--update-goldens"])

    captured = capsys.readouterr()
    assert code == cli.EXIT_CONFIG
    assert "no Connect IQ device fonts" in captured.err
    assert str(absent) in captured.err
    assert "Invalid Font Specified" in captured.err


def test_update_goldens_passes_the_font_gate_with_a_pack(tmp_path, monkeypatch, capsys) -> None:
    """With a pack in place the capability is available to every driver."""
    pack = _font_pack(tmp_path / "fonts")
    monkeypatch.setenv("GARMIN_FONTS_DIR", str(pack))
    calls: List[str] = []

    def stub_layout():
        calls.append("layout")
        raise cli.SdkError("sdk stub for the test")

    monkeypatch.setattr(cli, "load_layout", stub_layout)

    code = cli.main(["run", "smoke/launch-and-poll", "--update-goldens"])

    captured = capsys.readouterr()
    assert calls == ["layout"], "the run must reach the SDK stage, so the font gate passed"
    assert "no Connect IQ device fonts" not in captured.err
    assert "sdk stub for the test" in captured.err
    assert code == cli.EXIT_CONFIG


# ---------------------------------------------------------------------------
# golden guards: the wrong state must never become a baseline
# ---------------------------------------------------------------------------

def _runner_with_view(tmp_path: Path, *lines: str):
    """A ScenarioRunner whose trace reports the given console lines."""
    from e2e.trace import TraceLog

    runner = engine.ScenarioRunner.__new__(engine.ScenarioRunner)
    trace = TraceLog(lambda: list(lines))
    trace.poll()

    class _Ctx:
        pass

    context = _Ctx()
    context.trace = trace
    context.driver = _NullWithEvents()
    return runner, context


class _NullWithEvents(NullDriver):
    """The dry-run driver, but claiming an event stream so the guard runs."""

    supports_events = True


GLANCE = "E2E:VIEW;category=glance;view=GlanceSitemapView;depth=1"
MENU = "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1"
LOADING = "E2E:VIEW;category=sitemap;view=LoadingView;depth=1"


def test_golden_guard_rejects_the_glance(tmp_path: Path) -> None:
    runner, context = _runner_with_view(tmp_path, GLANCE)
    expect = {"category": "sitemap", "view": "HomepageMenu"}
    with pytest.raises(engine.AssertionFailure) as error:
        runner._require_view(context, expect, "homepage")
    message = str(error.value)
    assert "GlanceSitemapView" in message
    assert "must never become the baseline" in message


def test_golden_guard_rejects_a_loading_view(tmp_path: Path) -> None:
    runner, context = _runner_with_view(tmp_path, LOADING)
    with pytest.raises(engine.AssertionFailure) as error:
        runner._require_view(context, {"category": "sitemap", "view": "HomepageMenu"},
                             "homepage")
    assert "LoadingView" in str(error.value)


def test_golden_guard_accepts_the_expected_view(tmp_path: Path) -> None:
    runner, context = _runner_with_view(tmp_path, GLANCE, MENU)
    runner._require_view(context, {"category": "sitemap", "view": "HomepageMenu"},
                         "homepage")


def test_golden_guard_refuses_without_any_view(tmp_path: Path) -> None:
    runner, context = _runner_with_view(tmp_path, "E2E:LANGUAGE;code=eng")
    with pytest.raises(engine.AssertionFailure) as error:
        runner._require_view(context, {"category": "sitemap"}, "homepage")
    assert "no E2E:VIEW event" in str(error.value)


def test_golden_guard_matches_a_category_only_expectation(tmp_path: Path) -> None:
    runner, context = _runner_with_view(tmp_path, LOADING)
    runner._require_view(context, {"category": "sitemap"}, "homepage")

# ---------------------------------------------------------------------------
# content guard: an unpainted window must never become a baseline
# ---------------------------------------------------------------------------

def _pgm_bytes(width: int, height: int, blobs) -> bytes:
    pixels = bytearray([255] * (width * height))
    for x0, y0, w, h in blobs:
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                pixels[y * width + x] = 0
    return b"P5\n%d %d\n255\n" % (width, height) + bytes(pixels)


def test_parse_pgm_reads_a_binary_image() -> None:
    from e2e.assertions import parse_pgm

    assert parse_pgm(b"P5\n3 2\n255\n" + bytes(range(6))) == (3, 2, bytes(range(6)))
    assert parse_pgm(b"P2\n3 2\n255\n0 0 0") is None


def test_ink_components_counts_blobs_not_pixels() -> None:
    from e2e.assertions import ink_components

    data = _pgm_bytes(40, 40, [(2, 2, 3, 3), (10, 10, 3, 3), (20, 20, 4, 4)])
    width, height, pixels = 40, 40, data.split(b"\n", 3)[3]
    assert ink_components(width, height, pixels) == 3


def test_ink_components_ignores_specks() -> None:
    from e2e.assertions import ink_components

    data = _pgm_bytes(40, 40, [(2, 2, 1, 1), (10, 10, 1, 2)])
    width, height, pixels = 40, 40, data.split(b"\n", 3)[3]
    assert ink_components(width, height, pixels) == 0


def test_a_blank_window_fails_the_content_guard() -> None:
    from e2e.assertions import screen_content_problem

    blank = _pgm_bytes(60, 60, [])
    problem = screen_content_problem(
        Path("blank.png"), 40, runner=lambda command: blank
    )
    assert problem is not None
    assert "must never become a baseline" in problem


def test_a_rendered_window_passes_the_content_guard() -> None:
    from e2e.assertions import screen_content_problem

    blobs = [(4 * i, 20, 3, 6) for i in range(12)]
    rendered = _pgm_bytes(60, 60, blobs)
    assert (
        screen_content_problem(Path("menu.png"), 10, runner=lambda command: rendered)
        is None
    )


def test_the_content_guard_is_absent_without_imagemagick(monkeypatch) -> None:
    from e2e.assertions import screen_content_problem

    monkeypatch.setattr("e2e.assertions.shutil.which", lambda name: None)
    assert screen_content_problem(Path("x.png"), 40) is None

# ---------------------------------------------------------------------------
# wait_for_view dispatch
# ---------------------------------------------------------------------------

def test_wait_for_view_returns_when_the_menu_appears(tmp_path: Path) -> None:
    from e2e.scenarios import Step

    runner, context = _runner_with_view(tmp_path, GLANCE, MENU)
    context.skip_events = False
    context.remaining = lambda: 5.0
    step = Step(index=1, action="wait_for_view",
                params={"category": "sitemap", "view": "HomepageMenu"})
    status, message, _artifacts = runner._do_wait_for_view(step, context)
    assert status == "passed"
    assert "HomepageMenu" in message


def test_wait_for_view_fails_when_the_menu_never_appears(tmp_path: Path) -> None:
    from e2e.scenarios import Step

    runner, context = _runner_with_view(tmp_path, GLANCE)
    context.skip_events = False
    context.remaining = lambda: 0.0
    step = Step(index=1, action="wait_for_view",
                params={"category": "sitemap", "view": "HomepageMenu"})
    with pytest.raises(engine.AssertionFailure) as error:
        runner._do_wait_for_view(step, context)
    assert "did not show a view" in str(error.value)


def test_wait_for_view_is_skipped_without_an_event_stream(tmp_path: Path) -> None:
    from e2e.scenarios import Step
    from e2e.sim.null import NullDriver

    runner = engine.ScenarioRunner.__new__(engine.ScenarioRunner)

    class _Ctx:
        trace = None
        driver = NullDriver()
        skip_events = False

    step = Step(index=1, action="wait_for_view", params={"category": "sitemap"})
    status, message, _artifacts = runner._do_wait_for_view(step, _Ctx())
    assert status == "skipped"
    assert "no console event stream" in message