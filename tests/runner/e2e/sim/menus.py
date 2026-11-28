"""The simulator's own menu bar, and the connection-mode plan that drives it.

The Connect IQ simulator **has** a menu bar: `File`, `Settings`, `Simulation`,
`Data Fields`, `adb Connection`, `Help`. Two earlier claims in this repository
were wrong and are retired here:

* "the simulator has no menu bar" — there was no *X window* for the menus under
  Wine, which is not the same thing. Measured on the Windows SDK 9.3.0 build
  (2026-11-28) with the Win32 API, `GetMenu()` returns a real handle, the
  top-level labels are the list above, and `Settings -> Connection Type` holds
  `BLE`, `WiFi` and `LTE`, each a submenu of `Not Initialized`,
  `Not Connected` and `Connected` with stable command ids (BLE 6051-6053,
  WiFi 6054-6056, LTE 6057-6059). Selecting an item changes the menu's check
  state, so a selection takes effect in the simulator.
* "synthetic clicks on the menu bar are proven not to work" — a click on
  `Settings` opens the menu and a hover opens `Connection Type`; the probe
  simply had no X window to look for.

Where the two builds differ is where the popup is drawn, and that difference is
what made the driver decision worth re-opening:

* Win32 (`simulator.exe`, what the `wine` driver runs): each popup is a
  separate `#32768` window on Windows (measured: the BLE submenu is 121x72) but
  **under Wine no new X window appears** — Wine draws the popup inside the
  simulator window. A driver therefore cannot read the popup geometry through
  X, and menu automation there has to find labels and rows by pixel probing.
* Native GTK (`linux/bin/simulator`): GTK menus are ordinary X windows, so the
  popup is visible to `xwininfo` and the whole interface is mnemonics plus
  arrow keys. No coordinates are needed at all.

That is why the connection-mode capability belongs to the `native` driver, and
why `WineSimulatorDriver.probe_menus()` stays diagnostic: it can report what a
point did, not select an item deterministically.

The plan functions below are pure: they encode the measured menu model as
labels and indices, so they can be unit-tested without any simulator. The click
probe helpers remain for the diagnostic sweep.
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

#: The measured menu bar, left to right. The `&` marks the mnemonic in the
#: Win32 source; wxWidgets/GTK use the same letters, which is what the key plan
#: below relies on.
MENU_BAR_LABELS: Tuple[str, ...] = (
    "&File",
    "&Settings",
    "Si&mulation",
    "&Data Fields",
    "&adb Connection",
    "&Help",
)

#: The top-level menu every measured connection path starts from.
SETTINGS_MENU_LABEL = "&Settings"

#: Index of `Settings -> Connection Type` in the Settings menu (measured).
SETTINGS_CONNECTION_TYPE_INDEX = 3

#: Connection types, in menu order (measured).
CONNECTION_TYPES: Tuple[str, ...] = ("BLE", "WiFi", "LTE")

#: The state submenu every connection type has, in menu order (measured). `LTE`
#: and `WiFi` carry extra entries after these, which is why the index is
#: looked up in this tuple instead of being assumed.
CONNECTION_STATES: Tuple[str, ...] = ("Not Initialized", "Not Connected", "Connected")

#: Command ids of the state items, measured on the Windows build. They are not
#: used to activate an item (there is no Win32 command channel through Wine);
#: they are recorded so a log line can be matched against a menu dump.
CONNECTION_MENU_IDS: Tuple[Tuple[str, str, int], ...] = (
    ("BLE", "Not Initialized", 6051),
    ("BLE", "Not Connected", 6052),
    ("BLE", "Connected", 6053),
    ("WiFi", "Not Initialized", 6054),
    ("WiFi", "Not Connected", 6055),
    ("WiFi", "Connected", 6056),
    ("LTE", "Not Initialized", 6057),
    ("LTE", "Not Connected", 6058),
    ("LTE", "Connected", 6059),
)

#: The end state each app mode needs, in the order it is applied. The values
#: mirror ``wine.MODE_INI_VALUES`` so a mid-run transition reaches the same
#: simulator state as starting in that mode:
#:
#: * ``ble``: the phone is connected; Wi-Fi is not.
#: * ``wifi``: the phone is gone and Wi-Fi is connected.
#: * ``offline``: neither is available. Wi-Fi is *not initialised* rather than
#:   merely disconnected, because ``hasWifiCapability()`` is false then and the
#:   app goes straight to OFFLINE instead of waiting for a check.
#:
#: Wi-Fi is applied before BLE: with the reverse order the app can observe the
#: phone leaving while Wi-Fi is still in its previous state and pass through
#: OFFLINE for one poll, which makes the trace depend on timing.
CONNECTION_END_STATES: Tuple[Tuple[str, Tuple[Tuple[str, str], ...]], ...] = (
    ("ble", (("WiFi", "Not Connected"), ("BLE", "Connected"))),
    ("wifi", (("WiFi", "Connected"), ("BLE", "Not Connected"))),
    ("offline", (("WiFi", "Not Initialized"), ("BLE", "Not Connected"))),
)


@dataclass(frozen=True)
class MenuSelection:
    """One item to activate, named the way the menu shows it."""

    path: Tuple[str, ...]

    def describe(self) -> str:
        return " -> ".join(self.path)


def menu_mnemonic(label: str) -> str:
    """The lower-case mnemonic letter of a menu label, e.g. ``&Settings`` -> ``s``."""
    index = label.find("&")
    if index < 0 or index + 1 >= len(label) or not label[index + 1].isalnum():
        raise ValueError(f"menu label {label!r} declares no mnemonic")
    return label[index + 1].lower()


def menu_item_index(path: Sequence[str]) -> int:
    """The measured index of ``path`` inside its parent menu.

    Only the paths the connection plan uses are known: the top-level bar, the
    Settings menu's ``Connection Type`` entry, and the connection type and state
    submenus. Anything else raises instead of guessing, because a wrong index
    selects a different menu item silently.
    """
    parts = tuple(path)
    if len(parts) == 2:
        if parts == ("Settings", "Connection Type"):
            return SETTINGS_CONNECTION_TYPE_INDEX
    elif len(parts) == 3:
        if parts[0:2] == ("Settings", "Connection Type") and parts[2] in CONNECTION_TYPES:
            return CONNECTION_TYPES.index(parts[2])
    elif len(parts) == 4:
        if (
            parts[0:2] == ("Settings", "Connection Type")
            and parts[2] in CONNECTION_TYPES
            and parts[3] in CONNECTION_STATES
        ):
            return CONNECTION_STATES.index(parts[3])
    raise ValueError(f"no measured menu index for path {parts!r}")


def connection_menu_selections(mode: str) -> Tuple[MenuSelection, ...]:
    """The menu items that put the simulator into app mode ``mode``.

    Two selections are returned: the Wi-Fi state first, then the BLE state. The
    caller applies them in order; the app proves the result through
    ``E2E:CONNECTION``, never through the click.
    """
    for name, states in CONNECTION_END_STATES:
        if name == mode:
            return tuple(
                MenuSelection(("Settings", "Connection Type", connection_type, state))
                for connection_type, state in states
            )
    raise ValueError(f"no connection plan for mode {mode!r}")


def menu_key_plan(selection: MenuSelection) -> Tuple[str, ...]:
    """The keys that open and activate ``selection`` from a focused window.

    The plan is mnemonic + arrows, with no coordinates:

    * the top-level mnemonic opens the menu with its first item highlighted;
    * ``Down`` × index moves to the wanted item;
    * ``Right`` opens a submenu and highlights its first item;
    * ``Return`` activates the item in the last menu.

    This is the ordinary GTK/Win32 menu idiom, so it does not depend on the
    window size, the font metrics or the popup position. Under Wine it is only
    a candidate (the guest recorded ``alt+f``/``alt+s`` doing nothing), which is
    exactly why the native driver owns the capability.
    """
    if not selection.path or selection.path[0] != "Settings":
        raise ValueError(
            f"the measured key plan starts at the Settings menu; got {selection.path!r}"
        )
    keys: List[str] = [f"alt+{menu_mnemonic(SETTINGS_MENU_LABEL)}"]
    for depth, _label in enumerate(selection.path[1:], start=1):
        keys.extend(["Down"] * menu_item_index(selection.path[: depth + 1]))
        if depth < len(selection.path) - 1:
            keys.append("Right")
    keys.append("Return")
    return tuple(keys)


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
            "appeared. The menu bar exists (a real Win32 menu, measured on the "
            "Windows build); under Wine its popups are drawn inside the window, "
            "so an empty report is inconclusive, not proof that the menus are "
            "absent. Use the native driver, whose popups are X windows, to drive "
            "a menu item."
        )

    def lines(self) -> List[str]:
        return [step.describe() for step in self.steps]
