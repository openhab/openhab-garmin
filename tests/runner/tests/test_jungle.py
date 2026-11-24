"""Device-scoped jungle generation and the compiler error it prevents.

``e2e build`` compiles a jungle generated for one device, exactly like
``.github/scripts/monkeyc-build``, so an SDK that only defines the device under
test is enough. These cases run against the repository's real ``monkey.jungle``
and need neither the SDK nor Docker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e import build as build_module
from e2e.build import BuildError, build_prg
from e2e.jungle import (
    JungleError,
    generate_device_jungle,
    invalid_qualifiers,
    qualifier_error,
)
from e2e.paths import repo_root
from e2e.sdk import SdkLayout
from e2e.variants import jungle_products, parse_jungle, resolve_device

JUNGLE = repo_root() / "monkey.jungle"

#: What ``monkeyc`` prints when the SDK's ``api.db`` lacks a device the jungle
#: names. The full jungle names edge550 but the vendored CI SDK does not.
QUALIFIER_FAILURE = (
    "ERROR: /work/monkey.jungle:449: 'edge550' is not a valid device / family qualifier.\n"
    "ERROR: /work/monkey.jungle:450: 'edge550' is not a valid device / family qualifier.\n"
)


def _generate(tmp_path: Path, device: str):
    return generate_device_jungle(JUNGLE, device, tmp_path / f"{device}.jungle")


def _properties(path: Path):
    return parse_jungle(path.read_text(encoding="utf-8"))


def _layout(tmp_path: Path) -> SdkLayout:
    monkeyc = tmp_path / "sdk" / "linux" / "bin" / "monkeyc"
    monkeyc.parent.mkdir(parents=True)
    monkeyc.write_text("#!/bin/sh\n", encoding="utf-8")
    return SdkLayout(
        root=tmp_path / "sdk",
        version="9.3.0",
        version_file=tmp_path / "sdk" / "version.txt",
        monkeyc=monkeyc,
        monkeydo=None,
        simulator=None,
    )


def test_generation_keeps_base_and_the_selected_device(tmp_path: Path) -> None:
    generated = _generate(tmp_path, "fenix847mm")
    properties = _properties(generated.path)

    assert generated.sections == ("base", "fenix847mm")
    assert "base.excludeAnnotations" in properties
    assert "base.sourcePath" in properties
    assert "fenix847mm.resourcePath" in properties
    assert "project.manifest" in properties, "project settings are not device-scoped"


def test_transitive_references_are_followed(tmp_path: Path) -> None:
    generated = _generate(tmp_path, "venu3")
    properties = _properties(generated.path)

    assert properties["venu3.excludeAnnotations"] == "$(approachs50.excludeAnnotations)"
    assert "approachs50.excludeAnnotations" in properties
    assert "approachs50.resourcePath" in properties
    assert generated.transitive == ("approachs50",)


def test_unrelated_device_sections_are_dropped(tmp_path: Path) -> None:
    generated = _generate(tmp_path, "venu3")
    properties = _properties(generated.path)

    assert "edge550" in JUNGLE.read_text(encoding="utf-8"), "the fixture must be meaningful"
    assert "edge550" not in generated.sections
    assert not any(key.startswith("edge550.") for key in properties)
    assert not any(key.startswith("fenix847mm.") for key in properties)


def test_generated_jungle_parses_as_a_section_list(tmp_path: Path) -> None:
    generated = _generate(tmp_path, "venu3")
    properties = _properties(generated.path)
    original = parse_jungle(JUNGLE.read_text(encoding="utf-8"))

    assert jungle_products(properties) == ("approachs50", "venu3")
    assert resolve_device(properties, "venu3") == resolve_device(original, "venu3")


def test_unknown_device_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(JungleError) as error:
        generate_device_jungle(JUNGLE, "notadevice", tmp_path / "out.jungle")

    assert "notadevice" in str(error.value)


def test_qualifier_failures_are_collected_once() -> None:
    assert invalid_qualifiers(QUALIFIER_FAILURE) == ("edge550",)
    assert invalid_qualifiers("ERROR: something else") == ()


def test_qualifier_message_names_the_device_and_the_escape_hatches(tmp_path: Path) -> None:
    message = qualifier_error("fenix7", ("edge550",), tmp_path / "monkey.jungle")

    assert "edge550" in message
    assert "narrower than the jungle" in message
    assert "--device" in message
    assert "tests/docs/sdk.md" in message


class _Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def _fake_monkeyc(monkeypatch: pytest.MonkeyPatch, captured: dict, completed=None) -> None:
    result = completed or _Completed()

    def fake_run(command, **kwargs):  # noqa: ANN001 - subprocess signature
        captured["command"] = list(command)
        output = Path(command[command.index("-o") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"PRG")
        return result

    monkeypatch.setattr(build_module.subprocess, "run", fake_run)


def test_build_generates_a_jungle_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    _fake_monkeyc(monkeypatch, captured)

    result = build_prg(_layout(tmp_path), "fenix847mm", tmp_path / "out")

    generated = tmp_path / "out" / "fenix847mm.jungle"
    assert result.jungle == generated
    assert result.jungle_generated is True
    assert generated.is_file()
    assert str(generated) in captured["command"]
    assert "generated" in result.jungle_note
    assert "fenix847mm.resourcePath" in _properties(generated)


def test_explicit_jungle_is_used_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    _fake_monkeyc(monkeypatch, captured)
    explicit = tmp_path / "full.jungle"
    explicit.write_text("base.resourcePath = resources\n", encoding="utf-8")

    result = build_prg(_layout(tmp_path), "fenix7", tmp_path / "out", jungle=explicit)

    assert result.jungle == explicit
    assert result.jungle_generated is False
    assert "explicit" in result.jungle_note
    assert str(explicit) in captured["command"]


def test_build_maps_a_qualifier_failure_to_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Failed(_Completed):
        returncode = 1
        stderr = QUALIFIER_FAILURE

    _fake_monkeyc(monkeypatch, {}, _Failed())

    with pytest.raises(BuildError) as error:
        build_prg(_layout(tmp_path), "fenix7", tmp_path / "out")

    message = str(error.value)
    assert "edge550" in message
    assert "narrower than the jungle" in message
    assert "--device" in message
    assert "tests/docs/sdk.md" in message
    assert "exit 1" not in message, "the raw compiler dump must not survive"
