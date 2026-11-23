"""Host prerequisite checks for the spike.

Everything the host needs is checked **before** the runner image is built, so a
missing Docker daemon or SDK produces one message instead of a half-run:

- Docker on ``PATH`` and a reachable daemon;
- the repository layout the runner image expects (``monkey.jungle``,
  ``manifest.xml``, ``tests/runner/Dockerfile``);
- the Connect IQ SDK, with the same layout ``tests/docs/sdk.md`` documents:
  ``version.txt``, ``linux/bin/monkeyc``, ``linux/bin/monkeydo`` and
  ``windows/bin/simulator.exe``, plus a version matching
  ``tests/config/sdk.version``.

Wine and the X display cannot be checked from the host: they live inside the
runner image. The in-container probe runs the same kind of check and refuses to
start the procedure when either is missing (see :mod:`spike.probe`).

This module is standard-library only. It must work on a Linux host that has
Docker and the SDK but has never installed the harness's Python dependencies.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Mapping, Optional, Sequence, Tuple

#: Host variables the spike accepts, in precedence order. ``CIQ_SDK_HOST_PATH``
#: is the Compose convention; the other two are accepted because the harness and
#: issue #365 already use them.
ENV_VARS = ("CIQ_SDK_HOST_PATH", "CIQ_SDK_ROOT", "CIQ_SDK_PATH")

MONKEYC_CANDIDATES = ("linux/bin/monkeyc", "bin/monkeyc")
MONKEYDO_CANDIDATES = ("linux/bin/monkeydo", "bin/monkeydo")
SIMULATOR_CANDIDATES = ("windows/bin/simulator.exe", "bin/simulator.exe")
VERSION_CANDIDATES = ("version.txt", "bin/version.txt")

#: Files the spike needs in the checkout before it can build the image.
REPO_REQUIRED = ("monkey.jungle", "manifest.xml", "tests/runner/Dockerfile")

DOCKER_PROBE_TIMEOUT_S = 20.0


class PrereqError(RuntimeError):
    """Raised when a single prerequisite cannot be resolved.

    The CLI aggregates :class:`HostStatus` problems into one message; this
    exception is for helpers that are used on their own (and in tests).
    """


@dataclass(frozen=True)
class SdkLayout:
    """Validated Connect IQ SDK paths needed by the spike."""

    root: Path
    version: str
    version_file: Path
    monkeyc: Path
    monkeydo: Path
    simulator: Path


@dataclass(frozen=True)
class HostStatus:
    """Result of the host prerequisite pass."""

    problems: Tuple[str, ...] = ()
    sdk: Optional[SdkLayout] = None
    docker: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def message(self) -> str:
        """One sentence naming everything that is missing."""
        return "missing prerequisites: " + "; ".join(self.problems)


def _first_existing(root: Path, candidates: Iterable[str]) -> Optional[Path]:
    for relative in candidates:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def read_pinned_version(repo_root: Path) -> str:
    """Read ``tests/config/sdk.version`` from the checkout."""
    path = Path(repo_root) / "tests" / "config" / "sdk.version"
    try:
        version = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PrereqError(f"cannot read the SDK version pin {path}: {error}") from None
    if not version:
        raise PrereqError(f"{path} is empty")
    return version


def resolve_sdk_root(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Resolve the SDK root from the environment, or raise.

    The error names the variable to set and the expected layout; the SDK is not
    redistributable, so it is never in this repository or in the image.
    """
    env = os.environ if environ is None else environ
    for name in ENV_VARS:
        value = env.get(name)
        if not value:
            continue
        path = Path(value).expanduser()
        if not path.is_dir():
            raise PrereqError(
                f"Connect IQ SDK ({name} points at {path}, which is not a directory)"
            )
        return path
    raise PrereqError(
        "Connect IQ SDK (set CIQ_SDK_HOST_PATH to the SDK root: it must contain "
        "version.txt, linux/bin/monkeyc, linux/bin/monkeydo and "
        "windows/bin/simulator.exe)"
    )


def inspect_sdk(root: Path, expected_version: Optional[str] = None) -> SdkLayout:
    """Validate an SDK root and return the four paths the spike uses.

    Unlike ``e2e.sdk.inspect_sdk``, ``monkeydo`` and ``simulator.exe`` are
    required: this spike exists to run them.
    """
    root = Path(root)
    if not root.is_dir():
        raise PrereqError(f"Connect IQ SDK (root {root} is not a directory)")

    version_file = _first_existing(root, VERSION_CANDIDATES)
    if version_file is None:
        raise PrereqError(
            f"Connect IQ SDK (no version.txt or bin/version.txt under {root})"
        )
    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PrereqError(f"Connect IQ SDK (cannot read {version_file}: {error})") from None
    if not version:
        raise PrereqError(f"Connect IQ SDK ({version_file} is empty)")
    if expected_version is not None and version != expected_version:
        raise PrereqError(
            f"Connect IQ SDK (version {version!r} does not match the "
            f"tests/config/sdk.version pin {expected_version!r})"
        )

    monkeyc = _first_existing(root, MONKEYC_CANDIDATES)
    if monkeyc is None:
        raise PrereqError(
            "Connect IQ SDK (missing the Linux compiler: expected "
            f"{' or '.join(MONKEYC_CANDIDATES)})"
        )
    monkeydo = _first_existing(root, MONKEYDO_CANDIDATES)
    if monkeydo is None:
        raise PrereqError(
            "Connect IQ SDK (missing monkeydo: expected "
            f"{' or '.join(MONKEYDO_CANDIDATES)})"
        )
    simulator = _first_existing(root, SIMULATOR_CANDIDATES)
    if simulator is None:
        raise PrereqError(
            "Connect IQ SDK (missing the Windows simulator: expected "
            f"{' or '.join(SIMULATOR_CANDIDATES)})"
        )

    return SdkLayout(
        root=root,
        version=version,
        version_file=version_file,
        monkeyc=monkeyc,
        monkeydo=monkeydo,
        simulator=simulator,
    )


def _probe_docker(docker: str, run: Callable[..., subprocess.CompletedProcess]) -> Optional[str]:
    """Return the daemon's version string, or ``None`` when it does not answer."""
    try:
        completed = run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=DOCKER_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    version = (completed.stdout or "").strip()
    return version or "unknown"


def check_host(
    environ: Optional[Mapping[str, str]] = None,
    repo_root: Optional[Path] = None,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    docker_binary: str = "docker",
    require_docker: bool = True,
) -> HostStatus:
    """Check everything the host must provide before the spike starts.

    ``require_docker=False`` is used by ``--dry-run``: the SDK and checkout are
    still validated (so the plan is meaningful) but a missing daemon is only
    reported by the real run. The injected ``which``/``run`` keep the unit tests
    free of Docker.
    """
    env = os.environ if environ is None else environ
    repo = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]

    problems: List[str] = []
    sdk: Optional[SdkLayout] = None
    docker_path: Optional[str] = None

    for relative in REPO_REQUIRED:
        candidate = repo / relative
        if not candidate.is_file():
            problems.append(f"repository ({relative} is missing; run from the checkout)")

    try:
        root = resolve_sdk_root(env)
    except PrereqError as error:
        problems.append(str(error))
    else:
        try:
            sdk = inspect_sdk(root, expected_version=read_pinned_version(repo))
        except PrereqError as error:
            problems.append(str(error))

    if require_docker:
        docker_path = which(docker_binary)
        if docker_path is None:
            problems.append(
                f"docker ({docker_binary!r} is not on PATH; install Docker Engine "
                "or pass --docker)"
            )
        elif _probe_docker(docker_path, run) is None:
            problems.append(
                f"docker daemon ({docker_path} cannot reach it; is Docker running "
                "and is this user allowed to use it?)"
            )

    return HostStatus(problems=tuple(problems), sdk=sdk, docker=docker_path)


def describe_environment(status: HostStatus, repo_root: Path) -> str:
    """Short multi-line summary printed by ``check``."""
    lines = [f"repository: {repo_root}"]
    if status.sdk is not None:
        lines.append(f"sdk:        {status.sdk.root} (version {status.sdk.version})")
        lines.append(f"monkeyc:    {status.sdk.monkeyc}")
        lines.append(f"monkeydo:   {status.sdk.monkeydo}")
        lines.append(f"simulator:  {status.sdk.simulator}")
    lines.append(f"docker:     {status.docker or '<not checked>'}")
    if status.problems:
        lines.append(status.message())
    else:
        lines.append("prerequisites: ok")
    return "\n".join(lines)


def split_problems(problems: Sequence[str]) -> str:
    """Render a problem list as one line (used by the CLI and tests)."""
    return "missing prerequisites: " + "; ".join(problems)
