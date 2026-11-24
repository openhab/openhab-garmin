"""PRG compilation helpers.

The live run could not compile unattended, because ``monkeyc`` refuses to build
without a private key and nothing generated one. These cases pin the fix: a
throwaway key per invocation, inside the artifacts directory, gone afterwards.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from e2e import build as build_module
from e2e.build import BuildError, build_prg, ephemeral_developer_key
from e2e.sdk import SdkLayout

requires_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl is required to generate the key"
)


def _layout(tmp_path: Path) -> SdkLayout:
    monkeyc = tmp_path / "sdk" / "linux" / "bin" / "monkeyc"
    monkeyc.parent.mkdir(parents=True)
    monkeyc.write_text("#!/bin/sh\n", encoding="utf-8")
    jungle = tmp_path / "monkey.jungle"
    jungle.write_text("base.resourcePath = resources\n", encoding="utf-8")
    return SdkLayout(
        root=tmp_path / "sdk",
        version="9.3.0",
        version_file=tmp_path / "sdk" / "version.txt",
        monkeyc=monkeyc,
        monkeydo=None,
        simulator=None,
    )


@requires_openssl
def test_ephemeral_key_is_der_inside_the_artifacts_directory(tmp_path: Path) -> None:
    with ephemeral_developer_key(tmp_path) as key:
        assert key.parent == tmp_path / "keys"
        assert key.read_bytes()[:1] == b"\x30", "PKCS#8 DER is a SEQUENCE"
        if os.name == "posix":
            assert os.stat(key).st_mode & 0o777 == 0o600
        der = key
    assert not der.exists()
    assert not der.with_suffix(".pem").exists()


@requires_openssl
def test_every_invocation_gets_a_fresh_key(tmp_path: Path) -> None:
    with ephemeral_developer_key(tmp_path) as first:
        first_bytes, first_name = first.read_bytes(), first.name
    with ephemeral_developer_key(tmp_path) as second:
        second_bytes, second_name = second.read_bytes(), second.name
    assert first_bytes != second_bytes
    assert first_name != second_name


def test_missing_openssl_is_reported(tmp_path: Path) -> None:
    with pytest.raises(BuildError) as error:
        with ephemeral_developer_key(tmp_path, openssl=str(tmp_path / "no-openssl")):
            pass
    assert "openssl" in str(error.value)


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


def test_build_passes_the_developer_key_to_monkeyc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    _fake_monkeyc(monkeypatch, captured)
    key = tmp_path / "developer-key.der"
    result = build_prg(
        _layout(tmp_path),
        "fenix7",
        tmp_path / "out",
        jungle=tmp_path / "monkey.jungle",
        developer_key=key,
    )
    assert "-y" in captured["command"]
    assert str(key) in captured["command"]
    assert result.prg.is_file()


def test_build_without_a_key_leaves_the_flag_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    _fake_monkeyc(monkeypatch, captured)
    build_prg(
        _layout(tmp_path),
        "fenix7",
        tmp_path / "out",
        jungle=tmp_path / "monkey.jungle",
    )
    assert "-y" not in captured["command"]
