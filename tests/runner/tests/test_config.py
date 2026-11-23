"""Device matrix tests.

The matrix is the approved build-variant subset from issue #366. These tests
pin the schema, the default entry and the variant/tier annotations; the
coverage itself is checked in ``test_variants.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from e2e.config import (
    ConfigError,
    Device,
    default_device_id,
    find_device,
    load_devices,
    read_sdk_version,
)


def test_matrix_loads() -> None:
    devices = load_devices()
    assert devices
    assert len({device.id for device in devices}) == len(devices)


def test_products_are_declared_in_the_manifest() -> None:
    manifest = (Path(__file__).resolve().parents[3] / "manifest.xml").read_text(encoding="utf-8")
    for device in load_devices():
        assert f'id="{device.product}"' in manifest, device.product


def test_default_device_exists() -> None:
    assert find_device(default_device_id()).id == default_device_id()


def test_device_inputs_and_capabilities_are_consistent() -> None:
    for device in load_devices():
        assert device.supports_buttons or device.supports_touch
        assert device.screen_width > 0 and device.screen_height > 0
        assert device.input in ("button", "touch", "touch+button")


def test_tiers_list_drives_the_primary_tier() -> None:
    devices = load_devices()
    assert all(device.tiers_run for device in devices)
    assert all(device.tier == device.tiers_run[0] for device in devices)
    assert {tier for device in devices for tier in device.tiers_run} == {"protocol", "ui"}
    assert any(len(device.tiers_run) == 2 for device in devices)


def test_variant_tags_are_optional_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "default": "one",
                "devices": [
                    {
                        "id": "one",
                        "product": "fenix7",
                        "input": "button",
                        "screen": {"shape": "round", "width": 260, "height": 260},
                    },
                    {
                        "id": "two",
                        "product": "venu3",
                        "input": "touch",
                        "screen": {"shape": "round", "width": 454, "height": 454},
                        "variants": ["input:touch", "screen:round", "edge_form:touch"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    devices = load_devices(path)
    assert devices[0].variants == ()
    assert devices[1].variants == ("input:touch", "screen:round", "edge_form:touch")


def test_invalid_variant_tag_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "devices": [
                    {
                        "id": "one",
                        "product": "fenix7",
                        "input": "button",
                        "screen": {"shape": "round", "width": 260, "height": 260},
                        "variants": ["only-a-word"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as error:
        load_devices(path)
    assert "dimension:value" in str(error.value)


def test_unknown_tier_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "devices": [
                    {
                        "id": "one",
                        "product": "fenix7",
                        "input": "button",
                        "tier": "full",
                        "screen": {"shape": "round", "width": 260, "height": 260},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_devices(path)


def test_invalid_tiers_list_is_rejected(tmp_path: Path) -> None:
    entry = {
        "id": "one",
        "product": "fenix7",
        "input": "button",
        "tiers": ["protocol", "full"],
        "screen": {"shape": "round", "width": 260, "height": 260},
    }
    path = tmp_path / "devices.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "devices": [entry]}), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_devices(path)


def test_duplicate_variant_dimension_is_rejected(tmp_path: Path) -> None:
    entry = {
        "id": "one",
        "product": "fenix7",
        "input": "button",
        "screen": {"shape": "round", "width": 260, "height": 260},
        "variants": ["screen:round", "screen:rectangular"],
    }
    path = tmp_path / "devices.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "devices": [entry]}), encoding="utf-8"
    )
    with pytest.raises(ConfigError) as error:
        load_devices(path)
    assert "more than once" in str(error.value)


def test_duplicate_device_ids_are_rejected(tmp_path: Path) -> None:
    entry = {
        "id": "one",
        "product": "fenix7",
        "input": "button",
        "screen": {"shape": "round", "width": 260, "height": 260},
    }
    path = tmp_path / "devices.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "devices": [entry, entry]}), encoding="utf-8"
    )
    with pytest.raises(ConfigError) as error:
        load_devices(path)
    assert "duplicate" in str(error.value)


def test_sdk_version_is_pinned() -> None:
    version = read_sdk_version()
    assert version.count(".") == 2
    assert version == "9.3.0"
