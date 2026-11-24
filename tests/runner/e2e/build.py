"""PRG compilation with the Linux ``monkeyc``.

The runner image contains the compiler, never the app's build output: every
scenario run compiles the checkout it was mounted with, so a run always tests
the code under review.

Debug annotations matter here. The app's ``(:debug)`` trace events (the
``E2E:`` console lines used by protocol-tier assertions) are stripped from
release builds, so the harness builds without ``--release`` by default.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Sequence, Tuple

from .config import jungle_path
from .jungle import (
    JungleError,
    generate_device_jungle,
    invalid_qualifiers,
    qualifier_error,
)
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
    jungle: Path
    jungle_generated: bool
    jungle_note: str


def prg_name(device: str) -> str:
    return f"openhab-garmin-{device}.prg"


def _run_openssl(openssl: str, arguments: Sequence[str]) -> None:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argument list
            [openssl, *arguments],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildError(f"cannot run openssl: {error}") from None
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise BuildError(
            f"openssl {arguments[0]} failed (exit {completed.returncode}): {detail}"
        )


@contextmanager
def ephemeral_developer_key(
    directory: Path,
    *,
    openssl: Optional[str] = None,
) -> Iterator[Path]:
    """Yield a throwaway RSA developer key for ``monkeyc -y``.

    ``monkeyc`` refuses to compile without a private key, which made an
    unattended ``e2e build`` impossible. The key only signs a build for the
    simulator; it is not the Connect IQ store key and it proves nothing about
    ownership, but it must not be reused or committed all the same.

    The key is generated with ``openssl`` (RSA 4096, PKCS#8 DER, the format
    monkeyc wants), stored under the caller's artifacts directory — never in the
    checkout — with mode ``0600``, and removed when the context exits. Every
    invocation therefore gets a fresh key. ``--developer-key`` remains the
    override for callers that have a stable key.
    """
    binary = openssl or shutil.which("openssl")
    if binary is None:
        raise BuildError(
            "openssl is required to generate the ephemeral developer key; install "
            "openssl or pass an explicit --developer-key"
        )

    keys_dir = Path(directory) / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}-{time.time_ns()}"
    pem = keys_dir / f"developer-key-{token}.pem"
    der = keys_dir / f"developer-key-{token}.der"
    try:
        _run_openssl(binary, ["genrsa", "-out", str(pem), "4096"])
        _run_openssl(
            binary,
            [
                "pkcs8",
                "-topk8",
                "-inform",
                "PEM",
                "-outform",
                "DER",
                "-in",
                str(pem),
                "-out",
                str(der),
                "-nocrypt",
            ],
        )
        os.chmod(der, 0o600)
        yield der
    finally:
        for path in (pem, der):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


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

    By default the jungle is generated for ``device`` from the checkout's
    ``monkey.jungle`` and written to ``output_dir``. That is what lets a reduced
    SDK compile at all: the full jungle names every supported device, and
    ``monkeyc`` rejects qualifiers the SDK does not define. ``jungle`` is the
    ``--jungle`` escape hatch for a complete SDK.
    """
    if not layout.monkeyc.is_file():
        raise BuildError(f"monkeyc not found at {layout.monkeyc}")

    if jungle is None:
        source = jungle_path()
        target = Path(output_dir) / f"{device}.jungle"
        try:
            generated = generate_device_jungle(source, device, target)
        except JungleError as error:
            raise BuildError(str(error)) from None
        jungle_file = generated.path
        jungle_generated = True
        kept = ", ".join(generated.sections)
        jungle_note = (
            f"generated {jungle_file} from {source} ({kept}) because the full "
            "jungle names every supported device and a reduced SDK cannot "
            "validate them; pass --jungle to use a full one with a complete SDK"
        )
    else:
        jungle_file = Path(jungle)
        if not jungle_file.is_file():
            raise BuildError(f"Monkey Jungle not found at {jungle_file}")
        jungle_generated = False
        jungle_note = (
            f"using the explicit jungle {jungle_file} (--jungle); the SDK must "
            "define every device it names"
        )

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
        rejected = invalid_qualifiers(output_text)
        if rejected:
            raise BuildError(qualifier_error(device, rejected, jungle_file))
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
        jungle=jungle_file,
        jungle_generated=jungle_generated,
        jungle_note=jungle_note,
    )


def available(monkeyc: Path) -> bool:
    """True when the compiler file exists (executability is checked on run)."""
    return monkeyc.is_file()
