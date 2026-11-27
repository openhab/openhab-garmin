"""Wine simulator driver.

How the pieces fit
==================

The Connect IQ simulator is a Windows GUI application with no automation API
(see the investigation recorded in issue #140). This driver runs it under Wine
on an X display and drives it from the outside:

1. ``prepare``  -- copy the PRG somewhere the launch will read it, and fail
   early when Wine, ``monkeydo``, ``xdotool`` or ImageMagick are missing.
1. ``start``    -- check the display, write the startup keys the simulator
   reads (device, communication state, language), start ``simulator.exe``,
   wait until its window title *names the device* (a generic title means the
   device did not load), seed the app properties, then launch the PRG with
   ``monkeydo`` and capture its stdout as the event stream.
1. ``wait_ready`` -- wait for the first ``E2E:VIEW``/``E2E:LANGUAGE`` pair and
   verify the language that is actually active, not the one that was written.
1. ``press``/``tap`` -- ``xdotool`` against the simulator window, focused first.
   Button bindings are read from the device definition (``simulator.json``)
   when present and fall back to the simulator defaults.
1. ``screenshot`` -- ImageMagick ``import`` against the simulator window, never
   against the root window (a window-less root export is a single colour).
1. ``stop``     -- terminate the app and the simulator by PID, leaving the X
   display alone so the next ``start`` can reuse it.

Ground truth this file encodes (verified on the Linux guest):

- the runner stages the SDK at ``/tmp/ciq-sdk`` and links device definitions
  into both ``$HOME/.Garmin/ConnectIQ/Devices`` and the Wine prefix; without
  the Wine-prefix link the simulator opens an ``Error`` dialog and its title
  stays generic;
- the simulator listens on TCP ``127.0.0.1:1234`` and ``monkeydo <prg>
  <device>`` streams ``System.println`` output to stdout, which is where the
  ``E2E:`` lines arrive;
- ``pkill -f <pattern>`` inside ``docker exec`` matches the exec's own command
  line, so this driver never uses it: it keeps both PIDs and terminates them.

Anything this driver cannot do reliably is documented in
``tests/docs/simulator-driver.md`` and raises ``DriverError`` with the next
concrete step instead of pretending the step happened.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

from ..config import Device
from ..languages import (
    DEFAULT_LANGUAGE,
    LANGUAGE_INDEX_FILE,
    language_index,
    load_language_index,
)
from ..sdk import (
    SdkLayout,
    find_device_definitions,
    require_simulator,
    wine_prefix,
)
from ..settings import AppSettings
from ..trace import TraceEvent, parse_lines
from .base import Button, DriverError, SimulatorDriver
from .menus import (
    MENU_PROBE_STEP,
    MenuProbeReport,
    MenuProbeStep,
    menu_bar_points,
    new_window_ids,
    parse_window_ids,
)

#: Environment variable that may point at an already installed Wine binary.
WINE_ENV = "E2E_WINE_BINARY"
#: Display the simulator renders on; the entrypoint starts Xvfb here.
DEFAULT_DISPLAY = ":99"
#: Explicit path of an application-properties file to write before launch. The
#: harness compiles properties into the PRG (a ``settings.xml`` resource
#: override), so this is only an escape hatch for a hand-made file; the default
#: path needs no file at all.
ENV_SETTINGS_FILE = "E2E_WINE_SETTINGS_FILE"
#: ``x,y,width,height`` of the watch screen inside the simulator window. The
#: default is a centred, aspect-preserving fit; pin this when taps miss.
ENV_SCREEN_RECT = "E2E_WINE_SCREEN_RECT"
#: Where :meth:`WineSimulatorDriver.screenshot` parks the pointer before a
#: capture. The simulator's status strip prints the pointer position, so a
#: capture would otherwise depend on where the last press left the mouse.
POINTER_PARK_POINT = (2, 2)
#: Prefix for per-button X keysym overrides, e.g. ``E2E_WINE_KEY_MENU=ctrl+m``.
ENV_KEY_PREFIX = "E2E_WINE_KEY_"
#: Startup and readiness budgets in seconds.
ENV_STARTUP_TIMEOUT = "E2E_WINE_STARTUP_TIMEOUT"
DEFAULT_STARTUP_TIMEOUT = 120.0
DEFAULT_READY_TIMEOUT = 300.0
#: Delay between repeated xdotool events, so the app can process each one.
DEFAULT_KEY_DELAY_MS = 150
#: How long a declared ``isHold`` key stays pressed.
HOLD_MS = 900
#: Pause after focusing and before the key, as in the verified manual command
#: (`xdotool windowfocus $W; sleep 0.3; xdotool key Down`).
FOCUS_SETTLE_S = 0.3
#: How long a key stays down in the ``keyboard-hold`` mechanism. A wxWidgets app
#: that polls key state (`GetAsyncKeyState`) sees a held key and not an
#: instantaneous tap, so the two mechanisms are both tried.
KEY_HOLD_S = 0.05
#: How long the primary button stays down in the ``mouse-hold`` mechanism.
MOUSE_HOLD_MS = 150
#: Gap between consecutive launch keys, as in the verified manual command
#: (`sleep 2` between keys). The keys do not register when they arrive faster.
LAUNCH_KEY_GAP_S = 2.0
#: Page-downs before select, as measured on the guest (2/2 runs with them).
LAUNCH_DOWNS = 2
#: Override for a device whose app entry is a different number of pages away.
ENV_LAUNCH_DOWNS = "E2E_WINE_LAUNCH_DOWNS"
#: Seconds the app must have been running before the first launch key. Waiting
#: for the first `E2E:VIEW` is not the same as waiting until the app accepts
#: input: the guest's verified command waits **50 s** after `monkeydo`, and a
#: launch at 41.7 s failed every attempt while the screen was already stable.
LAUNCH_DWELL_S = 50.0
#: Override for the dwell.
ENV_LAUNCH_DWELL = "E2E_WINE_LAUNCH_DWELL"
#: Upper bound on the settle (dwell + stability), measured from app start.
LAUNCH_SETTLE_MAX_S = 90.0
#: Override for the maximum settle. Replaces the old fixed 2 s wait.
ENV_LAUNCH_SETTLE = "E2E_WINE_LAUNCH_SETTLE"
#: Seconds between the two captures that must match before the sequence is sent.
LAUNCH_STABLE_INTERVAL_S = 1.0
#: How long one launch retry waits for `E2E:CONNECTION` or a new view.
LAUNCH_RESPONSE_WAIT_S = 4.0
#: Seconds between launch retries. The guest's successful runs took anywhere
#: between one and several minutes after launch, and the same command alternated
#: between working and doing nothing, so retrying is the fix, not tuning.
LAUNCH_RETRY_INTERVAL_S = 10.0
#: Total time spent retrying the launch sequence, after the settle.
LAUNCH_RETRY_BUDGET_S = 180.0
ENV_LAUNCH_RETRY_INTERVAL = "E2E_WINE_LAUNCH_RETRY_INTERVAL"
ENV_LAUNCH_RETRY_BUDGET = "E2E_WINE_LAUNCH_RETRY_BUDGET"
#: Total budget for :meth:`WineSimulatorDriver.launch`: the settle (up to
#: ``LAUNCH_SETTLE_MAX_S``) plus the retry budget.
DEFAULT_LAUNCH_TIMEOUT = 260.0
#: Accelerators for the simulator's own menus, kept for diagnostics only: the
#: guest's simulator has no menu bar, so the driver does not rely on one.
INPUT_PROBE_KEYS: Tuple[str, ...] = ("alt+s", "alt+c")
#: Window pixels that must change for a press to count as having had an effect.
#: A glance clock ticking changes a few hundred; an opened menu or a new view
#: changes thousands.
PIXEL_CHANGE_THRESHOLD = 2000

#: Keyboard keysyms the guest has proven this simulator honours. The select
#: path (declared hit areas) works as well; the keyboard is preferred for
#: navigation because it needs no coordinates.
PROVEN_KEYSYMS: Mapping[Button, str] = {
    Button.UP: "Up",
    Button.DOWN: "Down",
}
#: TCP transport ``monkeydo`` connects to; the simulator listens on it after
#: start. Waiting for it avoids racing the push against the simulator's start-up.
SIMULATOR_TCP_HOST = "127.0.0.1"
SIMULATOR_TCP_PORT = 1234
SIMULATOR_TRANSPORT_TIMEOUT = 15.0

#: ``CIQ Simulator - <device marketing name> (<version>)``. The version group is
#: optional because a device that fails to load keeps the bare title, and some
#: simulator builds omit the version.
_TITLE = re.compile(
    r"^CIQ\s*Simulator\s*-\s*(?P<device>.+?)(?:\s*\((?P<version>[^()]*)\))?\s*$",
    re.IGNORECASE,
)
_ERROR_WORD = re.compile(r"\berror\b", re.IGNORECASE)

#: Default simulator key bindings, used when the device definition does not
#: provide ``simulator.json``. ``:start``/``:menu`` are the simulator's own
#: accelerators; every entry can be overridden with ``E2E_WINE_KEY_<BUTTON>``.
DEFAULT_KEYS: Mapping[Button, str] = {
    Button.START: "ctrl+s",
    Button.UP: "Up",
    Button.DOWN: "Down",
    Button.ENTER: "Return",
    Button.BACK: "Escape",
    Button.MENU: "ctrl+m",
}

#: Action names ``simulator.json`` may use for each semantic button.
_SIMULATOR_KEY_ACTIONS: Mapping[str, Button] = {
    "up": Button.UP,
    "down": Button.DOWN,
    "enter": Button.ENTER,
    "select": Button.ENTER,
    "back": Button.BACK,
    "esc": Button.BACK,
    "menu": Button.MENU,
    "start": Button.START,
}

#: Windows virtual-key codes that appear in ``simulator.json`` -> X keysyms.
_VK_KEYSYMS: Mapping[int, str] = {
    0x08: "BackSpace",
    0x09: "Tab",
    0x0D: "Return",
    0x1B: "Escape",
    0x20: "space",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
}

#: Startup keys the simulator reads from ``simulator.ini``. The simulation
#: state keys come from the #140 investigation; the driver writes them and
#: records exactly what was written, but never claims a runtime mode change.
#:
#: The simulator stores these states as numbers, not booleans: the operator's
#: Windows simulator writes ``BleState=2`` for a connected phone, and the other
#: values follow ``System.ConnectionState`` (0 = not initialised, 1 = not
#: connected, 2 = connected). ``offline`` uses 0 for Wi-Fi on purpose: the app's
#: ``hasWifiCapability()`` is false then, so it goes straight to ``OFFLINE``
#: instead of waiting for a Wi-Fi check that has nothing to talk to.
MODE_INI_VALUES: Mapping[str, Mapping[str, str]] = {
    "ble": {"BleState": "2", "WifiState": "1"},
    "wifi": {"BleState": "1", "WifiState": "2"},
    "offline": {"BleState": "1", "WifiState": "0"},
}

#: The simulator enforces "secure connection required" for web requests unless
#: this is off, and the E2E stack is plain HTTP. The app's own documentation
#: calls a ``-1001`` response "secure connection required", and a working
#: installation carries this key, so writing it is what turns a served 200 into
#: a 200 the app can read instead of a ``-1001``.
HTTPS_REQUIREMENTS_KEY = "UseHttpsRequirements"
HTTPS_REQUIREMENTS_OFF = "0"

#: Night mode is also a ``simulator.ini`` key, numeric like the rest: ``1`` on,
#: ``0`` off. It only takes effect at start, so the driver always writes it and
#: documents the restart as the way to change it (see ``restart_in_mode``).
NIGHT_MODE_KEY = "nightMode"
ENV_NIGHT_MODE = "E2E_WINE_NIGHT_MODE"

#: The app starts in BLE and only leaves it after a request fails
#: (``ConnectionManager``), so a requested mode is consistent with these runtime
#: modes. The driver reports them; it does not assert them.
MODE_CONNECTION_NAMES: Mapping[str, Tuple[str, ...]] = {
    "ble": ("BLE",),
    "wifi": ("WIFI", "WIFI_CHECK_PENDING"),
    "offline": ("OFFLINE",),
}

#: Where the simulator keeps its own runtime log. It is the only place an app
#: runtime error (a Monkey C stack, a missing symbol, a bad font) appears: the
#: `E2E:` stream only carries what the app chooses to print.
CIQ_LOG_HINT = "drive_c/users/{user}/Temp/com.garmin.connectiq/GARMIN/APPS/LOGS/CIQ_LOG.YML"

#: The app's own persistent storage, relative to the Wine prefix. The app caches
#: the last sitemap JSON here (`SitemapStore`: ``sitemapJson``/``sitemapLabel``),
#: and that cache outlives a scenario: without clearing it, the next scenario
#: starts on the previous scenario's sitemap, reports
#: ``E2E:SITEMAP;result=unchanged`` and never requests its own. `LOGS` is
#: deliberately not touched, because ``CIQ_LOG.YML`` is the crash evidence.
APP_DATA_HINTS: Tuple[str, ...] = (
    "drive_c/users/{user}/Temp/com.garmin.connectiq/GARMIN/APPS/DATA",
    "drive_c/users/{user}/Temp/com.garmin.connectiq/GARMIN/APPS/SETTINGS",
    "drive_c/users/{user}/Temp/com.garmin.connectiq/GARMIN/Settings",
)

#: Set to keep the app's storage across launches, for debugging the cache itself.
ENV_KEEP_APP_DATA = "E2E_WINE_KEEP_APP_DATA"

#: Runtime errors this suite meets, and what each one means. The first two are
#: false defect classes: a device/PRG mismatch and a device-definition gap.
CIQ_ERROR_CLASSES: Tuple[Tuple[str, str, str], ...] = (
    (
        "symbol-not-found",
        "Symbol Not Found",
        "build/device mismatch: the PRG was compiled for another device, or its "
        "annotations do not match this device. Rebuild with `e2e build --device <id>` "
        "and launch that build; do not treat it as an app defect.",
    ),
    (
        "invalid-font",
        "Invalid Font Specified",
        "device definition: this device's definition does not provide the font. "
        "Not an app defect until a second device shows it too.",
    ),
    (
        "out-of-memory",
        "Out Of Memory",
        "app or size limit: compare the device's memory limit with the sitemap size.",
    ),
)

#: Candidate ``simulator.ini`` locations, relative to the Wine prefix. The
#: simulator keeps the file at the ``ConnectIQ`` root, not in a ``Simulator``
#: subdirectory: on the guest the real file is
#: ``ConnectIQ/simulator.ini`` with flat ``key=value`` lines and no section
#: header. A stale ``ConnectIQ/Simulator/simulator.ini`` is deliberately not a
#: candidate, so an earlier run cannot leave the driver writing a file the
#: simulator never reads.
INI_HINTS: Tuple[str, ...] = (
    "drive_c/users/{user}/AppData/Roaming/Garmin/ConnectIQ/simulator.ini",
    "drive_c/users/{user}/AppData/Local/Garmin/ConnectIQ/simulator.ini",
    "drive_c/users/{user}/Temp/com.garmin.connectiq/simulator.ini",
)


@dataclass(frozen=True)
class PressResult:
    """What one ``press`` did, and whether it changed anything.

    ``mechanism`` is ``keyboard``, ``mouse`` or ``keyboard (override)``;
    ``detail`` names the keysym or the window point. ``effect`` is filled in by
    :meth:`WineSimulatorDriver.press_measured` and is empty when the press
    produced no observable change, so a no-op can never be reported as success.
    """

    button: str
    mechanism: str
    detail: str
    effect: str = ""
    screen_delta: Optional[int] = None
    views: Tuple[str, ...] = ()
    requests: Optional[int] = None

    def describe(self) -> str:
        parts = [f"mechanism={self.mechanism} ({self.detail})"]
        parts.append(f"effect={self.effect or 'none'}")
        if self.screen_delta is not None:
            parts.append(f"screen={self.screen_delta}px")
        if self.views:
            parts.append("views=" + ",".join(self.views))
        if self.requests is not None:
            parts.append(f"requests +{self.requests}")
        return "; ".join(parts)


# ---------------------------------------------------------------------------
# pure helpers: window titles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowTitle:
    """A parsed simulator window title."""

    raw: str
    device: str = ""
    version: str = ""
    error: bool = False

    @property
    def loaded(self) -> bool:
        """True when the title names a device, i.e. the device did load."""
        return bool(self.device) and not self.error

    def describe(self) -> str:
        if self.error:
            return f"Error dialog ({self.raw!r})"
        if self.loaded:
            return f"device {self.device!r} (simulator {self.version or 'unknown'})"
        return f"generic title {self.raw!r}"


def parse_window_title(title: str) -> WindowTitle:
    """Parse ``CIQ Simulator - <device> (<version>)``.

    A title without a device segment ("CIQ Simulator"), or any title that
    mentions an error, means the device did not load. Non-simulator titles are
    reported as generic so a caller can distinguish "not this window" from
    "this window, no device".
    """
    text = (title or "").strip()
    if not text:
        return WindowTitle(raw="")
    if _ERROR_WORD.search(text):
        return WindowTitle(raw=text, error=True)
    match = _TITLE.match(text)
    if match is None:
        return WindowTitle(raw=text)
    device = (match.group("device") or "").strip()
    version = (match.group("version") or "").strip()
    return WindowTitle(raw=text, device=device, version=version)


def _normalise(text: str) -> str:
    """Lower-case ASCII-folded alphanumerics, so "fēnix 7" == "fenix7"."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in folded if not unicodedata.combining(char))
    return "".join(char for char in stripped.lower() if char.isalnum())


def title_matches_device(
    device_name: str,
    product: str,
    display_name: str = "",
) -> Optional[bool]:
    """Does ``device_name`` from the window title name ``product``?

    Returns ``None`` when there is nothing to compare against, so the caller can
    warn instead of failing a run over a missing device definition. Comparison
    is on folded alphanumerics and accepts either direction of containment,
    because the title may abbreviate a marketing name and vice versa.

    ``display_name`` is the device definition's ``displayName`` (for example
    "Forerunner® 965"): its segments are the names the simulator is likely to
    print, and the product id alone would not match them.
    """
    observed = _normalise(device_name)
    if not observed:
        return False
    candidates: List[str] = [_normalise(product)]
    if display_name:
        for segment in re.split(r"[/|,()]", display_name):
            candidates.append(_normalise(segment))
    candidates = [candidate for candidate in candidates if candidate]
    if not candidates:
        return None
    for candidate in candidates:
        if candidate == observed or candidate in observed:
            return True
        if len(observed) >= 4 and observed in candidate:
            return True
    return False


def read_device_display_name(devices_root: Optional[Path], product: str) -> str:
    """``displayName`` from ``<devices_root>/<product>/compiler.json``, or ``""``."""
    if devices_root is None:
        return ""
    path = Path(devices_root) / product / "compiler.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(document, Mapping):
        return ""
    name = document.get("displayName")
    return str(name).strip() if isinstance(name, str) else ""


def read_device_runtime_version(devices_root: Optional[Path], product: str) -> str:
    """``connectIQVersion`` from ``<devices_root>/<product>/compiler.json``.

    This is the Connect IQ runtime the *device definition emulates* (fenix7
    5.2.0, fenix847mm 6.0.3), not the simulator's build version. The window title
    prints this number, which is why it is easy to mistake for the simulator
    version.
    """
    if devices_root is None:
        return ""
    path = Path(devices_root) / product / "compiler.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(document, Mapping):
        return ""
    version = document.get("connectIQVersion")
    return str(version).strip() if isinstance(version, str) else ""


# ---------------------------------------------------------------------------
# pure helpers: process command lines
# ---------------------------------------------------------------------------


def simulator_command(wine: str, simulator: Path) -> List[str]:
    """Command that starts the simulator under Wine."""
    return [wine, str(simulator)]


def monkeydo_command(
    monkeydo: Path,
    prg: Path,
    product: str,
    *,
    stdbuf: Optional[str] = None,
) -> List[str]:
    """Command that launches ``prg`` on ``product`` and streams its console.

    ``monkeydo`` buffers stdout when it is not a terminal, which would delay
    every ``E2E:`` line until exit; ``stdbuf`` turns that off when available.
    """
    command = [str(monkeydo), str(prg), product]
    if stdbuf:
        command = [stdbuf, "-oL", "-eL"] + command
    return command


def xdotool_search_command() -> List[str]:
    return ["xdotool", "search", "--onlyvisible", "--name", "CIQ Simulator"]


def xdotool_error_search_command() -> List[str]:
    return ["xdotool", "search", "--onlyvisible", "--name", "^Error$"]


def xdotool_name_command(window: str) -> List[str]:
    return ["xdotool", "getwindowname", window]


def xdotool_geometry_command(window: str) -> List[str]:
    return ["xdotool", "getwindowgeometry", "--shell", window]


def xdotool_focus_commands(window: str) -> List[List[str]]:
    """Commands that make the window the input target, strongest first."""
    return [
        ["xdotool", "windowactivate", "--sync", window],
        ["xdotool", "windowfocus", "--sync", window],
    ]


def xdotool_key_command(
    keys: str,
    count: int = 1,
    delay_ms: int = DEFAULT_KEY_DELAY_MS,
) -> List[str]:
    """Type a key as real X input (XTEST) to the focused window.

    ``xdotool key --window <id>`` uses ``XSendEvent``, which many toolkits
    (wxWidgets among them) ignore; the focused-window path generates genuine
    key events. The window-targeted form is kept as a fallback.
    """
    return [
        "xdotool",
        "key",
        "--clearmodifiers",
        "--repeat",
        str(max(1, int(count))),
        "--delay",
        str(max(0, int(delay_ms))),
        keys,
    ]


def xdotool_keydown_command(keysym: str) -> List[str]:
    """Press a key and keep it down (a state change, not a tap)."""
    return ["xdotool", "keydown", "--clearmodifiers", keysym]


def xdotool_keyup_command(keysym: str) -> List[str]:
    """Release a key pressed with :func:`xdotool_keydown_command`."""
    return ["xdotool", "keyup", "--clearmodifiers", keysym]


def xdotool_mousedown_to_window_command(window: str, x: int, y: int) -> List[str]:
    return [
        "xdotool",
        "mousemove",
        "--window",
        window,
        str(int(x)),
        str(int(y)),
        "mousedown",
        "1",
    ]


def xdotool_key_to_window_command(
    window: str,
    keys: str,
    count: int = 1,
    delay_ms: int = DEFAULT_KEY_DELAY_MS,
) -> List[str]:
    return [
        "xdotool",
        "key",
        "--window",
        window,
        "--clearmodifiers",
        "--repeat",
        str(max(1, int(count))),
        "--delay",
        str(max(0, int(delay_ms))),
        keys,
    ]


def xdotool_mousemove_command(x: int, y: int) -> List[str]:
    return ["xdotool", "mousemove", str(int(x)), str(int(y))]


def xdotool_mousemove_window_command(window: str, x: int, y: int) -> List[str]:
    """Move the pointer to a point relative to ``window``."""
    return [
        "xdotool",
        "mousemove",
        "--window",
        str(window),
        str(int(x)),
        str(int(y)),
    ]


def xdotool_click_only_command(
    count: int = 1, delay_ms: int = DEFAULT_KEY_DELAY_MS
) -> List[str]:
    return [
        "xdotool",
        "click",
        "--repeat",
        str(max(1, int(count))),
        "--delay",
        str(max(0, int(delay_ms))),
        "1",
    ]


def xdotool_mousedown_command() -> List[str]:
    """Press the primary button at the current pointer (see mousemove)."""
    return ["xdotool", "mousedown", "1"]


def xdotool_mouseup_command() -> List[str]:
    return ["xdotool", "mouseup", "1"]


def xdotool_mouselocation_command() -> List[str]:
    return ["xdotool", "getmouselocation", "--shell"]


def xdotool_window_pid_command(window: str) -> List[str]:
    return ["xdotool", "getwindowpid", window]


def xwininfo_root_command() -> List[str]:
    return ["xwininfo", "-root"]


def xwininfo_children_command() -> List[str]:
    """Child windows of the root, for popup detection."""
    return ["xwininfo", "-root", "-children"]


def xdotool_getwindowfocus_command() -> List[str]:
    return ["xdotool", "getwindowfocus"]


def parse_mouselocation(text: str) -> Optional[Dict[str, int]]:
    """Parse ``xdotool getmouselocation --shell`` output."""
    values: Dict[str, int] = {}
    for line in (text or "").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if value.lstrip("-").isdigit():
            values[key.strip().lower()] = int(value)
    if {"x", "y", "window"} <= set(values):
        return values
    return None


def parse_root_window_id(text: str) -> Optional[str]:
    """The root window id from ``xwininfo -root``, as a decimal string."""
    match = re.search(r'Window id:\s*(0x[0-9a-fA-F]+|[0-9]+)', text or "")
    if match is None:
        return None
    raw = match.group(1)
    try:
        return str(int(raw, 16)) if raw.lower().startswith("0x") else str(int(raw))
    except ValueError:
        return None


def compare_command(before: Path, after: Path) -> List[str]:
    """ImageMagick absolute pixel difference between two captures."""
    return ["compare", "-metric", "AE", str(before), str(after), "null:"]


def crop_command(source: Path, rect: ScreenRect, target: Path) -> List[str]:
    """Crop a capture to a declared rectangle, in window pixels."""
    return [
        "convert",
        str(source),
        "-crop",
        f"{rect.width}x{rect.height}+{rect.x}+{rect.y}",
        "+repage",
        str(target),
    ]


def import_command(display: str, window: str, path: Path) -> List[str]:
    """Capture one window. The root window is never captured: on a display with
    no window manager it is a single colour, so it proves nothing."""
    return [
        "import",
        "-display",
        display,
        "-window",
        window,
        "-silent",
        str(path),
    ]


def xdpyinfo_command(display: str) -> List[str]:
    return ["xdpyinfo", "-display", display]


def parse_window_geometry(text: str) -> Optional[Dict[str, int]]:
    """Parse ``xdotool getwindowgeometry --shell`` output."""
    values: Dict[str, int] = {}
    for line in (text or "").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if value.lstrip("-").isdigit():
            values[key.strip().lower()] = int(value)
    if {"x", "y", "width", "height"} <= set(values):
        return values
    return None


# ---------------------------------------------------------------------------
# pure helpers: input mapping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenRect:
    """The watch screen inside the simulator window, in window pixels."""

    x: int
    y: int
    width: int
    height: int


def parse_screen_rect(text: str) -> Optional[ScreenRect]:
    """Parse ``E2E_WINE_SCREEN_RECT=x,y,width,height``."""
    parts = [part.strip() for part in (text or "").split(",")]
    if len(parts) != 4 or not all(part.lstrip("-").isdigit() for part in parts):
        return None
    x, y, width, height = (int(part) for part in parts)
    if width <= 0 or height <= 0:
        return None
    return ScreenRect(x=x, y=y, width=width, height=height)


def screen_rect(
    geometry: Mapping[str, int],
    screen: Tuple[int, int],
) -> ScreenRect:
    """The screen rectangle when the simulator fits the screen in the window.

    The Connect IQ simulator scales the device graphic to the window and keeps
    the aspect ratio, so the screen is the centred, aspect-preserving fit of the
    device resolution inside the window. A pin from ``E2E_WINE_SCREEN_RECT``
    replaces this when the bezel makes the fit visibly wrong.
    """
    window_width = max(1, int(geometry["width"]))
    window_height = max(1, int(geometry["height"]))
    screen_width, screen_height = max(1, int(screen[0])), max(1, int(screen[1]))
    scale = min(window_width / screen_width, window_height / screen_height)
    width = max(1, round(screen_width * scale))
    height = max(1, round(screen_height * scale))
    return ScreenRect(
        x=(window_width - width) // 2,
        y=(window_height - height) // 2,
        width=width,
        height=height,
    )


def window_point(
    x: int,
    y: int,
    rect: ScreenRect,
    screen: Tuple[int, int],
) -> Tuple[int, int]:
    """Map device screen pixels to window pixels, clamped to the screen."""
    screen_width, screen_height = max(1, int(screen[0])), max(1, int(screen[1]))
    x = min(max(0, int(x)), screen_width - 1)
    y = min(max(0, int(y)), screen_height - 1)
    return (
        rect.x + round(x * rect.width / screen_width),
        rect.y + round(y * rect.height / screen_height),
    )


def vk_to_keysym(code: int) -> Optional[str]:
    """Translate a Windows virtual-key code to an X keysym name."""
    if code in _VK_KEYSYMS:
        return _VK_KEYSYMS[code]
    if 0x30 <= code <= 0x39 or 0x41 <= code <= 0x5A:
        return chr(code)
    if 0x70 <= code <= 0x7B:
        return f"F{code - 0x6F}"
    return None


def key_bindings(
    device_keys: Optional[Mapping[str, Any]] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[Button, str]:
    """Button -> X keysym, from the device definition, defaults and overrides.

    ``device_keys`` is the ``keys`` object of a device definition's
    ``simulator.json``. Values are Windows virtual-key codes or keysym names.
    An ``E2E_WINE_KEY_<BUTTON>`` environment variable always wins.
    """
    bindings: Dict[Button, str] = dict(DEFAULT_KEYS)
    for action, raw in (device_keys or {}).items():
        button = _SIMULATOR_KEY_ACTIONS.get(str(action).strip().lower())
        if button is None:
            continue
        keysym: Optional[str] = None
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            keysym = vk_to_keysym(raw)
        elif isinstance(raw, str) and raw.strip():
            text = raw.strip()
            if text.lstrip("-").isdigit():
                keysym = vk_to_keysym(int(text))
            else:
                keysym = text
        if keysym:
            bindings[button] = keysym
    env = os.environ if environ is None else environ
    for button in Button:
        override = env.get(f"{ENV_KEY_PREFIX}{button.value}")
        if override:
            bindings[button] = override.strip()
    return bindings


@dataclass(frozen=True)
class SimulatorKey:
    """One declared device key: a hit area and the behaviour it triggers.

    A device definition's ``simulator.json`` describes the physical buttons as
    clickable rectangles, not keyboard keys. ``fenix7`` for example declares
    ``{"behavior": "onSelect", "id": "enter", "location": {x: 356, y: 145, ...}}``.
    The driver clicks that rectangle; keystrokes are only a fallback.
    """

    button: Button
    location: ScreenRect
    is_hold: bool = False
    source: str = ""

    def describe(self) -> str:
        hold = " (hold)" if self.is_hold else ""
        return (
            f"{self.button.value} via {self.source or 'key'}{hold} at "
            f"{self.location.x},{self.location.y} "
            f"{self.location.width}x{self.location.height}"
        )


#: ``behavior`` values a device definition uses for the semantic buttons.
_BEHAVIOUR_BUTTONS: Mapping[str, Button] = {
    "onselect": Button.ENTER,
    "select": Button.ENTER,
    "previouspage": Button.UP,
    "nextpage": Button.DOWN,
    "onback": Button.BACK,
    "back": Button.BACK,
    "onmenu": Button.MENU,
    "menu": Button.MENU,
    "onstart": Button.START,
    "start": Button.START,
}

#: ``id`` values a device definition uses for the semantic buttons.
_KEY_ID_BUTTONS: Mapping[str, Button] = {
    "enter": Button.ENTER,
    "select": Button.ENTER,
    "up": Button.UP,
    "down": Button.DOWN,
    "esc": Button.BACK,
    "back": Button.BACK,
    "menu": Button.MENU,
    "start": Button.START,
    "lap": Button.START,
}


def _location_rect(value: Any) -> Optional[ScreenRect]:
    if not isinstance(value, Mapping):
        return None
    try:
        return ScreenRect(
            x=int(value["x"]),
            y=int(value["y"]),
            width=int(value["width"]),
            height=int(value["height"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def key_area(entry: Mapping[str, Any]) -> Optional[SimulatorKey]:
    """Parse one ``keys[]`` entry of a device definition."""
    location = _location_rect(entry.get("location"))
    if location is None:
        return None
    name = str(entry.get("id") or "").strip()
    behaviour = str(entry.get("behavior") or entry.get("behaviour") or "").strip()
    button = _KEY_ID_BUTTONS.get(name.lower()) or _BEHAVIOUR_BUTTONS.get(
        behaviour.lower()
    )
    if button is None:
        return None
    return SimulatorKey(
        button=button,
        location=location,
        is_hold=bool(entry.get("isHold") or entry.get("is_hold")),
        source=behaviour or name,
    )


def reference_size(
    display: Optional[ScreenRect],
    keys: Sequence[SimulatorKey],
) -> Optional[Tuple[int, int]]:
    """The far edge of the declared rectangles, kept for diagnostics only.

    The SDK's ``simulator.json`` already expresses ``display.location`` and
    ``keys[].location`` in the simulator window's client space: on the guest a
    260 px screen is declared as a 266 px rectangle at (79,149) inside a
    409x600 client area, because that is how the simulator draws it. Scaling
    those numbers against the window is therefore wrong; this helper only
    reports the declared extent.
    """
    rights: List[int] = []
    bottoms: List[int] = []
    if display is not None:
        rights.append(display.x + display.width)
        bottoms.append(display.y + display.height)
    for key in keys:
        rights.append(key.location.x + key.location.width)
        bottoms.append(key.location.y + key.location.height)
    if not rights:
        return None
    return (max(rights), max(bottoms))


@dataclass(frozen=True)
class CoordinateMapper:
    """One mapping from declared simulator coordinates to window pixels.

    Both ``press`` and ``tap`` use this object, so a click and a tap of the same
    declared point land on the same pixel. The declared coordinates are already
    in window client space, so the default is the identity; a pinned
    ``E2E_WINE_SCREEN_RECT`` re-anchors both the screen and the key hit areas
    when the window really is showing a different rectangle.
    """

    display: Optional[ScreenRect] = None
    scale: float = 1.0
    offset_x: int = 0
    offset_y: int = 0
    source: str = "declared coordinates (1:1 with the window)"

    def point(self, location: ScreenRect) -> Tuple[int, int]:
        """Centre of a declared rectangle, in window pixels."""
        return (
            self.offset_x + round((location.x + location.width / 2.0) * self.scale),
            self.offset_y + round((location.y + location.height / 2.0) * self.scale),
        )

    def rect(self, location: ScreenRect) -> ScreenRect:
        """A declared rectangle in window pixels."""
        return ScreenRect(
            x=self.offset_x + round(location.x * self.scale),
            y=self.offset_y + round(location.y * self.scale),
            width=max(1, round(location.width * self.scale)),
            height=max(1, round(location.height * self.scale)),
        )

    def screen_point(
        self, x: int, y: int, screen: Tuple[int, int]
    ) -> Tuple[int, int]:
        """A device screen pixel in window pixels (``tap``'s mapping)."""
        if self.display is None:
            raise DriverError(
                "cannot map a tap without the device definition's display.location"
            )
        rect = self.rect(self.display)
        return window_point(x, y, rect, screen)


def coordinate_mapper(
    display: Optional[ScreenRect],
    pinned: Optional[ScreenRect],
) -> CoordinateMapper:
    """The mapper for this run: declared 1:1, or re-anchored by the screen pin.

    The declared space is the window's client space, so no window-size scaling
    is applied. ``E2E_WINE_SCREEN_RECT`` says the screen is somewhere else in
    the window; the same scale and offset then move the key hit areas with it,
    so one pin corrects taps and buttons together.
    """
    if pinned is not None and display is not None and display.width > 0:
        scale = pinned.width / display.width
        return CoordinateMapper(
            display=display,
            scale=scale,
            offset_x=pinned.x - round(display.x * scale),
            offset_y=pinned.y - round(display.y * scale),
            source=ENV_SCREEN_RECT,
        )
    return CoordinateMapper(display=display)


@dataclass(frozen=True)
class SimulatorDefinition:
    """Facts the SDK's ``simulator.json`` declares for one device."""

    keys: Mapping[str, Any] = field(default_factory=dict)
    key_areas: Tuple[SimulatorKey, ...] = ()
    display: Optional["ScreenRect"] = None
    is_touch: bool = True

    def key_for(self, button: Button) -> Optional[SimulatorKey]:
        for key in self.key_areas:
            if key.button == button:
                return key
        return None


def read_simulator_definition(
    devices_root: Optional[Path], product: str
) -> "SimulatorDefinition":
    """The ``keys``/``display`` facts from ``<devices>/<product>/simulator.json``.

    Two ``keys`` shapes exist across SDK builds: a mapping of action names to
    Windows virtual-key codes (keystrokes), and a list of hit areas with a
    ``behavior`` and an ``isHold`` flag (clicks). Both are kept; ``press``
    prefers the declared hit area because it is what the simulator actually
    listens to.
    """
    if devices_root is None:
        return SimulatorDefinition()
    path = Path(devices_root) / product / "simulator.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SimulatorDefinition()
    if not isinstance(document, Mapping):
        return SimulatorDefinition()

    keys_document = document.get("keys")
    legacy_keys: Mapping[str, Any] = (
        keys_document if isinstance(keys_document, Mapping) else {}
    )
    key_areas: List[SimulatorKey] = []
    if isinstance(keys_document, list):
        for entry in keys_document:
            if isinstance(entry, Mapping):
                parsed = key_area(entry)
                if parsed is not None:
                    key_areas.append(parsed)

    display_rect: Optional[ScreenRect] = None
    is_touch = True
    display = document.get("display")
    if isinstance(display, Mapping):
        display_rect = _location_rect(display.get("location"))
        touch = display.get("isTouch")
        if isinstance(touch, bool):
            is_touch = touch
    return SimulatorDefinition(
        keys=legacy_keys,
        key_areas=tuple(key_areas),
        display=display_rect,
        is_touch=is_touch,
    )


def read_device_keys(devices_root: Optional[Path], product: str) -> Mapping[str, Any]:
    """The legacy ``keys`` mapping of ``<devices_root>/<product>/simulator.json``."""
    return read_simulator_definition(devices_root, product).keys


# ---------------------------------------------------------------------------
# pure helpers: simulator.ini
# ---------------------------------------------------------------------------


def ini_updates(
    product: str, mode: str, language_value: Optional[str], night: bool = False
) -> Dict[str, str]:
    """Startup keys written before the simulator starts.

    ``language_value`` is the numeric ``Language`` index the map measured, or
    ``None`` for the simulator's own default language: the key is then removed
    rather than written, because writing an unmeasured index would silently run
    the app in a language nobody asked for.

    ``night`` is the simulator's night theme (``nightMode=1``). Like the mode it
    is a start-up key, not a runtime switch, so changing it means a restart.
    """
    if mode not in MODE_INI_VALUES:
        raise DriverError(f"unknown mode {mode!r}; expected one of {sorted(MODE_INI_VALUES)}")
    updates: Dict[str, str] = {
        "LastUsedDevice": product,
        HTTPS_REQUIREMENTS_KEY: HTTPS_REQUIREMENTS_OFF,
        NIGHT_MODE_KEY: night_mode_value(night),
    }
    if language_value is not None:
        updates["Language"] = str(language_value)
    updates.update(MODE_INI_VALUES[mode])
    return updates


def ini_removals(language_value: Optional[str]) -> Tuple[str, ...]:
    """Keys to delete: ``Language`` when the default language is requested."""
    return () if language_value is not None else ("Language",)


def _night_from_environment() -> bool:
    """``E2E_WINE_NIGHT_MODE`` as a boolean (``1``/``true``/``yes``/``on``)."""
    raw = os.environ.get(ENV_NIGHT_MODE, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def night_mode_value(night: bool) -> str:
    """The ``nightMode`` ini value for a boolean."""
    return "1" if night else "0"


@dataclass(frozen=True)
class IniUpdate:
    """Result of rewriting a ``simulator.ini`` document."""

    text: str
    written: Tuple[str, ...] = ()
    appended: Tuple[str, ...] = ()
    removed: Tuple[str, ...] = ()

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(sorted(set(self.written) | set(self.appended) | set(self.removed)))


def update_ini(
    text: Optional[str],
    updates: Mapping[str, str],
    remove: Sequence[str] = (),
) -> IniUpdate:
    """Rewrite ``simulator.ini`` in the simulator's own format.

    The file the simulator writes is flat ``key=value`` lines with **no section
    header** (for example ``LastFplVersion=9``, ``BleState=2``). Existing keys
    are replaced in place and keep their original spelling, every unrelated key
    is preserved because the simulator rewrites the file itself, and a missing
    key is appended at the end rather than under an invented section. Keys in
    ``remove`` are deleted outright: a stale ``Language`` index from a previous
    run would otherwise put the app in the wrong language.
    """
    removed = {name.lower() for name in remove}
    lines = [
        line
        for line in (text or "").splitlines()
        if not (line.partition("=")[1] and line.partition("=")[0].strip().lower() in removed)
    ]
    written: List[str] = []
    pending: Dict[str, str] = {}
    for name, value in updates.items():
        found = False
        for index, line in enumerate(lines):
            key, separator, _ = line.partition("=")
            if separator and key.strip().lower() == name.lower():
                lines[index] = f"{key.strip()}={value}"
                written.append(name)
                found = True
        if not found:
            pending[name] = value

    for name, value in pending.items():
        lines.append(f"{name}={value}")

    return IniUpdate(
        text="\n".join(lines) + "\n" if lines else "",
        written=tuple(written),
        appended=tuple(pending),
        removed=tuple(sorted(set(remove))),
    )


# ---------------------------------------------------------------------------
# pure helpers: application properties
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PropertiesDocument:
    """An app-properties file split into its editable form."""

    properties: Mapping[str, Any]
    fmt: str = "key-value"
    container: Optional[str] = None
    body: Mapping[str, Any] = field(default_factory=dict)


def parse_properties(text: str) -> PropertiesDocument:
    """Parse either a JSON settings file or a ``key=value`` properties file.

    The simulator build on the guest decides the shape; both are accepted, and
    the format is preserved so the file stays readable by the simulator.
    """
    stripped = (text or "").strip()
    if stripped.startswith("{"):
        try:
            document = json.loads(stripped)
        except ValueError:
            document = None
        if isinstance(document, Mapping):
            if isinstance(document.get("properties"), Mapping):
                return PropertiesDocument(
                    properties=dict(document["properties"]),  # type: ignore[arg-type]
                    fmt="json",
                    container="properties",
                    body=dict(document),
                )
            return PropertiesDocument(properties=dict(document), fmt="json")

    properties: Dict[str, Any] = {}
    for line in stripped.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        properties[key.strip()] = _coerce_value(value.strip())
    return PropertiesDocument(properties=properties, fmt="key-value")


def _coerce_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def render_properties(
    properties: Mapping[str, Any],
    *,
    fmt: str = "key-value",
    body: Optional[Mapping[str, Any]] = None,
    container: Optional[str] = None,
) -> str:
    """Render a properties document back to the format it came from."""
    if fmt == "json":
        document: Dict[str, Any] = dict(body or {})
        if container:
            inner = dict(document.get(container) or {})
            inner.update(properties)
            document[container] = inner
        else:
            document.update(properties)
        return json.dumps(document, indent=2, sort_keys=True) + "\n"
    lines = [f"{name}={_render_value(value)}" for name, value in sorted(properties.items())]
    return "\n".join(lines) + "\n"


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ---------------------------------------------------------------------------
# pure helpers: the simulator's runtime log (CIQ_LOG.YML)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CiqLogError:
    """One classified simulator runtime error."""

    kind: str
    label: str
    message: str

    def to_document(self) -> Dict[str, str]:
        return {"kind": self.kind, "label": self.label, "message": self.message}


def classify_ciq_message(message: str) -> Optional[CiqLogError]:
    """Map a log message to one of the known false-defect classes."""
    text = " ".join((message or "").split())
    lowered = text.lower()
    for kind, marker, label in CIQ_ERROR_CLASSES:
        if marker.lower() in lowered:
            return CiqLogError(kind=kind, label=label, message=text)
    return None


def _ciq_messages(node: Any) -> List[str]:
    """Every message-ish string in a parsed log document."""
    found: List[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if (
                    str(key).lower() in ("message", "text", "description")
                    and isinstance(item, str)
                ):
                    found.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            lowered = value.lower()
            if "error" in lowered or "invalid" in lowered or "not found" in lowered:
                found.append(value)

    walk(node)
    return found


def parse_ciq_log(text: str) -> Tuple[Dict[str, str], List[CiqLogError]]:
    """Parse ``CIQ_LOG.YML`` into its header and classified runtime errors.

    The file is YAML on the guest builds, but its exact shape is not part of the
    SDK's contract. The parser therefore walks whatever YAML it gets, and falls
    back to the raw lines for the known markers. One error per class is kept:
    the report needs the class and the evidence, not a thousand repeats.
    """
    header: Dict[str, str] = {}
    messages: List[str] = []
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        document = None
    if isinstance(document, Mapping):
        raw_header = document.get("header") or document.get("Header")
        if isinstance(raw_header, Mapping):
            header = {str(key): str(value) for key, value in raw_header.items()}
        messages = _ciq_messages(document)
    if not messages:
        messages = [line for line in text.splitlines() if line.strip()]

    errors: List[CiqLogError] = []
    seen: set = set()
    for message in messages:
        error = classify_ciq_message(message)
        if error is None or error.kind in seen:
            continue
        seen.add(error.kind)
        errors.append(error)
    return header, errors


# ---------------------------------------------------------------------------
# pure helpers: error messages
# ---------------------------------------------------------------------------

_STAGE_HINTS: Mapping[str, str] = {
    "display": (
        "The entrypoint starts Xvfb on this display before it stages the SDK, "
        "so a running container should have one. Check "
        "`xdpyinfo -display {display}` inside the container and that DISPLAY "
        "names the same display."
    ),
    "simulator-window": (
        "The simulator must show a window titled `CIQ Simulator`. An Error "
        "dialog or no window at all means the simulator itself did not start: "
        "run `python -m e2e link-devices` and check that the Wine prefix has "
        "`drive_c/users/<user>/AppData/Roaming/Garmin/ConnectIQ/Devices`. The "
        "device name only appears in the title after `monkeydo` pushes the app, "
        "so it is not the readiness signal here."
    ),
    "app-launch": (
        "`monkeydo <prg> <device>` must stay running and stream `E2E:` lines. "
        "A non-zero exit means the PRG or the device name is wrong; its output "
        "is in logs/console.log."
    ),
    "app-ready": (
        "The app must print `E2E:LANGUAGE` and the first `E2E:VIEW`. Nothing "
        "else proves it rendered; logs/console.log has the full console."
    ),
    "app-settings": (
        "The app reads its properties at startup. Without them it shows the "
        "configuration error view (`ERRORVIEW;id=UI:CONFIG`), which would make "
        "every scenario pass vacuously."
    ),
}


def timeout_error(
    stage: str,
    timeout_s: float,
    *,
    display: str = DEFAULT_DISPLAY,
    details: Sequence[str] = (),
) -> DriverError:
    """Build an actionable error for a stage that ran out of time."""
    hint = _STAGE_HINTS.get(stage, "").format(display=display)
    lines = [f"{stage} did not complete within {timeout_s:.0f}s.", hint]
    for detail in details:
        if detail:
            lines.append(f"  {detail}")
    lines.append("See tests/docs/simulator-driver.md.")
    return DriverError("\n".join(lines))


def tail(lines: Sequence[str], limit: int = 12) -> str:
    """The last ``limit`` non-empty lines, for an error message."""
    kept = [line for line in lines if line.strip()][-limit:]
    return "\n".join(f"  {line}" for line in kept) if kept else "  <no output>"


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------


class WineSimulatorDriver(SimulatorDriver):
    """Drive the Windows Connect IQ simulator through Wine and Xvfb."""

    name: str = "wine"
    supports_events: bool = True
    supports_screenshots: bool = True
    #: The driver can relaunch in another start-up mode and probe the menu bar.
    #: Neither is a mid-run transition: the mode keys are read at start, and the
    #: menu bar is where a transition would come from (see ``probe_menus``).
    supports_restart_in_mode: bool = True
    supports_menu_probe: bool = True
    #: The simulator's ``Language`` setting is a numeric index, not a code, and
    #: the index is measured per code (see ``e2e.languages``). Every code with a
    #: measured index can be provisioned; ``eng`` always can, by removing the key
    #: and using the simulator's own default. ``can_provision_language()`` is the
    #: precise question, because this is per language and not per driver.
    provisions_language: bool = True

    def __init__(
        self,
        layout: SdkLayout,
        *,
        wineprefix: Optional[Path] = None,
        display: Optional[str] = None,
        artifact_dir: Optional[Path] = None,
        log=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self.layout = layout
        self.wineprefix = Path(wineprefix) if wineprefix else None
        self.display = display or os.environ.get("DISPLAY") or DEFAULT_DISPLAY
        self.artifact_dir = (
            Path(artifact_dir)
            if artifact_dir
            else Path(tempfile.gettempdir()) / "openhab-garmin-e2e" / "wine"
        )
        self._log = log or (lambda message: None)
        self._log_path = self.artifact_dir / "logs" / "driver.log"
        self._events: List[Tuple[float, str]] = []
        self._events_lock = threading.Lock()
        self._console_handle: Optional[Any] = None
        self._simulator_handle: Optional[Any] = None
        self._app_process: Optional[subprocess.Popen] = None
        self._app_thread: Optional[threading.Thread] = None
        self._simulator_process: Optional[subprocess.Popen] = None
        self._window: Optional[str] = None
        self._prg: Optional[Path] = None
        self._device: Optional[Device] = None
        self._settings: Optional[AppSettings] = None
        self._settings_file: Optional[Path] = None
        self._keys: Dict[Button, str] = dict(DEFAULT_KEYS)
        self._definition = SimulatorDefinition()
        self._last_press: Optional[PressResult] = None
        self._app_started_at: Optional[float] = None
        self._mode = "ble"
        self._language = "eng"
        self._language_value: Optional[str] = None
        self._night = False
        self._expect_language = True
        self._language_index_map: Optional[Dict[str, int]] = None
        #: The language code the app reported at the last ``wait_ready``.
        self.last_language: Optional[str] = None
        self._display_name = ""
        self._ini_path: Optional[Path] = None
        self._ini_summary: Optional[IniUpdate] = None
        self._ciq_errors: List[CiqLogError] = []
        self._ciq_header: Dict[str, str] = {}
        self._ciq_log: Optional[Path] = None
        self._started = False
        self._display_checked = False

    # -- capability probe --------------------------------------------------
    @classmethod
    def is_available(cls, layout: SdkLayout) -> bool:
        """True when Wine, ``monkeydo`` and the Windows simulator are present."""
        try:
            require_simulator(layout)
        except Exception:  # noqa: BLE001 - probe must not raise
            return False
        if layout.monkeydo is None:
            return False
        wine = os.environ.get(WINE_ENV) or shutil.which("wine")
        return bool(wine)

    # -- paths -------------------------------------------------------------
    @property
    def prefix(self) -> Path:
        return self.wineprefix or wine_prefix()

    @property
    def stage_dir(self) -> Path:
        return self.artifact_dir / "launch"

    @property
    def console_log(self) -> Path:
        return self.artifact_dir / "logs" / "console.log"

    @property
    def simulator_log(self) -> Path:
        return self.artifact_dir / "logs" / "simulator.log"

    @property
    def settings_file(self) -> Optional[Path]:
        """The application-properties file the app reads, once seeded."""
        return self._settings_file

    @property
    def process_ids(self) -> Mapping[str, Optional[int]]:
        """PIDs kept for ``stop()``; useful in a log when a run is killed."""
        return {
            "simulator": getattr(self._simulator_process, "pid", None),
            "app": getattr(self._app_process, "pid", None),
        }

    # -- contract ----------------------------------------------------------
    def prepare(self, prg: Path) -> None:
        """Validate the tools and stage ``prg`` where ``start`` will launch it."""
        source = Path(prg)
        if not source.is_file():
            raise DriverError(f"cannot stage {source}: it is not a file")
        require_simulator(self.layout)
        if self.layout.monkeydo is None:
            raise DriverError(
                f"{self.layout.root} has no Linux monkeydo: expected "
                "linux/bin/monkeydo or bin/monkeydo.\n"
                "The Windows shell.exe is not a replacement (see "
                "tests/docs/simulator-driver.md)."
            )
        self._require_tool("wine")
        for tool in ("xdotool", "import", "xdpyinfo"):
            self._require_tool(tool)

        target = self.stage_dir / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        self._prg = target
        self._record(f"staged PRG {source} -> {target}")
        self._sweep_existing_simulator()

    def start(
        self,
        device: Device,
        settings: AppSettings,
        mode: str = "ble",
        language: str = "eng",
    ) -> None:
        """Start the simulator for ``device`` and launch the app."""
        self._start_with(
            device,
            settings,
            mode=mode,
            language=language,
            language_value=self._language_ini_value(language),
        )

    def can_provision_language(self, language: str) -> bool:
        """True when this driver can put the app into ``language``."""
        if language == DEFAULT_LANGUAGE:
            return True
        return language_index(language, self._language_map()) is not None

    def probe_language(
        self,
        index: int,
        device: Device,
        settings: AppSettings,
        mode: str = "ble",
        timeout_s: float = DEFAULT_READY_TIMEOUT,
    ) -> str:
        """Launch with an explicit ``Language`` index and return the reported code.

        ``e2e language-scan`` uses this to enumerate the map: the simulator does
        not document its language order, so the only honest source is one launch
        per index plus the ``E2E:LANGUAGE`` line the app prints.
        """
        self._start_with(
            device,
            settings,
            mode=mode,
            language=f"index:{index}",
            language_value=str(int(index)),
            expect_language=False,
        )
        self.wait_ready(timeout_s)
        return self.last_language or "unknown"

    def _language_map(self) -> Dict[str, int]:
        cached = getattr(self, "_language_index_map", None)
        if cached is None:
            cached = load_language_index()
            self._language_index_map = cached
        return cached

    def _language_ini_value(self, language: str) -> Optional[str]:
        """The numeric ``Language`` value, or ``None`` for the simulator default."""
        if language == DEFAULT_LANGUAGE:
            return None
        index = language_index(language, self._language_map())
        if index is None:
            raise DriverError(
                f"language {language!r} has no measured simulator index, so it "
                "cannot be provisioned.\n"
                "The simulator stores its language as an index (`Language=7` is "
                "German), and its order is not documented. Measure it once:\n"
                f"  python -m e2e language-scan --prg <built.prg> --write\n"
                f"which updates {LANGUAGE_INDEX_FILE}. Until then the scenario is "
                "reported blocked rather than run in the wrong language."
            )
        return str(index)

    def _start_with(
        self,
        device: Device,
        settings: AppSettings,
        *,
        mode: str,
        language: str,
        language_value: Optional[str],
        expect_language: bool = True,
    ) -> None:
        if self._prg is None:
            raise DriverError("prepare() must stage a PRG before start()")
        if mode not in MODE_INI_VALUES:
            raise DriverError(f"unknown mode {mode!r}; expected one of {sorted(MODE_INI_VALUES)}")
        require_simulator(self.layout)

        self.stop()
        self._device = device
        self._settings = settings
        self._mode = mode
        self._language = language
        self._language_value = language_value
        self._expect_language = expect_language
        self._night = _night_from_environment()
        if self._night:
            self._record(
                f"{ENV_NIGHT_MODE}=1: the simulator starts in night mode "
                f"({NIGHT_MODE_KEY}=1)"
            )
        self.last_language = None
        self._ciq_errors = []
        self._ciq_header = {}
        self._ciq_log = None
        devices_root = find_device_definitions(self.layout.root)
        self._definition = read_simulator_definition(devices_root, device.product)
        self._display_name = read_device_display_name(devices_root, device.product)
        runtime = read_device_runtime_version(devices_root, device.product)
        if runtime:
            self._record(
                f"device runtime: {device.product} emulates Connect IQ {runtime} "
                "(compiler.json connectIQVersion; this is the number in the "
                "window title and it is NOT the simulator build)"
            )
        self._keys = key_bindings(self._definition.keys)
        self._record(
            "device definition: "
            + (
                ", ".join(key.describe() for key in self._definition.key_areas)
                or "no declared key hit areas (keystrokes only)"
            )
        )

        self._ensure_display()
        self._sweep_existing_simulator()
        self._write_simulator_ini(device, mode, language_value)
        self._start_simulator()
        self._seed_settings(settings)
        self.clear_app_data()
        self._launch_app(device)
        self._started = True

    def wait_ready(self, timeout_s: float = DEFAULT_READY_TIMEOUT) -> None:
        """Wait for the first view and verify the language the app reported."""
        if not self._started or self._app_process is None:
            raise DriverError("wait_ready() called before start()")

        timeout = timeout_s if timeout_s and timeout_s > 0 else DEFAULT_READY_TIMEOUT
        deadline = time.monotonic() + min(timeout, DEFAULT_READY_TIMEOUT * 2)
        parsed = self._wait_for_startup(timeout)
        self._record_ini_language_after_start()
        language = next((event for event in parsed if event.event == "LANGUAGE"), None)
        observed = language.get("code") if language is not None else "unknown"
        self.last_language = observed
        language_error: Optional[str] = None
        if observed != self._language:
            if self._expect_language:
                language_error = (
                    f"the app reported system language {observed!r} but the "
                    f"scenario requested {self._language!r}, and this driver "
                    "provisioned it (the numeric simulator index was written).\n"
                    "That means the index is wrong for this code or the simulator "
                    "ignored the key: correct the map with\n"
                    f"  python -m e2e language-scan --prg <built.prg> --write\n"
                    f"({LANGUAGE_INDEX_FILE}). A run in the wrong language would "
                    "assert the wrong strings, so it is stopped here.\n"
                    "The launch is attempted first so the rest of the run is still "
                    "diagnosable from the same log."
                )
                self._record(f"language mismatch (deferred): {language_error}")
            else:
                self._record(
                    f"probed language: the app reported {observed!r} for this index"
                )
        else:
            self._record(f"language confirmed: {observed}")

        # Launch before raising a language mismatch: a run that never opened the
        # app must not look like a launch failure caused by the language.
        parsed = self._open_app_if_needed(parsed, deadline)

        if self._settings is not None and self._settings.url:
            for event in parsed:
                if event.event == "ERRORVIEW" and event.get("id") == "UI:CONFIG":
                    raise DriverError(
                        "the app showed the configuration error view "
                        "(`ERRORVIEW;id=UI:CONFIG`), so the property defaults it "
                        "was compiled with do not contain the URL.\n"
                        f"properties requested: {sorted(self._settings.properties)}\n"
                        f"properties file written: {self._settings_file or '<none>'}\n"
                        "Properties are built into the PRG by `e2e build` (a "
                        "settings.xml resource override asserted against monkeyc's "
                        "settings JSON). Rebuild with the scenario's fixture, or "
                        f"pass --prg with a PRG built that way, or set "
                        f"{ENV_SETTINGS_FILE} to an explicit properties file."
                    )

        modes = [event.get("mode") for event in parsed if event.event == "CONNECTION"]
        self._record(
            f"ready: language={observed} mode(s)={modes or ['<none>']} "
            f"requested mode={self._mode}"
        )
        self._record(
            "note: an E2E:VIEW line means the view was constructed, not that it "
            "was painted; a paint failure appears as `Invalid Font Specified` in "
            "CIQ_LOG.YML"
        )
        expected = MODE_CONNECTION_NAMES.get(self._mode, ())
        if expected and modes and not set(modes) & set(expected):
            self._record(
                f"note: the app reports BLE first and changes mode after a request; "
                f"{modes} is expected for requested mode {self._mode!r}. The scenario's "
                "request assertions prove the transition."
            )
        self._record_device_title()
        self._verify_window_geometry()
        if language_error:
            raise DriverError(language_error)

    def _record_ini_language_after_start(self) -> None:
        """Record what ``simulator.ini`` says after the simulator started.

        The driver writes the file *before* ``simulator.exe``, which is what the
        maintainer's manual runs do. If the simulator rewrites it on start, the
        value we wrote is gone and the app never sees the language; only the
        file's state after start can tell the difference.
        """
        path = self._ini_path
        if path is None:
            return
        value = None
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            self._record(f"warning: could not re-read {path}: {error}")
            return
        for line in text.splitlines():
            key, separator, raw = line.partition("=")
            if separator and key.strip().lower() == "language":
                value = raw.strip()
        state = "absent" if value is None else value
        self._record(
            f"simulator.ini after start: Language={state} "
            f"(expected {self._language!r})"
        )

    def _verify_window_geometry(self) -> None:
        """Record the window size and check the declared space fits inside it.

        Each device's ``simulator.json`` is in that device's own art space and
        maps 1:1 to that device's window, and the size is per device and
        persisted by the simulator: fenix7's window is 409x600 while
        fenix847mm's is 713x961. Anything that assumes one of those sizes is only
        accidentally right for one device, so the size is read per run and the
        declared screen rectangle is checked against it.
        """
        window = self._window
        if window is None:
            return
        display = self._definition.display
        pinned = parse_screen_rect(os.environ.get(ENV_SCREEN_RECT, ""))
        try:
            geometry = self._geometry(window)
        except DriverError as error:
            self._record(
                f"warning: could not read the window geometry, so the declared "
                f"display.location was not checked against it ({error})"
            )
            return
        if display is not None and pinned is None:
            # The window can still be resizing right after the app launch, so
            # give it a moment before declaring the declared space impossible.
            for _ in range(6):
                if (
                    display.x + display.width <= geometry.get("width", 0)
                    and display.y + display.height <= geometry.get("height", 0)
                ):
                    break
                time.sleep(0.5)
                geometry = self._geometry(window)
        width, height = geometry.get("width", 0), geometry.get("height", 0)
        self._record(
            f"window geometry: {width}x{height} at "
            f"(+{geometry.get('x', 0)},+{geometry.get('y', 0)})"
        )
        if display is None:
            return
        if pinned is not None:
            self._record(
                f"{ENV_SCREEN_RECT} is pinned, so the declared "
                "display.location is not checked against the window"
            )
            return
        if display.x + display.width > width or display.y + display.height > height:
            product = self._device.product if self._device else "?"
            raise DriverError(
                f"the simulator window is {width}x{height}, but {product} declares "
                f"display.location {display.x},{display.y} "
                f"{display.width}x{display.height}, which does not fit inside it.\n"
                "The declared coordinates are in this device's own art space and "
                "map 1:1 to its own window, so the window is the wrong device's "
                "size or the simulator ignored the device. Check "
                "`LastUsedDevice` in simulator.ini, or pin "
                f"{ENV_SCREEN_RECT}=x,y,width,height from a screenshot."
            )
        self._record(
            f"declared display {display.x},{display.y} {display.width}x"
            f"{display.height} fits the {width}x{height} window 1:1"
        )

    def launch(self, timeout_s: float = DEFAULT_LAUNCH_TIMEOUT) -> bool:
        """Open the app from the glance by retrying until the app responds.

        The sequence is *page down, then select* (two page-downs), with every key
        sent after re-taking focus and a 2 s gap. The guest then measured that
        input delivery itself is **nondeterministic**: the same command, same PRG,
        same container and same focus alternated between working and doing
        nothing, roughly one run in two, and the successful ones took anywhere
        between one and several minutes after launch. The variant did not matter.

        So this is a retry loop, not a one-shot sequence: after the settle it
        sends one sequence, waits ``LAUNCH_RESPONSE_WAIT_S`` for
        ``E2E:CONNECTION`` or a non-glance view, and repeats every
        ``LAUNCH_RETRY_INTERVAL_S`` until ``LAUNCH_RETRY_BUDGET_S`` or the
        deadline. Variants cycle — the declared hit areas, keyboard taps,
        keydown/keyup, and a held mouse button — because they are cheap
        alternatives and any of them may be the one that lands.

        Every retry logs its index, elapsed time, variant, the ``E2E:CONNECTION``
        state and the view, so the flakiness is data in the log rather than a
        bug hunt. Success is ``E2E:CONNECTION`` or a view whose category is not
        ``glance``, or an ``ERRORVIEW``.
        """
        deadline = time.monotonic() + max(1.0, timeout_s)
        parsed = parse_lines(self.events())
        current = next((event for event in reversed(parsed) if event.event == "VIEW"), None)
        if current is not None and current.get("category") != "glance":
            return True
        device = self._device
        if device is None:
            return False
        started = self._app_started_at or time.monotonic()
        self._record(
            "the app started on the glance page; opening it with the measured "
            f"sequence: {self._launch_down_count()} x page down, then select, "
            "retried until the app responds"
        )
        self._settle_before_launch(deadline)
        self._probe_selection(deadline)
        plans: List[Tuple[str, str, Optional[str], Optional[str]]] = []
        if device.supports_buttons:
            plans = [
                ("declared hit areas", "mouse", None, None),
                ("keyboard taps", "keyboard", "Down", "Return"),
                ("keyboard keydown/keyup", "keyboard-hold", "Down", "Return"),
                ("declared hit areas (hold)", "mouse-hold", None, None),
            ]
        else:
            plans = [("a tap on the screen", "tap", None, None)]

        retry_budget = self._seconds_from_env(
            ENV_LAUNCH_RETRY_BUDGET, LAUNCH_RETRY_BUDGET_S, 0.0, 600.0
        )
        interval = self._seconds_from_env(
            ENV_LAUNCH_RETRY_INTERVAL, LAUNCH_RETRY_INTERVAL_S, 0.0, 60.0
        )
        stop = min(deadline, time.monotonic() + retry_budget)
        retry = 0
        while time.monotonic() < stop:
            retry += 1
            label, mechanism, down_key, select_key = plans[(retry - 1) % len(plans)]
            mark = len(self.events())
            elapsed = time.monotonic() - started
            self._record(
                f"launch retry {retry}: variant={label}; elapsed={elapsed:.1f}s"
            )
            try:
                if mechanism == "tap":
                    self.tap(
                        device.screen_width // 2, device.screen_height // 2, 1
                    )
                else:
                    downs = self._launch_down_count()
                    for step in range(downs):
                        self._launch_step(
                            Button.DOWN,
                            mechanism,
                            down_key,
                            f"page down {step + 1}/{downs}",
                        )
                        time.sleep(LAUNCH_KEY_GAP_S)
                    self._launch_step(
                        Button.ENTER, mechanism, select_key, "select"
                    )
            except DriverError as error:
                self._record(f"warning: launch retry {retry} failed: {error}")
            else:
                if self._wait_for_app_view(mark, stop, retry, label):
                    return True
                if self._app_died():
                    return False
            self._record(
                f"launch retry {retry} result: connection="
                f"{self._connection_state().split('=', 1)[-1]}; "
                f"view={self._current_view()}; elapsed={time.monotonic() - started:.1f}s"
            )
            next_tick = min(time.monotonic() + interval, stop)
            while time.monotonic() < next_tick:
                time.sleep(min(0.25, next_tick - time.monotonic()))
        self._record(
            f"the launch sequence left the app on the glance page after "
            f"{retry} retr{'y' if retry == 1 else 'ies'} over "
            f"{retry_budget:g}s. Every retry above records the variant, the "
            "focus, the view and the connection state. The guest measured this "
            "as nondeterministic rather than wrong: the same command works on "
            "one run and not the next."
        )
        return False

    def _probe_selection(self, deadline: float) -> None:
        """One page down, then measure whether the glance selection moved.

        If a single `Down` sometimes moves the highlight and sometimes does not,
        the simulator is intermittently ignoring input and the retry loop is the
        right answer. Cheap, and it makes that visible in the log.
        """
        if not self.supports_screenshots or time.monotonic() >= deadline:
            return
        region = self.screen_region()
        if region is None:
            return
        before = self.artifact_dir / "launch-probe-before.png"
        after = self.artifact_dir / "launch-probe-after.png"
        try:
            self.screenshot(before)
            self.press(Button.DOWN, 1)
            time.sleep(1.0)
            self.screenshot(after)
            difference = self.region_pixel_difference(before, after, region)
        except (DriverError, OSError) as error:
            self._record(f"note: selection probe skipped ({error})")
            return
        changed = int(difference or 0)
        elapsed = time.monotonic() - (self._app_started_at or time.monotonic())
        verdict = (
            "the glance selection moved"
            if changed > 0
            else "no visible change, which can happen while the simulator is "
            "still starting"
        )
        self._record(
            f"selection probe at {elapsed:.1f}s since app start: one page down "
            f"changed the screen by {changed}px — {verdict}. This is an early "
            "signal only; the launch retries below decide whether input lands."
        )

    def _launch_down_count(self) -> int:
        """How many page-downs the launch sequence sends (measured: 2)."""
        raw = os.environ.get(ENV_LAUNCH_DOWNS, "").strip()
        try:
            value = int(raw) if raw else LAUNCH_DOWNS
        except ValueError:
            value = LAUNCH_DOWNS
        return max(1, min(5, value))

    def _settle_before_launch(self, deadline: float) -> None:
        """Wait until the app is interactive, not merely drawing.

        The guest measured that waiting for the first ``E2E:VIEW`` is not
        equivalent to the app accepting input: with a ~50 s wait after
        ``monkeydo`` the sequence worked every time, while a 2 s wait failed all
        attempts. The rule here is the explicit version of that: the app must
        have been running for ``LAUNCH_DWELL_S`` **and** the screen must be
        stable (two captures ``LAUNCH_STABLE_INTERVAL_S`` apart that match),
        bounded by ``LAUNCH_SETTLE_MAX_S`` and the launch deadline. Both limits
        and the dwell have environment overrides.
        """
        dwell = self._seconds_from_env(ENV_LAUNCH_DWELL, LAUNCH_DWELL_S, 0.0, 120.0)
        maximum = self._seconds_from_env(
            ENV_LAUNCH_SETTLE, LAUNCH_SETTLE_MAX_S, 0.0, 180.0
        )
        started = self._app_started_at or time.monotonic()
        target = min(started + dwell, started + maximum, deadline)
        if target > time.monotonic():
            self._record(
                f"settling: waiting until {dwell:g}s after app start before the "
                "launch sequence (the app must accept input, not just draw)"
            )
            while time.monotonic() < target:
                time.sleep(min(0.25, target - time.monotonic()))

        stable = self._wait_for_stable_screen(
            min(
                deadline,
                max(
                    started + maximum,
                    time.monotonic() + 2 * LAUNCH_STABLE_INTERVAL_S,
                ),
            )
        )
        self._record(
            "settle complete: "
            + ("the screen is stable" if stable else "the screen is still changing")
            + f"; {time.monotonic() - started:.1f}s since app start"
        )

    def _seconds_from_env(
        self, name: str, default: float, low: float, high: float
    ) -> float:
        raw = os.environ.get(name, "").strip()
        try:
            value = float(raw) if raw else default
        except ValueError:
            value = default
        return max(low, min(high, value))

    def _wait_for_stable_screen(self, deadline: float) -> bool:
        """Two captures a second apart must match before input is sent."""
        difference = getattr(self, "pixel_difference", None)
        if difference is None or not self.supports_screenshots:
            return False
        window = self._window
        if window is None:
            return False
        first = self.artifact_dir / "launch-settle-a.png"
        second = self.artifact_dir / "launch-settle-b.png"
        first.parent.mkdir(parents=True, exist_ok=True)
        while time.monotonic() + LAUNCH_STABLE_INTERVAL_S <= deadline:
            try:
                self.screenshot(first)
                time.sleep(LAUNCH_STABLE_INTERVAL_S)
                self.screenshot(second)
                measured = difference(first, second)
            except (DriverError, OSError) as error:
                self._record(
                    f"warning: could not measure screen stability ({error}); "
                    "sending the sequence anyway"
                )
                return False
            if measured is not None and int(measured) == 0:
                return True
        return False

    def _connection_state(self) -> str:
        parsed = parse_lines(self.events())
        mode = next(
            (event.get("mode") for event in reversed(parsed) if event.event == "CONNECTION"),
            None,
        )
        return f"connection={mode}" if mode else "connection=none"

    def _window_focus(self) -> Optional[str]:
        """The window X currently considers focused, or ``None``."""
        try:
            completed = self._run(
                xdotool_getwindowfocus_command(),
                check=False,
                action="xdotool getwindowfocus",
            )
        except DriverError:
            return None
        value = (completed.stdout or "").strip()
        return value or None

    def _launch_step(
        self,
        button: Button,
        mechanism: str,
        keysym: Optional[str],
        what: str,
    ) -> None:
        """Send one key of the launch sequence and record what it produced.

        Records the focused window before and after each key: focus can move
        between keys (Wine's splash window takes it), which is invisible in a log
        that only says "the key was sent" and is indistinguishable from a
        simulator that ignores it.
        """
        focus_before = self._window_focus()
        self.press(button, 1, mechanism=mechanism)
        focus_after = self._window_focus()
        self._record(
            f"launch step {what}: focus before={focus_before}; "
            f"focus after={focus_after}; view={self._current_view()}; "
            f"{self._connection_state()}"
        )
        simulator = str(self._window) if self._window else None
        if simulator and focus_before and focus_before != simulator:
            self._record(
                f"warning: focus was on window {focus_before} before {what}, not "
                f"the simulator window {simulator}"
            )
        if mechanism.startswith("mouse"):
            path = self.artifact_dir / f"launch-step-{what.replace(' ', '-')}.png"
            try:
                self.screenshot(path)
            except (DriverError, OSError) as error:
                self._record(f"note: no screenshot after {what}: {error}")
            else:
                self._record(f"screenshot after {what}: {path}")

    def _current_view(self) -> str:
        parsed = parse_lines(self.events())
        view = next((event for event in reversed(parsed) if event.event == "VIEW"), None)
        if view is None:
            return "none"
        return f"{view.get('category')}/{view.get('view')}"

    def _app_died(self) -> bool:
        process = self._app_process
        if process is not None and process.poll() is not None:
            self._record(
                f"monkeydo exited with code {process.returncode} during the "
                "launch sequence"
            )
            return True
        return False

    def _wait_for_app_view(
        self, mark: int, deadline: float, number: int, label: str
    ) -> bool:
        """Wait for ``E2E:CONNECTION`` or a non-glance view after one retry.

        ``E2E:CONNECTION`` is the app actually starting, which the guest uses as
        the success signal; a different view (or an ``ERRORVIEW``) counts too.
        After a connection line it waits a little longer for the view, so a
        caller that runs immediately afterwards sees the new screen.
        """
        until = min(time.monotonic() + LAUNCH_RESPONSE_WAIT_S, deadline)
        connected = False
        while time.monotonic() < until:
            if self._app_died():
                return False
            for event in parse_lines(list(self.events())[mark:]):
                if event.event == "CONNECTION":
                    connected = True
                if event.event == "ERRORVIEW" or (
                    event.event == "VIEW" and event.get("category") != "glance"
                ):
                    self._record(
                        f"launch retry {number} ({label}) opened the app: "
                        f"{event.describe()}"
                    )
                    return True
            if connected:
                # The app started; give it a moment for the first view.
                until = min(until + 1.0, deadline)
                connected = False
                self._record(
                    f"launch retry {number} ({label}) produced E2E:CONNECTION; "
                    "waiting for the first view"
                )
            time.sleep(0.25)
        self._record(
            f"launch retry {number} ({label}) produced no connection and no view "
            f"other than the glance (view is {self._current_view()})"
        )
        return False

    def _open_app_if_needed(
        self, parsed: Sequence[TraceEvent], deadline: float
    ) -> List[TraceEvent]:
        """Run :meth:`launch` when the first view is the glance."""
        current = next(
            (event for event in reversed(parsed) if event.event == "VIEW"), None
        )
        if current is None or current.get("category") != "glance":
            return list(parsed)
        remaining = max(1.0, deadline - time.monotonic())
        self.launch(min(remaining, DEFAULT_LAUNCH_TIMEOUT))
        return list(parse_lines(self.events()))

    def restart_in_mode(
        self,
        mode: str,
        *,
        night: Optional[bool] = None,
        timeout_s: float = DEFAULT_READY_TIMEOUT,
    ) -> bool:
        """Relaunch the app in another start-up mode (and optionally night mode).

        This is the documented capability the harness has without scripting the
        simulator's menus: the mode keys are written to ``simulator.ini`` before
        ``simulator.exe`` starts, so a different start-up mode means a restart.
        It answers "the app starts in Wi-Fi", **not** "the app transitions to
        Wi-Fi while running"; only the latter exercises the connection-mode
        indicator and the Wi-Fi sync path, and only the menu bar can do that
        (see :meth:`probe_menus`).

        The call is deliberately explicit about what it did: it records the old
        and new mode, the night value, and whether the app came back. It returns
        ``True`` when the app reported a connection mode after the restart.
        ``night`` defaults to the current value.
        """
        device = self._device
        settings = self._settings
        if device is None or settings is None or self._prg is None:
            raise DriverError("restart_in_mode() needs a started session")
        if mode not in MODE_INI_VALUES:
            raise DriverError(
                f"unknown mode {mode!r}; expected one of {sorted(MODE_INI_VALUES)}"
            )
        previous = self._mode
        previous_night = self._night
        if night is not None:
            self._night = bool(night)
        self._record(
            f"restarting in mode {mode!r} (was {previous!r}) with "
            f"{NIGHT_MODE_KEY}={night_mode_value(self._night)}; this proves "
            "start-up mode, not a mid-run transition"
        )
        self.stop()
        self._mode = mode
        self._ciq_errors = []
        self._ciq_header = {}
        self._ciq_log = None
        self._write_simulator_ini(device, mode, self._language_value)
        self._start_simulator()
        self._seed_settings(settings)
        self._launch_app(device, reset_events=False, append_log=True)
        self._started = True
        try:
            self.wait_ready(min(timeout_s, DEFAULT_READY_TIMEOUT))
        except DriverError as error:
            self._record(f"restart in mode {mode!r} did not become ready: {error}")
            self._night = previous_night
            return False
        modes = [
            event.get("mode")
            for event in parse_lines(list(self.events()))
            if event.event == "CONNECTION"
        ]
        self._record(
            f"restart in mode {mode!r} ready; app reported connections {modes}; "
            f"night {night_mode_value(self._night)}"
        )
        return bool(modes)

    def probe_menus(self, timeout_s: float = 20.0) -> MenuProbeReport:
        """Walk the simulator's menu bar with real clicks and report what happens.

        The window has a menu bar drawn inside it, so the labels are found by
        clicking along the bar row and watching for a popup window
        (``xwininfo -root -children`` gains an id) or a screen change. Every
        candidate point and its result goes into the report, so a run that finds
        nothing still says exactly what was tried.

        This is a probe, not a capability claim: it returns the evidence and the
        caller decides. Nothing is asserted here.
        """
        window = self._require_window()
        geometry = self._geometry(window)
        report = MenuProbeReport()
        shots = self.artifact_dir / "menus"
        shots.mkdir(parents=True, exist_ok=True)
        before = shots / "menu-before.png"
        self.screenshot(before)
        root_before = self._children_ids()
        deadline = time.monotonic() + max(1.0, timeout_s)
        for x, y in menu_bar_points(
            geometry.get("width", 0), geometry.get("height", 0)
        ):
            if time.monotonic() >= deadline or report.opened:
                break
            point_shot = shots / f"menu-{x}-{y}.png"
            try:
                self._click_at(window, x, y, action=f"menu probe ({x},{y})")
                time.sleep(0.4)
                self.screenshot(point_shot)
                root_after = self._children_ids()
                delta = self.pixel_difference(before, point_shot)
            except DriverError as error:
                report.steps.append(
                    MenuProbeStep(x=x, y=y, note=f"skipped: {error}")
                )
                continue
            fresh = new_window_ids(root_before, root_after)
            step = MenuProbeStep(
                x=x,
                y=y,
                screen_delta=int(delta or 0),
                new_windows=tuple(fresh),
            )
            report.steps.append(step)
            if fresh:
                report.popup_window = fresh[0]
                report.screenshot = str(point_shot)
                report.popup_geometry = self._geometry_text(fresh[0])
                break
        report.screenshot = report.screenshot or str(shots / "menu-last.png")
        self._record("menu probe: " + report.verdict())
        for line in report.lines():
            self._record(f"menu probe step {line}")
        return report

    def _children_ids(self) -> List[int]:
        """Window ids the X server reports as children of the root."""
        try:
            completed = self._run(
                xwininfo_children_command(),
                check=False,
                action="xwininfo -root -children",
            )
        except DriverError:
            return []
        return sorted(parse_window_ids(completed.stdout or ""))

    def _geometry_text(self, window: int) -> str:
        try:
            geometry = self._geometry(str(window))
        except DriverError:
            return "geometry unknown"
        return (
            f"{geometry.get('width', 0)}x{geometry.get('height', 0)} at "
            f"(+{geometry.get('x', 0)},+{geometry.get('y', 0)})"
        )

    def _record_device_title(self) -> None:
        """Corroborate the requested device from the window title.

        The trace stream, not the title, proves the app is running: the title
        only gains the device name after ``monkeydo`` pushes the app. A silent
        or different title is recorded as a warning so the run is not failed on
        a name, but the log says what to check when results look wrong.
        """
        device = self._device
        if device is None:
            return
        window = self._window
        if window is None:
            windows = self._find_windows()
            window = windows[0] if windows else None
        name = self._window_name(window) if window else None
        title = parse_window_title(name or "")
        if title.error:
            self._record(
                f"warning: an error dialog is visible after the app started: {name!r}"
            )
            return
        if not title.loaded:
            self._record(
                "warning: the simulator title still does not name a device "
                f"({name!r}); the E2E trace proves the app is running, so this is "
                "recorded rather than failed. If the app misbehaves, check "
                f"`LastUsedDevice` in {self._ini_description()}"
            )
            return
        matches = title_matches_device(title.device, device.product, self._display_name)
        if matches is False:
            self._record(
                f"warning: the simulator title names {title.device!r}, which does "
                f"not match {device.product!r}; treat these results as suspect and "
                f"check `LastUsedDevice` in {self._ini_description()}"
            )
            return
        self._record(
            f"device title confirms {title.device!r} for {device.product!r}"
        )

    def stop(self) -> None:
        """Terminate the app and the simulator by PID; keep the X display."""
        self._stop_app()
        self._collect_ciq_log()
        self._stop_simulator()
        self._window = None
        self._started = False

    def runtime_errors(self) -> Sequence[CiqLogError]:
        """Classified simulator runtime errors from the last collected log."""
        return tuple(self._ciq_errors)

    def last_press(self) -> Optional[PressResult]:
        """What the last ``press`` used and, if measured, what it changed."""
        return self._last_press

    def press_measured(
        self,
        button: Button,
        count: int = 1,
        *,
        timeout_s: float = 3.0,
        requests: Optional[Callable[[], Optional[int]]] = None,
    ) -> PressResult:
        """Press a button and measure whether anything changed.

        A no-op is reported as an empty ``effect``, never as success. The
        measurement is a new, different view/ERRORVIEW or a change inside the
        screen rectangle; ``requests``, when given, is a callable returning the
        shim's request count for the report.
        """
        window = self._require_window()
        region = self.screen_region(window)
        shots = self.artifact_dir / "logs"
        shots.mkdir(parents=True, exist_ok=True)
        before = shots / f"press-{button.value.lower()}-before.png"
        if region is not None:
            self.screenshot(before)
        parsed = parse_lines(list(self.events()))
        known = {
            (event.get("category"), event.get("view"))
            for event in parsed
            if event.event == "VIEW"
        }
        requests_before = requests() if requests is not None else None
        mark = len(self.events())
        self.press(button, count)
        base = self._last_press or PressResult(button.value, "unknown", "")

        deadline = time.monotonic() + timeout_s
        difference: Optional[int] = None
        views: List[str] = []
        effect = ""
        while time.monotonic() < deadline:
            for event in parse_lines(list(self.events())[mark:]):
                if event.event == "ERRORVIEW":
                    views.append("ERRORVIEW")
                    effect = f"ERRORVIEW {event.get('id')}"
                    break
                if event.event != "VIEW":
                    continue
                signature = (event.get("category"), event.get("view"))
                if signature not in known and event.get("category") != "glance":
                    views.append(f"{signature[0]}/{signature[1]}")
                    effect = f"view {signature[0]}/{signature[1]}"
                    break
            if effect:
                break
            if region is not None:
                after = shots / f"press-{button.value.lower()}-after.png"
                self.screenshot(after)
                difference = self.region_pixel_difference(before, after, region)
                if difference is not None and difference >= PIXEL_CHANGE_THRESHOLD:
                    effect = f"screen change {difference}px"
                    break
            time.sleep(0.25)

        requests_after = requests() if requests is not None else None
        self._last_press = PressResult(
            button=base.button,
            mechanism=base.mechanism,
            detail=base.detail,
            effect=effect,
            screen_delta=difference,
            views=tuple(views),
            requests=(
                None
                if requests_before is None or requests_after is None
                else max(0, requests_after - requests_before)
            ),
        )
        self._record(f"press measurement: {self._last_press.describe()}")
        return self._last_press

    @property
    def ciq_header(self) -> Mapping[str, str]:
        """The ``CIQ_LOG.YML`` header, e.g. ``Language-Code`` and
        ``Part-Number``; corroborating evidence for the run's environment."""
        return dict(self._ciq_header)

    @property
    def ciq_log(self) -> Optional[Path]:
        """The copied simulator log in the artifacts, once collected."""
        return self._ciq_log

    def press(
        self, button: Button, count: int = 1, *, mechanism: Optional[str] = None
    ) -> None:
        """Press a device button ``count`` times.

        A device definition declares its physical buttons as clickable hit
        areas (``keys[].location`` with a ``behavior``) and the simulator also
        takes keystrokes, so the driver records which mechanism it used: a
        keystroke the simulator ignores is indistinguishable from a successful
        press unless it is recorded. ``mechanism`` forces one path
        (``"mouse"`` or ``"keyboard"``) for the launch sequence's retries; by
        default a proven keysym wins over a click for navigation, then the
        declared hit area, then the bound keysym.
        """
        device = self._require_started()
        if not device.supports_buttons:
            raise DriverError(
                f"device {device.id} has input {device.input!r} and no buttons; "
                "use tap() for this device"
            )
        window = self._require_window()
        self._focus_window(window)

        override = os.environ.get(f"{ENV_KEY_PREFIX}{button.value}")
        if override:
            self._send_keys(
                window, button, override.strip(), count, "environment override"
            )
            return

        if mechanism in ("keyboard", "keyboard-hold"):
            keysym = PROVEN_KEYSYMS.get(button) or self._keys.get(button)
            if not keysym:
                raise DriverError(f"no keysym is bound for {button.value}")
            self._send_keys(
                window,
                button,
                keysym,
                count,
                "forced keyboard path",
                hold=mechanism == "keyboard-hold",
            )
            return

        if mechanism in ("mouse", "mouse-hold"):
            declared = self._definition.key_for(button)
            if declared is None:
                raise DriverError(
                    f"{device.product} declares no hit area for {button.value}"
                )
            self._click_declared_key(
                window, declared, count, hold=mechanism == "mouse-hold"
            )
            return

        proven = PROVEN_KEYSYMS.get(button)
        if proven:
            # The guest proved the simulator honours these keys, and they need
            # no coordinates; the declared hit area is the alternative path.
            self._send_keys(window, button, proven, count, "proven keyboard binding")
            return

        declared = self._definition.key_for(button)
        if declared is not None:
            self._click_declared_key(window, declared, count)
            return

        keysym = self._keys.get(button)
        if not keysym:
            raise DriverError(
                f"no proven keysym, no declared hit area and no key binding for "
                f"{button.value}; set {ENV_KEY_PREFIX}{button.value}=<keysym>"
            )
        self._send_keys(window, button, keysym, count, "no proven binding or hit area")

    def _send_keys(
        self,
        window: str,
        button: Button,
        keysym: str,
        count: int,
        why: str,
        hold: bool = False,
    ) -> None:
        """Send ``count`` keystrokes, focusing the window before **each** one.

        Focus is not stable between keys: the guest lost it mid-sequence and the
        second key went nowhere, which is indistinguishable from a simulator
        that ignores the key unless the focus is re-established and logged.

        ``hold`` sends ``keydown``/``keyup`` as separate events with a short hold
        instead of one ``key`` tap, for a toolkit that reads key state rather
        than key messages. The exact argv of every command is recorded, because
        ``--window``, ``--clearmodifiers`` or ``--delay`` change what the
        simulator receives and are invisible in a log that only says "a key was
        sent".
        """
        transport = "XTEST key to the focused window"
        for _ in range(max(1, int(count))):
            self._focus_window(window)
            time.sleep(FOCUS_SETTLE_S)
            if hold:
                down = xdotool_keydown_command(keysym)
                up = xdotool_keyup_command(keysym)
                self._run(down, check=True, action=f"press {button.value}")
                self._record(f"command: {shlex.join(down)}")
                time.sleep(KEY_HOLD_S)
                self._run(up, check=True, action=f"release {button.value}")
                self._record(f"command: {shlex.join(up)}")
                transport = "keydown/keyup held 50 ms"
            else:
                command = xdotool_key_command(keysym, 1)
                try:
                    self._run(command, check=True, action=f"press {button.value}")
                    self._record(f"command: {shlex.join(command)}")
                    transport = "XTEST key tap to the focused window"
                except DriverError:
                    command = xdotool_key_to_window_command(window, keysym, 1)
                    self._run(
                        command,
                        check=True,
                        action=f"press {button.value} (window-targeted fallback)",
                    )
                    self._record(f"command: {shlex.join(command)}")
                    transport = "window-targeted key (XSendEvent)"
            time.sleep(DEFAULT_KEY_DELAY_MS / 1000.0)
        mechanism = (
            "keyboard (override)" if why == "environment override" else "keyboard"
        )
        self._last_press = PressResult(
            button=button.value, mechanism=mechanism, detail=f"keysym {keysym}"
        )
        self._record(
            f"press {button.value} x{count} -> keysym {keysym!r} ({transport}; "
            f"{why}; window focused before each key)"
        )

    def _click_declared_key(
        self, window: str, key: SimulatorKey, count: int, hold: bool = False
    ) -> None:
        geometry = self._geometry(window)
        mapper = self._mapper()
        x, y = mapper.point(key.location)
        if not (0 <= x < geometry["width"] and 0 <= y < geometry["height"]):
            raise DriverError(
                f"the declared {key.describe()} maps to window ({x},{y}), outside "
                f"the {geometry['width']}x{geometry['height']} simulator window.\n"
                "The device definition's coordinates and the window disagree; pin "
                f"{ENV_SCREEN_RECT}=x,y,width,height to the screen rectangle from a "
                f"screenshot, or {ENV_KEY_PREFIX}{key.button.value} with a keysym."
            )
        for _ in range(max(1, int(count))):
            if key.is_hold or hold:
                self._pointer_down(
                    window, x, y, action=f"press {key.button.value} (hold)"
                )
                time.sleep((HOLD_MS if key.is_hold else MOUSE_HOLD_MS) / 1000.0)
                self._pointer_up()
            else:
                self._click_at(window, x, y, action=f"press {key.button.value}")
            time.sleep(DEFAULT_KEY_DELAY_MS / 1000.0)
        self._last_press = PressResult(
            button=key.button.value,
            mechanism="mouse",
            detail=f"simulator window {window} at ({x},{y}), pointer verified",
        )
        self._record(
            f"press {key.button.value} x{count} -> clicked {key.describe()} at "
            f"window ({x},{y}) in simulator window {window} ({mapper.source}); "
            "the pointer was verified over that window before the button went down"
        )

    def _mapper(self) -> CoordinateMapper:
        """The one mapper both ``press`` and ``tap`` use."""
        return coordinate_mapper(
            self._definition.display,
            parse_screen_rect(os.environ.get(ENV_SCREEN_RECT, "")),
        )

    # -- pointer and keyboard delivery -------------------------------------
    def _pointer_down(
        self, window: str, x: int, y: int, *, action: str
    ) -> None:
        """Press the pointer at a verified window point, as real X input."""
        self._move_verified(window, x, y, action)
        command = xdotool_mousedown_command()
        self._run(command, check=True, action=action)
        self._record(f"command: {shlex.join(command)}")

    def _pointer_up(self) -> None:
        command = xdotool_mouseup_command()
        self._run(command, check=True, action="release")
        self._record(f"command: {shlex.join(command)}")

    def _click_at(self, window: str, x: int, y: int, *, action: str) -> None:
        """Click a point that the pointer was verified to be over.

        There is deliberately no window-targeted fallback: `xdotool click`
        clicks wherever the pointer is, so a fallback would be exactly the blind
        click this check exists to prevent.
        """
        self._move_verified(window, x, y, action)
        command = xdotool_click_only_command(1)
        self._run(command, check=True, action=action)
        self._record(f"command: {shlex.join(command)}")

    def _move_verified(self, window: str, x: int, y: int, action: str) -> None:
        """Move the pointer and prove it is over the simulator window.

        Clicking blind is how the "silent mouse" verdict was born: on the guest
        the pointer was once over the root window (`xdotool getmouselocation`
        reported the root id) and Wine received no button event at all. The window
        position can change between runs, so the geometry is re-read and the
        move retried once before failing.
        """
        for attempt in (1, 2):
            abs_x, abs_y = self._absolute(window, x, y)
            move = xdotool_mousemove_command(abs_x, abs_y)
            self._run(
                move,
                check=True,
                action=f"{action} (move pointer)",
            )
            self._record(f"command: {shlex.join(move)}")
            location = self._mouse_location()
            if location is None:
                self._record(
                    "warning: xdotool getmouselocation is unavailable, so the "
                    "pointer could not be verified before this click"
                )
                return
            if (
                abs(location["x"] - abs_x) <= 1
                and abs(location["y"] - abs_y) <= 1
            ):
                self._verify_pointer_window(window, location["window"])
                return
            if attempt == 2:
                raise DriverError(
                    f"the pointer did not reach ({abs_x},{abs_y}) for "
                    f"{action}: xdotool reports ({location['x']},{location['y']}).\n"
                    "The simulator window may have moved between reading its "
                    "geometry and moving the pointer; re-run, or check that no "
                    "other client is grabbing the pointer."
                )

    def _mouse_location(self) -> Optional[Dict[str, int]]:
        try:
            completed = self._run(
                xdotool_mouselocation_command(),
                check=False,
                action="xdotool getmouselocation",
            )
        except DriverError:
            return None
        return parse_mouselocation(completed.stdout or "")

    def _verify_pointer_window(self, window: str, pointer_window: int) -> None:
        """Fail unless the pointer is over the simulator, or inside its window.

        Wine creates several X windows for one application process — a splash
        window and the client area were both seen — so a pointer over any of
        them is over the simulator. Accept the same window id, a window owned by
        the simulator's PID, or a window whose rectangle lies inside the
        simulator window's rectangle.
        """
        if str(pointer_window) == str(window):
            return
        root = self._root_window_id()
        if root is not None and str(pointer_window) == root:
            raise DriverError(
                "the pointer never reached the simulator window: "
                f"`xdotool getmouselocation` reports window {pointer_window}, which "
                f"is the root window, not the simulator ({window}).\n"
                "A click would go to the desktop and look like a silent simulator. "
                "Check the window geometry and that the window is mapped and on "
                "this display."
            )
        simulator_pid = self._window_pid(window)
        pointer_pid = self._window_pid(str(pointer_window))
        if (
            simulator_pid is not None
            and pointer_pid is not None
            and simulator_pid == pointer_pid
        ):
            return
        if self._window_inside(window, str(pointer_window)):
            self._record(
                f"note: the pointer is over window {pointer_window}, which lies "
                f"inside the simulator window {window}; that is the simulator's "
                "own child window (Wine creates several per process)"
            )
            return
        raise DriverError(
            f"the pointer is over window {pointer_window}, not the simulator "
            f"window {window} (pid {simulator_pid!r} vs {pointer_pid!r}), and its "
            "rectangle is not inside the simulator's.\n"
            "Refusing to click blind; see tests/docs/simulator-driver.md."
        )

    def _window_inside(self, outer: str, inner: str) -> bool:
        """True when ``inner``'s rectangle lies within ``outer``'s."""
        try:
            outer_geometry = self._geometry(outer)
            inner_geometry = self._geometry(inner)
        except DriverError:
            return False
        ox, oy = outer_geometry.get("x", 0), outer_geometry.get("y", 0)
        ow, oh = outer_geometry.get("width", 0), outer_geometry.get("height", 0)
        ix, iy = inner_geometry.get("x", 0), inner_geometry.get("y", 0)
        iw, ih = inner_geometry.get("width", 0), inner_geometry.get("height", 0)
        if ow <= 0 or oh <= 0 or iw <= 0 or ih <= 0:
            return False
        return ix >= ox and iy >= oy and ix + iw <= ox + ow and iy + ih <= oy + oh

    def _root_window_id(self) -> Optional[str]:
        cached = getattr(self, "_root_id", None)
        if cached is not None:
            return cached
        try:
            completed = self._run(
                xwininfo_root_command(), check=False, action="xwininfo -root"
            )
        except DriverError:
            return None
        self._root_id = parse_root_window_id(completed.stdout or "")
        return self._root_id

    def _window_pid(self, window: str) -> Optional[int]:
        try:
            completed = self._run(
                xdotool_window_pid_command(window),
                check=False,
                action="xdotool getwindowpid",
            )
        except DriverError:
            return None
        value = (completed.stdout or "").strip()
        return int(value) if value.isdigit() else None

    def _absolute(self, window: str, x: int, y: int) -> Tuple[int, int]:
        geometry = self._geometry(window)
        return geometry.get("x", 0) + int(x), geometry.get("y", 0) + int(y)

    def tap(self, x: int, y: int, count: int = 1) -> None:
        """Tap a device screen coordinate ``count`` times."""
        device = self._require_started()
        if not device.supports_touch:
            raise DriverError(
                f"device {device.id} has input {device.input!r} and no touch screen; "
                "use press() for this device"
            )
        window = self._require_window()
        self._focus_window(window)
        mapper = self._mapper()
        rect = self._screen_rect_for(window, device)
        if mapper.display is not None:
            px, py = mapper.screen_point(
                x, y, (device.screen_width, device.screen_height)
            )
        else:
            px, py = window_point(
                x, y, rect, (device.screen_width, device.screen_height)
            )
        for _ in range(max(1, int(count))):
            self._click_at(window, px, py, action="tap")
            time.sleep(DEFAULT_KEY_DELAY_MS / 1000.0)
        self._last_press = PressResult(
            button="TAP",
            mechanism="mouse",
            detail=f"simulator window {window} at ({px},{py}), pointer verified",
        )
        self._record(
            f"tap device=({x},{y}) window=({px},{py}) x{count} "
            f"screen={rect.x},{rect.y},{rect.width},{rect.height} "
            f"in simulator window {window}; the pointer was verified before each click"
        )

    def screenshot(self, path: Path) -> Path:
        """Capture the simulator window (never the root window) as a PNG.

        The pointer is parked first: the simulator's status strip prints the
        pointer position, so a capture taken wherever the mouse happened to be
        would differ for a reason unrelated to the app. Measured on the guest
        with a whole-window capture: moving the pointer changes 466 pixels
        (0.19% of a 409x600 window); the same state with a parked pointer drifts
        by ~157 pixels (0.06%) because the strip's live counters tick.
        """
        window = self._require_window()
        self._park_pointer(window)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        completed = self._run(
            import_command(self.display, window, target),
            check=False,
            action="screenshot",
        )
        if completed.returncode != 0 or not target.is_file():
            raise DriverError(
                f"ImageMagick could not capture window {window} on {self.display}: "
                f"exit {completed.returncode}\n{tail((completed.stderr or '').splitlines())}\n"
                "Capture the simulator window, not the root window: a root capture "
                "on a display without a window manager is a single colour."
            )
        reason = png_problem(target)
        if reason:
            raise DriverError(f"{target}: {reason}")
        self._record(f"screenshot -> {target}")
        return target

    def _park_pointer(self, window: str) -> None:
        """Move the pointer to a fixed window point so captures are repeatable.

        The status strip prints the pointer position, so leaving the pointer
        wherever a press left it makes two captures of the same view differ.
        Parking is best effort: a failure is recorded, never fatal, because the
        capture itself is still valid and the tolerance absorbs the difference.
        """
        completed = self._run(
            xdotool_mousemove_window_command(window, *POINTER_PARK_POINT),
            check=False,
            action="park the pointer for a deterministic capture",
        )
        if completed.returncode != 0:
            self._record(
                "warning: the pointer could not be parked before the capture, so "
                "the status strip may print a different pointer position"
            )
            return
        self._record(f"pointer parked at {POINTER_PARK_POINT} in window {window}")

    def pixel_difference(self, before: Path, after: Path) -> Optional[int]:
        """Absolute pixel difference between two captures, or ``None``.

        ``None`` means ImageMagick's ``compare`` is unavailable or the metric
        could not be read; callers must then rely on the trace stream instead.
        """
        compare = shutil.which("compare")
        if compare is None:
            return None
        try:
            completed = self._run(
                compare_command(Path(before), Path(after)),
                check=False,
                timeout=60,
                action="compare",
            )
        except DriverError:
            return None
        match = re.search(r"(-?\d+(?:\.\d+)?)", completed.stderr or "")
        return int(float(match.group(1))) if match else None

    def probe_input_delivery(self, timeout_s: float = 8.0) -> str:
        """Prove a bezel press reaches the app, measured on the app's screen.

        The guest's simulator has no menu bar, so a menu accelerator cannot be
        the probe. This presses the declared ``onSelect`` area once and requires
        a new ``E2E:VIEW``/``ERRORVIEW`` or a change inside the declared
        ``display.location`` rectangle. Measuring the screen, never the whole
        window, keeps the simulator's own bezel graphics from counting.
        """
        if shutil.which("compare") is None:
            raise DriverError(
                "ImageMagick 'compare' is not installed, so input delivery cannot "
                "be proved with pixels. Install ImageMagick in the runner image."
            )
        window = self._require_window()
        self._focus_window(window)
        region = self.screen_region(window)
        if region is None:
            raise DriverError(
                "no display.location in the device definition, so a press effect "
                "cannot be measured on the screen; add the definition or set "
                f"{ENV_SCREEN_RECT}"
            )
        before = self.artifact_dir / "logs" / "input-probe-before.png"
        after = self.artifact_dir / "logs" / "input-probe-after.png"
        self.screenshot(before)
        mark = len(self.events())
        self.press(Button.ENTER, 1)
        deadline = time.monotonic() + timeout_s
        difference: Optional[int] = None
        while time.monotonic() < deadline:
            for event in parse_lines(list(self.events())[mark:]):
                if event.event in ("VIEW", "ERRORVIEW"):
                    return (
                        f"the declared {Button.ENTER.value} press produced "
                        f"{event.describe()}, so input reaches the app"
                    )
            self.screenshot(after)
            difference = self.region_pixel_difference(before, after, region)
            if difference is not None and difference >= PIXEL_CHANGE_THRESHOLD:
                return (
                    f"the declared {Button.ENTER.value} press changed the screen "
                    f"area by {difference} px, so input reaches the app"
                )
            time.sleep(0.25)
        raise DriverError(
            "the declared select press changed neither a view nor the screen area "
            f"({region.x},{region.y} {region.width}x{region.height}; last pixel "
            f"difference {difference!r}).\n"
            "The press was clicked inside the window, so this is either delivery "
            "to the app or the app not reacting: check `logs/driver.log` for the "
            "clicked point, whether the app is stuck on the glance page (a widget "
            "started in glance mode has to be selected to launch), and the "
            "focus/delivery notes in tests/docs/simulator-driver.md."
        )

    def screen_region(self, window: Optional[str] = None) -> Optional[ScreenRect]:
        """The declared screen rectangle in window pixels, for pixel checks."""
        mapper = self._mapper()
        if mapper.display is None:
            return None
        return mapper.rect(mapper.display)

    def region_pixel_difference(
        self,
        before: Path,
        after: Path,
        region: ScreenRect,
    ) -> Optional[int]:
        """Pixel difference restricted to ``region``.

        The simulator highlights its own bezel buttons; a whole-window
        difference would count that and could pass while the app never reacted.
        """
        convert = shutil.which("convert")
        if convert is None:
            return self.pixel_difference(before, after)
        crops: List[Path] = []
        for label, source in (("crop-before", before), ("crop-after", after)):
            target = self.artifact_dir / "logs" / f"input-{label}.png"
            self._run(
                crop_command(Path(source), region, target),
                check=True,
                action="crop for the screen difference",
            )
            crops.append(target)
        return self.pixel_difference(crops[0], crops[1])

    def events(self) -> Sequence[str]:
        """The app console lines seen so far, oldest first."""
        return tuple(line for _, line in self.timed_events())

    def timed_events(self) -> Sequence[Tuple[float, str]]:
        """Console lines with the monotonic receipt timestamp."""
        with self._events_lock:
            return tuple(self._events)

    # -- display -----------------------------------------------------------
    def _sweep_existing_simulator(self) -> None:
        """Stop a simulator left behind by an earlier run.

        The simulator is single-instance: a second ``simulator.exe`` exits
        silently and ``monkeydo`` then waits forever with empty output, which
        looks like a launch failure and burns the whole readiness budget.
        ``wineserver -k`` is used deliberately instead of ``pkill -f <pattern>``,
        which would also match the ``docker exec`` that runs it; the prefix is
        this driver's own, so no unrelated process is affected. The sweep is
        best effort and never fails a run.
        """
        stale: List[str] = []
        try:
            stale = self._find_windows() + self._error_windows()
        except DriverError:
            stale = []
        wineserver = shutil.which("wineserver")
        if wineserver is None:
            if stale:
                self._record(
                    f"warning: a simulator is already running (window(s) {stale}) "
                    "and wineserver is not installed, so it cannot be stopped"
                )
            return
        try:
            self._run(
                [wineserver, "-k"], check=False, timeout=30, action="wineserver -k"
            )
        except DriverError as error:
            self._record(
                f"warning: could not stop a leftover Wine server: "
                f"{str(error).splitlines()[0]}"
            )
            return
        deadline = time.monotonic() + 15.0
        while stale and time.monotonic() < deadline:
            try:
                if not (self._find_windows() + self._error_windows()):
                    break
            except DriverError:
                break
            time.sleep(0.25)
        if stale:
            self._record(
                f"a simulator was already running (window(s) {stale}); stopped it "
                "with wineserver -k before starting a new one"
            )

    def _ensure_display(self) -> None:
        completed = self._run(
            xdpyinfo_command(self.display), check=False, action="xdpyinfo"
        )
        if completed.returncode != 0:
            raise DriverError(
                f"no X display is answering on {self.display!r}: xdpyinfo exited "
                f"{completed.returncode}.\n"
                f"{_STAGE_HINTS['display'].format(display=self.display)}\n"
                f"{tail((completed.stderr or '').splitlines(), 6)}"
            )
        if not self._display_checked:
            self._record(f"display {self.display} is ready")
            self._display_checked = True

    # -- simulator.ini -----------------------------------------------------
    def _user_candidates(self) -> List[str]:
        return list(
            dict.fromkeys(
                [
                    os.environ.get("WINEUSERNAME") or "",
                    os.environ.get("USER") or "",
                    "root",
                ]
            )
        )

    def _ini_candidates(self) -> List[Path]:
        candidates: List[Path] = []
        for user in (user for user in self._user_candidates() if user):
            for hint in INI_HINTS:
                candidates.append(self.prefix / hint.format(user=user))
        return candidates

    def _write_simulator_ini(
        self, device: Device, mode: str, language_value: Optional[str]
    ) -> None:
        updates = ini_updates(device.product, mode, language_value, night=self._night)
        existing = next((path for path in self._ini_candidates() if path.is_file()), None)
        created = existing is None
        path = existing or self._ini_candidates()[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        update = update_ini(text, updates, remove=ini_removals(language_value))
        path.write_text(update.text, encoding="utf-8")
        self._ini_path = path
        self._ini_summary = update
        language_note = (
            f"Language={language_value}"
            if language_value is not None
            else "Language removed (simulator default)"
        )
        self._record(
            f"simulator.ini {path}: "
            + ("created" if created else "rewrote")
            + f" written={list(update.written)} appended={list(update.appended)} "
            + f"removed={list(update.removed)}; {language_note} "
            + f"({HTTPS_REQUIREMENTS_KEY}={HTTPS_REQUIREMENTS_OFF})"
        )

    def _ini_description(self) -> str:
        if self._ini_path is None:
            return "<not written>"
        if self._ini_summary is None:
            return str(self._ini_path)
        return (
            f"{self._ini_path} (written={list(self._ini_summary.written)}, "
            f"appended={list(self._ini_summary.appended)}, "
            f"removed={list(self._ini_summary.removed)})"
        )

    # -- simulator ---------------------------------------------------------
    def _start_simulator(self) -> None:
        wine = self._wine()
        command = simulator_command(wine, require_simulator(self.layout))
        self.simulator_log.parent.mkdir(parents=True, exist_ok=True)
        self._simulator_handle = self.simulator_log.open("w", encoding="utf-8")
        self._simulator_process = subprocess.Popen(
            command,
            stdout=self._simulator_handle,
            stderr=subprocess.STDOUT,
            env=self._wine_env(),
        )
        self._record(
            f"simulator PID {self._simulator_process.pid}: {shlex.join(command)}"
        )
        self._await_simulator_window()
        self._await_simulator_transport()

    def _await_simulator_transport(self) -> None:
        """Wait until the simulator accepts TCP connections from ``monkeydo``.

        The window can exist before the transport is listening. A closed port
        is only a warning: the launch that follows fails with its own,
        more precise message, and this must not mask it.
        """
        deadline = time.monotonic() + SIMULATOR_TRANSPORT_TIMEOUT
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(
                    (SIMULATOR_TCP_HOST, SIMULATOR_TCP_PORT), timeout=0.5
                ):
                    self._record(
                        f"simulator transport {SIMULATOR_TCP_HOST}:"
                        f"{SIMULATOR_TCP_PORT} accepts connections"
                    )
                    return
            except OSError:
                time.sleep(0.25)
        self._record(
            f"warning: {SIMULATOR_TCP_HOST}:{SIMULATOR_TCP_PORT} did not accept a "
            f"connection within {SIMULATOR_TRANSPORT_TIMEOUT:.0f}s; launching anyway"
        )

    def _await_simulator_window(self) -> None:
        """Wait until the simulator has *a* window.

        The generic title ``CIQ Simulator`` is the correct readiness signal for
        the process: the title only names the device once ``monkeydo`` pushes
        the app, which happens after this returns. Waiting for the device name
        here deadlocks for the full startup timeout and reports the wrong
        cause, so the device name is only corroboration in :meth:`wait_ready`.
        """
        timeout = _float_env(ENV_STARTUP_TIMEOUT, DEFAULT_STARTUP_TIMEOUT)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            process = self._simulator_process
            if process is not None and process.poll() is not None:
                raise DriverError(
                    f"the simulator exited with code {process.returncode} before "
                    "its window appeared.\n"
                    f"log: {self.simulator_log}\n"
                    f"{_STAGE_HINTS['simulator-window']}"
                )
            for window in self._find_windows():
                name = self._window_name(window) or ""
                title = parse_window_title(name)
                if title.error:
                    raise DriverError(
                        f"the simulator opened an error dialog ({name!r}).\n"
                        f"{_STAGE_HINTS['simulator-window']}\n"
                        f"log: {self.simulator_log}"
                    )
                self._window = window
                self._record(
                    f"simulator window {window}: {name!r} ({title.describe()}); "
                    "the device name appears once monkeydo pushes the app"
                )
                return
            if self._error_windows():
                raise DriverError(
                    "the simulator opened an error dialog instead of its main "
                    "window.\n"
                    f"{_STAGE_HINTS['simulator-window']}\n"
                    f"log: {self.simulator_log}"
                )
            time.sleep(0.5)

        raise timeout_error(
            "simulator-window",
            timeout,
            display=self.display,
            details=[
                "no window titled `CIQ Simulator` appeared",
                f"log: {self.simulator_log}",
            ],
        )

    def _find_windows(self) -> List[str]:
        completed = self._run(
            xdotool_search_command(), check=False, action="xdotool search"
        )
        if completed.returncode != 0:
            return []
        windows: List[str] = []
        for window in (completed.stdout or "").split():
            if window and window not in windows:
                windows.append(window)
        return windows

    def _error_windows(self) -> List[str]:
        """Visible windows titled ``Error``: the simulator's missing-device dialog."""
        completed = self._run(
            xdotool_error_search_command(), check=False, action="xdotool search"
        )
        if completed.returncode != 0:
            return []
        return [window for window in (completed.stdout or "").split() if window]

    def _window_name(self, window: str) -> Optional[str]:
        completed = self._run(
            xdotool_name_command(window), check=False, action="xdotool getwindowname"
        )
        if completed.returncode != 0:
            return None
        return (completed.stdout or "").strip() or None

    def _require_window(self) -> str:
        if self._window and self._window_name(self._window):
            return self._window
        windows = self._find_windows()
        for window in windows:
            title = parse_window_title(self._window_name(window) or "")
            if title.loaded:
                self._window = window
                return self._window
        if windows:
            self._window = windows[0]
            return self._window
        raise DriverError(
            "the simulator window is gone; it was closed or the simulator exited.\n"
            f"log: {self.simulator_log}"
        )

    def _focus_window(self, window: str) -> None:
        """Focus ``window`` before sending input.

        Xvfb runs without a window manager, so ``windowactivate`` (which needs
        ``_NET_ACTIVE_WINDOW``) can fail; ``windowfocus`` uses XSetInputFocus
        and works without one. If focus cannot be taken, key events would go to
        whatever else has focus, so the driver fails instead of pressing keys
        into the void.
        """
        activate, focus = xdotool_focus_commands(window)
        if not getattr(self, "_activate_attempted", False):
            # windowactivate needs a window manager; try it once for the session,
            # then use windowfocus only, which works on bare Xvfb.
            self._activate_attempted = True
            activated = self._run(
                activate, check=False, action="xdotool windowactivate"
            )
            if activated.returncode != 0:
                self._record(
                    "note: xdotool windowactivate failed (no window manager on "
                    "Xvfb); windowfocus succeeds, which is enough for input"
                )
        focused = self._run(focus, check=False, action="xdotool windowfocus")
        if focused.returncode != 0:
            raise DriverError(
                f"window {window} could not be focused, so synthetic input would "
                "land on the wrong window.\n"
                f"xdotool windowfocus exited {focused.returncode}: "
                f"{tail((focused.stderr or '').splitlines(), 4)}\n"
                "Check that the display has a focusable window "
                f"(DISPLAY={self.display}) and that no other client grabs input."
            )
    def _geometry(self, window: str) -> Dict[str, int]:
        completed = self._run(
            xdotool_geometry_command(window),
            check=False,
            action="xdotool getwindowgeometry",
        )
        geometry = parse_window_geometry(completed.stdout or "")
        if geometry is None:
            raise DriverError(
                f"could not read the geometry of window {window}:\n"
                f"{tail((completed.stderr or '').splitlines(), 4)}"
            )
        return geometry

    def _screen_rect_for(self, window: str, device: Device) -> ScreenRect:
        """The watch screen inside the simulator window, for ``tap`` and its log.

        Precedence: the device definition's ``display.location`` through the
        shared mapper (an ``E2E_WINE_SCREEN_RECT`` pin re-anchors it), then a
        centred aspect-preserving fit for an SDK that ships no
        ``simulator.json``.
        """
        mapper = self._mapper()
        if mapper.display is not None:
            return mapper.rect(mapper.display)
        pinned = parse_screen_rect(os.environ.get(ENV_SCREEN_RECT, ""))
        if pinned is not None:
            return pinned
        self._record(
            "note: no display.location in the device definition; falling back to a "
            "centred screen rectangle. Pin E2E_WINE_SCREEN_RECT if taps miss."
        )
        return screen_rect(
            self._geometry(window), (device.screen_width, device.screen_height)
        )

    def _stop_simulator(self) -> None:
        self._terminate(self._simulator_process, "simulator")
        self._simulator_process = None
        if self._simulator_handle is not None:
            try:
                self._simulator_handle.close()
            except OSError:
                pass
            self._simulator_handle = None

    # -- app launch --------------------------------------------------------
    def _launch_app(
        self, device: Device, *, reset_events: bool = True, append_log: bool = False
    ) -> None:
        monkeydo = self.layout.monkeydo
        if monkeydo is None:  # guarded by prepare()
            raise DriverError("monkeydo is missing from the SDK layout")
        assert self._prg is not None
        stdbuf = shutil.which("stdbuf")
        command = monkeydo_command(monkeydo, self._prg, device.product, stdbuf=stdbuf)

        if reset_events:
            with self._events_lock:
                self._events = []
        self.console_log.parent.mkdir(parents=True, exist_ok=True)
        self._console_handle = self.console_log.open(
            "a" if append_log else "w", encoding="utf-8"
        )
        self._app_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=self._env(),
        )
        self._record(
            f"app PID {self._app_process.pid}: {shlex.join(command)}"
        )
        self._app_started_at = time.monotonic()
        self._app_thread = threading.Thread(target=self._read_console, daemon=True)
        self._app_thread.start()

    def _read_console(self) -> None:
        process = self._app_process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            text = line.rstrip("\n")
            with self._events_lock:
                self._events.append((time.monotonic(), text))
            handle = self._console_handle
            if handle is not None:
                try:
                    handle.write(line)
                    handle.flush()
                except (OSError, ValueError):
                    pass

    def _stop_app(self) -> None:
        self._terminate(self._app_process, "app")
        self._app_process = None
        if self._app_thread is not None:
            self._app_thread.join(timeout=3)
            self._app_thread = None
        if self._console_handle is not None:
            try:
                self._console_handle.close()
            except OSError:
                pass
            self._console_handle = None

    def _wait_for_startup(self, timeout_s: float) -> List[TraceEvent]:
        timeout = timeout_s if timeout_s > 0 else DEFAULT_READY_TIMEOUT
        deadline = time.monotonic() + min(timeout, DEFAULT_READY_TIMEOUT * 2)
        while time.monotonic() < deadline:
            process = self._app_process
            if process is not None and process.poll() is not None:
                raise DriverError(
                    f"monkeydo exited with code {process.returncode} before the app "
                    "rendered its first view.\n"
                    f"{_STAGE_HINTS['app-launch']}\n"
                    f"log: {self.console_log}\n"
                    f"{tail(self.events(), 12)}"
                )
            parsed = parse_lines(self.events())
            # The full-screen error view also proves the app rendered: a
            # configuration error (or any error before the first menu) shows
            # ERRORVIEW instead of a VIEW line.
            has_view = any(event.event in ("VIEW", "ERRORVIEW") for event in parsed)
            has_language = any(event.event == "LANGUAGE" for event in parsed)
            if has_view and has_language:
                return parsed
            time.sleep(0.25)

        lines = [line for line in self.events() if line.strip()]
        if not lines:
            raise DriverError(
                "app-ready: monkeydo produced no console output at all within "
                f"{min(timeout, DEFAULT_READY_TIMEOUT * 2):g}s, so the app never "
                "started.\n"
                "This is a launch or packaging problem for this device, not the "
                "glance transition: a device that starts the app prints at least "
                "`E2E:LANGUAGE` even when it then stays on the glance.\n"
                "What to check, in order: logs/console.log for monkeydo's own "
                "error; that the PRG was built for this device "
                "(`openhab-garmin-<device>.prg`); the PRG size against the "
                "device's memory limit; then launch it by hand with "
                "`monkeydo <prg> <device>`.\n"
                f"log: {self.console_log}"
            )
        if not any("E2E:" in line for line in lines):
            raise DriverError(
                "app-ready: the console has output but no `E2E:` line within "
                f"{min(timeout, DEFAULT_READY_TIMEOUT * 2):g}s, so the launched "
                "binary is probably not this E2E build (a release build does not "
                "print the trace).\n"
                f"log: {self.console_log}\n"
                f"{tail(lines, 12)}"
            )
        raise timeout_error(
            "app-ready",
            min(timeout, DEFAULT_READY_TIMEOUT * 2),
            display=self.display,
            details=[
                f"log: {self.console_log}",
                "console tail:",
                tail(self.events(), 12),
            ],
        )

    # -- application properties -------------------------------------------
    def _app_data_dirs(self) -> List[Path]:
        """Existing per-app storage directories in this Wine prefix."""
        found: List[Path] = []
        for user in (user for user in self._user_candidates() if user):
            for hint in APP_DATA_HINTS:
                path = self.prefix / hint.format(user=user)
                if path.is_dir():
                    found.append(path)
        return found

    def clear_app_data(self) -> List[str]:
        """Delete the app's cached sitemap and settings before a launch.

        The app persists the last sitemap JSON in its own storage
        (``SitemapStore``: ``sitemapJson``/``sitemapLabel``) under
        ``GARMIN/APPS/DATA``. That cache survives a scenario, so without this a
        scenario inherits the previous scenario's sitemap, logs
        ``E2E:SITEMAP;result=unchanged`` and never requests its own page — which
        is exactly how a run looked like a navigation or protocol failure.
        ``LOGS`` is left alone; ``CIQ_LOG.YML`` is the crash evidence.

        Returns the paths that were removed. ``E2E_WINE_KEEP_APP_DATA=1`` skips
        the clear, for debugging the cache itself.
        """
        if os.environ.get(ENV_KEEP_APP_DATA, "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            self._record(
                f"{ENV_KEEP_APP_DATA} is set: keeping the app's cached sitemap "
                "and settings across launches"
            )
            return []
        removed: List[str] = []
        for path in self._app_data_dirs():
            try:
                shutil.rmtree(path)
            except OSError as error:
                self._record(f"warning: could not clear {path}: {error}")
                continue
            removed.append(str(path))
        if removed:
            self._record(
                "cleared the app's persistent storage before launch "
                f"({len(removed)} path(s), LOGS untouched): "
                + ", ".join(removed)
            )
        else:
            self._record(
                "no app storage to clear before launch (first run in this prefix)"
            )
        return removed

    def relaunch_app(
        self, *, clear_data: bool = True, timeout_s: float = DEFAULT_READY_TIMEOUT
    ) -> bool:
        """Stop the app, optionally clear its storage, and launch it again.

        Used by the contract's isolation case: a second launch must request its
        sitemap instead of reporting ``result=unchanged`` from the cache.
        """
        device = self._device
        if device is None or self._prg is None:
            raise DriverError("relaunch_app() needs a started session")
        self._record(
            "relaunching the app"
            + (" with its persistent storage cleared" if clear_data else " keeping its storage")
        )
        mark = len(self.events())
        self._stop_app()
        if clear_data:
            self.clear_app_data()
        time.sleep(1.0)
        self._launch_app(device, reset_events=False, append_log=True)
        self._started = True
        try:
            self.wait_ready(min(timeout_s, DEFAULT_READY_TIMEOUT))
        except DriverError as error:
            self._record(f"relaunched app did not become ready: {error}")
            return False
        for event in parse_lines(list(self.events())[mark:]):
            if event.event == "SITEMAP":
                self._record(
                    f"relaunch sitemap result: {event.get('result')} "
                    f"(code={event.get('code')})"
                )
                return event.get("result") != "unchanged"
        return False

    def _seed_settings(self, settings: AppSettings) -> None:
        """Write an explicit properties file only when one is requested.

        Application properties are built into the PRG: ``e2e build`` writes a
        ``settings.xml`` resource override and asserts the defaults ``monkeyc``
        emitted, so the default path here does nothing. The escape hatch exists
        for a PRG built by other means, and it must still be read back.
        """
        override = os.environ.get(ENV_SETTINGS_FILE)
        if override:
            self._write_settings(Path(override), settings)
            self._record(
                f"{ENV_SETTINGS_FILE} is set, so the properties were written to a "
                "file as well; the compiled defaults are the normal route"
            )
            return
        self._settings_file = None
        self._record(
            "app properties come from the build (settings.xml resource override); "
            "no simulator-side properties file is written"
        )

    def _write_settings(self, path: Path, settings: AppSettings) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        document = parse_properties(text)
        merged: Dict[str, Any] = dict(document.properties)
        merged.update(dict(settings.properties))
        path.write_text(
            render_properties(
                merged,
                fmt=document.fmt,
                body=document.body,
                container=document.container,
            ),
            encoding="utf-8",
        )
        back = parse_properties(path.read_text(encoding="utf-8", errors="replace"))
        missing = [
            name
            for name, value in settings.properties.items()
            if back.properties.get(name) != value
        ]
        if missing:
            raise DriverError(
                f"writing {path} did not round-trip these properties: {missing}.\n"
                f"format={document.fmt}; the simulator must be able to read the "
                "file the driver wrote."
            )
        self._settings_file = path
        self._record(
            f"seeded {len(settings.properties)} app property/properties into {path} "
            f"({document.fmt}) as fixture {settings.name!r}"
        )

    # -- simulator runtime log ---------------------------------------------
    def _ciq_log_source(self) -> Optional[Path]:
        for user in (user for user in self._user_candidates() if user):
            candidate = self.prefix / CIQ_LOG_HINT.format(user=user)
            if candidate.is_file():
                return candidate
        for candidate in self.prefix.glob(
            "drive_c/users/*/Temp/com.garmin.connectiq/GARMIN/APPS/LOGS/CIQ_LOG.YML"
        ):
            return candidate
        return None

    def _collect_ciq_log(self) -> None:
        """Copy and classify ``CIQ_LOG.YML``, the only place runtime errors appear.

        The ``E2E:`` stream only carries what the app prints; a Monkey C stack
        from a crash, a missing symbol or a bad font lives here. Classifying it
        is what turns "the screenshot looked fine" into an honest result.
        """
        source = self._ciq_log_source()
        if source is None:
            self._record("no CIQ_LOG.YML in the prefix; nothing to classify")
            return
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            self._record(f"warning: cannot read {source}: {error}")
            return
        target = self.artifact_dir / "logs" / "CIQ_LOG.YML"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            self._ciq_log = target
        except OSError as error:
            self._record(f"warning: cannot copy {source} to {target}: {error}")

        header, errors = parse_ciq_log(text)
        self._ciq_header = header
        self._ciq_errors = list(errors)
        if header:
            self._record(
                "CIQ log header: "
                + ", ".join(f"{key}={value}" for key, value in sorted(header.items()))
            )
        if not errors:
            self._record(f"CIQ log collected from {source}: no classified runtime error")
            return
        for error in errors:
            self._record(
                f"CIQ runtime error [{error.kind}]: {error.label} — {error.message}"
            )
        summary = self.artifact_dir / "logs" / "ciq-log-errors.txt"
        try:
            summary.write_text(
                "\n".join(
                    f"{error.kind}\t{error.label}\t{error.message}" for error in errors
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- helpers -----------------------------------------------------------
    def _wine(self) -> str:
        wine = os.environ.get(WINE_ENV) or shutil.which("wine")
        if not wine:
            raise DriverError(
                "wine is not installed (or set E2E_WINE_BINARY to its path); "
                "the Windows simulator cannot start without it."
            )
        return wine

    def _env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env["DISPLAY"] = self.display
        return env

    def _wine_env(self) -> Dict[str, str]:
        env = self._env()
        env.update(
            {
                "WINEPREFIX": str(self.prefix),
                "WINEARCH": "win32",
                "WINEDEBUG": "-all",
                "WINEDLLOVERRIDES": "mscoree,mshtml=",
            }
        )
        return env

    def _require_tool(self, name: str) -> None:
        if shutil.which(name) is not None:
            return
        if name == "wine" and os.environ.get(WINE_ENV):
            return
        raise DriverError(
            f"{name} is not installed; the Wine driver needs wine, xdotool, "
            "xdpyinfo and ImageMagick (`import`). Install them in the runner "
            "image (tests/runner/Dockerfile)."
        )

    def _require_started(self) -> Device:
        if not self._started or self._device is None:
            raise DriverError("start() must run before device input")
        return self._device

    def _run(
        self,
        command: Sequence[str],
        *,
        check: bool = False,
        timeout: float = 30.0,
        action: str = "",
    ) -> subprocess.CompletedProcess:
        try:
            completed = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._env(),
                check=False,
            )
        except FileNotFoundError:
            raise DriverError(
                f"{command[0]} is not installed; the Wine driver needs it. "
                "See tests/docs/simulator-driver.md."
            ) from None
        except subprocess.TimeoutExpired:
            raise DriverError(
                f"`{shlex.join(list(command))}` did not finish within {timeout:.0f}s "
                f"({action or 'command'})."
            ) from None
        if check and completed.returncode != 0:
            raise DriverError(
                f"`{shlex.join(list(command))}` failed with exit "
                f"{completed.returncode} ({action or 'command'}):\n"
                f"{tail((completed.stderr or completed.stdout or '').splitlines(), 8)}"
            )
        return completed

    def _terminate(self, process: Optional[subprocess.Popen], label: str) -> None:
        if process is None:
            return
        pid = process.pid
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._record(f"{label} PID {pid} did not exit after SIGKILL")
        self._record(f"stopped {label} PID {pid} (exit {process.returncode})")

    def _record(self, message: str) -> None:
        self._log(message)
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n")
        except OSError:
            pass


def png_problem(path: Path) -> str:
    """Return an empty string when ``path`` is a usable PNG, else the reason."""
    if not path.is_file():
        return "file does not exist"
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "missing PNG signature"
    if len(data) < 33 or data[12:16] != b"IHDR":
        return "missing IHDR chunk"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 1 or height <= 1:
        return f"trivial {width}x{height} image; the simulator window was not rendered"
    return ""


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return max(1.0, float(value))
    except ValueError:
        return default
