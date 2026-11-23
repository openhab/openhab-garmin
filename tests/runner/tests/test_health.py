"""Stack health tests: parsing, classification and rendering.

Everything here runs without a Docker daemon. The parsers are exercised against
recorded ``docker inspect``/``docker stats`` output in ``fixtures/health``, and
the probe's command plan is driven by a scripted ``command`` callable, so the
report contract stays honest on a workstation with no stack at all.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Sequence

import pytest

from e2e import health
from e2e.health import (
    INVALID,
    UNKNOWN,
    VALID,
    ContainerHealth,
    EnvironmentVerdict,
    HealthError,
    HealthSnapshot,
)
from e2e.reporting import ERRORED, FAILED, PASSED, Report, ScenarioResult

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "health"

OPENHAB = "openhab-garmin-e2e-openhab-stable-1"
SHIM = "openhab-garmin-e2e-shim-1"

OPENHAB_ID = "3f2a9c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b"

#: 512 MiB: the limit from the incident this module exists for.
OPENHAB_PEAK = 536870912


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _inspect(name: str) -> object:
    return json.loads(_read(f"inspect-{name}.json"))


def _recording(name: str, *, clean: bool) -> Dict[str, object]:
    """A recorded phase: inspect plus the per-container cgroup evidence."""
    return {
        "captured_at": "2026-11-23T16:00:00+00:00",
        "source": "snapshot",
        "inspect": _inspect(name),
        "stats": _read(f"stats-{name}.txt"),
        "memory_events": {
            OPENHAB: _read("memory.events" if not clean else "memory.events-clean")
        },
        "memory_peak": {OPENHAB: str(OPENHAB_PEAK)},
        "dmesg": "" if clean else _read("dmesg.txt"),
    }


def _window() -> "tuple[HealthSnapshot, HealthSnapshot]":
    before = health.snapshot_from_recording(_recording("before", clean=True))
    after = health.snapshot_from_recording(_recording("after", clean=False))
    return before, after


def _mutate(
    snapshot: HealthSnapshot, name: str, **changes: object
) -> HealthSnapshot:
    containers = tuple(
        replace(container, **changes) if container.name == name else container
        for container in snapshot.containers
    )
    return replace(snapshot, containers=containers)


def _container(snapshot: HealthSnapshot, name: str) -> ContainerHealth:
    container = snapshot.find(name)
    assert container is not None, f"{name} missing from the snapshot"
    return container


# ---------------------------------------------------------------------------
# parsing recorded output
# ---------------------------------------------------------------------------


def test_parse_inspect_reads_state_health_and_restart_counts() -> None:
    containers = {c.name: c for c in health.parse_inspect(_inspect("before"))}

    assert set(containers) == {
        OPENHAB,
        SHIM,
        "openhab-garmin-e2e-runner-1",
    }, "the leading slash in .Name must be stripped and every container kept"
    openhab = containers[OPENHAB]
    assert openhab.restart_count == 0
    assert openhab.state == "running"
    assert openhab.running is True
    assert openhab.health == "healthy"
    assert openhab.memory_limit_bytes is None, "HostConfig.Memory 0 means unlimited"
    assert openhab.oom_killed is False
    assert containers["openhab-garmin-e2e-runner-1"].health is None, (
        "a container without a healthcheck has no health status"
    )


def test_parse_inspect_accepts_json_text_and_single_objects() -> None:
    from_text = health.parse_inspect(_read("inspect-before.json"))
    from_object = health.parse_inspect(_inspect("before")[0])
    assert len(from_text) == 3
    assert from_object[0].name == OPENHAB


def test_parse_inspect_rejects_malformed_json() -> None:
    with pytest.raises(HealthError) as error:
        health.parse_inspect("{not json")
    assert "not JSON" in str(error.value)


def test_parse_mem_usage_reads_working_set_and_limit() -> None:
    usage, limit = health.parse_mem_usage("512.0MiB / 7.775GiB")
    assert usage == int(512.0 * 1024**2)
    assert limit == int(round(7.775 * 1024**3))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0B", 0),
        ("1024", 1024),
        ("1KiB", 1024),
        ("24.6MiB", int(round(24.6 * 1024**2))),
        ("1.5GiB", int(round(1.5 * 1024**3))),
        ("512MB", 512 * 1000**2),
        ("nonsense", None),
    ],
)
def test_parse_bytes_units(text: str, expected: "int | None") -> None:
    assert health.parse_bytes(text) == expected


def test_parse_stats_reads_json_lines() -> None:
    samples = health.parse_stats(_read("stats-after.txt"))
    assert set(samples) == {OPENHAB, SHIM, "openhab-garmin-e2e-runner-1"}
    assert samples[OPENHAB].working_set_bytes == int(512.0 * 1024**2)


def test_parse_stats_reads_the_default_table() -> None:
    from_table = health.parse_stats(_read("stats-table.txt"))
    from_json = health.parse_stats(_read("stats-after.txt"))
    assert {
        name: sample.working_set_bytes for name, sample in from_table.items()
    } == {name: sample.working_set_bytes for name, sample in from_json.items()}


def test_parse_stats_ignores_an_unrelated_header() -> None:
    assert health.parse_stats("CONTAINER ID   NAME   CPU %") == {}


def test_parse_memory_events_reads_the_oom_kill_counter() -> None:
    counters = health.parse_memory_events(_read("memory.events"))
    assert counters["oom"] == 3
    assert counters["oom_kill"] == 2
    assert health.parse_memory_events("") == {}


def test_parse_oom_lines_keeps_only_kernel_oom_evidence() -> None:
    lines = health.parse_oom_lines(_read("dmesg.txt"))
    assert lines, "the fixture contains two OOM kills"
    assert all("eth0" not in line and "apparmor" not in line for line in lines)
    assert any("Memory cgroup out of memory" in line for line in lines)


def test_oom_evidence_is_attributed_by_container_id() -> None:
    lines = health.parse_oom_lines(_read("dmesg.txt"))
    openhab = health.parse_inspect(_inspect("after"))[0]
    shim = health.parse_inspect(_inspect("after"))[1]

    attributed = health.match_oom_evidence(lines, openhab)
    assert attributed, "the cgroup path carries the container id"
    assert all(OPENHAB_ID[:12] in line for line in attributed)
    assert health.match_oom_evidence(lines, shim) == ()


def test_snapshot_merges_inspect_stats_cgroup_and_kernel_evidence() -> None:
    _before, after = _window()
    container = _container(after, OPENHAB)

    assert container.restart_count == 1
    assert container.working_set_bytes == int(512.0 * 1024**2)
    assert container.peak_working_set_bytes == OPENHAB_PEAK
    assert container.peak_source == "cgroup memory.peak"
    assert container.oom_kill_count == 2, "the cgroup counter, not State.OOMKilled"
    assert container.oom_killed is False, "the fixture proves the Docker flag is blind"
    assert container.oom_evidence
    assert after.available is True


def test_snapshot_without_containers_is_not_available() -> None:
    snapshot = health.snapshot_from_recording({"inspect": []})
    assert snapshot.available is False
    assert "no containers" in snapshot.error


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def test_restart_during_the_run_makes_the_run_invalid() -> None:
    before, after = _window()
    verdict = health.classify(before, after)

    assert verdict.verdict == INVALID
    assert verdict.invalid is True
    assert any(
        "restarted 1 time(s) during the run" in reason for reason in verdict.reasons
    ), verdict.reasons
    openhab = next(c for c in verdict.containers if c.name == OPENHAB)
    assert openhab.restart_count_before == 0
    assert openhab.restart_count_after == 1
    assert openhab.restarts_during_run == 1


def test_new_oom_kills_make_the_run_invalid() -> None:
    before, after = _window()
    after = _mutate(after, OPENHAB, restart_count=0)
    verdict = health.classify(before, after)

    assert verdict.verdict == INVALID
    assert any("new OOM kill(s)" in reason for reason in verdict.reasons)
    openhab = next(c for c in verdict.containers if c.name == OPENHAB)
    assert openhab.oom_kills_during_run == 2


def test_a_restart_before_the_window_is_reported_but_not_invalidating() -> None:
    before, after = _window()
    for snapshot in (before, after):
        snapshot_new = _mutate(snapshot, OPENHAB, restart_count=4)
        if snapshot is before:
            before = snapshot_new
        else:
            after = snapshot_new
    after = _mutate(after, OPENHAB, oom_kill_count=0)
    verdict = health.classify(before, after)

    assert verdict.verdict == VALID, verdict.reasons
    openhab = next(c for c in verdict.containers if c.name == OPENHAB)
    assert openhab.restarts_during_run == 0
    assert any("before the run window" in note for note in openhab.notes)


def test_a_container_that_stops_during_the_run_makes_it_invalid() -> None:
    before, after = _window()
    after = _mutate(after, SHIM, state="exited", running=False, restart_count=0)
    verdict = health.classify(before, after)

    assert verdict.verdict == INVALID
    assert any("is exited at the end of the run" in reason for reason in verdict.reasons)


def test_a_recreated_container_makes_the_run_invalid() -> None:
    before, after = _window()
    before = _mutate(before, OPENHAB, restart_count=5)
    after = _mutate(after, OPENHAB, restart_count=0)
    verdict = health.classify(before, after)

    assert verdict.verdict == INVALID
    assert any("was recreated" in reason for reason in verdict.reasons)


def test_a_container_appearing_during_the_run_makes_it_invalid() -> None:
    before, after = _window()
    before = replace(
        before,
        containers=tuple(c for c in before.containers if c.name != OPENHAB),
    )
    verdict = health.classify(before, after)

    assert verdict.verdict == INVALID
    assert any("not present for both snapshots" in reason for reason in verdict.reasons)


def test_uncollected_health_is_unknown_not_valid() -> None:
    snapshot = health.unavailable_snapshot("the docker CLI is not on PATH")
    verdict = health.classify(snapshot, snapshot)

    assert verdict.verdict == UNKNOWN
    assert verdict.invalid is False
    assert "not collected" in verdict.summary
    assert "docker CLI" in verdict.reasons[0]


def test_dry_run_never_probes_the_daemon() -> None:
    class ExplodingProbe(health.DockerProbe):
        def capture(self) -> Dict[str, object]:  # pragma: no cover - must not run
            raise AssertionError("a dry run must not touch Docker")

    snapshot, recording = health.capture(dry_run=True, probe=ExplodingProbe())
    assert recording is None
    assert snapshot.available is False
    assert snapshot.source == "dry-run"
    assert health.classify(snapshot, snapshot).verdict == UNKNOWN


def test_missing_docker_cli_is_reported_without_raising() -> None:
    probe = health.DockerProbe(which=lambda _name: None)
    snapshot, recording = health.capture(probe=probe)

    assert recording is None
    assert snapshot.available is False
    assert "docker CLI is not on PATH" in snapshot.error


def test_report_never_prints_the_host_total_as_a_memory_limit() -> None:
    _before, after = _window()
    container = _container(after, OPENHAB)
    assert container.memory_limit_bytes is None
    assert health.describe_limit(container.memory_limit_bytes) == "unlimited"


# ---------------------------------------------------------------------------
# probe command plan and snapshot files
# ---------------------------------------------------------------------------


def _scripted_probe(calls: List[Sequence[str]], *, restart_count: int = 0) -> health.DockerProbe:
    inspect = _inspect("after" if restart_count else "before")
    if restart_count:
        inspect[0]["RestartCount"] = restart_count  # type: ignore[index]

    def command(argv: Sequence[str]) -> str:
        calls.append(list(argv))
        if argv[:3] == ["docker", "ps", "-aq"]:
            return "\n".join(
                [
                    OPENHAB_ID,
                    "7b1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3d4e5f60718293a4b5c6d7e8f90",
                ]
            )
        if argv[:2] == ["docker", "inspect"]:
            return json.dumps(inspect)
        if argv[:2] == ["docker", "stats"]:
            return _read("stats-after.txt")
        if argv[0] == "docker" and argv[1] == "exec":
            if argv[-1] == health.CGROUP_MEMORY_EVENTS:
                return _read("memory.events")
            if argv[-1] == health.CGROUP_MEMORY_PEAK:
                return str(OPENHAB_PEAK)
        if argv[0] == "dmesg":
            return _read("dmesg.txt")
        raise AssertionError(f"unexpected command: {argv}")

    return health.DockerProbe(command=command, which=lambda name: f"/usr/bin/{name}")


def test_probe_collects_a_recording_with_the_compose_project_label() -> None:
    calls: List[Sequence[str]] = []
    probe = _scripted_probe(calls)
    recording = probe.capture()

    assert calls[0][:4] == ["docker", "ps", "-aq", "--filter"]
    assert calls[0][4] == (
        f"label={health.COMPOSE_PROJECT_LABEL}={health.DEFAULT_COMPOSE_PROJECT}"
    )
    assert recording["memory_events"][OPENHAB] == _read("memory.events")
    assert recording["memory_peak"][OPENHAB] == str(OPENHAB_PEAK)
    assert "oom-kill" in str(recording["dmesg"])

    snapshot = health.snapshot_from_recording(recording, source="docker")
    assert snapshot.available is True
    assert _container(snapshot, OPENHAB).oom_kill_count == 2


def test_probe_reports_an_empty_compose_project() -> None:
    probe = health.DockerProbe(
        command=lambda _argv: "", which=lambda name: f"/usr/bin/{name}"
    )
    with pytest.raises(HealthError) as error:
        probe.capture()
    assert "no containers found" in str(error.value)


def test_snapshot_file_round_trip_classifies_the_run(tmp_path: Path) -> None:
    target = tmp_path / "health-window.json"
    health.write_snapshot_phase(str(target), "before", _scripted_probe([]))
    health.write_snapshot_phase(str(target), "after", _scripted_probe([], restart_count=1))

    before, after = health.load_snapshot_file(str(target))
    verdict = health.classify(before, after)
    assert verdict.verdict == INVALID
    assert any("restarted 1 time(s)" in reason for reason in verdict.reasons)


def test_snapshot_file_missing_a_phase_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "health-window.json"
    health.write_snapshot_phase(str(target), "before", _scripted_probe([]))
    with pytest.raises(HealthError) as error:
        health.load_snapshot_file(str(target))
    assert "before" in str(error.value) and "after" in str(error.value)


# ---------------------------------------------------------------------------
# report and JUnit rendering
# ---------------------------------------------------------------------------


def _result(status: str, tmp_path: Path, name: str = "launch-and-poll") -> ScenarioResult:
    artifacts = tmp_path / "artifacts" / "smoke" / name
    artifacts.mkdir(parents=True, exist_ok=True)
    return ScenarioResult(
        scenario_id=f"smoke/{name}",
        name=name,
        suite="smoke",
        tier="ui",
        device="fenix7",
        mode="ble",
        fixture="default",
        language="eng",
        source="test",
        status=status,
        duration_ms=12.5,
        failures=["assertion failed"] if status in (FAILED, ERRORED) else [],
        artifacts_dir=str(artifacts),
    )


def _invalid_report(tmp_path: Path, *, status: str = FAILED) -> Report:
    before, after = _window()
    report = Report()
    report.environment = health.classify(before, after)
    report.add(_result(status, tmp_path))
    return report


def test_report_writes_the_environment_block_to_every_artifact(tmp_path: Path) -> None:
    report = _invalid_report(tmp_path, status=PASSED)
    written = report.write_artifacts(tmp_path / "artifacts")

    document = json.loads(written["json"].read_text(encoding="utf-8"))
    environment = document["environment"]
    assert environment["verdict"] == INVALID
    assert any("restarted" in reason for reason in environment["reasons"])
    container = next(c for c in environment["containers"] if c["name"] == OPENHAB)
    assert container["restart_count_after"] == 1
    assert container["peak_working_set_bytes"] == OPENHAB_PEAK
    assert container["oom_kills_during_run"] == 2

    health_document = json.loads(written["environment"].read_text(encoding="utf-8"))
    assert health_document["verdict"] == INVALID
    assert health_document["snapshots"]["before"]["available"] is True

    markdown = written["markdown"].read_text(encoding="utf-8")
    assert "## Environment health" in markdown
    assert "Verdict: **invalid**" in markdown
    assert "This run is **invalid**" in markdown
    assert OPENHAB in markdown
    assert "unlimited" in markdown, "the host total from docker stats is not a limit"
    assert "oom-kill:constraint=CONSTRAINT_MEMCG" in markdown


def test_junit_carries_the_environment_verdict_and_per_container_health(
    tmp_path: Path,
) -> None:
    report = _invalid_report(tmp_path)
    written = report.write_artifacts(tmp_path / "artifacts")
    root = ET.parse(written["junit"]).getroot()

    suite = root.find("testsuite[@name='environment']")
    assert suite is not None
    assert suite.get("tests") == "0", "the block must not inflate the test count"
    assert suite.find("failure") is None, "an invalid environment is not a failing test"

    properties = {
        element.get("name"): element.get("value")
        for element in suite.findall("properties/property")
    }
    assert properties["environment.verdict"] == INVALID
    assert properties[f"environment.container.{OPENHAB}.restart_count"] == "1"
    assert properties[f"environment.container.{OPENHAB}.restarts_during_run"] == "1"
    assert (
        properties[f"environment.container.{OPENHAB}.peak_working_set_bytes"]
        == str(OPENHAB_PEAK)
    )
    system_out = suite.findtext("system-out") or ""
    assert "environment: invalid" in system_out
    assert "OOM kills during run: 2" in system_out


def test_junit_reports_uncollected_health_explicitly() -> None:
    report = Report()
    report.add(ScenarioResult(
        scenario_id="smoke/launch-and-poll",
        name="launch-and-poll",
        suite="smoke",
        tier="ui",
        device="fenix7",
        mode="ble",
        fixture="default",
        language="eng",
        source="test",
        status=PASSED,
    ))
    root = ET.fromstring(
        _junit_text(report)
    )
    suite = root.find("testsuite[@name='environment']")
    assert suite is not None
    properties = {
        element.get("name"): element.get("value")
        for element in suite.findall("properties/property")
    }
    assert properties["environment.verdict"] == UNKNOWN
    assert "not collected" in (properties["environment.summary"] or "")


def _junit_text(report: Report) -> str:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = report.write_junit(Path(directory) / "junit.xml")
        return path.read_text(encoding="utf-8")


def test_exit_code_distinguishes_failure_from_invalid_environment(tmp_path: Path) -> None:
    valid = Report()
    valid.environment = EnvironmentVerdict(
        verdict=VALID, summary="no container restarted during the run window"
    )
    valid.add(_result(PASSED, tmp_path))
    assert valid.exit_code == 0

    valid.add(_result(FAILED, tmp_path, name="other"))
    assert valid.exit_code == 1

    invalid = _invalid_report(tmp_path, status=PASSED)
    assert invalid.exit_code == health.EXIT_INVALID_ENVIRONMENT == 3


def test_a_failed_scenario_bundle_carries_the_environment_evidence(
    tmp_path: Path,
) -> None:
    report = _invalid_report(tmp_path)
    result = report.results[0]
    written = report.write_artifacts(tmp_path / "artifacts")

    evidence = Path(result.artifacts_dir) / "environment.json"
    assert evidence.is_file(), "unzipping one failing scenario must show the environment"
    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["verdict"] == INVALID

    with zipfile.ZipFile(written[f"bundle:{result.scenario_id}"]) as archive:
        assert "environment.json" in archive.namelist()


def test_a_passing_scenario_bundle_does_not_repeat_the_environment_block(
    tmp_path: Path,
) -> None:
    report = _invalid_report(tmp_path, status=PASSED)
    result = report.results[0]
    report.write_artifacts(tmp_path / "artifacts")
    assert not (Path(result.artifacts_dir) / "environment.json").exists()


def test_a_finished_window_rerenders_the_run_artifacts(tmp_path: Path) -> None:
    from e2e import reporting

    directory = tmp_path / "artifacts"
    report = Report()
    report.add(_result(FAILED, tmp_path))
    report.write_artifacts(directory)
    assert (
        json.loads((directory / "report.json").read_text(encoding="utf-8"))["environment"][
            "verdict"
        ]
        == UNKNOWN
    )

    loaded = reporting.load_report(directory)
    assert [result.scenario_id for result in loaded.results] == ["smoke/launch-and-poll"]
    before, after = _window()
    loaded.environment = health.classify(before, after)
    written = loaded.write_artifacts(directory)

    document = json.loads(written["json"].read_text(encoding="utf-8"))
    assert document["environment"]["verdict"] == INVALID
    assert document["results"][0]["scenario"] == "smoke/launch-and-poll"
    root = ET.parse(written["junit"]).getroot()
    suite = root.find("testsuite[@name='environment']")
    assert suite is not None
    properties = {
        element.get("name"): element.get("value")
        for element in suite.findall("properties/property")
    }
    assert properties["environment.verdict"] == INVALID
    markdown = written["markdown"].read_text(encoding="utf-8")
    assert "Verdict: **invalid**" in markdown
    assert "smoke/launch-and-poll" in markdown
    assert (Path(loaded.results[0].artifacts_dir) / "environment.json").is_file()


def test_health_snapshot_after_phase_classifies_and_exits_three(
    tmp_path: Path, monkeypatch
) -> None:
    from e2e import cli

    directory = tmp_path / "artifacts"
    report = Report()
    report.add(_result(PASSED, tmp_path))
    report.write_artifacts(directory)

    def fake_write_snapshot_phase(
        path: str, phase: str, probe: object = None
    ) -> str:
        window = Path(path)
        document = json.loads(window.read_text(encoding="utf-8")) if window.exists() else {}
        document["captured_at"] = "2026-11-23T16:00:00+00:00"
        document[phase] = _recording(phase, clean=phase == "before")
        window.write_text(json.dumps(document), encoding="utf-8")
        return path

    monkeypatch.setattr(health, "write_snapshot_phase", fake_write_snapshot_phase)

    window = tmp_path / "health-window.json"
    assert cli.main(["health-snapshot", "--phase", "before", "--output", str(window)]) == 0
    exit_code = cli.main(
        [
            "health-snapshot",
            "--phase",
            "after",
            "--output",
            str(window),
            "--artifacts",
            str(directory),
        ]
    )

    assert exit_code == health.EXIT_INVALID_ENVIRONMENT == 3
    document = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert document["environment"]["verdict"] == INVALID
