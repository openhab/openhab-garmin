"""Static Compose validation tests.

Docker is not required: ``validate_compose`` only inspects the document. The
cases below encode the requirements of issue #365 so a future edit cannot
quietly drop the read-only fixture mount, the SDK mount or the exclusive
profiles.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from e2e import compose as compose_module
from e2e.config import COMPOSE_DIR


def _document() -> dict:
    return copy.deepcopy(compose_module.load_compose())


def _env() -> dict:
    return compose_module.read_env_file(COMPOSE_DIR / ".env.example")


def test_repository_compose_validates() -> None:
    problems = compose_module.validate_compose()
    assert problems == [], "\n".join(problems)


def test_compose_file_and_example_exist() -> None:
    assert compose_module.COMPOSE_FILE.is_file()
    assert compose_module.ENV_EXAMPLE.is_file()


def test_required_services_are_present() -> None:
    services = _document()["services"]
    for name in compose_module.REQUIRED_SERVICES:
        assert name in services


def test_missing_service_is_reported() -> None:
    document = _document()
    del document["services"]["shim"]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("services.shim is missing" in problem for problem in problems)


def test_openhab_profiles_are_exclusive() -> None:
    document = _document()
    document["services"]["openhab-dev"]["profiles"] = ["stable", "dev"]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("profiles must be exactly ['dev']" in problem for problem in problems)


def test_fixture_directories_must_be_read_only() -> None:
    document = _document()
    document["services"]["openhab-stable"]["volumes"] = [
        "openhab-stable-conf:/openhab/conf",
        "../fixtures/openhab/conf/items:/openhab/conf/items",
        "../fixtures/openhab/conf/sitemaps:/openhab/conf/sitemaps:ro",
        "openhab-stable-userdata:/openhab/userdata",
    ]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("fixtures/openhab/conf/items read-only" in problem for problem in problems)


def test_fixture_directories_must_be_mounted() -> None:
    document = _document()
    document["services"]["openhab-stable"]["volumes"] = [
        "openhab-stable-conf:/openhab/conf",
        "openhab-stable-userdata:/openhab/userdata",
    ]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("fixtures/openhab/conf/items" in problem for problem in problems)


def test_conf_must_be_a_writable_named_volume() -> None:
    document = _document()
    document["services"]["openhab-stable"]["volumes"] = [
        "../fixtures/openhab/conf/items:/openhab/conf/items:ro",
        "../fixtures/openhab/conf/sitemaps:/openhab/conf/sitemaps:ro",
        "openhab-stable-userdata:/openhab/userdata",
    ]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("writable conf volume" in problem for problem in problems)


def test_healthcheck_must_not_depend_on_wget() -> None:
    document = _document()
    document["services"]["openhab-stable"]["healthcheck"]["test"] = [
        "CMD-SHELL",
        "wget -q -O /dev/null http://127.0.0.1:8080/rest/ || exit 1",
    ]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("must use curl" in problem for problem in problems)


def test_dev_override_points_the_shim_at_openhab_dev() -> None:
    document = compose_module.load_compose(compose_module.DEV_OVERRIDE_FILE)
    shim_env = document["services"]["shim"]["environment"]
    assert shim_env["OPENHAB_UPSTREAM"] == "http://openhab-dev:8080"
    assert compose_module.validate_dev_override() == []


def test_dev_override_is_validated(tmp_path: Path) -> None:
    path = tmp_path / "compose.dev.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "shim": {"environment": {"OPENHAB_UPSTREAM": "http://openhab-stable:8080"}},
                    "runner": {"environment": {"E2E_OPENHAB_URL": "http://openhab-stable:8080"}},
                }
            }
        ),
        encoding="utf-8",
    )
    problems = compose_module.validate_dev_override(path)
    assert any("openhab-dev" in problem for problem in problems)


def test_missing_dev_override_is_reported(tmp_path: Path) -> None:
    problems = compose_module.validate_dev_override(tmp_path / "nope.yaml")
    assert any("missing dev override" in problem for problem in problems)


def test_env_example_uses_the_host_sdk_variable() -> None:
    env = _env()
    assert "CIQ_SDK_HOST_PATH" in env
    assert "CIQ_SDK_ROOT" not in env


def test_openhab_images_must_be_environment_references() -> None:
    document = _document()
    document["services"]["openhab-stable"]["image"] = "openhab/openhab:5.2.5-debian"
    problems = compose_module.validate_compose_document(document, _env())
    assert any("must reference ${OPENHAB_STABLE_IMAGE}" in problem for problem in problems)


def test_services_must_not_share_userdata() -> None:
    document = _document()
    document["services"]["openhab-dev"]["volumes"] = list(
        document["services"]["openhab-stable"]["volumes"]
    )
    problems = compose_module.validate_compose_document(document, _env())
    assert any("must not share volumes" in problem for problem in problems)


def test_shim_upstream_must_default_to_openhab_stable() -> None:
    document = _document()
    document["services"]["shim"]["environment"]["OPENHAB_UPSTREAM"] = "http://openhab-dev:8080"
    problems = compose_module.validate_compose_document(document, _env())
    assert any("OPENHAB_UPSTREAM" in problem for problem in problems)


def test_runner_sdk_mount_must_be_read_only() -> None:
    document = _document()
    document["services"]["runner"]["volumes"] = [
        "${CIQ_SDK_ROOT:?set it}:/opt/ciq-sdk",
        "e2e-artifacts:/work/artifacts",
    ]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("read-only" in problem for problem in problems)


def test_runner_sdk_mount_is_required() -> None:
    document = _document()
    document["services"]["runner"]["volumes"] = ["e2e-artifacts:/work/artifacts"]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("must mount the Connect IQ SDK" in problem for problem in problems)


def test_privileged_services_are_rejected() -> None:
    document = _document()
    document["services"]["shim"]["privileged"] = True
    problems = compose_module.validate_compose_document(document, _env())
    assert any("privileged" in problem for problem in problems)


def test_host_networking_is_rejected() -> None:
    document = _document()
    document["services"]["openhab-stable"]["network_mode"] = "host"
    problems = compose_module.validate_compose_document(document, _env())
    assert any("host networking" in problem for problem in problems)


def test_bridge_network_is_required() -> None:
    document = _document()
    del document["networks"]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("bridge network" in problem for problem in problems)


def test_env_example_must_default_to_the_stable_profile() -> None:
    env = dict(_env())
    env["COMPOSE_PROFILES"] = "dev"
    problems = compose_module.validate_compose_document(_document(), env)
    assert any("COMPOSE_PROFILES" in problem for problem in problems)


def test_env_example_must_declare_every_image() -> None:
    env = dict(_env())
    del env["SHIM_BASE_IMAGE"]
    problems = compose_module.validate_compose_document(_document(), env)
    assert any("SHIM_BASE_IMAGE" in problem for problem in problems)


# ---------------------------------------------------------------------------
# pinning
# ---------------------------------------------------------------------------


def test_image_variables_include_digests_when_pinned() -> None:
    pins = compose_module.openhab_images()
    variables = compose_module.image_variables(pins, pinned=True)
    assert set(variables) == set(compose_module.IMAGE_ENV_KEYS.values())
    for value in variables.values():
        assert "@sha256:" in value


def test_image_variables_can_be_tag_only() -> None:
    pins = compose_module.openhab_images()
    variables = compose_module.image_variables(pins, pinned=False)
    assert all("@sha256:" not in value for value in variables.values())


def test_pin_images_writes_a_checkable_env(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    resolved = compose_module.pin_images(
        write=True,
        env_path=env_path,
        resolver=lambda repository, tag: "sha256:" + "0" * 64,
    )
    text = env_path.read_text(encoding="utf-8")
    for key, value in resolved.items():
        assert f"{key}={value}" in text
        assert "@sha256:" in value
    # The non-image lines from .env.example must survive the rewrite.
    assert "COMPOSE_PROFILES=stable" in text


def test_check_pins_flags_unpinned_images(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("OPENHAB_STABLE_IMAGE=openhab/openhab:5.2.5-debian\n", encoding="utf-8")
    problems = compose_module.check_pins(env_path=env_path)
    assert any("not pinned" in problem for problem in problems)


def test_check_pins_flags_a_missing_env(tmp_path: Path) -> None:
    problems = compose_module.check_pins(env_path=tmp_path / "missing.env")
    assert any("does not exist" in problem for problem in problems)


def test_config_pins_match_the_example_env() -> None:
    pins = compose_module.openhab_images()
    env = _env()
    for role, key in compose_module.IMAGE_ENV_KEYS.items():
        expected = pins[role]
        assert env[key] == expected.tagged, f"{key} drifted from tests/config/openhab.version"
        assert expected.digest and expected.digest.startswith("sha256:")


def test_openhab_version_file_is_key_value() -> None:
    from e2e.config import read_key_value_file

    values = read_key_value_file(COMPOSE_DIR.parent / "config" / "openhab.version")
    assert values["stable_tag"] == "5.2.5-debian"
    assert values["dev_tag"] == "5.3.0.M5-debian"
