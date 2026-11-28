"""Simulator driver abstraction.

A driver owns exactly one Connect IQ simulator instance and exposes the
smallest set of primitives a scenario needs: launch, wait until the app is up,
inject input, capture the screen, stop, and (where available) read the app's
console output.

The interface is the contract. ``wine.py`` drives the real simulator through
Wine, Xvfb and ``xdotool``; ``null.py`` is the dry-run driver. Contract tests in
``e2e.contract`` exercise every method, so a half-implemented driver fails
loudly instead of producing a plausible-looking empty run.
"""

from __future__ import annotations

import abc
import time
from enum import Enum
from pathlib import Path
from typing import ClassVar, List, Optional, Sequence

from ..config import Device
from ..settings import AppSettings

#: Languages every driver can always put the app into, whatever the simulator
#: stores. ``eng`` is the Connect IQ default; a driven simulator that persists a
#: numeric ``Language`` index relies on its own default for this one. Every other
#: language needs a driver that says it can provision that code, which
#: ``can_provision_language`` answers precisely.
RUNNABLE_LANGUAGES = ("eng",)

#: The app mode a scenario requests, and the value its ``E2E:CONNECTION`` line
#: must carry once the transition has happened. ``WIFI_CHECK_PENDING`` is a
#: transient state, never a destination: a scenario that switches to Wi-Fi waits
#: for ``WIFI`` even though the app may report the pending state on the way.
CONNECTION_TRACE_MODES = {
    "ble": "BLE",
    "wifi": "WIFI",
    "offline": "OFFLINE",
}


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
    #: True when ``start()`` can put the app into the requested system language.
    #: A driver that cannot must say so: the engine reports the runs for other
    #: languages as blocked rather than failing them for an environment gap.
    #: ``can_provision_language`` is the per-language answer and is what the
    #: engine asks; this flag only says the driver can provision *some* language.
    provisions_language: ClassVar[bool] = True
    #: True when the driver can relaunch the app into another start-up mode
    #: (``restart_in_mode``). That proves start-up mode, never a mid-run
    #: transition; a driver that cannot script the simulator's menus says so.
    supports_restart_in_mode: ClassVar[bool] = False
    #: True when the driver can probe the simulator's own menu bar with real
    #: clicks (``probe_menus``). Probing reports evidence; it asserts nothing.
    supports_menu_probe: ClassVar[bool] = False
    #: True when the driver can change the connection state while the app runs
    #: (``set_connection_mode``). This is a driver capability, not a suite-wide
    #: claim: it needs the simulator's own menu, which only the native build
    #: exposes as X windows (see ``sim/menus.py``).
    supports_connection_switch: ClassVar[bool] = False

    #: True when the window toolkit can report *no* child window under a pointer
    #: that is nevertheless inside the top-level. A native GTK top-level does;
    #: Wine's windows report themselves or a child. The pointer guard then falls
    #: back to the window rectangle instead of failing, while still failing for a
    #: point outside it.
    pointer_top_level_fallback: ClassVar[bool] = False

    def set_connection_mode(self, mode: str, *, timeout_s: float = 0.0) -> str:
        """Put the simulator into the state app ``mode`` expects, under the app.

        The driver only *issues* the menu action; it must not claim the app
        changed mode. The caller proves the effect from the app's
        ``E2E:CONNECTION`` line, because a click that returns success is not
        evidence (the lesson from #421).

        Returns a one-line description of what was done, for the log and for a
        failure message. Raises :class:`NotImplementedError` on a driver that
        does not advertise ``supports_connection_switch``.
        """
        raise NotImplementedError(
            f"{self.name} cannot change the connection mode while the app runs"
        )

    def restart_in_mode(
        self, mode: str, *, night: Optional[bool] = None, timeout_s: float = 0.0
    ) -> bool:
        """Relaunch the app into another start-up ``mode``, if supported.

        Must not be used to claim a mid-run transition: only the simulator's own
        menu can change the mode while the app runs.
        """
        raise NotImplementedError(
            f"{self.name} cannot relaunch the app into another start-up mode"
        )

    def can_provision_language(self, language: str) -> bool:
        """True when ``start()`` can put the app into ``language``.

        Drivers override this when provisioning is per language (a simulator
        that stores a numeric index for a subset of codes). The default answers
        from ``RUNNABLE_LANGUAGES`` and the driver-wide flag.
        """
        return language in RUNNABLE_LANGUAGES or self.provisions_language

    # -- lifecycle ---------------------------------------------------------
    @abc.abstractmethod
    def prepare(self, prg: Path) -> None:
        """Make the compiled PRG available to the simulator."""

    @abc.abstractmethod
    def start(
        self,
        device: Device,
        settings: AppSettings,
        mode: str = "ble",
        language: str = "eng",
    ) -> None:
        """Start the simulator, push the PRG and launch the app.

        ``mode`` is the communication mode to initialise the simulator with
        (``ble``, ``wifi`` or ``offline``); where a simulator cannot be
        scripted into that state, the driver must raise rather than silently
        running in a different mode. ``language`` is the Connect IQ system
        language (``eng``, ``deu``, ...); the driver must select it before
        launch and must not silently fall back to the simulator's default.
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

    def screen_screenshot(self, window_capture: Path, path: Path) -> Optional[Path]:
        """Crop a window capture to the device screen, or return ``None``.

        Goldens compare the device screen, never the window: the window chrome
        is simulator-version and driver specific (Wine renders one size, the
        native build another, the device screen is the same in both), so a
        window-sized golden would encode the simulator's bezel and window size
        as expected app output. A driver that cannot isolate the screen returns
        ``None``, and the caller skips the comparison instead of asserting on
        the wrong pixels.
        """
        return None

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
