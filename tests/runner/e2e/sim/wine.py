"""Wine simulator driver -- skeleton.

Why this is a skeleton
======================

The Connect IQ simulator is a Windows GUI application with no supported
automation interface (see the investigation recorded in issue #140):

- no headless mode and no screen/input API;
- app launch goes through ``monkeydo <prg> <device>``, which streams
  ``System.println`` output to stdout;
- startup conditions (``BleState``, ``WifiState``, ``nightMode``,
  ``SleepMode``, ``TouchScreenEnabled``, ``LastUsedDevice``) are scriptable
  through ``simulator.ini``;
- runtime controls are native Win32 menus and device input must be injected as
  keyboard/mouse events into the simulator window;
- screenshots come from the simulator's own "Save Screenshot" action or from an
  external window capture.

The foundation stops deliberately before that work: this class fixes the
contract and the failure behaviour, so the implementation can be added without
redesigning the runner. Every method raises :class:`DriverError` with the next
concrete step, rather than being silently absent.

Implementation plan (tracked separately, see tests/docs/simulator-driver.md):

1. ``prepare``: create a throwaway ``WINEPREFIX`` per run, install the SDK's
   Windows runtime if needed, and seed ``%TEMP%\\com.garmin.connectiq``.
2. ``start``: write ``simulator.ini`` for the requested device and mode, start
   the simulator under Xvfb, launch with ``monkeydo`` and capture stdout as the
   event stream.
3. ``wait_ready``: wait for the first ``E2E: view`` trace line (added by the
   debug trace change) instead of guessing from a fixed delay.
4. ``press``/``tap``: drive the simulator menus and device buttons with
   ``xdotool`` against the Xvfb display; keep input coordinates in the device
   matrix, not in scenarios.
5. ``screenshot``: simulator "Save Screenshot" if it can be scripted, otherwise
   ``xwd``/ImageMagick against the simulator window.
6. ``stop``: terminate simulator and ``wineserver``, remove the prefix.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import ClassVar, List, Optional, Sequence

from ..config import Device
from ..sdk import SdkLayout, require_simulator
from ..settings import AppSettings
from .base import Button, DriverError, SimulatorDriver

#: Environment variable that may point at an already installed Wine binary.
WINE_ENV = "E2E_WINE_BINARY"
#: Display used for the simulator window when Xvfb is managed by the driver.
DEFAULT_DISPLAY = ":99"


class WineSimulatorDriver(SimulatorDriver):
    """Drive the Windows Connect IQ simulator through Wine (not implemented)."""

    name: ClassVar[str] = "wine"
    supports_events: ClassVar[bool] = True
    supports_screenshots: ClassVar[bool] = True

    def __init__(
        self,
        layout: SdkLayout,
        *,
        wineprefix: Optional[Path] = None,
        display: Optional[str] = None,
        artifact_dir: Optional[Path] = None,
    ) -> None:
        self.layout = layout
        self.wineprefix = Path(wineprefix) if wineprefix else None
        self.display = display or os.environ.get("DISPLAY", DEFAULT_DISPLAY)
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None
        self._events: List[str] = []
        self._started = False

    # -- capability probe --------------------------------------------------
    @classmethod
    def is_available(cls, layout: SdkLayout) -> bool:
        """True when Wine and the Windows simulator are both present."""
        try:
            require_simulator(layout)
        except Exception:  # noqa: BLE001 - probe must not raise
            return False
        wine = os.environ.get(WINE_ENV) or shutil.which("wine")
        return bool(wine)

    # -- contract ----------------------------------------------------------
    def prepare(self, prg: Path) -> None:
        require_simulator(self.layout)
        raise NotImplementedError(
            self._todo("prepare", "create a throwaway WINEPREFIX and stage the PRG")
        )

    def start(self, device: Device, settings: AppSettings, mode: str = "ble") -> None:
        raise NotImplementedError(
            self._todo("start", "write simulator.ini and launch into the requested mode")
        )

    def wait_ready(self, timeout_s: float) -> None:
        raise NotImplementedError(
            self._todo("wait_ready", "wait for the first `E2E:VIEW` trace event")
        )

    def stop(self) -> None:
        self._started = False

    def press(self, button: Button, count: int = 1) -> None:
        raise NotImplementedError(self._todo("press", "inject key events with xdotool"))

    def tap(self, x: int, y: int, count: int = 1) -> None:
        raise NotImplementedError(self._todo("tap", "inject pointer events with xdotool"))

    def screenshot(self, path: Path) -> Path:
        raise NotImplementedError(
            self._todo("screenshot", "script Save Screenshot or capture the window")
        )

    def events(self) -> Sequence[str]:
        return tuple(self._events)

    # -- helpers -----------------------------------------------------------
    def _todo(self, method: str, next_step: str) -> str:
        return (
            f"WineSimulatorDriver.{method} is not implemented yet: {next_step}. "
            "See tests/docs/simulator-driver.md for the implementation plan. "
            "Protocol-tier scenarios can run without it; UI-tier scenarios cannot."
        )
