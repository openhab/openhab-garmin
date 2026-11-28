"""Host side of the spike: build the runner image and run the probe in it.

The host only needs Docker and the SDK. Everything that touches Wine, Xvfb,
xdotool or ImageMagick runs in :mod:`spike.probe` inside the runner image, with
the checkout and the SDK mounted read-only and one writable artifacts mount.

The image is built from ``tests/runner/Dockerfile`` -- the same image the E2E
harness uses -- so the spike measures the environment the driver would actually
run in. Nothing is baked into the image and nothing is committed: the SDK is a
host path mounted at ``/opt/ciq-sdk``.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .model import ProbeDocument, SpikeError
from .prereqs import SdkLayout

EXIT_OK = 0
EXIT_INCOMPLETE = 1
EXIT_PREREQ = 2

DEFAULT_IMAGE = "openhab-garmin-e2e-runner:spike"
#: The spike reproduces the runner image, so it follows the runner base: 22.04,
#: which still ships the `webkit2gtk-4.0` the native simulator links.
DEFAULT_BASE_IMAGE = "ubuntu:22.04"
CONTAINER_REPO = "/work"
CONTAINER_SDK = "/opt/ciq-sdk"
CONTAINER_ARTIFACTS = "/spike-artifacts"
CONTAINER_DISPLAY = ":99"

PROBE_DOCUMENT = "probe.json"


@dataclass(frozen=True)
class SpikePlan:
    """Everything one spike run needs, resolved by the CLI."""

    repo_root: Path
    sdk: SdkLayout
    artifacts: Path
    image: str = DEFAULT_IMAGE
    base_image: str = DEFAULT_BASE_IMAGE
    docker: str = "docker"
    button_device: str = "fenix6pro"
    touch_device: str = "venu3"
    language: str = "deu"
    poll_intervals: Tuple[int, ...] = (0, 1000)
    timeout_s: float = 1800.0
    build_timeout_s: float = 1800.0
    skip_build: bool = False
    keep_container: bool = False

    @property
    def container_name(self) -> str:
        return f"openhab-garmin-spike-{os.getpid()}"

    @property
    def dockerfile(self) -> Path:
        return self.repo_root / "tests" / "runner" / "Dockerfile"

    @property
    def build_context(self) -> Path:
        return self.repo_root / "tests"

    def to_document(self) -> Dict[str, Any]:
        return {
            "repo_root": str(self.repo_root),
            "sdk_root": str(self.sdk.root),
            "sdk_version": self.sdk.version,
            "image": self.image,
            "base_image": self.base_image,
            "button_device": self.button_device,
            "touch_device": self.touch_device,
            "language": self.language,
            "poll_intervals": list(self.poll_intervals),
            "timeout_s": self.timeout_s,
        }


@dataclass
class SpikeRun:
    """Outcome of one host-side run."""

    exit_code: int
    artifacts: Path
    probe: Optional[ProbeDocument] = None
    message: str = ""
    logs: Dict[str, Path] = field(default_factory=dict)


def build_command(plan: SpikePlan) -> List[str]:
    """``docker build`` for the E2E runner image under a spike-specific tag."""
    return [
        plan.docker,
        "build",
        "--build-arg",
        f"RUNNER_BASE_IMAGE={plan.base_image}",
        "-f",
        str(plan.dockerfile),
        "-t",
        plan.image,
        str(plan.build_context),
    ]


def run_command(plan: SpikePlan) -> List[str]:
    """``docker run`` for the in-container probe.

    The checkout is read-only and the artifacts directory is the only writable
    mount, so a probe cannot modify the working tree. ``--init`` keeps Wine's
    child processes from being reaped as PID 1.
    """
    command = [
        plan.docker,
        "run",
        "--rm",
        "--init",
        "--name",
        plan.container_name,
        "-e",
        f"CIQ_SDK_ROOT={CONTAINER_SDK}",
        "-e",
        f"DISPLAY={CONTAINER_DISPLAY}",
        "-e",
        "PYTHONPATH=/work/tests/spike:/work/tests/runner:/work/tests",
        "-e",
        "PYTHONUNBUFFERED=1",
        "-e",
        "WINEDEBUG=-all",
        "-v",
        f"{plan.repo_root}:{CONTAINER_REPO}:ro",
        "-v",
        f"{plan.sdk.root}:{CONTAINER_SDK}:ro",
        "-v",
        f"{plan.artifacts}:{CONTAINER_ARTIFACTS}",
        plan.image,
        "python",
        "-m",
        "spike.probe",
        "--artifacts",
        CONTAINER_ARTIFACTS,
        "--repo",
        CONTAINER_REPO,
        "--button-device",
        plan.button_device,
        "--touch-device",
        plan.touch_device,
        "--language",
        plan.language,
        "--poll-intervals",
        ",".join(str(value) for value in plan.poll_intervals),
    ]
    return command


def plan_lines(plan: SpikePlan) -> List[str]:
    """Human-readable plan used by ``--dry-run``."""
    lines = [
        "spike plan (no container started)",
        f"repository: {plan.repo_root}",
        f"sdk:        {plan.sdk.root} (version {plan.sdk.version})",
        f"artifacts:  {plan.artifacts}",
        f"devices:    {plan.button_device} (button), {plan.touch_device} (touch)",
        "",
        "build:",
        "  " + " ".join(build_command(plan)),
        "run:",
        "  " + " ".join(run_command(plan)),
    ]
    if plan.skip_build:
        lines.append("")
        lines.append("--skip-build: the existing image is reused.")
    return lines


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run_spike(
    plan: SpikePlan,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    log: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
) -> SpikeRun:
    """Build the image (unless skipped) and run the probe.

    ``run`` is injectable so the host orchestration can be tested without
    Docker. The returned :class:`SpikeRun` never raises for a probe that
    reported missing prerequisites: that is a result with ``exit_code=2`` and a
    single message.
    """
    artifacts = Path(plan.artifacts)
    logs_dir = artifacts / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logs: Dict[str, Path] = {}

    _write(
        artifacts / "plan.json",
        json.dumps(plan.to_document(), indent=2) + "\n",
    )

    if not plan.skip_build:
        log(f"building {plan.image} from {plan.dockerfile}")
        started = clock()
        try:
            built = run(
                build_command(plan),
                capture_output=True,
                text=True,
                timeout=plan.build_timeout_s,
            )
        except FileNotFoundError:
            return SpikeRun(
                exit_code=EXIT_PREREQ,
                artifacts=artifacts,
                message=f"docker ({plan.docker!r} is not on PATH)",
            )
        except subprocess.TimeoutExpired:
            return SpikeRun(
                exit_code=EXIT_INCOMPLETE,
                artifacts=artifacts,
                message=f"docker build timed out after {plan.build_timeout_s:.0f}s",
            )
        build_output = (built.stdout or "") + (built.stderr or "")
        logs["build"] = _write(logs_dir / "docker-build.log", build_output)
        if built.returncode != 0:
            daemon_problem = _daemon_problem(build_output)
            if daemon_problem:
                return SpikeRun(
                    exit_code=EXIT_PREREQ,
                    artifacts=artifacts,
                    message=f"missing prerequisites: {daemon_problem}",
                    logs=logs,
                )
            return SpikeRun(
                exit_code=EXIT_INCOMPLETE,
                artifacts=artifacts,
                message=(
                    f"docker build failed (exit {built.returncode}); "
                    f"see {logs['build']}"
                ),
                logs=logs,
            )
        log(f"built in {clock() - started:.0f}s")

    log(f"running the probe in {plan.image}")
    started = clock()
    try:
        completed = run(
            run_command(plan),
            capture_output=True,
            text=True,
            timeout=plan.timeout_s,
        )
    except FileNotFoundError:
        return SpikeRun(
            exit_code=EXIT_PREREQ,
            artifacts=artifacts,
            message=f"docker ({plan.docker!r} is not on PATH)",
            logs=logs,
        )
    except subprocess.TimeoutExpired as error:
        if plan.keep_container:
            log(f"container {plan.container_name} left running (--keep-container)")
        else:
            _force_remove(plan, run=run)
        logs["probe"] = _write(
            logs_dir / "probe.log",
            _timeout_text(error),
        )
        return SpikeRun(
            exit_code=EXIT_INCOMPLETE,
            artifacts=artifacts,
            message=(
                f"the probe timed out after {plan.timeout_s:.0f}s; "
                f"see {logs['probe']}"
            ),
            logs=logs,
        )

    logs["probe"] = _write(
        logs_dir / "probe.log",
        (completed.stdout or "") + (completed.stderr or ""),
    )

    document_path = artifacts / PROBE_DOCUMENT
    if not document_path.is_file():
        # The runner entrypoint exits 78 when it cannot see the SDK. That is a
        # prerequisite failure, not a crash, and the operator must get the same
        # single message the host checks use.
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode == 78 or "Connect IQ SDK is not available" in output:
            return SpikeRun(
                exit_code=EXIT_PREREQ,
                artifacts=artifacts,
                message=(
                    "missing prerequisites: Connect IQ SDK (the runner "
                    f"container could not use the mounted SDK; see {logs['probe']})"
                ),
                logs=logs,
            )
        return SpikeRun(
            exit_code=EXIT_INCOMPLETE,
            artifacts=artifacts,
            message=(
                f"the probe produced no {PROBE_DOCUMENT} (exit "
                f"{completed.returncode}); see {logs['probe']}"
            ),
            logs=logs,
        )

    try:
        probe = ProbeDocument.from_document(
            json.loads(document_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, SpikeError) as error:
        return SpikeRun(
            exit_code=EXIT_INCOMPLETE,
            artifacts=artifacts,
            message=f"cannot read {document_path}: {error}",
            logs=logs,
        )

    elapsed = clock() - started
    if probe.prerequisites:
        return SpikeRun(
            exit_code=EXIT_PREREQ,
            artifacts=artifacts,
            probe=probe,
            message="missing prerequisites: " + "; ".join(probe.prerequisites),
            logs=logs,
        )
    if completed.returncode != 0:
        return SpikeRun(
            exit_code=EXIT_INCOMPLETE,
            artifacts=artifacts,
            probe=probe,
            message=(
                f"the probe exited {completed.returncode} after writing "
                f"{PROBE_DOCUMENT}; see {logs['probe']}"
            ),
            logs=logs,
        )

    log(f"probe finished in {elapsed:.0f}s")
    return SpikeRun(
        exit_code=EXIT_OK,
        artifacts=artifacts,
        probe=probe,
        logs=logs,
    )


def _daemon_problem(output: str) -> str:
    """Return a prerequisite message when Docker's daemon is the problem."""
    lowered = output.lower()
    if "cannot connect to the docker daemon" in lowered or "is the docker daemon running" in lowered:
        return (
            "docker daemon (cannot connect; is Docker running and is this user "
            "allowed to use it?)"
        )
    if "permission denied" in lowered and "docker" in lowered:
        return (
            "docker daemon (permission denied; add this user to the docker group "
            "or use a rootless setup)"
        )
    return ""


def _timeout_text(error: subprocess.TimeoutExpired) -> str:
    parts = []
    for stream in (error.stdout, error.stderr):
        if stream:
            parts.append(stream if isinstance(stream, str) else stream.decode("utf-8", "replace"))
    if not parts:
        parts.append(f"<no output before the {error.timeout:.0f}s timeout>")
    return "\n".join(parts)


def _force_remove(
    plan: SpikePlan,
    *,
    run: Callable[..., subprocess.CompletedProcess],
) -> None:
    try:
        run(
            [plan.docker, "rm", "-f", plan.container_name],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def probe_artifacts(run: SpikeRun) -> Sequence[Path]:
    """Files the verdict writer should mention, in a stable order."""
    found: List[Path] = []
    for path in sorted(Path(run.artifacts).rglob("*")):
        if path.is_file() and path.name not in {"plan.json"}:
            found.append(path)
    return found
