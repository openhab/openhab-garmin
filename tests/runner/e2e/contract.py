"""Driver contract tests.

The interface in :mod:`e2e.sim.base` is the contract every driver must honour.
These cases exercise it mechanically, so a driver that only implements the
methods it happened to need fails here instead of producing a run that looks
successful but asserted nothing.

Run with ``python -m e2e contract-test --driver null`` to check the harness
itself (works on any machine), or ``--driver wine --prg <built.prg>`` on the
guest to exercise the real simulator. Without ``--prg`` the device-dependent
cases are reported ``not-implemented`` with that reason.
"""

from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .config import Device, load_devices
from .settings import load_settings
from .sim.base import Button, SimulatorDriver
from .trace import parse_lines

PASS = "pass"
FAIL = "fail"
SKIPPED = "skipped"
NOT_IMPLEMENTED = "not-implemented"

#: How long one press may take to show a new view, in the `press-effect` case.
PRESS_EFFECT_TIMEOUT = 4.0
#: Window pixels that must change for a press to count as having had an effect.
PRESS_PIXEL_THRESHOLD = 2000
#: Seconds between the two idle captures that measure how much the screen
#: changes on its own. A press must beat that baseline, so a ticking clock or an
#: idle redraw cannot turn a dead input path into a pass.
PRESS_BASELINE_INTERVAL = 1.0


class ContractError(RuntimeError):
    """Raised when the contract cannot be exercised for the inputs given."""


#: The build names a PRG ``openhab-garmin-<device>.prg`` (``build.prg_name``),
#: so the device travels with the artifact. A PRG built for another device must
#: never be launched as the contract's default device: on the guest that ran a
#: fenix847mm build as fenix6pro, which produced a ``Symbol Not Found`` runtime
#: error and made ``tap`` fail for a device that has no touch screen.
_PRG_NAME = re.compile(r"^openhab-garmin-(?P<device>[A-Za-z0-9_]+)\.prg$")


@dataclass(frozen=True)
class ContractCase:
    name: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (PASS, SKIPPED)


def missing_methods(driver_class: type) -> List[str]:
    """Names of abstract members left unimplemented by ``driver_class``."""
    return sorted(getattr(driver_class, "__abstractmethods__", frozenset()))


def device_from_prg(prg: Path, devices: Optional[Sequence[Device]] = None) -> Optional[Device]:
    """The device a PRG was built for, from its name, or ``None``.

    ``None`` means the name is not the harness's pattern; a caller must then be
    told which device to use instead of silently picking one.
    """
    match = _PRG_NAME.match(Path(prg).name)
    if match is None:
        return None
    product = match.group("device")
    known = tuple(devices) if devices is not None else tuple(load_devices())
    for device in known:
        if device.product == product:
            return device
    for device in known:
        if device.id == product:
            return device
    return None


def _png_is_valid(path: Path) -> str:
    """Return an empty string when the file is a valid PNG, else the reason."""
    if not path.is_file():
        return "file does not exist"
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "missing PNG signature"
    if len(data) < 33 or data[12:16] != b"IHDR":
        return "missing IHDR chunk"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        return f"invalid dimensions {width}x{height}"
    return ""


def _run_case(name: str, fn: Callable[[], object]) -> ContractCase:
    try:
        result = fn()
    except NotImplementedError as error:
        return ContractCase(name, NOT_IMPLEMENTED, str(error))
    except Exception as error:  # noqa: BLE001 - a contract failure is a result
        return ContractCase(name, FAIL, f"{type(error).__name__}: {error}")
    if isinstance(result, tuple) and len(result) == 2:
        status, detail = result
        return ContractCase(name, str(status), str(detail))
    detail = result if isinstance(result, str) else ""
    return ContractCase(name, PASS, detail)


def run_contract(
    driver: SimulatorDriver,
    workdir: Path,
    *,
    device_id: Optional[str] = None,
    settings_fixture: str = "default",
    prg: Optional[Path] = None,
    ready_timeout_s: float = 300.0,
    requests: Optional[Callable[[], Optional[int]]] = None,
    mode: str = "ble",
    language: str = "deu",
) -> List[ContractCase]:
    """Exercise every part of the driver contract against ``driver``.

    ``prg`` is a real compiled PRG. A driven simulator can only prove anything
    with one: the placeholder written here is not a runnable application. When
    it is omitted, the device-dependent cases are reported ``not-implemented``
    with that reason instead of passing or failing for the wrong cause. The
    device is taken from the PRG's name when it matches the build's pattern, and
    a ``--device`` that disagrees raises :class:`ContractError`. The driver is
    stopped at the end even when a case fails.

    ``mode`` and ``language`` select what the launch exercises. ``language``
    defaults to a non-default code so the numeric index path is proven; ``mode``
    defaults to ``ble`` and only chooses the starting transport, because
    scenarios legitimately differ. A plain-HTTP stack works in either mode once
    the launch waits until the app is interactive (see
    tests/docs/simulator-driver.md).
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    devices = load_devices()
    if prg is not None:
        derived = device_from_prg(Path(prg), devices)
        if derived is None:
            if device_id is None:
                raise ContractError(
                    f"cannot tell which device {Path(prg).name} was built for: it "
                    "is not named `openhab-garmin-<device>.prg`.\n"
                    "Pass --device <id> so the contract launches it as the right "
                    "device; launching a build for another device reports runtime "
                    "errors that belong to no one."
                )
        else:
            if device_id is not None and device_id != derived.id:
                raise ContractError(
                    f"--prg {Path(prg).name} was built for {derived.id!r}, but "
                    f"--device says {device_id!r}. A PRG compiled for one device "
                    "must not be launched as another: it surfaces runtime errors "
                    "from the mismatch and fails input checks for the wrong "
                    "device. Drop --device or point --prg at that device's build."
                )
            device_id = derived.id
    device = next(
        (candidate for candidate in devices if candidate.id == (device_id or devices[0].id)),
        devices[0],
    )
    settings = load_settings(settings_fixture)
    if prg is None:
        staged_prg = workdir / "contract.prg"
        staged_prg.write_bytes(b"contract")
    else:
        staged_prg = Path(prg)
    shot = workdir / "contract-screenshot.png"

    cases: List[ContractCase] = []

    # A driver with a real console or screen can only prove that by launching a
    # real application; the null driver has neither and never launches.
    can_launch = prg is not None or not (driver.supports_events or driver.supports_screenshots)
    no_prg = (
        f"not exercised: no --prg supplied. {driver.name} launches a real "
        "application, and the contract placeholder is not one"
    )

    def interface() -> None:
        if not driver.name:
            raise AssertionError("driver.name is empty")
        abstract = missing_methods(type(driver))
        if abstract:
            raise AssertionError(f"unimplemented abstract methods: {', '.join(abstract)}")
        if getattr(type(driver), "__abstractmethods__", None):
            raise AssertionError("driver class is still abstract")
        signature = inspect.signature(type(driver).start)
        if "mode" not in signature.parameters:
            raise AssertionError("start() lost its 'mode' parameter")
        if "language" not in signature.parameters:
            raise AssertionError("start() lost its 'language' parameter")

    def prepare() -> None:
        driver.prepare(staged_prg)

    cases.append(_run_case("interface", interface))
    cases.append(_run_case("prepare", prepare))

    if not can_launch:
        for name in _launch_cases():
            cases.append(ContractCase(name, NOT_IMPLEMENTED, no_prg))
        cases.append(_run_case("stop", driver.stop))
        return cases

    start = _run_case(
        "start", lambda: driver.start(device, settings, mode, language=language)
    )
    cases.append(start)

    if start.status != PASS:
        blocked = f"blocked by start ({start.status}): {start.detail.splitlines()[0]}"
        for name in _launch_cases():
            if name == "start":
                continue
            cases.append(ContractCase(name, NOT_IMPLEMENTED, blocked))
        cases.append(_run_case("stop", driver.stop))
        return cases

    cases.append(
        _run_case("wait_ready", lambda: driver.wait_ready(ready_timeout_s))
    )

    def app_entry() -> None:
        """`wait_ready` must leave the app on a real view, not the glance.

                The app reaches the sitemap by selecting its glance entry, and that
                needs the entry focused first: page down once, then select. `wait_ready`
                runs exactly that sequence (see `WineSimulatorDriver.launch`), so this
                case fails only when the transition still did not happen.
        """
        parsed = parse_lines(list(driver.events()))
        view = next((event for event in reversed(parsed) if event.event == "VIEW"), None)
        category = view.get("category") if view is not None else None
        if category == "glance":
            raise AssertionError(
                "the app is still showing the glance (`VIEW;category=glance`), so "
                "getInitialView() never ran and no request is made.\n"
                        "The launch sequence is page down (twice in the measured runs), then "
                        "select; select alone does nothing. `wait_ready` sends it and logs "
                        "every attempt with its mechanism, point or keysym, and the view it "
                        "produced, so read logs/driver.log for which attempt ran and what "
                        "the view became. `E2E_WINE_LAUNCH_DOWNS` changes the count if this "
                        "device's entry is a different number of pages away.\n"
                        "If a press reports no effect at all, the input path is the "
                        "problem: `--key DOWN=Down` and `--key ENTER=Return` try other "
                        "bindings, and the pointer line shows whether a click reached the "
                        "simulator window. See tests/docs/simulator-driver.md."
            )
        if category is None:
            raise AssertionError(
                "no VIEW event was observed, so the app's initial view never "
                "rendered; see logs/console.log."
            )

    if driver.supports_events:
        cases.append(_run_case("app-entry", app_entry))

    def input_delivery() -> None:
        """Prove synthetic input reaches the simulator, independently of the app.

        The driver opens one of the simulator's own menus and looks for a large
        window change. This separates "input is not delivered" from "the device
        coordinates are wrong": a coordinate fix cannot help the former.
        """
        probe = getattr(driver, "probe_input_delivery", None)
        if probe is None:
            raise NotImplementedError(
                f"{driver.name} cannot probe input delivery (no simulator menu "
                "accelerator or no pixel comparison)"
            )
        probe()

    if driver.supports_screenshots:
        cases.append(_run_case("input-delivery", input_delivery))

    def press_effect() -> None:
        """A press must change what the app does, not just call xdotool.

        A repeated line for the same view is not an effect: the glance can
        re-render on its own clock, and the app's first draw can land late. Only
        a **different** view (category or name) or, once the app has left the
        glance, a substantial change inside its screen area counts.
        """
        pixel = getattr(driver, "pixel_difference", None)
        region_pixel = getattr(driver, "region_pixel_difference", None)
        screen_region = getattr(driver, "screen_region", None)
        region = None
        if screen_region is not None:
            region = screen_region()
        if not any("E2E:" in line for line in driver.events()):
            raise AssertionError(
                "the app produced no E2E: console output at all, so no press can "
                "have reached it and nothing on screen is attributable to input.\n"
                "This is a launch problem, not a press problem: check "
                "`wait_ready`/`start` and logs/console.log."
            )
        parsed_before = parse_lines(list(driver.events()))
        known = {
            (event.get("category"), event.get("view"))
            for event in parsed_before
            if event.event == "VIEW"
        }
        current = next(
            (event for event in reversed(parsed_before) if event.event == "VIEW"), None
        )
        on_glance = current is not None and current.get("category") == "glance"

        actions: List[tuple] = [
            ("ENTER", lambda: driver.press(Button.ENTER, 1)),
            ("MENU", lambda: driver.press(Button.MENU, 1)),
            ("DOWN", lambda: driver.press(Button.DOWN, 1)),
        ]
        if device.supports_touch:
            actions.append(
                (
                    "tap",
                    lambda: driver.tap(
                        device.screen_width // 2, device.screen_height // 2, 1
                    ),
                )
            )
        shots = workdir / "input"
        shots.mkdir(parents=True, exist_ok=True)
        baseline = 0
        if driver.supports_screenshots and not on_glance and region is not None:
            # Control measurement: how much does the screen change with no input?
            # A ticking clock or an idle redraw otherwise passes a dead press.
            calm_a = shots / "idle-1.png"
            calm_b = shots / "idle-2.png"
            try:
                driver.screenshot(calm_a)
                time.sleep(PRESS_BASELINE_INTERVAL)
                driver.screenshot(calm_b)
                measured = (
                    region_pixel(calm_a, calm_b, region)
                    if region_pixel is not None
                    else (pixel(calm_a, calm_b) if pixel is not None else None)
                )
                baseline = int(measured or 0)
            except Exception:  # noqa: BLE001 - a missing baseline is not a verdict
                baseline = 0
        threshold = max(PRESS_PIXEL_THRESHOLD, baseline)
        attempts: List[str] = []
        for label, action in actions:
            before = None
            if driver.supports_screenshots and not on_glance:
                before = shots / f"{label}-before.png"
                driver.screenshot(before)
            mark = len(driver.events())
            action()
            deadline = time.monotonic() + PRESS_EFFECT_TIMEOUT
            changed = None
            while time.monotonic() < deadline:
                for event in parse_lines(list(driver.events())[mark:]):
                    if event.event == "ERRORVIEW":
                        return f"{label}: ERRORVIEW"
                    if event.event != "VIEW":
                        continue
                    signature = (event.get("category"), event.get("view"))
                    if signature not in known and event.get("category") != "glance":
                        return (
                            f"{label}: new VIEW {signature[0]}/{signature[1]} "
                            f"(idle baseline {baseline}px)"
                        )
                if before is not None and pixel is not None:
                    after = shots / f"{label}-after.png"
                    driver.screenshot(after)
                    if region is not None and region_pixel is not None:
                        changed = region_pixel(before, after, region)
                    else:
                        changed = pixel(before, after)
                    if changed is not None and changed > threshold:
                        return (
                            f"{label}: screen change {changed}px in the screen "
                            f"rectangle (idle baseline {baseline}px)"
                        )
                time.sleep(0.25)
            attempts.append(f"{label} ({changed!r} px)")
        reason = (
            " The app is still on the glance page, so its screen changes are not "
            "counted; fix the glance transition first."
            if on_glance
            else ""
        )
        calibrate = (
            ""
            if baseline == 0
            else (
                f" The screen changes {baseline} px on its own within "
                f"{PRESS_BASELINE_INTERVAL:g}s, so a press had to beat that; "
                "freeze the animated region or assert a view instead."
            )
        )
        raise AssertionError(
            "pressing ENTER, MENU and DOWN"
            + (" and tapping the screen" if device.supports_touch else "")
            + " produced no different VIEW (a repeated view does not count), no "
            f"ERRORVIEW and no screen change above {threshold} px "
            f"(empty-screen baseline {baseline} px): {'; '.join(attempts)}."
            f"{reason}{calibrate}\n"
            "logs/driver.log names the mechanism and window point for each press. "
            "A press that reports no effect at all means the input path is the "
            "problem: compare the logged point with the device definition's "
            "keys[].location, or try `--key <BUTTON>=<keysym>`."
        )

    if driver.supports_events:
        cases.append(_run_case("press-effect", press_effect))

    for button in Button:
        def press_case(button: Button = button) -> object:
            if not device.supports_buttons:
                return (
                    SKIPPED,
                    f"device {device.id} has input {device.input!r} and no physical "
                    "buttons; tap covers its input",
                )
            measured = getattr(driver, "press_measured", None)
            if measured is None:
                driver.press(button, 1)
                return "mechanism not measured by this driver"
            result = measured(
                button, 1, timeout_s=PRESS_EFFECT_TIMEOUT, requests=requests
            )
            if result.effect:
                return result.describe()
            # A single press legitimately does nothing (nav at the top of a
            # list), so this is evidence, not the verdict: press-sequence is.
            return (
                SKIPPED,
                f"{result.describe()} — no effect in isolation; the "
                "press-sequence case is the delivery verdict",
            )

        cases.append(_run_case(f"press[{button.value}]", press_case))

    def press_sequence() -> object:
        """The delivery verdict, judged where delivery is still unproven.

        On the glance, a sequence of navigation presses must move: one `DOWN` at
        the top of a list changes nothing, two do, and a device whose navigation
        is cumulative cannot be judged press by press. Once the app is open, the
        same keys are ordinary navigation — a page-down on a menu with one page
        legitimately changes nothing — so a silent sequence there is reported as
        ``skipped`` with the evidence, not as a failure. `press-effect` is the
        case that judges input on the open app.
        """
        measured = getattr(driver, "press_measured", None)
        if not device.supports_buttons:
            return (
                SKIPPED,
                f"device {device.id} has input {device.input!r} and no physical "
                "buttons; tap covers its input",
            )
        if measured is None:
            for button in (Button.DOWN, Button.DOWN, Button.UP):
                driver.press(button, 1)
            return (
                SKIPPED,
                f"{driver.name} cannot measure a press, so delivery is unproven",
            )
        current = next(
            (event for event in reversed(parse_lines(list(driver.events()))) if event.event == "VIEW"),
            None,
        )
        on_glance = current is None or current.get("category") == "glance"
        seen: List[str] = []
        for button in (Button.DOWN, Button.DOWN, Button.UP):
            result = measured(
                button, 1, timeout_s=PRESS_EFFECT_TIMEOUT, requests=requests
            )
            seen.append(result.describe())
            if result.effect:
                return "sequence delivered: " + " | ".join(seen)
        if not on_glance:
            where = f"{current.get('category')}/{current.get('view')}"
            return (
                SKIPPED,
                f"the app is on {where}, not the glance, so these keys are "
                "ordinary navigation there and a one-page menu legitimately does "
                "not move; `press-effect` judges input on the open app. "
                "Measurement: " + " | ".join(seen),
            )
        raise AssertionError(
            "a sequence of DOWN, DOWN, UP produced no effect on any press while "
            "the app was still on the glance, so input has not been shown to "
            "reach it:\n  "
            + "\n  ".join(seen)
            + "\nThe mechanism each press used is above. A silent mouse and an "
            "ignored keysym look the same; `--key DOWN=<keysym>` tries another "
            "binding, and the pointer line shows whether a click reached the "
            "simulator window. Note that delivery on this guest is "
            "nondeterministic: the launch retries until it lands. See "
            "tests/docs/simulator-driver.md."
        )

    cases.append(_run_case("press-sequence", press_sequence))

    def app_data_reset() -> object:
        """A second launch must not answer from the app's cached sitemap.

        The app persists the last sitemap JSON (`SitemapStore`: `sitemapJson`)
        under `<prefix>/…/GARMIN/APPS/DATA`, and that cache outlives a scenario:
        the next launch starts on the previous sitemap and logs
        `E2E:SITEMAP;result=unchanged` instead of requesting its own. Without
        this case, a run that leaks the cache looks like a navigation or
        protocol failure for every scenario after the first.
        """
        relaunch = getattr(driver, "relaunch_app", None)
        if relaunch is None:
            return (
                SKIPPED,
                f"{driver.name} cannot relaunch the app, so storage isolation is "
                "unproven",
            )
        mark = len(driver.events())
        fresh = relaunch(clear_data=True)
        events = parse_lines(list(driver.events())[mark:])
        results = [event.get("result") for event in events if event.event == "SITEMAP"]
        error_views = [event for event in events if event.event == "ERRORVIEW"]
        working = [
            event
            for event in events
            if event.event == "VIEW"
            and event.get("category") not in (None, "glance", "error")
        ]
        if not fresh:
            raise AssertionError(
                "the relaunched app did not request its sitemap after its storage "
                f"was cleared (SITEMAP results after the relaunch: {results}).\n"
                "Either the storage path was not cleared or the app answered from "
                "another cache; logs/driver.log lists every path that was removed."
            )
        if error_views:
            raise AssertionError(
                "the app showed an error view after its storage was cleared "
                f"(ERRORVIEW id={error_views[-1].get('id')}); a launch that lands "
                "on an error view is not a working app, so this case cannot pass.\n"
                "The reset must clear only the sitemap cache (GARMIN/APPS/DATA and "
                "APPS/TEMP). Removing GARMIN/APPS/SETTINGS or GARMIN/Settings "
                "breaks AppSettings during initialisation "
                "(`Unexpected Type Error` at AppSettings.mc:128). See "
                "logs/CIQ_LOG.YML and logs/driver.log."
            )
        if not working:
            raise AssertionError(
                "the app requested its sitemap but never reached a non-error view "
                f"after the storage was cleared (SITEMAP results: {results}); the "
                "reset may have broken the app's state. See logs/CIQ_LOG.YML."
            )
        return (
            "the second launch requested its sitemap after the sitemap cache was "
            f"cleared (SITEMAP results: {results}) and reached "
            f"{working[-1].get('category')}/{working[-1].get('view')}; no cached "
            "`result=unchanged` and no error view carried over"
        )

    if driver.supports_events:
        cases.append(_run_case("app-data-reset", app_data_reset))

    def tap() -> object:
        driver.tap(1, 1, 1)
        return (
            "mechanism=mouse (window point logged); effect not asserted at an "
            "arbitrary coordinate"
        )

    cases.append(_run_case("tap", tap))

    def screenshot() -> None:
        result = driver.screenshot(shot)
        if Path(result) != shot:
            raise AssertionError(f"screenshot() returned {result}, expected {shot}")
        reason = _png_is_valid(shot)
        if reason:
            raise AssertionError(f"{shot}: {reason}")

    cases.append(_run_case("screenshot", screenshot))

    def events() -> None:
        lines = driver.events()
        if isinstance(lines, (str, bytes)):
            raise AssertionError("events() must return a sequence of lines, not a string")
        for line in lines:
            if not isinstance(line, str):
                raise AssertionError(f"event {line!r} is not a string")
        if not lines:
            if driver.supports_events:
                raise AssertionError(
                    "events() is empty after a real launch; monkeydo streams the app "
                    "console to stdout, so this means the capture did not start"
                )
            return
        if driver.supports_events and not any(
            line.strip().startswith("E2E:") for line in lines
        ):
            raise AssertionError(
                "no E2E: trace line in the captured console; the app must be the "
                "debug build (monkeyc -f ... see tests/docs/sdk.md)"
            )

    cases.append(_run_case("events", events))
    cases.append(_run_case("stop", driver.stop))

    return cases


def _launch_cases() -> List[str]:
    """Names of the cases that need a launched application, in order."""
    names = [
        "start",
        "wait_ready",
        "app-entry",
        "input-delivery",
        "press-effect",
        "press-sequence",
        "app-data-reset",
    ]
    names.extend(f"press[{button.value}]" for button in Button)
    names.extend(["tap", "screenshot", "events"])
    return names


def format_report(cases: Sequence[ContractCase]) -> str:
    """Render contract results as a fixed-width table."""
    width = max((len(case.name) for case in cases), default=0)
    lines = []
    for case in cases:
        detail = f"  {case.detail}" if case.detail else ""
        lines.append(f"  {case.name.ljust(width)}  {case.status}{detail}")
    return "\n".join(lines)


def summary(cases: Sequence[ContractCase]) -> Dict[str, int]:
    counts: Dict[str, int] = {PASS: 0, FAIL: 0, SKIPPED: 0, NOT_IMPLEMENTED: 0}
    for case in cases:
        counts[case.status] = counts.get(case.status, 0) + 1
    return counts


def exit_code(cases: Sequence[ContractCase]) -> int:
    """0 when nothing failed; 1 for a failure; 2 when the driver is unfinished."""
    counts = summary(cases)
    if counts.get(FAIL):
        return 1
    if counts.get(NOT_IMPLEMENTED):
        return 2
    return 0
