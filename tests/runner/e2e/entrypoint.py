"""Is the running container's entrypoint the one the harness expects?

``entrypoint.sh`` is **baked into the runner image**, while the harness is
bind-mounted from the checkout at ``/work``. Updating the harness therefore does
not update the entrypoint: a container recreated from an older image runs the old
script against the new checks. That produced a real, misleading failure - the old
entrypoint never staged the device fonts, so the new ``sdk-check`` reported
"no Connect IQ device fonts are usable ... does not exist" and pointed at the
font pack when the real cause was a stale image.

The script writes the SHA-256 of its own bytes at start, and the harness compares
that with the copy it can see. Comparing hashes needs no version to remember to
bump: edit ``entrypoint.sh`` and the mismatch appears by itself.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

#: Where the running entrypoint records the hash of the script it executed.
ENV_MARKER = "CIQ_ENTRYPOINT_MARKER"
DEFAULT_MARKER = Path("/run/ciq-entrypoint.sha256")

#: The entrypoint the harness ships, as seen from inside the runner container.
ENV_SCRIPT = "CIQ_ENTRYPOINT_SCRIPT"
DEFAULT_SCRIPT = Path("/work/tests/runner/entrypoint.sh")

FRESH = "fresh"
STALE = "stale"
UNKNOWN = "unknown"


def file_sha256(path: Path) -> Optional[str]:
    """SHA-256 of a file's bytes, or ``None`` when it cannot be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


@dataclass(frozen=True)
class EntrypointStatus:
    """Whether the executed entrypoint matches the one the harness ships."""

    status: str
    detail: str
    running: Optional[str] = None
    expected: Optional[str] = None

    @property
    def stale(self) -> bool:
        return self.status == STALE

    def rebuild_hint(self) -> str:
        """The one command that fixes a stale image."""
        return (
            "the image's entrypoint.sh is older than the harness's: rebuild the "
            "runner image before starting the stack (docker compose build runner). "
            "The harness is mounted, so new checks run against the old entrypoint "
            "and the failing check will name the wrong cause."
        )


def _resolve(environ: Mapping[str, str], name: str, default: Path) -> Path:
    configured = environ.get(name)
    return Path(configured) if configured else default


def entrypoint_status(
    environ: Optional[Mapping[str, str]] = None,
    *,
    script: Optional[Path] = None,
    marker: Optional[Path] = None,
) -> EntrypointStatus:
    """Compare the executed entrypoint with the harness copy.

    ``unknown`` is not a failure: suites that never start the runner container
    (CI) have neither file, and a missing marker on an image built before this
    check existed must not break a run.
    """
    env = os.environ if environ is None else environ
    script_path = Path(script) if script is not None else _resolve(env, ENV_SCRIPT, DEFAULT_SCRIPT)
    marker_path = Path(marker) if marker is not None else _resolve(env, ENV_MARKER, DEFAULT_MARKER)

    expected = file_sha256(script_path)
    if expected is None:
        return EntrypointStatus(
            UNKNOWN,
            f"{script_path} is not readable, so the entrypoint cannot be compared",
        )
    try:
        running = marker_path.read_text(encoding="utf-8").strip()
    except OSError:
        return EntrypointStatus(
            UNKNOWN,
            f"{marker_path} is missing, so the image is older than this check; "
            "rebuild the runner image to make the comparison reliable",
            expected=expected,
        )
    if not running:
        return EntrypointStatus(
            UNKNOWN,
            f"{marker_path} is empty; rebuild the runner image",
            expected=expected,
        )
    if running == expected:
        return EntrypointStatus(
            FRESH, f"entrypoint.sh matches the harness ({expected[:12]})", running, expected
        )
    return EntrypointStatus(
        STALE,
        f"the running entrypoint is {running[:12]} but the harness ships "
        f"{expected[:12]}",
        running,
        expected,
    )
