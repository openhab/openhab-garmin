"""The #370 pass criteria and the overall verdict.

The issue defines six pass criteria and three verdict paths. This module maps
observed checks onto the criteria and applies the decision rule:

- **pass** -- every criterion is met. Recommendation: implement
  ``WineSimulatorDriver`` and continue on Linux only.
- **partial** -- the simulator and console work, but at least one capability
  does not. Recommendation: record the failing capability, waive the affected
  scenarios explicitly, and keep the remaining tiers.
- **fail** -- the foundation does not work (the simulator does not start or
  render, or ``monkeydo`` does not stream trace lines). Recommendation: fall
  back to a Windows guest with the same driver contract.

A criterion with a ``skipped`` required check cannot be called met: the spike
saw no evidence either way. That is the same rule the E2E harness uses for
skipped assertions. Supporting checks (for example the occluded-window capture)
add evidence but never decide a criterion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .model import MET, NOT_MET, SKIPPED, Check, ProbeDocument

PASS = "pass"
PARTIAL = "partial"
FAIL = "fail"

VERDICTS = (PASS, PARTIAL, FAIL)

RECOMMENDATIONS: Mapping[str, str] = {
    PASS: (
        "Implement WineSimulatorDriver against tests/runner/e2e/sim/base.py "
        "and continue on Linux only."
    ),
    PARTIAL: (
        "Record which capability fails, waive the affected scenarios explicitly "
        "in the coverage matrix, and continue with the remaining tiers."
    ),
    FAIL: (
        "Document the failure, then fall back to a Windows guest (licence or "
        "evaluation) with the same driver contract; the rest of the unit stays "
        "unchanged."
    ),
}


@dataclass(frozen=True)
class Criterion:
    """One #370 pass criterion and the checks that decide it."""

    id: str
    title: str
    required: Tuple[str, ...]
    supporting: Tuple[str, ...] = ()

    @property
    def checks(self) -> Tuple[str, ...]:
        return self.required + self.supporting


# Titles are quoted from issue #370 so the verdict can be pasted into it.
CRITERIA: Tuple[Criterion, ...] = (
    Criterion(
        id="pc1",
        title=(
            "Simulator starts and renders the app under Wine without a "
            "Windows licence."
        ),
        required=("simulator.launches", "app.renders"),
        supporting=("preflight", "wine.available", "display.available", "wine.prefix"),
    ),
    Criterion(
        id="pc2",
        title="`monkeydo` launches the app and streams `E2E:` trace lines.",
        required=("monkeydo.launches", "console.trace_lines"),
    ),
    Criterion(
        id="pc3",
        title="Button and touch input reliably reach the app.",
        required=("input.buttons", "input.touch"),
    ),
    Criterion(
        id="pc4",
        title=(
            "Device/menu state can be set and read back, including connection "
            "mode and language."
        ),
        required=("menu.simulation", "menu.connection", "language.switch"),
        supporting=(
            "menu.simulation_ui",
            "config.simulation_state",
            "language.rendered",
        ),
    ),
    Criterion(
        id="pc5",
        title="Screenshots capture the watch face only.",
        required=("screenshot.watchface_only",),
        supporting=("screenshot.occluded", "screenshot.focusless"),
    ),
    Criterion(
        id="pc6",
        title="Timing is tight enough for polling and escalation tests.",
        required=("timing.poll_interval", "timing.escalation"),
    ),
)


@dataclass
class CriterionResult:
    """One criterion after the checks are combined."""

    id: str
    title: str
    status: str
    detail: str = ""
    checks: List[Check] = field(default_factory=list)

    @property
    def met(self) -> bool:
        return self.status == MET

    def to_document(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "checks": [check.to_document() for check in self.checks],
        }

    def evidence(self) -> List[str]:
        seen: List[str] = []
        for check in self.checks:
            for path in check.evidence:
                if path not in seen:
                    seen.append(path)
        return seen

    def measurements(self) -> Dict[str, float]:
        merged: Dict[str, float] = {}
        for check in self.checks:
            for key, value in check.measurements.items():
                merged[f"{check.id}.{key}"] = value
        return dict(sorted(merged.items()))


@dataclass
class Verdict:
    """Overall spike verdict plus everything that supports it."""

    verdict: str
    recommendation: str
    criteria: List[CriterionResult]
    checks: List[Check]
    unknown_checks: List[str] = field(default_factory=list)

    @property
    def statuses(self) -> Dict[str, str]:
        return {criterion.id: criterion.status for criterion in self.criteria}

    def to_document(self) -> Dict[str, object]:
        return {
            "issue": 370,
            "verdict": self.verdict,
            "recommendation": self.recommendation,
            "criteria": [criterion.to_document() for criterion in self.criteria],
            "checks": [check.to_document() for check in self.checks],
            "unknown_checks": list(self.unknown_checks),
        }


def combine(required: Sequence[Optional[Check]]) -> Tuple[str, str]:
    """Combine required checks into a criterion status and a short detail.

    - any ``not-met`` -> ``not-met`` (the criterion was disproved)
    - any ``skipped`` or absent -> ``skipped`` (not evaluable)
    - otherwise -> ``met``
    """
    ids = [check.id if check is not None else "<missing>" for check in required]
    statuses = [check.status if check is not None else SKIPPED for check in required]
    failed = [check_id for check_id, status in zip(ids, statuses) if status == NOT_MET]
    if failed:
        return NOT_MET, f"not observed: {', '.join(failed)}"
    skipped = [check_id for check_id, status in zip(ids, statuses) if status == SKIPPED]
    if skipped:
        return SKIPPED, f"not evaluable: {', '.join(skipped)}"
    return MET, "all required checks met"


def compute_verdict(
    probe: ProbeDocument,
    *,
    criteria: Sequence[Criterion] = CRITERIA,
) -> Verdict:
    """Map a probe document onto the #370 criteria and decide the verdict."""
    checks = probe.check_map()
    known: Dict[str, Check] = {}

    results: List[CriterionResult] = []
    for criterion in criteria:
        required: List[Check] = []
        supporting: List[Check] = []
        for check_id in criterion.required:
            check = checks.get(check_id)
            if check is None:
                check = Check(id=check_id, status=SKIPPED, detail="not reported by the probe")
            required.append(check)
            known[check_id] = check
        for check_id in criterion.supporting:
            check = checks.get(check_id)
            if check is not None:
                supporting.append(check)
                known[check_id] = check
        status, detail = combine(required)
        if status == MET and not all(check.met for check in required):
            # Defensive: combine() is the single source of truth.
            status, detail = SKIPPED, "not all required checks reported met"
        results.append(
            CriterionResult(
                id=criterion.id,
                title=criterion.title,
                status=status,
                detail=detail,
                checks=required + supporting,
            )
        )

    unknown = sorted(set(checks) - set(known))
    verdict = decide(results)
    return Verdict(
        verdict=verdict,
        recommendation=RECOMMENDATIONS[verdict],
        criteria=results,
        checks=[check for check in checks.values()],
        unknown_checks=unknown,
    )


def decide(results: Sequence[CriterionResult]) -> str:
    """Apply #370's three verdict paths.

    ``pc1`` (start and render) and ``pc2`` (console trace stream) are the
    foundation: without either, no tier can run under Wine, so they force
    ``fail`` rather than a partial waiver.
    """
    by_id = {result.id: result for result in results}
    for foundation in ("pc1", "pc2"):
        result = by_id.get(foundation)
        if result is not None and result.status == NOT_MET:
            return FAIL
    if results and all(result.met for result in results):
        return PASS
    return PARTIAL


def format_table(verdict: Verdict, width: int = 0) -> str:
    """Render the criteria table used on stdout and in ``verdict.md``."""
    labels = {"met": "met", "not-met": "not met", "skipped": "skipped"}
    rows = [
        (result.id, result.title, labels.get(result.status, result.status))
        for result in verdict.criteria
    ]
    if width <= 0:
        width = max((len(title) for _, title, _ in rows), default=0)
    lines = [f"verdict: {verdict.verdict}"]
    for criterion_id, title, status in rows:
        lines.append(f"  {criterion_id}  {title.ljust(width)}  {status}")
    lines.append("")
    lines.append(f"recommendation: {verdict.recommendation}")
    return "\n".join(lines)


def exit_code(verdict: Verdict) -> int:
    """Exit code for a completed spike.

    A ``fail`` or ``partial`` verdict is a successful experiment, not an
    environment error, so it returns 0. Missing prerequisites exit with 2 before
    a verdict exists; that is the only non-zero path the operator must act on.
    """
    return 0
