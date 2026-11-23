# Simulator driver

## Contract

`e2e.sim.base.SimulatorDriver` is the interface every driver implements:

| Method | Meaning |
| --- | --- |
| `prepare(prg)` | make the compiled PRG available to the simulator |
| `start(device, settings, mode)` | start the simulator into `mode` (`ble`/`wifi`/`offline`) and launch the app |
| `wait_ready(timeout_s)` | block until the app rendered its first view |
| `press(button, count)` | press a semantic device button |
| `tap(x, y, count)` | tap a screen coordinate |
| `screenshot(path)` | capture the simulator window as PNG |
| `stop()` | terminate and release the device filesystem |
| `events()` | app console lines, oldest first |

Class attributes state what a driver can actually do: `supports_events` and
`supports_screenshots`. A driver that cannot provide them must not pretend: the
engine reports event and golden assertions as **skipped**, never passed.

`python -m e2e contract-test --driver <name>` exercises every method and reports
`pass` / `fail` / `not-implemented` per case. `--driver null` proves the harness
itself works; `--driver wine` lists exactly what is still missing.

## Why the Wine driver is a skeleton

The investigation recorded in
[#140](https://github.com/openhab/openhab-garmin/issues/140) established:

- the simulator is a wxWidgets Windows GUI application with **no headless mode**
  and no screen or input API;
- `monkeydo <prg> <device>` launches the app and streams `System.println` output
  (which is where `E2E:` trace lines come from);
- startup conditions (`BleState`, `WifiState`, `nightMode`, `SleepMode`,
  `TouchScreenEnabled`, `LastUsedDevice`, `UseHttpsRequirements`) are scriptable
  through `simulator.ini`;
- runtime controls are native Win32 menus, and device input must be injected as
  keyboard/mouse events into the simulator window;
- one simulator instance serves one run, so suites are serial.

That work is feasible but sizeable, and it depends on the trace events from #364 and
on the host decision tracked by the infrastructure issue. It is therefore a
follow-up, not part of the foundation.

## Implementation plan

1. **prepare** – create a throwaway `WINEPREFIX` per run; seed
   `%TEMP%\com.garmin.connectiq` so no run inherits the previous device state.
1. **start** – write `simulator.ini` for the requested device and mode, start the
   simulator under Xvfb, launch with `monkeydo`, and pipe its stdout into the event
   buffer.
1. **wait_ready** – wait for the first `E2E:VIEW` trace event rather than a fixed
   delay.
1. **press / tap** – drive the simulator menus and device buttons with `xdotool`
   against the display. Input coordinates come from `config/devices.yaml`
   (screen size and shape), never from a scenario.
1. **screenshot** – prefer the simulator's "Save Screenshot" action; fall back to
   `xwd`/ImageMagick against the window.
1. **stop** – terminate the simulator and `wineserver`, remove the prefix.

## Failure behaviour today

Every method raises `DriverError` with the next concrete step and a pointer to this
file. `create_driver("wine", ...)` requires a validated SDK layout, so a missing
SDK is reported before any simulator work starts.

Consequences:

- `protocol`-tier scenarios can be developed and reviewed without Wine.
- `ui`-tier scenarios cannot pass until the driver is implemented; they fail loudly
  (`WineSimulatorDriver.start is not implemented yet: ...`), which is the intended
  behaviour.
- `run --dry-run` exercises scenario wiring with the null driver and marks
  simulator-dependent assertions as skipped.

## Fallback if Wine cannot drive the simulator menus

From #140: restart-based mode transitions with reduced coverage. A driver may
implement `start()` for each mode and treat transitions as separate launches; the
scenario schema already expresses the mode per scenario, so no scenario format
change would be needed.
