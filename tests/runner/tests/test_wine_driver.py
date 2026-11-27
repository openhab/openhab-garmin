"""Wine simulator driver tests that need no Wine, no display and no SDK.

Everything here is either a pure helper (window-title parsing, command
construction, INI and properties rewriting, tap mapping, error messages) or a
driver path that fails *before* it touches the simulator. The real proof is a
run on the guest, which is a scenario, not a unit test.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pytest

from e2e.cli import _check_writable, _parse_index_range
from e2e.config import Device, find_device
from e2e.contract import (
    FAIL,
    NOT_IMPLEMENTED,
    PASS,
    SKIPPED,
    ContractError,
    device_from_prg,
    exit_code,
    run_contract,
    summary,
)
from e2e.sdk import SdkLayout
from e2e.settings import load_settings
from e2e.sim.base import Button, DriverError, SimulatorDriver
from e2e.sim.null import png_bytes
from e2e.trace import parse_lines
from e2e.sim.wine import (
    DEFAULT_KEYS,
    ENV_KEY_PREFIX,
    ENV_SCREEN_RECT,
    ENV_SETTINGS_FILE,
    ENV_STARTUP_TIMEOUT,
    HTTPS_REQUIREMENTS_KEY,
    NIGHT_MODE_KEY,
    ENV_NIGHT_MODE,
    ENV_KEEP_APP_DATA,
    _night_from_environment,
    night_mode_value,
    ScreenRect,
    SimulatorDefinition,
    SimulatorKey,
    WindowTitle,
    classify_ciq_message,
    compare_command,
    coordinate_mapper,
    import_command,
    ini_updates,
    key_bindings,
    monkeydo_command,
    parse_ciq_log,
    parse_mouselocation,
    parse_properties,
    parse_root_window_id,
    parse_screen_rect,
    parse_window_geometry,
    parse_window_title,
    png_problem,
    read_device_display_name,
    read_device_keys,
    read_device_runtime_version,
    read_simulator_definition,
    render_properties,
    screen_rect,
    simulator_command,
    tail,
    timeout_error,
    title_matches_device,
    update_ini,
    vk_to_keysym,
    window_point,
    WineSimulatorDriver,
    xdotool_click_only_command,
    xdotool_focus_commands,
    xdotool_geometry_command,
    xdotool_key_command,
    xdotool_key_to_window_command,
    xdotool_keydown_command,
    xdotool_keyup_command,
    xdotool_mousedown_command,
    xdotool_mouselocation_command,
    xdotool_mousemove_command,
    xdotool_search_command,
    xdotool_window_pid_command,
    xdpyinfo_command,
    xwininfo_root_command,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _sdk(tmp_path: Path) -> SdkLayout:
    root = tmp_path / "sdk"
    (root / "linux" / "bin").mkdir(parents=True)
    (root / "windows" / "bin").mkdir(parents=True)
    version = root / "version.txt"
    version.write_text("9.3.0\n", encoding="utf-8")
    monkeyc = root / "linux" / "bin" / "monkeyc"
    monkeyc.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeydo = root / "linux" / "bin" / "monkeydo"
    monkeydo.write_text("#!/bin/sh\n", encoding="utf-8")
    simulator = root / "windows" / "bin" / "simulator.exe"
    simulator.write_bytes(b"")
    return SdkLayout(
        root=root,
        version="9.3.0",
        version_file=version,
        monkeyc=monkeyc,
        monkeydo=monkeydo,
        simulator=simulator,
        devices=None,
    )


def _driver(tmp_path: Path, **kwargs) -> WineSimulatorDriver:
    return WineSimulatorDriver(
        _sdk(tmp_path),
        wineprefix=tmp_path / "prefix",
        display=":99",
        artifact_dir=tmp_path / "artifacts",
        **kwargs,
    )


def _pressing_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WineSimulatorDriver:
    """A started driver with fenix7 declared input and a fixed 409x600 window."""
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 409, "height": 600}
    )
    return driver


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend every external tool is installed; no test runs one."""
    monkeypatch.setattr(
        "e2e.sim.wine.shutil.which", lambda name: f"/usr/bin/{name}"
    )


def _device(device_id: str = "fenix7") -> Device:
    return find_device(device_id)


class FakeRun:
    """Stand-in for ``driver._run`` that records commands."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        pointer_window: str = "42",
        root_window: str = "543",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.commands: List[List[str]] = []
        self.pointer_window = pointer_window
        self.root_window = root_window
        self._pointer = (0, 0)

    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool = False,
        timeout: float = 30.0,
        action: str = "",
    ) -> subprocess.CompletedProcess:
        self.commands.append(list(command))
        if check and self.returncode != 0:
            raise DriverError("command failed")
        stdout = self.stdout
        if command[:2] == ["xdotool", "mousemove"] and "--window" not in command:
            self._pointer = (int(command[2]), int(command[3]))
            stdout = ""
        elif command[:2] == ["xdotool", "getmouselocation"]:
            x, y = self._pointer
            stdout = (
                f"X={x}\nY={y}\nSCREEN=0\nWINDOW={self.pointer_window}\n"
            )
        elif command[:2] == ["xdotool", "getwindowpid"]:
            stdout = "1234\n"
        elif command[:2] == ["xwininfo", "-root"]:
            stdout = f'xwininfo: Window id: 0x{int(self.root_window):x} "root"\n'
        return subprocess.CompletedProcess(
            list(command), self.returncode, stdout, self.stderr
        )


# ---------------------------------------------------------------------------
# window titles
# ---------------------------------------------------------------------------


def test_loaded_title_names_the_device_and_version() -> None:
    title = parse_window_title("CIQ Simulator - Fenix 7 (6.0.3)")
    assert title == WindowTitle(
        raw="CIQ Simulator - Fenix 7 (6.0.3)", device="Fenix 7", version="6.0.3"
    )
    assert title.loaded
    assert "Fenix 7" in title.describe()


def test_title_without_device_is_generic() -> None:
    title = parse_window_title("CIQ Simulator")
    assert not title.loaded
    assert title.device == ""
    assert "generic" in title.describe()


def test_title_without_version_still_names_the_device() -> None:
    title = parse_window_title("CIQ Simulator - venu 3")
    assert title.loaded
    assert title.device == "venu 3"
    assert title.version == ""


def test_error_dialog_is_not_a_loaded_device() -> None:
    for raw in ("Error", "CIQ Simulator - Error", "Connect IQ Error"):
        title = parse_window_title(raw)
        assert title.error, raw
        assert not title.loaded


def test_unrelated_title_is_generic() -> None:
    title = parse_window_title("xmessage")
    assert not title.loaded
    assert not title.error


def test_empty_title_is_generic() -> None:
    assert not parse_window_title("").loaded


def test_title_matches_product_and_display_name() -> None:
    assert title_matches_device("Fenix 7", "fenix7") is True
    # The simulator prints the marketing name; the product id cannot match it.
    assert title_matches_device("Forerunner 965", "fr965", "Forerunner® 965") is True
    assert title_matches_device("fēnix 8 47mm", "fenix847mm", "fēnix® 8 47mm / 51mm") is True
    assert title_matches_device("Edge 540", "edge540") is True


def test_title_mismatch_is_reported() -> None:
    assert title_matches_device("Edge 540", "fenix7") is False
    assert title_matches_device("Fenix 7", "fenix7", "Forerunner® 965") is True


def test_title_match_is_unknown_without_anything_to_compare() -> None:
    assert title_matches_device("", "fenix7") is False
    assert title_matches_device("Fenix 7", "") is None


def test_device_definition_names_are_read(tmp_path: Path) -> None:
    root = tmp_path / "Devices"
    (root / "fr965").mkdir(parents=True)
    (root / "fr965" / "compiler.json").write_text(
        json.dumps({"displayName": "Forerunner® 965"}), encoding="utf-8"
    )
    (root / "fr965" / "simulator.json").write_text(
        json.dumps(
            {
                "keys": {"up": 38, "down": 40, "enter": 13, "back": 27},
                "display": {
                    "location": {"x": 134, "y": 221, "width": 454, "height": 454},
                    "isTouch": True,
                },
            }
        ),
        encoding="utf-8",
    )
    assert read_device_display_name(root, "fr965") == "Forerunner® 965"
    assert read_device_keys(root, "fr965")["up"] == 38
    definition = read_simulator_definition(root, "fr965")
    assert definition.display == ScreenRect(x=134, y=221, width=454, height=454)
    assert definition.is_touch is True
    assert read_device_display_name(root, "missing") == ""
    assert read_device_keys(None, "fr965") == {}
    assert read_simulator_definition(None, "fr965").display is None


def test_device_definition_without_a_display_has_no_rect(tmp_path: Path) -> None:
    root = tmp_path / "Devices"
    (root / "fenix7").mkdir(parents=True)
    (root / "fenix7" / "simulator.json").write_text(
        json.dumps({"keys": {"up": 38}}), encoding="utf-8"
    )
    definition = read_simulator_definition(root, "fenix7")
    assert definition.display is None
    assert definition.is_touch is True


#: The shape of ``Devices/fenix7/simulator.json`` as the operator reported it:
#: hit areas with behaviours, no keyboard key names.
FENIX7_DEFINITION = {
    "keys": [
        {
            "behavior": "onSelect",
            "id": "enter",
            "location": {"x": 356, "y": 145, "width": 44, "height": 69},
        },
        {
            "behavior": "previousPage",
            "id": "up",
            "location": {"x": 1, "y": 244, "width": 44, "height": 69},
        },
        {
            "behavior": "onMenu",
            "id": "menu",
            "isHold": True,
            "location": {"x": 1, "y": 244, "width": 44, "height": 69},
        },
        {
            "behavior": "nextPage",
            "id": "down",
            "location": {"x": 21, "y": 336, "width": 44, "height": 69},
        },
        {
            "behavior": "onBack",
            "id": "esc",
            "location": {"x": 356, "y": 336, "width": 44, "height": 69},
        },
    ],
    "display": {
        "location": {"x": 70, "y": 70, "width": 260, "height": 260},
        "isTouch": True,
    },
}


def _definition(tmp_path: Path, document: dict = FENIX7_DEFINITION):
    root = tmp_path / "Devices"
    (root / "fenix7").mkdir(parents=True, exist_ok=True)
    (root / "fenix7" / "simulator.json").write_text(
        json.dumps(document), encoding="utf-8"
    )
    return read_simulator_definition(root, "fenix7")


def test_declared_key_areas_are_parsed_by_behaviour(tmp_path: Path) -> None:
    definition = _definition(tmp_path)
    assert definition.key_for(Button.ENTER) is not None
    assert definition.key_for(Button.UP) is not None
    assert definition.key_for(Button.DOWN) is not None
    assert definition.key_for(Button.BACK) is not None
    menu = definition.key_for(Button.MENU)
    assert menu is not None and menu.is_hold is True
    assert definition.key_for(Button.START) is None, "fenix7 declares no start key"
    enter = definition.key_for(Button.ENTER)
    assert enter is not None and enter.location == ScreenRect(356, 145, 44, 69)
    assert "onSelect" in enter.describe()


def test_mapper_is_one_to_one_with_the_declared_space() -> None:
    """The SDK's simulator.json is already in window client coordinates."""
    display = ScreenRect(79, 149, 266, 266)
    mapper = coordinate_mapper(display, None)
    assert (mapper.scale, mapper.offset_x, mapper.offset_y) == (1.0, 0, 0)
    assert "declared" in mapper.source
    key = SimulatorKey(Button.ENTER, ScreenRect(356, 145, 44, 69), source="onSelect")
    assert mapper.point(key.location) == (378, 180)
    assert mapper.rect(display) == display


def test_press_and_tap_agree_on_a_shared_declared_point() -> None:
    """A key centred on the display's top-left maps where tap(0,0) maps."""
    display = ScreenRect(79, 149, 266, 266)
    shared = ScreenRect(78, 148, 2, 2)  # centred on (79,149)
    mapper = coordinate_mapper(display, None)
    assert mapper.point(shared) == mapper.screen_point(0, 0, (260, 260)) == (79, 149)


def test_pinned_screen_rect_places_the_key_areas() -> None:
    """The same E2E_WINE_SCREEN_RECT pin moves the keys with the screen."""
    display = ScreenRect(70, 70, 260, 260)
    key = SimulatorKey(Button.ENTER, ScreenRect(356, 145, 44, 69), source="onSelect")
    pinned = ScreenRect(100, 120, 520, 520)  # 2x, shifted
    mapper = coordinate_mapper(display, pinned)
    assert (mapper.scale, mapper.offset_x, mapper.offset_y) == (2.0, -40, -20)
    assert mapper.source == ENV_SCREEN_RECT
    assert mapper.point(key.location) == (716, 339)
    assert mapper.rect(display) == pinned
    assert mapper.screen_point(0, 0, (260, 260)) == (100, 120)


def test_press_clicks_the_declared_hit_area(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 400, "height": 405}
    )
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    driver.press(Button.ENTER)

    click = [command for command in fake.commands if command[1] == "mousemove"][0]
    assert click[2:4] == ["378", "180"]
    assert not any(command[1] == "key" for command in fake.commands), "no keystroke"
    log = _driver_log(driver)
    assert "clicked ENTER via onSelect" in log


def test_press_uses_the_screen_pin_for_the_hit_area(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setenv(ENV_SCREEN_RECT, "100,120,520,520")  # 2x, shifted
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 800, "height": 810}
    )
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    driver.press(Button.ENTER)

    click = [command for command in fake.commands if command[1] == "mousemove"][0]
    assert click[2:4] == ["716", "339"]
    assert ENV_SCREEN_RECT in _driver_log(driver)


def test_press_uses_the_proven_keyboard_binding_for_up_and_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guest proved Up/Down are honoured; clicks are silent."""
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)  # also declares up/down hit areas
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    driver.press(Button.DOWN)

    key = [command for command in fake.commands if command[1] == "key"][0]
    assert key[-1] == "Down"
    assert not any(command[1] == "mousemove" for command in fake.commands)
    result = driver.last_press()
    assert result is not None and result.mechanism == "keyboard"
    assert "proven keyboard binding" in _driver_log(driver)


def test_press_uses_the_mouse_for_a_button_without_a_proven_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 409, "height": 600}
    )
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    driver.press(Button.ENTER)

    assert any(command[1] == "mousemove" for command in fake.commands)
    result = driver.last_press()
    assert result is not None and result.mechanism == "mouse"
    assert "pointer was verified over that window" in _driver_log(driver)


def test_press_measured_reports_the_effect_and_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 409, "height": 600}
    )
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    monkeypatch.setattr(
        driver, "screenshot", lambda path: (Path(path).write_bytes(png_bytes()), Path(path))[1]
    )
    monkeypatch.setattr(
        driver, "region_pixel_difference", lambda before, after, region: 4321
    )
    driver._run = FakeRun()  # type: ignore[method-assign]

    counts = iter([3, 5])
    result = driver.press_measured(Button.DOWN, requests=lambda: next(counts))

    assert result.mechanism == "keyboard"
    assert result.effect == "screen change 4321px"
    assert result.screen_delta == 4321
    assert result.requests == 2
    assert "screen=4321px" in result.describe()
    assert "requests +2" in result.describe()


def test_press_measured_reports_no_effect_when_nothing_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    monkeypatch.setattr(
        driver, "screenshot", lambda path: (Path(path).write_bytes(png_bytes()), Path(path))[1]
    )
    monkeypatch.setattr(driver, "region_pixel_difference", lambda before, after, region: 0)
    driver._run = FakeRun()  # type: ignore[method-assign]

    result = driver.press_measured(Button.DOWN, timeout_s=0.05)

    assert result.effect == ""
    assert "effect=none" in result.describe()


def test_press_holds_a_declared_hold_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 400, "height": 405}
    )
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    driver.press(Button.MENU)

    assert ["xdotool", "mouseup", "1"] in fake.commands
    down = [command for command in fake.commands if command[-2:] == ["mousedown", "1"]]
    assert down, "the hold must press the button"
    assert "(hold)" in _driver_log(driver)


def test_press_falls_back_to_a_keysym_without_a_declared_area(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)  # fenix7 declares no start key
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 400, "height": 405}
    )
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    driver.press(Button.START)

    key = [command for command in fake.commands if command[1] == "key"][0]
    assert key[-1] == DEFAULT_KEYS[Button.START]
    assert "no proven binding or hit area" in _driver_log(driver)


def test_press_key_override_beats_the_declared_area(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setenv(f"{ENV_KEY_PREFIX}ENTER", "Return")
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    driver.press(Button.ENTER)

    assert [command for command in fake.commands if command[1] == "key"]
    assert not any(command[1] == "mousemove" for command in fake.commands)


def test_press_refuses_a_pointer_over_the_root_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guest's silent mouse: the pointer sat over the root window."""
    driver = _pressing_driver(tmp_path, monkeypatch)
    fake = FakeRun(pointer_window="543", root_window="543")
    driver._run = fake  # type: ignore[method-assign]

    with pytest.raises(DriverError) as error:
        driver.press(Button.ENTER)

    message = str(error.value)
    assert "never reached the simulator window" in message
    assert "root window" in message
    assert not any(command[1] == "click" for command in fake.commands), "no blind click"


def test_press_accepts_a_child_window_of_the_simulator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wine parents the client area, so a child window with the same pid is fine."""
    driver = _pressing_driver(tmp_path, monkeypatch)
    fake = FakeRun(pointer_window="99")  # same pid (see FakeRun.getwindowpid)
    driver._run = fake  # type: ignore[method-assign]

    driver.press(Button.ENTER)

    assert any(command[1] == "click" for command in fake.commands)
    assert "window 42" in _driver_log(driver)


def test_press_refuses_a_pointer_over_another_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _pressing_driver(tmp_path, monkeypatch)
    monkeypatch.setattr(
        driver,
        "_geometry",
        lambda window: {"x": 0, "y": 0, "width": 409, "height": 600}
        if window == "42"
        else {"x": 900, "y": 0, "width": 200, "height": 200},
    )

    class ForeignRun(FakeRun):
        def __call__(self, command, **kwargs):  # noqa: ANN001, ANN003
            if command[:2] == ["xdotool", "getwindowpid"]:
                pid = "4321\n" if command[-1] == "99" else "1234\n"
                return subprocess.CompletedProcess(list(command), 0, pid, "")
            return super().__call__(command, **kwargs)

    fake = ForeignRun(pointer_window="99")
    driver._run = fake  # type: ignore[method-assign]

    with pytest.raises(DriverError) as error:
        driver.press(Button.ENTER)
    assert "not the simulator window" in str(error.value)


def test_pointer_verification_retries_once_when_the_window_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Geometry is re-read: a stale position must not become a failure."""
    driver = _pressing_driver(tmp_path, monkeypatch)
    positions = iter([{"x": 0, "y": 0, "width": 409, "height": 600},
                      {"x": 10, "y": 20, "width": 409, "height": 600},
                      {"x": 10, "y": 20, "width": 409, "height": 600},
                      {"x": 10, "y": 20, "width": 409, "height": 600}])

    class MovingRun(FakeRun):
        def __call__(self, command, **kwargs):  # noqa: ANN001, ANN003
            if command[:2] == ["xdotool", "mousemove"] and "--window" not in command:
                # The first move misses because the window moved underneath us.
                if len([c for c in self.commands if c[1] == "mousemove"]) == 1:
                    self._pointer = (int(command[2]) - 5, int(command[3]))
                    return subprocess.CompletedProcess(list(command), 0, "", "")
            return super().__call__(command, **kwargs)

    monkeypatch.setattr(driver, "_geometry", lambda window: next(positions))
    fake = MovingRun()
    driver._run = fake  # type: ignore[method-assign]

    driver.press(Button.ENTER)

    assert any(command[1] == "click" for command in fake.commands)


def test_tap_uses_the_declared_one_to_one_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """display.location is already in window coordinates: no scaling."""
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")  # 260x260, touch+button
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 409, "height": 600}
    )
    monkeypatch.delenv(ENV_SCREEN_RECT, raising=False)
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    driver.tap(130, 130)  # centre of the device screen

    click = [command for command in fake.commands if command[1] == "mousemove"][0]
    assert click[2:4] == ["200", "200"]
    assert "screen=70,70,260,260" in _driver_log(driver)


def test_launch_pages_down_then_selects_on_a_button_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verified sequence is Down, then select — not select alone."""
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=eng"),
        (0.0, "E2E:VIEW;category=glance;view=GlanceSitemapView;depth=1"),
    ]
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 400, "height": 405}
    )
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RESPONSE_WAIT_S", 0.1)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RETRY_BUDGET_S", 5.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RETRY_INTERVAL_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_KEY_GAP_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.FOCUS_SETTLE_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_DWELL_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_SETTLE_MAX_S", 0.0)
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    opened = driver.launch(timeout_s=1.0)

    assert opened is False, "no new view was produced by the fake"
    log = _driver_log(driver)
    assert "2 x page down, then select" in log
    assert "launch retry 1: variant=declared hit areas" in log
    assert "launch step page down 1/2" in log
    assert "launch step page down 2/2" in log
    assert "launch step select" in log
    assert "focus before=" in log and "focus after=" in log
    assert "command: xdotool" in log, "the exact argv is logged"
    assert "launch retry 2: variant=keyboard taps" in log
    assert "launch retry 3: variant=keyboard keydown/keyup" in log
    assert "launch retry 4: variant=declared hit areas (hold)" in log
    assert "connection=" in log, "each step logs the connection state"
    # Attempt 1 clicks the declared areas, attempt 2 sends the keysyms.
    assert any(command[1] == "mousemove" for command in fake.commands)
    assert any(command[1] == "key" for command in fake.commands)


def test_launch_stops_when_the_app_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=eng"),
        (0.0, "E2E:VIEW;category=glance;view=GlanceSitemapView;depth=1"),
    ]
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 400, "height": 405}
    )
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_DWELL_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_SETTLE_MAX_S", 0.0)
    fake = FakeRun()

    class OpeningRun(FakeRun):
        def __call__(self, command, **kwargs):  # noqa: ANN001, ANN003
            completed = super().__call__(command, **kwargs)
            # Parking the pointer is capture setup, not input: only a real
            # press may open the app.
            if (
                command[1] == "mousemove"
                and "--window" not in command
                and "sitemap" not in str(driver._events)
            ):
                driver._events.append(
                    (0.0, "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2")
                )
            return completed

    driver._run = OpeningRun()  # type: ignore[method-assign]

    assert driver.launch(timeout_s=2.0) is True
    log = _driver_log(driver)
    assert "launch retry 1 (declared hit areas) opened the app" in log
    assert "launch retry 2:" not in log, "the retries stop on success"


def test_launch_taps_the_screen_on_a_touch_only_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("venu2")  # touch only
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=eng"),
        (0.0, "E2E:VIEW;category=glance;view=GlanceSitemapView;depth=1"),
    ]
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - venu 2")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 416, "height": 416}
    )
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RESPONSE_WAIT_S", 0.1)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RETRY_BUDGET_S", 5.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RETRY_INTERVAL_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_KEY_GAP_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.FOCUS_SETTLE_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_DWELL_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_SETTLE_MAX_S", 0.0)
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    assert driver.launch(timeout_s=1.0) is False
    assert any(command[1] == "mousemove" for command in fake.commands)
    assert not any(command[1] == "key" for command in fake.commands)
    assert "a tap on the screen" in _driver_log(driver)


def test_open_app_does_nothing_when_the_app_is_already_open(tmp_path: Path) -> None:
    driver = _driver(tmp_path)
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]
    parsed = list(
        parse_lines(["E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2"])
    )
    returned = driver._open_app_if_needed(parsed, time.monotonic() + 1.0)
    assert returned == parsed
    assert fake.commands == []
    log_path = driver.artifact_dir / "logs" / "driver.log"
    assert not log_path.exists() or "started on the glance page" not in log_path.read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# key bindings
# ---------------------------------------------------------------------------


def test_default_key_bindings_cover_every_button() -> None:
    assert set(DEFAULT_KEYS) == set(Button)


def test_device_definition_keys_override_defaults() -> None:
    bindings = key_bindings({"up": 38, "down": 40, "enter": 13, "back": 27, "menu": 77})
    assert bindings[Button.UP] == "Up"
    assert bindings[Button.DOWN] == "Down"
    assert bindings[Button.ENTER] == "Return"
    assert bindings[Button.BACK] == "Escape"
    assert bindings[Button.MENU] == "M"
    assert bindings[Button.START] == DEFAULT_KEYS[Button.START]


def test_string_key_values_are_accepted() -> None:
    assert key_bindings({"select": "Return"})[Button.ENTER] == "Return"
    assert key_bindings({"select": "13"})[Button.ENTER] == "Return"


def test_environment_overrides_every_binding() -> None:
    env = {f"{ENV_KEY_PREFIX}{button.value}": f"key-{button.value.lower()}" for button in Button}
    bindings = key_bindings({"up": 38}, env)
    for button in Button:
        assert bindings[button] == f"key-{button.value.lower()}"


def test_unknown_actions_and_booleans_are_ignored() -> None:
    bindings = key_bindings({"lap": 33, "up": True})
    assert bindings[Button.UP] == DEFAULT_KEYS[Button.UP]


def test_virtual_key_codes_map_to_keysyms() -> None:
    assert vk_to_keysym(0x26) == "Up"
    assert vk_to_keysym(0x0D) == "Return"
    assert vk_to_keysym(0x41) == "A"
    assert vk_to_keysym(0x70) == "F1"
    assert vk_to_keysym(0xEE) is None


# ---------------------------------------------------------------------------
# command construction
# ---------------------------------------------------------------------------


def test_simulator_and_app_commands() -> None:
    simulator = Path("/sdk/simulator.exe")
    assert simulator_command("wine", simulator) == ["wine", str(simulator)]
    assert monkeydo_command(Path("/sdk/monkeydo"), Path("/a/app.prg"), "fenix7") == [
        str(Path("/sdk/monkeydo")),
        str(Path("/a/app.prg")),
        "fenix7",
    ]
    assert monkeydo_command(
        Path("/sdk/monkeydo"), Path("/a/app.prg"), "fenix7", stdbuf="/usr/bin/stdbuf"
    ) == [
        "/usr/bin/stdbuf",
        "-oL",
        "-eL",
        str(Path("/sdk/monkeydo")),
        str(Path("/a/app.prg")),
        "fenix7",
    ]


def test_xdotool_and_capture_commands() -> None:
    assert xdotool_search_command()[:3] == ["xdotool", "search", "--onlyvisible"]
    assert xdotool_geometry_command("42") == [
        "xdotool",
        "getwindowgeometry",
        "--shell",
        "42",
    ]
    assert xdotool_key_command("Down", 2) == [
        "xdotool",
        "key",
        "--clearmodifiers",
        "--repeat",
        "2",
        "--delay",
        "150",
        "Down",
    ]
    assert xdotool_key_to_window_command("42", "Down", 2)[:4] == [
        "xdotool",
        "key",
        "--window",
        "42",
    ]
    assert import_command(":99", "42", Path("/shots/a.png")) == [
        "import",
        "-display",
        ":99",
        "-window",
        "42",
        "-silent",
        str(Path("/shots/a.png")),
    ]
    assert xdpyinfo_command(":99") == ["xdpyinfo", "-display", ":99"]
    assert compare_command(Path("/a.png"), Path("/b.png")) == [
        "compare",
        "-metric",
        "AE",
        str(Path("/a.png")),
        str(Path("/b.png")),
        "null:",
    ]


def test_parse_mouselocation_reads_the_window_under_the_pointer() -> None:
    """`xdotool getmouselocation` says which window would get the click."""
    location = parse_mouselocation("X=378\nY=179\nSCREEN=0\nWINDOW=543\n")
    assert location is not None
    assert (location["x"], location["y"], location["window"]) == (378, 179, 543)
    assert parse_mouselocation("X=0\nY=0\nSCREEN=0\n") is None
    assert parse_mouselocation("") is None
    assert parse_mouselocation("garbage") is None
    # The --shell form used by the driver carries the same fields.
    assert parse_mouselocation("WINDOW=85983235\nX=12\nY=34\nSCREEN=0\n")["window"] == (
        85983235
    )


def test_parse_root_window_id_reads_the_xwininfo_line() -> None:
    text = 'xwininfo: Window id: 0x21f "root"\n\n  Absolute upper-left X: 0\n'
    assert parse_root_window_id(text) == "543"
    assert parse_root_window_id("nothing here") is None


def test_pointer_commands_move_then_click_separately() -> None:
    """A blind click is forbidden: move, verify, then click at the pointer."""
    assert xdotool_mousemove_command(120, 240) == ["xdotool", "mousemove", "120", "240"]
    assert xdotool_mouselocation_command()[:2] == ["xdotool", "getmouselocation"]
    assert xdotool_click_only_command(2)[:2] == ["xdotool", "click"]
    assert xdotool_click_only_command(2)[2:4] == ["--repeat", "2"]
    assert xdotool_mousedown_command() == ["xdotool", "mousedown", "1"]
    assert xdotool_window_pid_command("42") == ["xdotool", "getwindowpid", "42"]
    assert xwininfo_root_command() == ["xwininfo", "-root"]


def test_count_and_delay_are_clamped() -> None:
    assert xdotool_key_command("Up", 0)[4] == "1"
    assert xdotool_key_command("Up", 1, delay_ms=-5)[6] == "0"


def test_focus_commands_activate_then_focus() -> None:
    commands = xdotool_focus_commands("42")
    assert commands[0][:3] == ["xdotool", "windowactivate", "--sync"]
    assert commands[1][:3] == ["xdotool", "windowfocus", "--sync"]


def test_window_geometry_is_parsed() -> None:
    text = "WINDOW=42\nX=10\nY=20\nWIDTH=713\nHEIGHT=961\nSCREEN=0\n"
    geometry = parse_window_geometry(text)
    assert geometry is not None
    assert (geometry["x"], geometry["y"]) == (10, 20)
    assert (geometry["width"], geometry["height"]) == (713, 961)
    assert parse_window_geometry("WINDOW=42\nX=10\n") is None


# ---------------------------------------------------------------------------
# simulator.ini
# ---------------------------------------------------------------------------


def test_mode_and_language_become_ini_updates() -> None:
    """`Language` is the numeric simulator index, not a code (Language=7 is German)."""
    ble = ini_updates("fenix7", "ble", "7")
    assert ble == {
        "LastUsedDevice": "fenix7",
        HTTPS_REQUIREMENTS_KEY: "0",
        NIGHT_MODE_KEY: "0",
        "Language": "7",
        "BleState": "2",
        "WifiState": "1",
    }
    wifi = ini_updates("fenix7", "wifi", None)
    assert (wifi["BleState"], wifi["WifiState"]) == ("1", "2")
    assert "Language" not in wifi, "the default language is the simulator's own"
    offline = ini_updates("fenix7", "offline", None)
    assert (offline["BleState"], offline["WifiState"]) == ("1", "0")
    with pytest.raises(DriverError):
        ini_updates("fenix7", "carrier-pigeon", None)


def test_a_stale_language_index_is_removed_for_the_default_language() -> None:
    """A leftover `Language=7` from the maintainer's file would run German."""
    update = update_ini("Language=7\nBleState=0\n", {"BleState": "2"}, remove=("Language",))
    assert "Language" not in update.text
    assert "BleState=2" in update.text
    assert update.removed == ("Language",)
    assert "Language" in update.keys


def test_the_language_index_is_written_for_a_mapped_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    monkeypatch.setattr(
        "e2e.sim.wine.load_language_index", lambda: {"eng": 0, "deu": 7}
    )
    driver._language_index_map = None
    assert driver._language_ini_value("deu") == "7"
    assert driver._language_ini_value("eng") is None


def test_an_unmapped_language_refuses_to_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    monkeypatch.setattr("e2e.sim.wine.load_language_index", lambda: {"deu": 7})
    driver._language_index_map = None
    with pytest.raises(DriverError) as error:
        driver._language_ini_value("pol")
    message = str(error.value)
    assert "no measured simulator index" in message
    assert "language-scan" in message


def test_the_declared_display_is_checked_against_the_real_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fenix847mm's window is 713x961: a 409x600 assumption is only right for fenix7."""
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix847mm")
    driver._window = "42"
    monkeypatch.delenv(ENV_SCREEN_RECT, raising=False)
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 713, "height": 961}
    )
    driver._definition = SimulatorDefinition(
        display=ScreenRect(134, 221, 454, 454), keys=()
    )
    driver._verify_window_geometry()
    log = _driver_log(driver)
    assert "window geometry: 713x961" in log
    assert "fits the 713x961 window 1:1" in log

    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 409, "height": 600}
    )
    with pytest.raises(DriverError) as error:
        driver._verify_window_geometry()
    message = str(error.value)
    assert "does not fit inside it" in message
    assert "1:1" in message
    assert "E2E_WINE_SCREEN_RECT" in message


def test_a_pinned_screen_rect_skips_the_window_fit_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._device = _device("fenix847mm")
    driver._window = "42"
    driver._definition = SimulatorDefinition(
        display=ScreenRect(134, 221, 454, 454), keys=()
    )
    monkeypatch.setenv(ENV_SCREEN_RECT, "10,20,300,300")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 409, "height": 600}
    )
    driver._verify_window_geometry()  # no raise: the pin is the operator's call
    assert "is pinned" in _driver_log(driver)


def test_parse_index_range_accepts_ranges_and_lists() -> None:
    assert _parse_index_range("0-3") == [0, 1, 2, 3]
    assert _parse_index_range("0,3,7") == [0, 3, 7]
    assert _parse_index_range("0-2,2-3") == [0, 1, 2, 3]
    with pytest.raises(ValueError):
        _parse_index_range("5-3")
    with pytest.raises(ValueError):
        _parse_index_range("")


def test_existing_ini_keys_are_rewritten_case_insensitively() -> None:
    update = update_ini(
        "language = eng\nBleState=0\n",
        {"Language": "deu", "BleState": "2"},
    )
    assert update.text.splitlines() == ["language=deu", "BleState=2"]
    assert set(update.written) == {"Language", "BleState"}
    assert update.appended == ()


def test_missing_ini_keys_are_appended_without_a_section_header() -> None:
    update = update_ini("Language=eng\n", {"WifiState": "2"})
    assert update.text.splitlines() == ["Language=eng", "WifiState=2"]
    assert update.appended == ("WifiState",)
    assert "[" not in update.text


def test_ini_created_from_nothing_is_flat() -> None:
    update = update_ini("", {"Language": "deu"})
    assert update.text == "Language=deu\n"


def test_ini_keeps_the_keys_the_simulator_wrote_itself() -> None:
    """The simulator rewrites the file, so unrelated keys must survive.

    The shape is the one the operator's Windows simulator writes: flat
    ``key=value``, numeric connection states, no section header.
    """
    original = (
        "LastFplVersion=9\n"
        "LastUsedDevice=fenix9pro47mm\n"
        "BleState=2\n"
        "UseHttpsRequirements=0\n"
        "nightMode=1\n"
    )
    update = update_ini(
        original,
        {
            "LastUsedDevice": "fenix6pro",
            "Language": "deu",
            "BleState": "1",
            "WifiState": "2",
        },
    )
    lines = update.text.splitlines()
    for kept in ("LastFplVersion=9", "UseHttpsRequirements=0", "nightMode=1"):
        assert kept in lines
    assert "LastUsedDevice=fenix6pro" in lines
    assert "BleState=1" in lines
    assert "Language=deu" in lines
    assert "WifiState=2" in lines
    assert not any(line.strip().startswith("[") for line in lines)


def test_ini_update_is_idempotent() -> None:
    once = update_ini("LastFplVersion=9\n", {"Language": "deu", "BleState": "2"})
    twice = update_ini(once.text, {"Language": "deu", "BleState": "2"})
    assert twice.text == once.text
    assert set(twice.written) == {"Language", "BleState"}


def test_simulator_ini_is_written_at_the_connectiq_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not under ``ConnectIQ/Simulator/``: the simulator never reads that."""
    monkeypatch.setenv("USER", "root")
    monkeypatch.delenv("WINEUSERNAME", raising=False)
    driver = _driver(tmp_path)
    driver._write_simulator_ini(_device("fenix7"), "ble", "deu")

    files = sorted((tmp_path / "prefix").rglob("simulator.ini"))
    assert len(files) == 1
    assert files[0].parent.name == "ConnectIQ"
    assert "Simulator" not in files[0].parts
    text = files[0].read_text(encoding="utf-8")
    assert "[Simulator]" not in text
    assert "LastUsedDevice=fenix7" in text
    assert "Language=deu" in text
    assert "BleState=2" in text
    assert "WifiState=1" in text


def test_simulator_ini_rewrite_keeps_the_simulators_own_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USER", "root")
    monkeypatch.delenv("WINEUSERNAME", raising=False)
    real = (
        tmp_path
        / "prefix"
        / "drive_c"
        / "users"
        / "root"
        / "AppData"
        / "Roaming"
        / "Garmin"
        / "ConnectIQ"
        / "simulator.ini"
    )
    real.parent.mkdir(parents=True)
    real.write_text(
        "LastFplVersion=9\nLastUsedDevice=fenix9pro47mm\nBleState=2\n", encoding="utf-8"
    )
    driver = _driver(tmp_path)
    driver._write_simulator_ini(_device("fenix7"), "wifi", "eng")

    text = real.read_text(encoding="utf-8")
    assert "LastFplVersion=9" in text
    assert "LastUsedDevice=fenix7" in text
    assert "BleState=1" in text
    assert "WifiState=2" in text
    assert "Language=eng" in text
    assert len(sorted((tmp_path / "prefix").rglob("simulator.ini"))) == 1


# ---------------------------------------------------------------------------
# tap mapping
# ---------------------------------------------------------------------------


def test_screen_rect_is_a_centred_aspect_preserving_fit() -> None:
    rect = screen_rect({"x": 0, "y": 0, "width": 800, "height": 600}, (400, 400))
    assert rect == ScreenRect(x=100, y=0, width=600, height=600)


def test_tap_points_map_into_the_screen_and_are_clamped() -> None:
    rect = ScreenRect(x=100, y=0, width=600, height=600)
    assert window_point(0, 0, rect, (400, 400)) == (100, 0)
    assert window_point(200, 200, rect, (400, 400)) == (400, 300)
    assert window_point(400, 400, rect, (400, 400)) == (698, 598)
    assert window_point(-5, 999, rect, (400, 400)) == (100, 598)


def test_screen_rect_can_be_pinned() -> None:
    assert parse_screen_rect("10,20,300,400") == ScreenRect(10, 20, 300, 400)
    assert parse_screen_rect("10,20,0,400") is None
    assert parse_screen_rect("10,20,30") is None


# ---------------------------------------------------------------------------
# application properties
# ---------------------------------------------------------------------------


def test_key_value_properties_round_trip_with_unknown_keys() -> None:
    text = "url_0=http://127.0.0.1:8080/\nrestapi_0=true\npollingInterval_0=500\ncustom_0=x\n"
    document = parse_properties(text)
    assert document.fmt == "key-value"
    assert document.properties["restapi_0"] is True
    assert document.properties["pollingInterval_0"] == 500
    merged: Dict[str, object] = dict(document.properties)
    merged["url_0"] = "http://127.0.0.1:9/"
    rendered = render_properties(merged, fmt=document.fmt)
    assert "custom_0=x" in rendered
    assert "url_0=http://127.0.0.1:9/" in rendered
    assert "restapi_0=true" in rendered


def test_flat_json_properties_keep_their_types() -> None:
    document = parse_properties('{"url_0": "u/", "restapi_0": true, "pollingInterval_0": 900}')
    assert document.fmt == "json"
    assert document.properties["restapi_0"] is True
    rendered = render_properties(document.properties, fmt="json")
    assert json.loads(rendered)["pollingInterval_0"] == 900


def test_nested_json_properties_preserve_the_document() -> None:
    text = json.dumps({"name": "app", "properties": {"url_0": "u/"}})
    document = parse_properties(text)
    assert document.container == "properties"
    assert document.properties == {"url_0": "u/"}
    rendered = json.loads(
        render_properties(
            {"url_0": "v/"},
            fmt=document.fmt,
            body=document.body,
            container=document.container,
        )
    )
    assert rendered["name"] == "app"
    assert rendered["properties"] == {"url_0": "v/"}


def test_broken_json_falls_back_to_key_value() -> None:
    document = parse_properties("{not json\nurl_0=u/\n")
    assert document.fmt == "key-value"
    assert document.properties == {"url_0": "u/"}


# ---------------------------------------------------------------------------
# error messages
# ---------------------------------------------------------------------------


def test_timeout_errors_name_the_stage_and_the_next_step() -> None:
    display = timeout_error("display", 5.0, display=":97")
    assert "display did not complete within 5s" in str(display)
    assert "xdpyinfo -display :97" in str(display)
    window = timeout_error(
        "simulator-window", 120.0, details=["last title seen: 'CIQ Simulator'"]
    )
    assert "link-devices" in str(window)
    assert "last title seen" in str(window)
    ready = timeout_error("app-ready", 60.0, details=["console tail:"])
    assert "logs/console.log" in str(ready)
    assert "simulator-driver.md" in str(ready)
    assert "did not complete" in str(timeout_error("unknown-stage", 1.0))


def test_tail_keeps_the_last_non_empty_lines() -> None:
    assert tail(["", "a", "b", "c"], limit=2) == "  b\n  c"
    assert tail([], limit=2) == "  <no output>"


def test_png_problem_rejects_trivial_and_missing_images(tmp_path: Path) -> None:
    assert "does not exist" in png_problem(tmp_path / "absent.png")
    placeholder = tmp_path / "a.png"
    placeholder.write_bytes(b"not a png")
    assert "signature" in png_problem(placeholder)


# ---------------------------------------------------------------------------
# driver failure paths that need no simulator
# ---------------------------------------------------------------------------


def test_prepare_rejects_a_missing_prg(tmp_path: Path) -> None:
    driver = _driver(tmp_path)
    with pytest.raises(DriverError) as error:
        driver.prepare(tmp_path / "absent.prg")
    assert "not a file" in str(error.value)


def test_prepare_stages_the_prg(tmp_path: Path, tools: None) -> None:
    driver = _driver(tmp_path)
    prg = tmp_path / "app.prg"
    prg.write_bytes(b"prg")
    driver.prepare(prg)
    assert driver._prg is not None
    assert driver._prg.read_bytes() == b"prg"


def test_start_requires_prepare(tmp_path: Path) -> None:
    driver = _driver(tmp_path)
    with pytest.raises(DriverError) as error:
        driver.start(_device(), load_settings("default"), "ble", "eng")
    assert "prepare()" in str(error.value)


def test_start_rejects_an_unknown_mode(tmp_path: Path, tools: None) -> None:
    driver = _driver(tmp_path)
    prg = tmp_path / "app.prg"
    prg.write_bytes(b"prg")
    driver.prepare(prg)
    with pytest.raises(DriverError) as error:
        driver.start(_device(), load_settings("default"), "carrier-pigeon", "eng")
    assert "unknown mode" in str(error.value)


def test_start_fails_clearly_when_the_display_is_not_answering(
    tmp_path: Path, tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    prg = tmp_path / "app.prg"
    prg.write_bytes(b"prg")
    monkeypatch.setenv("E2E_WINE_BINARY", "wine")
    driver.prepare(prg)
    fake = FakeRun(returncode=1, stderr="xdpyinfo: unable to open display ':99'")
    driver._run = fake  # type: ignore[method-assign]
    with pytest.raises(DriverError) as error:
        driver.start(_device(), load_settings("default"), "ble", "eng")
    message = str(error.value)
    assert ":99" in message
    assert "Xvfb" in message
    assert fake.commands[0] == ["xdpyinfo", "-display", ":99"]


def test_press_and_tap_need_a_started_session(tmp_path: Path) -> None:
    driver = _driver(tmp_path)
    with pytest.raises(DriverError) as press_error:
        driver.press(Button.UP)
    assert "start()" in str(press_error.value)
    with pytest.raises(DriverError) as tap_error:
        driver.tap(1, 1)
    assert "start()" in str(tap_error.value)


def test_tap_rejects_a_button_only_device(tmp_path: Path) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("edge540")
    with pytest.raises(DriverError) as error:
        driver.tap(10, 10)
    assert "no touch screen" in str(error.value)


def test_press_rejects_a_touch_only_device(tmp_path: Path) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("venu2")
    with pytest.raises(DriverError) as error:
        driver.press(Button.UP)
    assert "no buttons" in str(error.value)


def test_press_sends_the_bound_key_to_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    driver.press(Button.DOWN, 2)
    key = [command for command in fake.commands if command[1] == "key"][0]
    assert "--window" not in key, "XTEST goes to the focused window, not XSendEvent"
    assert key[-1] == "Down"
    assert "XTEST" in _driver_log(driver)


def test_tap_maps_device_coordinates_to_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("venu2")
    driver._window = "42"
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - venu 2")
    monkeypatch.setattr(
        driver,
        "_geometry",
        lambda window: {"x": 0, "y": 0, "width": 416, "height": 416},
    )
    monkeypatch.delenv(ENV_SCREEN_RECT, raising=False)
    driver.tap(208, 208)
    click = [command for command in fake.commands if command[1] == "mousemove"][0]
    assert click[2:4] == ["208", "208"]
    result = driver.last_press()
    assert result is not None
    assert "simulator window 42" in result.detail
    assert "pointer verified" in result.detail


def test_seeding_writes_and_round_trips_every_property(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    target = tmp_path / "prefix" / "settings.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("custom_0=keep\n", encoding="utf-8")
    monkeypatch.setenv(ENV_SETTINGS_FILE, str(target))
    settings = load_settings("default")
    driver._seed_settings(settings)
    document = parse_properties(target.read_text(encoding="utf-8"))
    assert document.properties["custom_0"] == "keep"
    for name, value in settings.properties.items():
        assert document.properties[name] == value
    assert driver.settings_file == target


def test_seeding_does_nothing_without_the_escape_hatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Properties are compiled into the PRG by the build, not seeded here."""
    driver = _driver(tmp_path)
    monkeypatch.delenv(ENV_SETTINGS_FILE, raising=False)
    driver._seed_settings(load_settings("default"))
    assert driver.settings_file is None
    assert list((tmp_path / "prefix").rglob("*.SET")) == []
    log = _driver_log(driver)
    assert "settings.xml resource override" in log
    assert "no simulator-side properties file is written" in log


def test_prepare_sweeps_a_stray_simulator(
    tmp_path: Path, tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second simulator exits silently and monkeydo then hangs forever."""
    driver = _driver(tmp_path)
    prg = tmp_path / "app.prg"
    prg.write_bytes(b"prg")
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]
    windows = {"visible": ["42"]}
    monkeypatch.setattr(
        driver,
        "_find_windows",
        lambda: windows.pop("visible", []) if windows else [],
    )
    monkeypatch.setattr(driver, "_error_windows", lambda: [])
    driver.prepare(prg)
    assert ["/usr/bin/wineserver", "-k"] in fake.commands
    log = _driver_log(driver)
    assert "a simulator was already running" in log
    assert "stopped it" in log


class _IdleSimulator:
    """A simulator process that keeps running."""

    returncode = None
    pid = 1

    def poll(self) -> Optional[int]:
        return None


def test_generic_title_is_enough_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The device name only appears after monkeydo pushes the app."""
    driver = _driver(tmp_path)
    driver._simulator_process = _IdleSimulator()  # type: ignore[assignment]
    monkeypatch.setattr(driver, "_find_windows", lambda: ["42"])
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator")
    monkeypatch.setattr(driver, "_error_windows", lambda: [])
    driver._await_simulator_window()
    assert driver._window == "42"
    log = (driver.artifact_dir / "logs" / "driver.log").read_text(encoding="utf-8")
    assert "monkeydo pushes the app" in log


def test_simulator_window_timeout_says_no_window_appeared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._simulator_process = _IdleSimulator()  # type: ignore[assignment]
    monkeypatch.setenv(ENV_STARTUP_TIMEOUT, "1")
    monkeypatch.setattr(driver, "_find_windows", lambda: [])
    monkeypatch.setattr(driver, "_error_windows", lambda: [])
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    with pytest.raises(DriverError) as error:
        driver._await_simulator_window()
    message = str(error.value)
    assert "no window titled `CIQ Simulator` appeared" in message
    assert "link-devices" in message
    assert "device name only appears" in message


def test_simulator_window_rejects_an_error_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._simulator_process = _IdleSimulator()  # type: ignore[assignment]
    monkeypatch.setattr(driver, "_find_windows", lambda: ["42"])
    monkeypatch.setattr(driver, "_window_name", lambda window: "Error")
    monkeypatch.setattr(driver, "_error_windows", lambda: [])
    with pytest.raises(DriverError) as error:
        driver._await_simulator_window()
    assert "error dialog" in str(error.value)


def test_separate_error_dialog_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._simulator_process = _IdleSimulator()  # type: ignore[assignment]
    monkeypatch.setattr(driver, "_find_windows", lambda: [])
    monkeypatch.setattr(driver, "_error_windows", lambda: ["99"])
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    with pytest.raises(DriverError) as error:
        driver._await_simulator_window()
    assert "error dialog" in str(error.value)


def test_transport_wait_records_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    driver = _driver(tmp_path)

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(
        "e2e.sim.wine.socket.create_connection", lambda address, timeout=0.5: Connection()
    )
    driver._await_simulator_transport()
    log = (driver.artifact_dir / "logs" / "driver.log").read_text(encoding="utf-8")
    assert "accepts connections" in log


def test_transport_wait_only_warns_when_the_port_stays_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)

    def refuse(address, timeout=0.5):  # type: ignore[no-untyped-def]
        raise OSError("connection refused")

    monkeypatch.setattr("e2e.sim.wine.SIMULATOR_TRANSPORT_TIMEOUT", 0.05)
    monkeypatch.setattr("e2e.sim.wine.socket.create_connection", refuse)
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    driver._await_simulator_transport()
    log = (driver.artifact_dir / "logs" / "driver.log").read_text(encoding="utf-8")
    assert "launching anyway" in log


# ---------------------------------------------------------------------------
# simulator runtime log
# ---------------------------------------------------------------------------


def test_ciq_log_messages_are_classified() -> None:
    symbol = classify_ciq_message(
        "Symbol Not Found Error: Could not find symbol 'createBufferedBitmap'"
    )
    assert symbol is not None and symbol.kind == "symbol-not-found"
    assert "Rebuild" in symbol.label and "not treat it as an app defect" in symbol.label
    assert "createBufferedBitmap" in symbol.message

    font = classify_ciq_message("Invalid Font Specified")
    assert font is not None and font.kind == "invalid-font"

    memory = classify_ciq_message("Out Of Memory")
    assert memory is not None and memory.kind == "out-of-memory"

    assert classify_ciq_message("Something else happened") is None


def test_ciq_log_is_parsed_with_its_header() -> None:
    text = (
        "header:\n"
        "  Language-Code: eng\n"
        "  Part-Number: 006-B4024-00\n"
        "entries:\n"
        "  - Message: \"Invalid Font Specified\"\n"
        "  - Message: \"Symbol Not Found Error: Could not find symbol 'x'\"\n"
        "  - Message: \"Unrelated warning\"\n"
    )
    header, errors = parse_ciq_log(text)
    assert header["Language-Code"] == "eng"
    assert {error.kind for error in errors} == {"invalid-font", "symbol-not-found"}
    assert len(errors) == 2, "one per class keeps the report readable"


def test_ciq_log_falls_back_to_raw_lines() -> None:
    header, errors = parse_ciq_log("not: [valid yaml\nOut Of Memory\n")
    assert header == {}
    assert [error.kind for error in errors] == ["out-of-memory"]


def test_ciq_log_is_collected_into_the_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USER", "root")
    monkeypatch.delenv("WINEUSERNAME", raising=False)
    driver = _driver(tmp_path)
    log = (
        tmp_path
        / "prefix"
        / "drive_c"
        / "users"
        / "root"
        / "Temp"
        / "com.garmin.connectiq"
        / "GARMIN"
        / "APPS"
        / "LOGS"
        / "CIQ_LOG.YML"
    )
    log.parent.mkdir(parents=True)
    log.write_text(
        "header:\n  Language-Code: eng\nentries:\n"
        "  - Message: \"Symbol Not Found Error: could not find 'x'\"\n",
        encoding="utf-8",
    )
    driver._collect_ciq_log()
    assert driver.ciq_log is not None and driver.ciq_log.is_file()
    assert driver.ciq_header["Language-Code"] == "eng"
    errors = driver.runtime_errors()
    assert [error.kind for error in errors] == ["symbol-not-found"]
    assert (driver.artifact_dir / "logs" / "ciq-log-errors.txt").is_file()


def test_ciq_log_absent_is_not_an_error(tmp_path: Path) -> None:
    driver = _driver(tmp_path)
    driver._collect_ciq_log()
    assert driver.runtime_errors() == ()
    assert driver.ciq_log is None


def _probe_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, difference: int):
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 409, "height": 600}
    )
    monkeypatch.setattr(
        "e2e.sim.wine.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)

    def fake_screenshot(path: Path) -> Path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(png_bytes(4, 4))
        return Path(path)

    monkeypatch.setattr(driver, "screenshot", fake_screenshot)
    monkeypatch.setattr(
        driver, "region_pixel_difference", lambda before, after, region: difference
    )
    driver._run = FakeRun()  # type: ignore[method-assign]
    return driver


def test_input_delivery_probe_reports_a_changed_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _probe_driver(tmp_path, monkeypatch, difference=5000)
    detail = driver.probe_input_delivery()
    assert "changed the screen area by 5000 px" in detail
    assert "input reaches the app" in detail


def test_input_delivery_probe_calls_a_silent_press_an_app_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _probe_driver(tmp_path, monkeypatch, difference=0)
    with pytest.raises(DriverError) as error:
        driver.probe_input_delivery(timeout_s=0.05)
    message = str(error.value)
    assert "delivery to the app" in message
    assert "glance" in message
    assert "logs/driver.log" in message


def test_the_wine_driver_provisions_languages_with_a_measured_index(
    tmp_path: Path,
) -> None:
    """`Language` is a numeric index, so provisioning is per code, not per driver."""
    driver = _driver(tmp_path)
    assert driver.can_provision_language("eng") is True  # the simulator default
    assert driver.can_provision_language("deu") is True  # measured index 7
    assert driver.can_provision_language("pol") is True  # measured index 13
    assert driver.can_provision_language("xyz") is False  # not a simulator language
    assert SimulatorDriver.provisions_language is True


class _IdleApp:
    """A launched app that keeps running."""

    returncode = None
    pid = 1234

    def poll(self) -> Optional[int]:
        return None


def _ready_driver(
    tmp_path: Path,
    language: str = "deu",
    url: str = "http://shim/",
    title: str = "CIQ Simulator - Fenix 7 (6.0.3)",
) -> WineSimulatorDriver:
    driver = _driver(tmp_path)
    driver._started = True
    driver._language = language
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._window_name = lambda window: title  # type: ignore[method-assign]
    driver._app_process = _IdleApp()  # type: ignore[assignment]
    settings = load_settings("default").with_overrides({"url_0": url})
    driver._settings = settings
    return driver


def _driver_log(driver: WineSimulatorDriver) -> str:
    return (driver.artifact_dir / "logs" / "driver.log").read_text(encoding="utf-8")


def test_wait_ready_confirms_the_device_from_the_title(tmp_path: Path) -> None:
    driver = _ready_driver(tmp_path, language="deu")
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=deu"),
        (0.0, "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2"),
    ]
    driver.wait_ready(5.0)
    assert "device title confirms" in _driver_log(driver)


def test_wait_ready_warns_when_the_title_never_names_the_device(
    tmp_path: Path,
) -> None:
    """The trace stream is the gate; the title is corroboration only."""
    driver = _ready_driver(tmp_path, language="deu", title="CIQ Simulator")
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=deu"),
        (0.0, "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2"),
    ]
    driver.wait_ready(5.0)
    log = _driver_log(driver)
    assert "does not name a device" in log
    assert "E2E trace proves the app is running" in log


def test_wait_ready_warns_on_a_different_device_title(tmp_path: Path) -> None:
    driver = _ready_driver(
        tmp_path, language="deu", title="CIQ Simulator - Edge 540 (6.0.3)"
    )
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=deu"),
        (0.0, "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2"),
    ]
    driver.wait_ready(5.0)
    log = _driver_log(driver)
    assert "does not match" in log
    assert "Edge 540" in log


def test_wait_ready_fails_when_the_provisioned_language_is_not_applied(
    tmp_path: Path,
) -> None:
    """A wrong index must stop the run: asserting another language proves nothing."""
    driver = _ready_driver(tmp_path)
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=eng"),
        (0.0, "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2"),
    ]
    with pytest.raises(DriverError) as error:
        driver.wait_ready(5.0)
    message = str(error.value)
    assert "requested 'deu'" in message
    assert "language-scan" in message


def test_wait_ready_records_a_probed_index_without_expecting_a_code(
    tmp_path: Path,
) -> None:
    """`language-scan` launches with an explicit index and only reads the result."""
    driver = _ready_driver(tmp_path, language="index:17")
    driver._expect_language = False
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=ita"),
        (0.0, "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2"),
    ]
    driver.wait_ready(5.0)
    assert driver.last_language == "ita"
    assert "probed language" in _driver_log(driver)


def test_wait_ready_confirms_the_requested_language_when_it_matches(
    tmp_path: Path,
) -> None:
    driver = _ready_driver(tmp_path, language="eng")
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=eng"),
        (0.0, "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2"),
    ]
    driver.wait_ready(5.0)
    assert "language confirmed: eng" in _driver_log(driver)


def test_wait_ready_accepts_the_matching_language(tmp_path: Path) -> None:
    driver = _ready_driver(tmp_path, language="deu")
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=deu"),
        (0.0, "E2E:CONNECTION;mode=BLE"),
        (0.0, "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2"),
    ]
    driver.wait_ready(5.0)


def test_wait_ready_rejects_the_config_error_view_when_a_url_was_seeded(
    tmp_path: Path,
) -> None:
    driver = _ready_driver(tmp_path, language="eng")
    driver._settings_file = tmp_path / "settings.txt"
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=eng"),
        (0.0, "E2E:ERRORVIEW;id=UI:CONFIG;code=-"),
        (0.0, "E2E:VIEW;category=error;view=ErrorView;depth=2"),
    ]
    with pytest.raises(DriverError) as error:
        driver.wait_ready(5.0)
    message = str(error.value)
    assert "UI:CONFIG" in message
    assert ENV_SETTINGS_FILE in message


def test_wait_ready_allows_a_deliberate_config_error_view(tmp_path: Path) -> None:
    driver = _ready_driver(tmp_path, language="eng", url="")
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=eng"),
        (0.0, "E2E:ERRORVIEW;id=UI:CONFIG;code=-"),
        (0.0, "E2E:VIEW;category=error;view=ErrorView;depth=2"),
    ]
    driver.wait_ready(5.0)


# ---------------------------------------------------------------------------
# contract reporting
# ---------------------------------------------------------------------------


class _FakeDriver(SimulatorDriver):
    """Minimal driver used to check contract reporting, not the simulator."""

    name = "fake"
    supports_events = True
    supports_screenshots = True

    def __init__(
        self,
        fail_start: bool = False,
        press_changes_view: bool = True,
        start_category: str = "sitemap",
        repeat_view: bool = False,
        effect_plan: Optional[List[bool]] = None,
        deltas: Optional[List[int]] = None,
    ) -> None:
        self.fail_start = fail_start
        self.press_changes_view = press_changes_view
        self.repeat_view = repeat_view
        self.effect_plan = list(effect_plan) if effect_plan is not None else None
        self.deltas = list(deltas) if deltas is not None else None
        self.stopped = False
        self._lines: List[str] = [
            f"E2E:VIEW;category={start_category};view=StartView;depth=1"
        ]

    def prepare(self, prg: Path) -> None:
        self.prg = prg

    def start(self, device, settings, mode="ble", language="eng") -> None:  # type: ignore[no-untyped-def]
        if self.fail_start:
            raise NotImplementedError("fake driver cannot start")
        self.started = True
        self.started_mode = mode
        self.started_language = language

    def wait_ready(self, timeout_s: float) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True

    def press(self, button: Button, count: int = 1) -> None:
        if not self.press_changes_view or button is not Button.ENTER:
            return
        if self.repeat_view:
            self._lines.append(self._lines[-1])
        else:
            self._lines.append("E2E:VIEW;category=sitemap;view=ActionMenu;depth=3")

    def relaunch_app(self, *, clear_data: bool = True, timeout_s: float = 90.0) -> bool:
        self._lines.append("E2E:SITEMAP;result=success;code=200;bytes=1")
        self._lines.append("E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1")
        return True

    def press_measured(self, button: Button, count: int = 1, **kwargs: object):
        effect = "" if not self.press_changes_view else "view sitemap/ActionMenu"
        if self.repeat_view:
            effect = ""
        if self.effect_plan is not None:
            effect = "view sitemap/ActionMenu" if self.effect_plan.pop(0) else ""

        class Result:
            mechanism = "keyboard"
            requests = 1

            def describe(self) -> str:
                return (
                    f"mechanism=keyboard (keysym {button.value}); "
                    f"effect={effect or 'none'}; requests +1"
                )

        Result.effect = effect  # type: ignore[attr-defined]
        return Result()

    def tap(self, x: int, y: int, count: int = 1) -> None:
        pass

    def screenshot(self, path: Path) -> Path:
        Path(path).write_bytes(png_bytes(4, 4))
        return Path(path)

    def pixel_difference(self, before: Path, after: Path) -> int:
        return 0

    def region_pixel_difference(
        self, before: Path, after: Path, region: ScreenRect
    ) -> int:
        if self.deltas:
            return self.deltas.pop(0) if len(self.deltas) > 1 else self.deltas[0]
        return 0

    def screen_region(self, window: Optional[str] = None) -> ScreenRect:
        return ScreenRect(0, 0, 4, 4)

    def probe_input_delivery(self, timeout_s: float = 8.0) -> str:
        return "the fake select press changed the screen"

    def events(self) -> Sequence[str]:
        return tuple(self._lines)


def test_contract_reports_not_implemented_without_a_prg(tmp_path: Path) -> None:
    cases = run_contract(_FakeDriver(), tmp_path)
    by_name = {case.name: case.status for case in cases}
    assert by_name["interface"] == PASS
    assert by_name["prepare"] == PASS
    assert by_name["start"] == NOT_IMPLEMENTED
    assert by_name["screenshot"] == NOT_IMPLEMENTED
    assert by_name["events"] == NOT_IMPLEMENTED
    assert by_name["stop"] == PASS
    assert exit_code(cases) == 2
    start = next(case for case in cases if case.name == "start")
    assert "--prg" in start.detail


def test_contract_blocks_the_rest_when_start_fails(tmp_path: Path) -> None:
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    cases = run_contract(_FakeDriver(fail_start=True), tmp_path, prg=prg)
    by_name = {case.name: case for case in cases}
    assert by_name["start"].status == NOT_IMPLEMENTED
    assert by_name["wait_ready"].status == NOT_IMPLEMENTED
    assert "blocked by start" in by_name["wait_ready"].detail
    assert summary(cases)[FAIL] == 0
    assert exit_code(cases) == 2


def test_contract_runs_every_case_with_a_real_prg(tmp_path: Path) -> None:
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    driver = _FakeDriver()
    cases = run_contract(driver, tmp_path, prg=prg)
    assert {case.status for case in cases} == {PASS}
    assert driver.stopped
    assert exit_code(cases) == 0


def test_device_is_derived_from_the_prg_name(tmp_path: Path) -> None:
    """A build for another device must not be launched as the default one."""
    prg = tmp_path / "openhab-garmin-fenix847mm.prg"
    prg.write_bytes(b"prg")
    derived = device_from_prg(prg)
    assert derived is not None and derived.id == "fenix847mm"
    assert device_from_prg(tmp_path / "app.prg") is None


def test_contract_refuses_a_prg_device_mismatch(tmp_path: Path) -> None:
    prg = tmp_path / "openhab-garmin-fenix847mm.prg"
    prg.write_bytes(b"prg")
    with pytest.raises(ContractError) as error:
        run_contract(_FakeDriver(), tmp_path, prg=prg, device_id="fenix6pro")
    message = str(error.value)
    assert "fenix847mm" in message and "fenix6pro" in message
    assert "Drop --device" in message


def test_contract_requires_a_device_for_an_unrecognised_prg_name(tmp_path: Path) -> None:
    prg = tmp_path / "my-build.prg"
    prg.write_bytes(b"prg")
    with pytest.raises(ContractError) as error:
        run_contract(_FakeDriver(), tmp_path, prg=prg)
    assert "--device" in str(error.value)


def test_contract_accepts_an_unrecognised_name_with_an_explicit_device(
    tmp_path: Path,
) -> None:
    prg = tmp_path / "my-build.prg"
    prg.write_bytes(b"prg")
    cases = run_contract(_FakeDriver(), tmp_path, prg=prg, device_id="fenix7")
    assert {case.status for case in cases} == {PASS}


def test_contract_reports_a_glance_only_app_as_app_entry_failure(
    tmp_path: Path,
) -> None:
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    cases = run_contract(
        _FakeDriver(start_category="glance"), tmp_path, prg=prg, device_id=None
    )
    by_name = {case.name: case for case in cases}
    assert by_name["app-entry"].status == FAIL
    detail = by_name["app-entry"].detail
    assert "glance" in detail
    assert "Down" in detail and "select" in detail
    assert "logs/driver.log" in detail


def test_contract_passes_app_entry_on_a_real_view(tmp_path: Path) -> None:
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    cases = run_contract(_FakeDriver(), tmp_path, prg=prg, device_id=None)
    by_name = {case.name: case for case in cases}
    assert by_name["app-entry"].status == PASS


def test_contract_fails_a_press_that_repeats_the_same_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-rendered glance is not evidence that a press did anything."""
    import e2e.contract as contract_module

    monkeypatch.setattr(contract_module, "PRESS_EFFECT_TIMEOUT", 0.05)
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    cases = run_contract(
        _FakeDriver(repeat_view=True), tmp_path, prg=prg, device_id=None
    )
    by_name = {case.name: case for case in cases}
    assert by_name["press-effect"].status == FAIL
    assert "no different VIEW" in by_name["press-effect"].detail
    assert "repeated view does not count" in by_name["press-effect"].detail


def test_contract_press_cases_report_the_input_path(tmp_path: Path) -> None:
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    cases = run_contract(_FakeDriver(), tmp_path, prg=prg, device_id=None)
    by_name = {case.name: case for case in cases}
    for button in Button:
        case = by_name[f"press[{button.value}]"]
        assert case.status == PASS
        assert "mechanism=keyboard" in case.detail
        assert "effect=" in case.detail
        assert "requests +1" in case.detail


def test_contract_marks_a_no_effect_press_as_evidence_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single no-op press is normal (nav at the top); the sequence decides."""
    import e2e.contract as contract_module

    monkeypatch.setattr(contract_module, "PRESS_EFFECT_TIMEOUT", 0.05)
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    cases = run_contract(
        _FakeDriver(press_changes_view=False, start_category="glance"),
        tmp_path,
        prg=prg,
        device_id=None,
    )
    by_name = {case.name: case for case in cases}

    for button in Button:
        case = by_name[f"press[{button.value}]"]
        assert case.status == SKIPPED
        assert "no effect in isolation" in case.detail
        assert "press-sequence case is the delivery verdict" in case.detail

    sequence = by_name["press-sequence"]
    assert sequence.status == FAIL
    assert "produced no effect" in sequence.detail
    assert "still on the glance" in sequence.detail
    assert "--key DOWN=" in sequence.detail
    assert "pointer line" in sequence.detail


def test_press_sequence_is_evidence_on_an_open_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On an open app a page-down is navigation, so a still view is not a failure."""
    import e2e.contract as contract_module

    monkeypatch.setattr(contract_module, "PRESS_EFFECT_TIMEOUT", 0.05)
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    cases = run_contract(
        _FakeDriver(press_changes_view=False, start_category="sitemap"),
        tmp_path,
        prg=prg,
        device_id=None,
    )
    by_name = {case.name: case for case in cases}
    sequence = by_name["press-sequence"]
    assert sequence.status == SKIPPED
    assert "not the glance" in sequence.detail
    assert "ordinary navigation" in sequence.detail
    assert "judges input on the open app" in sequence.detail


def test_contract_press_sequence_passes_when_a_later_press_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two DOWNs move a list even though the first DOWN at the top does not."""
    import e2e.contract as contract_module

    monkeypatch.setattr(contract_module, "PRESS_EFFECT_TIMEOUT", 0.05)
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    # Six per-button presses, then the sequence's first DOWN: all no-ops.
    plan = [False] * 7 + [True]
    cases = run_contract(
        _FakeDriver(effect_plan=plan), tmp_path, prg=prg, device_id=None
    )
    by_name = {case.name: case for case in cases}
    assert by_name["press[DOWN]"].status == SKIPPED
    assert by_name["press-sequence"].status == PASS
    assert "sequence delivered" in by_name["press-sequence"].detail
    assert by_name["press-sequence"].detail.count("|") == 1


def test_contract_skips_presses_on_a_buttonless_device(tmp_path: Path) -> None:
    """A touch-only device has no buttons: tapping is its input, not a failure."""
    prg = tmp_path / "openhab-garmin-venu2.prg"
    prg.write_bytes(b"prg")
    cases = run_contract(_FakeDriver(), tmp_path, prg=prg, device_id=None)
    by_name = {case.name: case for case in cases}

    assert by_name["press[UP]"].status == SKIPPED
    assert "no physical buttons" in by_name["press[UP]"].detail
    assert by_name["press-sequence"].status == SKIPPED
    assert "tap covers its input" in by_name["press-sequence"].detail
    assert by_name["tap"].status == PASS


def test_press_effect_fails_when_the_app_never_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fenix847mm false pass: no E2E output means nothing is attributable."""
    import e2e.contract as contract_module

    monkeypatch.setattr(contract_module, "PRESS_EFFECT_TIMEOUT", 0.05)
    prg = tmp_path / "openhab-garmin-fenix847mm.prg"
    prg.write_bytes(b"prg")
    driver = _FakeDriver()
    driver._lines = []
    cases = run_contract(driver, tmp_path, prg=prg, device_id=None)
    by_name = {case.name: case for case in cases}
    assert by_name["press-effect"].status == FAIL
    assert "no E2E: console output at all" in by_name["press-effect"].detail
    assert "launch problem" in by_name["press-effect"].detail


def test_press_effect_must_beat_the_idle_screen_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A screen that redraws on its own must not turn a dead press into a pass."""
    import e2e.contract as contract_module

    monkeypatch.setattr(contract_module, "PRESS_EFFECT_TIMEOUT", 0.05)
    monkeypatch.setattr(contract_module, "PRESS_BASELINE_INTERVAL", 0.0)
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")

    # The idle pairs change by 5000 px and so does every press: indistinguishable.
    idle = run_contract(
        _FakeDriver(press_changes_view=False, deltas=[5000]),
        tmp_path,
        prg=prg,
        device_id=None,
    )
    idle_cases = {case.name: case for case in idle}
    assert idle_cases["press-effect"].status == FAIL
    assert "baseline 5000 px" in idle_cases["press-effect"].detail
    assert "beaten" not in idle_cases["press-effect"].detail
    assert "beat that" in idle_cases["press-effect"].detail

    # A press that changes far more than the idle baseline is a real effect.
    moving = run_contract(
        _FakeDriver(press_changes_view=False, deltas=[5000, 60000]),
        tmp_path,
        prg=prg,
        device_id=None,
    )
    moving_cases = {case.name: case for case in moving}
    assert moving_cases["press-effect"].status == PASS
    assert "screen change 60000px" in moving_cases["press-effect"].detail
    assert "idle baseline 5000px" in moving_cases["press-effect"].detail


def test_app_ready_with_no_console_output_names_a_launch_problem(
    tmp_path: Path,
) -> None:
    """fenix847mm printed nothing: that is packaging, not the glance transition."""
    driver = _ready_driver(tmp_path, language="eng")
    driver._events = []
    with pytest.raises(DriverError) as error:
        driver.wait_ready(0.05)
    message = str(error.value)
    assert "no console output at all" in message
    assert "launch or packaging problem" in message
    assert "monkeydo <prg> <device>" in message


def test_app_ready_with_output_but_no_trace_points_at_the_build(
    tmp_path: Path,
) -> None:
    driver = _ready_driver(tmp_path, language="eng")
    driver._events = [(0.0, "some other output")]
    with pytest.raises(DriverError) as error:
        driver.wait_ready(0.05)
    message = str(error.value)
    assert "no `E2E:` line" in message
    assert "not this E2E build" in message


def test_language_scan_refuses_a_read_only_checkout_before_scanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 25-minute scan must not end in an OSError traceback."""
    from e2e.cli import _check_writable

    def refuse(*args: object, **kwargs: object):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(Path, "open", refuse)
    message = _check_writable(tmp_path / "tests" / "config" / "map.json")
    assert message is not None
    assert "Read-only file system" in message
    assert "--output /tmp/languages.json" in message


def test_contract_cli_mode_and_language_reach_the_driver(tmp_path: Path) -> None:
    """A plain-HTTP stack needs `--mode wifi`; `--language` keeps the index path."""
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    driver = _FakeDriver()
    run_contract(
        driver,
        tmp_path,
        prg=prg,
        device_id=None,
        mode="wifi",
        language="eng",
    )
    assert driver.started_mode == "wifi"
    assert driver.started_language == "eng"


def test_contract_defaults_to_ble_and_german(tmp_path: Path) -> None:
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    driver = _FakeDriver()
    run_contract(driver, tmp_path, prg=prg, device_id=None)
    assert driver.started_mode == "ble"
    assert driver.started_language == "deu"


def test_a_language_mismatch_still_launches_the_app_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The language error must not hide a launch failure behind it."""
    driver = _ready_driver(tmp_path)
    driver._definition = SimulatorDefinition(display=None, keys=())
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=eng"),
        (0.0, "E2E:VIEW;category=glance;view=GlanceSitemapView;depth=1"),
    ]
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RESPONSE_WAIT_S", 0.1)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RETRY_BUDGET_S", 5.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RETRY_INTERVAL_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_KEY_GAP_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.FOCUS_SETTLE_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_DWELL_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_SETTLE_MAX_S", 0.0)
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    with pytest.raises(DriverError) as error:
        driver.wait_ready(0.05)

    message = str(error.value)
    assert "language 'eng'" in message or "reported system language" in message
    log = _driver_log(driver)
    assert "language mismatch (deferred)" in log
    assert "launch retry 1:" in log, "the launch ran before the language error"


def test_the_ini_language_is_reported_after_start(
    tmp_path: Path,
) -> None:
    """The written index and the file's state after start are both in the log."""
    driver = _ready_driver(tmp_path, language="deu")
    ini = tmp_path / "simulator.ini"
    ini.write_text("LastUsedDevice=fenix7\nLanguage=7\n", encoding="utf-8")
    driver._ini_path = ini
    driver._record_ini_language_after_start()
    log = _driver_log(driver)
    assert "simulator.ini after start: Language=7" in log
    assert "expected 'deu'" in log

    ini.write_text("LastUsedDevice=fenix7\n", encoding="utf-8")
    driver._record_ini_language_after_start()
    assert "simulator.ini after start: Language=absent" in _driver_log(driver)


def test_the_pointer_guard_accepts_a_window_inside_the_simulator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wine creates several X windows; one inside the simulator is the simulator."""
    driver = _driver(tmp_path)
    driver._window = "42"
    monkeypatch.setattr(driver, "_root_window_id", lambda: "543")
    monkeypatch.setattr(
        driver,
        "_geometry",
        lambda window: {
            "x": 4,
            "y": 30,
            "width": 409,
            "height": 600,
        }
        if window == "42"
        else {"x": 10, "y": 40, "width": 342, "height": 566},
    )

    class DifferentPidRun(FakeRun):
        def __call__(self, command, **kwargs):  # noqa: ANN001, ANN003
            if command[:2] == ["xdotool", "getwindowpid"]:
                pid = "111\n" if command[-1] == "42" else "222\n"
                return subprocess.CompletedProcess(list(command), 0, pid, "")
            return super().__call__(command, **kwargs)

    driver._run = DifferentPidRun()  # type: ignore[method-assign]

    driver._verify_pointer_window("42", 99)  # no raise: 99's rect is inside 42
    assert "lies inside the simulator window" in _driver_log(driver)


def test_launch_waits_for_a_dwell_and_a_stable_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Waiting for the first VIEW is not the same as waiting for input."""
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=eng"),
        (0.0, "E2E:VIEW;category=glance;view=GlanceSitemapView;depth=1"),
    ]
    driver._app_started_at = time.monotonic() - 30.0
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 400, "height": 405}
    )
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RESPONSE_WAIT_S", 0.1)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RETRY_BUDGET_S", 5.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RETRY_INTERVAL_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_KEY_GAP_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.FOCUS_SETTLE_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_DWELL_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_SETTLE_MAX_S", 10.0)
    shots = []

    def fake_screenshot(path):  # noqa: ANN001
        shots.append(path)
        return path

    monkeypatch.setattr(driver, "screenshot", fake_screenshot)
    monkeypatch.setattr(driver, "pixel_difference", lambda a, b: 0)
    driver._run = FakeRun()  # type: ignore[method-assign]

    driver.launch(timeout_s=5.0)

    log = _driver_log(driver)
    assert "settling: waiting until 0" in log or "settle complete" in log
    assert "the screen is stable" in log
    assert len(shots) >= 2, "two captures are compared"


def test_keyboard_hold_sends_keydown_and_keyup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A toolkit that polls key state sees a held key, not an instantaneous tap."""
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    driver.press(Button.DOWN, 1, mechanism="keyboard-hold")

    commands = [command[1] for command in fake.commands]
    assert "keydown" in commands and "keyup" in commands
    assert "key" not in commands
    log = _driver_log(driver)
    assert "command: xdotool keydown --clearmodifiers Down" in log
    assert "command: xdotool keyup --clearmodifiers Down" in log


def test_the_exact_key_argv_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--window`, `--clearmodifiers` or `--delay` change what the simulator gets."""
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    driver._run = FakeRun()  # type: ignore[method-assign]

    driver.press(Button.DOWN, 1, mechanism="keyboard")

    assert (
        "command: xdotool key --clearmodifiers --repeat 1 --delay 150 Down"
        in _driver_log(driver)
    )


def test_mouse_hold_presses_and_releases_the_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 409, "height": 600}
    )
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    fake = FakeRun()
    driver._run = fake  # type: ignore[method-assign]

    driver.press(Button.ENTER, 1, mechanism="mouse-hold")

    commands = [command[1] for command in fake.commands]
    assert "mousedown" in commands and "mouseup" in commands
    assert "click" not in commands
    log = _driver_log(driver)
    assert "command: xdotool mousedown 1" in log
    assert "command: xdotool mouseup 1" in log


def test_keydown_and_keyup_command_builders() -> None:
    assert xdotool_keydown_command("Down") == [
        "xdotool",
        "keydown",
        "--clearmodifiers",
        "Down",
    ]
    assert xdotool_keyup_command("Down") == [
        "xdotool",
        "keyup",
        "--clearmodifiers",
        "Down",
    ]


def test_launch_retries_until_the_app_responds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delivery is nondeterministic on the guest; the loop is the fix."""
    import subprocess as _subprocess

    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=eng"),
        (0.0, "E2E:VIEW;category=glance;view=GlanceSitemapView;depth=1"),
    ]
    driver._app_started_at = time.monotonic()
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr(
        driver, "_geometry", lambda window: {"x": 0, "y": 0, "width": 409, "height": 600}
    )
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RESPONSE_WAIT_S", 0.1)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RETRY_BUDGET_S", 10.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_RETRY_INTERVAL_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_KEY_GAP_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.FOCUS_SETTLE_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_DWELL_S", 0.0)
    monkeypatch.setattr("e2e.sim.wine.LAUNCH_SETTLE_MAX_S", 0.0)

    class EventuallyRun(FakeRun):
        presses = 0

        def __call__(self, command, **kwargs):  # noqa: ANN001, ANN003
            completed = super().__call__(command, **kwargs)
            if command[1] in ("click", "key", "keydown"):
                type(self).presses += 1
                if type(self).presses == 8:  # third retry lands
                    driver._events.append((0.0, "E2E:CONNECTION;mode=BLE"))
                    driver._events.append(
                        (0.0, "E2E:VIEW;category=sitemap;view=LoadingView;depth=1")
                    )
            return completed

    driver._run = EventuallyRun()  # type: ignore[method-assign]

    assert driver.launch(timeout_s=20.0) is True
    log = _driver_log(driver)
    assert "launch retry 1: variant=declared hit areas" in log
    assert "launch retry 2: variant=keyboard taps" in log
    assert "launch retry 3: variant=keyboard keydown/keyup" in log
    assert "opened the app" in log
    assert "nondeterministic rather than wrong" not in log


def test_the_selection_probe_reports_a_moved_or_still_glance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One page down either moves the glance selection or it does not."""
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._window = "42"
    driver._definition = _definition(tmp_path)
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator - Fenix 7")
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)
    monkeypatch.setattr(driver, "screenshot", lambda path: Path(path))
    monkeypatch.setattr(
        driver, "region_pixel_difference", lambda before, after, region: 4200
    )
    driver._run = FakeRun()  # type: ignore[method-assign]

    driver._probe_selection(time.monotonic() + 5.0)

    log = _driver_log(driver)
    assert "selection probe at" in log
    assert "changed the screen by 4200px" in log
    assert "the glance selection moved" in log
    assert "early signal only" in log


def test_the_device_runtime_version_is_read_from_compiler_json(
    tmp_path: Path,
) -> None:
    """The title number is the emulated device runtime, not the simulator build."""
    product = tmp_path / "Devices" / "fenix7"
    product.mkdir(parents=True)
    (product / "compiler.json").write_text(
        json.dumps({"displayName": "Fenix 7", "connectIQVersion": "5.2.0"}),
        encoding="utf-8",
    )
    assert read_device_runtime_version(tmp_path / "Devices", "fenix7") == "5.2.0"
    assert read_device_runtime_version(tmp_path / "missing", "fenix7") == ""


def test_night_mode_is_written_as_a_numeric_ini_key() -> None:
    """`nightMode` is read at start like the mode keys, so it is not a switch."""
    on = ini_updates("fenix7", "ble", None, night=True)
    off = ini_updates("fenix7", "ble", None, night=False)
    assert on[NIGHT_MODE_KEY] == "1"
    assert off[NIGHT_MODE_KEY] == "0"
    assert night_mode_value(True) == "1"


def test_the_night_environment_flag_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv(ENV_NIGHT_MODE, value)
        assert _night_from_environment() is True, value
    for value in ("", "0", "false", "no"):
        monkeypatch.setenv(ENV_NIGHT_MODE, value)
        assert _night_from_environment() is False, value
    monkeypatch.delenv(ENV_NIGHT_MODE, raising=False)
    assert _night_from_environment() is False


def test_restart_in_mode_rewrites_the_ini_and_relaunches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restart proves start-up mode; it must say so and reuse the same PRG."""
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._settings = load_settings("default")
    driver._prg = tmp_path / "app.prg"
    driver._language_value = "7"
    driver._mode = "ble"
    calls: List[str] = []
    written: List[tuple] = []

    monkeypatch.setattr(driver, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(driver, "_start_simulator", lambda: calls.append("simulator"))
    monkeypatch.setattr(
        driver, "_seed_settings", lambda settings: calls.append("settings")
    )
    monkeypatch.setattr(
        driver, "_launch_app", lambda device, **kwargs: calls.append("launch")
    )
    monkeypatch.setattr(
        driver,
        "_write_simulator_ini",
        lambda device, mode, language: written.append((mode, language)),
    )

    def ready(timeout: float) -> None:
        calls.append("ready")
        driver._events.append((0.0, "E2E:CONNECTION;mode=WIFI"))

    monkeypatch.setattr(driver, "wait_ready", ready)

    assert driver.restart_in_mode("wifi") is True
    assert written == [("wifi", "7")]
    assert calls == ["stop", "simulator", "settings", "launch", "ready"]
    assert driver._mode == "wifi"
    log = _driver_log(driver)
    assert "restarting in mode 'wifi' (was 'ble')" in log
    assert "proves start-up mode, not a mid-run transition" in log


def test_restart_in_mode_reports_not_ready_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._settings = load_settings("default")
    driver._prg = tmp_path / "app.prg"
    driver._mode = "ble"
    monkeypatch.setattr(driver, "stop", lambda: None)
    monkeypatch.setattr(driver, "_start_simulator", lambda: None)
    monkeypatch.setattr(driver, "_seed_settings", lambda settings: None)
    monkeypatch.setattr(driver, "_launch_app", lambda device, **kwargs: None)
    monkeypatch.setattr(driver, "_write_simulator_ini", lambda *a: None)

    def refuse(timeout: float) -> None:
        raise DriverError("app-ready: no console output at all")

    monkeypatch.setattr(driver, "wait_ready", refuse)

    assert driver.restart_in_mode("offline") is False
    assert "did not become ready" in _driver_log(driver)


def test_probe_menus_reports_the_popup_it_finds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _pressing_driver(tmp_path, monkeypatch)
    children = [[1], [1], [1, 9]]
    monkeypatch.setattr(
        driver, "_children_ids", lambda: children.pop(0) if children else [1, 9]
    )
    monkeypatch.setattr(driver, "screenshot", lambda path: Path(path))
    monkeypatch.setattr(driver, "pixel_difference", lambda a, b: 0)
    monkeypatch.setattr(driver, "_click_at", lambda *a, **k: None)
    monkeypatch.setattr(
        driver, "_geometry_text", lambda window: "120x200 at (+40,+30)"
    )

    report = driver.probe_menus(timeout_s=5.0)

    assert report.opened is True
    assert report.popup_window == 9
    assert report.popup_geometry == "120x200 at (+40,+30)"
    assert "reachable with synthetic clicks" in report.verdict()
    log = _driver_log(driver)
    assert "menu probe: a click on the menu bar opened a popup" in log
    assert "menu probe step" in log


def test_probe_menus_reports_a_silent_menu_bar_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _pressing_driver(tmp_path, monkeypatch)
    monkeypatch.setattr(driver, "_children_ids", lambda: [1])
    monkeypatch.setattr(driver, "screenshot", lambda path: Path(path))
    monkeypatch.setattr(driver, "pixel_difference", lambda a, b: 0)
    monkeypatch.setattr(driver, "_click_at", lambda *a, **k: None)

    report = driver.probe_menus(timeout_s=5.0)

    assert report.opened is False
    assert report.steps, "every candidate point is recorded"
    assert "does not take synthetic clicks on its menu bar" in report.verdict()
    assert "restart-in-mode remains the documented capability" in report.verdict()


def test_the_app_storage_clear_is_limited_to_the_sitemap_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the cache may go: SETTINGS and Settings are what the app initialises from."""
    driver = _driver(tmp_path)
    root = tmp_path / "prefix/drive_c/users/root/Temp/com.garmin.connectiq/GARMIN"
    data = root / "APPS/DATA"
    temp = root / "APPS/TEMP"
    settings = root / "APPS/SETTINGS"
    device_settings = root / "Settings"
    logs = root / "APPS/LOGS"
    for path in (data, temp, settings, device_settings, logs):
        path.mkdir(parents=True)
    (data / "sitemapJson.bin").write_text("cached", encoding="utf-8")
    (settings / "OPENHAB-GARMIN-FENIX7.SET").write_text("table", encoding="utf-8")
    (device_settings / "device.ini").write_text("keep", encoding="utf-8")
    (logs / "CIQ_LOG.YML").write_text("keep me", encoding="utf-8")
    monkeypatch.delenv(ENV_KEEP_APP_DATA, raising=False)

    removed = driver.clear_app_data()

    assert len(removed) == 2, removed
    assert not data.exists() and not temp.exists()
    assert (settings / "OPENHAB-GARMIN-FENIX7.SET").is_file(), "property table survives"
    assert settings.is_dir(), "APPS/SETTINGS survives"
    assert device_settings.is_dir(), "device settings survive"
    assert (logs / "CIQ_LOG.YML").read_text(encoding="utf-8") == "keep me"
    log = _driver_log(driver)
    assert "cleared the app's sitemap cache" in log
    assert "APPS/SETTINGS, GARMIN/Settings and\nLOGS untouched" in log or (
        "APPS/SETTINGS, GARMIN/Settings and LOGS untouched" in log.replace("\n", " ")
    )


def test_the_storage_clear_can_be_switched_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    data = (
        tmp_path
        / "prefix/drive_c/users/root/Temp/com.garmin.connectiq/GARMIN/APPS/DATA"
    )
    data.mkdir(parents=True)
    monkeypatch.setenv(ENV_KEEP_APP_DATA, "1")

    assert driver.clear_app_data() == []
    assert data.is_dir()
    assert "keeping the app's cached sitemap" in _driver_log(driver)


def test_a_relaunch_requires_a_fresh_sitemap_not_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._started = True
    driver._device = _device("fenix7")
    driver._prg = tmp_path / "app.prg"
    monkeypatch.setattr(driver, "_stop_app", lambda: None)
    monkeypatch.setattr(driver, "clear_app_data", lambda: [])
    monkeypatch.setattr(driver, "_launch_app", lambda device, **kwargs: None)
    monkeypatch.setattr(
        driver,
        "wait_ready",
        lambda timeout: driver._events.append(
            (0.0, "E2E:SITEMAP;result=success;code=200;bytes=1")
        ),
    )
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)

    assert driver.relaunch_app() is True
    log = _driver_log(driver)
    assert "relaunch sitemap result: success" in log


def test_key_override_option_sets_the_driver_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from e2e.cli import _apply_key_overrides, build_parser

    parser = build_parser()
    args = parser.parse_args(
        ["contract-test", "--key", "ENTER=Return", "--key", "menu=shift+F10"]
    )
    monkeypatch.delenv(f"{ENV_KEY_PREFIX}ENTER", raising=False)
    monkeypatch.delenv(f"{ENV_KEY_PREFIX}MENU", raising=False)
    assert _apply_key_overrides(args) is None
    assert os.environ[f"{ENV_KEY_PREFIX}ENTER"] == "Return"
    assert os.environ[f"{ENV_KEY_PREFIX}MENU"] == "shift+F10"


def test_key_override_option_rejects_bad_input() -> None:
    from e2e.cli import _apply_key_overrides, build_parser

    parser = build_parser()
    for item in ("ENTER", "NOPE=Return", "ENTER="):
        args = parser.parse_args(["contract-test", "--key", item])
        message = _apply_key_overrides(args)
        assert message and "--key" in message, item


def test_the_run_command_accepts_key_overrides() -> None:
    from e2e.cli import build_parser

    args = build_parser().parse_args(
        ["run", "smoke/launch-and-poll", "--dry-run", "--key", "ENTER=Return"]
    )
    assert args.key == ["ENTER=Return"]


def test_contract_fails_a_press_that_has_no_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A press that changes nothing must not pass, even if xdotool succeeded."""
    import e2e.contract as contract_module

    monkeypatch.setattr(contract_module, "PRESS_EFFECT_TIMEOUT", 0.05)
    prg = tmp_path / "openhab-garmin-fenix7.prg"
    prg.write_bytes(b"prg")
    cases = run_contract(
        _FakeDriver(press_changes_view=False), tmp_path, prg=prg, device_id=None
    )
    by_name = {case.name: case for case in cases}
    assert by_name["press-effect"].status == FAIL
    assert "no different VIEW" in by_name["press-effect"].detail
    assert exit_code(cases) == 1


# ---------------------------------------------------------------------------
# deterministic capture
# ---------------------------------------------------------------------------

def test_the_pointer_is_parked_at_a_fixed_point() -> None:
    from e2e.sim.wine import POINTER_PARK_POINT, xdotool_mousemove_window_command

    assert POINTER_PARK_POINT == (2, 2)
    assert xdotool_mousemove_window_command("42", *POINTER_PARK_POINT) == [
        "xdotool", "mousemove", "--window", "42", "2", "2",
    ]


def test_screenshot_parks_the_pointer_before_capturing(tmp_path, monkeypatch) -> None:
    """The status strip prints the pointer position, so capture must pin it."""
    driver = _pressing_driver(tmp_path, monkeypatch)
    commands: List[List[str]] = []

    def fake_run(command, *, check=False, timeout=30.0, action=""):
        commands.append(list(command))
        if command[0] == "import":
            Path(command[-1]).write_bytes(png_bytes(409, 600))
        return subprocess.CompletedProcess(list(command), 0, "", "")

    monkeypatch.setattr(driver, "_run", fake_run)
    driver.screenshot(tmp_path / "shot.png")

    park = [index for index, command in enumerate(commands) if "mousemove" in command]
    capture = [index for index, command in enumerate(commands) if command[0] == "import"]
    assert park and capture
    assert park[0] < capture[0], "the pointer must be parked before the capture"
    assert commands[park[0]][:2] == ["xdotool", "mousemove"]
    assert "--window" in commands[park[0]]