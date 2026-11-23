"""Write the machine-readable and human-readable spike verdict.

``verdict.json`` is the contract with anyone who wants to re-check the result:
it carries every check, its evidence paths and the measured values.
``verdict.md`` is the text to paste into issue #370.

Both files are written even for a ``partial`` or ``fail`` verdict: the verdict
is the deliverable, not an error.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .criteria import Verdict
from .model import NOT_MET, SKIPPED, Check, ProbeDocument

REPORT_JSON = "verdict.json"
REPORT_MD = "verdict.md"

ISSUE_URL = "https://github.com/openhab/openhab-garmin/issues/370"


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def _cell(text: str) -> str:
    """Make a string safe inside one Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _evidence(checks: Iterable[Check]) -> List[str]:
    seen: List[str] = []
    for check in checks:
        for path in check.evidence:
            if path not in seen:
                seen.append(path)
    return seen


def build_document(
    verdict: Verdict,
    probe: ProbeDocument,
    *,
    generated_at: float,
    artifacts_root: Path,
) -> Dict[str, Any]:
    """Assemble the JSON verdict document."""
    document: Dict[str, Any] = {
        "issue": 370,
        "issue_url": ISSUE_URL,
        "generated_at": _iso(generated_at),
        "artifacts_root": str(artifacts_root),
        "verdict": verdict.verdict,
        "recommendation": verdict.recommendation,
        "criteria": [criterion.to_document() for criterion in verdict.criteria],
        "checks": [check.to_document() for check in verdict.checks],
        "unknown_checks": list(verdict.unknown_checks),
        "probe": probe.to_document(),
    }
    return document


def write_json(document: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def _criterion_section(criterion: Any) -> List[str]:
    lines = [f"### {criterion.id} -- {_cell(criterion.title)}", ""]
    for check in criterion.checks:
        lines.append(f"- `{check.id}` **{check.status}**")
        if check.detail:
            lines.append(f"  - {_cell(check.detail)}")
        if check.evidence:
            paths = ", ".join(f"`{path}`" for path in check.evidence)
            lines.append(f"  - evidence: {paths}")
        for key, value in sorted(check.measurements.items()):
            lines.append(f"  - {key}: {value:g}")
    if len(lines) == 2:
        lines.append("- no checks reported")
    lines.append("")
    return lines


def render_markdown(
    verdict: Verdict,
    probe: ProbeDocument,
    *,
    generated_at: float,
    artifacts_root: Path,
) -> str:
    """Render ``verdict.md``; the text is written for issue #370."""
    environment = probe.environment
    lines: List[str] = [
        "# Connect IQ simulator under Wine: spike verdict",
        "",
        f"Generated: {_iso(generated_at)}",
        "",
        f"Issue: [#370]({ISSUE_URL})",
        "",
        f"**Verdict: {verdict.verdict}**",
        "",
        verdict.recommendation,
        "",
        "## Pass criteria",
        "",
        "| # | Pass criterion | Status | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for criterion in verdict.criteria:
        evidence = criterion.evidence()
        evidence_cell = ", ".join(f"`{path}`" for path in evidence) if evidence else "-"
        lines.append(
            f"| {criterion.id} | {_cell(criterion.title)} | {criterion.status} "
            f"| {_cell(evidence_cell)} |"
        )

    lines += ["", "## Measurements", ""]
    measured = [
        (check.id, key, value)
        for check in verdict.checks
        for key, value in sorted(check.measurements.items())
    ]
    if measured:
        lines += ["| Check | Measurement | Value |", "| --- | --- | --- |"]
        for check_id, key, value in measured:
            lines.append(f"| `{check_id}` | {_cell(key)} | {value:g} |")
    else:
        lines.append("The probe recorded no numeric measurements.")

    lines += ["", "## What the probe observed", ""]
    for criterion in verdict.criteria:
        lines += _criterion_section(criterion)

    excerpts = environment.get("console_excerpts") or {}
    lines += ["## Console excerpts", ""]
    if isinstance(excerpts, Mapping) and excerpts:
        for name in sorted(excerpts):
            excerpt = excerpts[name] or []
            lines += [f"### {name}", "", "```text"]
            lines += [str(line) for line in excerpt] or ["<no output>"]
            lines += ["```", ""]
    else:
        lines += ["No console output was captured.", ""]

    lines += ["## Environment", "", "| Key | Value |", "| --- | --- |"]
    for key in sorted(environment):
        if key == "console_excerpts":
            continue
        value = environment[key]
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        lines.append(f"| `{key}` | {_cell(str(value))} |")

    skipped = [
        check for check in verdict.checks if check.status == SKIPPED
    ]
    failed = [
        check for check in verdict.checks if check.status == NOT_MET
    ]
    lines += ["", "## Limitations", ""]
    if skipped:
        lines.append("Not evaluable in this environment, so never counted as met:")
        lines.append("")
        for check in skipped:
            detail = f" -- {_cell(check.detail)}" if check.detail else ""
            lines.append(f"- `{check.id}`{detail}")
        lines.append("")
    if failed:
        lines.append("Attempted and not observed:")
        lines.append("")
        for check in failed:
            detail = f" -- {_cell(check.detail)}" if check.detail else ""
            lines.append(f"- `{check.id}`{detail}")
        lines.append("")
    if not skipped and not failed:
        lines += ["Every required check was met; the supporting checks add evidence only.", ""]

    lines += ["## Artifacts", "", f"Everything is under `{artifacts_root}`:", ""]
    relatives = [
        str(path.relative_to(artifacts_root))
        for path in Path(artifacts_root).rglob("*")
        if path.is_file()
    ]
    # The Markdown file is rendered before it is written, so name both verdict
    # files explicitly instead of listing only the JSON that exists by now.
    for name in (REPORT_JSON, REPORT_MD):
        if name not in relatives:
            relatives.append(name)
    for relative in sorted(relatives):
        lines.append(f"- `{relative}`")

    return "\n".join(lines)


def write_verdict(
    verdict: Verdict,
    probe: ProbeDocument,
    *,
    artifacts_root: Path,
    generated_at: float,
) -> Dict[str, Path]:
    """Write both verdict files and return their paths."""
    artifacts_root = Path(artifacts_root)
    document = build_document(
        verdict,
        probe,
        generated_at=generated_at,
        artifacts_root=artifacts_root,
    )
    json_path = write_json(document, artifacts_root / REPORT_JSON)
    markdown = render_markdown(
        verdict,
        probe,
        generated_at=generated_at,
        artifacts_root=artifacts_root,
    )
    markdown_path = artifacts_root / REPORT_MD
    markdown_path.write_text(markdown.rstrip("\n") + "\n", encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def summarize(verdict: Verdict) -> str:
    """One-line status counts, used by the CLI."""
    counts: Dict[str, int] = {}
    for criterion in verdict.criteria:
        counts[criterion.status] = counts.get(criterion.status, 0) + 1
    parts = [f"{count} {status}" for status, count in sorted(counts.items())]
    return ", ".join(parts) or "no criteria"


def evidence_paths(verdict: Verdict) -> Sequence[str]:
    """Every evidence path mentioned by any criterion, deduplicated."""
    return _evidence(
        check for criterion in verdict.criteria for check in criterion.checks
    )
