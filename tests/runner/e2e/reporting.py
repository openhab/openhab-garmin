"""Run reporting: JSON, JUnit XML, Markdown and the artifact bundle.

Reports are written per run under the artifacts directory::

    <artifacts>/report.json       machine-readable, survives CI log truncation
    <artifacts>/junit.xml         consumed by CI test reporters
    <artifacts>/summary.md        human summary for the PR comment
    <artifacts>/<scenario>/...    screenshots and per-scenario result
    <artifacts>/<scenario>/bundle.zip

The JUnit file is the contract with CI; the JSON file is the contract with
whoever debugs a failure later.
"""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

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
    """Collect scenario results and write the three report formats."""

    def __init__(self) -> None:
        self.results: List[ScenarioResult] = []
        self.started_at = time.time()

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
        return 1 if counts.get(FAILED) or counts.get(ERRORED) else 0

    # -- writers -----------------------------------------------------------
    def write_json(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "generated_at": _iso(self.started_at),
            "counts": self.counts(),
            "results": [result.to_document() for result in self.results],
        }
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return path

    def write_junit(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        root = ET.Element("testsuites", name="openhab-garmin-e2e")

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

        lines += ["", "| Scenario | Tier | Device | Status | Duration | Notes |", "| --- | --- | --- | --- | --- | --- |"]
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

    def write_artifacts(self, directory: Path) -> Dict[str, Path]:
        """Write all reports and per-scenario bundles into ``directory``."""
        directory.mkdir(parents=True, exist_ok=True)
        written = {
            "json": self.write_json(directory / "report.json"),
            "junit": self.write_junit(directory / "junit.xml"),
            "markdown": self.write_markdown(directory / "summary.md"),
        }
        for result in self.results:
            if result.artifacts_dir:
                bundle = write_bundle(Path(result.artifacts_dir), result)
                if bundle is not None:
                    written[f"bundle:{result.scenario_id}"] = bundle
        return written


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
