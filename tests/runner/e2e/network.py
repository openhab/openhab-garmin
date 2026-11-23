"""Loopback host and port definitions for the E2E stack.

``tests/config/network.yaml`` is the only place that defines the ports the
stack uses. This module gives the rest of the harness a typed view of it and
renders the Compose environment block, so the Compose overlay, the environment
example and the app settings fixtures cannot drift apart silently.

Two deployment profiles consume these values:

- **bridge** (the default, works on any Docker host): services talk to each
  other through the Compose service aliases at their container ports, and the
  host publishes the same ports on ``loopback_host`` only.
- **host** (the dedicated Linux VM): every service shares the host network
  namespace, listens on ``loopback_host`` and addresses its peers as
  ``http://<loopback_host>:<port>``.

Nothing here stores or references TLS material. External access, including TLS
termination, is the operator's reverse proxy.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional

import yaml

from .config import NETWORK_FILE, ConfigError

#: Compose environment variables rendered from the network definition.
ENV_LOOPBACK_HOST = "E2E_LOOPBACK_HOST"
ENV_PORTS = {
    "openhab_stable": "OPENHAB_STABLE_PORT",
    "openhab_dev": "OPENHAB_DEV_PORT",
    "shim": "SHIM_PORT",
}

#: Markers around the generated block in ``tests/compose/.env.example``.
ENV_BLOCK_START = "# >>> network >>>"
ENV_BLOCK_END = "# <<< network <<<"

#: Profiles that run an openHAB instance.
OPENHAB_PROFILES = ("stable", "dev")


class NetworkError(ConfigError):
    """Raised when the network definition is missing or invalid."""


@dataclass(frozen=True)
class Network:
    """The loopback host and ports every stack component derives from."""

    loopback_host: str
    openhab_stable_port: int
    openhab_dev_port: int
    shim_port: int

    def url(self, port: int) -> str:
        """A ``http://<loopback_host>:<port>`` URL."""
        host = f"[{self.loopback_host}]" if ":" in self.loopback_host else self.loopback_host
        return f"http://{host}:{port}"

    def openhab_port(self, profile: str = "stable") -> int:
        if profile == "dev":
            return self.openhab_dev_port
        if profile != "stable":
            raise NetworkError(f"unknown openHAB profile {profile!r}")
        return self.openhab_stable_port

    def openhab_url(self, profile: str = "stable") -> str:
        return self.url(self.openhab_port(profile))

    @property
    def shim_url(self) -> str:
        return self.url(self.shim_port)

    def compose_values(self) -> Dict[str, str]:
        """The ``KEY=value`` pairs the generated environment block contains."""
        values = {ENV_LOOPBACK_HOST: self.loopback_host}
        for role, key in ENV_PORTS.items():
            values[key] = str(getattr(self, f"{role}_port"))
        return values


def _port(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NetworkError(f"{where}: port must be an integer, got {value!r}")
    if not 1 <= value <= 65535:
        raise NetworkError(f"{where}: port must be 1..65535, got {value}")
    return value


def _loopback_host(value: object, where: str) -> str:
    text = str(value).strip()
    if not text:
        raise NetworkError(f"{where}: loopback_host must not be empty")
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        if text != "localhost":
            raise NetworkError(
                f"{where}: loopback_host must be a loopback address or localhost, got {text!r}"
            ) from None
        return text
    if not address.is_loopback:
        raise NetworkError(
            f"{where}: loopback_host must be loopback, got {text!r}; the stack never "
            "binds a public address"
        )
    return text


def load_network(path: Path = NETWORK_FILE) -> Network:
    """Load and validate ``tests/config/network.yaml``."""
    if not path.is_file():
        raise NetworkError(f"missing network definition: {path}")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise NetworkError(f"{path}: expected a mapping at the top level")
    ports = document.get("ports")
    if not isinstance(ports, Mapping):
        raise NetworkError(f"{path}: 'ports' must be a mapping")
    missing = [role for role in ENV_PORTS if role not in ports]
    if missing:
        raise NetworkError(f"{path}: 'ports' is missing {', '.join(missing)}")
    return Network(
        loopback_host=_loopback_host(document.get("loopback_host"), str(path)),
        openhab_stable_port=_port(ports["openhab_stable"], f"{path}: ports.openhab_stable"),
        openhab_dev_port=_port(ports["openhab_dev"], f"{path}: ports.openhab_dev"),
        shim_port=_port(ports["shim"], f"{path}: ports.shim"),
    )


# ---------------------------------------------------------------------------
# generated environment block
# ---------------------------------------------------------------------------


def env_block_lines(network: Network) -> List[str]:
    """The environment block written between the markers."""
    lines = [ENV_BLOCK_START]
    for key, value in network.compose_values().items():
        lines.append(f"{key}={value}")
    lines.append(ENV_BLOCK_END)
    return lines


def _write_block(path: Path, network: Network) -> Path:
    if not path.is_file():
        raise NetworkError(f"missing environment example: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(ENV_BLOCK_START)
        end = lines.index(ENV_BLOCK_END)
    except ValueError:
        raise NetworkError(
            f"{path}: expected both {ENV_BLOCK_START!r} and {ENV_BLOCK_END!r} markers"
        ) from None
    if end < start:
        raise NetworkError(f"{path}: network markers are out of order")
    updated = lines[:start] + env_block_lines(network) + lines[end + 1:]
    path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
    return path


def write_env_block(path: Path, network: Optional[Network] = None) -> Path:
    """Rewrite the generated block in ``path`` from ``network.yaml``."""
    return _write_block(path, network or load_network())


def check_env_block(path: Path, network: Optional[Network] = None) -> List[str]:
    """Return problems when ``path``'s block disagrees with ``network.yaml``."""
    network = network or load_network()
    if not path.is_file():
        return [f"{path}: missing; add the generated network block"]
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(ENV_BLOCK_START)
        end = lines.index(ENV_BLOCK_END)
    except ValueError:
        return [f"{path}: expected {ENV_BLOCK_START!r} and {ENV_BLOCK_END!r} markers"]
    actual = [line.strip() for line in lines[start:end + 1] if line.strip()]
    expected = env_block_lines(network)
    if actual != expected:
        return [
            f"{path}: the generated network block has drifted from tests/config/network.yaml; "
            "run `python -m e2e network-env --write`"
        ]
    return []
