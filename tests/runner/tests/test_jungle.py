"""Device-scoped jungle generation and the compiler error it prevents.

``e2e build`` compiles a jungle generated for one device, exactly like
``.github/scripts/monkeyc-build``, so an SDK that only defines the device under
test is enough. These cases run against the repository's real ``monkey.jungle``
and need neither the SDK nor Docker.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e import build as build_module
from e2e.build import (
    BuildError,
    PROFILE_WATCH_APP,
    PROFILE_WITHOUT_GLANCE,
    PROFILE_WITH_GLANCE,
    build_prg,
    describe_profile,
    read_compiled_settings,
    write_watch_app_manifest,
)
from e2e.jungle import (
    JungleError,
    generate_device_jungle,
    invalid_qualifiers,
    qualifier_error,
    write_resource_override,
)
from e2e.paths import repo_root
from e2e.sdk import SdkLayout
from e2e.settings import load_settings
from e2e.variants import jungle_products, parse_jungle, resolve_device

JUNGLE = repo_root() / "monkey.jungle"
CHECKOUT = JUNGLE.parent.resolve()

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


def _relative_to_checkout(entry: str) -> str:
    return Path(entry).relative_to(CHECKOUT).as_posix()


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
    generated_paths = resolve_device(properties, "venu3")
    original_paths = resolve_device(original, "venu3")

    assert jungle_products(properties) == ("approachs50", "venu3")
    assert generated_paths["excludeAnnotations"] == original_paths["excludeAnnotations"]
    for prop in ("resourcePath", "sourcePath"):
        assert [_relative_to_checkout(entry) for entry in generated_paths[prop]] == list(
            original_paths[prop]
        )


def test_generated_jungle_is_path_independent(tmp_path: Path) -> None:
    """A jungle outside the checkout must not rely on its own location.

    ``monkeyc`` resolves a relative path in a jungle file against that file's
    directory, so ``project.manifest = manifest.xml`` in
    ``<artifacts>/build/<device>.jungle`` made a real compile fail with
    "The specified manifest file doesn't exist: manifest.xml".
    """
    output = tmp_path / "outside-the-checkout" / "venu3.jungle"
    generated = generate_device_jungle(JUNGLE, "venu3", output)
    properties = _properties(generated.path)

    manifest = Path(properties["project.manifest"])
    assert manifest.is_absolute()
    assert manifest.is_file()
    assert properties["project.typecheck"] == "3", "non-path settings stay untouched"

    original = parse_jungle(JUNGLE.read_text(encoding="utf-8"))
    for product in jungle_products(properties):
        resolved = resolve_device(properties, product)
        for prop in ("resourcePath", "sourcePath"):
            for entry in resolved[prop]:
                assert Path(entry).is_absolute(), (product, prop, entry)
        assert resolved["excludeAnnotations"] == resolve_device(original, product)[
            "excludeAnnotations"
        ], "annotation names are not paths and must not be rewritten"

    for key, value in properties.items():
        if "." in key or key.startswith("annot"):
            continue
        for entry in value.split(";"):
            if entry and not entry.startswith("$("):
                assert Path(entry).is_absolute(), (key, entry)


def test_settings_override_is_written_after_resbase(tmp_path: Path) -> None:
    """Properties are compiled in through a resource override, not a file."""
    settings = load_settings("default")
    output = tmp_path / "out" / "fenix847mm.jungle"
    generated = generate_device_jungle(JUNGLE, "fenix847mm", output, settings=settings)

    assert generated.resource_override is not None
    override = generated.resource_override
    assert override == output.parent / "resource-override-fenix847mm"
    entries = [entry.strip() for entry in _properties(generated.path)[
        "fenix847mm.resourcePath"
    ].split(";")]
    assert entries[0] == "$(resBase)"
    assert entries[1] == str(override), "the override must come right after resBase"
    assert Path(override).is_absolute()

    settings_xml = (override / "settings.xml").read_text(encoding="utf-8")
    for name, value in settings.properties.items():
        assert f'id="{name}"' in settings_xml
    assert (
        f'<property id="url_0" type="string">{settings.properties["url_0"]}</property>'
        in settings_xml
    )
    assert '<property id="restapi_0" type="boolean">true</property>' in settings_xml
    assert "<settingConfig" in settings_xml, "only defaults change, not the schema"


def test_no_override_is_written_without_settings(tmp_path: Path) -> None:
    generated = _generate(tmp_path, "fenix847mm")
    assert generated.resource_override is None
    assert not (tmp_path / "resource-override-fenix847mm").exists()


def test_unknown_property_in_the_fixture_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(JungleError) as error:
        write_resource_override(tmp_path / "override", {"not_a_property_0": "x"})
    assert "no <property> element" in str(error.value)


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


def _fake_monkeyc(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict,
    completed=None,
    settings_document: object = None,
) -> None:
    result = completed or _Completed()

    def fake_run(command, **kwargs):  # noqa: ANN001 - subprocess signature
        captured["command"] = list(command)
        output = Path(command[command.index("-o") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"PRG")
        if settings_document is not None:
            (output.with_name(f"{output.stem}-settings.json")).write_text(
                json.dumps(settings_document), encoding="utf-8"
            )
        return result

    monkeypatch.setattr(build_module.subprocess, "run", fake_run)


def test_build_asserts_the_compiled_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = load_settings("default")
    document = {
        "properties": [
            {"id": "url_0", "type": "string", "defaultValue": settings.properties["url_0"]},
            {
                "id": "sitemap_0",
                "type": "string",
                "defaultValue": settings.properties["sitemap_0"],
            },
        ]
    }
    captured: dict = {}
    _fake_monkeyc(monkeypatch, captured, settings_document=document)

    result = build_prg(_layout(tmp_path), "fenix7", tmp_path / "out", settings=settings)

    assert result.settings_json is not None
    assert result.settings_values["url_0"] == settings.properties["url_0"]
    generated = _properties(result.jungle)
    assert "resource-override-fenix7" in generated["fenix7.resourcePath"]


def test_build_fails_when_the_compiled_settings_do_not_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = load_settings("default")
    document = {"url_0": "http://wrong.example/", "sitemap_0": settings.properties["sitemap_0"]}
    _fake_monkeyc(monkeypatch, {}, settings_document=document)

    with pytest.raises(BuildError) as error:
        build_prg(_layout(tmp_path), "fenix7", tmp_path / "out", settings=settings)

    message = str(error.value)
    assert "url_0: compiled" in message
    assert "but the build requested" in message
    assert "resourcePath order" in message


def test_build_fails_when_the_settings_json_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_monkeyc(monkeypatch, {})
    with pytest.raises(BuildError) as error:
        build_prg(
            _layout(tmp_path), "fenix7", tmp_path / "out", settings=load_settings("default")
        )
    assert "did not write the compiled settings JSON" in str(error.value)


def test_explicit_jungle_cannot_carry_the_settings(tmp_path: Path) -> None:
    explicit = tmp_path / "full.jungle"
    explicit.write_text("base.resourcePath = resources\n", encoding="utf-8")
    with pytest.raises(BuildError) as error:
        build_prg(
            _layout(tmp_path),
            "fenix7",
            tmp_path / "out",
            jungle=explicit,
            settings=load_settings("default"),
        )
    message = str(error.value)
    assert "--jungle" in message and "--prg" in message


def test_compiled_settings_are_read_from_the_known_shapes(tmp_path: Path) -> None:
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"url_0": "u/", "version": 1}), encoding="utf-8")
    assert read_compiled_settings(flat) == {"url_0": "u/"}

    keyed = tmp_path / "keyed.json"
    keyed.write_text(
        json.dumps({"url_0": {"type": "string", "defaultValue": "v/"}}), encoding="utf-8"
    )
    assert read_compiled_settings(keyed) == {"url_0": "v/"}

    listed = tmp_path / "listed.json"
    listed.write_text(
        json.dumps({"settings": [{"id": "sitemap_0", "defaultValue": "home"}]}),
        encoding="utf-8",
    )
    assert read_compiled_settings(listed) == {"sitemap_0": "home"}


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


def test_watch_app_manifest_switches_only_the_type(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "manifest.xml").write_text(
        '<iq:manifest version="3"><iq:application id="x" type="widget" '
        'name="App" entry="OHApp" launcherIcon="@Drawable/Launcher"/></iq:manifest>',
        encoding="utf-8",
    )
    target = write_watch_app_manifest(checkout, tmp_path / "out")
    assert target == tmp_path / "out" / "manifest-watch-app.xml"
    text = target.read_text(encoding="utf-8")
    assert 'type="watch-app"' in text
    assert 'entry="OHApp"' in text
    assert 'launcherIcon="@Drawable/Launcher"' in text
    assert text.count("iq:application") == 1


def test_build_watch_app_profile_points_the_jungle_at_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    _fake_monkeyc(monkeypatch, captured)

    result = build_prg(
        _layout(tmp_path), "fenix847mm", tmp_path / "out", profile=PROFILE_WATCH_APP
    )

    assert result.profile == PROFILE_WATCH_APP
    properties = _properties(result.jungle)
    manifest = Path(properties["project.manifest"])
    assert manifest.name == "manifest-watch-app.xml"
    assert manifest.is_file() and 'type="watch-app"' in manifest.read_text(encoding="utf-8")
    assert (tmp_path / "out" / "source-no-glance").is_dir(), "the glance is stubbed"
    assert "watch-app" in result.profile_note


def test_profiles_describe_what_they_do_not_cover() -> None:
    for profile in (PROFILE_WATCH_APP, PROFILE_WITHOUT_GLANCE, PROFILE_WITH_GLANCE):
        assert profile in describe_profile(profile)
    assert "#396" in describe_profile(PROFILE_WATCH_APP)
    assert "NOT covered" in describe_profile(PROFILE_WATCH_APP)
    assert "own glance view" in describe_profile(PROFILE_WITH_GLANCE)


def test_unknown_profile_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BuildError) as error:
        build_prg(_layout(tmp_path), "fenix7", tmp_path / "out", profile="nope")
    assert "unknown build profile" in str(error.value)


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


def test_settings_override_follows_a_variable_that_carries_resbase(tmp_path: Path) -> None:
    """An Edge lists ``$(resEdgeShort)``, which expands to ``$(resBase);...``.

    Inserting the override at the front there put it before the settings it has
    to override, so edge540 compiled empty property defaults and the build failed
    its own assertion. The injection must follow the entry that *reaches*
    ``$(resBase)``, directly or through a variable.
    """
    settings = load_settings("default")
    output = tmp_path / "out" / "edge540.jungle"
    generated = generate_device_jungle(JUNGLE, "edge540", output, settings=settings)

    assert generated.resource_override is not None
    override = generated.resource_override
    entries = [entry.strip() for entry in _properties(generated.path)[
        "edge540.resourcePath"
    ].split(";")]
    assert entries[0] == "$(resEdgeShort)", "the device's own variable stays first"
    assert entries[1] == str(override), (
        "the override must follow the entry that reaches resBase"
    )
    assert entries.index(str(override)) < len(entries) - 1, (
        "and still come before the launcher-icon directory"
    )


def test_settings_override_is_appended_when_nothing_carries_resbase(tmp_path: Path) -> None:
    """With no settings directory in the path, last is the only safe position."""
    source = tmp_path / "custom.jungle"
    source.write_text(
        "base.resourcePath=resources/base\n"
        "custom.resourcePath=resources/icons\n"
        "custom.sourcePath=source\n",
        encoding="utf-8",
    )
    settings = load_settings("default")
    generated = generate_device_jungle(
        source, "custom", tmp_path / "out" / "custom.jungle", settings=settings
    )
    entries = [entry.strip() for entry in _properties(generated.path)[
        "custom.resourcePath"
    ].split(";")]
    assert entries[-1] == str(generated.resource_override)