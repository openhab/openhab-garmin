"""Simulator driver abstraction.

A driver owns exactly one Connect IQ simulator instance and exposes the
smallest set of primitives a scenario needs: launch, wait until the app is up,
inject input, capture the screen, stop, and (where available) read the app's
console output.

The interface is the contract. The Wine implementation under ``wine.py`` is a
skeleton -- the simulator has no supported automation API, so it is driven
through Wine, Xvfb and xdotool, and that work is deliberately separate from
this foundation. Contract tests in ``e2e.contract`` exercise every method, so a
half-implemented driver fails loudly instead of producing a plausible-looking
empty run.
"""

from __future__ import annotations

import abc
import time
from enum import Enum
from pathlib import Path
from typing import ClassVar, List, Optional, Sequence

from ..config import Device
from ..settings import AppSettings


class Button(str, Enum):
    """Device buttons the harness can press.

    Names are the semantic buttons, not physical positions: a driver maps them
    to whatever the simulator exposes for the device under test.
    """

    START = "START"
    UP = "UP"
    DOWN = "DOWN"
    ENTER = "ENTER"
    BACK = "BACK"
    MENU = "MENU"


class DriverError(RuntimeError):
    """Raised when the simulator cannot be prepared, driven or captured."""


class SimulatorDriver(abc.ABC):
    """Contract for a simulator driver.

    Implementations must be usable as a context manager; ``__exit__`` stops the
    simulator even when a scenario fails.
    """

    #: Short driver name used by the CLI (``--driver wine``).
    name: ClassVar[str] = ""
    #: True when ``events()`` yields real app console output.
    supports_events: ClassVar[bool] = False
    #: True when ``screenshot()`` yields a real screen capture.
    supports_screenshots: ClassVar[bool] = False

    # -- lifecycle ---------------------------------------------------------
    @abc.abstractmethod
    def prepare(self, prg: Path) -> None:
        """Make the compiled PRG available to the simulator."""

    @abc.abstractmethod
    def start(self, device: Device, settings: AppSettings, mode: str = "ble") -> None:
        """Start the simulator, push the PRG and launch the app.

        ``mode`` is the communication mode to initialise the simulator with
        (``ble``, ``wifi`` or ``offline``); where a simulator cannot be
        scripted into that state, the driver must raise rather than silently
        running in a different mode.
        """

    @abc.abstractmethod
    def wait_ready(self, timeout_s: float) -> None:
        """Block until the app has been launched and rendered its first view."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Terminate the simulator and release the device filesystem."""

    # -- input -------------------------------------------------------------
    @abc.abstractmethod
    def press(self, button: Button, count: int = 1) -> None:
        """Press a device button ``count`` times."""

    @abc.abstractmethod
    def tap(self, x: int, y: int, count: int = 1) -> None:
        """Tap a screen coordinate ``count`` times."""

    # -- observation -------------------------------------------------------
    @abc.abstractmethod
    def screenshot(self, path: Path) -> Path:
        """Capture the simulator window as a PNG at ``path``."""

    @abc.abstractmethod
    def events(self) -> Sequence[str]:
        """Return the app console lines seen so far, oldest first."""

    # -- helpers -----------------------------------------------------------
    def wait_for_event(self, contains: str, timeout_s: float, poll_s: float = 0.25) -> Optional[str]:
        """Wait for a console line containing ``contains``.

        Returns the matching line, or ``None`` when the driver has no event
        stream. Scenarios that assert on events must treat ``None`` as
        "skipped", not as a pass: the distinction is recorded in the report.
        """
        if not self.supports_events:
            return None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for line in self.events():
                if contains in line:
                    return line
            time.sleep(poll_s)
        raise DriverError(
            f"no console line containing {contains!r} within {timeout_s:.0f}s; "
            f"last lines: {list(self.events())[-10:]}"
        )

    def __enter__(self) -> "SimulatorDriver":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        try:
            self.stop()
        except Exception:  # noqa: BLE001 - shutdown must never mask the real failure
            pass


def empty_events() -> List[str]:
    """Convenience for drivers without an event stream."""
    return []
