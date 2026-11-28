"""The simulator's own menu bar: the measured model, the key plan, and probing."""

from __future__ import annotations

import pytest

from e2e.sim.menus import (
    CONNECTION_MENU_IDS,
    CONNECTION_STATES,
    CONNECTION_TYPES,
    MENU_BAR_HEIGHT,
    MENU_PROBE_STEP,
    MenuProbeReport,
    MenuProbeStep,
    MenuSelection,
    connection_menu_selections,
    menu_bar_points,
    menu_item_index,
    menu_key_plan,
    menu_mnemonic,
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
    assert "inconclusive, not proof" in silent.verdict()
    assert "native driver" in silent.verdict()

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


def test_the_measured_connection_model_is_recorded_in_menu_order() -> None:
    assert CONNECTION_TYPES == ("BLE", "WiFi", "LTE")
    assert CONNECTION_STATES == ("Not Initialized", "Not Connected", "Connected")
    # The ids are the evidence that the model was measured, not invented.
    assert ("BLE", "Not Connected", 6052) in CONNECTION_MENU_IDS
    assert ("WiFi", "Connected", 6056) in CONNECTION_MENU_IDS


def test_each_mode_selects_its_end_state_wifi_first() -> None:
    ble = connection_menu_selections("ble")
    assert [selection.path for selection in ble] == [
        ("Settings", "Connection Type", "WiFi", "Not Connected"),
        ("Settings", "Connection Type", "BLE", "Connected"),
    ]

    wifi = connection_menu_selections("wifi")
    assert [selection.path for selection in wifi] == [
        ("Settings", "Connection Type", "WiFi", "Connected"),
        ("Settings", "Connection Type", "BLE", "Not Connected"),
    ]

    offline = connection_menu_selections("offline")
    assert [selection.path for selection in offline] == [
        # Not initialised, not merely disconnected: hasWifiCapability() is false
        # then and the app goes straight to OFFLINE.
        ("Settings", "Connection Type", "WiFi", "Not Initialized"),
        ("Settings", "Connection Type", "BLE", "Not Connected"),
    ]


def test_an_unknown_connection_mode_is_refused() -> None:
    with pytest.raises(ValueError):
        connection_menu_selections("lte")


def test_the_key_plan_is_a_mnemonic_then_arrows_with_no_coordinates() -> None:
    connected = MenuSelection(("Settings", "Connection Type", "BLE", "Connected"))
    assert menu_key_plan(connected) == (
        "alt+s",
        "Down",
        "Down",
        "Down",
        "Right",
        "Right",
        "Down",
        "Down",
        "Return",
    )

    not_connected = MenuSelection(("Settings", "Connection Type", "WiFi", "Not Connected"))
    assert menu_key_plan(not_connected) == (
        "alt+s",
        "Down",
        "Down",
        "Down",
        "Right",
        "Down",
        "Right",
        "Down",
        "Return",
    )
    allowed = {"Down", "Right", "Return"}
    assert {key for key in menu_key_plan(not_connected) if not key.startswith("alt+")} <= allowed


def test_the_key_plan_starts_at_the_settings_menu_only() -> None:
    with pytest.raises(ValueError):
        menu_key_plan(MenuSelection(("Simulation", "Time Simulation")))


def test_menu_item_index_refuses_to_guess() -> None:
    assert menu_item_index(("Settings", "Connection Type")) == 3
    assert menu_item_index(("Settings", "Connection Type", "WiFi")) == 1
    assert menu_item_index(("Settings", "Connection Type", "LTE", "Connected")) == 2
    for path in (
        ("Settings", "Language"),
        ("Settings", "Connection Type", "Satellite"),
        ("Settings", "Connection Type", "BLE", "Maybe"),
        ("Settings",),
    ):
        with pytest.raises(ValueError):
            menu_item_index(path)


def test_menu_mnemonic_reads_the_ampersand() -> None:
    assert menu_mnemonic("&Settings") == "s"
    assert menu_mnemonic("Si&mulation") == "m"
    with pytest.raises(ValueError):
        menu_mnemonic("Settings")
