"""Read-only host preflight checks for the E2E stack.

``python -m e2e preflight`` answers one question before anything is started:
would the next ``docker compose up`` fail for an environmental reason? Every
check has one pass/fail verdict and a short reason, and the command never
starts, stops or writes anything. It is not a replacement for the spike in
``tests/spike``: Wine, the X display and the simulator process are only proven
there, and the spike also builds the runner image.

The checks, by ``--skip`` group:

``docker``
    The CLI is on ``PATH``, the daemon answers and reports its server version,
    Docker Compose v2 is at least 2.24 (the host overlay uses ``!reset``, which
    older releases reject), and the base + host overlay parse.
``compose``
    The checked-in Compose files parse through the static loader, including the
    host overlays and the generated network block. No Docker needed.
``sdk``
    The Connect IQ SDK root is configured, has the Linux ``monkeyc`` and
    ``monkeydo`` and the Windows ``simulator.exe``, and reports the version in
    ``tests/config/sdk.version``.
``pins``
    ``tests/compose/.env`` pins the images in ``tests/config/openhab.version``
    (the same validation ``validate-compose --check-pins`` uses).
``ports``
    The loopback ports from ``tests/config/network.yaml`` can be bound, and a
    listening process is named when one is found.
``layout``
    The checkout contains the fixtures, scenarios, config and Compose files the
    runner mounts, and the artifacts directory is writable.
``resources``
    Memory, CPU count and free disk are reported against documented minimums.

Every probe is injectable (``which``, a command runner, the port probe and the
resource probe), and the command never imports Docker, the SDK or the network
for the static groups. The unit tests drive the command plan with recorded
output, which is why this module works in CI with no Docker daemon at all.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import compose as compose_module
from .config import ConfigError, read_sdk_version
from .network import Network, load_network
from .paths import repo_root, tests_root
from .sdk import SdkError, inspect_sdk

PASS = "pass"
FAIL = "fail"
WARN = "warn"
SKIP = "skip"

#: Check groups accepted by ``--skip``, in execution order.
GROUPS = ("docker", "compose", "sdk", "pins", "ports", "layout", "resources")

#: Every check, in output order. Keeping this table explicit makes ``--skip``
#: produce a stable document: a skipped group still has its checks listed.
CHECKS = (
    ("docker.engine", "docker"),
    ("docker.compose", "docker"),
    ("docker.compose-config", "docker"),
    ("compose.files", "compose"),
    ("sdk.layout", "sdk"),
    ("pins.images", "pins"),
    ("ports.loopback", "ports"),
    ("layout.repository", "layout"),
    ("layout.artifacts", "layout"),
    ("resources.host", "resources"),
)

#: SDK environment variables, in precedence order. ``CIQ_SDK_HOST_PATH`` is the
#: variable Compose consumes on the host; the other two are the aliases the
#: harness and ``tests/docs/sdk.md`` document.
SDK_ENV_VARS = ("CIQ_SDK_HOST_PATH", "CIQ_SDK_ROOT", "CIQ_SDK_PATH")

#: ``!reset`` in ``compose.host.yaml`` needs Docker Compose 2.24 or newer.
MINIMUM_COMPOSE_VERSION = (2, 24, 0)

COMMAND_TIMEOUT_S = 20.0

GIB = 1024**3

EXIT_OK = 0
EXIT_FAILURE = 1


class PreflightError(ConfigError):
    """Raised for an invalid ``--skip`` selector."""


@dataclass(frozen=True)
class CommandResult:
    """The parts of a finished command the checks need."""

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def detail(self) -> str:
        """One line naming why the command failed."""
        text = (self.stderr or self.stdout or "").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else f"exit code {self.returncode}"


CommandRunner = Callable[[Sequence[str]], CommandResult]
Which = Callable[[str], Optional[str]]
PortProbe = Callable[[str, int], bool]
ListenerMap = Callable[[], Mapping[int, str]]


def run_command(command: Sequence[str]) -> CommandResult:
    """Run a host command, never raising for a missing binary or a timeout."""
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return CommandResult(127, "", str(error))
    return CommandResult(
        completed.returncode,
        completed.stdout or "",
        completed.stderr or "",
    )


@dataclass
class CheckResult:
    """One check's verdict."""

    check_id: str
    group: str
    status: str
    reason: str
    details: Dict[str, object] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == FAIL

    def to_document(self) -> Dict[str, object]:
        document: Dict[str, object] = {
            "id": self.check_id,
            "group": self.group,
            "status": self.status,
            "reason": self.reason,
        }
        if self.details:
            document["details"] = dict(self.details)
        return document


@dataclass
class PreflightReport:
    """The finished preflight run."""

    checks: List[CheckResult] = field(default_factory=list)
    skipped_groups: Tuple[str, ...] = ()

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)

    @property
    def ok(self) -> bool:
        return not any(check.failed for check in self.checks)

    @property
    def exit_code(self) -> int:
        return EXIT_OK if self.ok else EXIT_FAILURE

    def counts(self) -> Dict[str, int]:
        return {
            "passed": sum(1 for check in self.checks if check.status == PASS),
            "failed": sum(1 for check in self.checks if check.status == FAIL),
            "warnings": sum(1 for check in self.checks if check.status == WARN),
            "skipped": sum(1 for check in self.checks if check.status == SKIP),
        }

    def to_document(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "skipped_groups": list(self.skipped_groups),
            "summary": self.counts(),
            "checks": [check.to_document() for check in self.checks],
        }

    def render_lines(self) -> List[str]:
        """Human-readable one-line-per-check output."""
        width = max((len(check.check_id) for check in self.checks), default=0)
        lines = [
            f"{check.check_id:<{width}}  {check.status.upper():<4}  {check.reason}"
            for check in self.checks
        ]
        counts = self.counts()
        lines.append("")
        lines.append(
            f"summary: {counts['passed']} passed, {counts['failed']} failed, "
            f"{counts['warnings']} warning(s), {counts['skipped']} skipped"
        )
        lines.append("preflight: ok" if self.ok else "preflight: FAILED")
        return lines


def parse_skip(values: Sequence[str]) -> Tuple[str, ...]:
    """Parse repeated/comma-separated ``--skip`` values into group names."""
    selected: List[str] = []
    for value in values:
        for raw in str(value).split(","):
            name = raw.strip()
            if not name:
                continue
            if name not in GROUPS:
                raise PreflightError(
                    f"unknown --skip group {name!r}; known groups: {', '.join(GROUPS)}"
                )
            if name not in selected:
                selected.append(name)
    return tuple(selected)


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------


def _missing_docker() -> CheckResult:
    return CheckResult(
        "docker.engine",
        "docker",
        FAIL,
        "docker is not on PATH; install Docker Engine or add it to PATH",
    )


def check_engine(
    runner: CommandRunner = run_command,
    which: Which = shutil.which,
) -> CheckResult:
    """Docker Engine reachable, with its server version."""
    docker = which("docker")
    if docker is None:
        return _missing_docker()
    result = runner([docker, "info", "--format", "{{.ServerVersion}}"])
    if not result.ok:
        return CheckResult(
            "docker.engine",
            "docker",
            FAIL,
            f"the Docker daemon is unreachable: {result.detail()}",
            {"docker": docker},
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    version = lines[0] if lines else ""
    if not version:
        return CheckResult(
            "docker.engine",
            "docker",
            FAIL,
            "the Docker daemon answered without a server version",
            {"docker": docker},
        )
    return CheckResult(
        "docker.engine",
        "docker",
        PASS,
        f"Docker Engine {version} reachable",
        {"docker": docker, "server_version": version},
    )


_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_compose_version(text: str) -> Optional[Tuple[int, int, int]]:
    """Extract ``(major, minor, patch)`` from ``docker compose version`` output."""
    match = _VERSION.search(text or "")
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _format_version(version: Tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def check_compose_version(
    runner: CommandRunner = run_command,
    which: Which = shutil.which,
) -> CheckResult:
    """Docker Compose v2 present and new enough for ``!reset``."""
    check_id, group = "docker.compose", "docker"
    docker = which("docker")
    if docker is None:
        return CheckResult(
            check_id,
            group,
            FAIL,
            "docker is not on PATH, so Docker Compose cannot be checked",
        )
    result = runner([docker, "compose", "version", "--short"])
    if not result.ok:
        return CheckResult(
            check_id,
            group,
            FAIL,
            "Docker Compose v2 is not available "
            f"(`docker compose version` failed: {result.detail()})",
        )
    text = result.stdout.strip()
    parsed = parse_compose_version(text)
    if parsed is None:
        return CheckResult(
            check_id,
            group,
            FAIL,
            f"cannot parse the Docker Compose version from {text!r}",
        )
    if parsed < MINIMUM_COMPOSE_VERSION:
        required = _format_version(MINIMUM_COMPOSE_VERSION)
        return CheckResult(
            check_id,
            group,
            FAIL,
            f"Docker Compose {_format_version(parsed)} is too old: the host overlay "
            f"uses !reset, which needs {required} or newer",
            {"compose_version": _format_version(parsed), "minimum": required},
        )
    return CheckResult(
        check_id,
        group,
        PASS,
        f"Docker Compose {_format_version(parsed)} (>= "
        f"{_format_version(MINIMUM_COMPOSE_VERSION)})",
        {"compose_version": _format_version(parsed)},
    )


def check_compose_config(
    runner: CommandRunner = run_command,
    which: Which = shutil.which,
    *,
    compose_file: Path = compose_module.COMPOSE_FILE,
    host_file: Path = compose_module.HOST_OVERRIDE_FILE,
    env_file: Path = compose_module.ENV_FILE,
) -> CheckResult:
    """The base file and the host overlay parse with Docker Compose."""
    check_id, group = "docker.compose-config", "docker"
    docker = which("docker")
    if docker is None:
        return CheckResult(
            check_id,
            group,
            FAIL,
            "docker is not on PATH, so the Compose files cannot be parsed by Compose",
        )
    if not env_file.is_file():
        return CheckResult(
            check_id,
            group,
            FAIL,
            f"{env_file} is missing; run `python -m e2e pin-images --write`",
        )
    command = [
        docker,
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(compose_file),
        "-f",
        str(host_file),
        "config",
        "--quiet",
    ]
    result = runner(command)
    if not result.ok:
        return CheckResult(
            check_id,
            group,
            FAIL,
            f"base + host overlay do not parse: {result.detail()}",
            {"command": " ".join(command)},
        )
    return CheckResult(
        check_id,
        group,
        PASS,
        "compose.yaml + compose.host.yaml parse with the host overlay applied",
    )


# ---------------------------------------------------------------------------
# static Compose and image pins
# ---------------------------------------------------------------------------


def check_compose_files(
    validator: Callable[[], List[str]] = compose_module.validate_compose,
) -> CheckResult:
    """The checked-in Compose files and overlays parse without Docker."""
    check_id, group = "compose.files", "compose"
    try:
        problems = validator()
    except (ConfigError, compose_module.ComposeError) as error:
        return CheckResult(check_id, group, FAIL, str(error))
    if problems:
        reason = problems[0]
        if len(problems) > 1:
            reason += f" (+{len(problems) - 1} more)"
        return CheckResult(
            check_id, group, FAIL, reason, {"problems": list(problems)}
        )
    return CheckResult(
        check_id,
        group,
        PASS,
        "base, dev and host overlays parse; loopback wiring matches network.yaml",
    )


def check_image_pins(
    env_file: Path = compose_module.ENV_FILE,
    checker: Callable[..., List[str]] = compose_module.check_pins,
) -> CheckResult:
    """The image pins resolve to the environment the stack will use.

    ``checker`` is the existing ``compose.check_pins``; it is injectable so the
    unit tests can record its answer without touching ``tests/compose/.env``.
    """
    check_id, group = "pins.images", "pins"
    problems = checker(env_path=env_file)
    if problems:
        reason = problems[0]
        if len(problems) > 1:
            reason += f" (+{len(problems) - 1} more)"
        return CheckResult(
            check_id, group, FAIL, reason, {"problems": list(problems)}
        )
    return CheckResult(
        check_id,
        group,
        PASS,
        f"images in {env_file.name} match tests/config/openhab.version",
    )


# ---------------------------------------------------------------------------
# Connect IQ SDK
# ---------------------------------------------------------------------------


def check_sdk(
    environ: Mapping[str, str],
    *,
    expected_version: Optional[str] = None,
    inspector: Callable[..., object] = inspect_sdk,
) -> CheckResult:
    """The SDK root, its Linux tools, the Wine-side simulator and the version."""
    check_id, group = "sdk.layout", "sdk"

    variable = None
    root: Optional[Path] = None
    for name in SDK_ENV_VARS:
        value = environ.get(name)
        if value:
            variable, root = name, Path(value).expanduser()
            break
    if variable is None:
        return CheckResult(
            check_id,
            group,
            FAIL,
            "Connect IQ SDK not configured: set "
            f"{SDK_ENV_VARS[0]} (or {SDK_ENV_VARS[1]} / {SDK_ENV_VARS[2]}) "
            "to the SDK root; see tests/docs/sdk.md",
        )
    if root is None or not root.is_dir():
        return CheckResult(
            check_id,
            group,
            FAIL,
            f"{variable} points at {root}, which is not a directory",
            {"variable": variable},
        )

    if expected_version is None:
        try:
            expected_version = read_sdk_version()
        except ConfigError as error:
            return CheckResult(check_id, group, FAIL, str(error))
    try:
        layout = inspector(root, expected_version=expected_version)
    except SdkError as error:
        lines = [line.strip() for line in str(error).splitlines() if line.strip()]
        return CheckResult(
            check_id,
            group,
            FAIL,
            lines[0] if lines else str(error),
            {"detail": str(error), "variable": variable},
        )

    missing: List[str] = []
    if getattr(layout, "monkeydo", None) is None:
        missing.append("linux/bin/monkeydo")
    if getattr(layout, "simulator", None) is None:
        missing.append("windows/bin/simulator.exe")
    if missing:
        return CheckResult(
            check_id,
            group,
            FAIL,
            f"SDK {layout.version} at {root} is missing {', '.join(missing)}; "
            "see tests/docs/sdk.md",
            {"root": str(root), "version": layout.version, "missing": missing},
        )
    return CheckResult(
        check_id,
        group,
        PASS,
        f"SDK {layout.version} at {root} (monkeyc, monkeydo, simulator.exe)",
        {
            "root": str(root),
            "version": layout.version,
            "monkeyc": str(layout.monkeyc),
            "monkeydo": str(layout.monkeydo),
            "simulator": str(layout.simulator),
        },
    )


# ---------------------------------------------------------------------------
# loopback ports
# ---------------------------------------------------------------------------

_SS_USER = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')


def role_ports(network: Network) -> Dict[int, List[str]]:
    """Map each stack port to the roles that use it.

    ``network.yaml`` gives the two openHAB profiles the same port because they
    are exclusive, so one entry names both roles.
    """
    ports: Dict[int, List[str]] = {}
    for role in ("openhab_stable", "openhab_dev", "shim"):
        port = getattr(network, f"{role}_port")
        ports.setdefault(port, []).append(role)
    return ports


def port_is_free(host: str, port: int) -> bool:
    """True when this process can bind ``host:port``.

    Binding is the definitive test for the address the stack will use;
    inspecting listeners is only needed to name a conflicting process.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def parse_ss_listeners(text: str) -> Dict[int, str]:
    """Parse ``ss -H -ltnp`` output into ``port -> process``.

    ``ss`` is the tool available on the dedicated Linux host. When the process
    cannot be resolved (no privileges, or ``ss`` built without ``-p`` support)
    the port is still reported, with an explicit "unidentified" value.
    """
    listeners: Dict[int, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[3]
        _, _, port_text = local.rpartition(":")
        if not port_text.isdigit():
            continue
        port = int(port_text)
        users = _SS_USER.findall(line)
        if users:
            name, pid = users[0]
            listeners[port] = f"{name} (pid {pid})"
        else:
            listeners.setdefault(port, "an unidentified process")
    return listeners


def ss_processes(
    runner: CommandRunner = run_command,
    which: Which = shutil.which,
) -> Dict[int, str]:
    """Best-effort listener map from ``ss``; empty when it is unavailable."""
    ss = which("ss")
    if ss is None:
        return {}
    result = runner([ss, "-H", "-ltnp"])
    if not result.ok:
        return {}
    return parse_ss_listeners(result.stdout)


def check_ports(
    network: Network,
    *,
    free: PortProbe = port_is_free,
    listeners: Optional[ListenerMap] = None,
) -> CheckResult:
    """Every stack port can be bound on the loopback host."""
    check_id, group = "ports.loopback", "ports"
    listeners = listeners or (lambda: {})
    ports = role_ports(network)
    listeners_by_port: Optional[Mapping[int, str]] = None
    busy: List[str] = []
    for port in sorted(ports):
        if free(network.loopback_host, port):
            continue
        if listeners_by_port is None:
            listeners_by_port = listeners()
        who = listeners_by_port.get(port)
        roles = ", ".join(ports[port])
        if who:
            busy.append(
                f"port {port} ({roles}) on {network.loopback_host} is already "
                f"in use by {who}"
            )
        else:
            busy.append(
                f"port {port} ({roles}) on {network.loopback_host} is already in use "
                "(process not identified; run as root or install `ss`)"
            )
    if busy:
        return CheckResult(
            check_id, group, FAIL, "; ".join(busy), {"busy": busy}
        )
    listed = ", ".join(str(port) for port in sorted(ports))
    return CheckResult(
        check_id,
        group,
        PASS,
        f"ports {listed} are free on {network.loopback_host}",
        {"host": network.loopback_host, "ports": sorted(ports)},
    )


# ---------------------------------------------------------------------------
# repository layout
# ---------------------------------------------------------------------------

REQUIRED_FILES = (
    "monkey.jungle",
    "manifest.xml",
    "tests/runner/Dockerfile",
    "tests/config/network.yaml",
    "tests/config/devices.yaml",
    "tests/config/sdk.version",
    "tests/config/openhab.version",
    "tests/compose/compose.yaml",
    "tests/compose/compose.host.yaml",
    "tests/compose/.env.example",
)

REQUIRED_DIRECTORIES = (
    "tests/fixtures/openhab/conf/items",
    "tests/fixtures/openhab/conf/sitemaps",
    "tests/fixtures/app/settings",
    "tests/scenarios",
    "tests/shim",
)


def check_repository_layout(root: Path) -> CheckResult:
    """The checkout contains everything the runner mounts."""
    check_id, group = "layout.repository", "layout"
    root = Path(root)
    missing: List[str] = [
        relative for relative in REQUIRED_FILES if not (root / relative).is_file()
    ]
    missing += [
        relative
        for relative in REQUIRED_DIRECTORIES
        if not (root / relative).is_dir()
    ]
    scenarios = root / "tests" / "scenarios"
    if scenarios.is_dir() and not any(scenarios.rglob("*.yaml")):
        missing.append("tests/scenarios/*.yaml (no scenarios found)")
    if missing:
        reason = f"missing from {root}: " + ", ".join(missing)
        return CheckResult(check_id, group, FAIL, reason, {"missing": missing})
    return CheckResult(
        check_id,
        group,
        PASS,
        "fixtures, scenarios, config and Compose files are present",
        {"root": str(root)},
    )


def check_artifacts(artifacts: Path) -> CheckResult:
    """The artifacts directory exists or can be created, and is writable."""
    check_id, group = "layout.artifacts", "layout"
    path = Path(artifacts)
    if path.exists():
        if not path.is_dir():
            return CheckResult(
                check_id,
                group,
                FAIL,
                f"{path} exists but is not a directory",
            )
        if not os.access(path, os.W_OK):
            return CheckResult(
                check_id, group, FAIL, f"{path} is not writable"
            )
        return CheckResult(
            check_id, group, PASS, f"artifacts directory {path} is writable"
        )
    parent = path.parent
    if not parent.is_dir():
        return CheckResult(
            check_id,
            group,
            FAIL,
            f"{path} does not exist and its parent {parent} is not a directory",
        )
    if not os.access(parent, os.W_OK):
        return CheckResult(
            check_id,
            group,
            FAIL,
            f"{path} does not exist and {parent} is not writable, so it cannot be created",
        )
    return CheckResult(
        check_id,
        group,
        PASS,
        f"{path} does not exist yet but {parent} is writable",
    )


# ---------------------------------------------------------------------------
# host resources
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HostResources:
    """One measurement of the host resources the stack needs."""

    memory_bytes: int
    cpu_count: int
    disk_bytes: int
    disk_path: str = ""


@dataclass(frozen=True)
class ResourceFloor:
    """A documented resource floor."""

    memory_bytes: int
    cpu_count: int
    disk_bytes: int


#: Below these the stack cannot start reliably. Documented in tests/README.md.
MINIMUM_RESOURCES = ResourceFloor(
    memory_bytes=2 * GIB,
    cpu_count=1,
    disk_bytes=5 * GIB,
)

#: Below these the stack runs but is likely to be slow or to OOM under load.
RECOMMENDED_RESOURCES = ResourceFloor(
    memory_bytes=4 * GIB,
    cpu_count=2,
    disk_bytes=20 * GIB,
)


def _windows_memory_bytes() -> int:
    try:
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    except (AttributeError, OSError, ValueError):
        return 0
    return 0


def total_memory_bytes() -> int:
    """Physical memory, or ``0`` when the host does not report it."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return int(pages) * int(page_size)
    except (AttributeError, OSError, ValueError):
        pass
    if os.name == "nt":
        return _windows_memory_bytes()
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(":")
            if key == "MemTotal":
                return int(value.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return 0


def measure_resources(path: Optional[Path] = None) -> HostResources:
    """Measure memory, CPU count and free disk at ``path``."""
    target = Path(path) if path is not None else tests_root()
    try:
        free = shutil.disk_usage(target).free
    except OSError:
        free = 0
    return HostResources(
        memory_bytes=total_memory_bytes(),
        cpu_count=os.cpu_count() or 0,
        disk_bytes=free,
        disk_path=str(target),
    )


def _format_gib(value: int) -> str:
    return f"{value / GIB:.1f} GiB"


def _format_cpu(value: int) -> str:
    return f"{value} CPU" if value == 1 else f"{value} CPUs"


def check_resources(
    resources: Optional[HostResources] = None,
    *,
    minimum: ResourceFloor = MINIMUM_RESOURCES,
    recommended: ResourceFloor = RECOMMENDED_RESOURCES,
) -> CheckResult:
    """Report resources against the minimums; only a clear violation is hard."""
    check_id, group = "resources.host", "resources"
    measured = resources if resources is not None else measure_resources()

    below_minimum: List[str] = []
    below_recommended: List[str] = []
    comparisons = (
        (
            "memory",
            measured.memory_bytes,
            minimum.memory_bytes,
            recommended.memory_bytes,
            _format_gib,
        ),
        (
            "CPU",
            measured.cpu_count,
            minimum.cpu_count,
            recommended.cpu_count,
            _format_cpu,
        ),
        (
            "free disk",
            measured.disk_bytes,
            minimum.disk_bytes,
            recommended.disk_bytes,
            _format_gib,
        ),
    )
    for label, value, floor, target, render in comparisons:
        if value <= 0:
            continue
        if value < floor:
            below_minimum.append(
                f"{label} {render(value)} is below the {render(floor)} minimum"
            )
        elif value < target:
            below_recommended.append(
                f"{label} {render(value)} is below the {render(target)} recommendation"
            )

    reported = (
        f"{_format_gib(measured.memory_bytes) if measured.memory_bytes else 'unknown'} RAM, "
        f"{_format_cpu(measured.cpu_count) if measured.cpu_count else 'unknown CPU count'}, "
        f"{_format_gib(measured.disk_bytes) if measured.disk_bytes else 'unknown'} free disk"
    )
    details: Dict[str, object] = {
        "memory_bytes": measured.memory_bytes,
        "cpu_count": measured.cpu_count,
        "disk_bytes": measured.disk_bytes,
        "disk_path": measured.disk_path,
        "minimum": {
            "memory_bytes": minimum.memory_bytes,
            "cpu_count": minimum.cpu_count,
            "disk_bytes": minimum.disk_bytes,
        },
        "recommended": {
            "memory_bytes": recommended.memory_bytes,
            "cpu_count": recommended.cpu_count,
            "disk_bytes": recommended.disk_bytes,
        },
    }

    if below_minimum:
        return CheckResult(
            check_id,
            group,
            FAIL,
            f"{reported}: " + "; ".join(below_minimum),
            details,
        )
    if below_recommended:
        return CheckResult(
            check_id,
            group,
            WARN,
            f"{reported}: " + "; ".join(below_recommended),
            details,
        )
    return CheckResult(check_id, group, PASS, reported, details)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


@dataclass
class Probes:
    """Injectable host probes. Every default is side-effect free."""

    which: Which = shutil.which
    run: CommandRunner = run_command
    free_port: PortProbe = port_is_free
    listeners: Optional[ListenerMap] = None
    resources: Optional[Callable[[], HostResources]] = None
    environ: Optional[Mapping[str, str]] = None


def run_preflight(
    *,
    skip: Sequence[str] = (),
    artifacts: Optional[Path] = None,
    repo: Optional[Path] = None,
    network: Optional[Network] = None,
    expected_sdk_version: Optional[str] = None,
    probes: Optional[Probes] = None,
) -> PreflightReport:
    """Run every check not in ``skip`` and return the report.

    Nothing here mutates the host. The static groups (``compose``, ``pins``,
    ``layout``, ``resources``) never touch Docker, the SDK or the network, so
    CI can run them with ``--skip docker,sdk,ports``.
    """
    active = probes or Probes()
    skipped = parse_skip(tuple(skip))
    repo_path = Path(repo) if repo is not None else repo_root()
    artifacts_path = Path(artifacts) if artifacts is not None else tests_root() / "artifacts"
    environ = active.environ if active.environ is not None else os.environ
    listeners = active.listeners or (lambda: ss_processes(active.run, active.which))
    measure = active.resources or measure_resources

    network_value = network
    network_error: Optional[str] = None
    if network_value is None:
        try:
            network_value = load_network()
        except ConfigError as error:
            network_error = str(error)

    def ports_check() -> CheckResult:
        if network_value is None:
            return CheckResult(
                "ports.loopback",
                "ports",
                FAIL,
                network_error or "tests/config/network.yaml could not be loaded",
            )
        return check_ports(
            network_value, free=active.free_port, listeners=listeners
        )

    factories: Dict[str, Callable[[], CheckResult]] = {
        "docker.engine": lambda: check_engine(active.run, active.which),
        "docker.compose": lambda: check_compose_version(active.run, active.which),
        "docker.compose-config": lambda: check_compose_config(
            active.run, active.which
        ),
        "compose.files": lambda: check_compose_files(),
        "sdk.layout": lambda: check_sdk(
            environ, expected_version=expected_sdk_version
        ),
        "pins.images": lambda: check_image_pins(),
        "ports.loopback": ports_check,
        "layout.repository": lambda: check_repository_layout(repo_path),
        "layout.artifacts": lambda: check_artifacts(artifacts_path),
        "resources.host": lambda: check_resources(measure()),
    }

    report = PreflightReport(skipped_groups=skipped)
    for check_id, group in CHECKS:
        if group in skipped:
            report.add(
                CheckResult(
                    check_id,
                    group,
                    SKIP,
                    f"skipped by --skip {group}",
                )
            )
            continue
        report.add(factories[check_id]())
    return report