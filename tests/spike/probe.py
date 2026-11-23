"""In-container spike procedure for issue #370.

This module runs **inside** the E2E runner image, where Wine, Xvfb, xdotool,
ImageMagick, the Linux ``monkeyc``/``monkeydo`` and the Windows simulator are
available. It works through the issue's procedure and records one
:class:`spike.model.Check` per observation:

1. preflight: Wine, Xvfb/display, xdotool, ImageMagick and the SDK tools;
1. build the debug PRG for a button device and a touch device;
1. start the simulator, launch the PRG with ``monkeydo`` and capture its
   console (``E2E:`` trace lines) with receive timestamps;
1. take a screenshot and verify it is the watch-face resolution;
1. inject navigation keys on the button device and taps on the touch device;
1. drive the ``Simulation`` and ``Connection`` menus and verify the app reacts;
1. switch the simulator language and verify ``E2E:LANGUAGE``;
1. measure polling intervals and the 10-second escalation window;
1. attempt a capture without focus and with the window occluded.

Every step is best-effort on purpose: this is the experiment that decides
whether those interactions are reliable. A capability that does not work is
recorded as ``not-met`` with the evidence that shows what happened; one that
cannot even be attempted is ``skipped``. The module never claims a capability
it did not observe.

The probe is standard-library only so a host with Docker and the SDK needs
nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import MET, NOT_MET, SKIPPED, CheckLog, ProbeDocument, parse_console_line

EXIT_OK = 0
EXIT_CRASH = 1
EXIT_PREREQ = 2

#: Environment variable that may point at an already installed Wine binary.
WINE_ENV = "E2E_WINE_BINARY"

#: Assumed menu item order for the two native menus. The probe presses Down
#: this many times before Return and saves a screenshot of the open menu, so a
#: wrong assumption is visible in the evidence instead of silently doing
#: nothing. Only ``Simulation`` and ``Connection`` are used.
SIMULATION_ITEMS: Tuple[str, ...] = ("active_app", "night_mode", "sleep_mode", "touch", "https")
CONNECTION_ITEMS: Tuple[str, ...] = ("ble", "wifi")

#: Candidate simulator.ini locations relative to a Wine prefix. After the first
#: launch the probe also searches the whole prefix and records what it found.
INI_HINTS = (
    "drive_c/users/{user}/AppData/Local/Garmin/ConnectIQ/Simulator/simulator.ini",
    "drive_c/users/{user}/AppData/Roaming/Garmin/ConnectIQ/Simulator/simulator.ini",
    "drive_c/users/{user}/Temp/com.garmin.connectiq/simulator.ini",
)

#: INI keys corresponding to the #370 state toggles. Only keys that already
#: exist in a discovered file are rewritten.
INI_KEYS: Mapping[str, str] = {
    "night_mode": "NightMode",
    "sleep_mode": "SleepMode",
    "touch": "TouchScreenEnabled",
    "https": "UseHttpsRequirements",
    "ble": "BleState",
    "wifi": "WifiState",
    "language": "Language",
}

#: Tools without which the procedure cannot start at all.
REQUIRED_TOOLS: Tuple[str, ...] = (
    "wine",
    "xdpyinfo",
    "xdotool",
    "import",
    "compare",
    "java",
)
#: Tools that only some steps use. Missing ones turn those steps into
#: ``skipped``; they never abort the spike.
OPTIONAL_TOOLS: Tuple[str, ...] = ("Xvfb", "stdbuf", "identify", "xmessage", "xprop")
TOOLS: Tuple[str, ...] = REQUIRED_TOOLS + OPTIONAL_TOOLS

TAP_POINTS: Tuple[Tuple[float, float], ...] = (
    (0.5, 0.5),
    (0.5, 0.25),
    (0.35, 0.5),
)

#: Pixel difference below this is treated as noise (a ticking watch face).
CHANGE_THRESHOLD = 500


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------


def percentile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile; the sequence must not be empty."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of an empty sequence")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def interval_stats(times: Sequence[float]) -> Dict[str, float]:
    """Inter-arrival statistics for a list of monotonic timestamps (seconds)."""
    empty = {
        "samples": float(len(times)),
        "median_ms": 0.0,
        "p95_ms": 0.0,
        "min_ms": 0.0,
        "max_ms": 0.0,
    }
    if len(times) < 2:
        return empty
    gaps = [
        (later - earlier) * 1000.0
        for earlier, later in zip(times, times[1:])
        if later >= earlier
    ]
    if not gaps:
        return empty
    return {
        "samples": float(len(times)),
        "median_ms": statistics.median(gaps),
        "p95_ms": percentile(gaps, 0.95),
        "min_ms": min(gaps),
        "max_ms": max(gaps),
    }


def classify_poll_stats(requested_ms: int, stats: Mapping[str, float]) -> Tuple[str, str]:
    """Decide whether measured intervals are tight enough for the harness.

    Thresholds are stated here so the verdict can be argued with:

    - at least four interval samples (five trace lines);
    - ``pollingInterval=1000``: median 800-1400 ms and no gap above 2500 ms;
    - ``pollingInterval=0``: median at most 250 ms (continuous re-poll).

    The upper bound allows the Connect IQ timer plus one console read; the
    probe timestamps are receive times, not app timestamps.
    """
    if stats.get("samples", 0) < 4:
        return NOT_MET, f"only {int(stats.get('samples', 0))} interval sample(s)"
    if requested_ms <= 0:
        if stats["median_ms"] <= 250.0:
            return MET, f"median {stats['median_ms']:.0f} ms for pollingInterval=0"
        return (
            NOT_MET,
            f"median {stats['median_ms']:.0f} ms for pollingInterval=0; "
            "the app did not re-poll immediately",
        )
    if 800.0 <= stats["median_ms"] <= 1400.0 and stats["max_ms"] <= 2500.0:
        return (
            MET,
            f"median {stats['median_ms']:.0f} ms, max {stats['max_ms']:.0f} ms "
            f"for pollingInterval={requested_ms}",
        )
    return (
        NOT_MET,
        f"median {stats['median_ms']:.0f} ms, max {stats['max_ms']:.0f} ms "
        f"for pollingInterval={requested_ms}",
    )


def classify_escalation(first_error_s: float, errorview_s: float) -> Tuple[str, str]:
    """Check the documented 10-second continuous-failure window."""
    elapsed = errorview_s - first_error_s
    if 8.0 <= elapsed <= 15.0:
        return MET, f"error view after {elapsed:.1f}s of continuous failure"
    return (
        NOT_MET,
        f"error view after {elapsed:.1f}s; the documented window is about 10s "
        "(8-15s accepted)",
    )


def png_dimensions(path: Path) -> Optional[Tuple[int, int]]:
    """Read PNG dimensions from the IHDR chunk without ImageMagick."""
    try:
        data = path.read_bytes()[:33]
    except OSError:
        return None
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


# ---------------------------------------------------------------------------
# device matrix
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceSpec:
    id: str
    product: str
    input: str
    width: int
    height: int

    @property
    def supports_buttons(self) -> bool:
        return self.input in ("button", "touch+button")

    @property
    def supports_touch(self) -> bool:
        return self.input in ("touch", "touch+button")


#: Used only when the checkout's YAML cannot be read. PyYAML is in the runner
#: image, so this keeps a bare container usable, not replaceable.
DEFAULT_DEVICES: Mapping[str, DeviceSpec] = {
    "fenix6pro": DeviceSpec("fenix6pro", "fenix6pro", "button", 260, 260),
    "fenix847mm": DeviceSpec("fenix847mm", "fenix847mm", "touch+button", 454, 454),
    "venu2": DeviceSpec("venu2", "venu2", "touch", 416, 416),
    "venu3": DeviceSpec("venu3", "venu3", "touch", 454, 454),
}


def load_device_specs(repo: Path) -> Dict[str, DeviceSpec]:
    """Read the approved device matrix, falling back to the four spike devices."""
    path = repo / "tests" / "config" / "devices.yaml"
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return dict(DEFAULT_DEVICES)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_DEVICES)
    if not isinstance(document, Mapping):
        return dict(DEFAULT_DEVICES)
    devices: Dict[str, DeviceSpec] = {}
    for raw in document.get("devices", []):
        try:
            screen = raw["screen"]
            devices[str(raw["id"])] = DeviceSpec(
                id=str(raw["id"]),
                product=str(raw["product"]),
                input=str(raw.get("input", "button")),
                width=int(screen["width"]),
                height=int(screen["height"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return devices or dict(DEFAULT_DEVICES)


# ---------------------------------------------------------------------------
# SDK and preflight
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SdkTools:
    root: Path
    monkeyc: Path
    monkeydo: Path
    simulator: Path


def find_sdk(environ: Mapping[str, str]) -> Tuple[Optional[SdkTools], List[str]]:
    """Locate the mounted SDK; returns ``(tools, problems)``."""
    root_value = environ.get("CIQ_SDK_ROOT") or environ.get("CIQ_SDK_PATH") or ""
    if not root_value:
        return None, ["Connect IQ SDK (CIQ_SDK_ROOT is not set inside the container)"]
    root = Path(root_value)
    if not root.is_dir():
        return None, [f"Connect IQ SDK ({root} is not a directory)"]

    def first(candidates: Iterable[str]) -> Optional[Path]:
        for relative in candidates:
            candidate = root / relative
            if candidate.is_file():
                return candidate
        return None

    monkeyc = first(("linux/bin/monkeyc", "bin/monkeyc"))
    monkeydo = first(("linux/bin/monkeydo", "bin/monkeydo"))
    simulator = first(("windows/bin/simulator.exe", "bin/simulator.exe"))
    problems: List[str] = []
    if monkeyc is None:
        problems.append("Connect IQ SDK (missing linux/bin/monkeyc)")
    if monkeydo is None:
        problems.append("Connect IQ SDK (missing linux/bin/monkeydo)")
    if simulator is None:
        problems.append("Connect IQ SDK (missing windows/bin/simulator.exe)")
    if problems:
        return None, problems
    assert monkeyc is not None and monkeydo is not None and simulator is not None
    return SdkTools(root, monkeyc, monkeydo, simulator), []


def tool_paths(environ: Mapping[str, str]) -> Dict[str, Optional[str]]:
    """Resolve the external tools the probe drives."""
    found: Dict[str, Optional[str]] = {}
    for name in TOOLS:
        if name == "wine":
            found[name] = environ.get(WINE_ENV) or shutil.which("wine")
        else:
            found[name] = shutil.which(name)
    return found


def _missing_tool(name: str) -> str:
    if name == "java":
        return (
            "Java runtime (monkeyc is a Java launcher; the runner image must "
            "provide a headless JRE)"
        )
    if name in ("import", "compare", "identify"):
        return f"ImageMagick ({name} is not installed in the runner image)"
    if name == "xvfb":
        return "Xvfb (not installed in the runner image)"
    if name == "wine":
        return "Wine (not installed in the runner image)"
    return f"{name} (not installed in the runner image)"


def preflight(
    environ: Mapping[str, str], repo: Path
) -> Tuple[Dict[str, Optional[str]], Optional[SdkTools], List[str]]:
    """Return tool paths, SDK tools and the list of missing prerequisites."""
    tools = tool_paths(environ)
    sdk, sdk_problems = find_sdk(environ)
    problems = [
        _missing_tool(name)
        for name in REQUIRED_TOOLS
        if tools.get(name) is None
    ]
    problems.extend(sdk_problems)
    if not (repo / "monkey.jungle").is_file():
        problems.append(f"repository ({repo / 'monkey.jungle'} is missing)")
    return tools, sdk, problems


def display_ready(display: str, xdpyinfo: Optional[str]) -> bool:
    if not xdpyinfo:
        return False
    try:
        completed = subprocess.run(
            [xdpyinfo, "-display", display],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def ensure_display(
    display: str,
    tools: Mapping[str, Optional[str]],
    artifacts: Path,
    *,
    log: Callable[[str], None] = print,
) -> bool:
    """Return True when the display answers, starting Xvfb if necessary."""
    if display_ready(display, tools.get("xdpyinfo")):
        return True
    xvfb = tools.get("xvfb")
    if not xvfb:
        return False
    log(f"starting {xvfb} {display}")
    log_path = artifacts / "logs" / "xvfb.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        subprocess.Popen(
            [xvfb, display, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    for _ in range(40):
        if display_ready(display, tools.get("xdpyinfo")):
            return True
        time.sleep(0.25)
    return False


# ---------------------------------------------------------------------------
# recorder
# ---------------------------------------------------------------------------


class Recorder:
    """Check log that writes ``probe.json`` after every record.

    A crash half-way still leaves the observations gathered so far, which is the
    difference between "menus work, timing crashed" and "no result".
    """

    def __init__(self, path: Path, artifacts: Path, environment: Mapping[str, Any]) -> None:
        self.path = path
        self.artifacts = artifacts
        self.environment: Dict[str, Any] = dict(environment)
        self.log = CheckLog()
        self.prerequisites: List[str] = []
        self.excerpts: Dict[str, List[str]] = {}
        self.environment["console_excerpts"] = self.excerpts

    def record(self, check_id: str, status: str, detail: str = "", **kwargs: Any) -> None:
        self.log.record(check_id, status, detail, **kwargs)
        self.flush()

    def add_excerpt(self, name: str, lines: Sequence[str]) -> None:
        self.excerpts[name] = [str(line) for line in lines]
        self.flush()

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = ProbeDocument.from_checks(
            self.log.checks,
            prerequisites=self.prerequisites,
            environment=self.environment,
            artifacts_root=str(self.artifacts),
        )
        self.path.write_text(
            json.dumps(document.to_document(), indent=2) + "\n", encoding="utf-8"
        )

    def evidence(self, path: Optional[Path]) -> str:
        if path is None:
            return ""
        try:
            return str(path.relative_to(self.artifacts))
        except ValueError:
            return str(path)


# ---------------------------------------------------------------------------
# processes
# ---------------------------------------------------------------------------


class CommandRunner:
    """Run commands, tee their output to ``artifacts/logs`` and time them."""

    def __init__(self, artifacts: Path, log: Callable[[str], None]) -> None:
        self.artifacts = artifacts
        self.log = log
        self.logs = artifacts / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        command: Sequence[str],
        *,
        name: str,
        timeout: float = 120.0,
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[Path] = None,
    ) -> subprocess.CompletedProcess:
        path = self.logs / f"{name}.log"
        started = time.monotonic()
        try:
            completed = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=dict(env) if env is not None else None,
                cwd=str(cwd) if cwd else None,
            )
        except subprocess.TimeoutExpired as error:
            text = f"$ {' '.join(command)}\n<timeout after {timeout:.0f}s>\n"
            if error.stdout:
                text += str(error.stdout)
            if error.stderr:
                text += str(error.stderr)
            path.write_text(text, encoding="utf-8")
            self.log(f"{name}: timed out after {timeout:.0f}s")
            raise
        output = (completed.stdout or "") + (completed.stderr or "")
        path.write_text(f"$ {' '.join(command)}\n{output}", encoding="utf-8")
        self.log(f"{name}: exit {completed.returncode} in {time.monotonic() - started:.1f}s")
        return completed


class ConsoleStream:
    """Read a child process's combined stdout/stderr line by line, with times."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lines: List[Tuple[float, str]] = []
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._handle: Optional[Any] = None

    def start(
        self,
        command: Sequence[str],
        *,
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[Path] = None,
        flush_tool: Optional[str] = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")
        wrapped = list(command)
        if flush_tool:
            wrapped = [flush_tool, "-oL", "-eL"] + wrapped
        self._process = subprocess.Popen(
            wrapped,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=dict(env) if env is not None else None,
            cwd=str(cwd) if cwd else None,
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        for line in self._process.stdout:
            with self._lock:
                self.lines.append((time.monotonic(), line.rstrip("\n")))
                if self._handle is not None:
                    self._handle.write(line)
                    self._handle.flush()

    @property
    def process(self) -> Optional[subprocess.Popen]:
        return self._process

    def snapshot(self) -> List[Tuple[float, str]]:
        with self._lock:
            return list(self.lines)

    def parsed(self) -> List[Tuple[float, Dict[str, str]]]:
        events = []
        for timestamp, line in self.snapshot():
            event = parse_console_line(line)
            if event is not None:
                events.append((timestamp, event))
        return events

    def wait_for(
        self,
        predicate: Callable[[Dict[str, str]], bool],
        timeout: float,
        *,
        poll: float = 0.1,
    ) -> Optional[Tuple[float, Dict[str, str]]]:
        deadline = time.monotonic() + timeout
        while True:
            for timestamp, event in self.parsed():
                if predicate(event):
                    return timestamp, event
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll)

    def excerpt(self, limit: int = 12) -> List[str]:
        lines = [line for _, line in self.snapshot() if line.strip()]
        return lines[-limit:]

    def trace_count(self) -> int:
        return len(self.parsed())

    def stop(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._handle is not None:
            self._handle.close()
            self._handle = None


# ---------------------------------------------------------------------------
# simulator
# ---------------------------------------------------------------------------


class Simulator:
    """One simulator process under Wine plus the window-driving helpers."""

    def __init__(
        self,
        *,
        wine: str,
        simulator_exe: Path,
        prefix: Path,
        display: str,
        artifacts: Path,
        runner: CommandRunner,
        log: Callable[[str], None],
    ) -> None:
        self.wine = wine
        self.simulator_exe = simulator_exe
        self.prefix = prefix
        self.display = display
        self.artifacts = artifacts
        self.runner = runner
        self.log = log
        self._process: Optional[subprocess.Popen] = None
        self.window: Optional[str] = None

    def wine_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "WINEPREFIX": str(self.prefix),
                "WINEARCH": "win32",
                "DISPLAY": self.display,
                "WINEDEBUG": "-all",
                "WINEDLLOVERRIDES": "mscoree,mshtml=",
            }
        )
        return env

    def start(self, timeout: float = 90.0) -> Tuple[bool, float, str]:
        """Start ``simulator.exe`` and wait for its window."""
        started = time.monotonic()
        log_path = self.artifacts / "logs" / "simulator.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("w", encoding="utf-8")
        self._process = subprocess.Popen(
            [self.wine, str(self.simulator_exe)],
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=self.wine_env(),
        )
        ok = self.wait_for_window(timeout)
        elapsed = time.monotonic() - started
        detail = (
            f"window {self.window} after {elapsed:.1f}s"
            if ok
            else f"no simulator window after {elapsed:.1f}s; see logs/simulator.log"
        )
        return ok, elapsed, detail

    def wait_for_window(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            window = self.find_window()
            if window:
                self.window = window
                return True
            time.sleep(0.5)
        return False

    def find_window(self) -> Optional[str]:
        xdotool = shutil.which("xdotool")
        if not xdotool:
            return None
        env = dict(os.environ, DISPLAY=self.display)
        try:
            search = subprocess.run(
                [xdotool, "search", "--onlyvisible", "--name", ".*"],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        best: Optional[Tuple[int, str]] = None
        for window in (search.stdout or "").split():
            geometry = self.geometry(window)
            if geometry is None:
                continue
            area = geometry["width"] * geometry["height"]
            if area < 160 * 160:
                continue
            title = self.window_name(window) or ""
            if re.search(r"(?i)connect\s*iq|simulator|fenix|venu|forerunner|edge", title):
                area += 10_000_000
            if best is None or area > best[0]:
                best = (area, window)
        return best[1] if best else None

    def _xdo(self, *args: str, timeout: float = 15.0) -> Optional[subprocess.CompletedProcess]:
        xdotool = shutil.which("xdotool")
        if not xdotool:
            return None
        try:
            return subprocess.run(
                [xdotool, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=dict(os.environ, DISPLAY=self.display),
            )
        except (OSError, subprocess.SubprocessError):
            return None

    def geometry(self, window: Optional[str] = None) -> Optional[Dict[str, int]]:
        target = window or self.window
        if not target:
            return None
        completed = self._xdo("getwindowgeometry", "--shell", target)
        if completed is None or completed.returncode != 0:
            return None
        values: Dict[str, int] = {}
        for line in (completed.stdout or "").splitlines():
            key, separator, value = line.partition("=")
            if separator and value.strip().lstrip("-").isdigit():
                values[key.strip().lower()] = int(value.strip())
        if {"x", "y", "width", "height"} <= set(values):
            return values
        return None

    def window_name(self, window: str) -> Optional[str]:
        completed = self._xdo("getwindowname", window)
        if completed is None or completed.returncode != 0:
            return None
        return (completed.stdout or "").strip() or None

    def key(self, keys: str) -> bool:
        if not self.window:
            return False
        completed = self._xdo("key", "--window", self.window, "--clearmodifiers", keys)
        return completed is not None and completed.returncode == 0

    def click(self, x: int, y: int) -> bool:
        if not self.window:
            return False
        completed = self._xdo(
            "mousemove", "--window", self.window, str(x), str(y), "click", "1"
        )
        return completed is not None and completed.returncode == 0

    def capture(self, name: str) -> Tuple[Optional[Path], Optional[Tuple[int, int]]]:
        """Capture the simulator window with ImageMagick ``import``."""
        if not self.window:
            return None, None
        import_tool = shutil.which("import")
        if not import_tool:
            return None, None
        path = self.artifacts / "shots" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        completed = self.runner.run(
            [import_tool, "-display", self.display, "-window", self.window, "-silent", str(path)],
            name=f"capture-{name}",
            timeout=30,
            env=dict(os.environ, DISPLAY=self.display),
        )
        if completed.returncode != 0 or not path.is_file():
            return None, None
        return path, png_dimensions(path)

    def pixel_difference(self, before: Path, after: Path) -> Optional[int]:
        """Absolute pixel difference using ImageMagick ``compare``."""
        compare_tool = shutil.which("compare")
        if not compare_tool:
            return None
        try:
            completed = subprocess.run(
                [compare_tool, "-metric", "AE", str(before), str(after), "null:"],
                capture_output=True,
                text=True,
                timeout=60,
                env=dict(os.environ, DISPLAY=self.display),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        match = re.search(r"(-?\d+(?:\.\d+)?)", completed.stderr or "")
        return int(float(match.group(1))) if match else None

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        self.window = None
        wineserver = shutil.which("wineserver")
        if wineserver:
            try:
                subprocess.run(
                    [wineserver, "-k"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    env=self.wine_env(),
                )
            except (OSError, subprocess.SubprocessError):
                pass


# ---------------------------------------------------------------------------
# the probe
# ---------------------------------------------------------------------------


class SpikeProbe:
    """Run the #370 procedure and record checks."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo = Path(args.repo)
        self.artifacts = Path(args.artifacts)
        for directory in ("logs", "shots", "build"):
            (self.artifacts / directory).mkdir(parents=True, exist_ok=True)
        self.display = args.display or os.environ.get("DISPLAY", ":99")
        self.environ = dict(os.environ)
        self.devices = load_device_specs(self.repo)
        self.tools: Dict[str, Optional[str]] = {}
        self.sdk: Optional[SdkTools] = None
        self.runner: Optional[CommandRunner] = None
        self.prefix = Path(args.wineprefix or "/tmp/openhab-garmin-spike-prefix")
        self.recorder = Recorder(
            self.artifacts / "probe.json",
            self.artifacts,
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "machine": platform.machine(),
                "display": self.display,
                "wineprefix": str(self.prefix),
                "repo": str(self.repo),
                "button_device": args.button_device,
                "touch_device": args.touch_device,
                "language": args.language,
                "poll_intervals": list(args.poll_intervals),
            },
        )

    # -- plumbing ----------------------------------------------------------
    def log(self, message: str) -> None:
        print(f"[spike] {message}", flush=True)

    def record(self, check_id: str, status: str, detail: str = "", **kwargs: Any) -> None:
        self.recorder.record(check_id, status, detail, **kwargs)
        self.log(f"{check_id}: {status} ({detail})" if detail else f"{check_id}: {status}")

    def evidence(self, path: Optional[Path]) -> str:
        return self.recorder.evidence(path)

    def _tap(self, *command: str) -> str:
        try:
            completed = subprocess.run(
                list(command), capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        text = ((completed.stdout or "") + (completed.stderr or "")).strip()
        return text.splitlines()[0] if text else ""

    # -- preflight ---------------------------------------------------------
    def preflight(self) -> bool:
        tools, sdk, problems = preflight(self.environ, self.repo)
        self.tools = tools
        self.sdk = sdk
        self.recorder.environment.update(
            {
                "sdk_root": str(sdk.root) if sdk else "",
                "tools": {name: (path or "") for name, path in sorted(tools.items())},
            }
        )
        display_ok = display_ready(self.display, tools.get("xdpyinfo")) or ensure_display(
            self.display, tools, self.artifacts, log=self.log
        )
        if not display_ok:
            reason = (
                "Xvfb is not installed"
                if not tools.get("Xvfb")
                else "Xvfb could not start one"
            )
            problems.append(
                f"X display ({self.display} does not answer and {reason})"
            )
        if problems:
            self.recorder.prerequisites = problems
            self.recorder.flush()
            return False
        assert sdk is not None
        self.runner = CommandRunner(self.artifacts, self.log)
        wine_version = self._tap(self.tools["wine"] or "wine", "--version")
        self.recorder.environment["wine"] = wine_version or "<unknown>"
        self.recorder.environment["sdk"] = {
            "root": str(sdk.root),
            "monkeyc": str(sdk.monkeyc),
            "monkeydo": str(sdk.monkeydo),
            "simulator": str(sdk.simulator),
        }
        self.record("wine.available", MET, wine_version or "wine is present")
        self.record("display.available", MET, f"DISPLAY {self.display} answers xdpyinfo")
        return True

    def prepare_prefix(self) -> None:
        self.prefix.mkdir(parents=True, exist_ok=True)
        assert self.runner is not None and self.sdk is not None
        simulator = Simulator(
            wine=self.tools["wine"] or "wine",
            simulator_exe=self.sdk.simulator,
            prefix=self.prefix,
            display=self.display,
            artifacts=self.artifacts,
            runner=self.runner,
            log=self.log,
        )
        wineboot = shutil.which("wineboot")
        if not wineboot:
            self.record(
                "wine.prefix",
                SKIPPED,
                "wineboot is not installed; the first wine start initialises the prefix",
            )
            return
        try:
            completed = self.runner.run(
                [wineboot, "--init"], name="wineboot", timeout=180, env=simulator.wine_env()
            )
            ok = completed.returncode == 0
            detail = f"wineboot --init exit {completed.returncode}"
        except subprocess.TimeoutExpired:
            ok = False
            detail = "wineboot --init timed out after 180s"
        self.record("wine.prefix", MET if ok else NOT_MET, detail, evidence=["logs/wineboot.log"])

    # -- build -------------------------------------------------------------
    def build_prg(self, device: DeviceSpec) -> Optional[Path]:
        assert self.sdk is not None and self.runner is not None
        output = self.artifacts / "build" / f"openhab-garmin-{device.product}.prg"
        command = [
            str(self.sdk.monkeyc),
            "-f",
            str(self.repo / "monkey.jungle"),
            "-o",
            str(output),
            "-d",
            device.product,
            "-w",
        ]
        started = time.monotonic()
        try:
            completed = self.runner.run(command, name=f"monkeyc-{device.id}", timeout=600)
        except subprocess.TimeoutExpired:
            self.record(
                f"build.prg.{device.id}",
                NOT_MET,
                "monkeyc timed out after 600s",
                evidence=[f"logs/monkeyc-{device.id}.log"],
            )
            return None
        duration = (time.monotonic() - started) * 1000.0
        if completed.returncode != 0 or not output.is_file():
            self.record(
                f"build.prg.{device.id}",
                NOT_MET,
                f"monkeyc exit {completed.returncode}; see logs/monkeyc-{device.id}.log",
                evidence=[f"logs/monkeyc-{device.id}.log"],
                measurements={"duration_ms": duration},
            )
            return None
        self.record(
            f"build.prg.{device.id}",
            MET,
            f"{output.name} built for {device.product}",
            evidence=[self.evidence(output), f"logs/monkeyc-{device.id}.log"],
            measurements={"duration_ms": duration},
        )
        return output

    # -- layout and settings ----------------------------------------------
    def discover_layout(self) -> Dict[str, Any]:
        found: Dict[str, Any] = {"simulator_ini": [], "app_data": [], "settings": []}
        for path in self.prefix.rglob("simulator.ini"):
            found["simulator_ini"].append(str(path.relative_to(self.prefix)))
        for path in self.prefix.rglob("com.garmin.connectiq"):
            if path.is_dir():
                found["app_data"].append(str(path.relative_to(self.prefix)))
        for pattern in ("settings.txt", "*.ini"):
            for path in self.prefix.rglob(pattern):
                if path.is_file() and path.name != "simulator.ini":
                    found["settings"].append(str(path.relative_to(self.prefix)))
        for key in found:
            found[key] = found[key][:40]
        (self.artifacts / "layout.json").write_text(
            json.dumps(found, indent=2) + "\n", encoding="utf-8"
        )
        self.recorder.environment["layout"] = found
        return found

    def ini_candidates(self) -> List[Path]:
        users = ["root", os.environ.get("USER", "root")]
        candidates: List[Path] = []
        for user in dict.fromkeys(users):
            for hint in INI_HINTS:
                candidates.append(self.prefix / hint.format(user=user))
        candidates.extend(self.prefix.rglob("simulator.ini"))
        seen: List[Path] = []
        for path in candidates:
            if path not in seen:
                seen.append(path)
        return seen

    def write_ini(self, updates: Mapping[str, Any]) -> Dict[str, Any]:
        """Best-effort INI update.

        Existing keys are rewritten. A missing ``Language`` key is appended
        under ``[Simulator]`` because the language step otherwise has no route
        at all; the detail says it was appended so the evidence stays honest.
        Other unknown keys are reported as missing rather than invented.
        """
        result: Dict[str, Any] = {
            "files": [],
            "written": {},
            "missing": [],
            "appended": [],
        }
        existing: List[Path] = []
        for path in self.ini_candidates():
            if not path.is_file():
                continue
            existing.append(path)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            changed = False
            for name, value in updates.items():
                wanted = INI_KEYS.get(name, name)
                for index, line in enumerate(lines):
                    key, separator, _ = line.partition("=")
                    if separator and key.strip().lower() == wanted.lower():
                        lines[index] = f"{key.strip()}={value}"
                        result["written"][name] = str(path)
                        changed = True
            if changed:
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                result["files"].append(str(path))

        if "language" in updates and "language" not in result["written"] and existing:
            path = existing[0]
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            section = next(
                (
                    index
                    for index, line in enumerate(lines)
                    if line.strip().lower() == "[simulator]"
                ),
                None,
            )
            key = f"{INI_KEYS['language']}={updates['language']}"
            if section is None:
                lines += ["[Simulator]", key]
            else:
                lines.insert(section + 1, key)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result["written"]["language"] = str(path)
            result["appended"].append("language")
            result["files"].append(str(path))

        for name in updates:
            if name not in result["written"]:
                result["missing"].append(name)
        return result

    def read_ini_values(self) -> Dict[str, str]:
        """Read every ``key=value`` pair from all discovered INI files."""
        values: Dict[str, str] = {}
        for path in self.ini_candidates():
            if path.is_file():
                values.update(self.read_ini(path))
        return values

    def write_app_properties(self, properties: Mapping[str, Any]) -> Dict[str, Any]:
        """Best-effort application-property write.

        The simulator stores application properties outside ``simulator.ini``;
        the probe writes a ``key=value`` file next to the discovered app data
        and records the exact path, so a future driver does not have to guess.
        """
        result: Dict[str, Any] = {"written": "", "candidates": []}
        layout = self.recorder.environment.get("layout", {})
        settings = [
            self.prefix / relative
            for relative in layout.get("settings", [])
            if relative.endswith(".txt")
        ]
        app_dirs = [self.prefix / relative for relative in layout.get("app_data", [])]
        candidates = settings + [directory / "settings.txt" for directory in app_dirs]
        result["candidates"] = [str(path) for path in candidates]
        if not candidates:
            return result
        path = candidates[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: List[str] = []
        if path.is_file():
            lines = [
                line
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            ]
        for key, value in sorted(properties.items()):
            lines = [line for line in lines if line.partition("=")[0].strip() != key]
            lines.append(f"{key}={value}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result["written"] = str(path)
        return result

    # -- app launch --------------------------------------------------------
    def launch_app(
        self,
        device: DeviceSpec,
        prg: Path,
        *,
        label: str,
        timeout: float,
    ) -> Tuple[ConsoleStream, Optional[Tuple[float, Dict[str, str]]]]:
        """Start ``monkeydo`` and wait for the first ``E2E:VIEW``."""
        assert self.sdk is not None
        console = ConsoleStream(self.artifacts / "logs" / f"console-{label}.log")
        flush_tool = self.tools.get("stdbuf")
        base = [str(self.sdk.monkeydo)]
        if self.args.flush:
            base.append("-f")
        base += [str(prg), device.product]
        console.start(
            base,
            env=dict(os.environ, DISPLAY=self.display),
            cwd=self.repo,
            flush_tool=flush_tool,
        )
        # Give a rejected option a few seconds to fail, instead of waiting out
        # the whole startup timeout before retrying.
        console.wait_for(lambda event: True, min(5.0, timeout))
        process = console.process
        if self.args.flush and process is not None and process.poll() not in (None, 0):
            self.log("monkeydo rejected -f; retrying with the documented form")
            console.stop()
            console = ConsoleStream(self.artifacts / "logs" / f"console-{label}.log")
            console.start(
                [str(self.sdk.monkeydo), str(prg), device.product],
                env=dict(os.environ, DISPLAY=self.display),
                cwd=self.repo,
                flush_tool=flush_tool,
            )
        view = console.wait_for(lambda event: event["event"] == "VIEW", timeout)
        return console, view

    # -- one device --------------------------------------------------------
    def run_device(self, device: DeviceSpec, *, primary: bool) -> Dict[str, Any]:
        assert self.sdk is not None and self.runner is not None
        outcome: Dict[str, Any] = {"device": device.id, "rendered": False}
        self.write_ini({"language": "eng"})
        simulator = Simulator(
            wine=self.tools["wine"] or "wine",
            simulator_exe=self.sdk.simulator,
            prefix=self.prefix,
            display=self.display,
            artifacts=self.artifacts,
            runner=self.runner,
            log=self.log,
        )
        try:
            simulator_ok, startup_s, detail = simulator.start(
                timeout=self.args.startup_timeout
            )
            outcome.update(
                {
                    "simulator_launches": simulator_ok,
                    "simulator_detail": detail,
                    "startup_s": startup_s,
                }
            )
            if not simulator_ok:
                return outcome

            self.discover_layout()
            prg = self.build_prg(device)
            if prg is None:
                return outcome
            outcome["prg"] = prg

            console, view = self.launch_app(
                device, prg, label=device.id, timeout=self.args.startup_timeout
            )
            try:
                outcome["monkeydo_started"] = console.process is not None
                outcome["trace_lines"] = console.trace_count()
                outcome["rendered"] = view is not None
                if view is not None and console.snapshot():
                    outcome["startup_to_view_s"] = view[0] - console.snapshot()[0][0]
                shot, dimensions = simulator.capture(f"{device.id}-launch")
                outcome["screenshot"] = shot
                outcome["dimensions"] = dimensions
                if console.trace_count():
                    self.recorder.add_excerpt(device.id, console.excerpt(limit=15))
                self.record(
                    f"device.{device.id}.launch",
                    MET if view is not None else NOT_MET,
                    (
                        f"first E2E:VIEW after {outcome.get('startup_to_view_s', 0.0):.1f}s, "
                        f"{console.trace_count()} E2E line(s)"
                        if view is not None
                        else f"no E2E:VIEW within {self.args.startup_timeout:.0f}s, "
                        f"{console.trace_count()} E2E line(s)"
                    ),
                    evidence=[f"logs/console-{device.id}.log"],
                )
                self.record(
                    f"device.{device.id}.screenshot",
                    MET if dimensions is not None else NOT_MET,
                    f"captured {dimensions}" if dimensions else "capture failed",
                    evidence=[self.evidence(shot)] if shot is not None else [],
                )

                outcome["input"] = self.run_input(simulator, device)
                if primary:
                    # Timing and escalation run before the menu experiments so a
                    # sleep/night/connection toggle cannot contaminate them.
                    # Each of those restarts the simulator, so the launch console
                    # is finished here and the menus get a fresh one.
                    console.stop()
                    outcome["language"] = self.run_language(device, prg, simulator)
                    outcome["timing"] = self.run_timing(device, prg, simulator)
                    outcome["escalation"] = self.run_escalation(device, prg, simulator)
                    menu_console, _ = self.launch_app(
                        device,
                        prg,
                        label=f"{device.id}-menus",
                        timeout=self.args.startup_timeout,
                    )
                    try:
                        outcome["menus"] = self.run_menus(simulator, menu_console)
                        outcome["occlusion"] = self.run_occlusion(simulator)
                    finally:
                        menu_console.stop()
            finally:
                console.stop()
        finally:
            simulator.stop()
        return outcome

    # -- input -------------------------------------------------------------
    def run_input(self, simulator: Simulator, device: DeviceSpec) -> Dict[str, Any]:
        target = "input.buttons" if device.supports_buttons else "input.touch"
        baseline, _ = simulator.capture(f"{device.id}-input-before")
        if baseline is None:
            self.record(target, SKIPPED, "no screenshot before input")
            return {"status": SKIPPED}
        changes = 0
        evidence = [self.evidence(baseline)]
        for attempt in range(3):
            if device.supports_buttons:
                simulator.key("Down")
            else:
                geometry = simulator.geometry()
                if geometry is None:
                    break
                x = int(geometry["width"] * TAP_POINTS[attempt][0])
                y = int(geometry["height"] * TAP_POINTS[attempt][1])
                simulator.click(x, y)
            time.sleep(self.args.input_delay)
            shot, _ = simulator.capture(f"{device.id}-input-{attempt + 1}")
            if shot is None:
                continue
            evidence.append(self.evidence(shot))
            difference = simulator.pixel_difference(baseline, shot)
            if difference is not None and difference > CHANGE_THRESHOLD:
                changes += 1
            baseline = shot
        status = MET if changes >= 2 else NOT_MET
        self.record(
            target,
            status,
            f"{changes}/3 input events changed the screen on {device.id}",
            evidence=evidence,
            measurements={"changes": float(changes)},
        )
        return {"status": status, "changes": changes}

    # -- menus -------------------------------------------------------------
    def _menu_open(
        self, simulator: Simulator, keys: str, name: str
    ) -> Tuple[bool, Optional[Path]]:
        """Open a native menu; returns ``(opened, closed_baseline)``."""
        before, _ = simulator.capture(f"{name}-before")
        if before is None:
            return False, None
        simulator.key(keys)
        time.sleep(0.5)
        opened, _ = simulator.capture(f"{name}-open")
        if opened is None:
            return False, before
        difference = simulator.pixel_difference(before, opened)
        return difference is not None and difference > CHANGE_THRESHOLD, before

    def _menu_toggle(
        self,
        simulator: Simulator,
        *,
        menu_keys: str,
        items: Sequence[str],
        item: str,
        name: str,
    ) -> Dict[str, Any]:
        index = list(items).index(item)
        opened, before = self._menu_open(simulator, menu_keys, f"menu-{name}")
        if not opened or before is None:
            simulator.key("Escape")
            return {"opened": False, "changed": False}
        # The menu starts with an item highlighted; press Down index times to
        # reach the intended one, then activate it and close the menu.
        for _ in range(index + 1):
            simulator.key("Down")
        simulator.key("Return")
        time.sleep(0.4)
        simulator.key("Escape")
        time.sleep(1.0)
        after, _ = simulator.capture(f"menu-{name}-after")
        if after is None:
            return {"opened": True, "changed": False, "before": self.evidence(before)}
        difference = simulator.pixel_difference(before, after)
        open_shot = self.artifacts / "shots" / f"menu-{name}-open.png"
        return {
            "opened": True,
            "changed": difference is not None and difference > CHANGE_THRESHOLD,
            "difference": difference,
            "before": self.evidence(before),
            "after": self.evidence(after),
            "open": self.evidence(open_shot) if open_shot.is_file() else "",
        }

    def run_menus(self, simulator: Simulator, console: ConsoleStream) -> Dict[str, Any]:
        outcomes: Dict[str, Any] = {}
        verified = 0
        ui_attempts = 0
        evidence: List[str] = []
        for item in ("night_mode", "sleep_mode", "touch", "https"):
            result = self._menu_toggle(
                simulator,
                menu_keys="alt+s",
                items=SIMULATION_ITEMS,
                item=item,
                name=item,
            )
            outcomes[item] = result
            if result.get("opened"):
                ui_attempts += 1
            if result.get("changed"):
                verified += 1
            evidence.extend(
                path for path in (result.get("before"), result.get("after")) if path
            )

        simulation_ok = ui_attempts >= 2 and verified >= 2
        self.record(
            "menu.simulation_ui",
            MET if simulation_ok else NOT_MET,
            f"{ui_attempts}/4 Simulation menu toggles opened, {verified}/4 changed the screen",
            evidence=evidence,
            measurements={"toggles_opened": float(ui_attempts), "toggles_verified": float(verified)},
        )

        # The INI route is recorded separately: a driver may script startup
        # state through configuration even when the native menu is not reliable.
        wanted = {
            "night_mode": "true",
            "sleep_mode": "true",
            "touch": "false",
            "https": "true",
        }
        config = self.write_ini(wanted)
        readback = self.read_ini_values()
        confirmed = [
            name
            for name, value in wanted.items()
            if readback.get(INI_KEYS[name].lower()) == value
        ]
        config_ok = len(confirmed) >= 2
        self.record(
            "config.simulation_state",
            MET if config_ok else SKIPPED,
            (
                f"wrote and read back {', '.join(sorted(confirmed))} via simulator.ini"
                if config_ok
                else "fewer than two Simulation keys could be set and read back; "
                "see layout.json"
            ),
            evidence=["layout.json"],
            measurements={
                "keys_written": float(len(config["written"])),
                "keys_confirmed": float(len(confirmed)),
            },
        )
        # Leave the simulator in its default state for anything that follows.
        self.write_ini(
            {"night_mode": "false", "sleep_mode": "false", "touch": "true", "https": "false"}
        )
        self.record(
            "menu.simulation",
            MET if simulation_ok else NOT_MET,
            (
                "Simulation menu toggles changed the app state"
                if simulation_ok
                else f"only {verified}/4 Simulation toggles were verified"
            ),
            evidence=evidence,
        )

        connection = self.run_connection_menu(simulator, console)
        outcomes["connection"] = connection
        self.record(
            "menu.connection",
            MET if connection["changed"] else NOT_MET,
            connection["detail"],
            evidence=connection["evidence"],
            measurements={"mode_changes": float(connection["mode_changes"])},
        )
        return outcomes

    def run_connection_menu(
        self, simulator: Simulator, console: ConsoleStream
    ) -> Dict[str, Any]:
        before_modes = {
            event.get("mode")
            for _, event in console.parsed()
            if event["event"] == "CONNECTION"
        }
        evidence: List[str] = []
        mode_changes = 0
        for item in CONNECTION_ITEMS:
            result = self._menu_toggle(
                simulator,
                menu_keys="alt+c",
                items=CONNECTION_ITEMS,
                item=item,
                name=f"connection-{item}",
            )
            evidence.extend(
                path for path in (result.get("before"), result.get("after")) if path
            )
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                after_modes = {
                    event.get("mode")
                    for _, event in console.parsed()
                    if event["event"] == "CONNECTION"
                }
                if after_modes - before_modes:
                    mode_changes += 1
                    before_modes = after_modes
                    break
                time.sleep(0.25)
        changed = mode_changes > 0
        return {
            "changed": changed,
            "mode_changes": mode_changes,
            "evidence": evidence,
            "detail": (
                f"{mode_changes} connection mode change(s) observed"
                if changed
                else "no E2E:CONNECTION mode change after the Connection menu"
            ),
        }

    # -- language ----------------------------------------------------------
    def run_language(
        self, device: DeviceSpec, prg: Path, simulator: Simulator
    ) -> Dict[str, Any]:
        target = self.args.language
        before, _ = simulator.capture(f"{device.id}-language-before")
        written = self.write_ini({"language": target})
        if "language" not in written["written"]:
            self.record(
                "language.switch",
                SKIPPED,
                "no language key was found in simulator.ini; see layout.json",
                evidence=["layout.json"],
            )
            return {"status": SKIPPED}
        simulator.stop()
        restart_ok, _, detail = simulator.start(timeout=self.args.startup_timeout)
        if not restart_ok:
            self.record(
                "language.switch",
                NOT_MET,
                f"simulator did not restart after the language change: {detail}",
                evidence=["logs/simulator.log"],
            )
            return {"status": NOT_MET}
        console, _ = self.launch_app(
            device, prg, label=f"{device.id}-language", timeout=self.args.startup_timeout
        )
        try:
            found = console.wait_for(
                lambda event: event["event"] == "LANGUAGE"
                and event.get("code") == target,
                timeout=min(60.0, self.args.startup_timeout),
            )
            evidence = [self.evidence(path) for path in (before,) if path]
            if console.trace_count():
                self.recorder.add_excerpt(f"{device.id}-language", console.excerpt(10))
            if found is None:
                self.record(
                    "language.switch",
                    NOT_MET,
                    f"no E2E:LANGUAGE;code={target} after the restart",
                    evidence=evidence + [f"logs/console-{device.id}-language.log"],
                )
                return {"status": NOT_MET}
            self.record(
                "language.switch",
                MET,
                f"E2E:LANGUAGE;code={target} observed after writing simulator.ini",
                evidence=evidence + [f"logs/console-{device.id}-language.log"],
            )
            after, _ = simulator.capture(f"{device.id}-language-after")
            difference = (
                simulator.pixel_difference(before, after)
                if before is not None and after is not None
                else None
            )
            if difference is not None and difference > CHANGE_THRESHOLD:
                self.record(
                    "language.rendered",
                    MET,
                    f"rendered screen differs after the language change ({difference} px); "
                    "text language not OCR-verified",
                    evidence=[self.evidence(after)],
                )
            return {"status": MET}
        finally:
            console.stop()

    # -- timing ------------------------------------------------------------
    def run_timing(
        self, device: DeviceSpec, prg: Path, simulator: Simulator
    ) -> Dict[str, Any]:
        # monkeydo created the app-data directory on the first launch; rescan so
        # the property file is found where the simulator actually keeps it.
        self.discover_layout()
        outcomes: Dict[str, Any] = {}
        all_ok = True
        for requested in self.args.poll_intervals:
            written = self.write_app_properties(
                {
                    "pollingInterval_0": requested,
                    "url_0": "http://127.0.0.1:9/",
                    "sitemap_0": "garmin_e2e",
                    "restapi_0": "true",
                }
            )
            if not written["written"]:
                self.record(
                    "timing.poll_interval",
                    SKIPPED,
                    "no writable application-properties file was discovered; "
                    "see layout.json",
                    evidence=["layout.json"],
                )
                return {"status": SKIPPED}
            # A fresh simulator guarantees the app reads the properties at
            # startup instead of inheriting the previous run's state.
            simulator.stop()
            restarted, _, detail = simulator.start(timeout=self.args.startup_timeout)
            if not restarted:
                self.record(
                    "timing.poll_interval",
                    NOT_MET,
                    f"simulator did not restart for pollingInterval={requested}: {detail}",
                    evidence=["logs/simulator.log"],
                )
                return {"status": NOT_MET}
            console, _ = self.launch_app(
                device,
                prg,
                label=f"{device.id}-poll-{requested}",
                timeout=self.args.startup_timeout,
            )
            try:
                window = max(self.args.poll_window, 6.0)
                deadline = time.monotonic() + window
                while time.monotonic() < deadline:
                    time.sleep(0.5)
                times = [
                    timestamp
                    for timestamp, event in console.parsed()
                    if event["event"] == "SITEMAP"
                ]
                stats = interval_stats(times)
                status, detail = classify_poll_stats(requested, stats)
                outcomes[f"poll_{requested}"] = stats
                evidence = [f"logs/console-{device.id}-poll-{requested}.log"]
                if console.trace_count():
                    self.recorder.add_excerpt(
                        f"{device.id}-poll-{requested}", console.excerpt(10)
                    )
                self.record(
                    f"timing.poll_interval.{requested}",
                    status,
                    f"{detail}; {written['written']}",
                    evidence=evidence,
                    measurements={
                        f"requested_ms_{requested}": float(requested),
                        **{f"{key}_{requested}": value for key, value in stats.items()},
                    },
                )
                outcomes[f"status_{requested}"] = status
                all_ok = all_ok and status == MET
            finally:
                console.stop()
        self.record(
            "timing.poll_interval",
            MET if all_ok else NOT_MET,
            "all requested poll intervals measured within tolerance"
            if all_ok
            else "at least one poll interval was outside tolerance",
        )
        return outcomes

    def run_escalation(
        self, device: DeviceSpec, prg: Path, simulator: Simulator
    ) -> Dict[str, Any]:
        results: List[Dict[str, float]] = []
        evidence: List[str] = []
        for attempt in (1, 2):
            simulator.stop()
            restarted, _, detail = simulator.start(timeout=self.args.startup_timeout)
            if not restarted:
                evidence.append("logs/simulator.log")
                results.append({"status": 0.0, "elapsed_s": -1.0})
                self.log(f"escalation run {attempt}: simulator did not restart: {detail}")
                continue
            console, _ = self.launch_app(
                device,
                prg,
                label=f"{device.id}-escalation-{attempt}",
                timeout=self.args.startup_timeout,
            )
            try:
                error = console.wait_for(
                    lambda event: event["event"] == "SITEMAP"
                    and event.get("result") == "error",
                    timeout=30.0,
                )
                view = console.wait_for(
                    lambda event: event["event"] == "ERRORVIEW", timeout=30.0
                )
                if error is not None and view is not None:
                    status, detail = classify_escalation(error[0], view[0])
                    results.append(
                        {"status": 1.0 if status == MET else 0.0, "elapsed_s": view[0] - error[0]}
                    )
                else:
                    results.append({"status": 0.0, "elapsed_s": -1.0})
                evidence.append(f"logs/console-{device.id}-escalation-{attempt}.log")
                if console.trace_count():
                    self.recorder.add_excerpt(
                        f"{device.id}-escalation-{attempt}", console.excerpt(10)
                    )
            finally:
                console.stop()
        ok = len(results) == 2 and all(item["status"] == 1.0 for item in results)
        detail = (
            "both runs escalated within the documented window"
            if ok
            else "escalation was not reproducible: "
            + ", ".join(f"{item['elapsed_s']:.1f}s" for item in results)
        )
        self.record(
            "timing.escalation",
            MET if ok else NOT_MET,
            detail,
            evidence=evidence,
            measurements={
                f"run{index}_elapsed_s": item["elapsed_s"]
                for index, item in enumerate(results, start=1)
            },
        )
        return {"status": MET if ok else NOT_MET, "runs": results}

    # -- screenshots -------------------------------------------------------
    def run_occlusion(self, simulator: Simulator) -> Dict[str, Any]:
        baseline, dimensions = simulator.capture("occlusion-baseline")
        if baseline is None:
            self.record("screenshot.occluded", SKIPPED, "no baseline screenshot")
            self.record("screenshot.focusless", SKIPPED, "no baseline screenshot")
            return {"status": SKIPPED}
        xmessage = self.tools.get("xmessage")
        if not xmessage:
            self.record("screenshot.occluded", SKIPPED, "xmessage is not installed")
            return {"status": SKIPPED}
        process = subprocess.Popen(
            [xmessage, "-geometry", "600x300+0+0", "-title", "spike occlusion", "occluded"],
            env=dict(os.environ, DISPLAY=self.display),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(1.0)
            covered, covered_dims = simulator.capture("occlusion-covered")
            status = MET if covered is not None and covered_dims is not None else NOT_MET
            self.record(
                "screenshot.occluded",
                status,
                (
                    f"captured {covered_dims} with an overlapping window"
                    if status == MET
                    else "capture failed with an overlapping window"
                ),
                evidence=[self.evidence(path) for path in (baseline, covered) if path],
            )
            xdotool = shutil.which("xdotool")
            if xdotool:
                subprocess.run(
                    [xdotool, "windowfocus", "root"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    env=dict(os.environ, DISPLAY=self.display),
                )
            time.sleep(0.5)
            focusless, focusless_dims = simulator.capture("occlusion-focusless")
            status = MET if focusless is not None and focusless_dims is not None else NOT_MET
            self.record(
                "screenshot.focusless",
                status,
                (
                    f"captured {focusless_dims} without focus"
                    if status == MET
                    else "capture failed without focus"
                ),
                evidence=[self.evidence(path) for path in (focusless,) if path],
            )
            return {"status": status}
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    # -- aggregation -------------------------------------------------------
    def record_aggregates(self, outcomes: Sequence[Dict[str, Any]]) -> None:
        def all_true(key: str) -> bool:
            return bool(outcomes) and all(bool(outcome.get(key)) for outcome in outcomes)

        simulator_ok = all_true("simulator_launches")
        self.record(
            "simulator.launches",
            MET if simulator_ok else NOT_MET,
            "; ".join(
                f"{outcome['device']}: {outcome.get('simulator_detail', 'not started')}"
                for outcome in outcomes
            )
            or "no device was attempted",
            evidence=["logs/simulator.log"],
            measurements={
                f"startup_s_{outcome['device']}": float(outcome.get("startup_s", 0.0))
                for outcome in outcomes
            },
        )

        renders = [bool(outcome.get("rendered")) for outcome in outcomes]
        evidence = [
            self.evidence(outcome.get("screenshot"))
            for outcome in outcomes
            if outcome.get("screenshot") is not None
        ]
        self.record(
            "app.renders",
            MET if renders and all(renders) else NOT_MET,
            "; ".join(
                f"{outcome['device']}: "
                + (
                    f"first E2E:VIEW after {outcome['startup_to_view_s']:.1f}s"
                    if outcome.get("startup_to_view_s") is not None
                    else "no E2E:VIEW"
                )
                for outcome in outcomes
            )
            or "no device was attempted",
            evidence=evidence,
            measurements={
                f"startup_to_view_s_{outcome['device']}": float(
                    outcome.get("startup_to_view_s", 0.0)
                )
                for outcome in outcomes
            },
        )

        started = all_true("monkeydo_started")
        self.record(
            "monkeydo.launches",
            MET if started else NOT_MET,
            "monkeydo started for every device" if started else "monkeydo did not start",
            evidence=[f"logs/console-{outcome['device']}.log" for outcome in outcomes],
        )

        traces = [int(outcome.get("trace_lines", 0)) for outcome in outcomes]
        trace_ok = bool(traces) and all(count > 0 for count in traces)
        self.record(
            "console.trace_lines",
            MET if trace_ok else NOT_MET,
            "; ".join(
                f"{outcome['device']}: {outcome.get('trace_lines', 0)} E2E line(s)"
                for outcome in outcomes
            )
            or "no console was captured",
            evidence=[f"logs/console-{outcome['device']}.log" for outcome in outcomes],
            measurements={
                f"trace_lines_{outcome['device']}": float(outcome.get("trace_lines", 0))
                for outcome in outcomes
            },
        )

        watchface = [
            outcome
            for outcome in outcomes
            if outcome.get("dimensions") is not None
        ]
        exact = [
            outcome
            for outcome in watchface
            if outcome["dimensions"] == (self.devices[outcome["device"]].width, self.devices[outcome["device"]].height)
        ]
        self.record(
            "screenshot.watchface_only",
            MET if watchface and len(exact) == len(outcomes) else NOT_MET,
            "; ".join(
                f"{outcome['device']}: {outcome.get('dimensions')} "
                f"(expected {self.devices[outcome['device']].width}x"
                f"{self.devices[outcome['device']].height})"
                for outcome in outcomes
            )
            or "no screenshot",
            evidence=[
                self.evidence(outcome.get("screenshot"))
                for outcome in outcomes
                if outcome.get("screenshot") is not None
            ],
        )

    # -- entry point -------------------------------------------------------
    def run(self) -> int:
        self.recorder.flush()
        if not self.preflight():
            return EXIT_PREREQ
        if self.args.preflight_only:
            self.log("preflight: ok")
            return EXIT_OK
        self.record("preflight", MET, "all tools, the display and the SDK are available")
        try:
            self.prepare_prefix()
            button = self.devices.get(self.args.button_device)
            touch = self.devices.get(self.args.touch_device)
            outcomes: List[Dict[str, Any]] = []
            if button is None or touch is None:
                self.recorder.prerequisites = [
                    f"device matrix (unknown device(s): "
                    f"{self.args.button_device}, {self.args.touch_device})"
                ]
                self.recorder.flush()
                return EXIT_PREREQ
            if not (button.supports_buttons and touch.supports_touch):
                self.recorder.prerequisites = [
                    f"device matrix ({button.id} is not a button device or "
                    f"{touch.id} is not a touch device)"
                ]
                self.recorder.flush()
                return EXIT_PREREQ
            outcomes.append(self.run_device(button, primary=True))
            outcomes.append(self.run_device(touch, primary=False))
            self.record_aggregates(outcomes)
        except Exception as error:  # noqa: BLE001 - a probe crash is a result
            self.record(
                "probe.crash",
                NOT_MET,
                f"{type(error).__name__}: {error}",
                evidence=["logs/probe.log"],
            )
            self.recorder.flush()
            return EXIT_CRASH
        self.recorder.flush()
        return EXIT_OK


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------


def parse_intervals(text: str) -> Tuple[int, ...]:
    values: List[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part)
        if not 0 <= value <= 60000:
            raise argparse.ArgumentTypeError(
                f"poll interval must be 0..60000 ms, got {value}"
            )
        values.append(value)
    if not values:
        raise argparse.ArgumentTypeError("at least one poll interval is required")
    return tuple(values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spike.probe",
        description="Run the #370 procedure inside the E2E runner image",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--artifacts", default="/spike-artifacts", help="writable artifacts directory")
    parser.add_argument("--repo", default="/work", help="read-only checkout mount")
    parser.add_argument("--button-device", default="fenix6pro", help="button device id from the matrix")
    parser.add_argument("--touch-device", default="venu3", help="touch device id from the matrix")
    parser.add_argument("--language", default="deu", help="language code to switch to")
    parser.add_argument(
        "--poll-intervals",
        type=parse_intervals,
        default=(0, 1000),
        help="comma-separated pollingInterval values in ms",
    )
    parser.add_argument("--startup-timeout", type=float, default=90.0, help="seconds to wait for a launch")
    parser.add_argument("--poll-window", type=float, default=12.0, help="seconds of console to measure per interval")
    parser.add_argument("--input-delay", type=float, default=0.6, help="seconds between injected inputs")
    parser.add_argument("--wineprefix", default="/tmp/openhab-garmin-spike-prefix")
    parser.add_argument("--display", default=os.environ.get("DISPLAY", ":99"))
    parser.add_argument(
        "--flush",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="pass -f to monkeydo (retried without it when unsupported)",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="check tools, the display and the SDK, then exit",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return SpikeProbe(args).run()


if __name__ == "__main__":  # pragma: no cover - exercised inside the container
    raise SystemExit(main())