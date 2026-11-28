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
from .assertions import AssertionFailure, DEFAULT_STABLE_DIFF_RATIO
from .config import ConfigError, Device
from .openhab import OpenHabClient, OpenHabError
from .reporting import ERRORED, FAILED, PASSED, SKIPPED, ScenarioResult, StepResult
from .scenarios import Scenario, Step
from .settings import AppSettings, load_settings, scenario_settings
from .shim import ShimClient, ShimError, ShimRequest
from .sim import Button, DriverError, SimulatorDriver, create_driver
from .sdk import SdkError, SdkLayout, require_fonts
from .trace import EventMatch, TraceLog, parse_lines

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
            profile=scenario.openhab.profile,
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
                settings=scenario_settings(scenario, shim_url=self.shim.base_url),
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
        except fixtures_module.ScenarioSkipped as error:
            result.status = SKIPPED
            result.skip_reason = str(error)
            self.log(f"SKIPPED {scenario.scenario_id}: {result.skip_reason}")
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
        settings = scenario_settings(scenario, shim_url=self.shim.base_url)

        if self.dry_run:
            self.log(
                f"[dry-run] {scenario.scenario_id}: skipping openHAB readiness, item seeding "
                "and shim script installation"
            )
            return settings

        self.openhab.wait_ready()
        self.shim.wait_ready()

        # Profiles are exclusive: one openHAB serves one fixture tree. A
        # scenario written for `dev` cannot run on a `stable` stack, and failing
        # it in the guard would read as a product failure. Detect what is
        # running and skip with the reason instead; the run summary reports
        # which profiles it covered, so "complete" means one pass per profile.
        if not self.dry_run:
            running, distances = fixtures_module.detect_profile(
                self.openhab,
                wait_s=fixtures_module.DEFAULT_WAIT_S,
                interval_s=fixtures_module.DEFAULT_INTERVAL_S,
            )
            wanted = scenario.openhab.profile
            if running != wanted:
                detail = ", ".join(
                    f"{name}: {missed[0]} item(s)/{missed[1]} sitemap(s) missing"
                    for name, missed in sorted(distances.items())
                )
                raise fixtures_module.ScenarioSkipped(
                    f"needs the {wanted!r} openHAB profile, but the running stack "
                    f"serves {running!r} ({detail}). Profiles "
                    "are exclusive, so run this scenario against the "
                    f"{wanted!r} stack: set COMPOSE_PROFILES={wanted} and start "
                    f"openhab-{wanted}, then `python -m e2e run {scenario.scenario_id}`. "
                    f"If openhab-{wanted} already existed, restart it once the "
                    "other stack releases the port: a container that failed to "
                    "bind stays `Up (healthy)` without serving anything. "
                    "A complete pass is one run per profile; see "
                    "tests/docs/scenarios.md."
                )

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

        if status == FAILED:
            # The screen at the point of failure is the evidence a reader needs
            # when the app is on a different view than the step expected; the
            # step's own artifacts are empty because it never got to capture
            # anything. Best effort: a driver that cannot capture must not turn
            # one failure into two.
            shot = self._failure_screenshot(step, context)
            if shot is not None:
                artifacts = list(artifacts) + [str(shot)]
                message += f"\n  screenshot at failure: {shot}"

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

    def _failure_screenshot(self, step: Step, context: _Context) -> Optional[Path]:
        """Capture the screen when a step failed, for human review.

        Never raises and never replaces the failure: a driver without
        screenshots, a closed window or a dry run simply has no artifact.
        """
        if context.dry_run or not context.driver.supports_screenshots:
            return None
        path = context.workdir / f"failure-step-{step.index}.png"
        try:
            written = context.driver.screenshot(path)
        except Exception as error:  # noqa: BLE001 - diagnostics must not mask the failure
            self.log(f"note: no screenshot at failure: {error}")
            return None
        return Path(written)

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

    def _do_install_faults(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        rules = list(step.get("rules") or ())
        names = [str(rule.get("name", "?")) for rule in rules]
        if context.dry_run:
            return SKIPPED, "would install fault(s): " + ", ".join(names), []
        # Replaces any previously installed script, like the pre-launch install.
        installed = context.shim.install_script({"version": 1, "rules": rules})
        self.log(f"installed {installed} shim fault rule(s) mid-run: {', '.join(names)}")
        return PASSED, f"installed {installed} fault rule(s): {', '.join(names)}", []

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
                path_contains=step.get("path_contains"),
                path_prefix=step.get("path_prefix"),
                matched_rule=step.get("matched_rule"),
                faulted=step.get("faulted"),
            )
            if len(matched) >= minimum:
                return PASSED, f"{len(matched)} matching request(s)", []
            if time.monotonic() >= deadline:
                raise AssertionFailure(
                    f"expected at least {minimum} request(s) matching "
                    f"{self._describe_match(step)} within {timeout_s:.1f}s; "
                    f"recorded:\n{assertions.format_requests(requests)}\n"
                    + self._describe_app_state(context)
                )
            time.sleep(0.25)

    def _describe_app_state(self, context: "_Context") -> str:
        """The app's own state, for a wait that timed out with no requests.

        "No requests recorded" is compatible with a healthy app that is merely
        slow and with an app that crashed before its first poll; without this,
        the second one sends the reader to the fixtures and the network. The
        event counts, the last protocol event (``SITEMAP``/``COMMAND``/``ERROR``)
        and the last view separate them: an app that is polling has ``SITEMAP``
        events even when it sends no command.
        """
        try:
            events = parse_lines(list(context.driver.events()))
        except Exception:  # noqa: BLE001 - diagnostics must not mask the failure
            return "  app state: unavailable"
        if not events:
            return "  app state: the app printed nothing at all (see logs/console.log)"
        last_view = next(
            (event for event in reversed(events) if event.event == "VIEW"), None
        )
        errors = [event for event in events if event.event == "ERRORVIEW"]
        counts: Dict[str, int] = {}
        for event in events:
            counts[event.event] = counts.get(event.event, 0) + 1
        lines = [
            "  app state: last view="
            + (
                f"{last_view.get('category')}/{last_view.get('view')}"
                if last_view is not None
                else "none"
            ),
            "  app state: events: "
            + ", ".join(f"{name} x{count}" for name, count in counts.items()),
        ]
        for name in ("SITEMAP", "COMMAND", "ERROR", "INPUT", "SELECT"):
            matching = [event for event in events if event.event == name]
            if matching:
                lines.append(f"  app state: last {name} " + matching[-1].describe())
        if errors:
            lines.append(
                "  app state: ERRORVIEW id="
                + str(errors[-1].get("id"))
                + " (the app showed an error view, not a working page)"
            )
        runtime_errors = []
        getter = getattr(context.driver, "runtime_errors", None)
        if callable(getter):
            try:
                runtime_errors = list(getter())
            except Exception:  # noqa: BLE001
                runtime_errors = []
        for error in runtime_errors[:3]:
            label = getattr(error, "label", None) or getattr(error, "kind", "error")
            lines.append(f"  app state: simulator log reports {label}")
        tail = [event for event in events[-6:]]
        lines.append(
            "  console tail:\n"
            + "\n".join(f"    {event.raw}" for event in tail)
        )
        return "\n".join(lines)

    def _do_press(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        button = Button(step.get("button"))
        count = step.get("count", 1)
        expect = step.get("expect")
        if expect is None:
            context.driver.press(button, count)
            return PASSED, f"{button.value} x{count}", []
        if context.dry_run:
            return SKIPPED, f"would require {expect!r} from {button.value} x{count}", []
        measured = getattr(context.driver, "press_measured", None)
        if measured is None:
            # An unmeasured expectation is not proof, so this fails instead of
            # skipping: the scenario states what the press must do.
            raise AssertionFailure(
                f"press {button.value}: the scenario requires expect={expect!r}, but "
                f"the {context.driver.name} driver cannot measure a press. Run the "
                "scenario on the wine driver, or drop the expectation."
            )
        timeout_s = step.get("timeout_ms", 4000) / 1000.0
        # A press that must not do anything is not re-issued: a repeat could only
        # add evidence the scenario does not want.
        result = measured(
            button,
            count,
            timeout_s=timeout_s,
            require="input" if expect == "input" else "effect",
            retries=0 if expect == "none" else 1,
        )
        state = self._describe_app_state(context)
        if expect == "input" and not result.strong:
            raise AssertionFailure(
                f"press {button.value} x{count}: the app reported no input derived "
                f"from the press: {result.describe()}\n"
                "  A changed screen is not delivery: the app's own painting changes "
                "it too, which is how an ENTER sent while the homepage was still "
                "being built looked like a successful press. Only an "
                "INPUT/SELECT/COMMAND event proves the app handled it.\n"
                + state
            )
        if expect == "effect" and not result.effect:
            raise AssertionFailure(
                f"press {button.value} x{count} had no observable effect: "
                f"{result.describe()}\n"
                "  Nothing on screen changed from a settled baseline and the app "
                "reported no event, so the press cannot be shown to have reached "
                "the app.\n" + state
            )
        if expect == "none" and result.effect:
            raise AssertionFailure(
                f"press {button.value} x{count} had an effect, but the scenario "
                f"requires none: {result.describe()}"
            )
        return PASSED, result.describe(), []

    def _do_tap(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        count = step.get("count", 1)
        context.driver.tap(step.get("x"), step.get("y"), count)
        return PASSED, f"({step.get('x')},{step.get('y')}) x{count}", []

    def _do_screenshot(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        name = step.get("name")
        path = context.workdir / f"{step.index:02d}-{name}.png"
        if step.get("stable") and not context.dry_run and context.driver.supports_screenshots:
            written = self._stable_screenshot(step, context, path)
            context.screenshots[name] = Path(written)
            return PASSED, f"{name} -> {written} (stable frame)", [str(written)]
        written = context.driver.screenshot(path)
        context.screenshots[name] = Path(written)
        return PASSED, f"{name} -> {written}", [str(written)]

    def _stable_screenshot(self, step: Step, context: _Context, path: Path) -> Path:
        """Capture until two consecutive frames agree.

        ``E2E:VIEW`` is emitted when a view is *constructed*, not when it has
        been painted: a capture taken the instant ``HomepageMenu`` is reported
        still holds the previous screen, which is how the glance became a
        baseline. Waiting for two identical frames proves the paint finished,
        and is deterministic where a fixed sleep is a guess.
        """
        interval = step.get("interval_ms", 1000) / 1000.0
        timeout_s = min(step.get("timeout_ms", 30_000) / 1000.0, context.remaining())
        threshold = step.get("stable_diff_ratio", DEFAULT_STABLE_DIFF_RATIO)
        deadline = time.monotonic() + timeout_s
        # Each capture goes to its own file: writing both to `path` would compare
        # the second capture with itself and call any screen stable.
        previous = context.driver.screenshot(path)
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            probe = path.with_name(f"{path.stem}-probe{attempts}{path.suffix}")
            time.sleep(interval)
            current = context.driver.screenshot(probe)
            differing = assertions.diff_pixels(previous, current)
            if differing is not None:
                try:
                    width, height = assertions.image_size(current)
                except AssertionFailure:
                    width = height = 0
                if width and differing <= threshold * width * height:
                    path.write_bytes(Path(current).read_bytes())
                    try:
                        Path(probe).unlink()
                    except OSError:
                        pass
                    self.log(
                        f"screenshot {path.name}: stable after {attempts} attempt(s) "
                        f"({differing} differing pixels)"
                    )
                    return path
            previous = current
        raise AssertionFailure(
            f"the screen never settled within {timeout_s:.0f}s, so the capture "
            "would be of a repainting screen; the last comparison still differed"
        )

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

    def _failure_head(self, step: Step, summary: str) -> str:
        """Put the step's own description first, where a reader looks."""
        description = step.get("description")
        if not description:
            return summary
        return f"{description}\n  {summary}"

    def _activation_hint(self, context: "_Context") -> str:
        """What the app did with the presses, when a wait timed out.

        ``SELECT;kind=...`` is emitted only when a menu item handles a select,
        and it never says which row is focused. So "the expected view never
        opened" has three causes that look identical in the event stream: a
        press that never arrived, a press that moved the highlight onto another
        row, or a select that pushed something else. The last ``INPUT`` and
        ``SELECT`` lines separate them, and naming the row the step expected is
        the scenario author's job (the step description is printed above).
        """
        if context.trace is None:
            return ""
        try:
            context.trace.poll()
        except Exception:  # noqa: BLE001 - diagnostics must not mask the failure
            return ""
        lines: List[str] = []
        view = context.trace.current_view_event()
        lines.append(
            "  app view now: "
            + (view.describe() if view is not None else "no VIEW event yet")
        )
        inputs = context.trace.of_event("INPUT")
        selects = context.trace.of_event("SELECT")
        if inputs:
            lines.append("  last input: " + inputs[-1].describe())
        else:
            lines.append(
                "  last input: none; the press never reached the app's input delegate"
            )
        if selects:
            last = selects[-1]
            lines.append("  last selection: " + last.describe())
            lines.append(
                f"  activation: the app handled the last select as kind="
                f"{last.get('kind')} (result={last.get('result')}, "
                f"depth={last.get('depth')}). The focused row itself is not in the "
                "trace, so a wrong press count and a press that never landed look "
                "the same here; see tests/docs/scenarios.md."
            )
        else:
            lines.append(
                "  activation: no SELECT event at all, so no menu row handled a "
                "press"
            )
        return "\n" + "\n".join(lines)

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
                self._failure_head(
                    step,
                    f"no trace event matching {match.describe()} within {timeout_s:.1f}s",
                )
                + "\n"
                + context.trace.failure_report(match)
                + self._activation_hint(context)
            )
        return PASSED, f"{found.describe()} at {found.received_at:.3f}", []

    def _require_view(self, context: "_Context", expect: Mapping[str, str], name: str) -> None:
        """Fail closed when the app is not showing the view the golden claims.

        A golden is only worth committing if it depicts the state the scenario
        asserts. Without this check a capture of the glance — reproducible, and
        therefore passing on every later run — was once committed as the sitemap
        homepage: a reproducible wrong baseline is worse than a flaky one.
        """
        if context.trace is None or not context.driver.supports_events:
            raise AssertionFailure(
                f"golden {name}: the driver has no console event stream, so the "
                f"view cannot be verified against expect={dict(expect)}. A golden "
                "must be verified, not assumed."
            )
        context.trace.poll()
        event = context.trace.current_view_event()
        if event is None:
            raise AssertionFailure(
                f"golden {name}: no E2E:VIEW event has been seen, so the capture "
                f"cannot be verified against expect={dict(expect)}. Wait for the "
                "view with a wait_for_view step before the screenshot."
            )
        mismatches = [
            f"{field}={event.get(field)!r} (expected {value!r})"
            for field, value in expect.items()
            if event.get(field) != value
        ]
        if mismatches:
            raise AssertionFailure(
                f"golden {name}: the app is showing {event.describe()}, which does "
                f"not match expect ({'; '.join(mismatches)}). A capture of the "
                "glance, a loading view or any other state must never become the "
                "baseline; add a wait_for_view step before the screenshot."
            )
        return None

    def _do_wait_for_view(self, step: Step, context: _Context) -> Tuple[str, str, List[str]]:
        """Wait until the app reports the view the scenario needs on screen."""
        if context.skip_events:
            return SKIPPED, "--skip-events", []
        if not context.driver.supports_events or context.trace is None:
            return SKIPPED, f"{context.driver.name} driver has no console event stream", []
        fields = {
            field: step.params[field]
            for field in ("category", "view", "depth")
            if field in step.params
        }
        match = EventMatch(event="VIEW", fields=fields)
        timeout_s = min(step.get("timeout_ms", 30_000) / 1000.0, context.remaining())
        context.trace.poll()
        found = context.trace.wait_for(match, timeout_s)
        if found is None:
            raise AssertionFailure(
                self._failure_head(
                    step,
                    "the app did not show a view matching "
                    f"{match.describe()} within {timeout_s:.1f}s",
                )
                + "\n"
                + context.trace.failure_report(match)
                + self._activation_hint(context)
            )
        return PASSED, found.describe(), []

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
                self._require_view(context, golden["expect"], name)
                problem = assertions.screen_content_problem(
                    actual, golden["min_ink_components"]
                )
                if problem is not None:
                    raise AssertionFailure(f"golden {name}: {problem}")
                artifacts.append(str(actual))
                if context.update_goldens:
                    # Defence in depth for the write path: --update-goldens is
                    # already refused without a font pack, and a capture written
                    # from a crashed render must never become the baseline.
                    if not context.dry_run:
                        try:
                            require_fonts()
                        except SdkError as error:
                            raise AssertionFailure(str(error)) from error
                    assertions.update_golden(actual, golden_path)
                    notes.append(f"golden {name}: updated {golden_path}")
                else:
                    assertions.assert_golden(actual, golden_path, golden["max_diff_ratio"])
                    notes.append(f"golden {name}: matches {golden_path}")
                evaluated = True

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
                        "error view does not fail this check.\n"
                        + context.trace.failure_report()
                    )
                if not no_error_view and not showing:
                    raise AssertionFailure(
                        "expected the current view to be the error view, but the app "
                        f"is showing category={category or 'unknown'}\n"
                        + context.trace.failure_report()
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
                        "ERRORVIEW event was observed\n"
                        + context.trace.failure_report()
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
