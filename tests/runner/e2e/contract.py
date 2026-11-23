"""Driver contract tests.

The interface in :mod:`e2e.sim.base` is the contract every driver must honour.
These cases exercise it mechanically, so a driver that only implements the
methods it happened to need fails here instead of producing a run that looks
successful but asserted nothing.

Run with ``python -m e2e contract-test --driver null`` to check the harness
itself (works on any machine), or ``--driver wine`` to see exactly which parts
of the Wine implementation are still missing.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .config import load_devices
from .settings import load_settings
from .sim.base import Button, SimulatorDriver

PASS = "pass"
FAIL = "fail"
SKIPPED = "skipped"
NOT_IMPLEMENTED = "not-implemented"


@dataclass(frozen=True)
class ContractCase:
    name: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (PASS, SKIPPED)


def missing_methods(driver_class: type) -> List[str]:
    """Names of abstract members left unimplemented by ``driver_class``."""
    return sorted(getattr(driver_class, "__abstractmethods__", frozenset()))


def _png_is_valid(path: Path) -> str:
    """Return an empty string when the file is a valid PNG, else the reason."""
    if not path.is_file():
        return "file does not exist"
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "missing PNG signature"
    if len(data) < 33 or data[12:16] != b"IHDR":
        return "missing IHDR chunk"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        return f"invalid dimensions {width}x{height}"
    return ""


def _run_case(name: str, fn: Callable[[], None]) -> ContractCase:
    try:
        fn()
    except NotImplementedError as error:
        return ContractCase(name, NOT_IMPLEMENTED, str(error))
    except Exception as error:  # noqa: BLE001 - a contract failure is a result
        return ContractCase(name, FAIL, f"{type(error).__name__}: {error}")
    return ContractCase(name, PASS)


def run_contract(
    driver: SimulatorDriver,
    workdir: Path,
    *,
    device_id: Optional[str] = None,
    settings_fixture: str = "default",
) -> List[ContractCase]:
    """Exercise every part of the driver contract against ``driver``.

    The driver is expected to be a fresh, unstarted instance. It is stopped at
    the end even when a case fails.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    devices = load_devices()
    device = next(
        (candidate for candidate in devices if candidate.id == (device_id or devices[0].id)),
        devices[0],
    )
    settings = load_settings(settings_fixture)
    prg = workdir / "contract.prg"
    prg.write_bytes(b"contract")
    shot = workdir / "contract-screenshot.png"

    cases: List[ContractCase] = []

    def interface() -> None:
        if not driver.name:
            raise AssertionError("driver.name is empty")
        abstract = missing_methods(type(driver))
        if abstract:
            raise AssertionError(f"unimplemented abstract methods: {', '.join(abstract)}")
        if getattr(type(driver), "__abstractmethods__", None):
            raise AssertionError("driver class is still abstract")
        signature = inspect.signature(type(driver).start)
        if "mode" not in signature.parameters:
            raise AssertionError("start() lost its 'mode' parameter")

    cases.append(_run_case("interface", interface))
    cases.append(_run_case("prepare", lambda: driver.prepare(prg)))
    cases.append(_run_case("start", lambda: driver.start(device, settings, "ble")))
    cases.append(_run_case("wait_ready", lambda: driver.wait_ready(1.0)))

    for button in Button:
        cases.append(
            _run_case(f"press[{button.value}]", lambda button=button: driver.press(button, 1))
        )
    cases.append(_run_case("tap", lambda: driver.tap(1, 1, 1)))

    def screenshot() -> None:
        result = driver.screenshot(shot)
        if Path(result) != shot:
            raise AssertionError(f"screenshot() returned {result}, expected {shot}")
        reason = _png_is_valid(shot)
        if reason:
            raise AssertionError(f"{shot}: {reason}")

    cases.append(_run_case("screenshot", screenshot))

    def events() -> None:
        lines = driver.events()
        if isinstance(lines, (str, bytes)):
            raise AssertionError("events() must return a sequence of lines, not a string")
        for line in lines:
            if not isinstance(line, str):
                raise AssertionError(f"event {line!r} is not a string")

    cases.append(_run_case("events", events))
    cases.append(_run_case("stop", driver.stop))

    return cases


def format_report(cases: Sequence[ContractCase]) -> str:
    """Render contract results as a fixed-width table."""
    width = max((len(case.name) for case in cases), default=0)
    lines = []
    for case in cases:
        detail = f"  {case.detail}" if case.detail else ""
        lines.append(f"  {case.name.ljust(width)}  {case.status}{detail}")
    return "\n".join(lines)


def summary(cases: Sequence[ContractCase]) -> Dict[str, int]:
    counts: Dict[str, int] = {PASS: 0, FAIL: 0, SKIPPED: 0, NOT_IMPLEMENTED: 0}
    for case in cases:
        counts[case.status] = counts.get(case.status, 0) + 1
    return counts


def exit_code(cases: Sequence[ContractCase]) -> int:
    """0 when nothing failed; 1 for a failure; 2 when the driver is unfinished."""
    counts = summary(cases)
    if counts.get(FAIL):
        return 1
    if counts.get(NOT_IMPLEMENTED):
        return 2
    return 0
