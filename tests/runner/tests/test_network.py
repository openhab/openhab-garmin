"""Network definition and loopback exposure tests.

``tests/config/network.yaml`` is the single source of truth for the stack's
loopback host and ports. These cases stop a port from drifting between the
Compose overlay, the generated ``.env.example`` block and the app settings
fixtures, and enforce that the ``tests/`` tree stays hostname-free and free of
TLS material.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from e2e import compose as compose_module
from e2e import network as network_module
from e2e.config import APP_SETTINGS_DIR, NETWORK_FILE
from e2e.network import (
    ENV_LOOPBACK_HOST,
    ENV_PORTS,
    NetworkError,
    check_env_block,
    load_network,
    write_env_block,
)
from e2e.paths import tests_root as harness_tests_root
from e2e.settings import load_settings

# Site-specific names that must never be baked into a reusable environment,
# plus private key and certificate block markers.
_FORBIDDEN_PATTERNS = (
    re.compile(r"openhab-test\.home", re.IGNORECASE),
    re.compile(r"vm-openhab-test", re.IGNORECASE),
    re.compile(r"\bnas\."),
    re.compile(r"https?://[a-z0-9.-]+\.home\b", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"-----BEGIN CERTIFICATE-----"),
    re.compile(r"\b[\w./-]+\.(pem|p12|pfx|jks)\b"),
    re.compile(r"/etc/ssl/"),
)

_TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".py", ".sh", ".toml", ".txt", ".json", ".sitemap", ".items"}
_SKIP_DIRS = {"__pycache__", ".pytest_cache", "artifacts"}


def _text_files() -> list[Path]:
    files = []
    for path in harness_tests_root().rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.name == "test_network.py":
            # This test file necessarily contains the patterns it looks for.
            continue
        files.append(path)
    return files


def test_repository_network_definition_loads() -> None:
    network = load_network()
    assert network.loopback_host == "127.0.0.1"
    assert network.shim_url == f"http://{network.loopback_host}:{network.shim_port}"
    assert network.openhab_url("stable").endswith(f":{network.openhab_stable_port}")
    assert network.openhab_url("dev").endswith(f":{network.openhab_dev_port}")


def test_non_loopback_host_is_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "network.yaml"
    invalid.write_text(
        yaml.safe_dump(
            {
                "loopback_host": "0.0.0.0",
                "ports": {"openhab_stable": 8080, "openhab_dev": 8080, "shim": 8081},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(NetworkError) as error:
        load_network(invalid)
    assert "loopback" in str(error.value)


@pytest.mark.parametrize("port", [0, 65536, "8080", True])
def test_invalid_port_is_rejected(tmp_path: Path, port: object) -> None:
    invalid = tmp_path / "network.yaml"
    invalid.write_text(
        yaml.safe_dump(
            {
                "loopback_host": "127.0.0.1",
                "ports": {"openhab_stable": port, "openhab_dev": 8080, "shim": 8081},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(NetworkError):
        load_network(invalid)


def test_env_example_network_block_matches_network_yaml() -> None:
    assert check_env_block(compose_module.ENV_EXAMPLE) == []


def test_env_example_block_is_written_from_network_yaml(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text(
        "\n".join(
            [
                "COMPOSE_PROFILES=stable",
                network_module.ENV_BLOCK_START,
                "E2E_LOOPBACK_HOST=0.0.0.0",
                "SHIM_PORT=1",
                network_module.ENV_BLOCK_END,
                "CIQ_SDK_HOST_PATH=",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_env_block(example, load_network())
    assert check_env_block(example, load_network()) == []
    text = example.read_text(encoding="utf-8")
    assert "COMPOSE_PROFILES=stable" in text
    assert "CIQ_SDK_HOST_PATH=" in text
    for key, value in load_network().compose_values().items():
        assert f"{key}={value}" in text


def test_env_example_block_drift_is_reported(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text(
        "\n".join(
            [
                network_module.ENV_BLOCK_START,
                "E2E_LOOPBACK_HOST=127.0.0.1",
                "OPENHAB_STABLE_PORT=9090",
                "OPENHAB_DEV_PORT=8080",
                "SHIM_PORT=8081",
                network_module.ENV_BLOCK_END,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    problems = check_env_block(example, load_network())
    assert any("network block has drifted" in problem for problem in problems)


def test_generated_env_keys_cover_the_ports() -> None:
    values = load_network().compose_values()
    assert set(values) == {ENV_LOOPBACK_HOST, *ENV_PORTS.values()}
    assert values[ENV_LOOPBACK_HOST] == "127.0.0.1"


def test_settings_fixtures_agree_with_the_network_ports(monkeypatch) -> None:
    monkeypatch.delenv("E2E_SHIM_URL", raising=False)
    network = load_network()
    for path in APP_SETTINGS_DIR.glob("*.yaml"):
        settings = load_settings(path.stem)
        assert settings.url == network.shim_url + "/", path
        assert str(network.shim_port) in settings.url


def test_network_file_is_the_single_port_source() -> None:
    # The overlay and the example must not contain a bare, hardcoded port; they
    # must reference the variables that network.yaml renders.
    network = load_network()
    assert NETWORK_FILE.is_file()
    for path in (compose_module.COMPOSE_FILE, compose_module.HOST_OVERRIDE_FILE):
        text = path.read_text(encoding="utf-8")
        for literal in (
            f":{network.openhab_stable_port}:",
            f":{network.shim_port}:",
        ):
            assert literal not in text, f"{path} hardcodes {literal}"


def test_tests_tree_is_hostname_and_certificate_free() -> None:
    offenders = []
    for path in _text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in _FORBIDDEN_PATTERNS:
            match = pattern.search(text)
            if match:
                offenders.append(f"{path}: {match.group(0)!r}")
    assert offenders == [], "\n".join(offenders)
