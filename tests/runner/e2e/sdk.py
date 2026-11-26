"""Connect IQ SDK discovery and validation.

The SDK is **never** stored in this repository or in any image built from it:
Garmin's SDK agreement does not permit redistribution. The operator mounts or
downloads it and points the harness at it with an environment variable.

Accepted layout (both spellings are supported because the two SDK
distributions a contributor may have differ)::

    <sdk>/
      version.txt                  (or bin/version.txt)
      linux/bin/monkeyc            (or bin/monkeyc)
      linux/bin/monkeydo           (or bin/monkeydo)
      windows/bin/simulator.exe    (or bin/simulator.exe)

Environment variables:

- ``CIQ_SDK_ROOT`` -- preferred, used by Compose and the runner image
- ``CIQ_SDK_PATH`` -- accepted alias, used in issue #365's acceptance criteria
- ``CIQ_DEVICES_ROOT`` -- device definitions directory, when the SDK does not
  ship one at a recognised location
- ``CIQ_DEVICES_MOUNT`` -- read-only mount that holds device definitions
  (default ``/opt/ciq-devices``, empty in the stock stack)
- ``GARMIN_DEVICES_DIR`` -- where :func:`link_devices` stages the Linux copy
  (default ``$HOME/.Garmin/ConnectIQ/Devices``, the path ``monkeyc`` searches)
- ``WINEPREFIX`` -- Wine prefix whose simulator needs its own device definitions
  (default ``$HOME/.wine``); :func:`link_devices` links the same tree into
  ``drive_c/users/<user>/AppData/Roaming/Garmin/ConnectIQ/Devices``

Both SDK variables are validated the same way. A missing SDK, a missing tool or a
version that does not match ``tests/config/sdk.version`` is a hard, early failure
with an actionable message: a version mismatch produces app behaviour that cannot
be compared with anyone else's results.

Device definitions matter for the same reason: ``monkeyc -d <device>`` resolves
the device from ``$HOME/.Garmin/ConnectIQ/Devices``, not from the SDK path, and a
missing definition only surfaces as a failed build much later.
"""

from __future__ import annotations

import getpass
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from .config import ConfigError, read_sdk_version

ENV_VARS = ("CIQ_SDK_ROOT", "CIQ_SDK_PATH")
ENV_DEVICES_ROOT = "CIQ_DEVICES_ROOT"
ENV_DEVICES_MOUNT = "CIQ_DEVICES_MOUNT"
ENV_DEVICES_TARGET = "GARMIN_DEVICES_DIR"
ENV_FONTS_ROOT = "CIQ_FONTS_ROOT"
ENV_FONTS_MOUNT = "CIQ_FONTS_MOUNT"
ENV_FONTS_TARGET = "GARMIN_FONTS_DIR"
ENV_WINE_PREFIX = "WINEPREFIX"

DEFAULT_DEVICES_MOUNT = Path("/opt/ciq-devices")
DEFAULT_FONTS_MOUNT = Path("/opt/ciq-fonts")

#: Suffixes a usable font pack contains. Windows installations hold `.ttf`
#: faces plus `.cft` (compiled font table) files; only the latter are loaded by
#: the simulator, so either marks a populated pack.
_FONT_SUFFIXES = (".cft", ".ttf")

_MONKEYC_CANDIDATES = ("linux/bin/monkeyc", "bin/monkeyc")
_MONKEYDO_CANDIDATES = ("linux/bin/monkeydo", "bin/monkeydo")
_SIMULATOR_CANDIDATES = ("windows/bin/simulator.exe", "bin/simulator.exe")
_VERSION_CANDIDATES = ("version.txt", "bin/version.txt")

#: Where the two SDK distributions a contributor may have put their device
#: definitions. The walk in :func:`find_device_definitions` is the fallback.
_DEVICES_CANDIDATES = (
    "Devices",
    "devices",
    "bin/Devices",
    "bin/devices",
    "linux/bin/Devices",
    "linux/bin/devices",
    "linux/Devices",
    "linux/devices",
    "windows/bin/Devices",
    "windows/bin/devices",
)

#: Marker that makes a directory a device definition root.
_DEVICE_MARKER = "compiler.json"

#: Depth of the fallback walk below the SDK root.
_DEVICES_WALK_DEPTH = 3


class SdkError(RuntimeError):
    """Raised when the Connect IQ SDK is missing or unusable."""


@dataclass(frozen=True)
class SdkLayout:
    """Validated locations inside a Connect IQ SDK."""

    root: Path
    version: str
    version_file: Path
    monkeyc: Path
    monkeydo: Optional[Path]
    simulator: Optional[Path]
    devices: Optional[Path] = None

    @property
    def has_simulator(self) -> bool:
        return self.simulator is not None

    @property
    def has_devices(self) -> bool:
        return self.devices is not None


def _first_existing(root: Path, candidates: Iterable[str]) -> Optional[Path]:
    for relative in candidates:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def sdk_root(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Resolve the SDK root from the environment, or fail with guidance."""
    env = os.environ if environ is None else environ
    for name in ENV_VARS:
        value = env.get(name)
        if value:
            path = Path(value).expanduser()
            if not path.is_dir():
                raise SdkError(
                    f"{name} points at {path}, which is not a directory.\n"
                    "Mount or download the Connect IQ SDK, then set "
                    f"{ENV_VARS[0]} to its root (the directory containing "
                    "'linux/' and 'windows/').\n"
                    "The SDK is not redistributable, so it is never part of "
                    "this repository or of the runner image."
                )
            return path
    raise SdkError(
        "Connect IQ SDK not configured. Set "
        f"{ENV_VARS[0]} (or {ENV_VARS[1]}) to the SDK root.\n"
        "Expected layout: version.txt (or bin/version.txt) + "
        "linux/bin/monkeyc + windows/bin/simulator.exe.\n"
        "The SDK is not redistributable, so it is never part of this "
        "repository or of the runner image; see tests/docs/sdk.md."
    )


def _read_version(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise SdkError(f"cannot read {path}: {error}") from None


def inspect_sdk(root: Path, expected_version: Optional[str] = None) -> SdkLayout:
    """Validate an SDK root and return its layout.

    ``expected_version`` defaults to the pin in ``tests/config/sdk.version``.
    The simulator and ``monkeydo`` are optional so that a protocol-tier-only
    environment (for example a Linux host without Wine) can still build and
    validate the toolchain; a UI scenario fails later, with its own message.
    """
    if expected_version is None:
        try:
            expected_version = read_sdk_version()
        except ConfigError as error:
            raise SdkError(str(error)) from None

    if not root.is_dir():
        raise SdkError(f"SDK root {root} is not a directory")

    version_file = _first_existing(root, _VERSION_CANDIDATES)
    if version_file is None:
        raise SdkError(
            f"{root} does not look like a Connect IQ SDK: no version.txt and no "
            "bin/version.txt."
        )
    version = _read_version(version_file)
    if version != expected_version:
        raise SdkError(
            f"Connect IQ SDK version mismatch: {root} reports {version!r} but "
            f"tests/config/sdk.version pins {expected_version!r}.\n"
            "Install the pinned SDK, or update the pin deliberately and record "
            "why in tests/config/openhab.version's counterpart (sdk.version) "
            "and in the pull request."
        )

    monkeyc = _first_existing(root, _MONKEYC_CANDIDATES)
    if monkeyc is None:
        raise SdkError(
            f"{root} does not contain the Linux compiler: expected "
            f"{' or '.join(_MONKEYC_CANDIDATES)}.\n"
            "The runner image builds the PRG with the Linux monkeyc; the "
            "Windows SDK archive can be added next to it for the simulator."
        )

    return SdkLayout(
        root=root,
        version=version,
        version_file=version_file,
        monkeyc=monkeyc,
        monkeydo=_first_existing(root, _MONKEYDO_CANDIDATES),
        simulator=_first_existing(root, _SIMULATOR_CANDIDATES),
        devices=find_device_definitions(root),
    )


def require_simulator(layout: SdkLayout) -> Path:
    """Return the simulator executable or raise a scenario-time error."""
    if layout.simulator is None:
        raise SdkError(
            f"{layout.root} has no Windows simulator: expected "
            f"{' or '.join(_SIMULATOR_CANDIDATES)}.\n"
            "UI-tier scenarios need the Windows SDK archive (simulator.exe) and "
            "a Wine installation; protocol-tier scenarios do not."
        )
    return layout.simulator


def load_layout(environ: Optional[Mapping[str, str]] = None) -> SdkLayout:
    """Resolve and validate the SDK in one step."""
    return inspect_sdk(sdk_root(environ))


# ---------------------------------------------------------------------------
# device definitions
# ---------------------------------------------------------------------------


def is_device_definitions(path: Path) -> bool:
    """True when ``path`` holds device definition directories.

    A definition directory is one level down and contains ``compiler.json``;
    that is the same marker the connected-device definitions committed in
    ``.github/ConnectIQ/Devices`` use.
    """
    if not path.is_dir():
        return False
    try:
        children = list(path.iterdir())
    except OSError:
        return False
    return any(
        child.is_dir() and (child / _DEVICE_MARKER).is_file() for child in children
    )


def device_definition_count(path: Path) -> int:
    """Number of devices defined in ``path`` (0 when it is not a root)."""
    if not path.is_dir():
        return 0
    try:
        children = list(path.iterdir())
    except OSError:
        return 0
    return sum(
        1 for child in children if child.is_dir() and (child / _DEVICE_MARKER).is_file()
    )


def _walk_for_definitions(root: Path) -> Optional[Path]:
    """Bounded search for a ``devices`` directory below ``root``."""
    base_depth = len(root.parts)
    for dirpath, dirnames, _ in os.walk(root):
        depth = len(Path(dirpath).parts) - base_depth
        if depth >= _DEVICES_WALK_DEPTH:
            dirnames[:] = []
            continue
        for name in sorted(dirnames):
            if name.lower() != "devices":
                continue
            candidate = Path(dirpath) / name
            if is_device_definitions(candidate):
                return candidate
    return None


def device_definition_candidates(
    root: Path,
    environ: Optional[Mapping[str, str]] = None,
) -> Sequence[Path]:
    """Where :func:`find_device_definitions` looks inside the container.

    Exposed so the error message and the documentation name the same paths.
    The ``CIQ_DEVICES_ROOT`` override is named separately, because it replaces
    this search instead of adding to it.
    """
    env = os.environ if environ is None else environ
    mount = env.get(ENV_DEVICES_MOUNT)
    candidates = [Path(mount).expanduser() if mount else DEFAULT_DEVICES_MOUNT]
    candidates.extend(root / relative for relative in _DEVICES_CANDIDATES)
    return candidates


def find_device_definitions(
    root: Path,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[Path]:
    """Return the device definition root for ``root``, or ``None``.

    Order: ``CIQ_DEVICES_ROOT`` (which must be valid), the read-only devices
    mount, the known positions inside the SDK tree, then a bounded walk.
    """
    env = os.environ if environ is None else environ
    override = env.get(ENV_DEVICES_ROOT)
    if override:
        path = Path(override).expanduser()
        if not is_device_definitions(path):
            raise SdkError(
                f"{ENV_DEVICES_ROOT}={path} does not contain device definitions "
                f"(expected subdirectories with {_DEVICE_MARKER})"
            )
        return path

    for candidate in device_definition_candidates(root, env):
        if is_device_definitions(candidate):
            return candidate
    return _walk_for_definitions(root)


def _home(env: Mapping[str, str]) -> Path:
    """``$HOME`` from the given environment, so tests never touch the real home."""
    configured = env.get("HOME")
    return Path(configured).expanduser() if configured else Path.home()


def wine_prefix(environ: Optional[Mapping[str, str]] = None) -> Path:
    """The Wine prefix the simulator will run in."""
    env = os.environ if environ is None else environ
    configured = env.get(ENV_WINE_PREFIX)
    if configured:
        return Path(configured).expanduser()
    return _home(env) / ".wine"


def wine_user_name(environ: Optional[Mapping[str, str]] = None) -> str:
    """The Windows user directory Wine creates inside the prefix.

    Wine derives it from the UNIX user name, so the same variables decide it.
    """
    env = os.environ if environ is None else environ
    for name in ("WINEUSERNAME", "USER", "LOGNAME"):
        value = env.get(name)
        if value:
            return value
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "root"


def wine_devices_target(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Where the Windows simulator looks for device definitions under Wine."""
    env = os.environ if environ is None else environ
    return (
        wine_prefix(env)
        / "drive_c"
        / "users"
        / wine_user_name(env)
        / "AppData"
        / "Roaming"
        / "Garmin"
        / "ConnectIQ"
        / "Devices"
    )


def wine_fonts_target(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Where the Windows simulator looks for device fonts under Wine.

    The simulator reads its font pack from the per-user Connect IQ directory,
    exactly as it does for device definitions.
    """
    env = os.environ if environ is None else environ
    return (
        wine_prefix(env)
        / "drive_c"
        / "users"
        / wine_user_name(env)
        / "AppData"
        / "Roaming"
        / "Garmin"
        / "ConnectIQ"
        / "Fonts"
    )


def fonts_dir(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Where the simulator resolves the device font faces it draws."""
    env = os.environ if environ is None else environ
    configured = env.get(ENV_FONTS_TARGET)
    if configured:
        return Path(configured).expanduser()
    return _home(env) / ".Garmin" / "ConnectIQ" / "Fonts"


def font_asset_count(path: Path) -> int:
    """Number of faces (``.cft``/``.ttf``) a font pack holds, 0 when unusable."""
    if not path.is_dir():
        return 0
    try:
        return sum(
            1
            for entry in path.iterdir()
            if entry.is_file() and entry.suffix.lower() in _FONT_SUFFIXES
        )
    except OSError:
        return 0


def fonts_mount(environ: Optional[Mapping[str, str]] = None) -> Path:
    """The read-only mount a font pack is staged from, or the default mount."""
    env = os.environ if environ is None else environ
    configured = env.get(ENV_FONTS_MOUNT)
    return Path(configured).expanduser() if configured else DEFAULT_FONTS_MOUNT


def fonts_problem(
    path: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """Why the font pack at ``path`` cannot render, or ``None`` when it can.

    The simulator resolves every device face from this directory; an absent or
    empty directory is not a warning, because the app crashes on its first draw
    instead of rendering a degraded view.
    """
    target = Path(path) if path is not None else fonts_dir(environ)
    if not target.is_dir():
        return f"{target} does not exist"
    if font_asset_count(target):
        return None
    try:
        entries = sum(1 for _ in target.iterdir())
    except OSError as error:
        return f"{target} cannot be read: {error}"
    if not entries:
        return f"{target} is empty"
    return (
        f"{target} holds {entries} entries but no .cft or .ttf font files, so it "
        "is not a Connect IQ font pack"
    )


def require_fonts(
    path: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    """Return the font pack directory, or raise with the actionable message.

    The fonts are licensed Garmin assets, so they are never committed and never
    part of an image: the operator copies them from a working Connect IQ
    installation or mounts that directory read-only.
    """
    target = Path(path) if path is not None else fonts_dir(environ)
    problem = fonts_problem(target, environ)
    if problem is not None:
        mount = fonts_mount(environ)
        raise SdkError(
            f"no Connect IQ device fonts are usable at {target}: {problem}.\n"
            "The simulator resolves each device's faces from that directory; "
            "without them every app view fails with \"Invalid Font Specified\" "
            "and the screen stays on the watch face. The SDK archive does not "
            "ship device fonts.\n"
            "Copy the Fonts directory from a working Connect IQ installation "
            "(on Windows %APPDATA%\\Garmin\\ConnectIQ\\Fonts) to that path, or "
            f"mount it read-only at {mount.as_posix()} (CIQ_FONTS_HOST_PATH in "
            "tests/compose/.env).\n"
            "The fonts are licensed Garmin assets: mount or copy them, never "
            "commit them."
        )
    return target


def _point_at(target: Path, source: Path) -> None:
    """Replace ``target`` with a symlink to ``source``, or a copy as fallback."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    try:
        target.symlink_to(source, target_is_directory=True)
    except (OSError, NotImplementedError):
        shutil.copytree(source, target)


def _copy_tree(target: Path, source: Path) -> None:
    """Replace ``target`` with a real copy of ``source``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    shutil.copytree(source, target)


@dataclass(frozen=True)
class DeviceLinks:
    """Where :func:`link_devices` staged the device definitions."""

    linux: Path
    wine: Path


def link_devices(
    root: Path,
    target: Optional[Path] = None,
    wine_target: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> "DeviceLinks":
    """Stage the device definitions where both toolchains look for them.

    Two locations are required, and one alone silently breaks one side:

    - ``$HOME/.Garmin/ConnectIQ/Devices`` -- what the Linux ``monkeyc`` and
      ``monkeydo`` resolve ``-d <device>`` from;
    - ``<WINEPREFIX>/drive_c/users/<user>/AppData/Roaming/Garmin/ConnectIQ/Devices``
      -- what the Windows simulator reads under Wine. Without it
      ``simulator.exe -d <device>`` opens an Error window and no device.

    The Linux target is a copy (the compiler may write caches there). The Wine
    target is a symlink to that copy, so there is exactly one set of files; it
    falls back to a copy where symlinks are not available.
    """
    env = os.environ if environ is None else environ
    source = find_device_definitions(root, env)
    if source is None:
        searched = "\n".join(f"  {path}" for path in device_definition_candidates(root, env))
        override = env.get(ENV_DEVICES_ROOT)
        if override:
            searched = f"  {ENV_DEVICES_ROOT}={override}\n{searched}"
        raise SdkError(
            "no Connect IQ device definitions found; monkeyc needs them to resolve "
            f"-d <device>. Searched:\n{searched}\n"
            f"Put them in the SDK, or set {ENV_DEVICES_ROOT} (or mount them at "
            f"{DEFAULT_DEVICES_MOUNT} via {ENV_DEVICES_MOUNT}). "
            "See tests/docs/sdk.md."
        )

    if target is None:
        configured = env.get(ENV_DEVICES_TARGET)
        target = (
            Path(configured).expanduser()
            if configured
            else _home(env) / ".Garmin" / "ConnectIQ" / "Devices"
        )
    linux_target = Path(target)
    linux_target.parent.mkdir(parents=True, exist_ok=True)
    if linux_target.exists():
        shutil.rmtree(linux_target)
    shutil.copytree(source, linux_target)

    staged_wine_target = (
        Path(wine_target) if wine_target is not None else wine_devices_target(env)
    )
    _point_at(staged_wine_target, linux_target)
    return DeviceLinks(linux=linux_target, wine=staged_wine_target)


@dataclass(frozen=True)
class FontLinks:
    """Where :func:`link_fonts` staged the device font pack."""

    linux: Path
    wine: Path
    source: Path


def find_fonts_source(
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[Path]:
    """A usable font pack to stage, or ``None``.

    Order: ``CIQ_FONTS_ROOT`` (which must be valid), the read-only fonts mount,
    then the target itself, which an operator may already have populated.
    """
    env = os.environ if environ is None else environ
    override = env.get(ENV_FONTS_ROOT)
    if override:
        path = Path(override).expanduser()
        if fonts_problem(path, env) is not None:
            raise SdkError(
                f"{ENV_FONTS_ROOT}={path} is not a usable Connect IQ font pack: "
                f"{fonts_problem(path, env)}"
            )
        return path
    for candidate in (fonts_mount(env), fonts_dir(env)):
        if fonts_problem(candidate, env) is None:
            return candidate
    return None


def link_fonts(
    mount: Optional[Path] = None,
    target: Optional[Path] = None,
    wine_target: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> "FontLinks":
    """Stage the font pack where both simulators read it.

    Two locations are required, for the same reason as the device definitions:

    - ``$HOME/.Garmin/ConnectIQ/Fonts`` -- what the Linux simulator opens before
      every draw (and what ``monkeydo`` resolves a device against);
    - ``<WINEPREFIX>/drive_c/users/<user>/AppData/Roaming/Garmin/ConnectIQ/Fonts``
      -- what the Windows simulator reads under Wine.

    The Linux target is a real copy and the Wine target is a symlink to it, so
    there is exactly one set of 1+ GB of licensed assets on disk. The copy is
    deliberate: the Wine simulator did not resolve a symlink *chain* (prefix ->
    home -> read-only mount) and still raised `Invalid Font Specified`, while it
    does read a single symlink to the copied tree, exactly as it does for the
    device definitions. When the target is already the source, nothing is
    replaced.
    """
    env = os.environ if environ is None else environ
    linux_target = Path(target) if target is not None else fonts_dir(env)
    source = Path(mount) if mount is not None else find_fonts_source(env)
    if source is None:
        # Raises with the directory, the provenance and the licence rule.
        require_fonts(linux_target, env)
        raise AssertionError("require_fonts returned without raising")
    problem = fonts_problem(source, env)
    if problem is not None:
        raise SdkError(f"the font pack to stage is unusable: {problem}")
    if source.resolve() != linux_target.resolve():
        _copy_tree(linux_target, source)
    staged_wine_target = (
        Path(wine_target) if wine_target is not None else wine_fonts_target(env)
    )
    _point_at(staged_wine_target, linux_target)
    return FontLinks(linux=linux_target, wine=staged_wine_target, source=source)


def describe_candidates() -> Sequence[str]:
    """Human-readable layout summary used by the CLI and the docs."""
    return (
        "version.txt | bin/version.txt -> SDK version",
        "linux/bin/monkeyc | bin/monkeyc -> PRG compilation",
        "linux/bin/monkeydo | bin/monkeydo -> app launch (optional)",
        "windows/bin/simulator.exe | bin/simulator.exe -> simulator (optional)",
        "Devices/ | devices/ (or CIQ_DEVICES_ROOT) -> device definitions, "
        "linked into $HOME/.Garmin/ConnectIQ/Devices and into the Wine prefix",
    )
