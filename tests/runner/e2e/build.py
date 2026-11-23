"""PRG compilation with the Linux ``monkeyc``.

The runner image contains the compiler, never the app's build output: every
scenario run compiles the checkout it was mounted with, so a run always tests
the code under review.

Debug annotations matter here. The app's ``(:debug)`` trace events (the
``E2E:`` console lines used by protocol-tier assertions) are stripped from
release builds, so the harness builds without ``--release`` by default.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple

from .config import jungle_path
from .sdk import SdkLayout


class BuildError(RuntimeError):
    """Raised when ``monkeyc`` fails or is not usable."""


@dataclass(frozen=True)
class BuildResult:
    """A compiled PRG plus the exact invocation used."""

    prg: Path
    command: Tuple[str, ...]
    output: str
    duration_ms: float


def prg_name(device: str) -> str:
    return f"openhab-garmin-{device}.prg"


def build_prg(
    layout: SdkLayout,
    device: str,
    output_dir: Path,
    *,
    jungle: Optional[Path] = None,
    release: bool = False,
    developer_key: Optional[Path] = None,
    extra_args: Sequence[str] = (),
    env: Optional[dict] = None,
) -> BuildResult:
    """Compile the app for one device.

    ``release`` defaults to False on purpose: the E2E trace events are debug
    annotations, and a release build silently removes the harness's event
    assertions.
    """
    if not layout.monkeyc.is_file():
        raise BuildError(f"monkeyc not found at {layout.monkeyc}")
    jungle_file = Path(jungle) if jungle else jungle_path()
    if not jungle_file.is_file():
        raise BuildError(f"Monkey Jungle not found at {jungle_file}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / prg_name(device)

    command = [
        str(layout.monkeyc),
        "-f",
        str(jungle_file),
        "-o",
        str(output),
        "-d",
        device,
        "-w",
    ]
    if developer_key is not None:
        command += ["-y", str(developer_key)]
    if release:
        command.append("-r")
    command += list(extra_args)

    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argument list
            command,
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
        )
    except FileNotFoundError as error:
        raise BuildError(f"cannot run {command[0]}: {error}") from None
    except subprocess.TimeoutExpired:
        raise BuildError(f"monkeyc timed out after 600s for device {device}") from None
    duration_ms = (time.monotonic() - started) * 1000.0

    output_text = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise BuildError(
            f"monkeyc failed for device {device} (exit {completed.returncode}):\n"
            f"{output_text.strip()}"
        )
    if not output.is_file():
        raise BuildError(
            f"monkeyc reported success but {output} does not exist:\n{output_text.strip()}"
        )

    return BuildResult(
        prg=output,
        command=tuple(command),
        output=output_text.strip(),
        duration_ms=duration_ms,
    )


def available(monkeyc: Path) -> bool:
    """True when the compiler file exists (executability is checked on run)."""
    return monkeyc.is_file()
