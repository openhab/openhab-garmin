"""Guards for the visual-truth split and the golden-write policy.

The app runs end to end on the guest, but this simulator build under Wine
cannot paint app text. A screenshot captured there is a crashed render, so it
must never become a golden. These tests fail if that guard is removed, if a
missing golden stops naming the Windows requirement, or if the documentation
stops saying which host verifies what.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import ClassVar, List

import pytest

from e2e import engine
from e2e.assertions import AssertionFailure, assert_golden
from e2e.cli import main
from e2e.config import default_device_id, find_device
from e2e.scenarios import find_scenario, parse_steps
from e2e.settings import load_settings
from e2e.sim import DRIVER_CLASSES, DRIVER_NAMES, GOLDEN_WRITE_REFUSED, NullDriver
from e2e.sim.base import golden_write_refusal
from e2e.sim.null import png_bytes

README = Path(__file__).resolve().parents[2] / "README.md"
DOCS = Path(__file__).resolve().parents[2] / "docs"


def _flat(path: Path) -> str:
    """Document text with Markdown quoting and line wrapping removed."""
    text = path.read_text(encoding="utf-8").replace(">", " ")
    return " ".join(text.split())


def _missing(phrases: tuple, text: str) -> List[str]:
    return [phrase for phrase in phrases if phrase not in text]


# ---------------------------------------------------------------------------
# the guard itself
# ---------------------------------------------------------------------------


def test_driver_registry_covers_every_known_name() -> None:
    assert set(DRIVER_CLASSES) == set(DRIVER_NAMES)


def test_wine_driver_is_not_a_golden_source() -> None:
    wine = DRIVER_CLASSES["wine"]
    assert wine.supports_screenshots is True
    assert wine.supports_golden_capture is False
    refusal = golden_write_refusal(wine)
    assert refusal is not None
    assert refusal.startswith(GOLDEN_WRITE_REFUSED)
    assert "Invalid Font Specified" in refusal
    assert "Windows" in refusal


def test_null_driver_is_not_a_golden_source() -> None:
    assert DRIVER_CLASSES["null"].supports_golden_capture is False


def test_engine_refuses_update_goldens_when_the_driver_cannot_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ScreenshotOnlyDriver(NullDriver):
        name = "wine"
        supports_screenshots: ClassVar[bool] = True

    monkeypatch.setattr(
        engine, "create_driver", lambda *args, **kwargs: ScreenshotOnlyDriver()
    )
    result = engine.run_scenario(
        find_scenario("smoke/launch-and-poll"),
        find_device(default_device_id()),
        openhab_url="http://openhab.invalid:8080",
        shim_url="http://shim.invalid:8080",
        artifacts_dir=tmp_path / "artifacts",
        goldens_dir=tmp_path / "goldens",
        driver_name="wine",
        update_goldens=True,
    )
    assert result.status == "failed"
    assert any(GOLDEN_WRITE_REFUSED in failure for failure in result.failures)
    assert not (tmp_path / "goldens").exists(), "a refused run must write no golden"
    assert result.steps == [], "the refusal must happen before any step runs"


def test_engine_writes_a_golden_when_the_driver_may_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard refuses the wrong driver, not golden capture in general."""

    class GoldenCapableDriver(NullDriver):
        name = "wine"
        supports_screenshots: ClassVar[bool] = True
        supports_golden_capture: ClassVar[bool] = True

    class Harness(engine.ScenarioRunner):
        def _preflight(self) -> None:
            return None

        def _prepare_stack(self):
            return load_settings(self.scenario.fixture)

    device = find_device(default_device_id())
    scenario = replace(
        find_scenario("smoke/launch-and-poll"),
        steps=parse_steps(
            [
                {"action": "launch"},
                {"action": "screenshot", "name": "homepage"},
                {"action": "assert", "golden": {"name": "homepage"}},
            ],
            "golden-policy-test",
        ),
    )
    monkeypatch.setattr(
        engine, "create_driver", lambda *args, **kwargs: GoldenCapableDriver()
    )
    result = Harness(
        scenario,
        device,
        openhab_url="http://openhab.invalid:8080",
        shim_url="http://shim.invalid:8080",
        artifacts_dir=tmp_path / "artifacts",
        goldens_dir=tmp_path / "goldens",
        driver_name="wine",
        update_goldens=True,
    ).run()

    assert result.status == "passed", result.failures
    golden = tmp_path / "goldens" / device.id / "homepage.png"
    assert golden.is_file()


def test_missing_golden_names_the_windows_requirement(tmp_path: Path) -> None:
    actual = tmp_path / "actual.png"
    actual.write_bytes(png_bytes(2, 2))
    with pytest.raises(AssertionFailure) as error:
        assert_golden(actual, tmp_path / "missing.png")
    message = " ".join(str(error.value).split())
    assert "no golden image at" in message
    assert "Windows host" in message
    assert "tests/docs/visual-truth.md" in message


def test_cli_refuses_update_goldens_on_the_wine_driver(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "run",
            "--all",
            "--driver",
            "wine",
            "--update-goldens",
            "--artifacts",
            str(tmp_path),
        ]
    )
    assert code == 2
    stderr = " ".join(capsys.readouterr().err.split())
    assert GOLDEN_WRITE_REFUSED in stderr
    assert "Invalid Font Specified" in stderr
    assert "Windows" in stderr
    assert not (tmp_path / "goldens").exists()


def test_cli_refuses_update_goldens_on_a_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["run", "--all", "--dry-run", "--update-goldens", "--artifacts", str(tmp_path)])
    assert code == 2
    assert GOLDEN_WRITE_REFUSED in capsys.readouterr().err


# ---------------------------------------------------------------------------
# the documentation must keep stating the split
# ---------------------------------------------------------------------------

README_PHRASES = (
    "The verification split",
    "Verified automatically on the Linux host",
    "Verified only on Windows",
    "does not claim pixel parity",
    "A golden must come from a Windows run",
    "Invalid Font Specified",
    "an older simulator build under Wine is untested",
)

COVERAGE_PHRASES = (
    "Verified automatically on the Linux host",
    "Verified only on Windows",
    "does not claim pixel parity",
    "a golden must come from a Windows run",
)

VISUAL_TRUTH_PHRASES = (
    "Revision note (2026-11-26, visual truth)",
    "this simulator build, under this Wine, cannot render app text",
    "Windows-version emulation",
    "WINEDEBUG=+font,+gdiplus",
    "older `simulator.exe`",
)

FONT_RECORD_PHRASES = (
    "### Invalid Font Specified under Wine",
    "Windows-version emulation — negative",
    "Wine font resolution — negative",
    "An older `simulator.exe` — OPEN",
)


def test_readme_states_the_verification_split() -> None:
    text = _flat(README)
    missing = _missing(README_PHRASES, text)
    assert not missing, f"tests/README.md no longer states the split; missing: {missing}"


def test_coverage_document_states_the_verification_split() -> None:
    text = _flat(DOCS / "coverage.md")
    missing = _missing(COVERAGE_PHRASES, text)
    assert not missing, f"tests/docs/coverage.md no longer states the split; missing: {missing}"


def test_visual_truth_document_records_the_font_findings() -> None:
    text = _flat(DOCS / "visual-truth.md")
    missing = _missing(VISUAL_TRUTH_PHRASES, text)
    assert not missing, (
        f"tests/docs/visual-truth.md lost the marked revision; missing: {missing}"
    )


def test_simulator_driver_document_records_the_font_experiments() -> None:
    text = _flat(DOCS / "simulator-driver.md")
    missing = _missing(FONT_RECORD_PHRASES, text)
    assert not missing, (
        "tests/docs/simulator-driver.md must record both negative font experiments "
        f"and the open one; missing: {missing}"
    )
