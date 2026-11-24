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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..config import Device
from ..sdk import (
    SdkLayout,
    find_device_definitions,
    require_simulator,
    wine_prefix,
)
from ..settings import AppSettings
from ..trace import TraceEvent, parse_lines
from .base import Button, DriverError, SimulatorDriver

#: Environment variable that may point at an already installed Wine binary.
WINE_ENV = "E2E_WINE_BINARY"
#: Display the simulator renders on; the entrypoint starts Xvfb here.
DEFAULT_DISPLAY = ":99"
#: Explicit path of the simulator's application-properties file. Set it when
#: discovery picks the wrong file; the driver then never guesses.
ENV_SETTINGS_FILE = "E2E_WINE_SETTINGS_FILE"
#: ``x,y,width,height`` of the watch screen inside the simulator window. The
#: default is a centred, aspect-preserving fit; pin this when taps miss.
ENV_SCREEN_RECT = "E2E_WINE_SCREEN_RECT"
#: Prefix for per-button X keysym overrides, e.g. ``E2E_WINE_KEY_MENU=ctrl+m``.
ENV_KEY_PREFIX = "E2E_WINE_KEY_"
#: Startup and readiness budgets in seconds.
ENV_STARTUP_TIMEOUT = "E2E_WINE_STARTUP_TIMEOUT"
DEFAULT_STARTUP_TIMEOUT = 120.0
DEFAULT_READY_TIMEOUT = 90.0
#: Delay between repeated xdotool events, so the app can process each one.
DEFAULT_KEY_DELAY_MS = 150
#: How long a discovery launch may run while looking for the properties file.
DISCOVERY_LAUNCH_TIMEOUT = 25.0
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

#: The app starts in BLE and only leaves it after a request fails
#: (``ConnectionManager``), so a requested mode is consistent with these runtime
#: modes. The driver reports them; it does not assert them.
MODE_CONNECTION_NAMES: Mapping[str, Tuple[str, ...]] = {
    "ble": ("BLE",),
    "wifi": ("WIFI", "WIFI_CHECK_PENDING"),
    "offline": ("OFFLINE",),
}

#: Where the simulator keeps the app-properties file. The exact name and shape
#: differ per simulator build, so the driver discovers it instead of assuming
#: one; ``E2E_WINE_SETTINGS_FILE`` pins it when discovery is ambiguous.
SETTINGS_GLOBS: Tuple[str, ...] = (
    "drive_c/users/*/AppData/Roaming/Garmin/ConnectIQ/Simulator/**/*.SET",
    "drive_c/users/*/AppData/Roaming/Garmin/ConnectIQ/Simulator/**/settings.*",
    "drive_c/users/*/AppData/Roaming/Garmin/ConnectIQ/**/settings.*",
    "drive_c/users/*/AppData/Local/Garmin/ConnectIQ/**/settings.*",
    "drive_c/users/*/Temp/com.garmin.connectiq/**/*.SET",
    "drive_c/users/*/Temp/com.garmin.connectiq/**/settings.*",
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


def xdotool_click_command(
    window: str,
    x: int,
    y: int,
    count: int = 1,
    delay_ms: int = DEFAULT_KEY_DELAY_MS,
) -> List[str]:
    return [
        "xdotool",
        "mousemove",
        "--window",
        window,
        str(int(x)),
        str(int(y)),
        "click",
        "--repeat",
        str(max(1, int(count))),
        "--delay",
        str(max(0, int(delay_ms))),
        "1",
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


def read_device_keys(devices_root: Optional[Path], product: str) -> Mapping[str, Any]:
    """The ``keys`` object of ``<devices_root>/<product>/simulator.json``."""
    if devices_root is None:
        return {}
    path = Path(devices_root) / product / "simulator.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, Mapping):
        return {}
    keys = document.get("keys")
    return keys if isinstance(keys, Mapping) else {}


# ---------------------------------------------------------------------------
# pure helpers: simulator.ini
# ---------------------------------------------------------------------------


def ini_updates(product: str, mode: str, language: str) -> Dict[str, str]:
    """Startup keys written before the simulator starts."""
    if mode not in MODE_INI_VALUES:
        raise DriverError(f"unknown mode {mode!r}; expected one of {sorted(MODE_INI_VALUES)}")
    updates: Dict[str, str] = {"LastUsedDevice": product, "Language": language}
    updates.update(MODE_INI_VALUES[mode])
    return updates


@dataclass(frozen=True)
class IniUpdate:
    """Result of rewriting a ``simulator.ini`` document."""

    text: str
    written: Tuple[str, ...] = ()
    appended: Tuple[str, ...] = ()

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(sorted(set(self.written) | set(self.appended)))


def update_ini(text: Optional[str], updates: Mapping[str, str]) -> IniUpdate:
    """Rewrite ``simulator.ini`` in the simulator's own format.

    The file the simulator writes is flat ``key=value`` lines with **no section
    header** (for example ``LastFplVersion=9``, ``BleState=2``). Existing keys
    are replaced in place and keep their original spelling, every unrelated key
    is preserved because the simulator rewrites the file itself, and a missing
    key is appended at the end rather than under an invented section.
    """
    lines = (text or "").splitlines()
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
        self._mode = "ble"
        self._language = "eng"
        self._display_name = ""
        self._ini_path: Optional[Path] = None
        self._ini_summary: Optional[IniUpdate] = None
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

    def start(
        self,
        device: Device,
        settings: AppSettings,
        mode: str = "ble",
        language: str = "eng",
    ) -> None:
        """Start the simulator for ``device`` and launch the app."""
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
        devices_root = find_device_definitions(self.layout.root)
        self._display_name = read_device_display_name(devices_root, device.product)
        self._keys = key_bindings(read_device_keys(devices_root, device.product))

        self._ensure_display()
        self._write_simulator_ini(device, mode, language)
        self._start_simulator()
        self._seed_settings(device, settings)
        self._launch_app(device)
        self._started = True

    def wait_ready(self, timeout_s: float = DEFAULT_READY_TIMEOUT) -> None:
        """Wait for the first view and verify the language the app reported."""
        if not self._started or self._app_process is None:
            raise DriverError("wait_ready() called before start()")

        timeout = timeout_s if timeout_s and timeout_s > 0 else DEFAULT_READY_TIMEOUT
        parsed = self._wait_for_startup(timeout)
        language = next((event for event in parsed if event.event == "LANGUAGE"), None)
        observed = language.get("code") if language is not None else "unknown"
        if observed != self._language:
            raise DriverError(
                f"the app reported system language {observed!r} but the scenario "
                f"requested {self._language!r}.\n"
                f"simulator.ini: {self._ini_description()}\n"
                "The simulator applies `Language` when it starts; if it ignored "
                "the key, the language scenarios cannot run through this driver "
                "yet (the Connection/Simulation menus are not scripted). "
                "tests/docs/simulator-driver.md records what is implemented."
            )

        if self._settings is not None and self._settings.url:
            for event in parsed:
                if event.event == "ERRORVIEW" and event.get("id") == "UI:CONFIG":
                    raise DriverError(
                        "the app showed the configuration error view "
                        "(`ERRORVIEW;id=UI:CONFIG`) even though the driver seeded "
                        "a URL.\n"
                        f"seeded file: {self._settings_file}\n"
                        f"properties written: {sorted(self._settings.properties)}\n"
                        "The properties did not reach the app. Check that the file "
                        "is the one the simulator reads (a stale or different file "
                        "is ignored), or pin it with "
                        f"{ENV_SETTINGS_FILE}=<path>.\n"
                        f"searched: {self._settings_search_note()}"
                    )

        modes = [event.get("mode") for event in parsed if event.event == "CONNECTION"]
        self._record(
            f"ready: language={observed} mode(s)={modes or ['<none>']} "
            f"requested mode={self._mode}"
        )
        expected = MODE_CONNECTION_NAMES.get(self._mode, ())
        if expected and modes and not set(modes) & set(expected):
            self._record(
                f"note: the app reports BLE first and changes mode after a request; "
                f"{modes} is expected for requested mode {self._mode!r}. The scenario's "
                "request assertions prove the transition."
            )
        self._record_device_title()

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
        self._stop_simulator()
        self._window = None
        self._started = False

    def press(self, button: Button, count: int = 1) -> None:
        """Press a device button ``count`` times."""
        device = self._require_started()
        if not device.supports_buttons:
            raise DriverError(
                f"device {device.id} has input {device.input!r} and no buttons; "
                "use tap() for this device"
            )
        window = self._require_window()
        self._focus_window(window)
        keysym = self._keys.get(button)
        if not keysym:
            raise DriverError(
                f"no key binding for {button.value}; set "
                f"{ENV_KEY_PREFIX}{button.value}=<keysym>"
            )
        command = xdotool_key_command(window, keysym, count)
        self._run(command, check=True, action=f"press {button.value}")
        self._record(f"press {button.value} x{count} -> {keysym}")

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
        rect = self._resolve_screen_rect(window, device)
        px, py = window_point(x, y, rect, (device.screen_width, device.screen_height))
        command = xdotool_click_command(window, px, py, count)
        self._run(command, check=True, action="tap")
        self._record(
            f"tap device=({x},{y}) window=({px},{py}) x{count} "
            f"screen={rect.x},{rect.y},{rect.width},{rect.height}"
        )

    def screenshot(self, path: Path) -> Path:
        """Capture the simulator window (never the root window) as a PNG."""
        window = self._require_window()
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

    def events(self) -> Sequence[str]:
        """The app console lines seen so far, oldest first."""
        return tuple(line for _, line in self.timed_events())

    def timed_events(self) -> Sequence[Tuple[float, str]]:
        """Console lines with the monotonic receipt timestamp."""
        with self._events_lock:
            return tuple(self._events)

    # -- display -----------------------------------------------------------
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
    def _ini_candidates(self) -> List[Path]:
        users = list(
            dict.fromkeys(
                [
                    os.environ.get("WINEUSERNAME") or "",
                    os.environ.get("USER") or "",
                    "root",
                ]
            )
        )
        candidates: List[Path] = []
        for user in (user for user in users if user):
            for hint in INI_HINTS:
                candidates.append(self.prefix / hint.format(user=user))
        return candidates

    def _write_simulator_ini(self, device: Device, mode: str, language: str) -> None:
        updates = ini_updates(device.product, mode, language)
        existing = next((path for path in self._ini_candidates() if path.is_file()), None)
        created = existing is None
        path = existing or self._ini_candidates()[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        update = update_ini(text, updates)
        path.write_text(update.text, encoding="utf-8")
        self._ini_path = path
        self._ini_summary = update
        self._record(
            f"simulator.ini {path}: "
            + ("created" if created else "rewrote")
            + f" written={list(update.written)} appended={list(update.appended)}"
        )

    def _ini_description(self) -> str:
        if self._ini_path is None:
            return "<not written>"
        if self._ini_summary is None:
            return str(self._ini_path)
        return (
            f"{self._ini_path} (written={list(self._ini_summary.written)}, "
            f"appended={list(self._ini_summary.appended)})"
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
        activated = self._run(activate, check=False, action="xdotool windowactivate")
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
        if activated.returncode != 0:
            self._record(
                "note: xdotool windowactivate failed (no window manager on "
                "Xvfb); windowfocus succeeded, which is enough for input"
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

    def _resolve_screen_rect(self, window: str, device: Device) -> ScreenRect:
        pinned = parse_screen_rect(os.environ.get(ENV_SCREEN_RECT, ""))
        if pinned is not None:
            return pinned
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
    def _launch_app(self, device: Device) -> None:
        monkeydo = self.layout.monkeydo
        if monkeydo is None:  # guarded by prepare()
            raise DriverError("monkeydo is missing from the SDK layout")
        assert self._prg is not None
        stdbuf = shutil.which("stdbuf")
        command = monkeydo_command(monkeydo, self._prg, device.product, stdbuf=stdbuf)

        with self._events_lock:
            self._events = []
        self.console_log.parent.mkdir(parents=True, exist_ok=True)
        self._console_handle = self.console_log.open("w", encoding="utf-8")
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
    def _settings_candidates(self) -> List[Path]:
        override = os.environ.get(ENV_SETTINGS_FILE)
        if override:
            return [Path(override)]
        found: List[Path] = []
        for pattern in SETTINGS_GLOBS:
            try:
                for path in sorted(self.prefix.glob(pattern)):
                    if path.is_file() and path.name != "simulator.ini":
                        found.append(path)
            except (OSError, ValueError):
                continue
        seen: List[Path] = []
        for path in found:
            if path not in seen:
                seen.append(path)
        return seen

    def _settings_search_note(self) -> str:
        patterns = list(SETTINGS_GLOBS)
        return f"{self.prefix} with {patterns}; pin the file with {ENV_SETTINGS_FILE}"

    def _choose_settings_file(
        self, candidates: Sequence[Path], settings: AppSettings
    ) -> Optional[Path]:
        """Pick the app-properties file, preferring one that already holds the
        app's keys so a stray file cannot be rewritten."""
        if not candidates:
            return None
        wanted = set(settings.properties)

        def rank(path: Path) -> Tuple[int, int, str]:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            document = parse_properties(text)
            hits = len(wanted & set(document.properties))
            try:
                modified = int(path.stat().st_mtime)
            except OSError:
                modified = 0
            return (-hits, -modified, str(path))

        return sorted(candidates, key=rank)[0]

    def _seed_settings(self, device: Device, settings: AppSettings) -> None:
        path = self._choose_settings_file(self._settings_candidates(), settings)
        if path is None:
            self._record(
                "no application-properties file found; running one discovery "
                "launch so the simulator creates it"
            )
            self._discovery_launch(device)
            path = self._choose_settings_file(self._settings_candidates(), settings)
        if path is None:
            raise DriverError(
                "no application-properties file was found, so the app would run "
                "without a server URL and show the configuration error view.\n"
                f"searched {self._settings_search_note()}\n"
                "Run the app once in the simulator, open its settings, save them, "
                f"and set {ENV_SETTINGS_FILE} to the file the simulator wrote."
            )
        self._write_settings(path, settings)

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

    def _discovery_launch(self, device: Device) -> None:
        """Launch once so the simulator creates its app-properties file."""
        started = time.monotonic()
        self._launch_app(device)
        deadline = started + DISCOVERY_LAUNCH_TIMEOUT
        while time.monotonic() < deadline:
            if self._settings_candidates():
                break
            process = self._app_process
            if process is not None and process.poll() is not None:
                break
            time.sleep(0.5)
        self._stop_app()
        self._record(
            "discovery launch finished after "
            f"{time.monotonic() - started:.1f}s; "
            f"candidate count={len(self._settings_candidates())}"
        )

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
