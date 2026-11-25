# Simulator driver

## Contract

`e2e.sim.base.SimulatorDriver` is the interface every driver implements:

| Method | Meaning |
| --- | --- |
| `prepare(prg)` | make the compiled PRG available to the simulator |
| `start(device, settings, mode, language)` | start the simulator into `mode` (`ble`/`wifi`/`offline`) and system `language` (`eng`/`deu`/...), then launch the app |
| `wait_ready(timeout_s)` | block until the app rendered its first view |
| `press(button, count)` | press a semantic device button |
| `tap(x, y, count)` | tap a device screen coordinate |
| `screenshot(path)` | capture the simulator window as PNG |
| `stop()` | terminate and release the device filesystem |
| `events()` | app console lines, oldest first |

`WineSimulatorDriver` additionally exposes `timed_events()`, which returns
`(monotonic_timestamp, line)` pairs for the same buffer.

Class attributes state what a driver can actually do: `supports_events` and
`supports_screenshots`. A driver that cannot provide them must not pretend: the
engine reports event and golden assertions as **skipped**, never passed.

### Running the contract test

`python -m e2e contract-test --driver <name>` exercises every method and reports
`pass` / `fail` / `not-implemented` per case.

- `--driver null` proves the harness itself works; it runs anywhere.
- `--driver wine --prg <built.prg>` exercises the real simulator on the guest.
  A driven simulator can only prove anything with a real application: without
  `--prg`, the cases that need a launch (`start`, `wait_ready`, input,
  `screenshot`, `events`) are reported `not-implemented` with that reason, and
  the command exits `2`. The placeholder PRG the contract writes is not a
  runnable app.
- The device comes from the PRG's name (`openhab-garmin-<device>.prg`, the
  build's pattern). A `--device` that names another device is **refused**, and
  an unrecognised PRG name needs an explicit `--device`: launching one device's
  build as another produced a `Symbol Not Found` and a pointless `tap` failure
  on the guest, which is a false result rather than a test.
- `app-entry` requires the app to have left the glance: `wait_ready` runs it, and
  a widget still in glance mode fails with the delegate/memory pointers instead
  of a generic timeout.
- `press-effect` proves the input path: it presses ENTER, MENU and DOWN (and
  taps on touch devices) and requires a new view **or** a change inside the
  screen rectangle. A pass means the app reacted, not that `xdotool` ran; the
  simulator's bezel highlight cannot satisfy it.
- `input-delivery` presses the declared select once and reports whether the app
  changed a view or its screen area. It does not assume a simulator menu.
- The contract starts with `mode=ble, language=deu`. The language is reported
  and compared, never forced (see "Language cannot be provisioned").

## How the Wine driver works

The simulator is a wxWidgets Windows application with no automation API (the
investigation is recorded in [#140](https://github.com/openhab/openhab-garmin/issues/140)).
The driver runs it under Wine on the X display the runner starts and drives it
from the outside:

1. **prepare** — check that Wine, `monkeydo`, `xdotool`, `xdpyinfo` and
   ImageMagick's `import` exist, then copy the PRG to
   `<artifacts>/launch/app.prg`.
1. **start** — verify the display answers; sweep a stray simulator; rewrite
   `simulator.ini` for the device, communication state and language; start
   `wine simulator.exe` and wait for its window and transport; launch
   `monkeydo <prg> <device>` and capture its stdout. The app's property defaults
   are already in the PRG, so nothing is seeded unless the escape hatch is set.
1. **wait_ready** — wait for `E2E:LANGUAGE` plus the first `E2E:VIEW` (or
   `E2E:ERRORVIEW`), compare the reported language with the requested one, and
   record what the window title says about the device.
1. **press / tap** — `xdotool` against the simulator window after focusing it.
1. **screenshot** — ImageMagick `import -window <id>`, never the root window.
1. **stop** — terminate the app and the simulator by PID; the X display and the
   Wine prefix stay alive so the next `start` is quick.

### Start ordering: the generic title is expected

Measured on the guest: the simulator window is titled plain `CIQ Simulator`
until `monkeydo` pushes the app, even with a correct `simulator.ini` and every
device definition present. With the app pushed, the title becomes
`CIQ Simulator - fēnix® 8 47mm / … (6.0.3)`.

So the order is fixed and must not be "improved" back:

```text
display ready -> simulator.ini -> simulator window ("CIQ Simulator")
              -> monkeydo launch -> first E2E: line is the readiness gate
```

Waiting for the device name _before_ launching deadlocks for the full startup
timeout and blames the device definitions, which is a false lead. The driver
therefore:

- treats **any** window named `CIQ Simulator` as "the simulator started";
- gates readiness on the first `E2E:` line, because that proves the app is
  running rather than that a window exists;
- records the device name from the title as **corroboration**: a warning when
  the title never names a device, or names a different one, never a failure.

A launch that produces no `E2E:` line at all is the failure case, and that is
where the `link-devices` hint is accurate: the app really did not reach the
simulator.

### Cold starts and the stray-simulator hazard

Measured on the guest: the simulator window takes **36 s** on a cold Wine
prefix (2-3 s warm), and the first `E2E:` line can take up to ~55 s after
`monkeydo`. The window wait is therefore 120 s and readiness 90 s; do not shrink
either to a "reasonable-looking" few seconds.

The simulator is single-instance. A second `simulator.exe` started while one is
alive exits **silently**, and `monkeydo` then waits forever with empty output —
which looks exactly like a launch failure and burns the whole readiness budget.
`prepare()` and `start()` therefore sweep first: `wineserver -k` for this prefix
(prefix-scoped and name-based, unlike `pkill -f`) plus a bounded wait for the
old windows to disappear. When a stale simulator was found, the log says so:

```text
a simulator was already running (window(s) 8388611); stopped it with wineserver -k ...
```

Artifacts land under the run's artifacts directory:

| Path | Contents |
| --- | --- |
| `logs/driver.log` | every driver decision, including the PIDs and the ini/properties files it wrote |
| `logs/simulator.log` | Wine's stdout/stderr for the simulator |
| `logs/console.log` | `monkeydo`'s stdout/stderr, i.e. the app console with the `E2E:` lines |
| `logs/CIQ_LOG.YML` | the simulator's own runtime log, copied on `stop()`; the only place a crash or a missing symbol appears |
| `logs/ciq-log-errors.txt` | the classified runtime errors from that log, one per class |

`stop()` never uses `pkill -f <pattern>`: inside `docker exec` such a pattern
matches the exec's own command line and kills the exec. The driver keeps both
PIDs instead.

### Environment overrides

| Variable | Effect |
| --- | --- |
| `E2E_WINE_BINARY` | Wine binary, when it is not on `PATH` |
| `DISPLAY` | X display (default `:99`) |
| `E2E_WINE_STARTUP_TIMEOUT` | seconds to wait for the simulator window (default `120`); the app's first trace line is bounded by the caller (90 s by default) |
| `E2E_WINE_SETTINGS_FILE` | escape hatch: write this application-properties file before launch; the default path writes nothing because properties come from the build |
| `E2E_WINE_SCREEN_RECT` | `x,y,width,height` of the watch screen inside the window; pin it when taps miss |
| `E2E_WINE_KEY_<BUTTON>` | X keysym for one button, e.g. `E2E_WINE_KEY_MENU=ctrl+m` |

## Per-method status

| Method | Status | Notes |
| --- | --- | --- |
| `prepare` | implemented | Validates the external tools and copies the PRG. Runs before anything touches Wine. |
| `start` | implemented | Display check, `simulator.ini`, simulator-window wait, property seeding and `monkeydo` launch. The device name is expected to appear only after the launch. |
| `wait_ready` | implemented | First view **and** the reported language, both from the trace stream. A dead app fails with the console tail. The first view may be the glance (`category=glance`) on a plain `monkeydo` launch; reaching the sitemap is the app's behaviour and the scenario asserts it, not the driver. |
| `press` | implemented, verified by effect | Clicks the declared `keys[].location` hit area (hold where `isHold`) as real X input; keysym only as an override/fallback. The contract proves delivery (`input-delivery`) and effect (`press-effect`). |
| `tap` | implemented | The window is focused first. The screen rectangle comes from the device definition's `display.location` (`simulator.json`), with `E2E_WINE_SCREEN_RECT` as the override; a centred fit is only the last resort for an SDK without `simulator.json`. |
| `screenshot` | implemented | Captures the simulator window. Fails when the capture is not a usable PNG instead of returning a blank image. |
| `events` | implemented | `monkeydo` stdout/stderr with receipt timestamps; `stdbuf` disables its block buffering when available. |
| `stop` | implemented | Terminates the app and the simulator by PID; leaks nothing into the next scenario. |
| mode (`ble`/`wifi`/`offline`) | partial | The startup keys are written and reported. The app always starts in `BLE` and only leaves it after a request fails, so the driver does not claim a runtime mode; the scenario's request-count assertions cover the transition. |
| language (`eng`/`deu`/...) | implemented, not provisionable | `Language` is written before start but the simulator does not apply it. The reported language is compared and a mismatch is recorded as a warning; the engine skips non-`eng` runs and `coverage.md` marks them blocked. |
| app properties | implemented, verified at build | Compiled into the PRG through a `settings.xml` resource override; `e2e build` asserts the defaults `monkeyc` emitted, and `wait_ready` rejects a `UI:CONFIG` error view. See below. |

## Application properties

An application property is a key and value **built into the app at compile
time** from its resource defaults, and properties follow the rules of resource
overrides. The simulator's settings editor is a convenience for interactive
work; it is not the delivery mechanism, and no settings file is needed.

`e2e build` therefore compiles the requested configuration in:

1. A harness-owned copy of `resources/base/settings.xml` is written to
   `<artifacts>/build/resource-override-<device>/settings.xml`, with the
   fixture's values as the `<property>` defaults (`${E2E_SHIM_URL}` and the
   other tokens resolve exactly as they do for the fixtures).
1. The generated jungle for the device gets that directory appended to
   `<device>.resourcePath`, immediately after `$(resBase)`, so the override
   wins over `resources/base` and the launcher-icon directories are untouched.
1. After compiling, the harness reads the settings JSON `monkeyc` emits next to
   the PRG and **asserts** that `url_0` and `sitemap_0` equal the requested
   values. A mismatch, or a missing JSON, fails the build. This runs with no
   simulator, so a silent override regression cannot reach the guest.

`--jungle` cannot carry the override (a supplied jungle is used verbatim and
the harness must not edit it), so a run that needs settings uses the generated
jungle. `--prg` remains the escape hatch for a PRG built elsewhere.

The driver's runtime backstop is unchanged: `wait_ready` raises when the app
reports `ERRORVIEW;id=UI:CONFIG` although the fixture configured a URL, so a PRG
built without the override fails the scenario instead of passing vacuously. A
scenario that deliberately empties `url_0` (for example
`extended/localisation-error-config-url`) is unaffected, because its fixture
URL is empty.

`E2E_WINE_SETTINGS_FILE` is only an escape hatch for a hand-made properties
file; the default path writes nothing.

### What the simulator does with settings (recorded, do not re-investigate)

Measured on the guest with `strace`: at app start `simulator.exe` scans
`C:\users\<user>\Temp\com.garmin.connectiq\GARMIN\APPS\SETTINGS\` and
`...\GARMIN\Settings\`, then rewrites `OPENHAB-GARMIN-<DEVICE>.SET` (254 bytes)
with a property **key table only** — no values. Dropping an
`<app>-settings.json` into that directory is consumed and deleted at launch and
no value reaches the `.SET`. The `.SET` binary format is undocumented, so the
driver neither writes nor parses it.

## Mode and language

`simulator.ini` is rewritten before the simulator starts, in the simulator's own
format:

- the path is
  `<prefix>/drive_c/users/<user>/AppData/Roaming/Garmin/ConnectIQ/simulator.ini`
  — the `ConnectIQ` root, **not** a `Simulator/` subdirectory;
- the file is flat `key=value` lines with **no section header**;
- every existing key is preserved, because the simulator rewrites this file
  itself (`LastFplVersion=9` must survive) and only the keys below are replaced
  or appended.

| Requested | Keys written |
| --- | --- |
| `ble` | `BleState=2`, `WifiState=1` |
| `wifi` | `BleState=1`, `WifiState=2` |
| `offline` | `BleState=1`, `WifiState=0` |

The states are numbers, not booleans. The operator's Windows simulator writes
`BleState=2` for a connected phone; the other values follow
`System.ConnectionState` (0 = not initialised, 1 = not connected,
2 = connected). `offline` uses `0` for Wi-Fi on purpose: the app's
`hasWifiCapability()` is false then, so it goes straight to `OFFLINE` instead of
waiting for a Wi-Fi check that has nothing to talk to. `nightMode=1` and
`UseHttpsRequirements=0` in that same file confirm the numeric convention; the
driver does not set them.

Language is written as `Language=<code>` before start and the app's actual
language is read from `E2E:LANGUAGE` (which comes from
`System.getDeviceSettings().systemLanguage`). The configuration that was written
is never used as evidence.

### Language cannot be provisioned

Measured on the guest, twice:

```text
simulator.ini: Language=deu                            -> app: E2E:LANGUAGE;code=eng
LANG=de_DE.UTF-8 LANGUAGE=de LC_ALL=de_DE.UTF-8 + deu  -> app: E2E:LANGUAGE;code=eng
```

The simulator's own `CIQ_LOG.YML` says `Language-Code: eng` in both cases.
`GARMIN/Settings/` stays empty, there is no `HKCU\Software\Garmin*` key, and
`deu` is a language the binary knows (the value survives in `simulator.ini`), so
this is not a spelling problem: **device language is a device setting the
simulator applies through its own UI**, and those menus are not scripted.

The consequence is honest reporting, not pretending:

- `wait_ready` requires that a language is reported and compares it with the
  request. A mismatch is recorded as a **warning** in `logs/driver.log`
  (including the requested and reported codes); it does not fail the run,
  because no driver-side action can change it.
- The engine skips a run whose language is not `eng` when the driver says it
  cannot provision a language (`provisions_language = False` on the Wine
  driver), with `skip_reason` in the report and a `skipped` entry in JUnit. The
  dry run still exercises those scenarios' wiring with the null driver.
- `tests/docs/coverage.md` lists every non-English variant as **blocked**
  instead of counting it as evidence, and the summary counts them.

The future path is one of: script the simulator's Simulation menu (device
settings), or find a persistence file the simulator actually reads. The SDK
history notes device-settings change notifications for language; nothing else on
the guest accepted a language yet.

A mode change at runtime (the simulator's `Simulation` and `Connection` menus)
is likewise **not implemented**. The driver would have to script native Win32
menus with `Alt+S`/`Alt+C` plus arrow keys and confirm the effect from
`E2E:CONNECTION`; the spike under [`spike/`](../spike/README.md) exercises that
path, and its verdict decides whether it is reliable enough for the driver.
Until then a scenario that needs a mode gets it at startup, and the request-count
assertions prove the app's behaviour.

Night mode, sleep mode and HTTPS requirements are also only available through
those menus and are not scripted.

## Simulator runtime log (`CIQ_LOG.YML`)

The `E2E:` stream only carries what the app prints. A crash, a missing symbol or
a bad font appears only in the simulator's own log,
`Temp/com.garmin.connectiq/GARMIN/APPS/LOGS/CIQ_LOG.YML`. `stop()` copies it to
`<artifacts>/logs/CIQ_LOG.YML`, writes the classified entries to
`logs/ciq-log-errors.txt`, records them in `logs/driver.log`, and exposes them
through `runtime_errors()`; the engine puts them in `report.json`, the JUnit
`system-err` and the markdown notes.

| Class | Marker | What it means |
| --- | --- | --- |
| `symbol-not-found` | `Symbol Not Found` | **Build/device mismatch**: the PRG was compiled for another device, or its annotations do not match this device. Rebuild and launch that device's build; this is not an app defect. |
| `invalid-font` | `Invalid Font Specified` | **Device definition**: this device's definition does not provide the font. Not an app defect until a second device shows it too. |
| `out-of-memory` | `Out Of Memory` | App or size limit: compare the device's memory limit with the sitemap size. |

The log's header (`Language-Code`, `Part-Number`, `Firmware-Version`,
`ConnectIQ-Version`, `Appname`) is recorded too: it corroborates the environment
a run actually had, which a screenshot cannot. Only one error per class is
reported, so a thousand repeats do not drown the report.

Two cases seen on the guest, recorded so neither is filed as an app bug:

- `Symbol Not Found ... Could not find symbol 'createBufferedBitmap'` at
  `BufferedBitmapFactory.mc:35`: that line is inside the `(:exclForCiqPre400)`
  overload, which belongs in a `fenix847mm` build and is excluded for
  `fenix6pro`. It appeared only because a `fenix847mm` PRG was launched as
  `fenix6pro` — the mismatch the contract test now refuses.
- `Invalid Font Specified` at `GlanceSitemapView.mc:99` (`_textArea.draw`). It
  is not a device-definition gap: it reproduces with `TextArea` and with a
  plain `dc.drawText`, with `[FONT_SMALL, FONT_TINY, FONT_GLANCE]`,
  `[FONT_GLANCE]` and `[FONT_SMALL]`, on `fenix6pro` (CIQ 3.4.5), `fenix7`
  (5.2.0) and `fenix847mm` (6.0.x), with both the generated jungle and the full
  `monkey.jungle`. See the next section and
  [#396](https://github.com/openhab/openhab-garmin/issues/396).

### Glance: the profile that keeps runs alive

The crash happens before the app can poll, so the glance is **excluded from
E2E builds by default**. The driver always launches a PRG that draws no glance
text:

- `run` and `build` use the `without-glance` profile unless `--with-glance` is
  passed. The generated jungle's `sourcePath` is rebased onto
  `<artifacts>/build/source-no-glance/source`, a copy of the checkout's
  `source/` in which `source/main/user-interface/features/glance/GlanceSitemapView.mc`
  is replaced by an empty stub. Nothing in the checkout changes, and
  `OHApp.getGlanceView()` still returns a valid (but blank) view, so the app
  proceeds to its normal initial view.
- The profile and the full `monkeyc` command are printed for every build, so a
  run is explainable from its log. There is no `-x` in that command:
  `monkeyc -x` is rejected together with `-f` (`The -f command line option is
  incompatible with the options -x, -m, -z`), and the jungle's
  `excludeAnnotations = glance` removes `OHApp` — the manifest entry class —
  and shared classes such as `SitemapStore`, `E2ETrace` and `AppLifecycle`,
  which are annotated `(:glance)` too. The build then fails with
  `Cannot find entry point class '$.OHApp'`.
- A green run therefore says **nothing** about the glance. The glance-related
  scope entries are marked **blocked** in
  [coverage.md](coverage.md#summary) with a link to #396, not waived.
- Re-enable it when the hardware question is answered:

  ```bash
  python -m e2e run smoke/launch-and-poll --with-glance
  python -m e2e build --device fenix847mm --with-glance
  ```

  If it still crashes there, the evidence belongs in #396; do not "fix" the
  glance to make the suite green.

## Input

`press` and `tap` first focus the simulator window:

```bash
xdotool windowactivate --sync <window>   # needs a window manager
xdotool windowfocus --sync <window>      # works on bare Xvfb
```

Xvfb runs without a window manager, so `windowactivate` normally fails. That is
recorded as a note and is not fatal, because `windowfocus` uses `XSetInputFocus`
and is sufficient for synthetic key and pointer events. If `windowfocus` fails,
the driver raises: without focus the events would land on another window, and a
"passing" run would have pressed keys into the void.

Button definitions are **hit areas, not keyboard keys**. `simulator.json`
declares each physical button as a rectangle with a behaviour, for example
`fenix7`:

```json
"keys": [
  {"behavior": "onSelect",     "id": "enter", "location": {"x": 356, "y": 145, "width": 44, "height": 69}},
  {"behavior": "previousPage", "id": "up",    "location": {"x": 1,   "y": 244, "width": 44, "height": 69}},
  {"behavior": "onMenu",       "id": "menu",  "isHold": true, "location": {"x": 1, "y": 244, "width": 44, "height": 69}},
  {"behavior": "nextPage",     "id": "down",  "location": {"x": 21,  "y": 336, "width": 44, "height": 69}},
  {"behavior": "onBack",       "id": "esc",   "location": {"x": 356, "y": 336, "width": 44, "height": 69}}
]
```

`press` maps the semantic button (`onSelect` → `ENTER`, `previousPage` → `UP`,
`nextPage` → `DOWN`, `onBack` → `BACK`, `onMenu` → `MENU`) and **clicks the
declared rectangle**, with `isHold` producing a press-and-hold (mouse down,
`HOLD_MS`, mouse up).

The declared rectangles are already in the window's **client space**, so there
is no window-size scaling. Measured on the guest: the client area is
`409x600`, and `fenix7` declares `display.location = {79,149,266,266}` — the
260 px screen drawn at 266 px by the simulator itself, inside a 409-wide client
area. Multiplying those numbers by `window/declared` was wrong and moved a
click off the bezel; `press` and `tap` now share one `CoordinateMapper`, so a
tap and a click of the same declared point land on the same pixel. A test
asserts exactly that agreement.

`E2E_WINE_SCREEN_RECT` is the only transform: it pins the screen rectangle and
the same scale/offset re-anchors the key hit areas, for a window that really is
showing a different rectangle. `logs/driver.log` records the mechanism for every
press: the clicked window point and the mapper source, or the keysym.

### Proving that input is delivered

A click that lands correctly and a click the simulator never receives look the
same in a log, so the driver separates the two from the simulator's chrome:

- The pointer moves to **absolute root coordinates** (`window origin + point`)
  and the click is generated with XTEST; keys go to the **focused** window
  instead of `xdotool key --window`, because `XSendEvent` is ignored by many
  toolkits. Both keep the window-targeted form as a fallback if the first
  command fails.
- `probe_input_delivery()` presses the declared `onSelect` area once and
  requires a new `E2E:VIEW`/`ERRORVIEW` **or** a change inside the
  `display.location` rectangle. The contract case `input-delivery` fails with
  "delivery to the app or the app not reacting" when neither happens — it does
  **not** try a simulator menu: the guest's simulator has no menu bar, and no
  `Alt+F`/`Alt+S`/`Alt+M` opens a window there.
- `press-effect` accepts a new view or a change in the **screen rectangle** at or
  above the pixel threshold. The simulator highlights its own bezel buttons, so
  a whole-window difference would pass while the app never reacted; the screen
  region is therefore the only area measured. It presses ENTER, MENU and DOWN,
  and taps the screen centre on a touch device.

Both cases are red on the guest exactly while the app sits on the glance page:
the simulator receives the clicks, but the app does not react (see "Opening the
app from the glance page"). That is the accurate state, not a coordinate bug.

Keystrokes are only a fallback:

- `E2E_WINE_KEY_<BUTTON>=<keysym>` always wins and sends a key instead;
- an SDK whose `keys` is a mapping of action → Windows virtual-key code (the
  older shape) also produces keysyms;
- the documented defaults (`START=ctrl+s`, `UP=Up`, `DOWN=Down`,
  `ENTER=Return`, `BACK=Escape`, `MENU=ctrl+m`) are the last resort.

A keystroke the simulator ignores is indistinguishable from a successful press
unless the effect is observed, so the contract test does not stop at "the call
succeeded": `press-effect` presses ENTER, then MENU, then DOWN and requires a
new `E2E:VIEW` or `E2E:ERRORVIEW` within a few seconds. Without one, input never
reached the app and the case fails, naming both mechanisms.

### Opening the app from the glance page

The app is a **widget**, and a widget that the simulator shows as part of the
glance page is started **in glance mode**, with a hard 32 KB memory budget; the
SDK documents that selecting it launches the widget in its standard mode. So the
simulator is behaving correctly when only
`E2E:VIEW;category=glance` appears: `getInitialView()` has not run and no
request is made.

`wait_ready` therefore checks the first view and, when it is the glance, presses
the declared `onSelect` hit area, then the `onMenu` hold, then `onSelect` again;
on a touch-only device it taps the screen centre twice instead. If none of that
changes the view, it **relaunches the PRG once** (the `monkeydo` console log is
appended, not replaced). Everything is bounded by the readiness budget, and every
attempt plus its mapped window point is in `logs/driver.log`.

None of those transitions the app on the guest. The candidates, in the order
worth checking:

1. **The glance delegate.** `OHApp.getGlanceView()` returns
   `[ new GlanceSitemapView() ]` with no `GlanceViewDelegate`, although the SDK
   allows `[ GlanceView, GlanceViewDelegate ]`. Whether the system needs a
   delegate to make the glance selectable is an **app** question; the harness
   must not change app source.
1. **Glance memory.** Glance mode has a 32 KB budget on most devices. The font
   error seen only on the glance (`Invalid Font Specified`) and the missing
   transition may share that cause; #396 records the measurement.
1. **The simulator's own widget entry.** No simulator menu exists on the guest
   (`xwininfo -root -children` lists only the main window plus 1x1 helper
   windows, and no `Alt+…` accelerator opens anything), so there is no menu
   action to drive.

The contract keeps this honest: `app-entry` fails while the app is still on the
glance, `input-delivery` fails because the declared select press changes neither
a view nor the screen, and `press-effect` fails for the same reason. A scenario
must not paper over it with a request assertion. Replacing the glance with a
cleared background changed nothing either, so "the glance cannot be selected
because it draws nothing" is not the cause.

`tap(x, y)` takes **device screen pixels**. The driver maps them into the
window through the watch-screen rectangle declared by the device definition:
`simulator.json`'s `display.location` (`x`, `y`, `width`, `height`) is the
authoritative screen position, already in window client space. For example
`fenix847mm` declares `{x: 134, y: 221, width: 454, height: 454}`. The same
mapper and the same optional pin are used as for the key hit areas, so a tap and
a press cannot disagree about where the window is.

`E2E_WINE_SCREEN_RECT=x,y,width,height` overrides the declared rectangle when a
build's window differs. A centred, aspect-preserving fit is only the last
resort, for an SDK whose device definitions ship no `simulator.json`.

Capturing the root window is never an option: on a display without a window
manager it is a single colour and would silently satisfy a golden assertion.

## Failure modes

Every failure names the stage, the timeout, and the next concrete step, so a log
is enough to act on it.

| Message | Meaning | Next step |
| --- | --- | --- |
| `no X display is answering on ':99'` | the display never came up, or `DISPLAY` names the wrong one | run `xdpyinfo -display :99` in the container; the entrypoint starts Xvfb before staging the SDK |
| `a simulator was already running (window(s) …); stopped it` | a stray simulator was swept before start; the run continues | none, unless it repeats: check that nothing else starts a simulator |
| `the simulator opened an error dialog` | the Wine prefix has no device definitions, or the simulator cannot start | `python -m e2e link-devices`, then check the prefix's `AppData/Roaming/Garmin/ConnectIQ/Devices` |
| `simulator-window … no window titled 'CIQ Simulator' appeared` | the simulator process never produced a window | read `logs/simulator.log`; the device definitions are only one cause at this point |
| `device title … does not match` / `does not name a device` | **warning only**; the trace stream already proved the app is running | check `LastUsedDevice` in `simulator.ini` if results look wrong |
| `monkeydo exited with code …` | wrong PRG or device name | read `logs/console.log` |
| `app-ready … no console line` | the app started but produced no `E2E:` line | check the PRG is a debug build and the device wiring (`link-devices` is accurate here) |
| `warning: the app is still on the glance page` | a widget in glance mode; `getInitialView()` did not run, so no request is made | an app-side transition question: the glance delegate and the 32 KB glance budget, see below |
| `app-entry … still showing the glance` | a widget started in glance mode; selecting it should launch it, but nothing transitions it | check the glance delegate (`getGlanceView()` returns no `GlanceViewDelegate`) and the 32 KB glance budget; app-side, not harness-side |
| `no simulator menu opened: … synthetic input did not reach the simulator` | legacy text from the removed menu probe | replaced: `input-delivery` now presses select and measures the screen |
| `press-effect … no new VIEW or ERRORVIEW and no screen change` | the press reached nothing or the app did not react | the log names the mechanism and point; if `input-delivery` and `app-entry` also failed, the app is on the glance, not a coordinate bug |
| `the app reported system language …` | `Language` in `simulator.ini` was ignored | see the language section above |
| `the app showed the configuration error view` | the PRG was built without the requested properties | rebuild with `e2e build --settings <fixture>`, or pass a PRG built that way |

## The Windows `shell.exe` bridge does not work

Tried on the guest: running the SDK's Windows `shell.exe` under Wine as
`monkeydo -s`, on the theory that it could replace the Linux SDK for launching
the app. It connects (`Found Transport: tcp / Connecting...`) but produces no
app output and the device screen stays black.

The Linux `shell` binary from the official SDK remains the supported path. This
is a recorded conclusion, not an open question, so it does not have to be
re-investigated.

## Stopping the simulator

`pkill -f <pattern>` inside a `docker exec` also matches the exec's own command
line, so `docker exec runner pkill -f simulator.exe` kills the `exec` itself
before it can report anything. This cost two commands on the guest. A driver
that has to stop the simulator must use a PID it obtained explicitly - the
window PID from `xdotool getwindowpid`, or a recorded child PID - or
`pkill -x wine`, which matches the process name only.

`WineSimulatorDriver.stop()` follows that rule: it keeps the `Popen` handles of
`simulator.exe` and `monkeydo`, terminates those PIDs (TERM, then KILL after a
timeout) and never calls `pkill`. It leaves `wineserver` and the X display
running so the next `start` does not pay for prefix initialisation again.

## What is not verified in this change

The driver was written and reviewed without Wine, a display or the SDK. The
proof is a driver run on the guest, which the operator performs:

1. Build the debug PRG with the contract's fixture:
   `python -m e2e build --device fenix6pro --settings default` (it prints the
   PRG path and the compiled properties). `--settings` is what compiles the
   app's property defaults in, and the build fails if `monkeyc`'s settings JSON
   does not echo them.
1. Run `python -m e2e contract-test --driver wine --prg <built.prg>` inside the
   runner container and confirm the report is all `pass` with exit `0`. The
   device is taken from the PRG name; pass `--device fenix847mm` with that
   build so `tap` is exercised on a touch device for real.
1. Run `python -m e2e run smoke/launch-and-poll --driver wine` (or `--prg
   <built.prg>`; the run command builds per scenario, with that scenario's
   fixture) and confirm the assertions are evaluated, not skipped.
1. Open `logs/driver.log` and `logs/CIQ_LOG.YML`: the first should show the
   `simulator.ini` keys, the transport, `app properties come from the build`,
   the device-title corroboration and the language warning; the second is the
   evidence when anything crashed. `logs/ciq-log-errors.txt` lists the
   classified errors.
1. Run one language scenario (`extended/localisation-sitemap`) and one mode
   scenario (`extended/wifi-mode`) to confirm the verification paths.
1. With the app open, check a press reaches it: the contract's `input-delivery`
   case proves synthetic input changes the simulator at all, and `press-effect`
   proves a press changes the app. `logs/driver.log` shows the clicked window
   point. If either fails, treat it as a delivery/coordinate question in that
   order before assuming an app defect.
1. If the log says the app stayed on the glance page, the simulator never
   reached `getInitialView()`. The driver tried the declared select, the menu
   hold, a tap and one relaunch. Next, check whether a second `monkeydo` starts
   another process, and whether the PRG's manifest app types include the watch
   app. Record what worked here so the launch path can stop guessing.
1. If taps miss, compare a screenshot with `display.location` in the device's
   `simulator.json` and pin `E2E_WINE_SCREEN_RECT` for the next run; record the
   value for the device in this document.

## Fallback if Wine cannot drive the simulator menus

From #140: restart-based mode transitions with reduced coverage. `start()`
already takes the mode per launch, so a scenario can re-launch into another mode
without a scenario-format change; only the runtime menu path remains unproven.
