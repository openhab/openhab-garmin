"""Stack health for a run: restarts, working set and OOM evidence.

A scenario can fail because the app is wrong, or because the environment died
underneath it. Those two outcomes must not look the same in a report, and the
usual signals do not separate them:

- ``docker ps`` and the health status only describe *now*. A container that was
  OOM-killed an hour ago and restarted is healthy at the moment it is looked at.
- ``State.OOMKilled`` is set by Docker only when it observes the kill. A cgroup
  v2 memory kill is performed by the kernel, so the flag stays ``false``.
- ``docker stats`` reports a point-in-time working set. RSS excludes page cache
  that the cgroup still accounts for against its limit.

What does separate them is the *delta across the run window*: the restart
counter, the cgroup's ``oom_kill`` counter and the container's state. This
module parses that evidence, classifies the run as ``valid``, ``invalid`` or
``unknown`` and renders it for the report and JUnit. It never turns an
environment problem into a scenario failure and it never asserts on application
behaviour, so it cannot mask an app defect.

Everything here is pure parsing plus one injectable command runner: no Docker
daemon is needed to import, classify or test the module. The command plan is
the only part that touches a host, and the unit tests script it with recorded
output. The full procedure is in ``tests/docs/diagnostics.md``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VALID = "valid"
INVALID = "invalid"
UNKNOWN = "unknown"

#: Exit code for a run whose environment was invalidated. It is deliberately
#: distinct from ``1`` (scenario failures) so CI can retry instead of reporting
#: an application defect.
EXIT_INVALID_ENVIRONMENT = 3

#: Compose project that owns the stack, matching ``tests/compose/compose.yaml``.
COMPOSE_PROJECT_ENV = "COMPOSE_PROJECT_NAME"
DEFAULT_COMPOSE_PROJECT = "openhab-garmin-e2e"

#: Labels Compose puts on every container it creates.
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"

#: Host-wide kernel log command; only used when the probe runs on the Docker
#: host, and never fatal when it is not permitted.
DMESG_COMMAND = ("dmesg",)

#: cgroup v2 and v1 files that carry the OOM and memory-peak evidence.
CGROUP_MEMORY_EVENTS = "/sys/fs/cgroup/memory.events"
CGROUP_MEMORY_PEAK = "/sys/fs/cgroup/memory.peak"
CGROUP_V1_OOM_CONTROL = "/sys/fs/cgroup/memory/memory.oom_control"

COMMAND_TIMEOUT_S = 60.0

#: Maximum number of kernel OOM lines kept, so a noisy host cannot bloat the
#: artifact.
MAX_OOM_LINES = 20

_MEMORY_TOKEN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?\s*$")

#: Docstring units are binary (Docker prints ``MiB``/``GiB``); the decimal
#: spellings are accepted because operators paste output from other tools.
_MEMORY_UNITS = {
    "": 1,
    "b": 1,
    "byte": 1,
    "bytes": 1,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
}

_OOM_LINE = re.compile(
    r"(out of memory|oom-kill|oom_kill|invoked oom-killer|killed process)",
    re.IGNORECASE,
)


class HealthError(RuntimeError):
    """Raised when stack health cannot be read or a document is malformed."""


# ---------------------------------------------------------------------------
# small parsing helpers
# ---------------------------------------------------------------------------


def parse_bytes(text: str) -> Optional[int]:
    """Parse ``512MiB``, ``1.5GiB``, ``0B`` or ``1024`` into bytes."""
    if text is None:
        return None
    match = _MEMORY_TOKEN.match(str(text))
    if not match:
        return None
    number, unit = match.group(1), (match.group(2) or "").lower()
    factor = _MEMORY_UNITS.get(unit)
    if factor is None:
        return None
    return int(round(float(number) * factor))


def format_bytes(value: Optional[int]) -> str:
    """Render a byte count with binary units; ``unlimited`` for 0/None."""
    if value is None:
        return "unavailable"
    if value <= 0:
        return "0 B"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    size = float(value)
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[index]}"


def describe_limit(value: Optional[int]) -> str:
    """Memory limit as it should appear in a report."""
    return "unlimited" if not value else format_bytes(value)


def parse_mem_usage(text: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse ``docker stats``' ``MemUsage`` field: ``usage / limit``."""
    if not text:
        return None, None
    if "/" in text:
        usage_text, _, limit_text = text.partition("/")
        return parse_bytes(usage_text), parse_bytes(limit_text)
    return parse_bytes(text), None


def parse_memory_events(text: str) -> Dict[str, int]:
    """Parse a cgroup ``memory.events`` file into its counters.

    cgroup v2 writes one ``name value`` pair per line (``low``, ``high``,
    ``max``, ``oom``, ``oom_kill``, ``oom_group_kill``). Unknown keys are kept
    so a newer kernel is not silently dropped.
    """
    counters: Dict[str, int] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            counters[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return counters


def parse_oom_lines(text: str) -> Tuple[str, ...]:
    """Keep the kernel lines that are OOM evidence."""
    lines: List[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped and _OOM_LINE.search(stripped):
            lines.append(stripped)
    return tuple(lines[:MAX_OOM_LINES])


def match_oom_evidence(
    lines: Sequence[str], container: "ContainerHealth"
) -> Tuple[str, ...]:
    """The kernel lines attributable to one container.

    The host kernel log is not namespaced, so a line is attributed by the
    container name or by the first twelve characters of its id, which is what
    the cgroup path in the line contains.
    """
    if not lines:
        return ()
    needles = [needle for needle in (container.name, container.container_id[:12]) if needle]
    if not needles:
        return ()
    return tuple(line for line in lines if any(needle in line for needle in needles))


# ---------------------------------------------------------------------------
# container model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatsSample:
    """One container's row from ``docker stats --no-stream``."""

    name: str
    working_set_bytes: Optional[int] = None
    memory_limit_bytes: Optional[int] = None


@dataclass(frozen=True)
class ContainerHealth:
    """What one snapshot says about one container."""

    name: str
    container_id: str = ""
    restart_count: int = 0
    state: str = ""
    running: bool = False
    health: Optional[str] = None
    memory_limit_bytes: Optional[int] = None
    working_set_bytes: Optional[int] = None
    peak_working_set_bytes: Optional[int] = None
    peak_source: Optional[str] = None
    oom_killed: bool = False
    oom_kill_count: Optional[int] = None
    oom_evidence: Tuple[str, ...] = ()

    def with_sample(self, sample: Optional[StatsSample]) -> "ContainerHealth":
        if sample is None:
            return self
        return replace(self, working_set_bytes=sample.working_set_bytes)

    def with_memory_events(self, text: str) -> "ContainerHealth":
        counters = parse_memory_events(text)
        if "oom_kill" not in counters:
            return self
        return replace(self, oom_kill_count=counters["oom_kill"])

    def with_peak(self, text: str) -> "ContainerHealth":
        value = parse_bytes(text)
        if value is None:
            return self
        return replace(self, peak_working_set_bytes=value, peak_source="cgroup memory.peak")

    def with_oom_evidence(self, lines: Sequence[str]) -> "ContainerHealth":
        attributed = match_oom_evidence(lines, self)
        if not attributed:
            return self
        return replace(self, oom_evidence=attributed)

    def to_document(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "id": self.container_id,
            "restart_count": self.restart_count,
            "state": self.state,
            "running": self.running,
            "health": self.health,
            "memory_limit_bytes": self.memory_limit_bytes,
            "working_set_bytes": self.working_set_bytes,
            "peak_working_set_bytes": self.peak_working_set_bytes,
            "peak_source": self.peak_source,
            "oom_killed": self.oom_killed,
            "oom_kill_count": self.oom_kill_count,
            "oom_evidence": list(self.oom_evidence),
        }


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: object) -> Optional[int]:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _inspect_entries(document: object) -> List[Mapping[str, Any]]:
    if document in (None, ""):
        return []
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError as error:
            raise HealthError(f"docker inspect output is not JSON: {error}") from None
    if isinstance(document, Mapping):
        return [document]
    if isinstance(document, (list, tuple)):
        return [entry for entry in document if isinstance(entry, Mapping)]
    raise HealthError("docker inspect output must be a list of container objects")


def parse_inspect(document: object) -> Tuple[ContainerHealth, ...]:
    """Parse ``docker inspect`` output into containers.

    Accepts the JSON text, the parsed list, or a single container object, so the
    same function reads a live probe and a recorded fixture.
    """
    containers: List[ContainerHealth] = []
    for entry in _inspect_entries(document):
        state = _mapping(entry.get("State"))
        host = _mapping(entry.get("HostConfig"))
        config = _mapping(entry.get("Config"))
        name = str(entry.get("Name") or "").lstrip("/")
        if not name:
            name = str(config.get("Hostname") or "unknown")
        health_status = _mapping(state.get("Health")).get("Status")
        try:
            restart_count = int(entry.get("RestartCount") or 0)
        except (TypeError, ValueError):
            restart_count = 0
        containers.append(
            ContainerHealth(
                name=name,
                container_id=str(entry.get("Id") or entry.get("ID") or ""),
                restart_count=restart_count,
                state=str(state.get("Status") or ""),
                running=bool(state.get("Running")),
                health=str(health_status) if isinstance(health_status, str) else None,
                memory_limit_bytes=_positive_int(host.get("Memory")),
                oom_killed=bool(state.get("OOMKilled")),
            )
        )
    return tuple(containers)


def _samples_from_entries(entries: Iterable[Mapping[str, Any]]) -> Dict[str, StatsSample]:
    samples: Dict[str, StatsSample] = {}
    for entry in entries:
        name = entry.get("Name") or entry.get("name")
        if not name:
            continue
        usage_text = entry.get("MemUsage") or entry.get("mem_usage") or ""
        usage, limit = parse_mem_usage(str(usage_text))
        samples[str(name)] = StatsSample(
            name=str(name),
            working_set_bytes=usage,
            memory_limit_bytes=limit,
        )
    return samples


def _parse_stats_table(text: str) -> Dict[str, StatsSample]:
    """Parse the default ``docker stats --no-stream`` table.

    Fields are single tokens except the three that contain `` / `` (memory, net
    I/O, block I/O). Reading from the left is stable: id, name, CPU%,
    ``usage / limit``, MEM%, then the rest.
    """
    samples: Dict[str, StatsSample] = {}
    lines = [line for line in text.splitlines() if line.strip()]
    for line in lines:
        tokens = line.split()
        if len(tokens) < 6 or tokens[4] != "/":
            continue
        usage = parse_bytes(tokens[3])
        limit = parse_bytes(tokens[5])
        if usage is None:
            continue
        samples[tokens[1]] = StatsSample(
            name=tokens[1], working_set_bytes=usage, memory_limit_bytes=limit
        )
    return samples


def parse_stats(document: object) -> Dict[str, StatsSample]:
    """Parse ``docker stats`` output keyed by container name.

    Accepts ``--format '{{json .}}'`` (one object per line), a JSON array, a
    list of already-parsed objects, or the default table.
    """
    if document in (None, ""):
        return {}
    if isinstance(document, (list, tuple)):
        return _samples_from_entries(
            [entry for entry in document if isinstance(entry, Mapping)]
        )
    text = str(document).strip()
    if not text:
        return {}
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return _samples_from_entries(
                [entry for entry in parsed if isinstance(entry, Mapping)]
            )
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and lines[0].lstrip().startswith("{"):
        entries: List[Mapping[str, Any]] = []
        for line in lines:
            try:
                parsed_line = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed_line, Mapping):
                entries.append(parsed_line)
        return _samples_from_entries(entries)
    return _parse_stats_table(text)


def _match_sample(
    samples: Mapping[str, StatsSample], container: ContainerHealth
) -> Optional[StatsSample]:
    """Find a stats row for a container, tolerating Compose' name shortening."""
    if container.name in samples:
        return samples[container.name]
    for name, sample in samples.items():
        if container.name.endswith(f"-{name}") or name.endswith(f"-{container.name}"):
            return sample
    return None


def _match_keyed(
    mapping: Mapping[str, str], container: ContainerHealth
) -> Optional[str]:
    if container.name in mapping:
        return mapping[container.name]
    short_id = container.container_id[:12]
    if short_id and short_id in mapping:
        return mapping[short_id]
    for key, value in mapping.items():
        if container.name.endswith(f"-{key}") or key.endswith(f"-{container.name}"):
            return value
    return None


# ---------------------------------------------------------------------------
# snapshots and recorded input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthSnapshot:
    """One observation of the stack."""

    captured_at: str
    source: str
    available: bool
    containers: Tuple[ContainerHealth, ...] = ()
    error: str = ""

    def find(self, name: str) -> Optional[ContainerHealth]:
        for container in self.containers:
            if container.name == name:
                return container
        return None

    def to_document(self) -> Dict[str, object]:
        return {
            "captured_at": self.captured_at,
            "source": self.source,
            "available": self.available,
            "error": self.error,
            "containers": [container.to_document() for container in self.containers],
        }


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def unavailable_snapshot(reason: str, source: str = "unavailable") -> HealthSnapshot:
    """A snapshot that records why nothing was measured."""
    return HealthSnapshot(captured_at=_now(), source=source, available=False, error=reason)


def snapshot_from_recording(
    recording: Mapping[str, Any], *, captured_at: Optional[str] = None, source: str = "snapshot"
) -> HealthSnapshot:
    """Build a snapshot from the raw commands that recorded it.

    ``recording`` holds what the probe captured:

    - ``inspect``: ``docker inspect`` output (JSON text or parsed list);
    - ``stats``: ``docker stats --no-stream --format '{{json .}}'`` output;
    - ``memory_events``: container name/id -> ``memory.events`` text;
    - ``memory_peak``: container name/id -> ``memory.peak`` text;
    - ``dmesg``: host kernel log text.
    """
    containers = parse_inspect(recording.get("inspect"))
    samples = parse_stats(recording.get("stats"))
    memory_events = _mapping(recording.get("memory_events"))
    memory_peak = _mapping(recording.get("memory_peak"))
    oom_lines = parse_oom_lines(str(recording.get("dmesg") or ""))

    observed: List[ContainerHealth] = []
    for container in containers:
        observed.append(
            container.with_sample(_match_sample(samples, container))
            .with_memory_events(str(_match_keyed(memory_events, container) or ""))
            .with_peak(str(_match_keyed(memory_peak, container) or ""))
            .with_oom_evidence(oom_lines)
        )
    return HealthSnapshot(
        captured_at=str(recording.get("captured_at") or captured_at or _now()),
        source=str(recording.get("source") or source),
        available=bool(observed),
        containers=tuple(observed),
        error="" if observed else "no containers in the recording",
    )


def load_snapshot_file(path: str) -> Tuple[HealthSnapshot, HealthSnapshot]:
    """Read a two-phase snapshot file written by ``e2e health-snapshot``."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise HealthError(f"cannot read the health snapshot: {error}") from None
    except ValueError as error:
        raise HealthError(f"health snapshot is not JSON: {error}") from None
    if not isinstance(document, Mapping):
        raise HealthError("health snapshot must be a JSON object")
    before = document.get("before")
    after = document.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise HealthError(
            "health snapshot must contain 'before' and 'after' recordings; "
            "capture both phases with `python -m e2e health-snapshot`"
        )
    return (
        snapshot_from_recording(before, captured_at=str(document.get("captured_at") or "")),
        snapshot_from_recording(after, captured_at=str(document.get("captured_at") or "")),
    )


# ---------------------------------------------------------------------------
# probe (the only part that talks to Docker)
# ---------------------------------------------------------------------------


def _default_command(argv: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise HealthError(f"{' '.join(argv)} failed: {error}") from None
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip().splitlines()
        detail = message[0] if message else f"exit code {completed.returncode}"
        raise HealthError(f"{' '.join(argv)} failed: {detail}")
    return completed.stdout


class DockerProbe:
    """Collect a recording with the Docker CLI.

    ``command`` and ``which`` are injectable so the command plan is testable
    without a daemon. :meth:`capture` is all-or-nothing for the inspect step and
    best-effort for the cgroup and kernel evidence, which may legitimately be
    unreadable from the caller's account.
    """

    def __init__(
        self,
        *,
        command: Optional[Callable[[Sequence[str]], str]] = None,
        which: Optional[Callable[[str], Optional[str]]] = None,
        project: Optional[str] = None,
    ) -> None:
        self._command = command or _default_command
        self._which = which or shutil.which
        self._project = (
            project or os.environ.get(COMPOSE_PROJECT_ENV) or DEFAULT_COMPOSE_PROJECT
        )

    @property
    def project(self) -> str:
        return self._project

    def capture(self) -> Dict[str, Any]:
        """Record the stack's current state; raises :class:`HealthError`."""
        if self._which("docker") is None:
            raise HealthError(
                "the docker CLI is not on PATH, so stack health cannot be read here "
                "(the runner container has no Docker socket by design; capture the run "
                "window on the host with `python -m e2e health-snapshot`)"
            )
        container_ids = [
            line.strip()
            for line in self._command(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    f"label={COMPOSE_PROJECT_LABEL}={self._project}",
                ]
            ).splitlines()
            if line.strip()
        ]
        if not container_ids:
            raise HealthError(
                f"no containers found for Compose project {self._project!r}"
            )

        inspect = self._command(["docker", "inspect", *container_ids])
        stats = self._command(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", *container_ids]
        )
        try:
            inspect_document = json.loads(inspect)
        except ValueError as error:
            raise HealthError(f"docker inspect did not return JSON: {error}") from None
        containers = parse_inspect(inspect_document)

        memory_events: Dict[str, str] = {}
        memory_peak: Dict[str, str] = {}
        for container in containers:
            if not container.running:
                continue
            key = container.name or container.container_id
            events = self._try_exec(container.container_id, CGROUP_MEMORY_EVENTS)
            if events is None:
                events = self._try_exec(container.container_id, CGROUP_V1_OOM_CONTROL)
            if events is not None:
                memory_events[key] = events
            peak = self._try_exec(container.container_id, CGROUP_MEMORY_PEAK)
            if peak is not None:
                memory_peak[key] = peak

        return {
            "captured_at": _now(),
            "source": "docker",
            "project": self._project,
            "inspect": inspect_document,
            "stats": stats,
            "memory_events": memory_events,
            "memory_peak": memory_peak,
            "dmesg": self._try_host_log(),
        }

    def _try_exec(self, container_id: str, path: str) -> Optional[str]:
        if not container_id:
            return None
        try:
            return self._command(["docker", "exec", container_id, "cat", path])
        except HealthError:
            return None

    def _try_host_log(self) -> str:
        if self._which(DMESG_COMMAND[0]) is None:
            return ""
        try:
            return self._command(list(DMESG_COMMAND))
        except HealthError:
            return ""


def capture(
    *, dry_run: bool = False, probe: Optional[DockerProbe] = None
) -> Tuple[HealthSnapshot, Optional[Dict[str, Any]]]:
    """Take one snapshot, returning it with the raw recording when captured."""
    if dry_run:
        return (
            unavailable_snapshot(
                "dry run: stack health is not collected, and a dry run proves nothing "
                "about the environment",
                source="dry-run",
            ),
            None,
        )
    active = probe or DockerProbe()
    try:
        recording = active.capture()
    except HealthError as error:
        return unavailable_snapshot(str(error)), None
    snapshot = snapshot_from_recording(recording, source="docker")
    if not snapshot.available:
        return (
            unavailable_snapshot(
                snapshot.error or "the Docker probe returned no containers",
                source="docker",
            ),
            recording,
        )
    return snapshot, recording


def write_snapshot_phase(path: str, phase: str, probe: Optional[DockerProbe] = None) -> str:
    """Record one phase into a two-phase snapshot file for an in-stack run."""
    if phase not in ("before", "after"):
        raise HealthError(f"unknown snapshot phase {phase!r}")
    active = probe or DockerProbe()
    recording = active.capture()
    document: Dict[str, Any] = {}
    try:
        existing = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(existing, Mapping):
            document = dict(existing)
    except (OSError, ValueError):
        document = {}
    document.setdefault("captured_at", _now())
    document["project"] = active.project
    if phase == "before":
        # A fresh window must not be classified against a stale after-phase.
        document.pop("after", None)
    document[phase] = recording
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContainerReport:
    """One container's contribution to the run verdict."""

    name: str
    state: str
    health: Optional[str]
    restart_count_before: Optional[int]
    restart_count_after: Optional[int]
    restarts_during_run: int
    memory_limit_bytes: Optional[int]
    working_set_bytes: Optional[int]
    peak_working_set_bytes: Optional[int]
    peak_source: Optional[str]
    oom_killed: bool
    oom_kill_count: Optional[int]
    oom_kills_during_run: Optional[int]
    oom_evidence: Tuple[str, ...]
    notes: Tuple[str, ...] = ()

    @property
    def headroom_bytes(self) -> Optional[int]:
        if not self.memory_limit_bytes or self.peak_working_set_bytes is None:
            return None
        return self.memory_limit_bytes - self.peak_working_set_bytes

    def to_document(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "state": self.state,
            "health": self.health,
            "restart_count_before": self.restart_count_before,
            "restart_count_after": self.restart_count_after,
            "restarts_during_run": self.restarts_during_run,
            "memory_limit_bytes": self.memory_limit_bytes,
            "working_set_bytes": self.working_set_bytes,
            "peak_working_set_bytes": self.peak_working_set_bytes,
            "peak_source": self.peak_source,
            "headroom_bytes": self.headroom_bytes,
            "oom_killed": self.oom_killed,
            "oom_kill_count": self.oom_kill_count,
            "oom_kills_during_run": self.oom_kills_during_run,
            "oom_evidence": list(self.oom_evidence),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class EnvironmentVerdict:
    """The run's environment classification and the evidence behind it."""

    verdict: str
    summary: str
    reasons: Tuple[str, ...] = ()
    containers: Tuple[ContainerReport, ...] = ()
    before: Optional[HealthSnapshot] = None
    after: Optional[HealthSnapshot] = None

    @property
    def invalid(self) -> bool:
        return self.verdict == INVALID

    def to_document(self) -> Dict[str, object]:
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "containers": [container.to_document() for container in self.containers],
            "snapshots": {
                "before": self.before.to_document() if self.before else None,
                "after": self.after.to_document() if self.after else None,
            },
        }

    def render_lines(self) -> List[str]:
        """Plain-text block for the console and JUnit ``system-out``."""
        lines = [f"environment: {self.verdict} - {self.summary}"]
        for reason in self.reasons:
            lines.append(f"  cause: {reason}")
        for container in self.containers:
            health = container.health or "no healthcheck"
            peak = (
                f"peak working set {format_bytes(container.peak_working_set_bytes)}"
                f" ({container.peak_source})"
                if container.peak_working_set_bytes is not None
                else "peak working set unavailable"
            )
            lines.append(
                f"  {container.name}: state={container.state or 'unknown'} "
                f"health={health} "
                f"restarts={container.restart_count_after if container.restart_count_after is not None else 'gone'} "
                f"(during run {container.restarts_during_run}) "
                f"limit={describe_limit(container.memory_limit_bytes)} {peak}"
            )
            if container.oom_kills_during_run:
                lines.append(
                    f"    OOM kills during run: {container.oom_kills_during_run} "
                    f"(cgroup oom_kill counter)"
                )
            for line in container.oom_evidence:
                lines.append(f"    kernel: {line}")
            for note in container.notes:
                lines.append(f"    note: {note}")
        return lines


def _report_container(
    before: Optional[ContainerHealth], after: Optional[ContainerHealth]
) -> ContainerReport:
    source = after or before
    assert source is not None
    before_count = before.restart_count if before is not None else None
    after_count = after.restart_count if after is not None else None
    restarts_during = 0
    notes: List[str] = []
    if before_count is not None and after_count is not None:
        if after_count >= before_count:
            restarts_during = after_count - before_count
        else:
            # The counter only decreases when Docker recreated the container;
            # the recreate itself is the evidence, so the delta stays at zero.
            restarts_during = 0

    oom_kills_during: Optional[int] = None
    if (
        before is not None
        and after is not None
        and before.oom_kill_count is not None
        and after.oom_kill_count is not None
    ):
        oom_kills_during = after.oom_kill_count - before.oom_kill_count

    observed = [
        value
        for value in (
            before.peak_working_set_bytes if before is not None else None,
            before.working_set_bytes if before is not None else None,
            after.peak_working_set_bytes if after is not None else None,
            after.working_set_bytes if after is not None else None,
        )
        if value is not None
    ]
    peak = max(observed) if observed else None
    peak_source: Optional[str] = None
    if peak is not None:
        peak_source = (
            "cgroup memory.peak"
            if (after is not None and after.peak_source == "cgroup memory.peak")
            else "docker stats snapshots"
        )

    if before_count:
        notes.append(
            f"restart count was already {before_count} before the run window; "
            "those restarts belong to earlier runs"
        )
    if source.health == "unhealthy":
        notes.append("the container healthcheck reported unhealthy at the end of the run")
    if source.health is None:
        notes.append("no healthcheck is defined, so health status proves nothing")
    if not source.memory_limit_bytes:
        notes.append(
            "no container memory limit is set (HostConfig.Memory is 0), so the cgroup "
            "limit is the host's"
        )
    if source.oom_killed:
        notes.append(
            "Docker reports State.OOMKilled; that flag is only set when the daemon "
            "observes the kill, so a false value proves nothing"
        )
    if after is None:
        notes.append("the container is gone from the stack at the end of the run")
    elif before is None:
        notes.append("the container did not exist at the start of the run")

    return ContainerReport(
        name=source.name,
        state=source.state,
        health=source.health,
        restart_count_before=before_count,
        restart_count_after=after_count,
        restarts_during_run=restarts_during,
        memory_limit_bytes=source.memory_limit_bytes,
        working_set_bytes=after.working_set_bytes if after is not None else None,
        peak_working_set_bytes=peak,
        peak_source=peak_source,
        oom_killed=source.oom_killed,
        oom_kill_count=after.oom_kill_count if after is not None else None,
        oom_kills_during_run=oom_kills_during,
        oom_evidence=tuple(
            dict.fromkeys(
                [
                    *(before.oom_evidence if before is not None else ()),
                    *(after.oom_evidence if after is not None else ()),
                ]
            )
        ),
        notes=tuple(notes),
    )


def _classify_reasons(report: ContainerReport) -> List[str]:
    reasons: List[str] = []
    name = report.name
    if report.restart_count_before is None or report.restart_count_after is None:
        reasons.append(f"{name} was not present for both snapshots, so it cannot be trusted")
        return reasons
    if report.restart_count_after < report.restart_count_before:
        reasons.append(
            f"{name} was recreated during the run (restart counter went from "
            f"{report.restart_count_before} to {report.restart_count_after})"
        )
    elif report.restarts_during_run:
        reasons.append(
            f"{name} restarted {report.restarts_during_run} time(s) during the run "
            f"(restart count {report.restart_count_before} -> {report.restart_count_after})"
        )
    if report.oom_kills_during_run:
        reasons.append(
            f"{name} recorded {report.oom_kills_during_run} new OOM kill(s) during the run "
            "(cgroup memory.events oom_kill)"
        )
    if report.state and report.state not in ("running", "paused"):
        reasons.append(f"{name} is {report.state} at the end of the run, not running")
    return reasons


def classify(
    before: HealthSnapshot, after: HealthSnapshot
) -> EnvironmentVerdict:
    """Classify a run as valid, invalid or unknown from two snapshots.

    Only deltas across the run window invalidate a run: a restart, a recreate,
    a new cgroup OOM kill, or a container that stopped or appeared during the
    window. Anything that merely looks suspicious - a pre-existing restart
    count, an unhealthy healthcheck, a missing memory limit - is reported as a
    note and never changes the verdict, because it cannot be attributed to this
    run and must not hide an application defect.
    """
    if not before.available or not after.available:
        reason = after.error or before.error or "no snapshot was taken"
        return EnvironmentVerdict(
            verdict=UNKNOWN,
            summary="stack health was not collected; the environment is unverified",
            reasons=(reason,),
            before=before,
            after=after,
        )

    before_by_name = {container.name: container for container in before.containers}
    after_by_name = {container.name: container for container in after.containers}
    names = sorted(set(before_by_name) | set(after_by_name))
    reports = tuple(
        _report_container(before_by_name.get(name), after_by_name.get(name))
        for name in names
    )
    reasons: List[str] = []
    for report in reports:
        reasons.extend(_classify_reasons(report))

    if reasons:
        return EnvironmentVerdict(
            verdict=INVALID,
            summary=(
                "the environment, not the application, is suspect: the stack changed "
                "during the run window"
            ),
            reasons=tuple(reasons),
            containers=reports,
            before=before,
            after=after,
        )
    return EnvironmentVerdict(
        verdict=VALID,
        summary="no container restarted or was killed during the run window",
        containers=reports,
        before=before,
        after=after,
    )


def check(*, dry_run: bool = False) -> EnvironmentVerdict:
    """Point-in-time health for ``e2e status``; no run window is classified.

    The numbers are the same ones a run records, but without a before/after pair
    there is no delta, so the summary says so instead of borrowing the run
    verdict's wording.
    """
    snapshot, _recording = capture(dry_run=dry_run)
    if not snapshot.available:
        return classify(snapshot, snapshot)
    return replace(
        classify(snapshot, snapshot),
        summary=(
            "point-in-time state; restart counts are cumulative and no run window "
            "was classified"
        ),
    )
