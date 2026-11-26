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
`(monotonic_timestamp, line)` pairs for the same buffer, and `launch()`, the
measured way out of the glance page: page down ×2, then select (see "Opening the
app: DOWN, then SELECT"). `wait_ready` calls it.

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
- `press-sequence` is the input verdict **while the app is still on the glance**:
  `DOWN`, `DOWN`, `UP` must produce at least one measurable effect, and all three
  silent fails with every mechanism in the message. Once the app is open those
  keys are ordinary navigation — a one-page menu legitimately does not move — so a
  silent sequence there is reported `skipped` with the measurements, and
  `press-effect` judges input on the open app. A touch-only device (e.g. `venu2`)
  has no physical buttons, so its press cases are skipped and `tap` covers its
  input.
- `press-effect` presses ENTER, MENU and DOWN (and taps on touch devices) and
  requires a different view or a screen change in the screen rectangle; a repeat
  of the same view never counts.
- `input-delivery` presses the declared select once and reports whether the app
  changed a view or its screen area. It does not assume a simulator menu, and it
  verifies the pointer is over the simulator before clicking.
- The contract starts with `language=deu` so the numeric index path is proven;
  `--language` changes it. It starts in `mode=ble`; a plain-HTTP stack needs
  `--mode wifi`, because over BLE the platform requires HTTPS and the sitemap
  request is refused with `-1001` (see "`-1001` on a healthy 200").

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
| `wait_ready` | implemented | First view **and** the reported language, both from the trace stream. When the first view is the glance it runs the measured launch sequence and requires a non-glance view. A dead app fails with an actionable message (`no console output at all` = launch/packaging, `output but no E2E: line` = wrong build). |
| `launch` | implemented, retries until the app responds | Page down ×2, then select, with `windowfocus` before every key, a 50 s dwell and a stable screen, then a retry loop (`LAUNCH_RESPONSE_WAIT_S` 4 s, every `LAUNCH_RETRY_INTERVAL_S` 10 s, up to `LAUNCH_RETRY_BUDGET_S` 180 s) cycling click, key tap, keydown/keyup and mouse hold. Each retry logs its index, elapsed time, variant, the focus before/after every key, the exact argv and the resulting view/connection. |
| `press` | implemented, input delivered | Path per button, recorded: `E2E_WINE_KEY_<BUTTON>`/`--key` first, then a proven keysym, then the declared hit area (mouse, **pointer verified** before the click). `mechanism` can force `"mouse"` or `"keyboard"` for the launch retries; the window is focused before every key. |
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

1. A harness-owned copy of `resources/settings/settings.xml` is written to
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
The whole table was measured on the guest (fenix7, SDK 9.3.0):

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

## `-1001` on a healthy 200

Once the launch sequence worked, the app reached the sitemap request and reported:

```text
E2E:VIEW;category=sitemap;view=LoadingView;depth=1
E2E:SITEMAP;result=error;code=-1001;bytes=-
E2E:ERRORVIEW;id=S:-1001;code=-1001
```

while the shim logged the same request as a success:

```text
GET /rest/sitemaps/garmin_e2e -> 200, 207 ms, 11,113 bytes
```

That contradiction is not a measurement error. `-1001` is
`Communications.SECURE_CONNECTION_REQUIRED`, which `docs/USAGE.md` names "secure
connection required", and `BaseRequest.checkResponseCode()` throws for anything
but `200`.

**Superseded by observation: the same BLE/plain-HTTP configuration loads the
sitemap once the launch waits long enough.**

```text
E2E:CONNECTION;mode=BLE
E2E:VIEW;category=sitemap;view=LoadingView;depth=1
E2E:SITEMAP;result=success;code=200;bytes=27936
E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1
```

with `UseHttpsRequirements=0`, `Language=0`, `BleState=2` in `simulator.ini` and a
50 s settle. So `-1001` was **not** a protocol refusal and neither
`UseHttpsRequirements` nor `--mode wifi` is a fix: the earlier runs were launches
whose keys never landed (input delivery on this guest is nondeterministic, see
"Opening the app: DOWN, then SELECT"), so the request the shim logged belonged to
a launch that then failed differently. The key is still written because a working
installation has it, and `--mode`/`--language` stay because scenarios legitimately
differ in what they exercise — but the driver does not depend on either for the
request to succeed.

`bytes=27936` in that line is the app's **estimated** footprint for the
11,113-byte payload, not the body: the 16 KiB wall documented in
[fixture-limits.md](fixture-limits.md) applies to the response body.

If a run still reports `-1001`, check the settle first (the launch log says
whether the screen was stable) before suspecting the transfer; the size and
protocol measurements below only matter once the launch is deterministic.

1. **Payload size.** Build the ~1 KB variant with the checked-in fixture and run
   the same contract:

   ```bash
   python -m e2e build --device fenix7 --settings small-sitemap
   python -m e2e contract-test --driver wine --prg <built.prg>
   ```

   `small-sitemap` points the app at `garmin_localisation_long` (1,014 bytes)
   instead of the ~11 KB `garmin_e2e`. If both sizes report `-1001`, payload size
   and response time are ruled out; if only the large one does, it is a
   size/timeout effect in the simulator's HTTP stack (the 16 KiB _parse_ limit is
   a separate wall).
1. **Protocol.** Run the shim with HTTP/1.0 instead of HTTP/1.1 (the committed
   shim uses 1.1). A stack that waits for the connection to close would also fail
   after a served 200. The operator measured one HTTP/1.0 run that served a
   request in 107 ms but did not reach the app — with the broken launch sequence,
   so that result is **untested, not disproven**; repeat it now.
1. **The simulator's own view.** `CIQ_LOG.YML` records the transfer from the
   simulator side and is copied to `<artifacts>/logs/CIQ_LOG.YML`.

Record which combination produces `E2E:VIEW;category=sitemap` rather than
`SITEMAP;result=error;code=-1001`, because the two are indistinguishable in the
shim log alone.

A related app-side note from the same run: with the error view on screen the app
died with `Invalid Font Specified` at `ErrorView.mc:144` (`drawTextArea()`), the
same class of fault as the glance crash in #396 but on the error path. It now
also happens after the sitemap loads: `HomepageMenu` fails the same way, so three
different screens — glance, homepage and error view — hit it, and it reproduces
on devices emulating Connect IQ 3.4.5, 5.2.0 and 6.0.3 alike.

**It is not a simulator-version regression.** The number in the window title
(`CIQ Simulator - <device> (6.0.3)`) is the **emulated device runtime**, taken
from `Devices/<device>/compiler.json` `connectIQVersion` (fenix7 5.2.0,
fenix847mm 6.0.3, fenix9pro47mm 6.0.3, fenix6pro 3.4.5, edge540 6.0.0); it is not
the simulator's build. The maintainer runs SDK 9.3.0 exactly as we do, so
installing an older SDK cannot test anything — that experiment is withdrawn. The
driver now logs the emulated runtime from `compiler.json` at start, so the title
number cannot be misread again.

The remaining difference between the working setup (Windows) and this one is how
Wine handles fonts. Two experiments, in order:

1. **Install a standard font set into the Wine prefix** (`drive_c/windows/Fonts`,
   or `corefonts` via winetricks when it is available in the runner image) and
   rerun the same PRG and launch sequence. If the simulator asks GDI+ for a face
   the prefix lacks, this is a runner-image fix, not an app fix.
1. **Run once with `WINEDEBUG=+font`** and capture which faces are requested and
   what Wine returns around the `Invalid Font Specified` crash. That log is the
   evidence to act on if installing fonts changes nothing.

Record both outcomes here when they are measured.

One distinction that matters when reading a run: `E2E:VIEW;category=sitemap;view=HomepageMenu`
means the view was **constructed**, not painted. The trace can assert structure
today; it cannot yet assert that anything appeared, because the paint path is
where the font failure lives.

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

### Input: the sequence that was missing

**Input reaches the simulator; the earlier "no input" conclusion was wrong.**
From the glance page the app opens on **DOWN, then SELECT**. Every measurement
below pressed SELECT first, so the table records what those sequences did, not
whether input arrives. The working run and the two defects it exposed are in
[simulator-input.md](simulator-input.md).

The table is kept as history: every row was measured with the screen rectangle
cropped before and after, the shim's `/__requests` polled after every step, and —
from `658f500` on — an **idle baseline**: two captures a second apart with no
input, so a screen that redraws on its own cannot be mistaken for a press that
worked.

| Input | Screen change | Requests |
| --- | --- | --- |
| `Down` (`nextPage`), `Up` (`previousPage`), repeated | 0 | 0 |
| keys `a`, `b` | 0 | 0 |
| `Return`, `KP_Enter`, `Escape`, `Menu`, `space`, `F5`, `F6` | 0 | 0 |
| mouse click on the declared `enter` hit area, pointer verified | 0 | 0 |
| mouse click inside the screen, pointer verified | 0 | 0 |
| the same clicks with `matchbox-window-manager` and `windowactivate --sync` | 0 | 0 |
| the same clicks inside a Wine virtual desktop (`wine explorer /desktop=ciq,1024x768`) | 0 | 0 |

Three earlier numbers of 749/731/638 px for `Down`/`Up` were **the app's first
draw landing late**, not input: with the idle baseline in place every press
measures **0 px**, including the arrow keys whose changes those numbers were.
That is exactly the false positive the baseline exists to catch, and it is why
the per-press measurement is evidence and `press-sequence` is the verdict.

With the pointer verified over the simulator window id at the hit areas and
inside the screen, and with a window manager present or absent, and in a Wine
virtual desktop, every select-first click was silent. That was a sequence
problem: the glance carousel needs DOWN before SELECT. The pointer, focus,
window manager and event source were never the variable.

The consequence, corrected for the next reader:

> The glance needs DOWN, then SELECT. All earlier attempts pressed SELECT first.
> Input was never broken, and UI-level assertions are available on this host.

The source analysis in [simulator-input.md](simulator-input.md) (XTEST is an
ordinary XI2 slave; Wine builds `WM_INPUT` from the same hardware message as the
legacy messages; no synthetic flag exists) is why the input path was the wrong
place to look. The device-backed pointer run showed the same thing from the
other side: a real device moved the pointer, and a select-first click still did
nothing.

The `press`/`tap` machinery is kept and is correct: it delivers input. What was
missing was the sequence the readiness path used, and `launch()` now sends the
measured one. The contract cases (`app-entry`, `input-delivery`, `press-effect`)
measure that sequence.

**`press-effect` must not pass without an effect, and now cannot.** It once passed
while the app was not running at all, because a pixel comparison cannot tell "the
press changed the screen" from "the screen redraws on its own". Two idle captures
are taken `PRESS_BASELINE_INTERVAL` apart before any input, and a press must
change the screen rectangle by **more** than that idle delta (and by at least
`PRESS_PIXEL_THRESHOLD`). The detail reports both numbers:

```text
press-effect  pass  tap: screen change 60000px in the screen rectangle (idle baseline 5000px)
```

and a case with no `E2E:` output at all fails immediately with "the app produced
no E2E: console output at all … This is a launch problem, not a press problem",
so an unstarted app can never satisfy it.

`press` therefore picks its path and records it:

1. `E2E_WINE_KEY_<BUTTON>=<keysym>` (or `--key BUTTON=KEYSYM`) always wins;
1. a documented keyboard binding (currently `UP=Up`, `DOWN=Down`) is used
   instead of the declared hit area;
1. otherwise the declared `keys[].location` rectangle is clicked;
1. the last resort is the documented keysym (`START=ctrl+s`, `ENTER=Return`,
   `BACK=Escape`, `MENU=ctrl+m`).

`--key` exists so a candidate key can be tried without a code change. Given the
measurements above it is an escape hatch, not a workaround:

```bash
python -m e2e contract-test --driver wine --prg <prg> --key ENTER=Return
python -m e2e contract-test --driver wine --prg <prg> --key ENTER=KP_Enter --key MENU=Menu
```

Each measured press reports its path, its effect and (with `--shim-url` or
`E2E_SHIM_URL`) the shim requests it caused, in the normal contract output:

```text
press[DOWN]   skipped  mechanism=keyboard (keysym Down); effect=none; screen=0px; requests +0 — no effect in isolation; the press-sequence case is the delivery verdict
press[ENTER]  skipped  mechanism=mouse (window (378,180)); effect=none; screen=0px; requests +0 — no effect in isolation; the press-sequence case is the delivery verdict
press-sequence fail   a sequence of DOWN, DOWN, UP produced no effect on any press
```

On this guest `press-sequence` fails because its sequence is DOWN, DOWN, UP: it
never presses SELECT after the DOWN, so it cannot open the glance. The case
needs the two-step sequence; the environment is not the cause.

**The per-press measurement is evidence; the sequence is the verdict.** A single
`DOWN` at the top of a glance list legitimately changes nothing, so a per-press
pass/fail verdict would be unfair to a working input path. `press-sequence`
presses `DOWN`, `DOWN`, `UP` and requires at least one measurable effect, which is
a fair test for cumulative navigation and still catches a completely dead input
path (all three silent = fail, with every mechanism in the message). A press that
has no effect is never reported as a pass, and a repeated view is never an
effect: `press-effect` requires a _different_ `VIEW` (category or name), or, once
the app has left the glance, a change inside the declared screen rectangle.

`tap` remains a call-level case because its coordinate is arbitrary; its detail
names the verified window and point, and no effect is asserted there.

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
   point **and** over the simulator window — the same window id, another window
   belonging to the same PID, or a window whose rectangle lies inside the
   simulator window's. Wine creates several X windows per process (a splash and
   the client area were both seen), so only a window outside the simulator is
   refused;
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

Verifying the pointer is still necessary: a click that lands on the root window
is silent for a different reason. It is no longer the explanation for the guest's
silence; the select-first sequence was. Keep the check so a future failure does
not repeat the earlier confusion.

### Opening the app: DOWN, then SELECT

The app opens from the glance on **page down (twice in the measured runs), then
SELECT** (measured 2026-11-26). `WineSimulatorDriver.launch()` implements exactly
that and `wait_ready` calls it, so the app now reaches `getInitialView()` and the
sitemap request runs.

An earlier three-run comparison of `Down`, `Down`, `Return` suggested per-key
focus made it deterministic (2/2 with focus, 0/1 without), but the later runs
below show that was luck: the same focused command also fails. Focus is still
taken before every key, because it costs nothing and focus did move between keys
in one run, but it is not the deciding factor.

**Input delivery is nondeterministic on this guest, so `launch()` retries.** The
guest ran the same command in the same container, same PRG, same 50 s dwell and
same focused client window three times:

| Keys | Result |
| --- | --- |
| `xdotool key --clearmodifiers Down` ×2, `Return` | worked: `E2E:CONNECTION`, `SITEMAP;result=success;code=200`, `LoadingView` |
| `xdotool key Down` ×2, `Return` | failed: no `E2E:CONNECTION` |
| `--clearmodifiers` again, after resolving the client window explicitly | failed: no `E2E:CONNECTION` |

So `--clearmodifiers`, the window choice and the variant are all fine; the same
command alternates between working and doing nothing, roughly one run in two, and
the successful runs took anywhere between one and several minutes after launch.
The answer is a loop, not a better single sequence:

1. **Settle until the app is interactive** (below), then send **one** sequence.
1. **Wait `LAUNCH_RESPONSE_WAIT_S` = 4 s** for `E2E:CONNECTION` or a non-glance
   view. `E2E:CONNECTION` is the app starting, which is the guest's success
   signal; a different view or an `ERRORVIEW` also counts.
1. **Repeat every `LAUNCH_RETRY_INTERVAL_S` = 10 s** for up to
   `LAUNCH_RETRY_BUDGET_S` = 180 s (`E2E_WINE_LAUNCH_RETRY_BUDGET`,
   `E2E_WINE_LAUNCH_RETRY_INTERVAL`), stopping the moment the app responds. The
   whole `wait_ready` budget is 300 s.
1. **Cycle the four delivery variants** — declared hit areas, keyboard taps,
   keydown/keyup held 50 ms, and a held mouse button — as cheap alternatives
   inside the loop, not as one-shot attempts. The variant does not appear to
   matter; retrying the same one is what succeeded on the guest.

Each retry logs its index, elapsed time, variant, the `E2E:CONNECTION` state and
the view, so the flakiness is data in the log:

```text
launch retry 3: variant=keyboard keydown/keyup; elapsed=71.4s
launch step page down 1/2: focus before=10485763; focus after=10485763; view=glance/…; connection=none
command: xdotool keydown --clearmodifiers Down
launch retry 3 (keyboard keydown/keyup) opened the app: VIEW;category=sitemap;view=LoadingView;depth=1
```

The settle itself has three properties:

1. **The window is focused before every key** (`windowfocus`, which needs no
   window manager; `windowactivate` is attempted once and is not relied on).
1. **The glance is given time to become interactive, not just to draw.** The app
   must have been running for `LAUNCH_DWELL_S` = **50 s** since app start
   (`E2E_WINE_LAUNCH_DWELL`), and the screen must be stable (two captures 1 s
   apart that match), bounded at `LAUNCH_SETTLE_MAX_S` = 90 s
   (`E2E_WINE_LAUNCH_SETTLE`). The guest's verified manual command waits 50 s
   after `monkeydo`; a launch at 41.7 s failed every attempt even though the
   screen had been stable for a while.
1. **Every key is sent like the verified command**: focus, a 0.3 s pause, the
   key, then a 2 s gap before the next (`LAUNCH_KEY_GAP_S`), the exact argv
   logged (`command: xdotool …`), and `xdotool getwindowfocus` recorded before
   **and** after every key. After every mouse click it captures
   `launch-step-<step>.png`.

Before the first sequence it also sends one `Down` and measures the screen with
a before/after capture:

```text
selection probe at 52.1s since app start: one page down changed the screen by 4200px — the glance selection moved. This is an early signal only; the launch retries below decide whether input lands.
```

A zero there is **not** a verdict: the probe runs before the first retry, and the
guest has shown `0px` on a run whose second retry then opened the app. The
message says so, and records the elapsed time, so the two are not read as a
contradiction. `E2E_WINE_LAUNCH_DOWNS` changes the page-down count for a device
whose app entry is a different number of pages away.

Success requires `E2E:CONNECTION`, a view whose category is not `glance`, or an
`ERRORVIEW`, so a silent retry cannot pass as a launch.

The app also latches glance mode in its own process:

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

Neither the harness nor a build profile may patch the lifecycle: a test build
that changes the semantics would test a different application. The three rows
above were measured with the select-first sequence; they are kept only to show
what that wrong sequence produced. With the measured launch sequence the app
reaches the sitemap, and the `app-entry`, `input-delivery`, `press-effect` and
`press-sequence` cases measure the app, not the sequence.

Two related measurements, so they are not repeated: replacing the glance with a
cleared background changed nothing, and `GlanceViewDelegate` is not a candidate
(it only has `onGlanceEvent`, "none for now"). A widget left in glance mode also
has a 32 KB budget, which is why the glance-only `Invalid Font Specified` may
share the cause. Both are recorded in #396.

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
| `warning: the app is still on the glance page` | the launch sequence did not open the app | read the `launch attempt …` lines: mechanism, point or keysym, and the view each step produced. `E2E_WINE_LAUNCH_DOWNS` changes the page-down count |
| `app-entry … still showing the glance` | `getInitialView()` did not run; the launch sequence did not open the app | read the `launch attempt …` lines: each records the mechanism, the point or keysym, and the view it produced. `E2E_WINE_LAUNCH_DOWNS` changes the page-down count |
| `app-ready … no console output at all` | the app never started: `monkeydo` produced nothing, so this is launch/packaging for that device, not the glance | read `logs/console.log` and the build log; confirm the PRG is the device's build and fits its memory; launch `monkeydo <prg> <device>` by hand |
| `app-ready … output but no 'E2E:' line` | something ran, but not this E2E build | pass the debug PRG built by `e2e build`; a release build prints no trace |
| `press-effect … no E2E: console output at all` | the app was not running, so no pixel change is attributable to input | same as `app-ready`: fix the launch first |
| `press-effect … beat that` | the screen changes on its own by at least the idle baseline, so the press is indistinguishable | freeze the animated region or assert a view instead of pixels |
| `press-effect … no different VIEW` | the press produced a repeated view or none | check the launch log first: the app is only entered by page down then select |
| `press[<button>] … effect=none` | the press ran but changed nothing measurable. A single press can legitimately do nothing. Reported as evidence (`skipped`); the sequence is the verdict | check the mechanism and verified pointer in `logs/driver.log`; then the order of the presses |
| `press-sequence … produced no effect` | no press in the sequence had an effect **while the app was still on the glance**, so delivery has not been shown | read the three mechanisms; delivery on this guest is nondeterministic and the launch retries, so a single silent sequence is not conclusive. `--key DOWN=<keysym>` tries another binding. On an open app the case reports `skipped` instead |
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

## The native Linux simulator: input and text (#402)

The mounted SDK also ships a native build,
`linux/bin/simulator` (31,142,896 bytes, ELF x86-64, GTK3 + WebKit). It runs the
same `monkeydo` push protocol, so it fits the existing driver shape. It was
exercised end to end on 2026-11-26 in a disposable `ubuntu:22.04` container: it
accepts input, loads the sitemap and **paints the text**, exactly as the Wine
simulator does. Wine remains the driver because a second driver buys nothing
today, not because the native build is unusable.

### Getting a window under Xvfb

Ubuntu 22.04 is required: 24.04 dropped `webkit2gtk-4.0`, and `ldd` reports 12
missing libraries there. With the following the count is 0:

| Purpose | Packages |
| --- | --- |
| simulator runtime | `libgtk-3-0 libwebkit2gtk-4.0-37 libsecret-1-0 libnotify4 libusb-1.0-0 librsvg2-common` |
| display and capture | `xvfb x11-utils x11-xkb-utils xauth xdotool imagemagick` |
| `monkeydo` (`monkeybrains.jar`) | `default-jre-headless` |

`librsvg2-common` alone is not enough; the pixbuf cache has to be regenerated or
the first draw aborts on an Adwaita SVG:

```bash
/usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/gdk-pixbuf-query-loaders \
  > /usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/loaders.cache
```

The binary on the read-only mount is `-rw-rw-r--`, so it has to be copied and
`chmod +x`, or the mount fixed.

Three things, and only these three, are needed to get a window:

| Requirement | Failure without it |
| --- | --- |
| `XDG_DATA_DIRS=/usr/local/share:/usr/share` | GTK cannot load Adwaita's settings, logs `Gtk-CRITICAL: _gtk_style_provider_private_get_settings` and never maps a window |
| `GTK_THEME=Adwaita`, `GDK_BACKEND=x11` | the theme and backend are explicit rather than inherited |
| device definitions in `$HOME/.Garmin/ConnectIQ/Devices` | the Linux build reads the same location as `monkeyc`; `python -m e2e link-devices` already stages it |

**No window manager is needed.** Xvfb alone maps a 446×700 window titled
`CIQ Simulator - fēnix® 7 / quatix® 7 (5.2.0)`. `matchbox-window-manager` was
tried and is actively unhelpful: it maximises the window to the display size,
which destroys the `display.location` rectangle (fenix7: `x=77, y=146, 260×260`)
that pixel checks crop to.

### The single-instance lock is `$HOME/Sim-root`

The native simulator holds `$HOME/Sim-root`, a file containing the PID of the
instance. A simulator that died without being reaped stays a **zombie**, and
`kill(pid, 0)` still succeeds for it, so every later start opens the file
`O_EXCL`, sees it, and exits with code 255 within a second — after nothing but
the harmless AT-SPI `dbind-WARNING`. There is no other message. Recovering is
one line:

```bash
rm -f "$HOME/Sim-root"
```

This is the Wine driver's "stray simulator" hazard in a different shape; a
native driver would have to sweep the lock, not just the process.

### What does work

- **Input.** `xdotool windowfocus <window>`, then `xdotool key Down`, `Down`,
  `Return` (XTEST, no window manager, no pointer verification) opens the app
  from the glance: `E2E:CONNECTION;mode=BLE`,
  `E2E:VIEW;category=sitemap;view=LoadingView;depth=1`. Native X input is
  ordinary input, as expected.
- **HTTP.** With `UseHttpsRequirements=0` in
  `$HOME/.Garmin/ConnectIQ/simulator.ini` — the same key and the same flat
  `key=value` format the Wine driver writes — the app loaded the real sitemap
  through a local shim: `E2E:SITEMAP;result=success;code=200;bytes=29680`,
  followed by `E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1`.

Native input deserves to be stated on its own: the older story — "input is
impossible on Linux" — was really "input is impossible **through Wine** with a
select-first sequence". XTEST against a native GTK window is ordinary X input and
is now measured working. Wine stays the driver, but if it regresses further the
native build is a viable fallback for the input path: `press`, `screenshot` and
the `monkeydo` event stream all have working primitives here, behind the same
`SimulatorDriver` interface.

### Two traps when driving either simulator

- **The device screen is scaled inside the window.** The 260×260 device screen is
  drawn at **309×309**: at `(53,147)` in a native 446×607 window and at
  `(53,141)` in a 409×600 Wine window. `display.location` is therefore _not_ 1:1
  with the window in this setup, so a crop has to be derived from the window (the
  dark circular region) or pinned with `E2E_WINE_SCREEN_RECT`. Cropping the
  declared rectangle lands on the bezel and the idle watch face.
- **Under Wine the device window only exists after the push.** `simulator.exe`
  starts with just the 342×666 control window; the device window appears when
  `monkeydo` pushes the app. Searching for it before the push returns nothing,
  and keys sent to the control window do nothing.

### The font pack was the whole cause

The native simulator opens `$HOME/.Garmin/ConnectIQ/Fonts/` before every draw
(traced with `strace -e trace=openat,stat,access`):

```text
openat(AT_FDCWD, "/root/.Garmin/ConnectIQ/Fonts/", O_RDONLY) = 7
```

The open succeeds, and with that directory empty the device faces cannot be
resolved: the app crashed with `Invalid Font Specified` and the screen stayed on
the watch face. Garmin's own `samples/TrueTypeFonts` failed identically, and
installing `fonts-roboto` did not help, because the runtime resolves device font
_filenames_ through its own FNT/FreeType path (`FNT_intf.cpp`,
`FT_Get_WinFNT_Header` are in the binary), not fontconfig families. The SDK
archive ships no device fonts at all: `find <sdk> \( -iname '*.fnt' -o -iname
'*.ttf' \)` returns 8 files, all assets of `samples/Analog`.

Copying the pack from a working Windows installation
(`%APPDATA%\Garmin\ConnectIQ\Fonts`, 2,756 entries: 1,332 `.cft` compiled faces,
46 `.ttf`, 1,378 `.md5`) into `$HOME/.Garmin/ConnectIQ/Fonts` changed everything.
Measured on both hosts with the same debug PRG, the same `monkeydo` push and the
same `Down`, `Down`, `Return` sequence:

| Host | Sitemap | Text on screen | `Invalid Font Specified` | `ErrorView` |
| --- | --- | --- | --- | --- |
| native `linux/bin/simulator` | `SITEMAP;success;code=200;bytes=29680`, `HomepageMenu` | **yes**: OCR of the screen crop reads "My Home / Text / Default / Point and Slider" | no | draws "Request failed" |
| `simulator.exe` under Wine | same response, same view | **yes**, identical OCR | no | draws "Request failed" |

`compare -metric AE` between the native and the Wine screen crops is **0** for
both the sitemap and the error view (`RMSE 0 (0)`): one rasteriser, no Wine
artefact. The two simulators are interchangeable for rendering.

### The font pack is a harness requirement

Because the app crashes on its first draw without the pack, the harness treats it
like the SDK mount: licensed assets that are mounted or copied, never committed
and never baked into an image.

- `python -m e2e link-fonts` copies the pack into
  `$HOME/.Garmin/ConnectIQ/Fonts` and links that directory into the Wine
  prefix's `ConnectIQ/Fonts`. The home copy is real files on purpose: the Wine
  simulator did not follow a symlink chain into the read-only mount and still
  raised `Invalid Font Specified`.
- `python -m e2e sdk-check` fails — and `entrypoint.sh` therefore refuses to
  start the runner — when the directory is missing, empty, or holds no `.cft` or
  `.ttf` file. The message names the directory, says the SDK archive does not
  ship the fonts, and names `%APPDATA%\Garmin\ConnectIQ\Fonts` as the source.
- `preflight` reports it as `sdk.fonts` in the `sdk` group, so CI's
  `--skip docker,sdk,ports` skips it with the rest of the SDK.
- `run --update-goldens` refuses without the pack and names the directory. With
  the pack present the capability is available to **every** driver, because the
  gate is the assets, not the driver name.

Compose mounts the pack read-only at `/opt/ciq-fonts` from
`CIQ_FONTS_HOST_PATH` (see `tests/compose/.env.example`).

### Verdict and cost

Wine stays the driver and the default: a second driver would buy input, which
already works, and a rasteriser that is pixel-identical to the one we already
have. What did change is the **visual tier**: goldens can now be captured on the
guest from either host, so "goldens must come from Windows" is retired.

The native build remains the fallback for the input path if Wine regresses, and
the path to dropping Wine entirely. If a `linux` driver is written, the image
cost is concrete: base `ubuntu:22.04`, drop the `i386` architecture and
`wine/wine32:i386/wine64` (roughly the bulk of the 2.8 GB runner image), and add
the packages above. `monkeyc`, the unit tests, the fixtures and the compose pins
are unaffected by the base change; only the `wine` driver would need a Wine image
of its own.

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
proof is a driver run on the guest, which the operator performs. Since `658f500`
the guest has confirmed the language path end to end (`deu` provisioned through
index 7) and the input limitation below.

1. Build the debug PRG with the contract's fixture:
   `python -m e2e build --device fenix6pro --settings default` (it prints the
   PRG path and the compiled properties). `--settings` is what compiles the
   app's property defaults in, and the build fails if `monkeyc`'s settings JSON
   does not echo them.
1. Run `python -m e2e contract-test --driver wine --prg <built.prg>` inside the
   runner container. It may take several minutes: the driver settles ~50 s, then
   retries the launch sequence for up to 3 minutes until `E2E:CONNECTION`
   appears. Every case should pass; if `app-entry` fails, the `launch retry …`
   lines say how many retries ran, with which variant, and what the view and
   connection state were each time.
1. Run `python -m e2e run smoke/launch-and-poll --driver wine` (or `--prg
   <built.prg>`; the run command builds per scenario, with that scenario's
   fixture) and confirm the request assertions are evaluated, not skipped.
1. Open `logs/driver.log` and `logs/CIQ_LOG.YML`: the first should show the
   `simulator.ini` keys (`Language=7` for `deu`, `Language` removed for `eng`,
   `UseHttpsRequirements=0`), the transport, `app properties come from the
   build`, the launch attempts, the device title and the window geometry; the
   second is the evidence when anything crashed. `logs/ciq-log-errors.txt` lists
   the classified errors.
1. Run one language scenario (`extended/localisation-sitemap`): all six
   languages should execute (no `skipped` runs) and each should confirm its
   reported language. Run one mode scenario (`extended/wifi-mode`) to confirm
   the mode verification path.
1. Confirm the sitemap loads. With the settle in place the same BLE/plain-HTTP
   stack succeeds (`SITEMAP;result=success;code=200`); `--mode` selects what a
   scenario exercises, not a workaround. If a run reports `-1001`, read the
   launch log first — it says whether the screen was stable before the keys — and
   only then the ordered measurements in "`-1001` on a healthy 200".
1. If taps miss on a device, compare a screenshot with `display.location` in
   the device's `simulator.json` and pin `E2E_WINE_SCREEN_RECT` for the next run.
   The `wait_ready` window-fit check already refuses a window that cannot contain
   the declared rectangle.

## Fallback if Wine cannot drive the simulator menus

From #140: restart-based mode transitions with reduced coverage. `start()`
already takes the mode per launch, so a scenario can re-launch into another mode
without a scenario-format change; only the runtime menu path remains unproven.
