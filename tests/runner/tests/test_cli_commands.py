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
from pathlib import Path
from typing import List

import pytest

from e2e import cli
from e2e.shim import ShimRequest


def _subcommands(parser: argparse.ArgumentParser) -> List[str]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    return []


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
