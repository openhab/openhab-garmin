"""Guards for the verification split and the golden-capture rule.

A golden is a drift detector: a match says the app still draws what a reviewer
once approved **on that simulator build**, not that it looks right on a device.
The split between what the Linux guest verifies and what only eyes on Windows or
hardware can verify lives in prose, so it needs a test that fails when the prose
disappears.

The golden gate belongs to the font pack, not to a driver name: both simulators
paint once the device fonts are in place, and without them the app crashes on
its first draw. These tests pin that rule so the earlier driver-name guard
cannot quietly come back.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from e2e import cli
from e2e.sim import NullDriver, SimulatorDriver, WineSimulatorDriver

README = Path(__file__).resolve().parents[2] / "README.md"
DOCS = Path(__file__).resolve().parents[2] / "docs"


def _flat(path: Path) -> str:
    """Document text with Markdown quoting and line wrapping removed."""
    text = path.read_text(encoding="utf-8").replace(">", " ")
    return " ".join(text.split())


def _missing(phrases: tuple, text: str) -> List[str]:
    return [phrase for phrase in phrases if phrase not in text]


def _font_pack(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "FNT_TEST_0.cft").write_text("face", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# the golden gate is the font pack, not the driver name
# ---------------------------------------------------------------------------


def test_golden_capture_is_not_a_driver_capability() -> None:
    """Both simulators paint with fonts in place, so no driver may be refused.

    A ``supports_golden_capture`` flag is how the earlier policy encoded
    "Wine cannot capture"; it is wrong now that the font pack makes a guest
    capture valid, so its return should fail here.
    """
    for driver in (SimulatorDriver, WineSimulatorDriver, NullDriver):
        assert not hasattr(driver, "supports_golden_capture"), driver.__name__


@pytest.mark.parametrize("driver", ["wine", "null"])
def test_update_goldens_reaches_the_sdk_stage_with_fonts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    driver: str,
) -> None:
    """The gate may not depend on the driver: with a pack, both proceed."""
    monkeypatch.setenv("GARMIN_FONTS_DIR", str(_font_pack(tmp_path / "fonts")))
    calls: List[str] = []

    def stub_layout():
        calls.append("layout")
        raise cli.SdkError("sdk stub for the test")

    monkeypatch.setattr(cli, "load_layout", stub_layout)

    code = cli.main(
        ["run", "smoke/launch-and-poll", "--driver", driver, "--update-goldens"]
    )

    captured = capsys.readouterr()
    assert calls == ["layout"], "the run must reach the SDK stage, so the font gate passed"
    assert "no Connect IQ device fonts" not in captured.err
    assert "sdk stub for the test" in captured.err
    assert code == cli.EXIT_CONFIG


@pytest.mark.parametrize("driver", ["wine", "null"])
def test_update_goldens_is_refused_without_fonts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    driver: str,
) -> None:
    """A capture from a crashed render must never become the baseline."""
    absent = tmp_path / "absent"
    monkeypatch.setenv("GARMIN_FONTS_DIR", str(absent))

    code = cli.main(
        ["run", "smoke/launch-and-poll", "--driver", driver, "--update-goldens"]
    )

    captured = capsys.readouterr()
    assert code == cli.EXIT_CONFIG
    assert "no Connect IQ device fonts" in captured.err
    assert str(absent) in captured.err
    assert "Invalid Font Specified" in captured.err


# ---------------------------------------------------------------------------
# the documentation must keep stating the split
# ---------------------------------------------------------------------------

README_PHRASES = (
    "The verification split",
    "Verified automatically on the Linux guest",
    "Verified only by eye, on Windows or on hardware",
    "does not claim pixel parity",
    "A golden is a drift detector, not a correctness proof",
    "not changed on this simulator build",
)

COVERAGE_PHRASES = (
    "What a green run proves",
    "pixel parity",
    "does not prove the app looks right on a device",
)

SCENARIOS_PHRASES = (
    "drift detector, not a correctness proof",
    "one simulator build",
)


def test_readme_states_the_verification_split() -> None:
    text = _flat(README)
    missing = _missing(README_PHRASES, text)
    assert not missing, f"tests/README.md no longer states the split; missing: {missing}"


def test_coverage_document_states_the_verification_split() -> None:
    text = _flat(DOCS / "coverage.md")
    missing = _missing(COVERAGE_PHRASES, text)
    assert not missing, f"tests/docs/coverage.md no longer states the split; missing: {missing}"


def test_scenario_docs_state_what_a_golden_proves() -> None:
    text = _flat(DOCS / "scenarios.md")
    missing = _missing(SCENARIOS_PHRASES, text)
    assert not missing, (
        f"tests/docs/scenarios.md no longer says what a golden proves; missing: {missing}"
    )
