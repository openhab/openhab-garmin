"""``e2e`` command-line interface.

Subcommands:

    setup             validate compose, SDK, openHAB and the shim; seed nothing
    run               run one or more scenarios and write reports
    reset             clear the shim's script and request trace
    status            one-shot health summary of every component
    list-scenarios    list the scenarios that would run
    variants          show build-variant coverage of the device matrix
    coverage          map #140 scope entries to scenarios and check for gaps
    contract-test     exercise a simulator driver against the driver contract
    preflight         check the host before the stack is started (read-only)
    validate-compose  static validation of the Compose stack (no Docker needed)
    network-env       render/verify the generated loopback network block in .env.example
    pin-images        resolve/verify image digests from Docker Hub
    sdk-check         validate the mounted Connect IQ SDK
    link-devices      stage the Connect IQ device definitions where monkeyc looks
    health-snapshot   record the stack's restart counts and memory before/after a run
    build             compile the PRG for one or more devices

Exit codes: ``0`` success, ``1`` scenario failures, ``2`` configuration or
environment error, ``3`` invalid environment (a container restarted or was
killed during the run, so the results are not attributable to the app).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import compose as compose_module
from . import health as health_module
from . import network as network_module
from . import reporting as reporting_module
from .build import BuildError, build_prg, ephemeral_developer_key
from .compose import ComposeError
from .config import (
    ConfigError,
    Device,
    default_device_id,
    find_device,
    load_devices,
    read_sdk_version,
)
from .contract import format_report as format_contract_report
from .contract import run_contract
from .contract import exit_code as contract_exit_code
from .contract import summary as contract_summary
from .coverage import (
    COVERAGE_DOC,
    CoverageProblem,
    build_report,
    document_is_current,
    report_document,
    write_markdown,
)
from .engine import run_scenario
from .health import EXIT_INVALID_ENVIRONMENT, HealthError
from .openhab import OpenHabClient, OpenHabError
from .paths import repo_root, tests_root
from .preflight import parse_skip, run_preflight
from .reporting import Report
from .scenarios import Scenario, ScenarioError, discover_scenarios, find_scenarios
from .sdk import (
    SdkError,
    describe_candidates,
    device_definition_count,
    link_devices,
    load_layout,
)
from .settings import SettingsError
from .shim import ShimClient, ShimError
from .sim import DRIVER_NAMES, create_driver
from .variants import (
    check_coverage,
    combinations,
    format_matrix_table,
    load_builds,
    load_waivers,
    ui_coverage_problems,
)

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_CONFIG = 2

DEFAULT_OPENHAB_URL = "http://localhost:8080"
DEFAULT_SHIM_URL = "http://localhost:8081"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _openhab_url(args: argparse.Namespace) -> str:
    return args.openhab_url or os.environ.get("E2E_OPENHAB_URL") or DEFAULT_OPENHAB_URL


def _shim_url(args: argparse.Namespace) -> str:
    return args.shim_url or os.environ.get("E2E_SHIM_URL") or DEFAULT_SHIM_URL


def _artifacts_dir(args: argparse.Namespace) -> Path:
    value = getattr(args, "artifacts", None) or os.environ.get("E2E_ARTIFACTS")
    return Path(value) if value else tests_root() / "artifacts"


def _goldens_dir(args: argparse.Namespace) -> Path:
    value = getattr(args, "goldens", None)
    return Path(value) if value else tests_root() / "goldens"


def _developer_key(artifacts: Path, args: argparse.Namespace):
    """An explicit ``--developer-key``, or a fresh ephemeral key for this run.

    The key is throwaway by design: it signs a build for the simulator, it is
    never reused and never committed. See
    :func:`e2e.build.ephemeral_developer_key`.
    """
    explicit = getattr(args, "developer_key", None)
    if explicit:
        return contextlib.nullcontext(Path(explicit))
    return ephemeral_developer_key(artifacts)


def _jungle_override(args: argparse.Namespace) -> Optional[Path]:
    """An explicit ``--jungle``, or ``None`` to generate a device-scoped one."""
    explicit = getattr(args, "jungle", None)
    return Path(explicit) if explicit else None


def _emit_json(document: Mapping[str, Any]) -> None:
    print(json.dumps(document, indent=2))


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return EXIT_CONFIG


# ---------------------------------------------------------------------------
# status / setup / reset
# ---------------------------------------------------------------------------


def _cmd_status(args: argparse.Namespace) -> int:
    report: Dict[str, Any] = {}

    compose_problems = compose_module.validate_compose()
    report["compose"] = {"ok": not compose_problems, "problems": compose_problems}

    try:
        layout = load_layout()
        report["sdk"] = {
            "ok": True,
            "root": str(layout.root),
            "version": layout.version,
            "monkeyc": str(layout.monkeyc),
            "simulator": str(layout.simulator) if layout.simulator else None,
        }
    except SdkError as error:
        report["sdk"] = {"ok": False, "error": str(error)}

    openhab = OpenHabClient(_openhab_url(args))
    report["openhab"] = {"ok": openhab.ping(), "url": openhab.base_url}

    shim = ShimClient(_shim_url(args))
    try:
        report["shim"] = {"ok": True, "url": shim.base_url, "health": dict(shim.health())}
    except ShimError as error:
        report["shim"] = {"ok": False, "url": shim.base_url, "error": str(error)}

    report["pins"] = compose_module.check_pins()
    verdict = health_module.check()
    report["stack"] = verdict.to_document()

    if args.json:
        _emit_json(report)
    else:
        print(f"compose:  {'ok' if report['compose']['ok'] else 'problems'}")
        for problem in compose_problems:
            print(f"  - {problem}")
        print(f"sdk:      {'ok' if report['sdk']['ok'] else 'not usable'}")
        if not report["sdk"]["ok"]:
            print(f"  - {report['sdk']['error']}")
        print(f"openHAB:  {'available' if report['openhab']['ok'] else 'unreachable'} ({openhab.base_url})")
        print(f"shim:     {'available' if report['shim']['ok'] else 'unreachable'} ({shim.base_url})")
        if report["shim"]["ok"]:
            health = report["shim"]["health"]
            print(
                f"          upstream={health.get('upstream')} "
                f"rules={health.get('rules')} requests={health.get('requests')}"
            )
        print(f"stack:    {verdict.verdict} - {verdict.summary}")
        for container in verdict.containers:
            print(
                f"          {container.name}: state={container.state or 'unknown'} "
                f"health={container.health or 'no healthcheck'} "
                f"restarts={container.restart_count_after} "
                f"peak working set={health_module.format_bytes(container.peak_working_set_bytes)} "
                f"limit={health_module.describe_limit(container.memory_limit_bytes)}"
            )
        for problem in report["pins"]:
            print(f"pins:     {problem}")

    return EXIT_OK if report["compose"]["ok"] else EXIT_FAILURES


def _cmd_setup(args: argparse.Namespace) -> int:
    problems = compose_module.validate_compose()
    if problems:
        for problem in problems:
            print(f"compose: {problem}", file=sys.stderr)
        return _fail("the Compose stack does not validate; fix the problems above")

    layout = load_layout()
    print(f"sdk:     {layout.root} (version {layout.version})")

    openhab = OpenHabClient(_openhab_url(args))
    openhab.wait_ready(args.timeout)
    print(f"openHAB: ready at {openhab.base_url}")

    shim = ShimClient(_shim_url(args))
    shim.wait_ready(args.timeout)
    print(f"shim:    ready, {shim.describe()}")

    items = {item.name for item in openhab.items()}
    print(f"items:   {len(items)} item(s) loaded")

    payload = openhab.sitemap_json(args.sitemap)
    if not payload:
        return _fail(f"sitemap {args.sitemap!r} is empty")
    referenced = openhab.item_names_in_sitemap(args.sitemap)
    missing = [name for name in referenced if name not in items]
    if missing:
        return _fail(
            f"sitemap {args.sitemap!r} references item(s) that are not defined: "
            + ", ".join(missing)
            + ". Fix tests/fixtures/openhab/conf/items/."
        )
    print(f"sitemap: {args.sitemap!r} resolves, {len(referenced)} item reference(s) all defined")
    print("setup:   ok")
    return EXIT_OK


def _cmd_reset(args: argparse.Namespace) -> int:
    shim = ShimClient(_shim_url(args))
    shim.reset()
    print(f"shim:     script and request trace cleared ({shim.base_url})")
    if args.items:
        openhab = OpenHabClient(_openhab_url(args))
        for item in args.items:
            openhab.set_state(item, "NULL")
        print(f"openHAB:  {len(args.items)} item state(s) reset to NULL")
    return EXIT_OK


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _select_scenarios(args: argparse.Namespace) -> List[Scenario]:
    if args.suite:
        return discover_scenarios(suite=args.suite)
    if args.all:
        return discover_scenarios()
    if args.scenarios:
        selected: List[Scenario] = []
        for identifier in args.scenarios:
            selected.extend(find_scenarios(identifier))
        return selected
    raise ScenarioError(
        "nothing to run: pass one or more scenario names, --suite or --all"
    )


def _cmd_run(args: argparse.Namespace) -> int:
    scenarios = _select_scenarios(args)
    if not scenarios:
        return _fail("no scenarios matched")

    dry_run = bool(args.dry_run)
    driver_name = args.driver or ("null" if dry_run else "wine")
    if driver_name not in DRIVER_NAMES:
        return _fail(f"unknown driver {driver_name!r}; known: {', '.join(DRIVER_NAMES)}")

    layout = None
    if not dry_run:
        layout = load_layout()

    artifacts = _artifacts_dir(args)
    goldens = _goldens_dir(args)
    report = Report()
    built: Dict[str, Path] = {}

    environment_before, environment_after = _environment_window(dry_run)

    # A dry run and a pre-built PRG need no key at all, so do not require
    # openssl for them.
    if layout is None or args.prg:
        key_context = contextlib.nullcontext(None)
    else:
        key_context = _developer_key(artifacts, args)

    with key_context as developer_key:
        for scenario in scenarios:
            device = _device_for(scenario, args)
            prg: Optional[Path] = None
            if layout is not None:
                if args.prg:
                    prg = Path(args.prg)
                else:
                    if device.id not in built:
                        built_prg = build_prg(
                            layout,
                            device.product,
                            artifacts / "build",
                            jungle=_jungle_override(args),
                            developer_key=developer_key,
                        )
                        print(f"jungle: {built_prg.jungle_note}")
                        print(f"built {built_prg.prg} in {built_prg.duration_ms:.0f} ms")
                        built[device.id] = built_prg.prg
                    prg = built[device.id]

            print(f"--- {scenario.scenario_id} ({scenario.tier}) device={device.id}")
            outcome = run_scenario(
                scenario,
                device,
                openhab_url=_openhab_url(args),
                shim_url=_shim_url(args),
                artifacts_dir=artifacts,
                goldens_dir=goldens,
                driver_name=driver_name,
                dry_run=dry_run,
                update_goldens=args.update_goldens,
                skip_events=args.skip_events,
                sdk_layout=layout,
                prg=prg,
                log=lambda message: print(f"    {message}"),
            )
            report.add(outcome)
            print(
                f"--- {scenario.scenario_id}: {outcome.status} "
                f"({outcome.duration_ms / 1000.0:.1f}s, {outcome.requests} request(s))"
            )
            if outcome.failures:
                for failure in outcome.failures:
                    print(f"    {failure}")

    if environment_after is None and environment_before.available:
        environment_after, _recording = health_module.capture()
    if environment_after is None:
        environment_after = environment_before
    report.environment = health_module.classify(environment_before, environment_after)
    for line in report.environment.render_lines():
        print(line)

    written = report.write_artifacts(artifacts)
    for name, path in written.items():
        print(f"{name}: {path}")

    if args.json:
        _emit_json(
            {
                "counts": report.counts(),
                "environment": report.environment_document(),
                "exit_code": report.exit_code,
                "results": [result.to_document() for result in report.results],
            }
        )

    return report.exit_code


def _environment_window(
    dry_run: bool,
) -> Tuple[health_module.HealthSnapshot, Optional[health_module.HealthSnapshot]]:
    """The before snapshot for the run window, taken before the scenarios.

    A dry run never probes Docker. When the probe cannot reach the daemon (the
    runner container has no Docker socket by design) the snapshot records why,
    and the verdict stays ``unknown`` instead of claiming health. A host-side
    window captured with ``e2e health-snapshot`` can classify the finished run's
    artifacts afterwards.
    """
    if dry_run:
        snapshot, _recording = health_module.capture(dry_run=True)
        return snapshot, snapshot
    snapshot, _recording = health_module.capture()
    return snapshot, None


def _cmd_health_snapshot(args: argparse.Namespace) -> int:
    path = health_module.write_snapshot_phase(args.output, args.phase)
    print(f"health:   {args.phase} snapshot written to {path}")
    if args.phase != "after":
        return EXIT_OK

    before, after = health_module.load_snapshot_file(path)
    verdict = health_module.classify(before, after)
    for line in verdict.render_lines():
        print(line)

    artifacts = Path(args.artifacts) if args.artifacts else None
    if artifacts is not None and (artifacts / "report.json").is_file():
        try:
            report = reporting_module.load_report(artifacts)
        except ValueError as error:
            return _fail(str(error))
        report.environment = verdict
        written = report.write_artifacts(artifacts)
        print(f"environment: {written['environment']}")
        print(f"json: {written['json']}")
        print(f"junit: {written['junit']}")
        print(f"markdown: {written['markdown']}")
    elif artifacts is not None:
        print(
            f"health:   {artifacts} has no report.json; nothing to re-render",
            file=sys.stderr,
        )

    return EXIT_INVALID_ENVIRONMENT if verdict.invalid else EXIT_OK


def _device_for(scenario: Scenario, args: argparse.Namespace) -> Device:
    if args.device:
        return find_device(args.device)
    if scenario.device != "any":
        return find_device(scenario.device)
    return find_device(default_device_id())


# ---------------------------------------------------------------------------
# maintenance commands
# ---------------------------------------------------------------------------


def _cmd_variants(args: argparse.Namespace) -> int:
    builds = load_builds()
    devices_path = Path(args.devices) if args.devices else None
    devices = load_devices(devices_path) if devices_path else load_devices()
    waivers = load_waivers(devices_path) if devices_path else load_waivers()
    problems = check_coverage(builds, devices, waivers) + ui_coverage_problems(builds, devices)

    if args.json:
        _emit_json(
            {
                "products": len(builds),
                "combinations": [
                    {
                        "variants": dict(combination.variants),
                        "products": list(combination.products),
                        "devices": sorted(
                            device.id
                            for device in devices
                            if all(
                                tag in device.variants
                                for tag in combination.variant_tags
                            )
                        ),
                    }
                    for combination in combinations(builds)
                ],
                "devices": [
                    {"id": device.id, "product": device.product, "tiers": list(device.tiers_run)}
                    for device in devices
                ],
                "waivers": [
                    {"variants": list(waiver.variants), "reason": waiver.reason}
                    for waiver in waivers
                ],
                "problems": [problem.detail for problem in problems],
                "ok": not problems,
            }
        )
    else:
        tier_counts = {
            tier: sum(1 for device in devices if tier in device.tiers_run)
            for tier in ("protocol", "ui")
        }
        print(f"products:     {len(builds)}")
        print(f"combinations: {len(combinations(builds))}")
        print(
            f"devices:      {len(devices)} "
            f"(protocol {tier_counts['protocol']}, ui {tier_counts['ui']})"
        )
        print(f"waivers:      {len(waivers)}")
        print()
        print(format_matrix_table(builds, devices, waivers))
        if problems:
            print()
            for problem in problems:
                print(f"coverage: {problem}", file=sys.stderr)

    return EXIT_FAILURES if problems else EXIT_OK


def _cmd_coverage(args: argparse.Namespace) -> int:
    report = build_report()
    problems = list(report.problems)

    document_current = document_is_current(report)
    if args.check and not document_current:
        problems.append(
            CoverageProblem(
                "stale-document",
                f"{COVERAGE_DOC} is out of date; run `python -m e2e coverage --write`",
            )
        )

    if args.write:
        output = Path(args.output) if args.output else COVERAGE_DOC
        path = write_markdown(report, output)
        document_current = True
        print(f"wrote {path}")

    if args.json:
        document = dict(report_document(report))
        document["document_current"] = document_current
        document["ok"] = not problems
        document["problems"] = [str(problem) for problem in problems]
        _emit_json(document)
    else:
        print(f"scope entries: {len(report.entries)}")
        print(f"covered:       {len(report.covered)}")
        print(f"waived:        {len(report.waived)}")
        print(f"empty:         {len(report.empty)}")
        print(f"scenarios:     {report.scenario_files} file(s), {len(report.scenarios)} run(s)")
        if args.check:
            print(f"document:      {'current' if document_current else 'stale'}")
        for problem in problems:
            print(f"coverage: {problem}", file=sys.stderr)

    return EXIT_FAILURES if problems else EXIT_OK


def _cmd_list_scenarios(args: argparse.Namespace) -> int:
    scenarios = discover_scenarios(suite=args.suite)
    if args.tier:
        scenarios = [scenario for scenario in scenarios if scenario.tier == args.tier]

    if args.json:
        _emit_json(
            [
                {
                    "scenario": scenario.scenario_id,
                    "tier": scenario.tier,
                    "device": scenario.device,
                    "mode": scenario.mode,
                    "fixture": scenario.fixture,
                    "language": scenario.language,
                    "timeout_s": scenario.timeout_s,
                    "requires_trace_events": scenario.requires_trace_events,
                    "source": str(scenario.source),
                }
                for scenario in scenarios
            ]
        )
        return EXIT_OK

    for scenario in scenarios:
        print(
            f"{scenario.scenario_id:<40} {scenario.tier:<9} "
            f"device={scenario.device:<12} mode={scenario.mode:<8} {scenario.description}"
        )
    print(f"\n{len(scenarios)} scenario(s)")
    return EXIT_OK


def _cmd_contract_test(args: argparse.Namespace) -> int:
    layout = None
    if args.driver == "wine":
        layout = load_layout()
    driver = create_driver(args.driver, layout=layout, log=lambda message: print(message))
    workdir = _artifacts_dir(args) / "contract" / args.driver
    cases = run_contract(driver, workdir, device_id=args.device)
    print(format_contract_report(cases))
    counts = contract_summary(cases)
    print(f"\n{counts}")
    if args.json:
        _emit_json(
            [{"name": case.name, "status": case.status, "detail": case.detail} for case in cases]
        )
    return contract_exit_code(cases)


def _cmd_preflight(args: argparse.Namespace) -> int:
    report = run_preflight(
        skip=parse_skip(args.skip),
        artifacts=_artifacts_dir(args),
        repo=repo_root(),
    )
    if args.json:
        _emit_json(report.to_document())
    else:
        for line in report.render_lines():
            print(line)
    return report.exit_code


def _cmd_validate_compose(args: argparse.Namespace) -> int:
    problems = compose_module.validate_compose(
        path=Path(args.compose) if args.compose else compose_module.COMPOSE_FILE,
        env_path=Path(args.env) if args.env else None,
    )
    if args.check_pins:
        problems += compose_module.check_pins(
            env_path=Path(args.env) if args.env else compose_module.ENV_FILE
        )
    for problem in problems:
        print(problem)
    if problems:
        return EXIT_FAILURES
    print("compose: valid")
    return EXIT_OK


def _cmd_pin_images(args: argparse.Namespace) -> int:
    if args.write:
        resolved = compose_module.pin_images(write=True, refresh=args.refresh)
        for key, value in resolved.items():
            print(f"{key}={value}")
        print(f"written: {compose_module.ENV_FILE}")
        return EXIT_OK

    problems = compose_module.check_pins()
    for problem in problems:
        print(problem)
    if problems:
        return EXIT_FAILURES
    print("pins: ok")
    return EXIT_OK


def _cmd_network_env(args: argparse.Namespace) -> int:
    path = Path(args.env) if args.env else compose_module.ENV_EXAMPLE
    try:
        network = network_module.load_network()
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIG
    if args.write:
        try:
            written = network_module.write_env_block(path, network)
        except ConfigError as error:
            print(f"error: {error}", file=sys.stderr)
            return EXIT_CONFIG
        for key, value in network.compose_values().items():
            print(f"{key}={value}")
        print(f"written: {written}")
        return EXIT_OK
    problems = network_module.check_env_block(path, network)
    for problem in problems:
        print(problem)
    if problems:
        return EXIT_FAILURES
    print("network: ok")
    return EXIT_OK


def _cmd_sdk_check(args: argparse.Namespace) -> int:
    try:
        layout = load_layout()
    except SdkError as error:
        if not args.quiet:
            print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIG
    if not args.quiet:
        print(f"root:      {layout.root}")
        print(f"version:   {layout.version} (pinned {read_sdk_version()})")
        print(f"monkeyc:   {layout.monkeyc}")
        print(f"monkeydo:  {layout.monkeydo or '<not present>'}")
        print(f"simulator: {layout.simulator or '<not present>'}")
        if layout.devices:
            print(
                f"devices:   {layout.devices} "
                f"({device_definition_count(layout.devices)} definitions)"
            )
        else:
            print("devices:   <not found>")
        for line in describe_candidates():
            print(f"  {line}")
    return EXIT_OK


def _cmd_link_devices(args: argparse.Namespace) -> int:
    """Stage the device definitions where ``monkeyc`` and the simulator look."""
    try:
        layout = load_layout()
        links = link_devices(
            layout.root,
            Path(args.target) if args.target else None,
        )
    except SdkError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIG
    if not args.quiet:
        print(
            f"devices:   {links.linux} "
            f"({device_definition_count(links.linux)} definitions)"
        )
        print(f"wine:      {links.wine}")
    return EXIT_OK


def _cmd_build(args: argparse.Namespace) -> int:
    layout = load_layout()
    artifacts = _artifacts_dir(args)
    output = Path(args.output) if args.output else artifacts / "build"
    device_ids = args.device or [default_device_id()]
    with _developer_key(artifacts, args) as developer_key:
        for device_id in device_ids:
            device = find_device(device_id)
            result = build_prg(
                layout,
                device.product,
                output,
                jungle=_jungle_override(args),
                release=args.release,
                developer_key=developer_key,
            )
            print(f"{device.id}: {result.jungle_note}")
            print(f"{device.id}: {result.prg} ({result.duration_ms:.0f} ms)")
    return EXIT_OK


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="e2e",
        description="E2E harness for the openHAB Garmin app",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes: 0 success, 1 scenario failures, 2 configuration error,\n"
            "3 invalid environment (a stack container restarted or was killed during\n"
            "the run, so the results are not attributable to the app).\n"
            "Every command that needs openHAB or the shim reads E2E_OPENHAB_URL and\n"
            "E2E_SHIM_URL first, then falls back to localhost."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--openhab-url", help="openHAB base URL (default: E2E_OPENHAB_URL or %s)" % DEFAULT_OPENHAB_URL)
        sub.add_argument("--shim-url", help="shim base URL (default: E2E_SHIM_URL or %s)" % DEFAULT_SHIM_URL)
        sub.add_argument("--artifacts", help="artifacts directory (default: E2E_ARTIFACTS or tests/artifacts)")
        sub.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    status = subparsers.add_parser("status", help="show the health of every component")
    add_common(status)
    status.set_defaults(handler=_cmd_status)

    setup = subparsers.add_parser("setup", help="validate compose, SDK, openHAB and the shim")
    add_common(setup)
    setup.add_argument("--timeout", type=float, default=180.0, help="readiness timeout in seconds")
    setup.add_argument("--sitemap", default="garmin_e2e", help="sitemap to resolve as a fixture check")
    setup.set_defaults(handler=_cmd_setup)

    reset = subparsers.add_parser("reset", help="clear the shim script and request trace")
    add_common(reset)
    reset.add_argument("--items", nargs="*", default=[], help="item names to reset to NULL")
    reset.set_defaults(handler=_cmd_reset)

    run = subparsers.add_parser("run", help="run one or more scenarios")
    add_common(run)
    run.add_argument("scenarios", nargs="*", help="scenario names (suite/name, name, or a path)")
    run.add_argument("--all", action="store_true", help="run every scenario")
    run.add_argument("--suite", choices=("smoke", "regression", "extended"), help="run one suite")
    run.add_argument("--device", help="override the device for every scenario")
    run.add_argument("--driver", choices=DRIVER_NAMES, help="simulator driver (default: wine, or null with --dry-run)")
    run.add_argument("--dry-run", action="store_true", help="no Docker, no SDK, no simulator: exercise scenario wiring only")
    run.add_argument("--update-goldens", action="store_true", help="replace golden images with the current screenshots")
    run.add_argument("--skip-events", action="store_true", help="skip assertions that need the console event stream")
    run.add_argument("--prg", help="use an existing PRG instead of building one")
    run.add_argument(
        "--jungle",
        help=(
            "use this jungle for the build instead of generating a "
            "device-scoped one; requires an SDK that defines every device the "
            "file names"
        ),
    )
    run.add_argument(
        "--developer-key",
        help=(
            "monkeyc signing key; default: a throwaway key generated per run "
            "under the artifacts directory"
        ),
    )
    run.add_argument("--goldens", help="goldens directory (default: tests/goldens)")
    run.set_defaults(handler=_cmd_run)

    listing = subparsers.add_parser("list-scenarios", help="list scenarios")
    listing.add_argument("--suite", choices=("smoke", "regression", "extended"))
    listing.add_argument("--tier", choices=("protocol", "ui"))
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(handler=_cmd_list_scenarios)

    variants = subparsers.add_parser(
        "variants", help="show build-variant coverage of the device matrix"
    )
    variants.add_argument("--devices", help="device matrix (default: tests/config/devices.yaml)")
    variants.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    variants.set_defaults(handler=_cmd_variants)

    coverage = subparsers.add_parser(
        "coverage", help="map the #140 scope to scenarios and check for gaps"
    )
    coverage.add_argument(
        "--write",
        action="store_true",
        help="regenerate tests/docs/coverage.md from the scope and scenario metadata",
    )
    coverage.add_argument(
        "--check",
        action="store_true",
        help="also fail when tests/docs/coverage.md is out of date",
    )
    coverage.add_argument("--output", help="coverage document path (default: tests/docs/coverage.md)")
    coverage.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    coverage.set_defaults(handler=_cmd_coverage)

    contract = subparsers.add_parser("contract-test", help="exercise a simulator driver against its contract")
    contract.add_argument("--driver", choices=DRIVER_NAMES, default="null")
    contract.add_argument("--device")
    contract.add_argument("--artifacts")
    contract.add_argument("--json", action="store_true")
    contract.set_defaults(handler=_cmd_contract_test)

    preflight = subparsers.add_parser(
        "preflight",
        help="check the host before the stack is started (read-only)",
        description=(
            "Check Docker, Docker Compose, the Connect IQ SDK, the loopback ports, "
            "the Compose stack, the image pins, the checkout layout and the host "
            "resources. One pass/fail per check; exit 1 when a hard check fails."
        ),
    )
    preflight.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    preflight.add_argument("--artifacts", help="artifacts directory (default: E2E_ARTIFACTS or tests/artifacts)")
    preflight.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="GROUP[,GROUP]",
        help=(
            "skip a check group; may be repeated: "
            "docker, compose, sdk, pins, ports, layout, resources"
        ),
    )
    preflight.set_defaults(handler=_cmd_preflight)

    validate = subparsers.add_parser("validate-compose", help="validate the Compose stack without Docker")
    validate.add_argument("--compose", help="path to compose.yaml")
    validate.add_argument("--env", help="path to the environment file")
    validate.add_argument("--check-pins", action="store_true", help="also require pinned image digests")
    validate.set_defaults(handler=_cmd_validate_compose)

    network = subparsers.add_parser(
        "network-env",
        help="render or verify the generated loopback network block in .env.example",
    )
    network.add_argument(
        "--write",
        action="store_true",
        help="rewrite tests/compose/.env.example from tests/config/network.yaml",
    )
    network.add_argument("--check", action="store_true", help="verify the block (default)")
    network.add_argument("--env", help="path to the environment example")
    network.set_defaults(handler=_cmd_network_env)

    pin = subparsers.add_parser("pin-images", help="resolve or verify image digests")
    pin.add_argument("--write", action="store_true", help="write tests/compose/.env from the committed pins")
    pin.add_argument("--check", action="store_true", help="verify the existing pins (default)")
    pin.add_argument(
        "--refresh",
        action="store_true",
        help="ask Docker Hub for the current digests instead of using the committed pins",
    )
    pin.set_defaults(handler=_cmd_pin_images)

    sdk = subparsers.add_parser("sdk-check", help="validate the mounted Connect IQ SDK")
    sdk.add_argument("--quiet", action="store_true")
    sdk.set_defaults(handler=_cmd_sdk_check)

    devices = subparsers.add_parser(
        "link-devices",
        help="stage the Connect IQ device definitions where monkeyc looks for them",
        description=(
            "Copy the SDK's device definitions into the user-level Connect IQ "
            "directory monkeyc searches ($HOME/.Garmin/ConnectIQ/Devices). The "
            "repository's CI does the same; running it explicitly makes the "
            "step visible instead of folklore."
        ),
    )
    devices.add_argument(
        "--target",
        help="destination directory (default: $HOME/.Garmin/ConnectIQ/Devices)",
    )
    devices.add_argument("--quiet", action="store_true")
    devices.set_defaults(handler=_cmd_link_devices)

    snapshot = subparsers.add_parser(
        "health-snapshot",
        help="record stack restart counts and memory for the before/after run window",
    )
    snapshot.add_argument(
        "--phase",
        required=True,
        choices=("before", "after"),
        help="before the run, then again after it; both phases go in one file",
    )
    snapshot.add_argument(
        "--output",
        required=True,
        help="snapshot file shared by both phases (e.g. tests/artifacts/health-window.json)",
    )
    snapshot.add_argument(
        "--artifacts",
        help=(
            "with --phase after: the run's artifacts directory; the finished "
            "window is classified and its environment block is re-rendered there"
        ),
    )
    snapshot.set_defaults(handler=_cmd_health_snapshot)

    build = subparsers.add_parser("build", help="compile the PRG")
    build.add_argument("--device", nargs="*", help="device ids (default: the matrix default)")
    build.add_argument("--output", help="output directory")
    build.add_argument(
        "--jungle",
        help=(
            "use this jungle instead of generating a device-scoped one; "
            "requires an SDK that defines every device the file names"
        ),
    )
    build.add_argument("--release", action="store_true", help="strip debug annotations (breaks event assertions)")
    build.add_argument(
        "--developer-key",
        help=(
            "monkeyc signing key; default: a throwaway key generated per build "
            "under the artifacts directory"
        ),
    )
    build.add_argument("--artifacts")
    build.set_defaults(handler=_cmd_build)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_CONFIG

    try:
        return handler(args)
    except (
        ConfigError,
        ComposeError,
        SdkError,
        BuildError,
        SettingsError,
        ShimError,
        OpenHabError,
        HealthError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIG
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_FAILURES


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
