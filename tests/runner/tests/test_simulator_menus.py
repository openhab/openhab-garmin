"""The simulator's own menu bar: probing it, and the restart fallback."""

from __future__ import annotations

from e2e.sim.menus import (
    MENU_BAR_HEIGHT,
    MENU_PROBE_STEP,
    MenuProbeReport,
    MenuProbeStep,
    menu_bar_points,
    new_window_ids,
    parse_window_ids,
)


def test_menu_bar_points_walk_the_top_row_inside_the_window() -> None:
    points = menu_bar_points(409, 600)
    assert points, "a 409-wide window has menu candidates"
    assert all(0 < x < 409 for x, _ in points)
    assert all(0 < y < MENU_BAR_HEIGHT for _, y in points)
    xs = [x for x, _ in points]
    assert xs == sorted(xs), "left to right"
    assert MENU_PROBE_STEP == xs[1] - xs[0]


def test_menu_bar_points_are_empty_for_a_degenerate_window() -> None:
    assert menu_bar_points(0, 600) == []
    assert menu_bar_points(409, 0) == []
    # A window shorter than the bar still yields the middle of what exists.
    assert menu_bar_points(40, 2) == [(12, 1)]


def test_parse_window_ids_reads_the_xwininfo_tree() -> None:
    text = """
xwininfo: Window id: 0x21f "root"

  Root window id: 0x21f (the root window) (has no name)
  Parent window id: 0x0 (none)
     5 children:
     0x6400003 "CIQ Simulator": ("simulator.exe" "simulator.exe")  409x600+4+30  +4+30
     0x1 "helper": ("x" "x")  1x1+0+0  +0+0
     0x2 (has no name): ()  2x2+0+0  +0+0
"""
    assert parse_window_ids(text) == {104857603, 1, 2}
    assert parse_window_ids("") == set()
    assert parse_window_ids("nothing") == set()


def test_new_window_ids_reports_only_the_appearing_ones() -> None:
    assert new_window_ids([1, 2, 3], [1, 2, 3, 9]) == [9]
    assert new_window_ids([1, 2], [1, 2]) == []
    assert new_window_ids([], [7]) == [7]


def test_a_probe_step_counts_a_new_window_or_a_screen_change_as_something() -> None:
    assert MenuProbeStep(10, 11, screen_delta=0).opened_something is False
    assert MenuProbeStep(10, 11, screen_delta=42).opened_something is True
    assert MenuProbeStep(10, 11, new_windows=(9,)).opened_something is True
    assert "delta=42px" in MenuProbeStep(10, 11, screen_delta=42).describe()
    assert "new windows=9" in MenuProbeStep(10, 11, new_windows=(9,)).describe()


def test_the_probe_verdict_says_what_was_and_was_not_proven() -> None:
    opened = MenuProbeReport(popup_window=9, popup_geometry="120x200 at (+40,+30)")
    assert opened.opened is True
    assert "reachable with synthetic clicks" in opened.verdict()
    assert "9" in opened.verdict()

    silent = MenuProbeReport(steps=[MenuProbeStep(12, 11), MenuProbeStep(36, 11)])
    assert silent.opened is False
    assert "does not take synthetic clicks on its menu bar" in silent.verdict()
    assert "restart-in-mode remains the documented capability" in silent.verdict()

    drawn = MenuProbeReport(steps=[MenuProbeStep(12, 11, screen_delta=800)])
    assert "in-window" in drawn.verdict()
    assert "screenshots" in drawn.verdict()


def test_probe_lines_describe_every_attempt() -> None:
    report = MenuProbeReport(
        steps=[MenuProbeStep(12, 11, screen_delta=0), MenuProbeStep(36, 11, note="x")]
    )
    lines = report.lines()
    assert len(lines) == 2
    assert lines[0].startswith("(12,11)")
    assert "x" in lines[1]
