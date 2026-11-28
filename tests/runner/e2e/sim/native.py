"""The native (GTK) Linux simulator driver.

Why this driver exists
----------------------

The suite needs a connection-mode change **while the app runs**, and the only
route to it is the simulator's own menu. Under Wine the menu popups are drawn
inside the simulator window, so X cannot see their geometry and automation there
is pixel probing. The native build's menus are GTK widgets and therefore real X
windows; the whole interaction is a mnemonic plus arrow keys
(``sim/menus.py`` records the measured model). That is the capability, and it is
why ``native`` is the target driver rather than a second equal to ``wine``.

What is measured, and what is not
---------------------------------

Measured on the Windows SDK 9.3.0 ``simulator.exe`` (2026-11-28, the same binary
the guest runs under Wine): the menu bar is a real Win32 menu, its items have
stable ids, and selecting one changes the menu's check state — so the *model*
the key plan navigates is real.

Not measured anywhere yet: the native GTK build itself. The #402 spike proved
launch, input, text and HTTP for it, but not the menu plan, and this change was
written without a Linux host. Everything that could be made a pure function is
one and is unit-tested; :meth:`NativeSimulatorDriver.set_connection_mode` and the
transition scenarios must be validated on the dedicated VM before ``native``
replaces ``wine`` as the default. Until then ``wine`` stays the default and
``native`` is opt-in with ``--driver native``.

Setup requirements (from ``tests/docs/simulator-driver.md``, all load-bearing):

* ubuntu:22.04 — 24.04 dropped ``webkit2gtk-4.0``, which the binary links. The
  runner base is pinned to 22.04 for that reason, and `prepare` checks ``ldd``
  so a custom base without the 4.0 stack fails with the library names instead of
  an opaque start failure;
* a regenerated gdk-pixbuf ``loaders.cache``, or the first draw aborts on an
  Adwaita SVG;
* ``XDG_DATA_DIRS=/usr/local/share:/usr/share``, ``GTK_THEME=Adwaita`` and
  ``GDK_BACKEND=x11``, or GTK never maps a window;
* no window manager; ``Xvfb`` alone is enough and ``matchbox`` breaks the
  display rectangle;
* device definitions under ``$HOME/.Garmin/ConnectIQ/Devices`` and the device
  font pack under ``$HOME/.Garmin/ConnectIQ/Fonts``;
* the simulator runs **in place** from the staged SDK. Its resources are
  resolved relative to the binary, and copying the binary alone makes its first
  image load assert (`image.IsOk()` in `InitFromImage`). The driver only sets
  the exec bit on the staged copy; it never copies the binary out;
* the native window is 446x700 for fenix7 on this build (Wine's is 409x600), so
  a whole-window golden is driver-specific and is re-captured, not compared;
* the single-instance lock ``$HOME/Sim-root``, which a simulator that died
  without being reaped leaves behind as a zombie PID.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..sdk import require_native_simulator
from .base import CONNECTION_TRACE_MODES, DriverError
from .menus import (
    connection_menu_selections,
    menu_key_plan,
    new_window_ids,
)
from .wine import (
    DEFAULT_KEY_DELAY_MS,
    DEFAULT_READY_TIMEOUT,
    MODE_INI_VALUES,
    ScreenRect,
    WineSimulatorDriver,
    coordinate_mapper,
    crop_command,
    import_command,
    parse_ciq_log,
    parse_screen_rect,
    png_problem,
    tail,
    xdotool_key_command,
)

#: The native simulator's single-instance lock: a file in ``$HOME`` holding the
#: PID of the running instance. A simulator that died without being reaped stays
#: a zombie, ``kill(pid, 0)`` still succeeds, and every later start exits with
#: code 255 after only an AT-SPI warning. Sweeping the file is the recovery.
NATIVE_LOCK_NAME = "Sim-root"

DEFAULT_ARTIFACT_DIR = Path(tempfile.gettempdir()) / "openhab-garmin-e2e" / "native"

#: Where the native simulator keeps per-run data. Measured on the VM: it writes
#: ``/tmp/com.garmin.connectiq/GARMIN/APPS/...`` — the same layout as Wine's
#: prefix tree, rooted at ``$TMPDIR`` instead of the prefix. The app's sitemap
#: cache lives under ``APPS/DATA``; ``APPS/SETTINGS`` holds the compiled
#: properties and ``GARMIN/Settings`` the device settings, and neither may be
#: cleared or the app fails to initialise.
NATIVE_PERSISTENCE_DIR = "com.garmin.connectiq"

#: Directories the native simulator uses, relative to its persistence root (and
#: to ``$HOME/.Garmin/ConnectIQ`` for a build that keeps its data there). Only
#: these two are cleared; SETTINGS, Settings and LOGS are never touched.
NATIVE_APP_DATA_HINTS = (
    "GARMIN/APPS/DATA",
    "GARMIN/APPS/TEMP",
)
NATIVE_CIQ_LOG_HINTS = ("GARMIN/APPS/LOGS/CIQ_LOG.YML",)
NATIVE_INI_HINT = "simulator.ini"

#: Pin the device canvas instead of detecting it, in screen coordinates:
#: ``x,y,width,height``. The native build draws the canvas at a different offset
#: than the device definition's art-space ``display.location``; the escape hatch
#: exists for a build the detector cannot read.
ENV_NATIVE_SCREEN_RECT = "E2E_NATIVE_SCREEN_RECT"

#: Minimum fraction of non-background pixels a screen-sized region must contain
#: before the detector calls it the device canvas.
MIN_CANVAS_INK = 0.005

_PIXEL_LINE = re.compile(r"^\s*(\d+),(\d+):\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


@dataclass(frozen=True)
class CanvasDetection:
    """A screen-sized region found in a window capture."""

    x: int
    y: int
    width: int
    height: int
    ink: int
    area: int

    @property
    def fraction(self) -> float:
        return self.ink / self.area if self.area else 0.0


def parse_pixel_dump(text: str) -> Tuple[Tuple[int, int, int], ...]:
    """Row-major RGB tuples from ImageMagick's ``txt:-`` output.

    ``convert -depth 8 txt:-`` writes ``x,y: (r,g,b) #hex name`` per pixel; the
    header and any alpha channel are ignored.
    """
    pixels: List[Tuple[int, int, int]] = []
    for line in (text or "").splitlines():
        match = _PIXEL_LINE.match(line)
        if match is None:
            continue
        pixels.append((int(match.group(3)), int(match.group(4)), int(match.group(5))))
    return tuple(pixels)


def locate_canvas(
    pixels: Sequence[Tuple[int, int, int]],
    width: int,
    height: int,
    screen_width: int,
    screen_height: int,
    *,
    min_ink_fraction: float = MIN_CANVAS_INK,
) -> Optional[CanvasDetection]:
    """Top-left of the screen-sized region with the most non-background pixels.

    The window's most common colour is its background. The device canvas is the
    one screen-sized region whose pixels differ from it (watch face, text,
    widgets), so the search maximises that count with a summed-area table.
    Returns ``None`` when no region reaches ``min_ink_fraction``: the capture
    then contains no canvas, and a pixel verdict would be meaningless.
    """
    if (
        width <= 0
        or height <= 0
        or screen_width <= 0
        or screen_height <= 0
        or screen_width > width
        or screen_height > height
        or len(pixels) != width * height
    ):
        return None
    background = Counter(pixels).most_common(1)[0][0]
    integral = [[0] * (width + 1) for _ in range(height + 1)]
    for y in range(height):
        row_total = 0
        offset = y * width
        for x in range(width):
            if pixels[offset + x] != background:
                row_total += 1
            integral[y + 1][x + 1] = integral[y][x + 1] + row_total
    best: Optional[Tuple[int, int]] = None
    best_score = -1
    for oy in range(height - screen_height + 1):
        bottom = integral[oy + screen_height]
        top = integral[oy]
        for ox in range(width - screen_width + 1):
            right = ox + screen_width
            score = bottom[right] - top[right] - bottom[ox] + top[ox]
            if score > best_score:
                best_score = score
                best = (ox, oy)
    area = screen_width * screen_height
    if best is None or best_score < max(1, int(area * min_ink_fraction)):
        return None
    return CanvasDetection(
        x=best[0],
        y=best[1],
        width=screen_width,
        height=screen_height,
        ink=best_score,
        area=area,
    )

#: Tools the driver runs. ``import``, ``convert`` and ``identify`` are
#: ImageMagick; ``xwininfo`` is how the driver sees that a menu popup opened,
#: which under Wine it cannot.
NATIVE_TOOLS = ("xdotool", "xwininfo", "xdpyinfo", "import", "convert", "identify")

#: The libraries the native binary needs. Ubuntu 22.04 is the image base because
#: it still ships ``libwebkit2gtk-4.0``, which 24.04 dropped. This list feeds the
#: ``ldd`` message, so a *custom* ``RUNNER_BASE_IMAGE`` without the 4.0 stack
#: fails with the missing library and these package names instead of an opaque
#: start failure; the default base is already correct.
NATIVE_PACKAGES = (
    "libgtk-3-0",
    "libwebkit2gtk-4.0-37",
    "libsecret-1-0",
    "libnotify4",
    "libusb-1.0-0",
    "librsvg2-common",
    "xvfb",
    "x11-utils",
    "x11-xkb-utils",
    "xauth",
    "xdotool",
    "imagemagick",
)


def parse_missing_libraries(ldd_output: str) -> Tuple[str, ...]:
    """Library names ``ldd`` reported as ``not found``, in order, deduplicated.

    ``ldd`` prints ``libwebkit2gtk-4.0.so.37 => not found``; the name before the
    arrow is what a package search needs. A line without an arrow but containing
    ``not found`` is kept whole, so an unusual output is still reported.
    """
    missing: List[str] = []
    for line in (ldd_output or "").splitlines():
        if "not found" not in line:
            continue
        name = line.split("=>")[0].strip() or line.strip()
        if name and name not in missing:
            missing.append(name)
    return tuple(missing)


def home_dir() -> Path:
    """``$HOME`` as the simulator sees it, falling back to ``Path.home()``.

    ``$HOME`` is used rather than ``Path.home()`` because the simulator resolves
    its lock, devices and fonts from the environment it is started with, and the
    driver must sweep the same file it will create.
    """
    return Path(os.environ.get("HOME") or Path.home())


class NativeSimulatorDriver(WineSimulatorDriver):
    """Drive ``linux/bin/simulator`` (GTK) directly on an X display.

    The X11, monkeydo and file-system machinery is shared with the Wine driver:
    both push the app with ``monkeydo`` and take input through ``xdotool``. Only
    the process that is launched, the paths below ``$HOME`` and the menu
    capability differ, so the Wine driver's implementation is reused rather than
    forked. ``tests/docs/simulator-driver.md`` explains the migration: ``native``
    becomes the default once it passes the same corpus on the VM, and ``wine``
    is the fallback until then. The subclass is a migration device, not a
    permanent shape: after the switch the shared X11/monkeydo core should move
    into a driver-neutral base and the Wine-specific parts should stay with the
    fallback.
    """

    name: str = "native"
    supports_events: bool = True
    supports_screenshots: bool = True
    supports_restart_in_mode: bool = True
    supports_menu_probe: bool = True
    supports_connection_switch: bool = True
    #: The native GTK top-level has no input child, so ``getmouselocation`` can
    #: report window 0 under a pointer that is inside it; the pointer guard falls
    #: back to the window rectangle for that case.
    pointer_top_level_fallback: bool = True
    provisions_language: bool = True

    def __init__(
        self,
        layout,  # type: ignore[no-untyped-def]
        *,
        display: Optional[str] = None,
        artifact_dir: Optional[Path] = None,
        log=None,  # type: ignore[no-untyped-def]
    ) -> None:
        super().__init__(
            layout,
            display=display,
            artifact_dir=Path(artifact_dir) if artifact_dir else DEFAULT_ARTIFACT_DIR,
            log=log,
        )
        self._simulator_binary: Optional[Path] = None
        #: The device canvas inside the window, measured after the app is up.
        #: ``display.location`` is art space and lands on window background on
        #: this build, so clicks must not use it.
        self._detected_screen_rect: Optional[ScreenRect] = None

    # -- capability probe --------------------------------------------------
    @classmethod
    def is_available(cls, layout) -> bool:  # type: ignore[no-untyped-def]
        """True when the native simulator and every X tool are present."""
        if not sys.platform.startswith("linux"):
            return False
        try:
            require_native_simulator(layout)
        except Exception:  # noqa: BLE001 - a probe must not raise
            return False
        if layout.monkeydo is None:
            return False
        return all(shutil.which(tool) for tool in NATIVE_TOOLS)

    # -- paths -------------------------------------------------------------
    @property
    def connectiq_root(self) -> Path:
        return home_dir() / ".Garmin" / "ConnectIQ"

    @property
    def persistence_root(self) -> Path:
        """Where the native simulator keeps its per-run tree (measured: /tmp)."""
        return (
            Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
            / NATIVE_PERSISTENCE_DIR
        )

    @property
    def lock_path(self) -> Path:
        return home_dir() / NATIVE_LOCK_NAME

    def _ini_candidates(self) -> List[Path]:
        """The native simulator's ini, at the ConnectIQ root (flat key=value)."""
        return [self.connectiq_root / NATIVE_INI_HINT]

    def _app_data_dirs(self) -> List[Path]:
        """The sitemap cache directories, under the measured root and the home one.

        Only ``GARMIN/APPS/DATA`` and ``GARMIN/APPS/TEMP`` are returned: the
        app initialises from ``APPS/SETTINGS`` and ``GARMIN/Settings``, and
        removing either kills it during start-up.
        """
        found: List[Path] = []
        for root in (self.persistence_root, self.connectiq_root):
            for hint in NATIVE_APP_DATA_HINTS:
                path = root / hint
                if path.is_dir() and path not in found:
                    found.append(path)
        return found

    def _ciq_log_source(self) -> Optional[Path]:
        for root in (self.persistence_root, self.connectiq_root):
            for hint in NATIVE_CIQ_LOG_HINTS:
                candidate = root / hint
                if candidate.is_file():
                    return candidate
        return None

    # -- contract ----------------------------------------------------------
    def prepare(self, prg: Path) -> None:
        """Validate the tools, make the simulator executable and stage the PRG."""
        source = Path(prg)
        if not source.is_file():
            raise DriverError(f"cannot stage {source}: it is not a file")
        binary = require_native_simulator(self.layout)
        if self.layout.monkeydo is None:
            raise DriverError(
                f"{self.layout.root} has no Linux monkeydo: expected "
                "linux/bin/monkeydo or bin/monkeydo. The Windows simulator's "
                "shell.exe is not a replacement."
            )
        for tool in NATIVE_TOOLS:
            self._require_tool(tool)

        self._simulator_binary = self._ensure_executable(binary)
        self._verify_runtime_libraries(self._simulator_binary)
        self._sweep_existing_simulator()

        target = self.stage_dir / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        self._prg = target
        self._record(f"staged PRG {source} -> {target}")

    def _ensure_executable(self, binary: Path) -> Path:
        """Make the staged simulator executable **in place**.

        The binary must never be copied out of its SDK tree: the simulator
        resolves its resources relative to its own path, and a lone copy fails
        its first image load with ``assert "image.IsOk()" failed in
        InitFromImage()`` (measured on the VM). The entrypoint stages the whole
        SDK into a writable directory (``CIQ_SDK_WORKDIR``), which is where this
        runs from, so setting the exec bit is all that is needed. If even that
        fails, say why instead of copying and producing the assert later.
        """
        if os.access(binary, os.X_OK):
            self._record(f"native simulator {binary} is executable; running it in place")
            return binary
        try:
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError as error:
            raise DriverError(
                f"{binary} is not executable and could not be made executable "
                f"({error}).\n"
                "The native simulator must run in place: it resolves its resources "
                "relative to its own path, and copying just the binary out of the "
                "SDK fails with `assert \"image.IsOk()\" failed in InitFromImage`. "
                "Run it from the entrypoint's writable staged copy "
                "(CIQ_SDK_WORKDIR, normally /tmp/ciq-sdk), or chmod that file once."
            ) from error
        self._record(
            f"native simulator {binary} was not executable; set the exec bit in "
            "place (the binary must stay in its SDK tree)"
        )
        return binary

    def _verify_runtime_libraries(self, binary: Path) -> None:
        """Fail before a start when the image lacks the native build's libraries.

        The runner base is pinned to ``ubuntu:22.04`` because the binary links
        ``webkit2gtk-4.0``, which 24.04 dropped. A custom ``RUNNER_BASE_IMAGE``
        can still be wrong, so ``ldd`` names exactly what is missing — the
        difference between an actionable image change and a mystery.
        """
        ldd = shutil.which("ldd")
        if ldd is None:
            self._record(
                "warning: ldd is not available, so the native simulator's shared "
                "libraries were not checked before launch"
            )
            return
        try:
            completed = subprocess.run(
                [ldd, str(binary)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self._record(f"warning: ldd could not inspect {binary}: {error}")
            return
        missing = parse_missing_libraries(
            (completed.stdout or "") + (completed.stderr or "")
        )
        if missing:
            raise DriverError(
                "the native simulator cannot start: these shared libraries are "
                "missing:\n  " + "\n  ".join(missing) + "\n"
                "The native build requires an ubuntu:22.04 base (24.04 dropped "
                "webkit2gtk-4.0) with: " + ", ".join(NATIVE_PACKAGES) + ".\n"
                "The runner image is pinned to 22.04 for this reason; check "
                "RUNNER_BASE_IMAGE in tests/compose/.env if this base was "
                "overridden. See tests/docs/simulator-driver.md."
            )
        self._record(f"native simulator {binary}: no missing shared libraries")

    def _start_simulator(self) -> None:
        binary = self._simulator_binary or self._ensure_executable(
            require_native_simulator(self.layout)
        )
        self._simulator_binary = binary
        self.simulator_log.parent.mkdir(parents=True, exist_ok=True)
        self._simulator_handle = self.simulator_log.open("w", encoding="utf-8")
        self._simulator_process = subprocess.Popen(
            [str(binary)],
            # The simulator resolves resources relative to its own directory; the
            # VM's working invocation was `cd <sdk>/linux/bin; ./simulator`.
            cwd=str(binary.parent),
            stdout=self._simulator_handle,
            stderr=subprocess.STDOUT,
            env=self._env(),
        )
        self._record(
            f"simulator PID {self._simulator_process.pid}: {binary} "
            "(native GTK build, no Wine, run in place)"
        )
        self._await_simulator_window()
        self._await_simulator_transport()

    def _start_with(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        # A restart measures the canvas again; never reuse a previous geometry.
        self._detected_screen_rect = None
        super()._start_with(*args, **kwargs)

    def stop(self) -> None:
        """Stop the app and simulator by PID, then release the single-instance lock."""
        super().stop()
        self._detected_screen_rect = None
        self._release_lock()

    def wait_ready(self, timeout_s: float = DEFAULT_READY_TIMEOUT) -> None:
        """Wait for the app, then measure the device canvas in the window.

        The canvas is measured rather than assumed: the device definition's
        ``display.location`` is art space, and on this build the canvas is at a
        different offset (the declared rectangle lands on window background).
        Taps, declared hit areas and the golden crop all use the measured rect,
        so a wrong one would make every pixel verdict meaningless.
        """
        super().wait_ready(timeout_s)
        self._detect_screen_rect()

    def _launch_plans(self, device):  # type: ignore[no-untyped-def]
        """Try the keydown/keyup hold first on native.

        Measured on the VM: plain XTEST key taps do nothing to this window and
        declared hit areas were refused by the old pointer guard, while the
        keydown/keyup hold opened the app. Keep the other variants as fallbacks.
        """
        plans = super()._launch_plans(device)
        if not device.supports_buttons:
            return plans
        order = {
            "keyboard keydown/keyup": 0,
            "declared hit areas (hold)": 1,
            "keyboard taps": 2,
            "declared hit areas": 3,
        }
        return sorted(plans, key=lambda plan: order.get(plan[0], 99))

    # -- capture and geometry ---------------------------------------------
    def _root_capture(self, path: Path) -> Path:
        """Capture the whole X root; ``import -window <id>`` is black here."""
        path.parent.mkdir(parents=True, exist_ok=True)
        completed = self._run(
            import_command(self.display, "root", path),
            check=False,
            action="root capture",
        )
        if completed.returncode != 0 or not path.is_file():
            raise DriverError(
                f"ImageMagick could not capture the root window on {self.display}: "
                f"exit {completed.returncode}\n"
                f"{tail((completed.stderr or '').splitlines())}"
            )
        return path

    def _colour_count(self, path: Path) -> Optional[int]:
        try:
            completed = self._run(
                ["identify", "-format", "%k", str(path)],
                check=False,
                action="identify",
            )
        except DriverError:
            return None
        try:
            return int((completed.stdout or "").strip())
        except ValueError:
            return None

    def screenshot(self, path: Path) -> Path:
        """Capture the window via the root, then crop it out.

        ``import -window <id>`` returns a single black colour for the native GTK
        window (measured: 1 colour, where a root capture has 203). The root holds
        the content, so the window rectangle is cropped from it. A capture with a
        single colour is refused: it cannot support any pixel verdict, and
        passing it was how a black screen looked like a screenshot.
        """
        window = self._require_window()
        self._park_pointer(window)
        geometry = self._geometry(window)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        root = self._root_capture(
            target.with_name(f"{target.stem}-root{target.suffix}")
        )
        rect = ScreenRect(
            x=geometry.get("x", 0),
            y=geometry.get("y", 0),
            width=geometry.get("width", 0),
            height=geometry.get("height", 0),
        )
        if rect.width <= 0 or rect.height <= 0:
            raise DriverError(f"the simulator window {window} has no usable size")
        completed = self._run(
            crop_command(root, rect, target),
            check=False,
            action="crop the simulator window out of the root capture",
        )
        try:
            root.unlink()
        except OSError:
            pass
        if completed.returncode != 0 or not target.is_file():
            raise DriverError(
                f"could not crop {rect.width}x{rect.height}+{rect.x}+{rect.y} out "
                f"of the root capture: exit {completed.returncode}\n"
                f"{tail((completed.stderr or '').splitlines())}"
            )
        reason = png_problem(target)
        if reason:
            raise DriverError(f"{target}: {reason}")
        colours = self._colour_count(target)
        if colours is not None and colours <= 1:
            raise DriverError(
                f"{target} has a single colour, so no pixel verdict is possible.\n"
                "The capture is the root window cropped to the simulator window "
                f"({rect.width}x{rect.height} at +{rect.x}+{rect.y}). A uniform "
                "crop means the window rectangle is wrong or the app has not "
                "painted; check logs/simulator.log and the window geometry."
            )
        self._record(
            f"screenshot -> {target} (root capture cropped to the "
            f"{rect.width}x{rect.height} window at +{rect.x}+{rect.y}; "
            f"{colours if colours is not None else '?'} colour(s))"
        )
        return target

    def _detect_screen_rect(self) -> None:
        """Find the device canvas in the window and refuse a wrong one."""
        device = self._require_started()
        window = self._require_window()
        declared = (device.screen_width, device.screen_height)
        override = parse_screen_rect(os.environ.get(ENV_NATIVE_SCREEN_RECT, ""))
        if override is not None:
            if (override.width, override.height) != declared:
                raise DriverError(
                    f"{ENV_NATIVE_SCREEN_RECT} is {override.width}x"
                    f"{override.height}, but {device.id} declares a "
                    f"{declared[0]}x{declared[1]} screen. The canvas must be the "
                    "device's declared size; fix the pin."
                )
            self._detected_screen_rect = override
            self._record(
                f"device canvas pinned by {ENV_NATIVE_SCREEN_RECT} at "
                f"+{override.x}+{override.y} {override.width}x{override.height}"
            )
            return

        geometry = self._geometry(window)
        width, height = geometry.get("width", 0), geometry.get("height", 0)
        root = self._root_capture(self.artifact_dir / "logs" / "screen-detect-root.png")
        crop = root.with_name("screen-detect-window.png")
        rect = ScreenRect(
            x=geometry.get("x", 0),
            y=geometry.get("y", 0),
            width=width,
            height=height,
        )
        completed = self._run(
            crop_command(root, rect, crop),
            check=False,
            action="crop for canvas detection",
        )
        if completed.returncode != 0 or not crop.is_file():
            raise DriverError(
                f"could not crop the simulator window for detection: exit "
                f"{completed.returncode}\n{tail((completed.stderr or '').splitlines())}"
            )
        dump = self._run(
            ["convert", str(crop), "-depth", "8", "txt:-"],
            check=False,
            action="pixel dump",
        )
        pixels = parse_pixel_dump(dump.stdout or "")
        found = locate_canvas(pixels, width, height, declared[0], declared[1])
        if found is None:
            background = (
                Counter(pixels).most_common(1)[0][0] if pixels else "<no pixels>"
            )
            raise DriverError(
                f"could not find a {declared[0]}x{declared[1]} device canvas in the "
                f"{width}x{height} simulator window: no screen-sized region has "
                f"more than {MIN_CANVAS_INK:.1%} pixels differing from the window "
                f"background {background}.\n"
                f"The window capture is kept at {crop}.\n"
                "Measure the canvas and pin "
                f"{ENV_NATIVE_SCREEN_RECT}=x,y,width,height; taps, the golden crop "
                "and every pixel verdict need it."
            )
        self._detected_screen_rect = ScreenRect(
            x=geometry.get("x", 0) + found.x,
            y=geometry.get("y", 0) + found.y,
            width=found.width,
            height=found.height,
        )
        self._record(
            f"device canvas detected at +{found.x}+{found.y} in the window "
            f"({self._detected_screen_rect.x},{self._detected_screen_rect.y} on "
            f"screen), {found.width}x{found.height}, ink {found.fraction:.1%} of the "
            f"region; declared display.location {self._definition.display} is art "
            "space and is not used for clicks"
        )

    def _screen_rect_for(self, window: str, device):  # type: ignore[no-untyped-def]
        if self._detected_screen_rect is not None:
            return self._detected_screen_rect
        return super()._screen_rect_for(window, device)

    def _mapper(self):  # type: ignore[no-untyped-def]
        if (
            self._detected_screen_rect is not None
            and self._definition.display is not None
        ):
            # Re-anchor the declared art space onto the measured canvas; the
            # declared size is asserted against the canvas size, so scale is 1.
            return coordinate_mapper(
                self._definition.display, self._detected_screen_rect
            )
        return super()._mapper()

    def _env(self) -> dict:
        """The environment the native simulator needs to map a window.

        ``XDG_DATA_DIRS`` is not cosmetic: without it GTK cannot load Adwaita's
        settings and never maps a window. The other two make the theme and
        backend explicit instead of inherited. See the module docstring.
        """
        env = dict(os.environ)
        env["DISPLAY"] = self.display
        if not env.get("XDG_DATA_DIRS"):
            env["XDG_DATA_DIRS"] = "/usr/local/share:/usr/share"
        env.setdefault("GTK_THEME", "Adwaita")
        env.setdefault("GDK_BACKEND", "x11")
        return env

    def _require_tool(self, name: str) -> None:
        if shutil.which(name) is not None:
            return
        raise DriverError(
            f"{name} is not installed; the native driver needs xdotool, xwininfo, "
            "xdpyinfo and ImageMagick (`import`) on the runner image. See "
            "tests/docs/simulator-driver.md (the native Linux simulator)."
        )

    def _stage_hint(self, stage: str) -> str:
        """Native advice; the Wine text names a prefix this driver does not have."""
        if stage == "simulator-window":
            return (
                "The native simulator did not map a window. Read "
                "logs/simulator.log first: an `assert \"image.IsOk()\" failed in "
                "InitFromImage` means the binary was run outside its SDK tree, "
                "because its resources are resolved relative to it. Check that it "
                "runs in place from the staged SDK and that the file is "
                "executable, then DISPLAY, XDG_DATA_DIRS, GTK_THEME and "
                "GDK_BACKEND; see the module docstring."
            )
        if stage == "app-launch":
            return (
                "`monkeydo` must stay running and stream E2E: lines. There is no "
                "Wine prefix here: check that the staged SDK has linux/bin/monkeydo "
                "and that the device definitions are under "
                "$HOME/.Garmin/ConnectIQ/Devices, then run "
                "`monkeydo <prg> <device>` by hand. logs/console.log has its output."
            )
        return super()._stage_hint(stage)

    # -- the single-instance lock -----------------------------------------
    def _lock_pid(self) -> Optional[int]:
        try:
            text = self.lock_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        try:
            return int(text.splitlines()[0])
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _process_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _sweep_existing_simulator(self) -> None:
        """Stop a simulator left behind and remove its stale lock.

        The lock is authoritative: it holds the PID. A zombie still answers
        ``kill(pid, 0)``, so the lock is removed even when the process cannot be
        signalled — that file is exactly what makes every later start exit with
        code 255.
        """
        stale_windows: List[str] = []
        try:
            stale_windows = self._find_windows() + self._error_windows()
        except DriverError:
            stale_windows = []

        pid = self._lock_pid()
        if pid is None:
            if self.lock_path.exists():
                self._record(
                    f"{self.lock_path} exists but holds no PID; removing it "
                    "(it makes the simulator exit with code 255)"
                )
                self._unlink_lock()
            return
        if not self._process_alive(pid):
            self._record(
                f"{self.lock_path} names PID {pid}, which is gone; removing the "
                "stale lock"
            )
            self._unlink_lock()
            return
        self._record(
            f"a native simulator is still running (PID {pid} from {self.lock_path}); "
            "terminating it before starting a new one"
        )
        for sig, wait_s in ((signal.SIGTERM, 10.0), (signal.SIGKILL, 5.0)):
            try:
                os.kill(pid, sig)
            except OSError:
                break
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                if not self._process_alive(pid):
                    break
                time.sleep(0.25)
        self._unlink_lock()
        if stale_windows:
            self._record(
                "window(s) still present after the lock sweep: "
                + ", ".join(stale_windows)
                + "; a zombie or a second instance may remain"
            )

    def _release_lock(self) -> None:
        pid = self._lock_pid()
        if pid is None:
            return
        own_pid = getattr(self._simulator_process, "pid", None)
        if self._process_alive(pid) and pid != own_pid:
            self._record(
                f"leaving {self.lock_path} alone: it names PID {pid}, which is "
                "still alive and is not this run's simulator"
            )
            return
        self._unlink_lock()

    def _unlink_lock(self) -> None:
        try:
            self.lock_path.unlink()
            self._record(f"removed {self.lock_path}")
        except FileNotFoundError:
            pass
        except OSError as error:
            self._record(f"warning: could not remove {self.lock_path}: {error}")

    # -- the capability ----------------------------------------------------
    def set_connection_mode(self, mode: str, *, timeout_s: float = 0.0) -> str:
        """Change the simulator's connection state through its own menu.

        Each mode needs two selections (Wi-Fi first, then BLE) so the app never
        observes a half-applied target; see ``menus.CONNECTION_END_STATES``.
        Every key is sent to the focused simulator window with ``xdotool``, and
        the windows that appear after the mnemonic are recorded: on the native
        build a GTK popup is a real X window, which is the evidence Wine cannot
        provide.

        The effect is *not* claimed here. The engine requires a new
        ``E2E:CONNECTION;mode=<target>`` line from the app and fails the step
        without it (the lesson from #421: a click that returns 0 is not proof).
        """
        if mode not in MODE_INI_VALUES:
            raise DriverError(
                f"unknown mode {mode!r}; expected one of {sorted(MODE_INI_VALUES)}"
            )
        window = self._require_window()
        self._focus_window(window)
        evidence: List[str] = []
        for selection in connection_menu_selections(mode):
            keys = menu_key_plan(selection)
            before = self._children_ids()
            for keysym in keys:
                self._run(
                    xdotool_key_command(keysym, 1),
                    action=f"menu key {keysym}",
                )
                time.sleep(DEFAULT_KEY_DELAY_MS / 1000.0)
            popups = new_window_ids(before, self._children_ids())
            detail = (
                f"{selection.describe()} <- {' '.join(keys)}; "
                f"popup window(s) {popups or 'none'}"
            )
            evidence.append(detail)
            self._record(f"connection menu: {detail}")
        summary = "; ".join(evidence)
        self._record(
            f"connection mode -> {mode}: {summary}. The simulator state is not "
            "the claim; the app must print E2E:CONNECTION;mode="
            f"{CONNECTION_TRACE_MODES[mode]} or the step fails."
        )
        return summary

    # -- simulator runtime log --------------------------------------------
    def _collect_ciq_log(self) -> None:
        """Copy and classify ``CIQ_LOG.YML`` from the native simulator's tree."""
        source = self._ciq_log_source()
        if source is None:
            self._record(
                "no CIQ_LOG.YML below "
                f"{self.persistence_root} or {self.connectiq_root}; nothing to classify"
            )
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
        if errors:
            self._record(
                f"CIQ log: {len(errors)} error(s), first: {errors[0].message}"
            )


__all__ = [
    "NATIVE_APP_DATA_HINTS",
    "NATIVE_LOCK_NAME",
    "NATIVE_PACKAGES",
    "NATIVE_PERSISTENCE_DIR",
    "NativeSimulatorDriver",
    "home_dir",
    "parse_missing_libraries",
]
