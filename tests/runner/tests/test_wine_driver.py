"""Wine simulator driver tests that need no Wine, no display and no SDK.

Everything here is either a pure helper (window-title parsing, command
construction, INI and properties rewriting, tap mapping, error messages) or a
driver path that fails *before* it touches the simulator. The real proof is a
run on the guest, which is a scenario, not a unit test.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pytest

from e2e.config import Device, find_device
from e2e.contract import (
    FAIL,
    NOT_IMPLEMENTED,
    PASS,
    exit_code,
    run_contract,
    summary,
)
from e2e.sdk import SdkLayout
from e2e.settings import load_settings
from e2e.sim.base import Button, DriverError, SimulatorDriver
from e2e.sim.null import png_bytes
from e2e.sim.wine import (
    DEFAULT_KEYS,
    ENV_KEY_PREFIX,
    ENV_SCREEN_RECT,
    ENV_SETTINGS_FILE,
    ENV_STARTUP_TIMEOUT,
    ScreenRect,
    WindowTitle,
    import_command,
    ini_updates,
    key_bindings,
    monkeydo_command,
    parse_properties,
    parse_screen_rect,
    parse_window_geometry,
    parse_window_title,
    png_problem,
    read_device_display_name,
    read_device_keys,
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
    xdotool_click_command,
    xdotool_focus_commands,
    xdotool_geometry_command,
    xdotool_key_command,
    xdotool_search_command,
    xdpyinfo_command,
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

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.commands: List[List[str]] = []

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
        return subprocess.CompletedProcess(
            list(command), self.returncode, self.stdout, self.stderr
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
        json.dumps({"keys": {"up": 38, "down": 40, "enter": 13, "back": 27}}),
        encoding="utf-8",
    )
    assert read_device_display_name(root, "fr965") == "Forerunner® 965"
    assert read_device_keys(root, "fr965")["up"] == 38
    assert read_device_display_name(root, "missing") == ""
    assert read_device_keys(None, "fr965") == {}


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
    assert xdotool_key_command("42", "Down", 2) == [
        "xdotool",
        "key",
        "--window",
        "42",
        "--clearmodifiers",
        "--repeat",
        "2",
        "--delay",
        "150",
        "Down",
    ]
    assert xdotool_click_command("42", 10, 20, 3)[:7] == [
        "xdotool",
        "mousemove",
        "--window",
        "42",
        "10",
        "20",
        "click",
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


def test_count_and_delay_are_clamped() -> None:
    assert xdotool_key_command("1", "Up", 0)[6] == "1"
    assert xdotool_key_command("1", "Up", 1, delay_ms=-5)[8] == "0"


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
    ble = ini_updates("fenix7", "ble", "deu")
    assert ble == {
        "LastUsedDevice": "fenix7",
        "Language": "deu",
        "BleState": "true",
        "WifiState": "false",
    }
    assert ini_updates("fenix7", "wifi", "eng")["WifiState"] == "true"
    offline = ini_updates("fenix7", "offline", "eng")
    assert (offline["BleState"], offline["WifiState"]) == ("false", "false")
    with pytest.raises(DriverError):
        ini_updates("fenix7", "carrier-pigeon", "eng")


def test_existing_ini_keys_are_rewritten_case_insensitively() -> None:
    update = update_ini(
        "[Simulator]\nlanguage = eng\nBleState=false\n",
        {"Language": "deu", "BleState": "true"},
    )
    assert update.text.splitlines() == [
        "[Simulator]",
        "language=deu",
        "BleState=true",
    ]
    assert set(update.written) == {"Language", "BleState"}
    assert update.appended == ()


def test_missing_ini_keys_are_appended_under_the_simulator_section() -> None:
    update = update_ini("[Simulator]\nLanguage=eng\n", {"WifiState": "true"})
    assert update.text.splitlines() == ["[Simulator]", "WifiState=true", "Language=eng"]
    assert update.appended == ("WifiState",)


def test_ini_created_from_nothing_has_a_section() -> None:
    update = update_ini("", {"Language": "deu"})
    assert update.text.splitlines() == ["[Simulator]", "Language=deu"]


def test_ini_update_is_idempotent() -> None:
    once = update_ini("", {"Language": "deu", "BleState": "true"})
    twice = update_ini(once.text, {"Language": "deu", "BleState": "true"})
    assert twice.text == once.text
    assert set(twice.written) == {"Language", "BleState"}


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
    assert key[3] == "42"
    assert key[-2:] == ["2", "Down"] or key[-1] == "Down"


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
    assert click[4:6] == ["208", "208"]


def test_seeding_raises_when_no_properties_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    called = {"discovery": False}

    def discovery(device: Device) -> None:
        called["discovery"] = True

    monkeypatch.delenv(ENV_SETTINGS_FILE, raising=False)
    driver._discovery_launch = discovery  # type: ignore[method-assign]
    with pytest.raises(DriverError) as error:
        driver._seed_settings(_device(), load_settings("default"))
    assert called["discovery"] is True
    assert ENV_SETTINGS_FILE in str(error.value)
    assert "configuration error view" in str(error.value)


def test_seeding_writes_and_round_trips_every_property(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    target = tmp_path / "prefix" / "settings.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("custom_0=keep\n", encoding="utf-8")
    monkeypatch.setenv(ENV_SETTINGS_FILE, str(target))
    settings = load_settings("default")
    driver._seed_settings(_device(), settings)
    document = parse_properties(target.read_text(encoding="utf-8"))
    assert document.properties["custom_0"] == "keep"
    for name, value in settings.properties.items():
        assert document.properties[name] == value
    assert driver.settings_file == target


def test_seeding_prefers_a_file_that_already_holds_the_app_keys(tmp_path: Path) -> None:
    driver = _driver(tmp_path)
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("other_0=1\n", encoding="utf-8")
    related = tmp_path / "related.txt"
    related.write_text("url_0=u/\nsitemap_0=s\n", encoding="utf-8")
    chosen = driver._choose_settings_file([unrelated, related], load_settings("default"))
    assert chosen == related


def test_device_window_timeout_names_the_title_it_saw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    driver._prg = tmp_path / "app.prg"
    driver._prg.write_bytes(b"prg")
    monkeypatch.setenv(ENV_STARTUP_TIMEOUT, "1")
    monkeypatch.setattr(driver, "_find_windows", lambda: ["42"])
    monkeypatch.setattr(driver, "_window_name", lambda window: "CIQ Simulator")
    monkeypatch.setattr(driver, "_error_windows", lambda: [])
    monkeypatch.setattr(
        "e2e.sim.wine.time.sleep", lambda seconds: None
    )

    class Idle:
        returncode = None
        pid = 1

        def poll(self) -> Optional[int]:
            return None

    driver._simulator_process = Idle()  # type: ignore[assignment]
    with pytest.raises(DriverError) as error:
        driver._await_device_window(_device())
    message = str(error.value)
    assert "CIQ Simulator" in message
    assert "link-devices" in message


def test_device_window_rejects_an_error_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    monkeypatch.setattr(driver, "_find_windows", lambda: ["42"])
    monkeypatch.setattr(driver, "_window_name", lambda window: "Error")
    monkeypatch.setattr(driver, "_error_windows", lambda: [])

    class Idle:
        returncode = None
        pid = 1

        def poll(self) -> Optional[int]:
            return None

    driver._simulator_process = Idle()  # type: ignore[assignment]
    with pytest.raises(DriverError) as error:
        driver._await_device_window(_device())
    assert "error dialog" in str(error.value)


def test_device_window_rejects_a_different_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    monkeypatch.setattr(driver, "_find_windows", lambda: ["42"])
    monkeypatch.setattr(
        driver, "_window_name", lambda window: "CIQ Simulator - Edge 540 (6.0.3)"
    )
    monkeypatch.setattr(driver, "_error_windows", lambda: [])

    class Idle:
        returncode = None
        pid = 1

        def poll(self) -> Optional[int]:
            return None

    driver._simulator_process = Idle()  # type: ignore[assignment]
    with pytest.raises(DriverError) as error:
        driver._await_device_window(_device("fenix7"))
    assert "different device" in str(error.value)
    assert "Edge 540" in str(error.value)


def test_separate_error_dialog_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver(tmp_path)
    monkeypatch.setattr(driver, "_find_windows", lambda: [])
    monkeypatch.setattr(driver, "_error_windows", lambda: ["99"])
    monkeypatch.setattr("e2e.sim.wine.time.sleep", lambda seconds: None)

    class Idle:
        returncode = None
        pid = 1

        def poll(self) -> Optional[int]:
            return None

    driver._simulator_process = Idle()  # type: ignore[assignment]
    with pytest.raises(DriverError) as error:
        driver._await_device_window(_device())
    assert "error dialog" in str(error.value)


class _IdleApp:
    """A launched app that keeps running."""

    returncode = None
    pid = 1234

    def poll(self) -> Optional[int]:
        return None


def _ready_driver(tmp_path: Path, language: str = "deu", url: str = "http://shim/") -> WineSimulatorDriver:
    driver = _driver(tmp_path)
    driver._started = True
    driver._language = language
    driver._app_process = _IdleApp()  # type: ignore[assignment]
    settings = load_settings("default").with_overrides({"url_0": url})
    driver._settings = settings
    return driver


def test_wait_ready_verifies_the_reported_language(tmp_path: Path) -> None:
    driver = _ready_driver(tmp_path)
    driver._events = [
        (0.0, "E2E:LANGUAGE;code=eng"),
        (0.0, "E2E:CONNECTION;mode=BLE"),
        (0.0, "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2"),
    ]
    with pytest.raises(DriverError) as error:
        driver.wait_ready(5.0)
    message = str(error.value)
    assert "eng" in message and "deu" in message


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

    def __init__(self, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.stopped = False

    def prepare(self, prg: Path) -> None:
        self.prg = prg

    def start(self, device, settings, mode="ble", language="eng") -> None:  # type: ignore[no-untyped-def]
        if self.fail_start:
            raise NotImplementedError("fake driver cannot start")
        self.started = True

    def wait_ready(self, timeout_s: float) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True

    def press(self, button: Button, count: int = 1) -> None:
        pass

    def tap(self, x: int, y: int, count: int = 1) -> None:
        pass

    def screenshot(self, path: Path) -> Path:
        Path(path).write_bytes(png_bytes(4, 4))
        return Path(path)

    def events(self) -> Sequence[str]:
        return ("E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2",)


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
    prg = tmp_path / "app.prg"
    prg.write_bytes(b"prg")
    cases = run_contract(_FakeDriver(fail_start=True), tmp_path, prg=prg)
    by_name = {case.name: case for case in cases}
    assert by_name["start"].status == NOT_IMPLEMENTED
    assert by_name["wait_ready"].status == NOT_IMPLEMENTED
    assert "blocked by start" in by_name["wait_ready"].detail
    assert summary(cases)[FAIL] == 0
    assert exit_code(cases) == 2


def test_contract_runs_every_case_with_a_real_prg(tmp_path: Path) -> None:
    prg = tmp_path / "app.prg"
    prg.write_bytes(b"prg")
    driver = _FakeDriver()
    cases = run_contract(driver, tmp_path, prg=prg)
    assert {case.status for case in cases} == {PASS}
    assert driver.stopped
    assert exit_code(cases) == 0
