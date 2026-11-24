"""PRG compilation with the Linux ``monkeyc``.

The runner image contains the compiler, never the app's build output: every
scenario run compiles the checkout it was mounted with, so a run always tests
the code under review.

Debug annotations matter here. The app's ``(:debug)`` trace events (the
``E2E:`` console lines used by protocol-tier assertions) are stripped from
release builds, so the harness builds without ``--release`` by default.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence, Tuple

from .config import jungle_path
from .jungle import (
    JungleError,
    generate_device_jungle,
    invalid_qualifiers,
    qualifier_error,
)
from .sdk import SdkLayout
from .settings import AppSettings


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
    settings_json: Optional[Path] = None
    settings_values: Mapping[str, Any] = field(default_factory=dict)


def prg_name(device: str) -> str:
    return f"openhab-garmin-{device}.prg"


#: Properties whose compiled defaults are asserted after every build. If the
#: resource override stops applying, the app would run without a server URL and
#: every scenario would pass vacuously, so this is checked before the guest ever
#: sees the PRG.
ASSERTED_PROPERTIES: Tuple[str, ...] = ("url_0", "sitemap_0")

#: Property ids as ``settings.xml`` spells them: a name and an index.
_PROPERTY_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]*_\d+$")


def settings_json_candidates(prg: Path) -> Tuple[Path, ...]:
    """Where ``monkeyc`` writes the compiled property defaults.

    ``monkeyc -o app.prg`` emits the values it compiled next to the PRG. The
    name has been seen both as ``app-settings.json`` and ``app.prg-settings.json``
    across SDK builds, so both are accepted.
    """
    return (
        Path(prg).with_name(f"{Path(prg).stem}-settings.json"),
        Path(f"{prg}-settings.json"),
    )


def read_compiled_settings(path: Path) -> Dict[str, Any]:
    """Extract ``property id -> default value`` from ``monkeyc``'s JSON.

    The exact document shape is SDK-specific; ids are found wherever they carry
    a ``defaultValue``, and a flat ``id -> value`` mapping is accepted too.
    """
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise BuildError(f"cannot read {path}: {error}") from None
    except ValueError as error:
        raise BuildError(f"{path} is not valid JSON: {error}") from None

    values: Dict[str, Any] = {}

    def collect(node: Any) -> None:
        if isinstance(node, Mapping):
            name = node.get("id") or node.get("name") or node.get("key")
            if isinstance(name, str) and "defaultValue" in node:
                values[name] = node["defaultValue"]
                return
            for key, value in node.items():
                if isinstance(value, Mapping) and "defaultValue" in value:
                    values[str(key)] = value["defaultValue"]
                elif isinstance(value, (Mapping, list)):
                    collect(value)
                elif isinstance(key, str) and _PROPERTY_ID.match(key):
                    values[key] = value
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(document)
    return values


def verify_compiled_settings(
    prg: Path, settings: AppSettings
) -> Tuple[Path, Dict[str, Any]]:
    """Assert the PRG really contains the configured property defaults.

    Runs with no simulator and no display, so a silent resource-override
    regression fails the build instead of reaching the guest.
    """
    path = next((candidate for candidate in settings_json_candidates(prg) if candidate.is_file()), None)
    if path is None:
        expected = " or ".join(str(candidate) for candidate in settings_json_candidates(prg))
        raise BuildError(
            f"monkeyc did not write the compiled settings JSON (looked for "
            f"{expected}).\n"
            "The settings resource override is the only way the harness sets the "
            "app's property defaults, so a run without it would show the "
            "configuration error view. Check that the generated jungle lists "
            "the resource override after `$(resBase)`."
        )
    values = read_compiled_settings(path)
    problems = []
    for name in ASSERTED_PROPERTIES:
        requested = settings.properties.get(name)
        if name not in values:
            problems.append(f"{name}: missing from {path.name}")
        elif str(values[name]) != str(requested):
            problems.append(
                f"{name}: compiled {values[name]!r} but the build requested {requested!r}"
            )
    if problems:
        raise BuildError(
            "the compiled app properties do not match the build settings:\n  "
            + "\n  ".join(problems)
            + f"\nsettings source: {path}\n"
            "The resource override did not win over resources/base. Check the "
            "generated jungle's <device>.resourcePath order."
        )
    return path, values


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
    settings: Optional[AppSettings] = None,
    settings_source: Optional[Path] = None,
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

    ``settings`` compiles the app's property defaults in: a modified
    ``settings.xml`` is written next to the generated jungle and appended to the
    device's ``resourcePath`` right after ``$(resBase)``. The defaults the
    compiler emitted are read back and asserted, so a run cannot silently end up
    with an empty server URL.
    """
    if not layout.monkeyc.is_file():
        raise BuildError(f"monkeyc not found at {layout.monkeyc}")

    if jungle is None:
        source = jungle_path()
        target = Path(output_dir) / f"{device}.jungle"
        try:
            generated = generate_device_jungle(
                source,
                device,
                target,
                settings=settings,
                settings_source=settings_source,
                environ=env,
            )
        except JungleError as error:
            raise BuildError(str(error)) from None
        jungle_file = generated.path
        jungle_generated = True
        kept = ", ".join(generated.sections)
        override_note = (
            f"; settings override {generated.resource_override}"
            if generated.resource_override is not None
            else ""
        )
        jungle_note = (
            f"generated {jungle_file} from {source} ({kept}) because the full "
            "jungle names every supported device and a reduced SDK cannot "
            f"validate them{override_note}; pass --jungle to use a full one "
            "with a complete SDK"
        )
    else:
        if settings is not None:
            raise BuildError(
                "--jungle cannot carry the harness settings override: only a "
                "generated jungle can add the resource directory that sets the "
                "app's compiled property defaults.\n"
                "Drop --jungle (the generated jungle works with any SDK that "
                "defines the device), or pass --prg with a PRG you built "
                "yourself."
            )
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

    settings_json: Optional[Path] = None
    settings_values: Dict[str, Any] = {}
    if settings is not None:
        settings_json, settings_values = verify_compiled_settings(output, settings)

    return BuildResult(
        prg=output,
        command=tuple(command),
        output=output_text.strip(),
        duration_ms=duration_ms,
        jungle=jungle_file,
        jungle_generated=jungle_generated,
        jungle_note=jungle_note,
        settings_json=settings_json,
        settings_values=settings_values,
    )


def available(monkeyc: Path) -> bool:
    """True when the compiler file exists (executability is checked on run)."""
    return monkeyc.is_file()
