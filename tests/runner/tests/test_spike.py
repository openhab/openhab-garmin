"""Unit tests for the Wine spike's pure logic.

The spike itself never runs here: no Docker, no Wine, no SDK, no simulator.
These tests cover the parts that can be wrong without a Linux host -- argument
parsing, prerequisite checks, the Docker command plan, verdict computation from
synthetic probe results and the two report writers -- so the existing
``harness`` CI job keeps them honest.

Every test that would touch the host injects its own ``which``/``run`` callable
or a temporary SDK/repository tree; nothing reads the real environment beyond
``PATH`` for the interpreter itself.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from spike import criteria, model, prereqs, procedure, report
from spike import cli as spike_cli
from spike import probe as spike_probe


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------


def make_sdk(root: Path, version: str = "9.3.0") -> Path:
    """Create a synthetic SDK tree with the layout the spike requires."""
    (root / "linux" / "bin").mkdir(parents=True)
    (root / "windows" / "bin").mkdir(parents=True)
    (root / "version.txt").write_text(version + "\n", encoding="utf-8")
    for relative in ("linux/bin/monkeyc", "linux/bin/monkeydo", "windows/bin/simulator.exe"):
        path = root / relative
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
    return root


def make_repo(root: Path, version: str = "9.3.0") -> Path:
    """Create the checkout shape the prerequisite check expects."""
    (root / "tests" / "runner").mkdir(parents=True)
    (root / "tests" / "config").mkdir(parents=True)
    (root / "tests" / "runner" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (root / "tests" / "config" / "sdk.version").write_text(version + "\n", encoding="utf-8")
    (root / "monkey.jungle").write_text("project.manifest = manifest.xml\n", encoding="utf-8")
    (root / "manifest.xml").write_text("<manifest/>\n", encoding="utf-8")
    return root


def sdk_layout(root: Path) -> prereqs.SdkLayout:
    return prereqs.inspect_sdk(root, expected_version="9.3.0")


def probe_document(statuses: dict[str, str]) -> model.ProbeDocument:
    checks = [
        model.Check(check_id, status, detail=f"{check_id} detail")
        for check_id, status in statuses.items()
    ]
    return model.ProbeDocument.from_checks(checks)


ALL_MET = {
    "simulator.launches": model.MET,
    "app.renders": model.MET,
    "monkeydo.launches": model.MET,
    "console.trace_lines": model.MET,
    "input.buttons": model.MET,
    "input.touch": model.MET,
    "menu.simulation": model.MET,
    "menu.connection": model.MET,
    "language.switch": model.MET,
    "screenshot.watchface_only": model.MET,
    "timing.poll_interval": model.MET,
    "timing.escalation": model.MET,
}


def with_status(check_id: str, status: str) -> dict[str, str]:
    statuses = dict(ALL_MET)
    statuses[check_id] = status
    return statuses


def completed(command, code: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, code, stdout, stderr)


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


def test_check_rejects_unknown_status():
    with pytest.raises(model.SpikeError):
        model.Check("x", "maybe")


def test_check_round_trips_through_json():
    check = model.Check(
        "input.buttons",
        model.MET,
        "2/3 events",
        evidence=["shots/a.png"],
        measurements={"changes": 2},
    )
    restored = model.Check.from_document(
        json.loads(json.dumps(check.to_document()))
    )
    assert restored == check
    assert restored.measurements["changes"] == 2.0


def test_check_rejects_non_numeric_measurement():
    with pytest.raises(model.SpikeError):
        model.Check("x", model.MET, measurements={"value": "fast"})  # type: ignore[dict-item]


def test_check_log_replaces_duplicates_by_id():
    log = model.CheckLog()
    log.record("a", model.NOT_MET, "first")
    log.record("a", model.MET, "second")
    assert len(log) == 1
    assert log.get("a").status == model.MET
    assert log.get("a").detail == "second"


def test_probe_document_round_trips_and_keeps_prerequisites():
    document = model.ProbeDocument.from_checks(
        [model.Check("a", model.MET)],
        prerequisites=["Wine (missing)"],
        environment={"wine": "9.0"},
        artifacts_root="/tmp/artifacts",
    )
    restored = model.ProbeDocument.from_document(
        json.loads(json.dumps(document.to_document()))
    )
    assert restored.prerequisites == ["Wine (missing)"]
    assert restored.environment["wine"] == "9.0"
    assert restored.checks[0].id == "a"


def test_probe_document_rejects_bad_shapes():
    with pytest.raises(model.SpikeError):
        model.ProbeDocument.from_document({"checks": "nope"})  # type: ignore[arg-type]
    with pytest.raises(model.SpikeError):
        model.ProbeDocument.from_document({"checks": [{"status": "met"}]})


def test_parse_console_line_keeps_unknown_events_and_fields():
    event = model.parse_console_line("noise E2E:LANGUAGE;code=deu;extra=a=b")
    assert event == {"event": "LANGUAGE", "code": "deu", "extra": "a=b", "raw": "noise E2E:LANGUAGE;code=deu;extra=a=b"}
    assert model.parse_console_line("plain output") is None
    assert model.parse_console_line("E2E:") is None


# ---------------------------------------------------------------------------
# criteria and verdict
# ---------------------------------------------------------------------------


def test_all_criteria_met_is_pass():
    verdict = criteria.compute_verdict(probe_document(ALL_MET))
    assert verdict.verdict == criteria.PASS
    assert verdict.recommendation == criteria.RECOMMENDATIONS[criteria.PASS]
    assert all(result.met for result in verdict.criteria)


def test_missing_required_check_is_skipped_not_met():
    statuses = dict(ALL_MET)
    del statuses["timing.escalation"]
    verdict = criteria.compute_verdict(probe_document(statuses))
    timing = next(result for result in verdict.criteria if result.id == "pc6")
    assert timing.status == model.SKIPPED
    assert "timing.escalation" in timing.detail
    assert verdict.verdict == criteria.PARTIAL


@pytest.mark.parametrize("foundation", ["simulator.launches", "app.renders", "console.trace_lines"])
def test_foundation_failure_is_fail(foundation):
    verdict = criteria.compute_verdict(probe_document(with_status(foundation, model.NOT_MET)))
    assert verdict.verdict == criteria.FAIL
    assert verdict.recommendation == criteria.RECOMMENDATIONS[criteria.FAIL]


def test_input_failure_is_partial():
    verdict = criteria.compute_verdict(probe_document(with_status("input.touch", model.NOT_MET)))
    assert verdict.verdict == criteria.PARTIAL
    assert verdict.recommendation == criteria.RECOMMENDATIONS[criteria.PARTIAL]
    assert verdict.statuses["pc3"] == model.NOT_MET


def test_supporting_check_does_not_decide_a_criterion():
    statuses = dict(ALL_MET)
    statuses["screenshot.occluded"] = model.NOT_MET
    verdict = criteria.compute_verdict(probe_document(statuses))
    screenshot = next(result for result in verdict.criteria if result.id == "pc5")
    assert screenshot.status == model.MET
    assert any(check.id == "screenshot.occluded" for check in screenshot.checks)


def test_unknown_checks_are_reported_not_dropped():
    statuses = dict(ALL_MET)
    statuses["config.simulation_state"] = model.MET
    verdict = criteria.compute_verdict(probe_document(statuses))
    assert "config.simulation_state" not in verdict.unknown_checks  # supporting for pc4


def test_fully_unknown_check_is_listed():
    statuses = dict(ALL_MET)
    statuses["probe.mystery"] = model.MET
    verdict = criteria.compute_verdict(probe_document(statuses))
    assert verdict.unknown_checks == ["probe.mystery"]


def test_empty_probe_is_partial_not_fail():
    verdict = criteria.compute_verdict(model.ProbeDocument())
    assert verdict.verdict == criteria.PARTIAL
    assert all(result.status == model.SKIPPED for result in verdict.criteria)


def test_format_table_lists_each_criterion_and_recommendation():
    verdict = criteria.compute_verdict(probe_document(ALL_MET))
    text = criteria.format_table(verdict)
    assert "verdict: pass" in text
    for criterion_id in ("pc1", "pc2", "pc3", "pc4", "pc5", "pc6"):
        assert criterion_id in text
    assert criteria.RECOMMENDATIONS[criteria.PASS] in text


# ---------------------------------------------------------------------------
# prerequisites
# ---------------------------------------------------------------------------


def test_inspect_sdk_accepts_the_documented_layout(tmp_path):
    layout = sdk_layout(make_sdk(tmp_path / "sdk"))
    assert layout.version == "9.3.0"
    assert layout.monkeyc.name == "monkeyc"
    assert layout.monkeydo.name == "monkeydo"
    assert layout.simulator.name == "simulator.exe"


def test_inspect_sdk_accepts_the_flat_layout(tmp_path):
    root = tmp_path / "sdk"
    (root / "bin").mkdir(parents=True)
    (root / "version.txt").write_text("9.3.0", encoding="utf-8")
    for name in ("monkeyc", "monkeydo", "simulator.exe"):
        (root / "bin" / name).write_text("", encoding="utf-8")
    layout = prereqs.inspect_sdk(root, expected_version="9.3.0")
    assert layout.monkeyc == root / "bin" / "monkeyc"


@pytest.mark.parametrize(
    "relative,expected",
    [
        ("version.txt", "version.txt"),
        ("linux/bin/monkeyc", "Linux compiler"),
        ("linux/bin/monkeydo", "monkeydo"),
        ("windows/bin/simulator.exe", "Windows simulator"),
    ],
)
def test_inspect_sdk_names_the_missing_piece(tmp_path, relative, expected):
    root = make_sdk(tmp_path / "sdk")
    (root / relative).unlink()
    with pytest.raises(prereqs.PrereqError) as error:
        prereqs.inspect_sdk(root, expected_version="9.3.0")
    assert expected in str(error.value)


def test_inspect_sdk_rejects_a_version_mismatch(tmp_path):
    root = make_sdk(tmp_path / "sdk", version="9.2.0")
    with pytest.raises(prereqs.PrereqError) as error:
        prereqs.inspect_sdk(root, expected_version="9.3.0")
    assert "9.2.0" in str(error.value) and "9.3.0" in str(error.value)


def test_resolve_sdk_root_prefers_the_host_variable(monkeypatch, tmp_path):
    first = tmp_path / "host"
    second = tmp_path / "root"
    first.mkdir()
    second.mkdir()
    environ = {"CIQ_SDK_ROOT": str(second), "CIQ_SDK_HOST_PATH": str(first)}
    assert prereqs.resolve_sdk_root(environ) == first


def test_resolve_sdk_root_names_the_variable(tmp_path):
    with pytest.raises(prereqs.PrereqError) as error:
        prereqs.resolve_sdk_root({})
    assert "CIQ_SDK_HOST_PATH" in str(error.value)


def test_check_host_reports_missing_docker_and_sdk(tmp_path):
    repo = make_repo(tmp_path / "repo")
    status = prereqs.check_host(
        {"CIQ_SDK_HOST_PATH": str(tmp_path / "absent")},
        repo,
        which=lambda name: None,
        run=lambda *args, **kwargs: completed(args[0], 1),
    )
    assert not status.ok
    message = status.message()
    assert "docker" in message
    assert "Connect IQ SDK" in message


def test_check_host_reports_a_dead_daemon(tmp_path):
    repo = make_repo(tmp_path / "repo")
    sdk = make_sdk(tmp_path / "sdk")
    status = prereqs.check_host(
        {"CIQ_SDK_HOST_PATH": str(sdk)},
        repo,
        which=lambda name: "/usr/bin/docker",
        run=lambda command, **kwargs: completed(command, 1, stderr="cannot connect"),
    )
    assert "docker daemon" in status.message()


def test_check_host_accepts_a_valid_environment(tmp_path):
    repo = make_repo(tmp_path / "repo")
    sdk = make_sdk(tmp_path / "sdk")
    status = prereqs.check_host(
        {"CIQ_SDK_HOST_PATH": str(sdk)},
        repo,
        which=lambda name: "/usr/bin/docker",
        run=lambda command, **kwargs: completed(command, 0, stdout="27.0.0"),
    )
    assert status.ok
    assert status.sdk is not None
    assert status.sdk.version == "9.3.0"
    assert status.docker == "/usr/bin/docker"


def test_check_host_can_skip_docker_for_dry_runs(tmp_path):
    repo = make_repo(tmp_path / "repo")
    sdk = make_sdk(tmp_path / "sdk")
    status = prereqs.check_host(
        {"CIQ_SDK_HOST_PATH": str(sdk)},
        repo,
        which=lambda name: None,
        run=lambda *args, **kwargs: completed(args[0], 1),
        require_docker=False,
    )
    assert status.ok


def test_check_host_reports_a_missing_repository_file(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "manifest.xml").unlink()
    sdk = make_sdk(tmp_path / "sdk")
    status = prereqs.check_host(
        {"CIQ_SDK_HOST_PATH": str(sdk)},
        repo,
        which=lambda name: "/usr/bin/docker",
        run=lambda command, **kwargs: completed(command, 0, stdout="27"),
        require_docker=False,
    )
    assert "manifest.xml" in status.message()


# ---------------------------------------------------------------------------
# host orchestrator
# ---------------------------------------------------------------------------


def plan_for(tmp_path: Path, **overrides) -> procedure.SpikePlan:
    repo = make_repo(tmp_path / "repo")
    sdk = sdk_layout(make_sdk(tmp_path / "sdk"))
    values = dict(repo_root=repo, sdk=sdk, artifacts=tmp_path / "artifacts")
    values.update(overrides)
    return procedure.SpikePlan(**values)


def test_build_command_uses_the_runner_dockerfile(tmp_path):
    plan = plan_for(tmp_path)
    command = procedure.build_command(plan)
    assert command[:2] == [plan.docker, "build"]
    assert str(plan.dockerfile) in command
    assert str(plan.build_context) in command
    assert f"RUNNER_BASE_IMAGE={plan.base_image}" in command
    assert plan.image in command


def test_run_command_mounts_the_sdk_read_only_and_artifacts_writable(tmp_path):
    plan = plan_for(tmp_path)
    command = procedure.run_command(plan)
    assert f"{plan.repo_root}:/work:ro" in command
    assert f"{plan.sdk.root}:/opt/ciq-sdk:ro" in command
    assert f"{plan.artifacts}:/spike-artifacts" in command
    assert "CIQ_SDK_ROOT=/opt/ciq-sdk" in command
    assert "python" in command and "spike.probe" in command
    assert "--button-device" in command and "--touch-device" in command
    assert "0,1000" in command


def test_run_spike_reports_a_build_failure(tmp_path):
    plan = plan_for(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return completed(command, 2, stderr="no such image")

    result = procedure.run_spike(plan, run=fake_run, log=lambda message: None)
    assert result.exit_code == procedure.EXIT_INCOMPLETE
    assert "docker build failed" in result.message
    assert result.logs["build"].is_file()
    assert len(calls) == 1


def test_run_spike_reports_missing_container_prerequisites(tmp_path):
    plan = plan_for(tmp_path)

    def fake_run(command, **kwargs):
        if "build" in command:
            return completed(command, 0)
        document = model.ProbeDocument.from_checks(
            [],
            prerequisites=["Wine (not installed in the runner image)"],
        )
        plan.artifacts.mkdir(parents=True, exist_ok=True)
        (plan.artifacts / "probe.json").write_text(
            json.dumps(document.to_document()), encoding="utf-8"
        )
        return completed(command, 2, stdout="preflight failed")

    result = procedure.run_spike(plan, run=fake_run, log=lambda message: None)
    assert result.exit_code == procedure.EXIT_PREREQ
    assert result.message.startswith("missing prerequisites: Wine")


def test_run_spike_returns_the_probe_document(tmp_path):
    plan = plan_for(tmp_path)

    def fake_run(command, **kwargs):
        if "build" in command:
            return completed(command, 0)
        document = model.ProbeDocument.from_checks(
            [model.Check("preflight", model.MET)],
            environment={"wine": "9.0"},
        )
        plan.artifacts.mkdir(parents=True, exist_ok=True)
        (plan.artifacts / "probe.json").write_text(
            json.dumps(document.to_document()), encoding="utf-8"
        )
        return completed(command, 0)

    result = procedure.run_spike(plan, run=fake_run, log=lambda message: None)
    assert result.exit_code == procedure.EXIT_OK
    assert result.probe is not None
    assert result.probe.environment["wine"] == "9.0"


def test_run_spike_reports_a_missing_probe_document(tmp_path):
    plan = plan_for(tmp_path)

    def fake_run(command, **kwargs):
        return completed(command, 0)

    result = procedure.run_spike(plan, run=fake_run, log=lambda message: None)
    assert result.exit_code == procedure.EXIT_INCOMPLETE
    assert "no probe.json" in result.message


def test_run_spike_turns_a_daemon_error_into_a_prerequisite(tmp_path):
    plan = plan_for(tmp_path)

    def fake_run(command, **kwargs):
        return completed(
            command,
            1,
            stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
        )

    result = procedure.run_spike(plan, run=fake_run, log=lambda message: None)
    assert result.exit_code == procedure.EXIT_PREREQ
    assert "docker daemon" in result.message


def test_daemon_problem_ignores_unrelated_failures():
    assert procedure._daemon_problem("no such image: runner") == ""
    assert "permission denied" in procedure._daemon_problem(
        "docker: permission denied while trying to connect"
    )


def test_run_spike_turns_the_entrypoint_sdk_error_into_a_prerequisite(tmp_path):
    plan = plan_for(tmp_path)

    def fake_run(command, **kwargs):
        if "build" in command:
            return completed(command, 0)
        return completed(command, 78, stderr="Connect IQ SDK is not available")

    result = procedure.run_spike(plan, run=fake_run, log=lambda message: None)
    assert result.exit_code == procedure.EXIT_PREREQ
    assert result.message.startswith("missing prerequisites: Connect IQ SDK")


def test_run_spike_skips_the_build_when_asked(tmp_path):
    plan = plan_for(tmp_path, skip_build=True)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        document = model.ProbeDocument.from_checks([])
        plan.artifacts.mkdir(parents=True, exist_ok=True)
        (plan.artifacts / "probe.json").write_text(
            json.dumps(document.to_document()), encoding="utf-8"
        )
        return completed(command, 0)

    procedure.run_spike(plan, run=fake_run, log=lambda message: None)
    assert all("build" not in command for command in commands)


def test_plan_lines_shows_both_commands(tmp_path):
    text = "\n".join(procedure.plan_lines(plan_for(tmp_path)))
    assert "spike plan" in text
    assert "docker build" in text
    assert "docker run" in text


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_write_verdict_creates_both_files(tmp_path):
    document = probe_document(ALL_MET)
    verdict = criteria.compute_verdict(document)
    written = report.write_verdict(
        verdict, document, artifacts_root=tmp_path, generated_at=0.0
    )
    assert written["json"].is_file()
    assert written["markdown"].is_file()
    payload = json.loads(written["json"].read_text(encoding="utf-8"))
    assert payload["verdict"] == criteria.PASS
    assert len(payload["criteria"]) == 6
    text = written["markdown"].read_text(encoding="utf-8")
    assert "# Connect IQ simulator under Wine: spike verdict" in text
    assert "**Verdict: pass**" in text
    assert criteria.RECOMMENDATIONS[criteria.PASS] in text
    for criterion in criteria.CRITERIA:
        assert criterion.id in text
    assert not any(line.endswith(" ") for line in text.splitlines())
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "`verdict.md`" in text and "`verdict.json`" in text


def test_report_mentions_skipped_checks_as_limitations(tmp_path):
    statuses = dict(ALL_MET)
    statuses["input.touch"] = model.SKIPPED
    document = probe_document(statuses)
    verdict = criteria.compute_verdict(document)
    written = report.write_verdict(
        verdict, document, artifacts_root=tmp_path, generated_at=0.0
    )
    text = written["markdown"].read_text(encoding="utf-8")
    assert "## Limitations" in text
    assert "`input.touch`" in text


def test_report_renders_console_excerpts(tmp_path):
    document = model.ProbeDocument.from_checks(
        [model.Check("simulator.launches", model.MET), model.Check("app.renders", model.MET)],
        environment={"console_excerpts": {"fenix6pro": ["E2E:VIEW;view=home", "E2E:LANGUAGE;code=eng"]}},
    )
    verdict = criteria.compute_verdict(document)
    written = report.write_verdict(
        verdict, document, artifacts_root=tmp_path, generated_at=0.0
    )
    text = written["markdown"].read_text(encoding="utf-8")
    assert "E2E:VIEW;view=home" in text
    assert "E2E:LANGUAGE;code=eng" in text


# ---------------------------------------------------------------------------
# probe helpers (no Docker)
# ---------------------------------------------------------------------------


def test_interval_stats_measures_gaps():
    stats = spike_probe.interval_stats([0.0, 1.0, 2.1, 3.3])
    assert stats["samples"] == 4
    assert 950 <= stats["median_ms"] <= 1200
    assert stats["min_ms"] == pytest.approx(1000, abs=150)


def test_interval_stats_needs_two_samples():
    stats = spike_probe.interval_stats([1.0])
    assert stats["samples"] == 1
    assert stats["median_ms"] == 0.0


@pytest.mark.parametrize(
    "requested,times,expected",
    [
        (1000, [0, 1, 2, 3, 4], model.MET),
        (1000, [0, 1, 3.5, 5, 7.5], model.NOT_MET),
        (0, [0, 0.05, 0.1, 0.15, 0.2], model.MET),
        (0, [0, 1, 2, 3, 4], model.NOT_MET),
        (1000, [0, 1], model.NOT_MET),
    ],
)
def test_classify_poll_stats(requested, times, expected):
    status, _ = spike_probe.classify_poll_stats(
        requested, spike_probe.interval_stats(times)
    )
    assert status == expected


@pytest.mark.parametrize(
    "elapsed,expected",
    [(9.8, model.MET), (7.9, model.NOT_MET), (16.0, model.NOT_MET)],
)
def test_classify_escalation(elapsed, expected):
    status, _ = spike_probe.classify_escalation(0.0, elapsed)
    assert status == expected


def test_png_dimensions_reads_the_header(tmp_path):
    # Minimal PNG: signature + IHDR length/type + width/height.
    header = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\x0dIHDR"
        b"\x00\x00\x01\x04"
        b"\x00\x00\x00\xc8"
    )
    path = tmp_path / "shot.png"
    path.write_bytes(header)
    assert spike_probe.png_dimensions(path) == (260, 200)
    path.write_bytes(b"not a png")
    assert spike_probe.png_dimensions(path) is None


def test_probe_parse_intervals_rejects_bad_values():
    assert spike_probe.parse_intervals("0,1000") == (0, 1000)
    with pytest.raises(Exception):
        spike_probe.parse_intervals("")
    with pytest.raises(Exception):
        spike_probe.parse_intervals("1000,abc")


def test_probe_device_matrix_contains_the_default_devices():
    repo = Path(__file__).resolve().parents[2].parent
    devices = spike_probe.load_device_specs(repo)
    assert "fenix6pro" in devices
    assert devices["fenix6pro"].supports_buttons
    assert "venu3" in devices
    assert devices["venu3"].supports_touch


def test_probe_missing_tool_messages_name_the_tool():
    assert "Java" in spike_probe._missing_tool("java")
    assert "Wine" in spike_probe._missing_tool("wine")
    assert "xdotool" in spike_probe._missing_tool("xdotool")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_parser_defaults():
    parser = spike_cli.build_parser()
    args = parser.parse_args(["run"])
    assert args.button_device == "fenix6pro"
    assert args.touch_device == "venu3"
    assert args.poll_intervals == (0, 1000)
    assert args.dry_run is False


def test_cli_parser_rejects_bad_intervals():
    with pytest.raises(SystemExit):
        spike_cli.build_parser().parse_args(["run", "--poll-intervals", "abc"])


def test_cli_sdk_override_wins_over_the_environment():
    environ = spike_cli._environment_with_sdk("/opt/ciq-sdk", {"CIQ_SDK_HOST_PATH": "/other"})
    assert environ["CIQ_SDK_HOST_PATH"] == "/opt/ciq-sdk"


def test_cli_dry_run_prints_the_plan(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    sdk = make_sdk(tmp_path / "sdk")
    code = spike_cli.main(
        [
            "run",
            "--dry-run",
            "--repo",
            str(repo),
            "--sdk",
            str(sdk),
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--docker",
            "/usr/bin/docker",
        ]
    )
    assert code == spike_cli.EXIT_OK
    out = capsys.readouterr().out
    assert "spike plan (no container started)" in out
    assert "docker build" in out
    assert "docker run" in out


def test_cli_missing_prerequisites_exit_2_with_one_message(tmp_path, capsys):
    repo = tmp_path / "empty"
    sdk = tmp_path / "absent"
    code = spike_cli.main(
        [
            "run",
            "--dry-run",
            "--repo",
            str(repo),
            "--sdk",
            str(sdk),
        ]
    )
    assert code == spike_cli.EXIT_PREREQ
    error = capsys.readouterr().err
    assert error.startswith("error: missing prerequisites:")
    assert "docker" not in error  # dry runs do not require the daemon


def test_cli_check_prints_the_sdk_paths(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    sdk = make_sdk(tmp_path / "sdk")
    code = spike_cli.main(
        [
            "check",
            "--no-docker",
            "--repo",
            str(repo),
            "--sdk",
            str(sdk),
        ]
    )
    assert code == spike_cli.EXIT_OK
    out = capsys.readouterr().out
    assert "prerequisites: ok" in out
    assert str(sdk) in out
