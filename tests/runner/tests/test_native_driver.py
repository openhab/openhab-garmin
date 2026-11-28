"""The native driver: registration, paths, the lock sweep and the capability gate.

These tests never launch a simulator. The native build needs a Linux host, a
display and the licensed SDK, so the parts that can be proven here are the pure
and file-system ones; the launch, input and menu paths are contract cases on the
VM (see tests/docs/simulator-driver.md).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from e2e.sdk import SdkLayout
from e2e.sim import DRIVER_NAMES, DriverError, NativeSimulatorDriver, create_driver
from e2e.sim.menus import menu_key_plan


def _layout(tmp_path: Path, *, native: bool = True, monkeydo: bool = True) -> SdkLayout:
    sdk = tmp_path / "sdk"
    binary = sdk / "linux" / "bin" / "simulator"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"\x7fELF placeholder")
    monitor = sdk / "linux" / "bin" / "monkeydo"
    monitor.write_text("#!/bin/sh\n", encoding="utf-8")
    return SdkLayout(
        root=sdk,
        version="9.3.0",
        version_file=sdk / "version.txt",
        monkeyc=sdk / "bin" / "monkeyc",
        monkeydo=monitor if monkeydo else None,
        simulator=None,
        devices=None,
        native_simulator=binary if native else None,
    )


def test_native_is_a_selectable_driver() -> None:
    assert "native" in DRIVER_NAMES
    with pytest.raises(DriverError):
        create_driver("native")


def test_the_native_driver_advertises_the_connection_capability(tmp_path: Path) -> None:
    driver = create_driver("native", layout=_layout(tmp_path))
    assert isinstance(driver, NativeSimulatorDriver)
    assert driver.name == "native"
    assert driver.supports_connection_switch is True
    assert driver.supports_events is True
    assert driver.supports_screenshots is True


def test_availability_needs_the_native_binary(tmp_path: Path) -> None:
    # Without linux/bin/simulator there is nothing to launch, whatever else exists.
    assert NativeSimulatorDriver.is_available(_layout(tmp_path, native=False)) is False


def test_prepare_names_the_missing_monkeydo(tmp_path: Path) -> None:
    prg = tmp_path / "app.prg"
    prg.write_bytes(b"prg")
    driver = create_driver("native", layout=_layout(tmp_path, monkeydo=False))
    with pytest.raises(DriverError) as error:
        driver.prepare(prg)
    assert "monkeydo" in str(error.value)


def test_an_unknown_mode_is_refused_before_any_window_is_needed(tmp_path: Path) -> None:
    driver = create_driver("native", layout=_layout(tmp_path))
    with pytest.raises(DriverError) as error:
        driver.set_connection_mode("lte")
    assert "unknown mode" in str(error.value)


def test_the_environment_carries_the_gtk_requirements(tmp_path: Path) -> None:
    driver = create_driver("native", layout=_layout(tmp_path), display=":99")
    env = driver._env()
    assert env["DISPLAY"] == ":99"
    assert env["XDG_DATA_DIRS"]
    assert env["GTK_THEME"] == "Adwaita"
    assert env["GDK_BACKEND"] == "x11"


def test_the_lock_is_swept_when_its_pid_is_gone(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    lock = tmp_path / "Sim-root"
    # A PID this large cannot be running, so the sweep must remove the stale file
    # rather than assume a live simulator.
    lock.write_text("99999999\n", encoding="utf-8")
    driver = create_driver("native", layout=_layout(tmp_path))
    assert driver.lock_path == lock
    driver._sweep_existing_simulator()
    assert not lock.exists()


def test_a_lock_without_a_pid_is_removed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    lock = tmp_path / "Sim-root"
    lock.write_text("not a pid\n", encoding="utf-8")
    driver = create_driver("native", layout=_layout(tmp_path))
    assert driver._lock_pid() is None
    driver._sweep_existing_simulator()
    assert not lock.exists()


def test_app_data_is_cleared_under_the_measured_tmp_root(
    tmp_path: Path, monkeypatch
) -> None:
    """The native simulator writes /tmp/com.garmin.connectiq, not $HOME.

    Clearing the wrong tree left the sitemap cache in place, so a relaunched app
    answered `unchanged` and the isolation case failed.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    root = tmp_path / "tmp" / "com.garmin.connectiq"
    data = root / "GARMIN" / "APPS" / "DATA"
    data.mkdir(parents=True)
    log = root / "GARMIN" / "APPS" / "LOGS" / "CIQ_LOG.YML"
    log.parent.mkdir(parents=True)
    log.write_text("---\n", encoding="utf-8")

    driver = create_driver("native", layout=_layout(tmp_path))
    assert data in driver._app_data_dirs()
    assert driver._ciq_log_source() == log
    assert all("SETTINGS" not in str(path) and "Settings" not in str(path) for path in driver._app_data_dirs())


def test_the_home_connectiq_layout_is_still_searched(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    data = tmp_path / "home" / ".Garmin" / "ConnectIQ" / "GARMIN" / "APPS" / "DATA"
    data.mkdir(parents=True)

    driver = create_driver("native", layout=_layout(tmp_path))
    assert data in driver._app_data_dirs()


def test_the_native_pointer_guard_accepts_a_point_inside_the_window(
    tmp_path: Path,
) -> None:
    """A GTK top-level reports window 0 under a pointer inside it.

    The rectangle decides for that case; a point outside still fails, so the
    guard keeps its purpose instead of being skipped.
    """
    driver = create_driver("native", layout=_layout(tmp_path))
    driver._geometry = lambda window: {"x": 0, "y": 0, "width": 446, "height": 700}  # type: ignore[assignment]
    driver._root_window_id = lambda: "1"  # type: ignore[assignment]

    driver._verify_pointer_window("2097164", 0, 2, 2)

    driver._window_pid = lambda window: None  # type: ignore[assignment]
    driver._window_inside = lambda outer, inner: False  # type: ignore[assignment]
    with pytest.raises(DriverError):
        driver._verify_pointer_window("2097164", 0, 500, 900)


def test_the_pointer_fallback_is_native_only(tmp_path: Path) -> None:
    assert NativeSimulatorDriver.pointer_top_level_fallback is True
    from e2e.sim.wine import WineSimulatorDriver

    assert WineSimulatorDriver.pointer_top_level_fallback is False


@pytest.mark.skipif(os.name != "posix", reason="the X bit is not meaningful on Windows")
def test_the_simulator_is_made_executable_in_place(tmp_path: Path, monkeypatch) -> None:
    """Never copy the binary: its resources are resolved relative to it.

    The VM proved a lone copy asserts in InitFromImage. The staged SDK directory
    is writable, so the driver only sets the exec bit and returns the same path.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    driver = create_driver("native", layout=_layout(tmp_path))
    binary = driver.layout.native_simulator
    assert binary is not None
    binary.chmod(0o664)
    executable = driver._ensure_executable(binary)
    assert executable == binary
    assert os.access(executable, os.X_OK)
    assert not (driver.artifact_dir / "bin").exists(), "no copy is made"


def test_the_native_failure_hints_do_not_talk_about_a_wine_prefix(tmp_path: Path) -> None:
    driver = create_driver("native", layout=_layout(tmp_path))
    window = driver._stage_hint("simulator-window")
    assert "InitFromImage" in window
    assert "in place" in window
    assert "Wine prefix" not in window

    launch = driver._stage_hint("app-launch")
    assert "no Wine prefix" in launch
    assert "$HOME/.Garmin/ConnectIQ/Devices" in launch

    # The display advice is shared and still applies.
    assert "xdpyinfo" in driver._stage_hint("display")


def test_the_plan_targets_the_menu_for_every_transition() -> None:
    from e2e.sim.menus import connection_menu_selections

    for mode in ("ble", "wifi", "offline"):
        plans = connection_menu_selections(mode)
        assert len(plans) == 2, "Wi-Fi state first, then the BLE state"
        for selection in plans:
            keys = menu_key_plan(selection)
            assert keys[0] == "alt+s"
            assert keys[-1] == "Return"


def test_the_native_launch_tries_the_key_hold_first(tmp_path: Path) -> None:
    """Measured: plain XTEST taps do nothing and the hold opens the app."""
    from e2e.config import find_device

    driver = create_driver("native", layout=_layout(tmp_path))
    plans = driver._launch_plans(find_device("fenix7"))
    assert plans[0][0] == "keyboard keydown/keyup"
    assert {plan[0] for plan in plans} == {
        "keyboard keydown/keyup",
        "declared hit areas (hold)",
        "keyboard taps",
        "declared hit areas",
    }


def test_pixel_dump_parsing_and_canvas_detection() -> None:
    from e2e.sim.native import locate_canvas, parse_pixel_dump

    # 8x6 window, grey background, a 4x3 "canvas" at (2,2) with varied pixels.
    rows = []
    pixels = []
    for y in range(6):
        for x in range(8):
            colour = (10 + x, 20 + y, 30) if 2 <= x < 6 and 2 <= y < 5 else (200, 200, 200)
            pixels.append(colour)
            rows.append(f"{x},{y}: ({colour[0]},{colour[1]},{colour[2]})  #000000 srgb()")
    parsed = parse_pixel_dump("\n".join(["# ImageMagick pixel enumeration:"] + rows))
    assert parsed == tuple(pixels)
    found = locate_canvas(parsed, 8, 6, 4, 3, min_ink_fraction=0.0)
    assert found is not None
    assert (found.x, found.y, found.width, found.height) == (2, 2, 4, 3)
    assert found.ink == 12
    # A uniform window has no canvas, so the detector must refuse it.
    uniform = tuple((200, 200, 200) for _ in range(8 * 6))
    assert locate_canvas(uniform, 8, 6, 4, 3) is None
    # The declared screen size must fit inside the window.
    assert locate_canvas(parsed, 8, 6, 9, 3) is None


def test_missing_libraries_are_read_from_ldd_output() -> None:
    from e2e.sim.native import parse_missing_libraries

    output = "\n".join(
        [
            "linux-vdso.so.1 (0x00007ffd)",
            "\tlibgtk-3.so.0 => /lib/x86_64-linux-gnu/libgtk-3.so.0 (0x00007f)",
            "\tlibwebkit2gtk-4.0.so.37 => not found",
            "\tlibjavascriptcoregtk-4.0.so.18 => not found",
            "\tlibwebkit2gtk-4.0.so.37 => not found",
        ]
    )
    assert parse_missing_libraries(output) == (
        "libwebkit2gtk-4.0.so.37",
        "libjavascriptcoregtk-4.0.so.18",
    )
    assert parse_missing_libraries("") == ()
    assert parse_missing_libraries("not a dynamic executable") == ()
