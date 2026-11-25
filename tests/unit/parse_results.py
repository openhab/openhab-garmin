#!/usr/bin/env python3
"""Classify the Run No Evil RESULTS block printed by ``monkeydo -t``.

``monkeydo`` does not document an exit code for a failing test, and it was
measured on the guest to exit **non-zero on a fully passing run** (18/18 PASS,
exit 1). The console output is therefore the only trustworthy signal, and this
module is the single place that decides.

The documented shape of a passing run is::

    ------------------------------------------------------------------------------
    Executing test ErrorClassificationTests.sizeLimitIsCritical...
    DEBUG (14:16): toast=S:-402 code=-402
    PASS
    ==============================================================================
    RESULTS
    Test:              Status:
    ErrorClassificationTests.sizeLimitIsCritical   PASS
    Ran 1 test
    PASSED (passed=1, failed=0, errors=0)

A failing assertion is reported as ``FAIL`` (the function returned false) or
``ERROR`` (an exception, including ``Test.assert*``). Both must fail the run.

Precedence:

- a ``RESULTS`` block that classifies as a pass wins, whatever the exit status;
- a timeout (``--status 124``) fails, even if the block looks complete, because
  the run did not finish on its own;
- otherwise the run fails, and a non-zero status is added to the report.

The tests execute inside the app process, so the app's ``E2E:`` trace lines
appear in the same log. They are ignored: only the ``RESULTS`` block counts.

Usage: ``parse_results.py [--status N] <monkeydo log>``. Prints one line per
test and exits 0 when the run passed, 1 when it failed or could not be
classified, 2 on a usage error.
"""

from __future__ import annotations

import re
import sys

TEST_LINE = re.compile(r"^(?P<name>\S+)\s+(?P<status>PASS|FAIL|ERROR)$", re.IGNORECASE)
SUMMARY_LINE = re.compile(r"^(PASSED|FAILED)\b", re.IGNORECASE)
RAN_LINE = re.compile(r"^Ran\s+(?P<count>\d+)", re.IGNORECASE)

#: Exit status of ``timeout`` when it had to kill the command.
TIMEOUT_STATUS = 124


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


def evaluate(text: str, status: int = 0) -> tuple[bool, list[str]]:
    """Decide the run from the console output and the exit status together.

    ``status`` is monkeydo's exit code (or 124 from ``timeout``). A passing
    RESULTS block is authoritative: monkeydo exits non-zero on a good run, so
    treating its status as a failure would turn every green run red.
    """
    if status == TIMEOUT_STATUS:
        _, report = classify(text)
        report.append(f"FAILED: monkeydo timed out (exit {status}); the run did not finish.")
        return False, report

    passed, report = classify(text)
    if passed:
        return True, report
    if status != 0:
        report.append(
            f"FAILED: monkeydo exited with code {status} and the output did not "
            "classify as a pass."
        )
    return False, report


def main(argv: list[str]) -> int:
    args = argv[1:]
    status = 0
    if len(args) >= 2 and args[0] == "--status":
        try:
            status = int(args[1])
        except ValueError:
            print("usage: parse_results.py [--status N] <monkeydo log>", file=sys.stderr)
            return 2
        args = args[2:]

    if len(args) != 1:
        print("usage: parse_results.py [--status N] <monkeydo log>", file=sys.stderr)
        return 2

    try:
        # utf-8-sig: a redirected Windows console log may carry a BOM.
        text = open(args[0], encoding="utf-8-sig", errors="replace").read()
    except OSError as error:
        print(f"FAILED: cannot read the log: {error}")
        return 1

    passed, report = evaluate(text, status)
    for line in report:
        print(line)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
