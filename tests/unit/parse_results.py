#!/usr/bin/env python3
"""Classify the Run No Evil RESULTS block printed by ``monkeydo -t``.

The SDK does not document an exit code for a failing test, so the console
output decides. The documented shape of a passing run is::

    ------------------------------------------------------------------------------
    Executing test homepageTitleAndWidgetTypes...
    DEBUG (14:16): homepage title=My Home widgets=5
    PASS
    ==============================================================================
    RESULTS
    Test:              Status:
    homepageTitleAndWidgetTypes   PASS
    Ran 1 test
    PASSED (failures=0, errors=0)

A failing assertion is reported as ``FAIL`` (the function returned false) or
``ERROR`` (an exception, including ``Test.assert*``). Both must fail the run.

Usage: ``parse_results.py <monkeydo log>``. Prints one line per test and exits
0 when the run passed, 1 when it failed or could not be classified.
"""

from __future__ import annotations

import re
import sys

TEST_LINE = re.compile(r"^(?P<name>\S+)\s+(?P<status>PASS|FAIL|ERROR)$", re.IGNORECASE)
SUMMARY_LINE = re.compile(r"^(PASSED|FAILED)\b", re.IGNORECASE)
RAN_LINE = re.compile(r"^Ran\s+(?P<count>\d+)", re.IGNORECASE)


def classify(text: str) -> tuple[bool, list[str]]:
    """Return ``(passed, report lines)`` for a captured monkeydo log."""
    lines = [line.rstrip() for line in text.splitlines()]

    results_at = [index for index, line in enumerate(lines) if line.strip() == "RESULTS"]
    if not results_at:
        return False, ["FAILED: the log has no RESULTS block; the tests did not run."]

    statuses: list[tuple[str, str]] = []
    ran: int | None = None
    summary: str | None = None
    for line in lines[results_at[-1] + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Ran "):
            match = RAN_LINE.match(stripped)
            if match:
                ran = int(match.group("count"))
            continue
        if SUMMARY_LINE.match(stripped):
            summary = stripped
            continue
        if stripped.startswith("Test:") or stripped == "Status:":
            continue
        match = TEST_LINE.match(stripped)
        if match:
            statuses.append((match.group("name"), match.group("status").upper()))

    report = [f"  {status:5} {name}" for name, status in statuses]
    if ran is not None:
        report.append(f"  Ran {ran} test{'' if ran == 1 else 's'}")

    if summary is None:
        report.append("FAILED: no PASSED/FAILED summary line; the run could not be classified.")
        return False, report
    if not summary.upper().startswith("PASSED"):
        report.append(f"FAILED: {summary}")
        return False, report

    failed = [name for name, status in statuses if status != "PASS"]
    if failed:
        report.append(f"FAILED: {len(failed)} test(s) did not pass: {', '.join(failed)}")
        return False, report
    if ran == 0:
        report.append("FAILED: no tests were executed.")
        return False, report

    report.append(f"PASSED: {summary}")
    return True, report


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: parse_results.py <monkeydo log>", file=sys.stderr)
        return 2
    try:
        # utf-8-sig: a redirected Windows console log may carry a BOM.
        text = open(argv[1], encoding="utf-8-sig", errors="replace").read()
    except OSError as error:
        print(f"FAILED: cannot read the log: {error}")
        return 1

    passed, report = classify(text)
    for line in report:
        print(line)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
