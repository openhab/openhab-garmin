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
  a still-glance app fails with the glance/transition pointers instead of a
  generic timeout.
- Every `press[...]` case is **measured**: it reports `mechanism=…`, `effect=…`
  and, with `--shim-url`, `requests +N`. A press with no effect is reported
  `skipped` with that evidence, not failed, because one press legitimately does
  nothing (navigation at the top of a list). `--key BUTTON=KEYSYM` (repeatable)
  tries a different keysym.
- `press-sequence` is the input verdict: `DOWN`, `DOWN`, `UP` must produce at
  least one measurable effect. All three silent fails with every mechanism in
  the message. A touch-only device (e.g. `venu2`) has no physical buttons, so its
  press cases are skipped and `tap` covers its input.
- `press-effect` presses ENTER, MENU and DOWN (and taps on touch devices) and
  requires a different view or a screen change in the screen rectangle; a repeat
  of the same view never counts.
- `input-delivery` presses the declared select once and reports whether the app
  changed a view or its screen area. It does not assume a simulator menu, and it
  verifies the pointer is over the simulator before clicking.
- The contract starts with `mode=ble, language=deu`. `deu` is provisioned through
  the measured numeric index and the reported language must match, so a wrong
  index fails the contract loudly instead of running in English.

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
| `E2E_WINE_KEY_<BUTTON>` | X keysym for one button, e.g. `E2E_WINE_KEY_ENTER=Return`; `--key BUTTON=KEYSYM` is the CLI spelling |

## Per-method status

| Method | Status | Notes |
| --- | --- | --- |
| `prepare` | implemented | Validates the external tools and copies the PRG. Runs before anything touches Wine. |
| `start` | implemented | Display check, `simulator.ini`, simulator-window wait, property seeding and `monkeydo` launch. The device name is expected to appear only after the launch. |
| `wait_ready` | implemented | First view **and** the reported language, both from the trace stream. A dead app fails with the console tail. The first view may be the glance (`category=glance`) on a plain `monkeydo` launch; reaching the sitemap is the app's behaviour and the scenario asserts it, not the driver. |
| `press` | implemented, measured | Path per button, recorded: `E2E_WINE_KEY_<BUTTON>`/`--key` first, then a proven keysym (`UP=Up`, `DOWN=Down`), then the declared hit area (mouse, **pointer verified** before the click), then a documented keysym. Per-press measurements are evidence; `press-sequence` is the delivery verdict. |
| `tap` | implemented, pointer verified | The window is focused and the pointer is verified to be over the simulator window before the click (see above). The screen rectangle comes from the device definition's `display.location` (`simulator.json`), with `E2E_WINE_SCREEN_RECT` as the override; a centred fit is only the last resort for an SDK without `simulator.json`. |
| `screenshot` | implemented | Captures the simulator window. Fails when the capture is not a usable PNG instead of returning a blank image. |
| `events` | implemented | `monkeydo` stdout/stderr with receipt timestamps; `stdbuf` disables its block buffering when available. |
| `stop` | implemented | Terminates the app and the simulator by PID; leaks nothing into the next scenario. |
| mode (`ble`/`wifi`/`offline`) | partial | The startup keys are written and reported. The app always starts in `BLE` and only leaves it after a request fails, so the driver does not claim a runtime mode; the scenario's request-count assertions cover the transition. |
| language (`eng`/`deu`/...) | implemented, per code | `eng` removes the `Language` key (simulator default); every other code writes the **numeric** index from `tests/config/simulator-languages.json` and the reported `E2E:LANGUAGE` must match or the run fails. A code with no measured index raises in `start()` and is reported blocked; `python -m e2e language-scan --write` measures it. |
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

### Language is a numeric index

The simulator stores the device language as an **index**, not a code: the
maintainer's working installation carries `Language=7`, and launching with it
makes the app report `E2E:LANGUAGE;code=deu`. The mapping lives in one place,
[`tests/config/simulator-languages.json`](../config/simulator-languages.json),
because the simulator's language order is undocumented and the only honest
source is measurement:

```bash
python -m e2e language-scan --prg <built.prg> --write     # on the guest
```

`language-scan` starts the simulator once per index (default `0-24`), reads
`E2E:LANGUAGE;code=...` from the app and merges `code -> index` into that file.
The whole table was measured on the guest (fenix7, Connect IQ 6.0.4 simulator):

| Index | Code | Index | Code | Index | Code |
| --- | --- | --- | --- | --- | --- |
| 0 | `eng` | 9 | `hun` | 18 | `spa` |
| 1 | `ces` | 10 | `hrv` | 19 | `swe` |
| 2 | `dan` | 11 | `ita` | 20 | `cht` |
| 3 | `dut` | 12 | `nob` | 21 | `chs` |
| 4 | `eng` again | 13 | `pol` | 22 | `jpn` |
| 5 | `fin` | 14 | `por` | 23 | `tha` |
| 6 | `fre` | 15 | `rus` | 24 | `ind` |
| 7 | `deu` | 16 | `slo` |  |  |
| 8 | `gre` | 17 | `slv` |  |  |

Index 4 reports `eng` as well; the map keeps the first index per code. The
app's six languages are therefore all provisionable: `eng` 0, `fre` 6, `deu` 7,
`ita` 11, `pol` 13, `spa` 18, and the blocked-variant count in
`tests/docs/coverage.md` is **0**. `deu: 7` also matches the maintainer's own
`simulator.ini`, which is an independent cross-check of the table.

`--write` checks writability **before** the scan starts, because a read-only
checkout (the runner mounts `/work` read-only) would otherwise waste the whole
25-index run:

```text
cannot write /work/tests/config/simulator-languages.json: [Errno 30] Read-only file system
Re-run with a writable target and copy it back:
  python -m e2e language-scan --prg <prg> --write --output /tmp/languages.json
  cp /tmp/languages.json /work/tests/config/simulator-languages.json
```

- `eng` is the simulator's default. The driver **removes** the `Language` key for
  it rather than writing an index, so a stale `Language=7` from a previous run
  (or the maintainer's own file) cannot run an English scenario in German.
- A code **with** an index is written and then **verified**: `wait_ready` fails
  the run when the app reports a different language, because a run in the wrong
  language asserts the wrong strings. The message names `language-scan`.
- A code **without** an index raises in `start()` and is reported `skipped` by
  the engine (`can_provision_language()` answers per language, not per driver),
  so an unprovisionable language can never masquerade as evidence.
- `tests/docs/coverage.md` counts a variant as blocked only while its code has no
  measured index; the measured table unblocked all 50 localisation runs.

The earlier conclusion that "device language cannot be provisioned" was wrong:
it came from writing `Language=deu`, which the simulator ignores because it wants
an index. `LANG`/`LC_ALL` on the Wine process make no difference either, and that
part still holds.

A mode change at runtime (the simulator's `Simulation` and `Connection` menus)
is **not implemented**. The driver would have to script native Win32
menus with `Alt+S`/`Alt+C` plus arrow keys and confirm the effect from
`E2E:CONNECTION`; the spike under [`spike/`](../spike/README.md) exercises that
path, and its verdict decides whether it is reliable enough for the driver.
Until then a scenario that needs a mode gets it at startup, and the request-count
assertions prove the app's behaviour. The guest also shows there is **no menu bar
to drive**: `xwininfo -root -children` lists only the main window and 1x1 helper
windows, and `Alt+F`/`Alt+S`/`Alt+M` open nothing.

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

### Glance: the build profiles

The app is a **widget**, and a widget that the simulator shows as part of the
glance page is started **in glance mode** (32 KB budget) until it is selected.
This simulator does not select it, so `getInitialView()` never runs and no
request is made. On top of that, the app's real glance crashes the simulator on
its first draw (`Invalid Font Specified`, #396). Three profiles exist:

| Profile | What it does | What it proves |
| --- | --- | --- |
| `watch-app` (default) | Stubs the glance view **and** writes `<artifacts>/build/manifest-watch-app.xml` with `type="watch-app"`, pointing the generated jungle's `project.manifest` at it | The right app type for a direct launch. Today the app's own glance latch (#396) still keeps it on the glance page, so the initial view does not run; this is the profile to keep and it should start working unchanged once the latch is released. Widget and glance modes are **not** covered. |
| `without-glance` | Stubs the glance view, keeps the widget manifest | Compiles, but the simulator still starts the widget in glance mode. |
| `with-glance` | Compiles the app's own glance view | Nothing green: the simulator crashes on the first glance draw (#396). |

`watch-app` is the only valid plain-app type in `monkeybrains.jar`
(`AppTypeManager`: `audio-content-provider-app`, `background`, `datafield`,
`glance`, `watch-app`, `watchface`, `widget`, `barrel`); `app` and `watchapp`
are rejected as `Unknown app type`. The checkout's `manifest.xml` is never
edited: the copy lives in the build output.

- The profile and the full `monkeyc` command are printed for every build, so a
  run is explainable from its log. There is no `-x` in that command:
  `monkeyc -x` is rejected together with `-f` (`The -f command line option is
  incompatible with the options -x, -m, -z`), and the jungle's
  `excludeAnnotations = glance` removes `OHApp` — the manifest entry class —
  and shared classes such as `SitemapStore`, `E2ETrace` and `AppLifecycle`,
  which are annotated `(:glance)` too. The build then fails with
  `Cannot find entry point class '$.OHApp'`.
- A green `watch-app` run says **nothing** about the widget or glance modes.
  Those scope entries are marked **blocked** in
  [coverage.md](coverage.md#summary) with a link to #396, not waived.
- Re-enable the app's own glance when #396 is answered:

  ```bash
  python -m e2e run smoke/launch-and-poll --with-glance
  python -m e2e build --device fenix847mm --with-glance
  ```

  If it still crashes there, the evidence belongs in #396; do not "fix" the
  glance to make the suite green.

The earlier `GlanceViewDelegate` idea is recorded here so it is not retried:
`Toybox.WatchUi.GlanceViewDelegate` has one method, `onGlanceEvent`, documented
as "Invoked when certain glance event occurs, none for now". There is no select
handling, so a delegate cannot be why the simulator stays in glance mode.

## Window size is per device

The simulator persists a window size per device and sizes the window to that
device's art: measured on the guest, **fenix7 is 409x600** and **fenix847mm is
713x961**. Each device's `simulator.json` (`display.location`, `keys[].location`)
is in that device's own art space and maps **1:1 to its own window**, so the
driver reads the geometry on every run and never assumes a size.

`wait_ready` records the size and then checks that the declared
`display.location` fits inside it:

```text
window geometry: 713x961 at (+0,+0)
declared display 134,221 454x454 fits the 713x961 window 1:1
```

If it does not fit, the run stops, because that means the window is another
device's size or the simulator ignored the device — the failure mode that made a
409x600 assumption look correct for fenix7 only:

```text
the simulator window is 409x600, but fenix847mm declares display.location
134,221 454x454, which does not fit inside it.
```

An `E2E_WINE_SCREEN_RECT` pin overrides the check (the operator's rectangle wins
when the definition is wrong). The window position is re-read immediately before
every click, because it changes between runs.

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

### Input: keyboard first, mouse kept

Guest measurements (fenix7, `watch-app` build, screen rectangle cropped before and
after, shim `/__requests` polled after every step):

| Input | Screen change | Requests |
| --- | --- | --- |
| keys `a`, `b` | 0 | 0 |
| `Down` (`nextPage`) | 749 / 731 / 638 on repeated presses | 0 |
| `Up` (`previousPage`) | 638 | 0 |
| `Return`, `KP_Enter`, `Escape`, `Menu`, `space`, `F5`, `F6` | 0 | 0 |
| mouse click on the `enter` rect, screen centre, 1.5 s hold, double click | 0 | 0 |
| the same clicks with `matchbox-window-manager`, `windowactivate --sync` and fullscreen-scaled coordinates | 0 | 0 |

**The keyboard reaches the simulator; button clicks do not (on fenix7).** Every
mouse row above was measured before the driver verified the pointer, so the guest
re-measured with `xdotool getmouselocation`: on fenix7 the pointer _is_ over the
simulator window id when clicking the `enter` hit area and when clicking inside
the screen, the events reach the window, and still no view changes and no request
is made. Pointer and focus are therefore not the explanation on this device.

The difference is probably the input model. The maintainer's working installation
is `fenix9pro47mm`, a **touch** device, where clicking the watch image is a _tap_
the app handles; on fenix7 (`touch+button`) the same click lands on a bezel hit
area whose behaviour the simulator does not forward under Wine:

| Input | Pointer verified | Screen change | Requests |
| --- | --- | --- | --- |
| click inside the screen | yes, simulator window id | 0 | 0 |
| click the declared `enter` hit area | yes, simulator window id | 0 | 0 |
| same clicks without a pointer check | unknown (root window in one trace) | 0 | 0 |

The fenix847mm experiment was run and produced a different failure instead of an
answer: the app printed **nothing at all** on that device (not even
`E2E:LANGUAGE`), so `monkeydo` never got it running and the touch question is
still open. That build took 88 s and the PRG is 642 KB, so packaging for the
device is the first suspect. `wait_ready` now separates the two cases explicitly:

```text
app-ready: monkeydo produced no console output at all within 90s, so the app
never started. This is a launch or packaging problem for this device, not the
glance transition: a device that starts the app prints at least `E2E:LANGUAGE`
```

against the glance case, where the trace arrives and stays on
`category=glance`. Re-run the same command once the fenix847mm build launches;
`press-effect` taps the screen centre (361,448 inside the declared
`134,221 454x454`), so a pass there with fenix7's bezel press failing is the
evidence for "clicks work for touch input only".

**`press-effect` needs a control measurement, and now has one.** On the
fenix847mm run it passed while the app was not running, because a pixel
comparison cannot tell "the press changed the screen" from "the screen redraws on
its own". Two idle captures are now taken `PRESS_BASELINE_INTERVAL` apart before
any input, and a press must change the screen rectangle by **more** than that
idle delta (and by at least `PRESS_PIXEL_THRESHOLD`). The detail string reports
both numbers:

```text
press-effect  pass  tap: screen change 60000px in the screen rectangle (idle baseline 5000px)
```

and a case with no `E2E:` output at all fails immediately with "the app produced
no E2E: console output at all … This is a launch problem, not a press problem",
so an unstarted app can never satisfy it.

`press` therefore picks its path and records it:

1. `E2E_WINE_KEY_<BUTTON>=<keysym>` (or `--key BUTTON=KEYSYM`) always wins;
1. a **proven** keyboard binding (currently `UP=Up`, `DOWN=Down`) is used
   instead of the declared hit area, because the click path is silent;
1. otherwise the declared `keys[].location` rectangle is clicked — the right
   path for select-like buttons once the mouse works, and reported as
   **unproven** until then;
1. the last resort is the documented keysym (`START=ctrl+s`, `ENTER=Return`,
   `BACK=Escape`, `MENU=ctrl+m`).

`--key` exists so candidate select keys can be tried without a code change:

```bash
python -m e2e contract-test --driver wine --prg <prg> --key ENTER=Return
python -m e2e contract-test --driver wine --prg <prg> --key ENTER=KP_Enter --key MENU=Menu
python -m e2e run smoke/launch-and-poll --driver wine --key UP=Up --key DOWN=Down
```

Each measured press reports its path, its effect and (with `--shim-url` or
`E2E_SHIM_URL`) the shim requests it caused, in the normal contract output:

```text
press[DOWN]   skipped  mechanism=keyboard (keysym Down); effect=none; screen=0px; requests +0 — no effect in isolation; the press-sequence case is the delivery verdict
press[ENTER]  skipped  mechanism=mouse (window (378,180)); effect=none; screen=0px; requests +0 — no effect in isolation; the press-sequence case is the delivery verdict
press-sequence pass   sequence delivered: mechanism=keyboard (keysym Down); effect=screen change 731px; screen=731px; requests +0
```

**The per-press measurement is evidence; the sequence is the verdict.** A single
`DOWN` at the top of a glance list legitimately changes nothing — the guest
measured 749/731/638 px on _repeated_ presses and 0 on the first — so a per-press
pass/fail verdict said `press[UP]` was broken when it was not. `press-sequence`
presses `DOWN`, `DOWN`, `UP` and requires at least one measurable effect, which
is a fair test for cumulative navigation while still catching a completely dead
input path (all three silent = fail, with every mechanism in the message).
A press that has no effect is never reported as a pass, and a repeated view is
never an effect: `press-effect` requires a _different_ `VIEW` (category or name),
or, once the app has left the glance, a change inside the declared screen
rectangle.

`tap` remains a call-level case because its coordinate is arbitrary; its detail
says the mouse path is unproven and no effect is asserted there.

### The pointer must be verified, not assumed

A click that lands on the **root window** is indistinguishable from a simulator
that ignores the mouse. That is not hypothetical: on the guest,
`xdotool getmouselocation` reported window `543` (the root) while the simulator
was `85983235`, and Wine logged no button or motion events at all. Every "the
mouse is silent" measurement before this check had that failure mode.

`press` and `tap` therefore never click blind:

1. resolve the simulator window id for _this_ run (it changes between runs);
1. read its geometry immediately before moving, and move the pointer to
   `window origin + declared point`;
1. read `xdotool getmouselocation` back and require the pointer to be at that
   point **and** over the simulator window — the same window id, or another
   window belonging to the same PID, because Wine parents the client area in a
   child window;
1. only then send the button. The verified window id and point appear in
   `last_press()` and in `logs/driver.log`.

If the pointer is not over the simulator the driver raises:

```text
DriverError: the pointer never reached the simulator window: `xdotool getmouselocation`
reports window 543, which is the root window, not the simulator (85983235).
```

That is a hard error rather than a silent press, and the pointer is re-moved once
with freshly read geometry first, because a window that moved between reading its
position and moving the pointer looks identical. If `xdotool getmouselocation` is
unavailable the driver records a warning and continues, so an old xdotool does not
break the run.

### Why the app stays on the glance page

The maintainer reports that **clicking the glance in their simulator switches
into app mode**, so the transition itself works. The open question is why our
synthetic input does not reproduce it: the keyboard scrolls the glance
(`Up`/`Down`) but clicks are silent, and the app also latches glance mode in its
own process:

```monkeyc
if( AppLifecycle.isGlance() ) { return; }   // onStart, onActive, ...
function getGlanceView(...) { AppLifecycle.setGlance(); ... }
```

`setGlance()` sets a process-wide flag, and the simulator does not give the
glance the separate process hardware gives it. Three builds on the guest:

| Build | App source | Result |
| --- | --- | --- |
| `watch-app`, glance stubbed | unchanged | glance only, no request |
| `watch-app` | `getGlanceView()` returns `null`, `setGlance()` kept | still `LANGUAGE` + `category=glance` |
| `watch-app` | `getGlanceView()` returns `null` with no `setGlance()` | `LANGUAGE` only, `monkeydo` exits 0: no first view at all |

So the latch alone does not explain everything (the middle build and the missing
mouse both matter), and neither the harness nor a build profile may patch it: a
test build that changes lifecycle semantics would test a different application.
Both threads are recorded in #396. The `watch-app` profile and the `app-entry`,
`input-delivery` and `press-effect` cases are the right shape and should start
working unchanged once the app and the input environment are understood.

`wait_ready` still tries select, the `onMenu` hold, a second select (two taps on
touch devices) and one PRG relaunch when the first view is the glance. Those are
defensive, and each attempt plus its mapped window point is logged. Each of those
clicks now goes through the verified-pointer path, so the next contract run
settles whether the mouse reaches the simulator at all: `press[ENTER]` either
reports an effect (the click works, and the remaining gap is the app's latch) or
raises _the pointer never reached the simulator window_ (the click never
happened, and the coordinates and keysyms were never the problem).

Two related measurements, so they are not repeated: replacing the glance with a
cleared background changed nothing, and `GlanceViewDelegate` is not a candidate
(it only has `onGlanceEvent`, "none for now"). A widget left in glance mode also
has a 32 KB budget, which is why the glance-only `Invalid Font Specified` may
share the cause.

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
| `warning: the app is still on the glance page` | the glance transition did not happen: clicks are silent in this environment and the app latches glance mode | see "Why the app stays on the glance page"; both threads are on #396 |
| `app-entry … still showing the glance` | `getInitialView()` never ran; the app is on the glance | read the glance section; try `--key ENTER=<keysym>` candidates once the mouse question is settled |
| `app-ready … no console output at all` | the app never started: `monkeydo` produced nothing, so this is launch/packaging for that device, not the glance | read `logs/console.log` and the build log; confirm the PRG is the device's build and fits its memory; launch `monkeydo <prg> <device>` by hand |
| `app-ready … output but no 'E2E:' line` | something ran, but not this E2E build | pass the debug PRG built by `e2e build`; a release build prints no trace |
| `press-effect … no E2E: console output at all` | the app was not running, so no pixel change is attributable to input | same as `app-ready`: fix the launch first |
| `press-effect … beat that` | the screen changes on its own by at least the idle baseline, so the press is indistinguishable | freeze the animated region or assert a view instead of pixels |
| `press-effect … no different VIEW` | the press produced a repeated view or none; while the app is on the glance, screen changes are not counted | see `app-entry`; otherwise compare the logged point with `keys[].location` |
| `press[<button>] … effect=none` | the press ran but changed nothing measurable. Reported as evidence (`skipped`); the verdict is `press-sequence` | try `--key <BUTTON>=<keysym>`; check the mechanism and the verified pointer target in `logs/driver.log` |
| `press-sequence … DOWN, DOWN, UP produced no effect` | no press in the sequence reached the app by any mechanism | read the three mechanisms in the message; if they are mouse presses, check the pointer line above |
| `the pointer never reached the simulator window` | the click would have gone to the root window or another client, so it was refused | the window moved or is not mapped on this display; re-run, and confirm `xdotool getmouselocation` names the simulator window. This is the failure mode behind every "the mouse is silent" measurement |
| `the app reported system language …` | the numeric index was written but the app reports another language, so the index is wrong or was ignored | run `python -m e2e language-scan --prg <prg> --write` and check `tests/config/simulator-languages.json` |
| `language 'pol' has no measured simulator index` | the code is not in the measured map, so the run would be in another language | measure it with `language-scan --write`; until then the run is reported blocked |
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
   case proves synthetic input changes the simulator at all, `press-effect`
   proves a press changes the app, and `press-sequence` decides delivery from a
   `DOWN`, `DOWN`, `UP` sequence. `logs/driver.log` shows the mechanism and the
   verified pointer target for every press. If the driver raises _the pointer
   never reached the simulator window_, the click never happened and no
   coordinate or keysym change can help; if the pointer is verified and the
   press still does nothing, it is a delivery/coordinate question in that order
   before assuming an app defect.
1. Measure the language indices once, which is what unblocks the localisation
   tier: `python -m e2e language-scan --prg <built.prg> --write`. It launches
   once per index and records `code -> index` in
   `tests/config/simulator-languages.json`; `deu: 7` comes from the maintainer's
   installation and has **not** been re-measured by this change. Then run
   `python -m e2e run extended/localisation-sitemap --driver wine` (and a
   `-deu` variant) and confirm the app reports the requested language and the
   `blocked` count in `tests/docs/coverage.md` drops as codes are measured.
1. Try the touch-input experiment: `python -m e2e contract-test --driver wine
   --prg <openhab-garmin-fenix847mm.prg> --shim-url <shim>` taps the screen
   centre in the 713x961 window. A pass there, with fenix7's bezel press failing,
   means clicks work for touch input only, and UI scenarios should prefer touch
   devices.
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
