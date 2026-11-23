"""Run reporting: JSON, JUnit XML, Markdown and the artifact bundle.

Reports are written per run under the artifacts directory::

    <artifacts>/report.json       machine-readable, survives CI log truncation
    <artifacts>/junit.xml         consumed by CI test reporters
    <artifacts>/summary.md        human summary for the PR comment
    <artifacts>/health.json       stack health: restarts, working set, OOM evidence
    <artifacts>/<scenario>/...    screenshots and per-scenario result
    <artifacts>/<scenario>/bundle.zip

The JUnit file is the contract with CI; the JSON file is the contract with
whoever debugs a failure later.

Every report carries an environment-health block. A run whose stack changed
during the run window (a container restarted, was recreated, was OOM-killed or
stopped) is marked **invalid**, not failed: the scenario results are then not
attributable to the application, and the run's exit code is
:data:`e2e.health.EXIT_INVALID_ENVIRONMENT` so CI can retry instead of filing a
defect. See ``tests/docs/diagnostics.md``.
"""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from .health import (
    EXIT_INVALID_ENVIRONMENT,
    INVALID,
    UNKNOWN,
    VALID,
    EnvironmentVerdict,
    describe_limit,
    format_bytes,
)

PASSED = "passed"
FAILED = "failed"
ERRORED = "errored"
SKIPPED = "skipped"

_FINAL_STATUS = (PASSED, FAILED, ERRORED, SKIPPED)


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


@dataclass
class StepResult:
    """Outcome of one scenario step."""

    index: int
    action: str
    status: str
    duration_ms: float = 0.0
    message: str = ""
    artifacts: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == PASSED

    def to_document(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "action": self.action,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 1),
            "message": self.message,
            "artifacts": list(self.artifacts),
        }


@dataclass
class ScenarioResult:
    """Outcome of one scenario run."""

    scenario_id: str
    name: str
    suite: str
    tier: str
    device: str
    mode: str
    fixture: str
    language: str
    source: str
    status: str
    started_at: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    steps: List[StepResult] = field(default_factory=list)
    requests: int = 0
    failures: List[str] = field(default_factory=list)
    artifacts_dir: str = ""
    dry_run: bool = False

    @property
    def skipped_steps(self) -> List[StepResult]:
        return [step for step in self.steps if step.status == SKIPPED]

    def to_document(self) -> Dict[str, object]:
        return {
            "scenario": self.scenario_id,
            "name": self.name,
            "suite": self.suite,
            "tier": self.tier,
            "device": self.device,
            "mode": self.mode,
            "fixture": self.fixture,
            "language": self.language,
            "source": self.source,
            "status": self.status,
            "started_at": _iso(self.started_at),
            "duration_ms": round(self.duration_ms, 1),
            "requests": self.requests,
            "dry_run": self.dry_run,
            "failures": list(self.failures),
            "artifacts_dir": self.artifacts_dir,
            "skipped_steps": [step.action for step in self.skipped_steps],
            "steps": [step.to_document() for step in self.steps],
        }


class Report:
    """Collect scenario results and write the report formats."""

    def __init__(self) -> None:
        self.results: List[ScenarioResult] = []
        self.started_at = time.time()
        #: Stack health for the run window; ``None`` means it was not recorded
        #: and every renderer reports that explicitly instead of claiming health.
        self.environment: Optional[EnvironmentVerdict] = None

    def add(self, result: ScenarioResult) -> None:
        self.results.append(result)

    # -- aggregate ---------------------------------------------------------
    def counts(self) -> Dict[str, int]:
        counts = {status: 0 for status in _FINAL_STATUS}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return counts

    @property
    def exit_code(self) -> int:
        counts = self.counts()
        if self.environment is not None and self.environment.invalid:
            return EXIT_INVALID_ENVIRONMENT
        return 1 if counts.get(FAILED) or counts.get(ERRORED) else 0

    def environment_document(self) -> Dict[str, object]:
        """The environment block, always present, never claiming unmeasured health."""
        if self.environment is None:
            return {
                "verdict": UNKNOWN,
                "summary": "stack health was not collected for this run",
                "reasons": ["no environment verdict was recorded"],
                "containers": [],
                "snapshots": {"before": None, "after": None},
            }
        return self.environment.to_document()

    # -- writers -----------------------------------------------------------
    def write_json(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "generated_at": _iso(self.started_at),
            "counts": self.counts(),
            "environment": self.environment_document(),
            "results": [result.to_document() for result in self.results],
        }
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return path

    def write_environment(self, path: Path) -> Path:
        """Write the environment block on its own, as ``health.json``."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.environment_document(), indent=2) + "\n", encoding="utf-8"
        )
        return path

    def write_junit(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        root = ET.Element("testsuites", name="openhab-garmin-e2e")
        self._add_environment_suite(root)

        by_suite: Dict[str, List[ScenarioResult]] = {}
        for result in self.results:
            by_suite.setdefault(result.suite, []).append(result)

        for suite, results in sorted(by_suite.items()):
            suite_element = ET.SubElement(
                root,
                "testsuite",
                name=suite,
                tests=str(len(results)),
                failures=str(sum(1 for item in results if item.status == FAILED)),
                errors=str(sum(1 for item in results if item.status == ERRORED)),
                skipped=str(sum(1 for item in results if item.status == SKIPPED)),
                time=f"{sum(item.duration_ms for item in results) / 1000.0:.3f}",
            )
            for result in results:
                case = ET.SubElement(
                    suite_element,
                    "testcase",
                    classname=f"{suite}.{result.tier}",
                    name=result.name,
                    time=f"{result.duration_ms / 1000.0:.3f}",
                )
                if result.status in (FAILED, ERRORED):
                    failure = ET.SubElement(
                        case,
                        "failure",
                        message=result.failures[0] if result.failures else result.status,
                        type=result.status,
                    )
                    failure.text = "\n".join(result.failures)
                elif result.status == SKIPPED:
                    ET.SubElement(case, "skipped", message="scenario skipped")
                if result.skipped_steps:
                    ET.SubElement(case, "system-out").text = (
                        "skipped steps: "
                        + ", ".join(step.action for step in result.skipped_steps)
                    )

        ET.indent(root)
        tree = ET.ElementTree(root)
        tree.write(path, encoding="utf-8", xml_declaration=True)
        return path

    def _add_environment_suite(self, root: ET.Element) -> None:
        """Add a zero-test ``environment`` suite carrying the health block.

        ``tests="0"`` keeps the suite out of the test counts, and it never has a
        ``failure`` element: an environment problem is reported as the run's
        verdict, not as a failing test.
        """
        environment = self.environment
        verdict = environment.verdict if environment is not None else UNKNOWN
        summary = (
            environment.summary
            if environment is not None
            else "stack health was not collected for this run"
        )
        suite = ET.SubElement(
            root,
            "testsuite",
            name="environment",
            tests="0",
            failures="0",
            errors="0",
            skipped="0",
            time="0.000",
        )
        properties = ET.SubElement(suite, "properties")
        ET.SubElement(properties, "property", name="environment.verdict", value=verdict)
        ET.SubElement(properties, "property", name="environment.summary", value=summary)
        if environment is not None:
            for index, reason in enumerate(environment.reasons):
                ET.SubElement(
                    properties,
                    "property",
                    name=f"environment.reason.{index}",
                    value=reason,
                )
            for container in environment.containers:
                prefix = f"environment.container.{container.name}"
                values = {
                    "state": container.state,
                    "health": container.health,
                    "restart_count": container.restart_count_after,
                    "restarts_during_run": container.restarts_during_run,
                    "peak_working_set_bytes": container.peak_working_set_bytes,
                    "memory_limit_bytes": container.memory_limit_bytes,
                    "oom_kills_during_run": container.oom_kills_during_run,
                }
                for key, value in values.items():
                    if value is None:
                        continue
                    ET.SubElement(
                        properties, "property", name=f"{prefix}.{key}", value=str(value)
                    )
        if environment is not None:
            lines = environment.render_lines()
        else:
            lines = [
                f"environment: {UNKNOWN} - stack health was not collected for this run",
                "  note: see tests/docs/diagnostics.md for how to capture it",
            ]
        ET.SubElement(suite, "system-out").text = "\n".join(lines)

    def write_markdown(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        counts = self.counts()
        lines = [
            "# E2E harness run",
            "",
            f"Generated: {_iso(self.started_at)}",
            "",
            "| Result | Count |",
            "| --- | --- |",
        ]
        for status in _FINAL_STATUS:
            lines.append(f"| {status} | {counts.get(status, 0)} |")

        lines += self._environment_markdown()

        lines += [
            "",
            "## Scenarios",
            "",
            "| Scenario | Tier | Device | Status | Duration | Notes |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for result in self.results:
            notes = []
            if result.dry_run:
                notes.append("dry run")
            if result.skipped_steps:
                notes.append(f"{len(result.skipped_steps)} step(s) skipped")
            if result.failures:
                notes.append(result.failures[0].splitlines()[0])
            notes_text = "; ".join(notes)
            lines.append(
                f"| {result.scenario_id} | {result.tier} | {result.device} | {result.status} "
                f"| {result.duration_ms / 1000.0:.1f}s | {notes_text} |"
            )

        lines += [
            "",
            "Screenshots and per-scenario JSON live next to this file, one directory per scenario.",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _environment_markdown(self) -> List[str]:
        environment = self.environment
        verdict = environment.verdict if environment is not None else UNKNOWN
        summary = (
            environment.summary
            if environment is not None
            else "stack health was not collected for this run"
        )
        lines = ["", "## Environment health", "", f"Verdict: **{verdict}** - {summary}"]
        if verdict == INVALID:
            lines += [
                "",
                "This run is **invalid**. The environment, not the application, is suspect,",
                "so the scenario results below cannot be attributed to the app. Re-run on a",
                "healthy stack before treating any failure as a defect.",
            ]
        if environment is not None and environment.reasons:
            lines += ["", "Causes:", ""]
            for reason in environment.reasons:
                lines.append(f"- {reason}")
        if environment is not None and environment.containers:
            lines += [
                "",
                "| Container | State | Health | Restarts (run) | Memory limit | Peak working set | OOM kills (run) |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for container in environment.containers:
                peak = format_bytes(container.peak_working_set_bytes)
                oom_kills = (
                    "unavailable"
                    if container.oom_kills_during_run is None
                    else str(container.oom_kills_during_run)
                )
                lines.append(
                    f"| {container.name} | {container.state or 'unknown'} "
                    f"| {container.health or 'no healthcheck'} "
                    f"| {container.restart_count_after if container.restart_count_after is not None else 'gone'} "
                    f"| {describe_limit(container.memory_limit_bytes)} | {peak} | {oom_kills} |"
                )
            notes = [
                (container.name, note)
                for container in environment.containers
                for note in container.notes
            ]
            kernels = [
                (container.name, line)
                for container in environment.containers
                for line in container.oom_evidence
            ]
            if notes or kernels:
                lines += ["", "Notes:", ""]
                for name, note in notes:
                    lines.append(f"- {name}: {note}")
                for name, kernel_line in kernels:
                    lines.append(f"- {name}: `{kernel_line}`")
        if verdict != VALID:
            lines += [
                "",
                "The classification rule and the exact commands are in",
                "[docs/diagnostics.md](../docs/diagnostics.md).",
            ]
        return lines

    def write_artifacts(self, directory: Path) -> Dict[str, Path]:
        """Write all reports and per-scenario bundles into ``directory``."""
        directory.mkdir(parents=True, exist_ok=True)
        written = {
            "json": self.write_json(directory / "report.json"),
            "junit": self.write_junit(directory / "junit.xml"),
            "markdown": self.write_markdown(directory / "summary.md"),
            "environment": self.write_environment(directory / "health.json"),
        }
        for result in self.results:
            if result.artifacts_dir:
                scenario_dir = Path(result.artifacts_dir)
                if result.status in (FAILED, ERRORED) and scenario_dir.is_dir():
                    # A failed scenario's bundle must carry the environment
                    # evidence too, otherwise "the shim was dying" is invisible
                    # when someone unzips just the failing scenario.
                    self.write_environment(scenario_dir / "environment.json")
                bundle = write_bundle(scenario_dir, result)
                if bundle is not None:
                    written[f"bundle:{result.scenario_id}"] = bundle
        return written


def _step_result_from_document(entry: Mapping[str, object]) -> StepResult:
    return StepResult(
        index=int(entry.get("index") or 0),
        action=str(entry.get("action") or ""),
        status=str(entry.get("status") or ""),
        duration_ms=float(entry.get("duration_ms") or 0.0),
        message=str(entry.get("message") or ""),
        artifacts=[str(path) for path in entry.get("artifacts") or []],
    )


def _scenario_result_from_document(entry: Mapping[str, object]) -> ScenarioResult:
    started_at = time.time()
    started = entry.get("started_at")
    if isinstance(started, str):
        try:
            started_at = datetime.fromisoformat(started).timestamp()
        except ValueError:
            pass
    return ScenarioResult(
        scenario_id=str(entry.get("scenario") or ""),
        name=str(entry.get("name") or ""),
        suite=str(entry.get("suite") or ""),
        tier=str(entry.get("tier") or ""),
        device=str(entry.get("device") or ""),
        mode=str(entry.get("mode") or ""),
        fixture=str(entry.get("fixture") or ""),
        language=str(entry.get("language") or ""),
        source=str(entry.get("source") or ""),
        status=str(entry.get("status") or ""),
        started_at=started_at,
        duration_ms=float(entry.get("duration_ms") or 0.0),
        steps=[
            _step_result_from_document(step)
            for step in entry.get("steps") or []
            if isinstance(step, Mapping)
        ],
        requests=int(entry.get("requests") or 0),
        failures=[str(failure) for failure in entry.get("failures") or []],
        artifacts_dir=str(entry.get("artifacts_dir") or ""),
        dry_run=bool(entry.get("dry_run")),
    )


def load_report(directory: Path) -> Report:
    """Rebuild a :class:`Report` from a written ``report.json``.

    A run writes its reports once, at the end. When the stack-health window is
    recorded on the Docker host around a run that executes inside the runner
    container, the after-phase is only available after the run has finished, so
    ``e2e health-snapshot --phase after --artifacts`` re-renders the same
    directory from the document instead of re-running any scenario.
    """
    path = Path(directory) / "report.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error}") from None
    except ValueError as error:
        raise ValueError(f"{path} is not JSON: {error}") from None

    report = Report()
    generated = document.get("generated_at")
    if isinstance(generated, str):
        try:
            report.started_at = datetime.fromisoformat(generated).timestamp()
        except ValueError:
            pass
    for entry in document.get("results") or []:
        if isinstance(entry, Mapping):
            report.add(_scenario_result_from_document(entry))
    return report


def write_bundle(scenario_dir: Path, result: ScenarioResult) -> Optional[Path]:
    """Zip a scenario's artifacts (screenshots plus its own result JSON)."""
    if not scenario_dir.is_dir():
        return None
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "result.json").write_text(
        json.dumps(result.to_document(), indent=2) + "\n", encoding="utf-8"
    )
    bundle = scenario_dir / "bundle.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(scenario_dir.rglob("*")):
            if path.is_file() and path != bundle:
                archive.write(path, path.relative_to(scenario_dir))
    return bundle
