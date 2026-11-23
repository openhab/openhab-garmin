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

Both are validated the same way. A missing SDK, a missing tool or a version
that does not match ``tests/config/sdk.version`` is a hard, early failure with
an actionable message: a version mismatch produces app behaviour that cannot be
compared with anyone else's results.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from .config import ConfigError, read_sdk_version

ENV_VARS = ("CIQ_SDK_ROOT", "CIQ_SDK_PATH")

_MONKEYC_CANDIDATES = ("linux/bin/monkeyc", "bin/monkeyc")
_MONKEYDO_CANDIDATES = ("linux/bin/monkeydo", "bin/monkeydo")
_SIMULATOR_CANDIDATES = ("windows/bin/simulator.exe", "bin/simulator.exe")
_VERSION_CANDIDATES = ("version.txt", "bin/version.txt")


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

    @property
    def has_simulator(self) -> bool:
        return self.simulator is not None


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


def describe_candidates() -> Sequence[str]:
    """Human-readable layout summary used by the CLI and the docs."""
    return (
        "version.txt | bin/version.txt -> SDK version",
        "linux/bin/monkeyc | bin/monkeyc -> PRG compilation",
        "linux/bin/monkeydo | bin/monkeydo -> app launch (optional)",
        "windows/bin/simulator.exe | bin/simulator.exe -> simulator (optional)",
    )
