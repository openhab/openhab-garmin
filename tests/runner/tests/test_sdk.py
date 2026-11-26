"""Connect IQ SDK layout tests.

No SDK is needed: each case builds a temporary directory tree that mimics the
layout the runner accepts. These tests exist because the SDK is the one input
the harness cannot ship, so the validation must be clear about what is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e.sdk import (
    ENV_DEVICES_ROOT,
    ENV_VARS,
    SdkError,
    device_definition_count,
    find_device_definitions,
    font_asset_count,
    fonts_dir,
    fonts_problem,
    inspect_sdk,
    is_device_definitions,
    link_devices,
    link_fonts,
    require_fonts,
    require_simulator,
    sdk_root,
)


def _touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sdk(root: Path, *, version: str = "9.3.0", layout: str = "split") -> Path:
    _touch(root / "version.txt", version + "\n")
    if layout == "split":
        _touch(root / "linux" / "bin" / "monkeyc", "#!/bin/sh\n")
        _touch(root / "linux" / "bin" / "monkeydo", "#!/bin/sh\n")
        _touch(root / "windows" / "bin" / "simulator.exe", "")
    else:
        _touch(root / "bin" / "monkeyc", "#!/bin/sh\n")
    return root


def test_split_layout_is_accepted(tmp_path: Path) -> None:
    layout = inspect_sdk(_sdk(tmp_path / "sdk"), expected_version="9.3.0")
    assert layout.version == "9.3.0"
    assert layout.monkeyc.name == "monkeyc"
    assert layout.monkeydo is not None
    assert layout.simulator is not None and layout.simulator.name == "simulator.exe"


def test_flat_layout_is_accepted(tmp_path: Path) -> None:
    layout = inspect_sdk(_sdk(tmp_path / "sdk", layout="flat"), expected_version="9.3.0")
    assert layout.monkeyc.parent.name == "bin"
    assert layout.simulator is None


def test_bin_version_file_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "sdk"
    _touch(root / "bin" / "version.txt", "9.3.0")
    _touch(root / "bin" / "monkeyc")
    assert inspect_sdk(root, expected_version="9.3.0").version == "9.3.0"


def test_version_mismatch_is_rejected_and_names_both_versions(tmp_path: Path) -> None:
    with pytest.raises(SdkError) as error:
        inspect_sdk(_sdk(tmp_path / "sdk", version="9.2.0"), expected_version="9.3.0")
    message = str(error.value)
    assert "9.2.0" in message and "9.3.0" in message


def test_missing_version_file_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "sdk"
    _touch(root / "linux" / "bin" / "monkeyc")
    with pytest.raises(SdkError) as error:
        inspect_sdk(root, expected_version="9.3.0")
    assert "does not look like a Connect IQ SDK" in str(error.value)


def test_missing_compiler_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "sdk"
    _touch(root / "version.txt", "9.3.0")
    with pytest.raises(SdkError) as error:
        inspect_sdk(root, expected_version="9.3.0")
    assert "Linux compiler" in str(error.value)


def test_missing_root_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SdkError):
        inspect_sdk(tmp_path / "nope", expected_version="9.3.0")


def test_require_simulator_explains_what_is_missing(tmp_path: Path) -> None:
    layout = inspect_sdk(_sdk(tmp_path / "sdk", layout="flat"), expected_version="9.3.0")
    with pytest.raises(SdkError) as error:
        require_simulator(layout)
    assert "simulator.exe" in str(error.value)
    assert "protocol-tier" in str(error.value)


def test_environment_variable_is_resolved(tmp_path: Path) -> None:
    root = _sdk(tmp_path / "sdk")
    assert sdk_root({ENV_VARS[0]: str(root)}) == root
    assert sdk_root({ENV_VARS[1]: str(root)}) == root


def test_preferred_variable_wins(tmp_path: Path) -> None:
    first = _sdk(tmp_path / "first")
    second = _sdk(tmp_path / "second")
    assert sdk_root({ENV_VARS[0]: str(first), ENV_VARS[1]: str(second)}) == first


def test_unset_environment_is_explained() -> None:
    with pytest.raises(SdkError) as error:
        sdk_root({})
    message = str(error.value)
    assert ENV_VARS[0] in message
    assert "not redistributable" in message


def test_directory_that_does_not_exist_is_explained(tmp_path: Path) -> None:
    with pytest.raises(SdkError) as error:
        sdk_root({ENV_VARS[0]: str(tmp_path / "missing")})
    assert "not a directory" in str(error.value)


def test_expected_version_defaults_to_the_pin(tmp_path: Path) -> None:
    layout = inspect_sdk(_sdk(tmp_path / "sdk", version="9.3.0"))
    assert layout.version == "9.3.0"


# ---------------------------------------------------------------------------
# device definitions
# ---------------------------------------------------------------------------


def _definitions(path: Path, devices: tuple = ("fenix7", "venu3")) -> Path:
    for device in devices:
        _touch(path / device / "compiler.json", "{}")
    return path


def test_device_definitions_at_the_sdk_root_are_found(tmp_path: Path) -> None:
    sdk = _sdk(tmp_path / "sdk")
    _definitions(sdk / "Devices")
    assert find_device_definitions(sdk, {}) == sdk / "Devices"
    assert device_definition_count(sdk / "Devices") == 2


def test_device_definitions_under_bin_are_found(tmp_path: Path) -> None:
    sdk = _sdk(tmp_path / "sdk", layout="flat")
    _definitions(sdk / "bin" / "devices")
    assert find_device_definitions(sdk, {}) == sdk / "bin" / "devices"


def test_device_definitions_are_found_by_the_bounded_walk(tmp_path: Path) -> None:
    sdk = _sdk(tmp_path / "sdk")
    nested = sdk / "windows" / "connectiq" / "Devices"
    _definitions(nested)
    assert find_device_definitions(sdk, {}) == nested


def test_directory_without_compiler_json_is_not_a_definition_root(tmp_path: Path) -> None:
    sdk = _sdk(tmp_path / "sdk")
    (sdk / "Devices" / "fenix7").mkdir(parents=True)
    assert not is_device_definitions(sdk / "Devices")
    assert find_device_definitions(sdk, {}) is None


def test_devices_mount_is_checked_before_the_sdk(tmp_path: Path) -> None:
    sdk = _sdk(tmp_path / "sdk")
    mount = _definitions(tmp_path / "mounted-devices")
    assert find_device_definitions(sdk, {"CIQ_DEVICES_MOUNT": str(mount)}) == mount


def test_devices_override_must_be_a_definition_root(tmp_path: Path) -> None:
    sdk = _sdk(tmp_path / "sdk")
    with pytest.raises(SdkError) as error:
        find_device_definitions(sdk, {ENV_DEVICES_ROOT: str(tmp_path / "absent")})
    assert ENV_DEVICES_ROOT in str(error.value)


def _home_env(tmp_path: Path) -> dict:
    """A fake home so linking never touches the real one during tests."""
    return {"HOME": str(tmp_path / "home"), "USER": "root"}


def test_link_devices_copies_and_re_links_idempotently(tmp_path: Path) -> None:
    sdk = _sdk(tmp_path / "sdk")
    _definitions(sdk / "Devices")
    target = tmp_path / "home" / ".Garmin" / "ConnectIQ" / "Devices"
    links = link_devices(sdk, target=target, environ=_home_env(tmp_path))
    assert links.linux == target
    assert (target / "fenix7" / "compiler.json").is_file()
    (target / "stale").mkdir()
    link_devices(sdk, target=target, environ=_home_env(tmp_path))
    assert not (target / "stale").exists()
    assert device_definition_count(target) == 2


def test_link_devices_stages_both_toolchain_locations(tmp_path: Path) -> None:
    """monkeyc reads $HOME/.Garmin; the Wine simulator reads its prefix.

    Linking only the Linux path disables the whole UI tier: simulator.exe -d
    <device> opens an Error window and no device.
    """
    sdk = _sdk(tmp_path / "sdk")
    _definitions(sdk / "Devices")
    env = _home_env(tmp_path)
    links = link_devices(sdk, environ=env)

    linux = tmp_path / "home" / ".Garmin" / "ConnectIQ" / "Devices"
    wine = (
        tmp_path
        / "home"
        / ".wine"
        / "drive_c"
        / "users"
        / "root"
        / "AppData"
        / "Roaming"
        / "Garmin"
        / "ConnectIQ"
        / "Devices"
    )
    assert links.linux == linux
    assert links.wine == wine
    for path in (linux, wine):
        assert (path / "fenix7" / "compiler.json").is_file()
        assert (path / "venu3" / "compiler.json").is_file()
    assert device_definition_count(links.wine) == 2


def test_link_devices_honours_an_explicit_wine_prefix(tmp_path: Path) -> None:
    sdk = _sdk(tmp_path / "sdk")
    _definitions(sdk / "Devices")
    prefix = tmp_path / "prefix"
    env = dict(_home_env(tmp_path), WINEPREFIX=str(prefix))
    links = link_devices(sdk, environ=env)
    assert links.wine == (
        prefix
        / "drive_c"
        / "users"
        / "root"
        / "AppData"
        / "Roaming"
        / "Garmin"
        / "ConnectIQ"
        / "Devices"
    )
    assert (links.wine / "fenix7" / "compiler.json").is_file()


def test_link_devices_explains_what_it_searched(tmp_path: Path) -> None:
    sdk = _sdk(tmp_path / "sdk")
    with pytest.raises(SdkError) as error:
        link_devices(sdk, target=tmp_path / "target", environ=_home_env(tmp_path))
    message = str(error.value)
    assert "Searched" in message and "CIQ_DEVICES_ROOT" in message
    assert not (tmp_path / "home" / ".wine").exists()


def test_inspect_sdk_reports_device_definitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_DEVICES_ROOT, raising=False)
    monkeypatch.delenv("CIQ_DEVICES_MOUNT", raising=False)
    sdk = _sdk(tmp_path / "sdk")
    _definitions(sdk / "Devices")
    layout = inspect_sdk(sdk, expected_version="9.3.0")
    assert layout.has_devices
    assert layout.devices == sdk / "Devices"


# ---------------------------------------------------------------------------
# device fonts
# ---------------------------------------------------------------------------

def _fonts(root: Path, *, faces: int = 2) -> Path:
    """A temporary font pack: the faces the simulator loads plus md5 sidecars."""
    root.mkdir(parents=True, exist_ok=True)
    for index in range(faces):
        _touch(root / f"FNT_TEST_{index}.cft", "compiled face")
        _touch(root / f"FNT_TEST_{index}.md5", "hash")
    return root


def test_font_asset_count_counts_faces_not_sidecars(tmp_path: Path) -> None:
    pack = _fonts(tmp_path / "fonts", faces=3)
    _touch(pack / "README.txt", "not a face")
    assert font_asset_count(pack) == 3
    assert font_asset_count(tmp_path / "absent") == 0


def test_fonts_problem_distinguishes_missing_empty_and_wrong(tmp_path: Path) -> None:
    assert "does not exist" in (fonts_problem(tmp_path / "absent") or "")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "is empty" in (fonts_problem(empty) or "")
    junk = tmp_path / "junk"
    _touch(junk / "readme.txt", "x")
    assert "no .cft or .ttf" in (fonts_problem(junk) or "")
    assert fonts_problem(_fonts(tmp_path / "ok")) is None


def test_require_fonts_explains_provenance_and_licence(tmp_path: Path) -> None:
    with pytest.raises(SdkError) as error:
        require_fonts(tmp_path / "absent", _home_env(tmp_path))
    message = str(error.value)
    assert "Invalid Font Specified" in message
    assert "%APPDATA%\\Garmin\\ConnectIQ\\Fonts" in message
    assert "never commit" in message
    assert "/opt/ciq-fonts" in message


def test_fonts_dir_honours_an_explicit_target(tmp_path: Path) -> None:
    env = dict(_home_env(tmp_path), GARMIN_FONTS_DIR=str(tmp_path / "elsewhere"))
    assert fonts_dir(env) == tmp_path / "elsewhere"


def test_link_fonts_stages_both_toolchain_locations(tmp_path: Path) -> None:
    source = _fonts(tmp_path / "host-fonts")
    env = _home_env(tmp_path)
    links = link_fonts(mount=source, environ=env)

    linux = tmp_path / "home" / ".Garmin" / "ConnectIQ" / "Fonts"
    wine = (
        tmp_path
        / "home"
        / ".wine"
        / "drive_c"
        / "users"
        / "root"
        / "AppData"
        / "Roaming"
        / "Garmin"
        / "ConnectIQ"
        / "Fonts"
    )
    assert links.linux == linux
    assert links.wine == wine
    assert links.source == source
    assert not linux.is_symlink(), (
        "the native target must be real files: the Wine simulator did not follow "
        "a symlink chain into the mount and still raised Invalid Font Specified"
    )
    for path in (linux, wine):
        assert font_asset_count(path) == 2


def test_link_fonts_replaces_a_stale_target(tmp_path: Path) -> None:
    source = _fonts(tmp_path / "host-fonts")
    env = _home_env(tmp_path)
    stale = _fonts(tmp_path / "home" / ".Garmin" / "ConnectIQ" / "Fonts", faces=0)
    _touch(stale / "old.cft", "stale")
    link_fonts(mount=source, environ=env)
    assert not (stale / "old.cft").exists()
    assert font_asset_count(stale) == 2


def test_link_fonts_keeps_a_populated_target_in_place(tmp_path: Path) -> None:
    """An operator who already copied the pack into $HOME must not lose it."""
    source = _fonts(tmp_path / "home" / ".Garmin" / "ConnectIQ" / "Fonts")
    links = link_fonts(environ=_home_env(tmp_path))
    assert links.source == source
    assert font_asset_count(source) == 2
    assert not source.is_symlink()


def test_link_fonts_explains_when_nothing_is_found(tmp_path: Path) -> None:
    with pytest.raises(SdkError) as error:
        link_fonts(environ=_home_env(tmp_path))
    message = str(error.value)
    assert "no Connect IQ device fonts" in message
    assert "Invalid Font Specified" in message
