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
- The contract starts with `mode=ble, language=deu`, so a language regression
  surfaces in `wait_ready` rather than in a scenario.

## How the Wine driver works

The simulator is a wxWidgets Windows application with no automation API (the
investigation is recorded in [#140](https://github.com/openhab/openhab-garmin/issues/140)).
The driver runs it under Wine on the X display the runner starts and drives it
from the outside:

1. **prepare** — check that Wine, `monkeydo`, `xdotool`, `xdpyinfo` and
   ImageMagick's `import` exist, then copy the PRG to
   `<artifacts>/launch/app.prg`.
1. **start** — verify the display answers; rewrite `simulator.ini` for the
   device, communication state and language; start `wine simulator.exe` and
   wait for its window; seed the app properties; launch
   `monkeydo <prg> <device>` and capture its stdout.
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

Waiting for the device name *before* launching deadlocks for the full startup
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

Artifacts land under the run's artifacts directory:

| Path | Contents |
| --- | --- |
| `logs/driver.log` | every driver decision, including the PIDs and the ini/properties files it wrote |
| `logs/simulator.log` | Wine's stdout/stderr for the simulator |
| `logs/console.log` | `monkeydo`'s stdout/stderr, i.e. the app console with the `E2E:` lines |

`stop()` never uses `pkill -f <pattern>`: inside `docker exec` such a pattern
matches the exec's own command line and kills the exec. The driver keeps both
PIDs instead.

### Environment overrides

| Variable | Effect |
| --- | --- |
| `E2E_WINE_BINARY` | Wine binary, when it is not on `PATH` |
| `DISPLAY` | X display (default `:99`) |
| `E2E_WINE_STARTUP_TIMEOUT` | seconds to wait for the simulator window (default `120`); the app's first trace line is bounded by the caller (90 s by default) |
| `E2E_WINE_SETTINGS_FILE` | exact application-properties file to seed; skips discovery |
| `E2E_WINE_SCREEN_RECT` | `x,y,width,height` of the watch screen inside the window; pin it when taps miss |
| `E2E_WINE_KEY_<BUTTON>` | X keysym for one button, e.g. `E2E_WINE_KEY_MENU=ctrl+m` |

## Per-method status

| Method | Status | Notes |
| --- | --- | --- |
| `prepare` | implemented | Validates the external tools and copies the PRG. Runs before anything touches Wine. |
| `start` | implemented | Display check, `simulator.ini`, simulator-window wait, property seeding and `monkeydo` launch. The device name is expected to appear only after the launch. |
| `wait_ready` | implemented | First view **and** the reported language, both from the trace stream. A dead app fails with the console tail. The first view may be the glance (`category=glance`) on a plain `monkeydo` launch; reaching the sitemap is the app's behaviour and the scenario asserts it, not the driver. |
| `press` | implemented | Keysym per semantic button; the device definition's `simulator.json` is preferred over the defaults. |
| `tap` | partial | The window is focused first. The screen rectangle is a centred, aspect-preserving fit of the device resolution in the window; if the bezel makes that wrong, pin `E2E_WINE_SCREEN_RECT` from a screenshot. |
| `screenshot` | implemented | Captures the simulator window. Fails when the capture is not a usable PNG instead of returning a blank image. |
| `events` | implemented | `monkeydo` stdout/stderr with receipt timestamps; `stdbuf` disables its block buffering when available. |
| `stop` | implemented | Terminates the app and the simulator by PID; leaks nothing into the next scenario. |
| mode (`ble`/`wifi`/`offline`) | partial | The startup keys are written and reported. The app always starts in `BLE` and only leaves it after a request fails, so the driver does not claim a runtime mode; the scenario's request-count assertions cover the transition. |
| language (`eng`/`deu`/...) | implemented, verified | `Language` is written before start and **verified** from `E2E:LANGUAGE`. A mismatch fails the run with the ini file and what was written. |
| app properties | implemented, verified | See below. Without properties the app shows `UI:CONFIG`, which would make every scenario pass vacuously, so the driver refuses to run unseeded and rejects that error view. |

## Application properties

The app reads its settings once at startup from `Properties.getValue`. The
driver seeds them by rewriting the file the simulator itself created:

1. Search the Wine prefix for `*.SET` and `settings.*` files under
   `AppData/Roaming/Garmin/ConnectIQ`, `AppData/Local/Garmin/ConnectIQ` and
   `Temp/com.garmin.connectiq`.
1. Prefer the file that already contains the app's property ids, so a stray
   file is not rewritten.
1. Keep the file's format — JSON (flat or under a `properties` object) or
   `key=value` — preserve unrelated keys, and write the fixture's values with
   their types.
1. Read the file back and fail when a value did not round-trip.

If no properties file exists yet, the driver performs **one discovery launch**:
it starts the app, waits up to 25 seconds for the simulator to create the file,
stops the app, seeds the file and launches again. If the file still does not
appear, `start` raises and names the patterns it searched, because a run with
empty settings is worse than no run.

After the launch, `wait_ready` raises when the app reports
`ERRORVIEW;id=UI:CONFIG` although the fixture configured a URL. That is the
machine-readable proof that the properties reached the app. A scenario that
deliberately empties `url_0` (for example
`extended/localisation-error-config-url`) is unaffected, because the fixture's
URL is empty.

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

Language is written as `Language=<code>` and is **verified** through
`E2E:LANGUAGE`, which the app emits from
`System.getDeviceSettings().systemLanguage`. The configuration that was written
is never used as evidence.

A mode change at runtime (the simulator's `Simulation` and `Connection` menus)
is **not implemented**. The driver would have to script native Win32 menus with
`Alt+S`/`Alt+C` plus arrow keys and confirm the effect from `E2E:CONNECTION`;
the spike under [`spike/`](../spike/README.md) exercises that path, and its
verdict decides whether it is reliable enough for the driver. Until then a
scenario that needs a mode gets it at startup, and the request-count assertions
prove the app's behaviour.

Night mode, sleep mode and HTTPS requirements are also only available through
those menus and are not scripted.

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

Button bindings come from the device definition's `simulator.json` (`keys` as
Windows virtual-key codes) when the SDK ships one, and otherwise from these
defaults: `START=ctrl+s`, `UP=Up`, `DOWN=Down`, `ENTER=Return`,
`BACK=Escape`, `MENU=ctrl+m`. Override any of them with
`E2E_WINE_KEY_<BUTTON>`. A device whose definition does not confirm the title
only produces a warning, so a missing definition does not fail a run over a
name it cannot compare.

`tap(x, y)` takes **device screen pixels**. The driver maps them into the window
through the watch-screen rectangle. The default rectangle is the centred,
aspect-preserving fit of the device resolution inside the window; the Connect IQ
simulator scales the device graphic that way, but a thick bezel can shift it.
Pin the rectangle from a screenshot when taps land in the wrong place:

```bash
E2E_WINE_SCREEN_RECT=100,160,416,416 python -m e2e run smoke/toggle-switch --device venu2
```

Capturing the root window is never an option: on a display without a window
manager it is a single colour and would silently satisfy a golden assertion.

## Failure modes

Every failure names the stage, the timeout, and the next concrete step, so a log
is enough to act on it.

| Message | Meaning | Next step |
| --- | --- | --- |
| `no X display is answering on ':99'` | the display never came up, or `DISPLAY` names the wrong one | run `xdpyinfo -display :99` in the container; the entrypoint starts Xvfb before staging the SDK |
| `the simulator opened an error dialog` | the Wine prefix has no device definitions, or the simulator cannot start | `python -m e2e link-devices`, then check the prefix's `AppData/Roaming/Garmin/ConnectIQ/Devices` |
| `simulator-window … no window titled 'CIQ Simulator' appeared` | the simulator process never produced a window | read `logs/simulator.log`; the device definitions are only one cause at this point |
| `device title … does not match` / `does not name a device` | **warning only**; the trace stream already proved the app is running | check `LastUsedDevice` in `simulator.ini` if results look wrong |
| `monkeydo exited with code …` | wrong PRG or device name | read `logs/console.log` |
| `app-ready … no console line` | the app started but produced no `E2E:` line | check the PRG is a debug build and the device wiring (`link-devices` is accurate here) |
| `the app reported system language …` | `Language` in `simulator.ini` was ignored | see the language section above |
| `the app showed the configuration error view` | the seeded properties did not reach the app | pin `E2E_WINE_SETTINGS_FILE` |
| `no application-properties file was found` | the simulator never created one | run the app once, save its settings, pin the file |

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

1. Build the debug PRG (`python -m e2e build --device fenix7`; the command prints
   the path it wrote), which the `E2E:` trace needs.
1. Run `python -m e2e contract-test --driver wine --prg <built.prg>` inside the
   runner container and confirm the report is all `pass` with exit `0`.
1. Run `python -m e2e run smoke/launch-and-poll --driver wine --prg <built.prg>`
   and confirm the assertions are evaluated, not skipped.
1. Open `logs/driver.log` and check the `simulator.ini` line names the file and
   the keys that were written, and that the seeded properties file is the one
   the simulator wrote.
1. Run one language scenario (`extended/localisation-sitemap`) and one mode
   scenario (`extended/wifi-mode`) to confirm the verification paths.
1. If taps miss, take a screenshot, read the watch-screen rectangle from it and
   set `E2E_WINE_SCREEN_RECT` for the next run; record the value for the device
   in this document.

## Fallback if Wine cannot drive the simulator menus

From #140: restart-based mode transitions with reduced coverage. `start()`
already takes the mode per launch, so a scenario can re-launch into another mode
without a scenario-format change; only the runtime menu path remains unproven.
