"""Probing the simulator's own menu bar.

The Connect IQ simulator's window has a **menu bar drawn inside the window**
(`File`, `Settings`, `Simulation`, `Data Fields`, `adb Connection`, `Help`). An
earlier claim that it had none was wrong: the accelerators (`alt+f`, `alt+s`)
prove only that the shortcuts do nothing, not that the menus are unreachable.

That matters because the menu bar is where connection mode and night mode can be
changed **while the app runs**, which is the difference between "the app starts in
Wi-Fi" and "the app transitions to Wi-Fi" — only the second exercises the
connection-mode indicator and the Wi-Fi sync path.

The labels' positions are not known ahead of time (they depend on the device and
font metrics), so probing walks along the menu bar row and reports what each
point did. Everything here is a pure function of the captured window size, the
`xwininfo` output and the measured pixel difference, so it is testable without
Wine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Sequence, Set, Tuple

#: Height of the menu bar row in the simulator window, in pixels. The row is the
#: first strip of the window and its text sits roughly in the middle of it.
MENU_BAR_HEIGHT = 22

#: Horizontal step when walking the menu bar looking for a clickable label.
MENU_PROBE_STEP = 24


def menu_bar_points(
    width: int, height: int, step: int = MENU_PROBE_STEP
) -> List[Tuple[int, int]]:
    """Candidate click points along the menu bar, left to right.

    ``y`` is the middle of the bar, ``x`` walks from the first label towards the
    right edge. A click in the window's own title area is avoided by starting a
    little inside the left edge.
    """
    bar = min(MENU_BAR_HEIGHT, max(1, height))
    y = max(1, bar // 2)
    if width <= 0 or height <= 0:
        return []
    start = max(6, step // 2)
    points: List[Tuple[int, int]] = []
    x = start
    while x < width - 4:
        points.append((x, y))
        x += max(4, step)
    return points


def parse_window_ids(text: str) -> Set[int]:
    """Every window id in ``xwininfo -root -children`` output, as integers."""
    ids: Set[int] = set()
    for match in re.finditer(r"^\s*(0x[0-9a-fA-F]+)\s", text or "", re.MULTILINE):
        try:
            ids.add(int(match.group(1), 16))
        except ValueError:
            continue
    return ids


def new_window_ids(before: Sequence[int], after: Sequence[int]) -> List[int]:
    """Window ids present in ``after`` but not in ``before``."""
    return sorted(set(after) - set(before))


@dataclass
class MenuProbeStep:
    """What one candidate point did."""

    x: int
    y: int
    screen_delta: int = 0
    new_windows: Tuple[int, ...] = ()
    note: str = ""

    @property
    def opened_something(self) -> bool:
        return bool(self.new_windows) or self.screen_delta > 0

    def describe(self) -> str:
        detail = f"({self.x},{self.y}) delta={self.screen_delta}px"
        if self.new_windows:
            detail += " new windows=" + ",".join(str(w) for w in self.new_windows)
        if self.note:
            detail += f" {self.note}"
        return detail


@dataclass
class MenuProbeReport:
    """The outcome of walking the menu bar once."""

    steps: List[MenuProbeStep] = field(default_factory=list)
    popup_window: int = 0
    popup_geometry: str = ""
    screenshot: str = ""

    @property
    def opened(self) -> bool:
        return self.popup_window != 0

    def verdict(self) -> str:
        if self.opened:
            return (
                f"a click on the menu bar opened a popup (window "
                f"{self.popup_window} {self.popup_geometry}). The simulator's "
                "menus are reachable with synthetic clicks; driving them is the "
                "next step."
            )
        if any(step.opened_something for step in self.steps):
            return (
                "the menu bar row responded to a click (screen changed) but no "
                "popup window appeared. Menu rendering may be in-window and "
                "invisible to a window check; inspect the screenshots before "
                "concluding anything."
            )
        return (
            "no click on the menu bar row changed anything and no popup window "
            "appeared. Under this display, the simulator does not take synthetic "
            "clicks on its menu bar; restart-in-mode remains the documented "
            "capability."
        )

    def lines(self) -> List[str]:
        return [step.describe() for step in self.steps]
