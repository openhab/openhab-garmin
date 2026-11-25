"""Result classification for the Connect IQ unit-test runner (issue #140).

``tests/unit/parse_results.py`` decides whether a ``monkeydo -t`` run passed.
The regression these tests guard is the one the first guest run exposed:
``monkeydo`` exits **non-zero on a fully passing run**, so a runner that trusts
the exit status turns every green run red. The RESULTS block must win whenever
it classifies, and the exit status may only be reported as an additional
failure when it cannot.

The passing log is the one captured on the guest (fenix7, 18/18 PASS, exit 1),
verbatim except for the test list, which is the pilot's real list.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from e2e.paths import repo_root

PARSER_PATH = repo_root() / "tests" / "unit" / "parse_results.py"

PASSING_LOG = """\
Device Version 0.1.0
Device id 1 name "fenix7"
Shell Version 0.1.0
E2E:LANGUAGE;code=deu
E2E:VIEW;category=error;view=ErrorView
Executing test ErrorClassificationTests.sizeLimitIsCritical...
DEBUG (14:16): toast=S:-402 code=-402
PASS
Executing test SitemapModelTests.homepageTitleAndWidgetTypes...
DEBUG (14:16): homepage title=My Home widgets=5
PASS
==============================================================================
RESULTS
Test:                                                            Status:
ErrorClassificationTests.sizeLimitIsCritical                     PASS
ErrorClassificationTests.otherCriticalCodesRequireFullScreen     PASS
ErrorClassificationTests.noPhoneErrorsAreDetectedAndTemporary    PASS
ErrorClassificationTests.sourcePrefixSeparatesSitemapFromCommand PASS
ErrorClassificationTests.emptyResponseSuppression                PASS
SitemapModelTests.homepageTitleAndWidgetTypes                     PASS
SitemapModelTests.labelStateIsSplitFromLabel                     PASS
SitemapModelTests.framesAndLinkedPagesAreBuilt                   PASS
SitemapModelTests.unsupportedElementTypeIsRejected               PASS
SitemapModelTests.emptyHomepageIsRejected                        PASS
SitemapModelTests.itemTypeStateAndUnitRules                      PASS
SitemapParameterTests.numericDefaultsAndRanges                   PASS
SitemapParameterTests.invalidNumericParametersAreRejected        PASS
SitemapParameterTests.releaseOnlyDefaults                        PASS
SitemapParameterTests.numericDisplayStateUsesValueAndUnit        PASS
SitemapParameterTests.switchMappingsAreNormalized                PASS
SitemapParameterTests.switchDisplayStatePrefersWidgetMappings    PASS
SitemapParameterTests.colorsAreParsed                            PASS
Ran 18 tests

PASSED (passed=18, failed=0, errors=0)
Connection Finished
Closing shell and port
"""

FAILING_LOG = """\
Executing test aTestOfAssert...
Unhandled Exception
ASSERTION FAILED
aTestOfAssert in C:\\workspace\\RunNoEvil\\source\\RunNoEvil.mc:21
ERROR
==============================================================================
RESULTS
Test:                                Status:
aDebugTest                           PASS
aTestOfAssert                        ERROR
Ran 2 tests
FAILED (passed=1, failed=0, errors=1)
"""

ZERO_TESTS_LOG = """\
RESULTS
Test:    Status:
Ran 0 tests
PASSED (passed=0, failed=0, errors=0)
"""


def _parser() -> ModuleType:
    spec = importlib.util.spec_from_file_location("unit_parse_results", PARSER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def parser() -> ModuleType:
    return _parser()


def test_passing_run_with_nonzero_status_passes(parser: ModuleType) -> None:
    """The regression: exit 1 + a clean RESULTS block is a pass."""
    passed, report = parser.evaluate(PASSING_LOG, status=1)
    assert passed is True, report
    assert any(line.startswith("PASSED:") for line in report)


def test_passing_run_with_zero_status_passes(parser: ModuleType) -> None:
    passed, report = parser.evaluate(PASSING_LOG, status=0)
    assert passed is True, report


def test_e2e_trace_lines_are_ignored(parser: ModuleType) -> None:
    """The tests run inside the app, so E2E: lines share the log."""
    passed, report = parser.evaluate(PASSING_LOG, status=1)
    assert passed is True
    assert not any("E2E:" in line for line in report)


def test_failed_summary_fails_even_with_zero_status(parser: ModuleType) -> None:
    passed, report = parser.evaluate(FAILING_LOG, status=0)
    assert passed is False
    assert any("FAILED" in line for line in report)
    assert any("aTestOfAssert" in line for line in report)


def test_error_test_fails_with_nonzero_status(parser: ModuleType) -> None:
    passed, _ = parser.evaluate(FAILING_LOG, status=1)
    assert passed is False


def test_missing_results_block_fails_and_reports_status(parser: ModuleType) -> None:
    passed, report = parser.evaluate("no results here\n", status=1)
    assert passed is False
    assert any("no RESULTS block" in line for line in report)
    assert any("exited with code 1" in line for line in report)


def test_timeout_fails_even_with_a_passing_block(parser: ModuleType) -> None:
    passed, report = parser.evaluate(PASSING_LOG, status=124)
    assert passed is False
    assert any("timed out" in line for line in report)


def test_zero_tests_fails(parser: ModuleType) -> None:
    passed, report = parser.evaluate(ZERO_TESTS_LOG, status=0)
    assert passed is False
    assert any("no tests were executed" in line for line in report)


def test_status_is_case_insensitive(parser: ModuleType) -> None:
    """The SDK docs print ``Pass``; the RESULTS table must accept it."""
    text = "RESULTS\nTest:    Status:\nx        Pass\nRan 1 test\nPASSED (failures=0, errors=0)\n"
    passed, report = parser.evaluate(text, status=1)
    assert passed is True, report


def test_runner_does_not_override_the_parser(parser: ModuleType) -> None:
    """The shell must pass the status to the parser, not act on it itself.

    This is the seam the first guest run exposed: the script classified a
    passing run correctly and then turned it into a failure because monkeydo
    exited non-zero. Keep the decision in one place.
    """
    script = (repo_root() / "tests" / "unit" / "run").read_text(encoding="utf-8")
    assert 'parse_results.py" --status "$monkeydo_status"' in script
    assert 'monkeydo_status" != "0"' not in script


def test_command_line_trusts_the_block_over_the_status(parser: ModuleType, tmp_path: Path) -> None:
    log = tmp_path / "monkeydo.log"
    log.write_text(PASSING_LOG, encoding="utf-8")
    assert parser.main(["parse_results.py", "--status", "1", str(log)]) == 0


def test_command_line_fails_without_a_block(parser: ModuleType, tmp_path: Path) -> None:
    log = tmp_path / "monkeydo.log"
    log.write_text("nothing useful\n", encoding="utf-8")
    assert parser.main(["parse_results.py", "--status", "1", str(log)]) == 1
