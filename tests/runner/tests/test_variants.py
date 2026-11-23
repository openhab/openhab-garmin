"""Build-variant coverage tests (issue #366).

These tests are the automated coverage check: they resolve ``monkey.jungle``,
cross-check it against ``manifest.xml`` and fail when a variant combination has
neither a device in ``tests/config/devices.yaml`` nor an explicit waiver. They
need neither Docker nor the Connect IQ SDK.
"""

from __future__ import annotations

from typing import Dict

import pytest

from e2e.config import Device, load_devices
from e2e.variants import (
    DIMENSIONS,
    UI_DIMENSIONS,
    DeviceBuild,
    VariantError,
    Waiver,
    check_coverage,
    combinations,
    load_builds,
    load_waivers,
    manifest_products,
    ui_coverage_problems,
)


def _by_product() -> Dict[str, DeviceBuild]:
    return {build.product: build for build in load_builds()}


def _tags(device: Device) -> Dict[str, str]:
    return {tag.partition(":")[0]: tag.partition(":")[2] for tag in device.variants}


def test_jungle_and_manifest_declare_the_same_products() -> None:
    builds = load_builds()
    assert {build.product for build in builds} == set(manifest_products())
    assert len(builds) == 93


def test_every_combination_is_covered_or_waived() -> None:
    problems = check_coverage(load_builds(), load_devices(), load_waivers())
    assert not problems, "\n".join(str(problem) for problem in problems)


def test_every_device_annotation_matches_the_jungle() -> None:
    builds = _by_product()
    for device in load_devices():
        tags = _tags(device)
        expected = builds[device.product].variants
        assert tags == expected, device.id


def test_the_matrix_is_minimal() -> None:
    """One device per combination: adding a duplicate needs a deliberate swap."""
    seen: Dict[tuple, str] = {}
    for device in load_devices():
        key = tuple(_tags(device)[name] for name in DIMENSIONS)
        assert key not in seen, f"{device.id} duplicates {seen[key]}"
        seen[key] = device.id


def test_every_device_runs_the_cheap_protocol_tier() -> None:
    for device in load_devices():
        assert "protocol" in device.tiers_run, device.id


def test_ui_tier_covers_every_interaction_value() -> None:
    problems = ui_coverage_problems(load_builds(), load_devices())
    assert not problems, "\n".join(str(problem) for problem in problems)


def test_ui_dimensions_exclude_config() -> None:
    assert "config" not in UI_DIMENSIONS
    assert set(UI_DIMENSIONS) < set(DIMENSIONS)


def test_resolution_follows_variable_and_section_inheritance() -> None:
    builds = _by_product()

    # venu3 inherits its annotations from approachs50 rather than the base.
    assert builds["venu3"].variants["input"] == "touch"
    assert "exclForSettingsMenuItem" in builds["venu3"].exclude_annotations

    # edge840 inherits resourcePath from edge540 and rebuilds the same config.
    assert builds["edge840"].variants["edge_resources"] == "short"
    assert builds["edge840"].variants["config"] == "edge-x40-button"
    assert builds["edge840"].variants == builds["edge540"].variants

    # base.* provides the defaults for devices without their own section.
    assert builds["fenix7"].variants == builds["d2mach1"].variants
    assert builds["fenix6pro"].variants["ciq_tier"] == "pre400"


def test_resolution_rejects_an_unknown_config_directory(tmp_path) -> None:
    jungle = tmp_path / "monkey.jungle"
    jungle.write_text(
        "\n".join(
            [
                "srcBase=source/main",
                "srcUnnamed=source/config/base;source/config/device-brand-new",
                "base.excludeAnnotations=exclForCiq510Plus;exclForButton;exclForSettingsFooter;exclForScreenRound;exclForActionMenu",
                "base.resourcePath=resources/base",
                "one.excludeAnnotations=$(base.excludeAnnotations)",
                "one.resourcePath=$(base.resourcePath)",
                "one.sourcePath=$(srcUnnamed)",
            ]
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.xml"
    manifest.write_text(
        '<iq:manifest xmlns:iq="http://www.garmin.com/xml/connectiq">'
        '<iq:products><iq:product id="one"/></iq:products></iq:manifest>',
        encoding="utf-8",
    )
    with pytest.raises(VariantError) as error:
        load_builds(jungle, manifest)
    assert "device-brand-new" in str(error.value)


def test_manifest_jungle_mismatch_is_rejected(tmp_path) -> None:
    jungle = tmp_path / "monkey.jungle"
    jungle.write_text("one.resourcePath=resources/base\n", encoding="utf-8")
    manifest = tmp_path / "manifest.xml"
    manifest.write_text(
        '<iq:manifest xmlns:iq="http://www.garmin.com/xml/connectiq">'
        '<iq:products><iq:product id="two"/></iq:products></iq:manifest>',
        encoding="utf-8",
    )
    with pytest.raises(VariantError) as error:
        load_builds(jungle, manifest)
    assert "two" in str(error.value)


def test_missing_combination_is_reported_without_a_waiver() -> None:
    builds = load_builds()
    problems = check_coverage(builds, ())
    uncovered = [problem for problem in problems if problem.kind == "uncovered"]
    assert len(uncovered) == len(combinations(builds))
    assert all("has no device and no waiver" in problem.detail for problem in uncovered)


def test_waiver_covers_exactly_one_combination() -> None:
    builds = load_builds()
    combination = combinations(builds)[0]
    waiver = Waiver(variants=combination.variant_tags, reason="covered by a physical-only check")

    problems = check_coverage(builds, (), (waiver,))
    waived = [problem for problem in problems if problem.kind == "uncovered"]
    assert len(waived) == len(combinations(builds)) - 1
    assert all(combination.products[0] not in problem.detail for problem in waived)


def test_waiver_for_a_covered_combination_is_rejected() -> None:
    builds = load_builds()
    devices = load_devices()
    combination = combinations(builds)[0]
    waiver = Waiver(variants=combination.variant_tags, reason="not needed")

    problems = check_coverage(builds, devices, (waiver,))
    assert any(problem.kind == "unnecessary-waiver" for problem in problems)


def test_waiver_that_matches_no_combination_is_rejected() -> None:
    builds = load_builds()
    waiver = Waiver(
        variants=tuple(f"{name}:does-not-exist" for name in DIMENSIONS),
        reason="stale",
    )
    problems = check_coverage(builds, (), (waiver,))
    assert any(problem.kind == "stale-waiver" for problem in problems)


def test_incomplete_waiver_is_rejected() -> None:
    builds = load_builds()
    waiver = Waiver(variants=("ciq_tier:pre400",), reason="partial")
    problems = check_coverage(builds, (), (waiver,))
    assert any(problem.kind == "invalid-waiver" for problem in problems)


def test_waiver_without_a_reason_is_rejected() -> None:
    builds = load_builds()
    waiver = Waiver(variants=combinations(builds)[0].variant_tags, reason="")
    problems = check_coverage(builds, (), (waiver,))
    assert any("needs a reason" in problem.detail for problem in problems)


def test_approved_matrix_has_no_waivers() -> None:
    assert load_waivers() == ()
