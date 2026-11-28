"""The CLI itself: every subcommand must be invocable, not just importable.

`isolation-check` shipped without its `--shim-url` argument and crashed on its
first real run, because nothing exercised the parser or the handler. These tests
drive `build_parser()`/`main()` the way an operator does — `--help` for every
subcommand, the promised arguments for `isolation-check`, and one end-to-end
handler run with a fake driver and shim — so a missing argument fails here
instead of on the guest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

import pytest

from e2e import cli
from e2e import fixtures
from e2e.openhab import OpenHabClient
from e2e.shim import ShimRequest


def _subcommands(parser: argparse.ArgumentParser) -> List[str]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    return []


def _subparser(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[name]
    raise AssertionError(f"no subcommands in {parser.prog}")


def test_a_container_run_can_write_a_golden_somewhere_writable() -> None:
    """`tests/goldens` is read-only in the runner; `--goldens` is the way out."""
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            "regression/unsupported-widget",
            "--update-goldens",
            "--goldens",
            "/artifacts/goldens",
        ]
    )
    assert args.update_goldens is True
    assert args.goldens == "/artifacts/goldens"

    help_text = " ".join(
        action.help or ""
        for action in _subparser(parser, "run")._actions
        if action.dest in ("update_goldens", "goldens")
    )
    assert "--goldens" in help_text, "the read-only trap must be named where it bites"
    assert "read-only" in help_text.lower()


def test_the_goldens_option_overrides_the_checkout_directory(
    tmp_path: Path,
) -> None:
    args = cli.build_parser().parse_args(
        ["run", "smoke/launch-and-poll", "--goldens", str(tmp_path)]
    )
    assert cli._goldens_dir(args) == tmp_path
    default = cli.build_parser().parse_args(["run", "smoke/launch-and-poll"])
    assert cli._goldens_dir(default).name == "goldens"


def test_every_subcommand_answers_help_through_main() -> None:
    names = _subcommands(cli.build_parser())
    assert len(names) >= 15, names
    assert "isolation-check" in names
    for name in names:
        with pytest.raises(SystemExit) as exit_info:
            cli.main([name, "--help"])
        assert exit_info.value.code == 0, f"`e2e {name} --help` failed"


def test_isolation_check_exposes_the_arguments_its_handler_uses() -> None:
    """The handler reads these names; the parser must define every one."""
    args = cli.build_parser().parse_args(
        ["isolation-check", "--prg", "app.prg", "--shim-url", "http://shim:8080"]
    )
    for attribute in (
        "prg",
        "device",
        "mode",
        "language",
        "settings",
        "timeout",
        "artifacts",
        "shim_url",
        "json",
    ):
        assert hasattr(args, attribute), f"isolation-check is missing --{attribute}"
    assert args.shim_url == "http://shim:8080"


class _Shim:
    """A shim that answers with a different recorded sequence per run."""

    sequences: List[List[ShimRequest]] = []

    def __init__(self, url: str) -> None:
        self.url = url
        self._runs = 0

    def reset(self) -> None:
        # The command resets the trace before each run, so this is the run
        # boundary for the fake as well.
        self._runs += 1

    def requests(self) -> List[ShimRequest]:
        index = min(max(self._runs - 1, 0), len(type(self).sequences) - 1)
        return list(type(self).sequences[index])


class _Driver:
    """A driver that "launches" instantly and reports a sitemap result."""

    sitemap_results: List[str] = ["success", "success"]
    created: List["_Driver"] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._events: List[str] = []
        self._index = len(type(self).created)
        type(self).created.append(self)

    def prepare(self, prg: Path) -> None:
        self.prg = prg

    def start(self, device, settings, mode, language="eng") -> None:  # type: ignore[no-untyped-def]
        index = min(self._index, len(type(self).sitemap_results) - 1)
        self._events = [
            f"E2E:SITEMAP;result={type(self).sitemap_results[index]};code=200;bytes=1",
            "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1",
        ]

    def wait_ready(self, timeout: float) -> None:
        return None

    def events(self) -> List[str]:
        return list(self._events)

    def stop(self) -> None:
        return None


def _run_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sequences: List[List[ShimRequest]],
    sitemap_results: List[str],
) -> int:
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    _Shim.sequences = sequences
    _Driver.sitemap_results = sitemap_results
    _Driver.created = []
    monkeypatch.setattr(cli, "ShimClient", _Shim)
    monkeypatch.setattr(cli, "create_driver", _Driver)
    monkeypatch.setattr(cli, "load_layout", lambda: None)
    return cli.main(
        [
            "isolation-check",
            "--prg",
            str(prg),
            "--device",
            "fenix7",
            "--shim-url",
            "http://shim:8080",
            "--artifacts",
            str(tmp_path / "artifacts"),
        ]
    )


def test_isolation_check_passes_when_both_runs_are_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sequence = [
        ShimRequest(seq=1, method="GET", path="/rest/sitemaps/garmin_e2e"),
        ShimRequest(seq=2, method="GET", path="/rest/sitemaps/garmin_e2e"),
    ]
    code = _run_isolation(
        tmp_path,
        monkeypatch,
        sequences=[sequence, sequence],
        sitemap_results=["success", "success"],
    )
    assert code == 0


def test_isolation_check_fails_when_the_second_run_serves_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sequence = [ShimRequest(seq=1, method="GET", path="/rest/sitemaps/garmin_e2e")]
    code = _run_isolation(
        tmp_path,
        monkeypatch,
        sequences=[sequence, []],
        sitemap_results=["success", "unchanged"],
    )
    assert code == 1


def test_isolation_check_reports_differing_sequences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = [ShimRequest(seq=1, method="GET", path="/rest/sitemaps/garmin_e2e")]
    second = [ShimRequest(seq=1, method="GET", path="/rest/sitemaps/garmin_commands")]
    code = _run_isolation(
        tmp_path,
        monkeypatch,
        sequences=[first, second],
        sitemap_results=["success", "success"],
    )
    assert code == 1


def test_run_accepts_profile_as_an_assertion() -> None:
    """`run --profile dev` must not be an "unrecognized arguments" dead end."""
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--all", "--profile", "dev"])
    assert args.openhab_profile == "dev"
    assert parser.parse_args(["run", "--all"]).openhab_profile is None

    help_text = " ".join(
        action.help or ""
        for action in _subparser(parser, "run")._actions
        if action.dest == "openhab_profile"
    )
    assert "assert" in help_text.lower()
    assert "detect" in help_text.lower(), "the flag must say `run` detects the profile"

    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--all", "--profile", "5.3.0"])


class _StableStack:
    """A stack whose REST API reports the pinned stable version and tree."""

    base_url = "http://localhost:8080/"

    def server_version(self) -> str:
        # Parse the captured-shape document with the real client: a stub that
        # returned the pin directly would not catch a wrong field in
        # OpenHabClient.server_version().
        client = OpenHabClient(self.base_url)
        client._request = lambda *args, **kwargs: (
            200,
            json.dumps(
                {
                    "version": "8",
                    "runtimeInfo": {
                        "version": fixtures.pinned_versions()["stable"]
                    },
                }
            ).encode("utf-8"),
        )
        return client.server_version()

    def items(self):
        return [
            type("Item", (), {"name": name})()
            for name in fixtures.load_expected("stable").items
        ]

    def sitemaps(self):
        return list(fixtures.load_expected("stable").sitemaps)


def test_run_profile_mismatch_fails_with_the_served_profile_and_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "OpenHabClient", lambda url: _StableStack())

    code = cli.main(["run", "--all", "--profile", "dev"])

    captured = capsys.readouterr()
    assert code == 2, "a wrong profile is a configuration error, not a run"
    assert "serves 'stable'" in captured.err
    assert "asked for the 'dev'" in captured.err
    assert "COMPOSE_PROFILES=dev" in captured.err
    assert "restart openhab-dev" in captured.err


def test_an_implicit_wine_run_is_refused_on_a_wine_6_image(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The 22.04 image's Wine cannot push the app; name native immediately.

    Without this, `run` without --driver spends the full readiness budget on a
    failure the image already knows about. An explicit --driver wine is still
    allowed, because reproducing the failure has to stay possible.
    """
    monkeypatch.setattr(cli, "wine_version", lambda: "wine-6.0.3 (Ubuntu 6.0.3~repack-1)")

    code = cli.main(["run", "smoke/launch-and-poll"])

    captured = capsys.readouterr()
    assert code == 2
    assert "cannot push the app" in captured.err
    assert "--driver native" in captured.err
    assert "device area stays black" in captured.err

