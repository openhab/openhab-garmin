"""Null simulator driver: dry-run only.

It records what would have been done and writes a valid 1x1 PNG for every
``screenshot()`` call. It is what ``--dry-run`` uses, which keeps scenario
wiring, assertion plumbing and reporting testable on any machine -- including
one without Wine.

It must never be mistaken for a real run: it advertises
``supports_events = False`` and ``supports_screenshots = False``, so event and
golden assertions are reported as skipped, not passed.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import ClassVar, List, Sequence

from ..config import Device
from ..settings import AppSettings
from .base import Button, SimulatorDriver


def png_bytes(width: int = 1, height: int = 1) -> bytes:
    """Build a valid RGBA PNG without any third-party library."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + b"\x00\x00\x00\x00" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


class NullDriver(SimulatorDriver):
    """A driver that performs no I/O against a simulator."""

    name: ClassVar[str] = "null"
    supports_events: ClassVar[bool] = False
    supports_screenshots: ClassVar[bool] = False
    #: A dry run captures no real screen, so there is nothing to write.
    supports_golden_capture: ClassVar[bool] = False
    golden_capture_blocker: ClassVar[str] = (
        "a dry run uses the null driver, which captures no real screen, so "
        "there is nothing to write as a golden. Capture it on a Windows host "
        "in the Connect IQ simulator instead; see tests/docs/visual-truth.md."
    )

    def __init__(self, log=None) -> None:  # type: ignore[no-untyped-def]
        self._actions: List[str] = []
        self._prg: Path | None = None
        self._device: Device | None = None
        self._settings: AppSettings | None = None
        self._log = log or (lambda message: None)

    @property
    def actions(self) -> Sequence[str]:
        return tuple(self._actions)

    def _record(self, action: str) -> None:
        self._actions.append(action)
        self._log(f"[dry-run] {action}")

    def prepare(self, prg: Path) -> None:
        self._prg = Path(prg)
        self._record(f"prepare {self._prg}")

    def start(
        self,
        device: Device,
        settings: AppSettings,
        mode: str = "ble",
        language: str = "eng",
    ) -> None:
        self._device = device
        self._settings = settings
        self._record(
            f"start device={device.id} mode={mode} language={language} settings={settings.name}"
        )

    def wait_ready(self, timeout_s: float) -> None:
        self._record(f"wait_ready timeout_s={timeout_s}")

    def stop(self) -> None:
        self._record("stop")

    def press(self, button: Button, count: int = 1) -> None:
        self._record(f"press {button.value} x{count}")

    def tap(self, x: int, y: int, count: int = 1) -> None:
        self._record(f"tap {x},{y} x{count}")

    def screenshot(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes())
        self._record(f"screenshot {path}")
        return path

    def events(self) -> Sequence[str]:
        return ()
