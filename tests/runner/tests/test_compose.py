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
from e2e.network import load_network


def _document() -> dict:
    return copy.deepcopy(compose_module.load_compose())


def _host_document() -> dict:
    return copy.deepcopy(compose_module.load_compose(compose_module.HOST_OVERRIDE_FILE))


def _write_host(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "compose.host.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


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


def test_openhab_must_not_mount_the_fixtures_itself() -> None:
    """openHAB's entrypoint chowns /openhab recursively; a read-only fixture
    mount underneath it aborts the container on first start (live-run defect)."""
    document = _document()
    document["services"]["openhab-stable"]["volumes"].insert(
        1, "../fixtures/openhab/conf/items:/openhab/conf/items:ro"
    )
    problems = compose_module.validate_compose_document(document, _env())
    assert any("must not mount fixtures/openhab/conf/items" in problem for problem in problems)
    assert any("chown -R openhab:openhab" in problem for problem in problems)


def test_staging_service_is_required() -> None:
    document = _document()
    del document["services"]["openhab-dev-init"]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("services.openhab-dev-init is missing" in problem for problem in problems)


def test_openhab_must_wait_for_the_fixture_staging() -> None:
    document = _document()
    del document["services"]["openhab-stable"]["depends_on"]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("service_completed_successfully" in problem for problem in problems)


def test_wrong_staging_dependency_condition_is_rejected() -> None:
    document = _document()
    document["services"]["openhab-stable"]["depends_on"]["openhab-stable-init"][
        "condition"
    ] = "service_started"
    problems = compose_module.validate_compose_document(document, _env())
    assert any("service_completed_successfully" in problem for problem in problems)


def test_staging_service_must_be_one_shot() -> None:
    document = _document()
    document["services"]["openhab-stable-init"]["restart"] = "always"
    problems = compose_module.validate_compose_document(document, _env())
    assert any('services.openhab-stable-init.restart must be "no"' in problem for problem in problems)


def test_staging_service_must_use_the_pinned_openhab_image() -> None:
    document = _document()
    document["services"]["openhab-stable-init"]["image"] = "busybox:1.36"
    problems = compose_module.validate_compose_document(document, _env())
    assert any("OPENHAB_STABLE_IMAGE" in problem for problem in problems)


def test_staging_profiles_must_match_the_openhab_service() -> None:
    document = _document()
    document["services"]["openhab-dev-init"]["profiles"] = ["stable"]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("profiles must match" in problem for problem in problems)


def test_fixture_source_mounted_read_write_is_rejected() -> None:
    document = _document()
    document["services"]["openhab-stable-init"]["volumes"] = [
        "openhab-stable-conf:/openhab/conf",
        "../fixtures/openhab/conf:/fixtures",
        "./seed-openhab-conf.sh:/seed-conf.sh:ro",
    ]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("must be read-only (:ro)" in problem for problem in problems)


def test_artifacts_mount_inside_the_read_only_checkout_is_rejected() -> None:
    """Reproduces the live-run daemon error without Docker: a volume target
    nested in a read-only mount cannot have its mountpoint created."""
    document = _document()
    runner = document["services"]["runner"]
    runner["volumes"] = [
        "${CIQ_SDK_HOST_PATH:?set it}:/opt/ciq-sdk:ro",
        "${CIQ_DEVICES_HOST_PATH:-e2e-empty-devices}:/opt/ciq-devices:ro",
        "${E2E_REPO_ROOT:-../..}:/work:ro",
        "e2e-artifacts:/work/artifacts",
    ]
    runner["environment"]["E2E_ARTIFACTS"] = "/work/artifacts"
    problems = compose_module.validate_compose_document(document, _env())
    assert any("read-only file system" in problem for problem in problems)
    assert any("E2E_ARTIFACTS=/work/artifacts is inside the read-only mount /work" in problem for problem in problems)


def test_artifacts_path_must_be_covered_by_a_writable_mount() -> None:
    document = _document()
    document["services"]["runner"]["environment"]["E2E_ARTIFACTS"] = "/data/artifacts"
    problems = compose_module.validate_compose_document(document, _env())
    assert any("is not covered by a writable volume" in problem for problem in problems)


def test_artifacts_path_must_be_absolute() -> None:
    document = _document()
    document["services"]["runner"]["environment"]["E2E_ARTIFACTS"] = "artifacts"
    problems = compose_module.validate_compose_document(document, _env())
    assert any("must be an absolute container path" in problem for problem in problems)


def test_mount_rules_hold_for_the_repository_stack() -> None:
    document = _document()
    services = document["services"]
    assert compose_module.mount_problems(services, _env()) == []
    assert compose_module.artifacts_mount_problems(services, _env()) == []
    assert compose_module.staging_problems(services) == []


def test_conf_must_be_a_writable_named_volume() -> None:
    document = _document()
    document["services"]["openhab-stable"]["volumes"] = [
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
        "${CIQ_SDK_HOST_PATH:?set it}:/opt/ciq-sdk",
        "${CIQ_DEVICES_HOST_PATH:-e2e-empty-devices}:/opt/ciq-devices:ro",
        "e2e-artifacts:/artifacts",
    ]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("read-only" in problem for problem in problems)


def test_runner_sdk_mount_is_required() -> None:
    document = _document()
    document["services"]["runner"]["volumes"] = [
        "${CIQ_DEVICES_HOST_PATH:-e2e-empty-devices}:/opt/ciq-devices:ro",
        "e2e-artifacts:/artifacts",
    ]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("must mount the Connect IQ SDK" in problem for problem in problems)


def test_runner_device_definition_mount_is_required_and_read_only() -> None:
    document = _document()
    document["services"]["runner"]["volumes"] = [
        "${CIQ_SDK_HOST_PATH:?set it}:/opt/ciq-sdk:ro",
        "e2e-artifacts:/artifacts",
    ]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("device definitions at /opt/ciq-devices" in problem for problem in problems)

    document["services"]["runner"]["volumes"].insert(
        1, "${CIQ_DEVICES_HOST_PATH:-e2e-empty-devices}:/opt/ciq-devices"
    )
    problems = compose_module.validate_compose_document(document, _env())
    assert any("/opt/ciq-devices read-only" in problem for problem in problems)


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
# loopback exposure
# ---------------------------------------------------------------------------


def test_default_profile_publishes_loopback_only() -> None:
    document = _document()
    assert compose_module.published_port_problems(document["services"], _env()) == []
    for name in ("openhab-stable", "openhab-dev", "shim"):
        ports = document["services"][name].get("ports", [])
        assert ports, f"{name} should publish a loopback port for developer access"
        for entry in ports:
            # The host IP must come from the single-source variable, never a
            # wildcard or a literal address.
            assert "${E2E_LOOPBACK_HOST" in str(entry), (name, entry)


def test_wildcard_published_port_is_rejected() -> None:
    document = _document()
    document["services"]["shim"]["ports"] = ["8081:8080"]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("has no host IP" in problem for problem in problems)


def test_non_loopback_published_port_is_rejected() -> None:
    document = _document()
    document["services"]["shim"]["ports"] = ["0.0.0.0:8081:8080"]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("not loopback" in problem for problem in problems)


def test_long_syntax_published_ports_are_checked() -> None:
    document = _document()
    document["services"]["shim"]["ports"] = [
        {"host_ip": "0.0.0.0", "published": 8081, "target": 8080}
    ]
    problems = compose_module.validate_compose_document(document, _env())
    assert any("not loopback" in problem for problem in problems)

    document["services"]["shim"]["ports"] = [
        {"host_ip": "127.0.0.1", "published": 8081, "target": 8080}
    ]
    assert compose_module.validate_compose_document(document, _env()) == []


# ---------------------------------------------------------------------------
# host-network overlay
# ---------------------------------------------------------------------------


def test_host_override_validates() -> None:
    assert compose_module.validate_host_override() == []


def test_host_override_runs_every_service_on_the_host() -> None:
    services = _host_document()["services"]
    for name in compose_module.REQUIRED_SERVICES:
        assert services[name]["network_mode"] == "host"
        assert not services[name].get("ports"), f"{name} must not publish ports"
        assert not services[name].get("networks"), f"{name} must not attach a bridge network"


def test_host_override_urls_match_the_network_definition() -> None:
    network = load_network()
    document = compose_module.load_compose(compose_module.HOST_OVERRIDE_FILE)
    env = _env()
    runner = document["services"]["runner"]["environment"]
    assert compose_module.interpolate(runner["E2E_SHIM_URL"], env) == network.shim_url
    assert (
        compose_module.interpolate(runner["E2E_OPENHAB_URL"], env)
        == network.openhab_url("stable")
    )
    shim = document["services"]["shim"]["environment"]
    assert compose_module.interpolate(shim["SHIM_BIND"], env) == network.loopback_host
    assert compose_module.interpolate(shim["SHIM_PORT"], env) == str(network.shim_port)


def test_host_override_requires_host_networking(tmp_path: Path) -> None:
    document = _host_document()
    document["services"]["runner"]["network_mode"] = "bridge"
    problems = compose_module.validate_host_override(
        _write_host(tmp_path, document), env=_env()
    )
    assert any("network_mode: host" in problem for problem in problems)


def test_host_override_rejects_published_ports(tmp_path: Path) -> None:
    document = _host_document()
    document["services"]["shim"]["ports"] = ["127.0.0.1:8081:8080"]
    problems = compose_module.validate_host_override(
        _write_host(tmp_path, document), env=_env()
    )
    assert any("must not publish ports" in problem for problem in problems)


def test_host_override_requires_a_wildcard_free_shim_bind(tmp_path: Path) -> None:
    document = _host_document()
    document["services"]["shim"]["environment"]["SHIM_BIND"] = "0.0.0.0"
    problems = compose_module.validate_host_override(
        _write_host(tmp_path, document), env=_env()
    )
    assert any("loopback host" in problem for problem in problems)


def test_host_override_requires_a_loopback_openhab_bind(tmp_path: Path) -> None:
    document = _host_document()
    document["services"]["openhab-stable"]["environment"].pop("EXTRA_JAVA_OPTS")
    problems = compose_module.validate_host_override(
        _write_host(tmp_path, document), env=_env()
    )
    assert any("listening.address" in problem for problem in problems)


def test_host_override_rejects_a_hardcoded_hostname(tmp_path: Path) -> None:
    document = _host_document()
    document["services"]["runner"]["environment"]["E2E_SHIM_URL"] = "http://shim:8080"
    problems = compose_module.validate_host_override(
        _write_host(tmp_path, document), env=_env()
    )
    assert any("E2E_SHIM_URL" in problem for problem in problems)


def test_host_dev_override_validates() -> None:
    assert compose_module.validate_host_dev_override() == []


def test_host_dev_override_points_at_the_dev_port(tmp_path: Path) -> None:
    network = load_network()
    document = compose_module.load_compose(compose_module.HOST_DEV_OVERRIDE_FILE)
    runner = document["services"]["runner"]["environment"]
    assert (
        compose_module.interpolate(runner["E2E_OPENHAB_URL"], _env())
        == network.openhab_url("dev")
    )
    document["services"]["runner"]["environment"]["E2E_OPENHAB_URL"] = "http://openhab-dev:8080"
    path = tmp_path / "compose.host.dev.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    problems = compose_module.validate_host_dev_override(path, env=_env())
    assert any("E2E_OPENHAB_URL" in problem for problem in problems)


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
