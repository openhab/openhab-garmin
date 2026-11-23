"""App settings fixture tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from e2e.config import APP_SETTINGS_DIR
from e2e.network import load_network
from e2e.settings import (
    AppSettings,
    SettingsError,
    load_settings,
    resolve_tokens,
    validate_properties,
)


def test_repository_fixtures_load(monkeypatch) -> None:
    monkeypatch.delenv("E2E_SHIM_URL", raising=False)
    network = load_network()
    fixtures = sorted(APP_SETTINGS_DIR.glob("*.yaml"))
    assert fixtures, "no settings fixtures found"
    for path in fixtures:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        # The fixture must derive from the stack's endpoint, not hardcode one.
        assert raw["properties"]["url_0"] == "${E2E_SHIM_URL}/", path
        settings = load_settings(path.stem)
        assert settings.properties["url_0"] == network.shim_url + "/"
        assert settings.sitemap


def test_fixtures_point_at_the_shim(monkeypatch) -> None:
    monkeypatch.delenv("E2E_SHIM_URL", raising=False)
    settings = load_settings("default")
    assert settings.url == load_network().shim_url + "/"
    assert settings.sitemap == "garmin_e2e"


def test_shim_url_token_follows_the_stack_environment() -> None:
    # The bridge profile passes the service alias to the runner; the fixture
    # resolves to exactly the same value, so the app cannot drift from the
    # stack the runner inspects.
    assert resolve_tokens("${E2E_SHIM_URL}/", {"E2E_SHIM_URL": "http://shim:8080"}) == (
        "http://shim:8080/"
    )
    assert resolve_tokens("${E2E_SHIM_URL}/", {}) == load_network().shim_url + "/"


def test_unknown_settings_token_fails_closed() -> None:
    with pytest.raises(SettingsError) as error:
        resolve_tokens("${NO_SUCH_STACK_URL}/", {})
    assert "unresolved settings token" in str(error.value)


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
