"""Scenario execution engine.

Wires the pieces together: prepare openHAB and the shim, instantiate a
simulator driver, run the steps, and collect a :class:`ScenarioResult`.

Failure policy:

- an assertion that does not hold fails the scenario immediately, so the
  artifacts show the state at the point of failure instead of a cascade;
- an infrastructure error (openHAB unreachable, shim unavailable, driver not
  implemented) is reported as a failed step with the underlying message, never
  as a pass;
- steps that cannot be evaluated in the current mode (dry run without events or
  screenshots) are reported as **skipped** and counted in the report. They are
  never silently treated as successful.

The engine never talks to openHAB through the shim for setup: seeding items
directly keeps the shim trace a record of what the *app* did, which is what the
assertions are about.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from . import assertions
from . import fixtures as fixtures_module
from .assertions import AssertionFailure
from .config import ConfigError, Device
from .openhab import OpenHabClient, OpenHabError
from .reporting import ERRORED, FAILED, PASSED, SKIPPED, ScenarioResult, StepResult
from .scenarios import Scenario, Step
from .settings import AppSettings, load_settings
from .shim import ShimClient, ShimError, ShimRequest
from .sim import Button, DriverError, SimulatorDriver, create_driver
from .sdk import SdkLayout
from .trace import EventMatch, TraceLog

Log = Callable[[str], None]


@dataclass
class _Context:
    """Mutable state shared by the steps of one scenario."""

    scenario: Scenario
    device: Device
    driver: SimulatorDriver
    openhab: OpenHabClient
    shim: ShimClient
    settings: AppSettings
    workdir: Path
    goldens_dir: Path
    dry_run: bool
    update_goldens: bool
    skip_events: bool
    deadline: float
    log: Log
    screenshots: Dict[str, Path] = field(default_factory=dict)
    requests: List[ShimRequest] = field(default_factory=list)
    trace: Optional[TraceLog] = None

    def remaining(self) -> float:
        return max(0.1, self.deadline - time.monotonic())


def _handler_name(action: str) -> str:
    return "_do_" + action


class ScenarioRunner:
    """Execute one scenario."""

    def __init__(
        self,
        scenario: Scenario,
        device: Device,
        *,
        openhab_url: str,
        shim_url: str,
        artifacts_dir: Path,
        goldens_dir: Optional[Path] = None,
        driver_name: str = "null",
        dry_run: bool = False,
        update_goldens: bool = False,
        skip_events: bool = False,
        sdk_layout: Optional[SdkLayout] = None,
        prg: Optional[Path] = None,
        log: Optional[Log] = None,
    ) -> None:
        self.scenario = scenario
        self.device = device
        self.openhab = OpenHabClient(openhab_url)
        self.shim = ShimClient(shim_url)
        self.artifacts_dir = Path(artifacts_dir)
        self.goldens_dir = Path(goldens_dir) if goldens_dir else self.artifacts_dir.parent / "goldens"
        self.driver_name = "null" if dry_run else driver_name
        self.dry_run = dry_run
        self.update_goldens = update_goldens
        self.skip_events = skip_events
        self.sdk_layout = sdk_layout
        self.prg = prg
        self.log = log or (lambda message: None)

    # -- public -----------------------------------------------------------
    def run(self) -> ScenarioResult:
        scenario = self.scenario
        started = time.monotonic()
        workdir = self.artifacts_dir / scenario.suite / scenario.name
        workdir.mkdir(parents=True, exist_ok=True)

        result = ScenarioResult(
            scenario_id=scenario.scenario_id,
            name=scenario.name,
            suite=scenario.suite,
            tier=scenario.tier,
            device=self.device.id,
            mode=scenario.mode,
            fixture=scenario.fixture,
            language=scenario.language,
            source=str(scenario.source),
            status=ERRORED,
            dry_run=self.dry_run,
            artifacts_dir=str(workdir),
        )

        driver: Optional[SimulatorDriver] = None
        context: Optional[_Context] = None
        try:
            driver = create_driver(
                self.driver_name,
                layout=self.sdk_layout,
                artifact_dir=workdir,
                log=self.log,
            )
            if not driver.can_provision_language(scenario.language):
                result.status = SKIPPED
                result.skip_reason = (
                    f"language {scenario.language!r} cannot be provisioned by "
                    f"driver {driver.name!r}: it has no measured simulator index, "
                    "so this run would execute in another language and prove "
                    "nothing. Measure the index once with "
                    "`python -m e2e language-scan --prg <built.prg> --write`; "
                    "see tests/docs/simulator-driver.md."
                )
                self.log(f"SKIPPED {scenario.scenario_id}: {result.skip_reason}")
                return result
            context = _Context(
                scenario=scenario,
                device=self.device,
                driver=driver,
                openhab=self.openhab,
                shim=self.shim,
                settings=load_settings(scenario.fixture),
                workdir=workdir,
                goldens_dir=self.goldens_dir,
                dry_run=self.dry_run,
                update_goldens=self.update_goldens,
                skip_events=self.skip_events,
                deadline=started + scenario.timeout_s,
                log=self.log,
            )
            context.trace = TraceLog(driver.events)

            self._preflight()
            context.settings = self._prepare_stack()
            driver.prepare(self.prg or workdir / "app.prg")

            failed = False
            for step in scenario.steps:
                if time.monotonic() > context.deadline:
                    raise AssertionFailure(
                        f"scenario budget of {scenario.timeout_s}s exhausted before step "
                        f"{step.index} ({step.action})"
                    )
                step_result = self._execute(step, context)
                result.steps.append(step_result)
                if step_result.status == FAILED:
                    failed = True
                    result.failures.append(step_result.message)
                    break

            if not failed:
                result.status = PASSED
            else:
                result.status = FAILED
        except AssertionFailure as error:
            result.status = FAILED
            result.failures.append(str(error))
        except (DriverError, OpenHabError, ShimError, ConfigError) as error:
            result.status = FAILED
            result.failures.append(f"{type(error).__name__}: {error}")
        except Exception as error:  # noqa: BLE001 - always produce a report
            result.status = ERRORED
            result.failures.append(f"unexpected {type(error).__name__}: {error}")
        finally:
            if driver is not None:
                try:
                    driver.stop()
                except Exception:  # noqa: BLE001 - never mask the real failure
                    pass
                # The simulator's own log is the only place an app runtime error
                # (Monkey C stack, missing symbol, bad font) appears; stop()
                # collects and classifies it, and the report carries the result.
                try:
                    runtime_errors = getattr(driver, "runtime_errors", None)
                    if callable(runtime_errors):
                        result.runtime_errors = [
                            dict(item.to_document()) for item in runtime_errors()
                        ]
                except Exception:  # noqa: BLE001 - diagnostics must not fail a run
                    result.runtime_errors = []
            if context is not None and not self.dry_run:
                try:
                    context.requests = self.shim.requests()
                except ShimError:
                    context.requests = []
            if context is not None:
                result.requests = len(context.requests)
            result.duration_ms = (time.monotonic() - started) * 1000.0

        return result

    # -- preparation ------------------------------------------------------
    def _preflight(self) -> None:
        scenario = self.scenario
        for step in scenario.steps:
            if step.action == "press" and not self.device.supports_buttons:
                raise ConfigError(
                    f"{scenario.scenario_id}: step {step.index} presses a button but device "
                    f"{self.device.id} has input {self.device.input!r}"
                )
            if step.action == "tap" and not self.device.supports_touch:
                raise ConfigError(
                    f"{scenario.scenario_id}: step {step.index} taps the screen but device "
                    f"{self.device.id} has input {self.device.input!r}"
                )

    def _prepare_stack(self) -> AppSettings:
        scenario = self.scenario
        settings = load_settings(scenario.fixture)
        if scenario.settings:
            settings = settings.with_overrides(scenario.settings)

        if self.dry_run:
            self.log(
                f"[dry-run] {scenario.scenario_id}: skipping openHAB readiness, item seeding "
                "and shim script installation"
            )
            return settings

        self.openhab.wait_ready()
        self.shim.wait_ready()

        # Refuse to run against an instance that dropped the fixture models.
        # HTTP 200 on /rest/ proves nothing; the sets are what the scenario
        # asserts against (issue #394).
        fixture_report = fixtures_module.verify(self.openhab, scenario.openhab.profile)
        self.log(f"fixtures: {fixture_report.summary()}")

        if scenario.openhab.items:
            seeded = self.openhab.seed(scenario.openhab.items)
            self.log(f"seeded {len(seeded)} item state(s)")

        self.shim.reset()
        if scenario.shim_rules:
            installed = self.shim.install_script(scenario.shim_script)
            self.log(f"installed {installed} shim fault rule(s)")

        return settings

    # -- step execution ---------------------------------------------------
    def _execute(self, step: Step, context: _Context) -> StepResult:
        started = time.monotonic()
        handler = getattr(self, _handler_name(step.action), None)
        if handler is None:  # pragma: no cover - guarded by the loader
            raise ConfigError(f"no handler for action {step.action!r}")

        try:
            status, message, artifacts = handler(step, context)
        except AssertionFailure as error:
            status, message, artifacts = FAILED, str(error), []
        except (DriverError, OpenHabError, ShimError) as error:
            status, message, artifacts = FAILED, f"{type(error).__name__}: {error}", []

        duration_ms = (time.monotonic() - started) * 1000.0
        prefix = f"step {step.index} ({step.action})"
        if status == FAILED:
            self.log(f"FAILED {prefix}: {message.splitlines()[0]}")
        else:
            self.log(f"{status.upper()} {prefix}{': ' + message if message else ''}")
        return StepResult(
            index=step.index,
            action=step.action,
            status=status,
            duration_ms=duration_ms,
            message=message,
            artifacts=list(artifacts),
        )

    # -- actions ----------------------------------------------------------
    def _do_launch(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        context.driver.start(
            context.device,
            context.settings,
            mode=self.scenario.mode,
            language=self.scenario.language,
        )
        if step.get("wait_ready", True):
            # A cold Wine prefix plus the first app start is not fast: the
            # simulator window comes up quickly, but the app needs a settle
            # (~50 s) before it accepts input, and the guest measured input
            # delivery as nondeterministic, so the driver retries for up to
            # ~3 minutes. 300 s matches the driver's own default and is still
            # bounded by the scenario budget.
            context.driver.wait_ready(min(300.0, context.remaining()))
        return PASSED, f"device={context.device.id} mode={self.scenario.mode}", []

    def _do_stop(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        context.driver.stop()
        return PASSED, "", []

    def _do_wait(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        seconds = min(step.get("ms") / 1000.0, context.remaining())
        time.sleep(seconds)
        return PASSED, f"{seconds:.2f}s", []

    def _do_reset_shim(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        if context.dry_run:
            return SKIPPED, "shim trace not available in a dry run", []
        context.shim.reset_requests()
        return PASSED, "request trace cleared", []

    def _do_wait_for_request(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        if context.dry_run:
            return SKIPPED, "shim trace not available in a dry run", []
        timeout_s = min(step.get("timeout_ms") / 1000.0, context.remaining())
        minimum = step.get("min_count", 1)
        deadline = time.monotonic() + timeout_s
        while True:
            requests = context.shim.requests()
            matched = assertions.filter_requests(
                requests,
                method=step.get("method"),
                path=step.get("path"),
                path_prefix=step.get("path_prefix"),
            )
            if len(matched) >= minimum:
                return PASSED, f"{len(matched)} matching request(s)", []
            if time.monotonic() >= deadline:
                raise AssertionFailure(
                    f"expected at least {minimum} request(s) matching "
                    f"{self._describe_match(step)} within {timeout_s:.1f}s; "
                    f"recorded:\n{assertions.format_requests(requests)}"
                )
            time.sleep(0.25)

    def _do_press(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        button = Button(step.get("button"))
        context.driver.press(button, step.get("count", 1))
        return PASSED, f"{button.value} x{step.get('count', 1)}", []

    def _do_tap(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        count = step.get("count", 1)
        context.driver.tap(step.get("x"), step.get("y"), count)
        return PASSED, f"({step.get('x')},{step.get('y')}) x{count}", []

    def _do_screenshot(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        name = step.get("name")
        path = context.workdir / f"{step.index:02d}-{name}.png"
        written = context.driver.screenshot(path)
        context.screenshots[name] = Path(written)
        return PASSED, f"{name} -> {written}", [str(written)]

    def _do_command(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        item = step.get("item")
        value = step.get("value")
        if context.dry_run:
            return SKIPPED, f"would send {value} to {item}", []
        if step.get("via") == "shim":
            self._post_through_shim(item, value)
            return PASSED, f"{value} -> {item} (via shim)", []
        context.openhab.send_command(item, value)
        return PASSED, f"{value} -> {item} (direct)", []

    def _do_wait_for_event(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        if context.skip_events:
            return SKIPPED, "--skip-events", []
        if not context.driver.supports_events:
            return SKIPPED, f"{context.driver.name} driver has no console event stream", []
        if context.trace is None:
            raise AssertionFailure("internal error: trace log was not initialised")

        match = EventMatch(
            event=step.get("event"),
            fields=step.get("fields") or {},
            contains=step.get("contains"),
        )
        timeout_s = min(step.get("timeout_ms") / 1000.0, context.remaining())
        context.trace.poll()
        found = context.trace.wait_for(match, timeout_s)
        if found is None:
            raise AssertionFailure(
                f"no trace event matching {match.describe()} within {timeout_s:.1f}s; "
                f"seen: {context.trace.describe()}"
            )
        return PASSED, f"{found.describe()} at {found.received_at:.3f}", []

    def _do_assert(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        notes: List[str] = []
        artifacts: List[str] = []
        evaluated = False

        spec = step.get("shim_requests")
        if spec is not None:
            if context.dry_run:
                notes.append("shim requests: skipped (dry run)")
            else:
                count = assertions.assert_requests(
                    context.requests or context.shim.requests(),
                    spec,
                    description=step.description,
                )
                notes.append(f"shim requests: {count} matched")
                evaluated = True

        golden = step.get("golden")
        if golden is not None:
            name = golden["name"]
            actual = context.screenshots.get(name)
            if actual is None:
                raise AssertionFailure(
                    f"golden assertion for {name!r} but no screenshot with that name was "
                    "captured earlier in the scenario"
                )
            if not context.driver.supports_screenshots:
                notes.append(f"golden {name}: skipped (driver has no real screenshots)")
            else:
                golden_path = context.goldens_dir / context.device.id / f"{name}.png"
                if context.update_goldens:
                    assertions.update_golden(actual, golden_path)
                    notes.append(f"golden {name}: updated {golden_path}")
                else:
                    assertions.assert_golden(actual, golden_path, golden["max_diff_ratio"])
                    notes.append(f"golden {name}: matches {golden_path}")
                evaluated = True
                artifacts.append(str(actual))

        no_error_view = step.get("no_error_view")
        if no_error_view is not None:
            if not context.driver.supports_events or context.trace is None:
                notes.append("no_error_view: skipped (driver has no console event stream)")
            else:
                context.trace.poll()
                category = context.trace.current_view_category()
                showing = context.trace.is_showing_error_view()
                if no_error_view and showing:
                    raise AssertionFailure(
                        "expected the current view not to be the error view, but the "
                        f"app is showing category={category or 'unknown'}; a dismissed "
                        f"error view does not fail this check. seen: {context.trace.describe()}"
                    )
                if not no_error_view and not showing:
                    raise AssertionFailure(
                        "expected the current view to be the error view, but the app "
                        f"is showing category={category or 'unknown'}; "
                        f"seen: {context.trace.describe()}"
                    )
                notes.append(f"no_error_view: ok (current view: {category or 'unknown'})")
                evaluated = True

        # The historical reading: an error view that appeared at any point,
        # even if it was dismissed later. Separate from `no_error_view`, which
        # only describes the current view.
        if step.get("never_error_view"):
            if not context.driver.supports_events or context.trace is None:
                notes.append("never_error_view: skipped (driver has no console event stream)")
            else:
                context.trace.poll()
                if context.trace.has_error_view():
                    raise AssertionFailure(
                        "expected no error view during the whole run, but an "
                        f"ERRORVIEW event was observed; seen: {context.trace.describe()}"
                    )
                notes.append("never_error_view: ok")
                evaluated = True

        status = PASSED if evaluated else SKIPPED
        return status, "; ".join(notes), artifacts

    # -- helpers ----------------------------------------------------------
    def _post_through_shim(self, item: str, value: str) -> None:
        url = self.shim.base_url + "/rest/items/" + urllib.parse.quote(item, safe="")
        request = urllib.request.Request(  # noqa: S310 - compose-internal URL
            url, data=str(value).encode("utf-8"), method="POST"
        )
        request.add_header("Content-Type", "text/plain")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                if response.status >= 400:
                    raise ShimError(f"POST {url} -> {response.status}")
        except urllib.error.HTTPError as error:
            raise ShimError(f"POST {url} -> {error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ShimError(f"POST {url} failed: {error}") from None

    @staticmethod
    def _describe_match(step: Step) -> str:
        parts = []
        for key in ("method", "path", "path_prefix"):
            value = step.get(key)
            if value:
                parts.append(f"{key}={value}")
        return " ".join(parts) or "<any request>"


def run_scenario(
    scenario: Scenario,
    device: Device,
    *,
    openhab_url: str,
    shim_url: str,
    artifacts_dir: Path,
    goldens_dir: Optional[Path] = None,
    driver_name: str = "null",
    dry_run: bool = False,
    update_goldens: bool = False,
    skip_events: bool = False,
    sdk_layout: Optional[SdkLayout] = None,
    prg: Optional[Path] = None,
    log: Optional[Log] = None,
) -> ScenarioResult:
    """Convenience wrapper around :class:`ScenarioRunner`."""
    return ScenarioRunner(
        scenario,
        device,
        openhab_url=openhab_url,
        shim_url=shim_url,
        artifacts_dir=artifacts_dir,
        goldens_dir=goldens_dir,
        driver_name=driver_name,
        dry_run=dry_run,
        update_goldens=update_goldens,
        skip_events=skip_events,
        sdk_layout=sdk_layout,
        prg=prg,
        log=log,
    ).run()
