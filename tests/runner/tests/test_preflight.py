"""Preflight checks: command plans, verdicts and the report contract.

The tests drive every probe with recorded output, so they pass on a
workstation with no Docker daemon, no Connect IQ SDK and no network. The port
probe and the resource measurement are injected the same way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pytest

from e2e import cli
from e2e import preflight
from e2e.network import Network

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "preflight"

NETWORK = Network(
    loopback_host="127.0.0.1",
    openhab_stable_port=18080,
    openhab_dev_port=18080,
    shim_port=18081,
)


def _recorded(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _sequence(*results: preflight.CommandResult) -> Callable[..., preflight.CommandResult]:
    """A command runner that returns recorded results in order."""
    queue = list(results)
    calls: List[List[str]] = []

    def run(command) -> preflight.CommandResult:
        calls.append([str(part) for part in command])
        if not queue:
            raise AssertionError(f"unexpected command: {command}")
        return queue.pop(0)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _which(name: str) -> Optional[str]:
    return f"/usr/bin/{name}"


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------


def test_engine_reports_the_server_version():
    runner = _sequence(preflight.CommandResult(0, _recorded("docker-info.txt")))

    result = preflight.check_engine(runner=runner, which=_which)

    assert result.status == preflight.PASS
    assert "27.5.1" in result.reason
    assert runner.calls[0] == [  # type: ignore[attr-defined]
        "/usr/bin/docker",
        "info",
        "--format",
        "{{.ServerVersion}}",
    ]


def test_engine_fails_when_docker_is_not_on_path():
    result = preflight.check_engine(runner=_sequence(), which=lambda name: None)

    assert result.status == preflight.FAIL
    assert "not on PATH" in result.reason


def test_engine_fails_when_the_daemon_does_not_answer():
    runner = _sequence(
        preflight.CommandResult(
            1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
        )
    )

    result = preflight.check_engine(runner=runner, which=_which)

    assert result.status == preflight.FAIL
    assert "unreachable" in result.reason
    assert "Cannot connect" in result.reason


def test_compose_version_accepts_2_24_and_newer():
    runner = _sequence(
        preflight.CommandResult(0, _recorded("docker-compose-version.txt"))
    )

    result = preflight.check_compose_version(runner=runner, which=_which)

    assert result.status == preflight.PASS
    assert "2.29.7" in result.reason


def test_compose_version_rejects_releases_without_reset_support():
    runner = _sequence(preflight.CommandResult(0, "2.23.0\n"))

    result = preflight.check_compose_version(runner=runner, which=_which)

    assert result.status == preflight.FAIL
    assert "2.24" in result.reason
    assert "!reset" in result.reason


def test_compose_version_fails_when_compose_is_missing():
    runner = _sequence(
        preflight.CommandResult(
            1, "", "docker: 'compose' is not a docker command."
        )
    )

    result = preflight.check_compose_version(runner=runner, which=_which)

    assert result.status == preflight.FAIL
    assert "Docker Compose v2 is not available" in result.reason


def test_compose_config_parses_base_and_host_overlay(tmp_path):
    env = tmp_path / ".env"
    env.write_text("COMPOSE_PROFILES=stable\n", encoding="utf-8")
    runner = _sequence(preflight.CommandResult(0, ""))

    result = preflight.check_compose_config(
        runner,
        _which,
        compose_file=tmp_path / "compose.yaml",
        host_file=tmp_path / "compose.host.yaml",
        env_file=env,
    )

    assert result.status == preflight.PASS
    command = runner.calls[0]  # type: ignore[attr-defined]
    assert command[:2] == ["/usr/bin/docker", "compose"]
    assert "--env-file" in command
    assert "config" in command


def test_compose_config_reports_a_parse_error(tmp_path):
    env = tmp_path / ".env"
    env.write_text("COMPOSE_PROFILES=stable\n", encoding="utf-8")
    runner = _sequence(
        preflight.CommandResult(1, "", "yaml: line 3: did not find expected key")
    )

    result = preflight.check_compose_config(
        runner,
        _which,
        compose_file=tmp_path / "compose.yaml",
        host_file=tmp_path / "compose.host.yaml",
        env_file=env,
    )

    assert result.status == preflight.FAIL
    assert "do not parse" in result.reason
    assert "did not find expected key" in result.reason


def test_compose_config_asks_for_the_pinned_env_file(tmp_path):
    result = preflight.check_compose_config(
        _sequence(),
        _which,
        compose_file=tmp_path / "compose.yaml",
        host_file=tmp_path / "compose.host.yaml",
        env_file=tmp_path / "missing.env",
    )

    assert result.status == preflight.FAIL
    assert "pin-images --write" in result.reason


# ---------------------------------------------------------------------------
# static Compose and image pins
# ---------------------------------------------------------------------------


def test_static_compose_pass():
    result = preflight.check_compose_files(lambda: [])

    assert result.status == preflight.PASS


def test_static_compose_reports_the_first_problem():
    result = preflight.check_compose_files(
        lambda: ["services.shim must set OPENHAB_UPSTREAM", "second problem"]
    )

    assert result.status == preflight.FAIL
    assert "OPENHAB_UPSTREAM" in result.reason
    assert "(+1 more)" in result.reason


def test_static_compose_reports_a_loader_error():
    from e2e.compose import ComposeError

    def broken() -> List[str]:
        raise ComposeError("invalid YAML at line 1")

    result = preflight.check_compose_files(broken)

    assert result.status == preflight.FAIL
    assert "invalid YAML" in result.reason


def test_pins_pass_reuses_the_existing_validation(tmp_path):
    env = tmp_path / ".env"
    seen: Dict[str, Path] = {}

    def checker(*, env_path):
        seen["env_path"] = env_path
        return []

    result = preflight.check_image_pins(env_file=env, checker=checker)

    assert result.status == preflight.PASS
    assert seen["env_path"] == env


def test_pins_failure_names_the_drift(tmp_path):
    result = preflight.check_image_pins(
        env_file=tmp_path / ".env",
        checker=lambda *, env_path: ["OPENHAB_STABLE_IMAGE is not pinned to a digest"],
    )

    assert result.status == preflight.FAIL
    assert "not pinned" in result.reason


# ---------------------------------------------------------------------------
# Connect IQ SDK
# ---------------------------------------------------------------------------


def _sdk_tree(
    tmp_path: Path,
    *,
    version: str = "9.3.0",
    monkeyc: bool = True,
    monkeydo: bool = True,
    simulator: bool = True,
) -> Path:
    root = tmp_path / "sdk"
    (root / "linux" / "bin").mkdir(parents=True)
    (root / "windows" / "bin").mkdir(parents=True)
    (root / "version.txt").write_text(version + "\n", encoding="utf-8")
    for relative, present in (
        ("linux/bin/monkeyc", monkeyc),
        ("linux/bin/monkeydo", monkeydo),
        ("windows/bin/simulator.exe", simulator),
    ):
        if present:
            (root / relative).write_text("", encoding="utf-8")
    return root


def test_sdk_pass_reports_the_version_and_tools(tmp_path):
    root = _sdk_tree(tmp_path)

    result = preflight.check_sdk(
        {"CIQ_SDK_HOST_PATH": str(root)}, expected_version="9.3.0"
    )

    assert result.status == preflight.PASS
    assert str(root) in result.reason
    assert "9.3.0" in result.reason


def test_sdk_host_path_takes_precedence_over_the_aliases(tmp_path):
    root = _sdk_tree(tmp_path)

    result = preflight.check_sdk(
        {
            "CIQ_SDK_HOST_PATH": str(root),
            "CIQ_SDK_ROOT": str(tmp_path / "absent"),
        },
        expected_version="9.3.0",
    )

    assert result.status == preflight.PASS


def test_sdk_names_the_variable_when_it_is_not_configured():
    result = preflight.check_sdk({})

    assert result.status == preflight.FAIL
    assert "CIQ_SDK_HOST_PATH" in result.reason
    assert "tests/docs/sdk.md" in result.reason


def test_sdk_rejects_a_path_that_is_not_a_directory(tmp_path):
    missing = tmp_path / "absent"

    result = preflight.check_sdk(
        {"CIQ_SDK_PATH": str(missing)}, expected_version="9.3.0"
    )

    assert result.status == preflight.FAIL
    assert "CIQ_SDK_PATH" in result.reason
    assert "not a directory" in result.reason


def test_sdk_rejects_a_version_mismatch(tmp_path):
    root = _sdk_tree(tmp_path, version="9.2.0")

    result = preflight.check_sdk(
        {"CIQ_SDK_HOST_PATH": str(root)}, expected_version="9.3.0"
    )

    assert result.status == preflight.FAIL
    assert "9.2.0" in result.reason
    assert "9.3.0" in result.reason


def test_sdk_rejects_a_missing_wine_side_simulator(tmp_path):
    root = _sdk_tree(tmp_path, simulator=False)

    result = preflight.check_sdk(
        {"CIQ_SDK_HOST_PATH": str(root)}, expected_version="9.3.0"
    )

    assert result.status == preflight.FAIL
    assert "simulator.exe" in result.reason


def test_sdk_rejects_a_missing_launcher(tmp_path):
    root = _sdk_tree(tmp_path, monkeydo=False)

    result = preflight.check_sdk(
        {"CIQ_SDK_HOST_PATH": str(root)}, expected_version="9.3.0"
    )

    assert result.status == preflight.FAIL
    assert "monkeydo" in result.reason


# ---------------------------------------------------------------------------
# loopback ports
# ---------------------------------------------------------------------------


def test_ports_pass_lists_the_loopback_ports():
    result = preflight.check_ports(NETWORK, free=lambda host, port: True)

    assert result.status == preflight.PASS
    assert "18080" in result.reason
    assert "18081" in result.reason


def test_ports_names_the_process_that_holds_a_port():
    result = preflight.check_ports(
        NETWORK,
        free=lambda host, port: port != 18080,
        listeners=lambda: {18080: "java (pid 4123)"},
    )

    assert result.status == preflight.FAIL
    assert "18080" in result.reason
    assert "java (pid 4123)" in result.reason
    assert "openhab_stable, openhab_dev" in result.reason


def test_ports_says_so_when_the_process_cannot_be_identified():
    result = preflight.check_ports(
        NETWORK,
        free=lambda host, port: port != 18081,
        listeners=lambda: {},
    )

    assert result.status == preflight.FAIL
    assert "not identified" in result.reason


def test_ports_does_not_ask_for_listeners_when_all_are_free():
    def unexpected():
        raise AssertionError("listen map should not be read")

    result = preflight.check_ports(
        NETWORK, free=lambda host, port: True, listeners=unexpected
    )

    assert result.status == preflight.PASS


def test_parse_ss_listeners_reads_recorded_output():
    listeners = preflight.parse_ss_listeners(_recorded("ss-listen.txt"))

    assert listeners[8080] == "java (pid 4123)"
    assert listeners[8787] == "nginx (pid 842)"
    assert listeners[9999] == "node (pid 404)"
    assert listeners[9090] == "an unidentified process"


def test_ss_processes_runs_the_recorded_command():
    runner = _sequence(
        preflight.CommandResult(0, _recorded("ss-listen.txt"))
    )

    listeners = preflight.ss_processes(runner=runner, which=_which)

    assert listeners[8080] == "java (pid 4123)"
    assert runner.calls[0] == ["/usr/bin/ss", "-H", "-ltnp"]  # type: ignore[attr-defined]


def test_ss_processes_is_empty_when_ss_is_absent():
    assert preflight.ss_processes(runner=_sequence(), which=lambda name: None) == {}


# ---------------------------------------------------------------------------
# repository layout
# ---------------------------------------------------------------------------


def test_layout_of_this_checkout_is_complete():
    result = preflight.check_repository_layout(preflight.repo_root())

    assert result.status == preflight.PASS


def test_layout_names_every_missing_path(tmp_path):
    result = preflight.check_repository_layout(tmp_path)

    assert result.status == preflight.FAIL
    assert "monkey.jungle" in result.reason
    assert "tests/scenarios" in result.reason


def test_layout_rejects_a_scenarios_directory_without_scenarios(tmp_path):
    for relative in preflight.REQUIRED_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    for relative in preflight.REQUIRED_DIRECTORIES:
        (tmp_path / relative).mkdir(parents=True, exist_ok=True)

    result = preflight.check_repository_layout(tmp_path)

    assert result.status == preflight.FAIL
    assert "no scenarios found" in result.reason


def test_artifacts_directory_that_exists_is_accepted(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    result = preflight.check_artifacts(artifacts)

    assert result.status == preflight.PASS


def test_artifacts_directory_is_created_lazily_without_side_effects(tmp_path):
    artifacts = tmp_path / "artifacts"

    result = preflight.check_artifacts(artifacts)

    assert result.status == preflight.PASS
    assert not artifacts.exists()


def test_artifacts_path_that_is_a_file_is_rejected(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.write_text("", encoding="utf-8")

    result = preflight.check_artifacts(artifacts)

    assert result.status == preflight.FAIL
    assert "not a directory" in result.reason


# ---------------------------------------------------------------------------
# host resources
# ---------------------------------------------------------------------------


def _resources(memory_gib: float, cpus: int, disk_gib: float) -> preflight.HostResources:
    return preflight.HostResources(
        memory_bytes=int(memory_gib * preflight.GIB),
        cpu_count=cpus,
        disk_bytes=int(disk_gib * preflight.GIB),
        disk_path="/tmp",
    )


def test_resources_pass_above_the_recommendation():
    result = preflight.check_resources(_resources(8, 4, 50))

    assert result.status == preflight.PASS
    assert "8.0 GiB RAM" in result.reason


def test_resources_warn_between_the_minimum_and_the_recommendation():
    result = preflight.check_resources(_resources(3, 1, 10))

    assert result.status == preflight.WARN
    assert "recommendation" in result.reason


def test_resources_fail_below_the_documented_minimum():
    result = preflight.check_resources(_resources(1, 1, 2))

    assert result.status == preflight.FAIL
    assert "minimum" in result.reason


def test_resources_unknown_measurements_are_not_failures():
    result = preflight.check_resources(_resources(0, 0, 0))

    assert result.status == preflight.PASS
    assert "unknown" in result.reason


# ---------------------------------------------------------------------------
# report and CLI contract
# ---------------------------------------------------------------------------


def test_report_fails_only_on_hard_failures():
    report = preflight.PreflightReport()
    report.add(preflight.CheckResult("a", "docker", preflight.FAIL, "boom"))
    report.add(preflight.CheckResult("b", "resources", preflight.WARN, "tight"))

    assert not report.ok
    assert report.exit_code == 1
    assert report.counts() == {
        "passed": 0,
        "failed": 1,
        "warnings": 1,
        "skipped": 0,
    }


def test_run_preflight_records_every_check_when_groups_are_skipped(tmp_path):
    report = preflight.run_preflight(
        skip=preflight.GROUPS,
        artifacts=tmp_path / "artifacts",
        repo=tmp_path,
    )

    assert report.ok
    assert report.exit_code == 0
    assert len(report.checks) == len(preflight.CHECKS)
    assert all(check.status == preflight.SKIP for check in report.checks)


def test_json_document_has_a_stable_shape(tmp_path):
    report = preflight.run_preflight(
        skip=preflight.GROUPS,
        artifacts=tmp_path / "artifacts",
        repo=tmp_path,
    )

    document = report.to_document()

    assert document["ok"] is True
    assert document["exit_code"] == 0
    assert document["summary"]["skipped"] == len(preflight.CHECKS)
    assert {check["id"] for check in document["checks"]} == {
        check_id for check_id, _group in preflight.CHECKS
    }


def test_text_rendering_ends_with_the_summary():
    report = preflight.PreflightReport()
    report.add(preflight.CheckResult("a", "docker", preflight.PASS, "fine"))

    lines = report.render_lines()

    assert "summary: 1 passed, 0 failed, 0 warning(s), 0 skipped" in lines
    assert lines[-1] == "preflight: ok"


def test_parse_skip_accepts_comma_separated_and_repeated_values():
    assert preflight.parse_skip(["docker,sdk", " ports "]) == (
        "docker",
        "sdk",
        "ports",
    )


def test_parse_skip_rejects_an_unknown_group():
    with pytest.raises(preflight.PreflightError):
        preflight.parse_skip(["nope"])


def test_cli_preflight_json_with_every_group_skipped(capsys, tmp_path):
    code = cli.main(
        [
            "preflight",
            "--json",
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--skip",
            ",".join(preflight.GROUPS),
        ]
    )

    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert document["summary"]["skipped"] == len(preflight.CHECKS)


def test_cli_preflight_rejects_an_unknown_skip_group(capsys):
    code = cli.main(["preflight", "--skip", "nope"])

    assert code == 2
    assert "unknown --skip group" in capsys.readouterr().err
