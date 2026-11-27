"""``e2e`` command-line interface.

Subcommands:

    setup             validate compose, SDK, openHAB, the shim and the fixture sets
    verify-fixtures   assert openHAB loaded the expected items and sitemaps
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
    link-fonts        stage the device font pack the simulator draws with
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
from . import fixtures as fixtures_module
from . import health as health_module
from . import network as network_module
from . import reporting as reporting_module
from .build import (
    BuildError,
    PROFILE_WATCH_APP,
    PROFILE_WITH_GLANCE,
    PROFILE_WITHOUT_GLANCE,
    build_prg,
    describe_profile,
    ephemeral_developer_key,
)
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
from .contract import ContractError
from .contract import device_from_prg
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
from .fixtures import FixtureError
from .health import EXIT_INVALID_ENVIRONMENT, HealthError
from .languages import (
    LANGUAGE_INDEX_FILE,
    LanguageIndexError,
    load_language_index,
    write_language_index,
)
from .openhab import OpenHabClient, OpenHabError
from .paths import repo_root, tests_root
from .preflight import parse_skip, run_preflight
from .reporting import Report
from .scenarios import Scenario, ScenarioError, discover_scenarios, find_scenarios
from .sdk import (
    SdkError,
    describe_candidates,
    device_definition_count,
    font_asset_count,
    fonts_dir,
    fonts_problem,
    link_devices,
    link_fonts,
    load_layout,
    require_fonts,
)
from .settings import SettingsError, load_settings
from .shim import ShimClient, ShimError
from .sim import DRIVER_NAMES, Button, create_driver
from .sim.wine import ENV_KEY_PREFIX
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

    try:
        fixture_report = fixtures_module.verify(openhab, args.profile, check_sizes=True)
    except FixtureError as error:
        print("fixtures: FAILED", file=sys.stderr)
        print(str(error), file=sys.stderr)
        return EXIT_FAILURES
    print(f"fixtures: {fixture_report.summary()}")
    for name, size in fixture_report.sitemap_bytes:
        print(f"sitemap: {name} = {size} bytes")

    payload = openhab.sitemap_json(args.sitemap)
    if not payload:
        return _fail(f"sitemap {args.sitemap!r} is empty")
    referenced = openhab.item_names_in_sitemap(args.sitemap)
    items = {item.name for item in openhab.items()}
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


def _cmd_verify_fixtures(args: argparse.Namespace) -> int:
    if args.dump_dir and not args.sizes:
        return _fail("--dump-dir requires --sizes")
    openhab = OpenHabClient(_openhab_url(args))
    try:
        openhab.wait_ready(args.timeout)
        report = fixtures_module.verify(
            openhab,
            args.profile,
            expected_path=Path(args.expected) if args.expected else None,
            log_paths=[Path(path) for path in args.log],
            log_dir=Path(args.log_dir) if args.log_dir else None,
            strict=args.strict,
            check_sizes=args.sizes,
            dump_dir=Path(args.dump_dir) if args.dump_dir else None,
            wait_s=args.wait,
        )
    except (FixtureError, OpenHabError) as error:
        print("fixtures: FAILED", file=sys.stderr)
        print(str(error), file=sys.stderr)
        return EXIT_FAILURES

    if args.json:
        _emit_json(
            {
                "ok": True,
                "profile": report.profile,
                "items": report.items,
                "sitemaps": report.sitemaps,
                "warnings": list(report.warnings),
                "sitemap_bytes": dict(report.sitemap_bytes),
            }
        )
    else:
        print(f"fixtures: {report.summary()}")
        for name, size in report.sitemap_bytes:
            print(f"sitemap: {name} = {size} bytes")
        for block in report.warnings:
            print(block)
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


def _apply_key_overrides(args: argparse.Namespace) -> Optional[str]:
    """Apply ``--key BUTTON=KEYSYM`` overrides to the driver's environment."""
    for item in getattr(args, "key", None) or []:
        name, separator, keysym = item.partition("=")
        if not separator or not keysym.strip():
            return f"--key expects BUTTON=KEYSYM, got {item!r}"
        try:
            button = Button(name.strip().upper())
        except ValueError:
            known = ", ".join(button_.value for button_ in Button)
            return f"--key {item!r}: unknown button {name!r}; expected one of {known}"
        os.environ[f"{ENV_KEY_PREFIX}{button.value}"] = keysym.strip()
    return None


def _shim_request_probe(url: Optional[str]):  # type: ignore[no-untyped-def]
    """A callable returning the shim's request count, or ``None`` when unreachable."""
    if not url:
        return None
    client = ShimClient(url)

    def probe() -> Optional[int]:
        try:
            return len(client.requests())
        except ShimError:
            return None

    return probe


def _cmd_run(args: argparse.Namespace) -> int:
    invalid = _apply_key_overrides(args)
    if invalid:
        return _fail(invalid)
    scenarios = _select_scenarios(args)
    if not scenarios:
        return _fail("no scenarios matched")

    dry_run = bool(args.dry_run)
    driver_name = args.driver or ("null" if dry_run else "wine")
    if driver_name not in DRIVER_NAMES:
        return _fail(f"unknown driver {driver_name!r}; known: {', '.join(DRIVER_NAMES)}")

    # Golden capture is a rendering capability, not a property of the driver:
    # both the native and the Wine simulator paint once the device font pack is
    # in place. Without it the app crashes on its first draw, so a capture would
    # freeze a crash as the expected state.
    if args.update_goldens and not dry_run:
        try:
            require_fonts()
        except SdkError as error:
            print(f"error: {error}", file=sys.stderr)
            return EXIT_CONFIG

    if args.prg:
        print(
            "profile: supplied PRG; the build profile is not applied, so its "
            "app type and glance state are unknown (#396). Build with "
            f"--{PROFILE_WATCH_APP} (default), --{PROFILE_WITHOUT_GLANCE} or "
            "--with-glance instead to record it."
        )
    else:
        print(f"profile: {describe_profile(args.build_profile)}")

    layout = None
    if not dry_run:
        layout = load_layout()

    artifacts = _artifacts_dir(args)
    goldens = _goldens_dir(args)
    report = Report()
    built: Dict[Tuple[str, Any], Path] = {}

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
            settings = load_settings(scenario.fixture)
            if scenario.settings:
                settings = settings.with_overrides(scenario.settings)
            prg: Optional[Path] = None
            if layout is not None:
                if args.prg:
                    prg = Path(args.prg)
                else:
                    # Properties are compiled in, so the cache key is the
                    # device *and* the effective settings, not just the device.
                    cache_key = (device.id, _settings_key(settings))
                    if cache_key not in built:
                        built_prg = build_prg(
                            layout,
                            device.product,
                            artifacts / "build",
                            jungle=_jungle_override(args),
                            developer_key=developer_key,
                            settings=settings,
                            profile=args.build_profile,
                        )
                        print(f"jungle: {built_prg.jungle_note}")
                        print(f"profile: {built_prg.profile_note}")
                        print(f"command: {' '.join(built_prg.command)}")
                        print(
                            f"built {built_prg.prg} in {built_prg.duration_ms:.0f} ms"
                        )
                        if built_prg.settings_json is not None:
                            print(_describe_compiled_settings(built_prg))
                        built[cache_key] = built_prg.prg
                    prg = built[cache_key]

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
        profile_name = "supplied-prg" if args.prg else args.build_profile
        profile_note = (
            "the PRG was supplied with --prg, so the build profile was not applied"
            if args.prg
            else describe_profile(args.build_profile)
        )
        _emit_json(
            {
                "profile": {
                    "name": profile_name,
                    "glance": args.build_profile == PROFILE_WITH_GLANCE and not args.prg,
                    "note": profile_note,
                },
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
        print(f"blocked:       {len(report.blocked)}")
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
    invalid = _apply_key_overrides(args)
    if invalid:
        return _fail(invalid)
    layout = None
    if args.driver == "wine":
        layout = load_layout()
    workdir = _artifacts_dir(args) / "contract" / args.driver
    driver = create_driver(
        args.driver,
        layout=layout,
        artifact_dir=workdir,
        log=lambda message: print(message),
    )
    prg = Path(args.prg) if args.prg else None
    if prg is not None and not prg.is_file():
        return _fail(f"--prg {prg} is not a file")
    if prg is not None:
        derived = device_from_prg(prg)
        if derived is not None:
            print(f"device: {derived.id} (from {prg.name})")
    try:
        cases = run_contract(
            driver,
            workdir,
            device_id=args.device,
            prg=prg,
            requests=_shim_request_probe(args.shim_url),
            mode=args.mode,
            language=args.language,
        )
    except ContractError as error:
        driver.stop()
        return _fail(str(error))
    print(format_contract_report(cases))
    counts = contract_summary(cases)
    print(f"\n{counts}")
    if args.driver != "null" and prg is None:
        print(
            "\nnote: no --prg was supplied, so the cases that need a launched "
            "application were not exercised. Pass a PRG built by `python -m e2e "
            "build` to run them."
        )
    if args.json:
        _emit_json(
            [{"name": case.name, "status": case.status, "detail": case.detail} for case in cases]
        )
    return contract_exit_code(cases)


def _parse_index_range(spec: str) -> List[int]:
    """Parse ``0-24`` or ``0,1,2`` into a list of simulator language indices."""
    indices: List[int] = []
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        start, separator, end = part.partition("-")
        if separator:
            first, last = int(start), int(end)
            if last < first:
                raise ValueError(f"--indices {spec}: {part} counts down")
            indices.extend(range(first, last + 1))
        else:
            indices.append(int(part))
    seen: List[int] = []
    for index in indices:
        if index not in seen:
            seen.append(index)
    if not seen:
        raise ValueError(f"--indices {spec!r} names no index")
    return seen


def _check_writable(path: Path) -> Optional[str]:
    """Return an actionable message when ``path`` cannot be written, else None."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            pass
    except OSError as error:
        return (
            f"cannot write {path}: {error}\n"
            "A mounted checkout is often read-only (for example `/work` in the "
            "runner container). Re-run with a writable target and copy it back:\n"
            "  python -m e2e language-scan --prg <prg> --write --output "
            "/tmp/languages.json\n"
            f"  cp /tmp/languages.json {path}"
        )
    return None


def _cmd_language_scan(args: argparse.Namespace) -> int:
    """Enumerate the simulator's numeric ``Language`` index by launching the app."""
    if args.driver != "wine":
        return _fail(
            "language-scan needs a real simulator; use --driver wine on the guest"
        )
    prg = Path(args.prg)
    if not prg.is_file():
        return _fail(f"--prg {prg} is not a file")
    derived = device_from_prg(prg)
    device_id = args.device or (derived.id if derived is not None else None)
    if not device_id:
        return _fail(
            f"cannot derive a device from {prg.name}; pass --device <id> "
            "(the PRG names the build's device as openhab-garmin-<device>.prg)"
        )
    try:
        indices = _parse_index_range(args.indices)
    except ValueError as error:
        return _fail(str(error))
    target = Path(args.output) if args.output else LANGUAGE_INDEX_FILE
    if args.write:
        problem = _check_writable(target)
        if problem:
            return _fail(problem)
    device = find_device(device_id)
    settings = load_settings(args.settings)
    workdir = _artifacts_dir(args) / "language-scan"
    driver = create_driver(
        "wine",
        layout=load_layout(),
        artifact_dir=workdir,
        log=lambda message: print(message),
    )
    measured: Dict[str, int] = {}
    rejected: List[int] = []
    print(f"device: {device.id} ({device.product}); probing indices {args.indices}")
    driver.prepare(prg)
    for index in indices:
        try:
            code = driver.probe_language(
                index,
                device,
                settings,
                mode=args.mode,
                timeout_s=args.timeout,
            )
        except Exception as error:  # noqa: BLE001 - a failed probe is a data point
            rejected.append(index)
            print(f"  index {index:>3}: rejected ({type(error).__name__}: {error})")
            continue
        finally:
            driver.stop()
        if code == "unknown":
            rejected.append(index)
            print(f"  index {index:>3}: no E2E:LANGUAGE line")
            continue
        measured.setdefault(code, index)
        print(f"  index {index:>3}: {code}")
    driver.stop()

    document = {
        code: index for code, index in sorted(measured.items(), key=lambda item: item[1])
    }
    print("\nmeasured:")
    for code, index in document.items():
        print(f"  {code}: {index}")
    if rejected:
        print(f"rejected indices (no language reported): {rejected}")
    if args.write:
        merged = dict(load_language_index())
        merged.update(document)
        try:
            written = write_language_index(merged, target)
        except LanguageIndexError as error:
            return _fail(str(error))
        print(f"\nwrote {written} ({len(merged)} codes)")
        if written != LANGUAGE_INDEX_FILE:
            print(
                f"note: the checkout's map is {LANGUAGE_INDEX_FILE}; copy this "
                "file over it from a writable shell."
            )
    elif document:
        print(
            "\nre-run with --write to persist this map in "
            f"{LANGUAGE_INDEX_FILE}; until then the driver treats these codes as "
            "unprovisionable and reports the runs blocked."
        )
    if args.json:
        _emit_json({"measured": document, "rejected": rejected})
    return 0 if document else 1


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
        assets = font_asset_count(fonts_dir())
        print(f"fonts:     {fonts_dir()} ({assets} font files)")
        for line in describe_candidates():
            print(f"  {line}")

    # The font pack is a hard requirement, not a warning: the simulator opens it
    # before every draw and the app crashes with `Invalid Font Specified` when it
    # is absent. It is reported even with --quiet, because entrypoint.sh relies
    # on this call to fail the container with an actionable message.
    try:
        require_fonts()
    except SdkError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIG
    return EXIT_OK


def _cmd_link_fonts(args: argparse.Namespace) -> int:
    """Stage the device font pack where both simulators read it."""
    try:
        links = link_fonts(
            Path(args.mount) if args.mount else None,
            Path(args.target) if args.target else None,
        )
    except SdkError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIG
    if not args.quiet:
        print(f"fonts:     {links.linux} ({font_asset_count(links.linux)} font files)")
        print(f"wine:      {links.wine}")
        print(f"source:    {links.source}")
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
    settings = load_settings(args.settings)
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
                settings=settings,
                profile=args.build_profile,
            )
            print(f"{device.id}: profile {result.profile_note}")
            print(f"{device.id}: command {' '.join(result.command)}")
            print(f"{device.id}: {result.jungle_note}")
            print(f"{device.id}: {result.prg} ({result.duration_ms:.0f} ms)")
            if result.settings_json is not None:
                print(f"{device.id}: {_describe_compiled_settings(result)}")
    return EXIT_OK


def _describe_compiled_settings(result: Any) -> str:
    """One line naming the compiled property defaults and their evidence."""
    values = result.settings_values or {}
    shown = ", ".join(
        f"{name}={values[name]!r}" for name in ("url_0", "sitemap_0") if name in values
    )
    return f"compiled properties: {shown} ({result.settings_json})"


def _settings_key(settings: Any) -> Tuple[Tuple[str, str], ...]:
    """Stable cache key for the property values a build compiles in."""
    return tuple(
        sorted((str(name), str(value)) for name, value in settings.properties.items())
    )


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

    def add_glance_profile(sub: argparse.ArgumentParser) -> None:
        """The build profile, defaulting to the one that lets scenarios run.

        ``monkeyc -x glance`` cannot be used here: ``-x`` is incompatible with
        ``-f``, and the manifest entry class ``OHApp`` carries ``(:glance)``
        too, so excluding the annotation removes the entry point.

        The ``watch-app`` default writes a harness-owned manifest declaring
        ``type="watch-app"`` and stubs the glance view in a generated copy of
        ``source/``. That is what stops the simulator from starting the widget
        in glance mode, where ``getInitialView()`` never runs (#396). The widget
        and glance modes stay explicitly unverified either way.
        """
        group = sub.add_mutually_exclusive_group()
        group.add_argument(
            f"--{PROFILE_WATCH_APP}",
            dest="build_profile",
            action="store_const",
            const=PROFILE_WATCH_APP,
            help=(
                "declare the app as a plain watch app in a generated manifest "
                "and stub the glance view (default); the simulator launches "
                "getInitialView() directly, widget and glance modes are not "
                "covered (#396)"
            ),
        )
        group.add_argument(
            f"--{PROFILE_WITHOUT_GLANCE}",
            dest="build_profile",
            action="store_const",
            const=PROFILE_WITHOUT_GLANCE,
            help=(
                "keep the widget manifest and replace the glance view by an "
                "empty stub; the app may still start in glance mode (#396)"
            ),
        )
        group.add_argument(
            "--with-glance",
            dest="build_profile",
            action="store_const",
            const=PROFILE_WITH_GLANCE,
            help=(
                "compile the app's real glance view instead; the simulator "
                "crashes on its first glance draw until #396 is resolved"
            ),
        )
        sub.set_defaults(build_profile=PROFILE_WATCH_APP)

    status = subparsers.add_parser("status", help="show the health of every component")
    add_common(status)
    status.set_defaults(handler=_cmd_status)

    setup = subparsers.add_parser("setup", help="validate compose, SDK, openHAB and the shim")
    add_common(setup)
    setup.add_argument("--timeout", type=float, default=180.0, help="readiness timeout in seconds")
    setup.add_argument("--profile", choices=fixtures_module.PROFILES, default="stable", help="openHAB profile staged in the stack")
    setup.add_argument("--sitemap", default="garmin_e2e", help="sitemap to resolve as a fixture check")
    setup.set_defaults(handler=_cmd_setup)

    verify_fixtures = subparsers.add_parser(
        "verify-fixtures",
        help="assert openHAB loaded the expected items and sitemaps",
    )
    add_common(verify_fixtures)
    verify_fixtures.add_argument(
        "--profile",
        choices=fixtures_module.PROFILES,
        default="stable",
        help="openHAB profile staged in the stack",
    )
    verify_fixtures.add_argument(
        "--expected",
        help="expected fixture manifest (default: <fixtures>/openhab/expected.yaml)",
    )
    verify_fixtures.add_argument(
        "--log",
        action="append",
        default=[],
        help="read openHAB DSL warnings from this log file (repeatable)",
    )
    verify_fixtures.add_argument(
        "--log-dir",
        help="directory containing <profile>/logs/openhab.log",
    )
    verify_fixtures.add_argument(
        "--strict",
        action="store_true",
        help="also fail when the sets match but openHAB logged a DSL model error",
    )
    verify_fixtures.add_argument(
        "--sizes",
        action="store_true",
        help="fetch each sitemap and report its REST body size; enforces max_sitemap_bytes from the manifest",
    )
    verify_fixtures.add_argument(
        "--dump-dir",
        help="write each fetched sitemap payload to <dump-dir>/<name>.json (requires --sizes)",
    )
    verify_fixtures.add_argument(
        "--timeout", type=float, default=180.0, help="readiness timeout in seconds"
    )
    verify_fixtures.add_argument(
        "--wait",
        type=float,
        default=60.0,
        help="seconds to keep retrying while openHAB finishes loading its models",
    )
    verify_fixtures.set_defaults(handler=_cmd_verify_fixtures)

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
        "--key",
        action="append",
        metavar="BUTTON=KEYSYM",
        help=(
            "send this X keysym for a semantic button instead of the driver's "
            "default path, e.g. --key ENTER=Return; repeatable"
        ),
    )
    run.add_argument(
        "--jungle",
        help=(
            "use this jungle for the build instead of generating a "
            "device-scoped one; requires an SDK that defines every device the "
            "file names. It cannot carry the harness settings override, so a "
            "run with settings needs the generated jungle (drop --jungle) or a "
            "pre-built --prg"
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
    add_glance_profile(run)
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
    contract.add_argument(
        "--device",
        help=(
            "device id; with --prg the device comes from the PRG's name and a "
            "disagreeing --device is refused"
        ),
    )
    contract.add_argument(
        "--prg",
        help=(
            "compiled PRG to launch; required to exercise start/wait_ready/input/"
            "screenshot/events on a driven simulator (build one with `e2e build`)"
        ),
    )
    contract.add_argument("--artifacts")
    contract.add_argument(
        "--shim-url",
        default=os.environ.get("E2E_SHIM_URL"),
        help=(
            "shim base URL; when reachable, each measured press reports the shim "
            "requests it caused (default: E2E_SHIM_URL)"
        ),
    )
    contract.add_argument(
        "--key",
        action="append",
        metavar="BUTTON=KEYSYM",
        help=(
            "send this X keysym for a semantic button instead of the driver's "
            "default path, e.g. --key ENTER=Return; repeatable"
        ),
    )
    contract.add_argument(
        "--mode",
        choices=("ble", "wifi", "offline"),
        default="ble",
        help=(
            "mode the launch exercises (default: ble); scenarios legitimately "
            "differ, so this only chooses what this run starts in"
        ),
    )
    contract.add_argument(
        "--language",
        default="deu",
        help="language the launch exercises (default: deu, to prove the numeric index)",
    )
    contract.add_argument("--json", action="store_true")
    contract.set_defaults(handler=_cmd_contract_test)

    scan = subparsers.add_parser(
        "language-scan",
        help="measure the simulator's numeric Language index per language code",
        description=(
            "The simulator stores its device language as an index (Language=7 is "
            "German), and its order is not documented. Launch the app once per "
            "index, read E2E:LANGUAGE;code=... and record the mapping, which is "
            "what makes a non-English scenario runnable. Run it on the guest."
        ),
    )
    scan.add_argument("--driver", choices=DRIVER_NAMES, default="wine")
    scan.add_argument("--prg", required=True, help="compiled PRG (build one with `e2e build`)")
    scan.add_argument("--device", help="device id; defaults to the PRG's name")
    scan.add_argument(
        "--indices",
        default="0-24",
        help="index ranges to probe, e.g. 0-24 or 0,3,7 (default: 0-24)",
    )
    scan.add_argument("--mode", default="ble", help="communication mode (default: ble)")
    scan.add_argument("--settings", default="default", help="fixture whose properties the PRG was built with")
    scan.add_argument("--timeout", type=float, default=90.0, help="seconds per index")
    scan.add_argument(
        "--write",
        action="store_true",
        help=f"merge the measurement into {LANGUAGE_INDEX_FILE} (or --output)",
    )
    scan.add_argument(
        "--output",
        help=(
            "write the map here instead of the checkout (the runner's /work is "
            "read-only); copy it back afterwards"
        ),
    )
    scan.add_argument("--json", action="store_true")
    scan.add_argument("--artifacts")
    scan.set_defaults(handler=_cmd_language_scan)

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

    fonts = subparsers.add_parser(
        "link-fonts",
        help="stage the device font pack the simulator draws with",
        description=(
            "Link the Connect IQ device font pack into the two locations the "
            "simulators read: $HOME/.Garmin/ConnectIQ/Fonts for the native "
            "build, and the Wine prefix's ConnectIQ/Fonts for simulator.exe. "
            "The fonts are licensed Garmin assets and are never committed; "
            "mount them read-only and stage them from here."
        ),
    )
    fonts.add_argument(
        "--mount",
        help="font pack to stage (default: $CIQ_FONTS_ROOT, then /opt/ciq-fonts)",
    )
    fonts.add_argument(
        "--target",
        help="native destination (default: $HOME/.Garmin/ConnectIQ/Fonts)",
    )
    fonts.add_argument("--quiet", action="store_true")
    fonts.set_defaults(handler=_cmd_link_fonts)

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
        "--settings",
        default="default",
        help=(
            "settings fixture whose property defaults are compiled into the PRG "
            "(default: default); the values are read back from monkeyc's settings "
            "JSON and asserted"
        ),
    )
    build.add_argument(
        "--jungle",
        help=(
            "use this jungle instead of generating a device-scoped one; "
            "requires an SDK that defines every device the file names. It "
            "cannot carry the harness settings override, so omit --settings or "
            "use the generated jungle"
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
    add_glance_profile(build)
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
