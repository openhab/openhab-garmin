"""The ``without-glance`` build profile and its documentation guard (#396).

The app crashes the simulator on its first glance draw, so scenario runs stub
the glance view instead of compiling it. These cases pin the parts a future
change could silently undo:

- the CLI default excludes the glance, and ``--with-glance`` re-includes it;
- the generated jungle really rebases onto the stubbed source copy, and the
  checkout is not modified;
- the stub keeps drawing nothing (no text, no font);
- the reviewed documents keep claiming the exclusion and linking #396.

They need no SDK, Docker or simulator.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e import build as build_module
from e2e.build import (
    GLANCE_STUB,
    GLANCE_VIEW,
    PROFILE_WATCH_APP,
    PROFILE_WITH_GLANCE,
    PROFILE_WITHOUT_GLANCE,
    BuildError,
    build_prg,
)
from e2e.cli import build_parser, main
from e2e.coverage import build_report
from e2e.paths import repo_root, tests_root as _tests_root
from e2e.scope import load_scope
from e2e.sdk import SdkLayout
from e2e.variants import parse_jungle


class _Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def _fake_monkeyc(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    def fake_run(command, **kwargs):  # noqa: ANN001 - subprocess signature
        captured["command"] = list(command)
        output = Path(command[command.index("-o") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"PRG")
        return _Completed()

    monkeypatch.setattr(build_module.subprocess, "run", fake_run)


def _layout(tmp_path: Path) -> SdkLayout:
    monkeyc = tmp_path / "sdk" / "linux" / "bin" / "monkeyc"
    monkeyc.parent.mkdir(parents=True)
    monkeyc.write_text("#!/bin/sh\n", encoding="utf-8")
    return SdkLayout(
        root=tmp_path / "sdk",
        version="9.3.0",
        version_file=tmp_path / "sdk" / "version.txt",
        monkeyc=monkeyc,
        monkeydo=None,
        simulator=None,
    )


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: dict, **kwargs):
    _fake_monkeyc(monkeypatch, captured)
    return build_prg(_layout(tmp_path), "fenix847mm", tmp_path / "out", **kwargs)


# ---------------------------------------------------------------------------
# the CLI default
# ---------------------------------------------------------------------------


def test_scenarios_and_builds_exclude_the_glance_by_default() -> None:
    parser = build_parser()

    run = parser.parse_args(["run", "--all", "--dry-run"])
    assert run.build_profile == PROFILE_WATCH_APP, (
        "a run must not compile the crashing glance by default, and must not "
        "start the widget in glance mode"
    )

    build = parser.parse_args(["build"])
    assert build.build_profile == PROFILE_WATCH_APP, "a build must match the run profile"


def test_the_profile_can_be_turned_around_both_ways() -> None:
    parser = build_parser()

    assert parser.parse_args(["run", "--all", "--with-glance"]).build_profile == (
        PROFILE_WITH_GLANCE
    )
    assert parser.parse_args(["run", "--all", "--without-glance"]).build_profile == (
        PROFILE_WITHOUT_GLANCE
    )
    assert parser.parse_args(["run", "--all", "--watch-app"]).build_profile == (
        PROFILE_WATCH_APP
    )
    assert parser.parse_args(["build", "--with-glance"]).build_profile == (
        PROFILE_WITH_GLANCE
    )
    assert parser.parse_args(["build", "--without-glance"]).build_profile == (
        PROFILE_WITHOUT_GLANCE
    )

    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--all", "--with-glance", "--without-glance"])
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--all", "--watch-app", "--with-glance"])


# ---------------------------------------------------------------------------
# the build
# ---------------------------------------------------------------------------


def test_without_glance_rebases_the_jungle_onto_the_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    result = _build(tmp_path, monkeypatch, captured, profile=PROFILE_WITHOUT_GLANCE)

    overlay = tmp_path / "out" / "source-no-glance"
    stub = overlay / GLANCE_VIEW
    assert result.profile == PROFILE_WITHOUT_GLANCE
    assert result.jungle_generated is True
    assert stub.is_file()
    assert "_textArea" not in stub.read_text(encoding="utf-8")
    assert "Graphics.FONT" not in stub.read_text(encoding="utf-8")
    assert "(:glance)" in stub.read_text(encoding="utf-8")

    properties = parse_jungle(result.jungle.read_text(encoding="utf-8"))
    assert properties["srcBase"].startswith(str(overlay))
    assert properties["srcConfigBase"].startswith(str(overlay))
    assert properties["project.manifest"] == str(repo_root() / "manifest.xml")
    assert properties["resBase"].startswith(str(repo_root() / "resources"))

    assert "-x" not in captured["command"], "monkeyc -x is incompatible with -f"
    assert "-f" in captured["command"]
    assert PROFILE_WITHOUT_GLANCE in result.profile_note


def test_the_checkout_glance_is_not_modified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = (repo_root() / GLANCE_VIEW).read_text(encoding="utf-8")
    _build(tmp_path, monkeypatch, {}, profile=PROFILE_WITHOUT_GLANCE)
    assert (repo_root() / GLANCE_VIEW).read_text(encoding="utf-8") == real


def test_with_glance_compiles_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    result = _build(tmp_path, monkeypatch, captured, profile=PROFILE_WITH_GLANCE)

    assert result.profile == PROFILE_WITH_GLANCE
    assert not (tmp_path / "out" / "source-no-glance").exists()
    properties = parse_jungle(result.jungle.read_text(encoding="utf-8"))
    assert properties["srcBase"].startswith(str(repo_root() / "source"))


def test_without_glance_needs_the_generated_jungle(tmp_path: Path) -> None:
    explicit = tmp_path / "full.jungle"
    explicit.write_text("base.resourcePath = resources\n", encoding="utf-8")

    with pytest.raises(BuildError) as error:
        build_prg(
            _layout(tmp_path),
            "fenix7",
            tmp_path / "out",
            jungle=explicit,
            profile=PROFILE_WITHOUT_GLANCE,
        )

    assert "--jungle" in str(error.value)
    assert "--with-glance" in str(error.value)


# ---------------------------------------------------------------------------
# the printed profile
# ---------------------------------------------------------------------------


def test_a_dry_run_prints_the_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--all", "--dry-run", "--artifacts", str(tmp_path)]) == 0
    output = capsys.readouterr().out

    assert "profile:" in output
    assert PROFILE_WATCH_APP in output
    assert "NOT covered" in output


def test_a_with_glance_dry_run_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "run",
                "smoke/launch-and-poll",
                "--dry-run",
                "--with-glance",
                "--artifacts",
                str(tmp_path),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert PROFILE_WITH_GLANCE in output
    assert PROFILE_WITHOUT_GLANCE not in output


# ---------------------------------------------------------------------------
# guards against silent regression
# ---------------------------------------------------------------------------


def test_the_real_glance_view_is_still_the_annotated_file() -> None:
    """If the app moves or renames the view, the stub would replace nothing."""
    source = (repo_root() / GLANCE_VIEW).read_text(encoding="utf-8")
    assert "(:glance)" in source
    assert "_textArea.draw" in source


def test_the_stub_draws_nothing() -> None:
    assert "TextArea" not in GLANCE_STUB
    assert ".draw(" not in GLANCE_STUB
    assert "setColor" not in GLANCE_STUB
    assert "FONT_" not in GLANCE_STUB


def test_coverage_marks_the_glance_entries_blocked() -> None:
    scope = load_scope()
    blocked = {entry.id: entry for entry in scope.blocked}

    assert blocked["lifecycle.glance-widget"].issue == 396
    assert "watch-app" in blocked["lifecycle.glance-widget"].reason
    assert "setGlance" in blocked["lifecycle.glance-widget"].reason
    assert blocked["devices.capabilities"].issue == 396

    report = build_report()
    assert report.problems == ()
    assert {entry.entry.id for entry in report.blocked} == {
        "lifecycle.glance-widget",
        "devices.capabilities",
    }


def test_the_documents_keep_claiming_the_exclusion() -> None:
    coverage = (_tests_root() / "docs" / "coverage.md").read_text(encoding="utf-8")
    assert "Blocked by a tracked issue" in coverage
    assert "`lifecycle.glance-widget`" in coverage
    assert "#396" in coverage
    assert "watch-app" in coverage

    for name in ("scenarios.md", "simulator-driver.md"):
        text = (_tests_root() / "docs" / name).read_text(encoding="utf-8")
        assert "without-glance" in text, name
        assert "watch-app" in text, name
        assert "--with-glance" in text, name
        assert "#396" in text, name

    readme = (_tests_root() / "README.md").read_text(encoding="utf-8")
    assert "without-glance" in readme
    assert "watch-app" in readme
    assert "--with-glance" in readme
    assert "#396" in readme
