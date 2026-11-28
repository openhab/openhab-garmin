"""The `connection` step: only a NEW trace line proves the mode changed.

The lesson from #421 is that a click or a repaint can look like success. This
step therefore fails unless the app reports the new mode *after* the action; a
`CONNECTION` line from start-up must never satisfy it, or every transition
scenario would be green without changing anything.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import ClassVar, List, Sequence

import pytest

from e2e import engine
from e2e.config import ConfigError, default_device_id, find_device
from e2e.engine import ScenarioRunner, _Context
from e2e.fixtures import ScenarioSkipped
from e2e.scenarios import find_scenario, parse_steps
from e2e.sim.base import CONNECTION_TRACE_MODES
from e2e.trace import TraceLog

STARTUP_BLE = ("E2E:LANGUAGE;code=eng", "E2E:CONNECTION;mode=BLE")
STARTUP_WIFI = STARTUP_BLE + ("E2E:CONNECTION;mode=WIFI",)


class _MenuDriver:
    """A driver with the menu capability and an editable console stream."""

    name: ClassVar[str] = "native"
    supports_connection_switch: ClassVar[bool] = True
    supports_events: ClassVar[bool] = True

    def __init__(self, lines: Sequence[str], adds: Sequence[str] = ()) -> None:
        self._lines: List[str] = list(lines)
        self._adds: List[str] = list(adds)
        self.calls: List[str] = []

    def events(self) -> Sequence[str]:
        return list(self._lines)

    def set_connection_mode(self, mode: str, *, timeout_s: float = 0.0) -> str:
        self.calls.append(mode)
        self._lines.extend(self._adds)
        return "Settings -> Connection Type -> BLE -> Not Connected <- keys"


class _NoMenuDriver(_MenuDriver):
    supports_connection_switch: ClassVar[bool] = False


def _step(mode: str, timeout_ms: int = 1000):
    return parse_steps(
        [{"action": "launch"}, {"action": "connection", "mode": mode, "timeout_ms": timeout_ms}],
        "test",
    )[1]


def _runner(tmp_path: Path) -> ScenarioRunner:
    scenario = find_scenario("extended/connection-ble-wifi")
    return ScenarioRunner(
        scenario,
        find_device(default_device_id()),
        openhab_url="http://openhab.invalid:8080",
        shim_url="http://shim.invalid:8080",
        artifacts_dir=tmp_path / "artifacts",
        driver_name="native",
    )


def _context(tmp_path: Path, driver, *, dry_run: bool = False, mode: str = "ble") -> _Context:
    scenario = find_scenario("extended/connection-ble-wifi")
    context = _Context(
        scenario=scenario,
        device=find_device(default_device_id()),
        driver=driver,  # type: ignore[arg-type]
        openhab=None,  # type: ignore[arg-type]
        shim=None,  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
        workdir=tmp_path,
        goldens_dir=tmp_path / "goldens",
        dry_run=dry_run,
        update_goldens=False,
        skip_events=False,
        deadline=time.monotonic() + 60,
        log=lambda message: None,
    )
    context.trace = TraceLog(driver.events)
    return context


def test_a_new_connection_line_after_the_action_passes(tmp_path: Path) -> None:
    driver = _MenuDriver(STARTUP_BLE, adds=["E2E:CONNECTION;mode=WIFI"])
    status, message, _artifacts = _runner(tmp_path)._do_connection(
        _step("wifi"), _context(tmp_path, driver)
    )
    assert status == "passed", message
    assert "CONNECTION;mode=WIFI" in message
    assert driver.calls == ["wifi"]


def test_the_startup_line_is_not_evidence(tmp_path: Path) -> None:
    """The trace already says WIFI, but nothing changed after the action."""
    driver = _MenuDriver(STARTUP_WIFI, adds=[])
    with pytest.raises(engine.AssertionFailure) as error:
        _runner(tmp_path)._do_connection(_step("wifi"), _context(tmp_path, driver))
    assert "no new E2E:CONNECTION;mode=WIFI" in str(error.value)
    assert "start-up CONNECTION line is not evidence" in str(error.value)


def test_a_wrong_new_mode_does_not_satisfy_the_step(tmp_path: Path) -> None:
    driver = _MenuDriver(STARTUP_BLE, adds=["E2E:CONNECTION;mode=OFFLINE"])
    with pytest.raises(engine.AssertionFailure) as error:
        _runner(tmp_path)._do_connection(_step("wifi"), _context(tmp_path, driver))
    assert "CONNECTION;mode=OFFLINE" in str(error.value)


def test_a_driver_without_the_capability_skips_the_scenario(tmp_path: Path) -> None:
    driver = _NoMenuDriver(STARTUP_BLE)
    with pytest.raises(ScenarioSkipped) as error:
        _runner(tmp_path)._do_connection(_step("wifi"), _context(tmp_path, driver))
    assert "cannot change the connection mode" in str(error.value)
    assert "--driver native" in str(error.value)


def test_a_dry_run_reports_the_intent_and_does_not_touch_the_driver(
    tmp_path: Path,
) -> None:
    driver = _MenuDriver(STARTUP_BLE)
    status, message, _artifacts = _runner(tmp_path)._do_connection(
        _step("wifi"), _context(tmp_path, driver, dry_run=True)
    )
    assert status == "skipped"
    assert "would switch" in message
    assert driver.calls == []


def test_preflight_rejects_a_transition_to_the_launch_mode() -> None:
    from dataclasses import replace

    steps = parse_steps(
        [{"action": "launch"}, {"action": "connection", "mode": "ble"}], "test"
    )
    scenario = replace(find_scenario("extended/connection-ble-wifi"), steps=steps)
    runner = ScenarioRunner(
        scenario,
        find_device(default_device_id()),
        openhab_url="http://openhab.invalid:8080",
        shim_url="http://shim.invalid:8080",
        artifacts_dir=Path("/tmp/unused"),
        driver_name="native",
    )
    with pytest.raises(ConfigError) as error:
        runner._preflight()
    assert "already in that mode" in str(error.value)


def test_every_mode_has_a_trace_name() -> None:
    assert CONNECTION_TRACE_MODES == {"ble": "BLE", "wifi": "WIFI", "offline": "OFFLINE"}
