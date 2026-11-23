"""Connect IQ SDK layout tests.

No SDK is needed: each case builds a temporary directory tree that mimics the
layout the runner accepts. These tests exist because the SDK is the one input
the harness cannot ship, so the validation must be clear about what is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e.sdk import ENV_VARS, SdkError, inspect_sdk, require_simulator, sdk_root


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
