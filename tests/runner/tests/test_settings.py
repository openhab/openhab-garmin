"""App settings fixture tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from e2e.config import APP_SETTINGS_DIR
from e2e.settings import (
    AppSettings,
    SettingsError,
    load_settings,
    validate_properties,
)


def test_repository_fixtures_load() -> None:
    fixtures = sorted(APP_SETTINGS_DIR.glob("*.yaml"))
    assert fixtures, "no settings fixtures found"
    for path in fixtures:
        settings = load_settings(path.stem)
        assert settings.properties["url_0"].startswith("http://shim:")
        assert settings.sitemap


def test_fixtures_point_at_the_shim() -> None:
    settings = load_settings("default")
    assert settings.url == "http://shim:8080/"
    assert settings.sitemap == "garmin_e2e"


def test_unknown_fixture_lists_available_names() -> None:
    with pytest.raises(SettingsError) as error:
        load_settings("does-not-exist")
    assert "available:" in str(error.value)


def test_unknown_property_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text(
        yaml.safe_dump(
            {"properties": {"url_0": "http://shim:8080/", "sitemap_0": "x",
                            "restapi_0": True, "pollingInterval_0": 1000,
                            "pollingIntervall_0": 1}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(SettingsError) as error:
        load_settings("broken", directory=tmp_path)
    assert "unknown application property" in str(error.value)


def test_missing_required_property_is_rejected() -> None:
    with pytest.raises(SettingsError) as error:
        validate_properties({"url_0": "http://shim:8080/"})
    assert "missing required property" in str(error.value)


@pytest.mark.parametrize(
    "properties,expected",
    [
        ({"restapi_0": 1}, "must be a boolean"),
        ({"pollingInterval_0": True}, "must be an integer"),
        ({"url_0": 5}, "must be a string"),
        ({"pollingInterval_0": -1}, "pollingInterval_0 must be 0..10000"),
        ({"postCommandHoldTime_0": 60000}, "postCommandHoldTime_0 must be 0..10000"),
    ],
)
def test_invalid_values_are_rejected(properties: dict, expected: str) -> None:
    base = {"url_0": "http://shim:8080/", "sitemap_0": "garmin_e2e",
            "restapi_0": True, "pollingInterval_0": 1000}
    base.update(properties)
    with pytest.raises(SettingsError) as error:
        validate_properties(base)
    assert expected in str(error.value)


def test_overrides_merge_and_revalidate() -> None:
    settings = load_settings("default")
    merged = settings.with_overrides({"pollingInterval_0": 250})
    assert merged.properties["pollingInterval_0"] == 250
    assert settings.properties["pollingInterval_0"] == 1000
    with pytest.raises(SettingsError):
        settings.with_overrides({"pollingInterval_0": "fast"})


def test_settings_are_immutable() -> None:
    settings: AppSettings = load_settings("default")
    with pytest.raises(Exception):
        settings.properties["url_0"] = "http://elsewhere/"  # type: ignore[index]
