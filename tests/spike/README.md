# Wine simulator spike (#370)

A one-command feasibility experiment: can the Windows Connect IQ simulator be
driven reliably enough under Wine/Xvfb to justify implementing
`WineSimulatorDriver`?

The script follows the procedure in
[#370](https://github.com/openhab/openhab-garmin/issues/370) end to end against
the **same** runner image the E2E harness uses. It does not implement the driver,
does not change the app, the Compose stack, the scenarios or the device matrix,
and never runs in CI.

The deliverable is a verdict, not a green build:

- `verdict.json` -- machine-readable, one check per observation with evidence
  paths and measurements;
- `verdict.md` -- the same result as a table plus console excerpts, ready to
  paste into the issue.

## Prerequisites

- A **Linux** host with Docker and Docker Compose v2. The simulator is a 32-bit
  Windows binary and the image installs `wine32:i386`; macOS and Windows hosts
  are not supported targets.
- The Connect IQ SDK **9.3.0** (the pin in `tests/config/sdk.version`),
  downloaded from Garmin with the licence accepted.
- Roughly 20 minutes for the first run: the image build, the Wine prefix, two
  devices, language runs, timing runs and two escalation runs.

No secrets, no SDK binaries and no generated PRGs are committed.

## SDK layout and mounting

The spike needs **both** SDK archives in one root:

| Path | Comes from | Used for |
| --- | --- | --- |
| `linux/bin/monkeyc` | Linux SDK archive | compile the debug PRG (Java launcher) |
| `linux/bin/monkeydo` | Linux SDK archive | launch the PRG and stream `System.println` |
| `windows/bin/simulator.exe` | Windows SDK archive | the simulator, started under Wine |
| `version.txt` | either archive | version pin check |

The flat spelling (`bin/monkeyc`, `bin/monkeydo`, `bin/simulator.exe`) is also
accepted, matching [../docs/sdk.md](../docs/sdk.md). If you downloaded the two
archives separately, merge them into one directory:

```text
/opt/garmin/connectiq-sdk-9.3.0/
  version.txt
  linux/bin/monkeyc
  linux/bin/monkeydo
  windows/bin/simulator.exe
```

The host path is mounted read-only at `/opt/ciq-sdk`; the checkout is mounted
read-only at `/work`; only `tests/artifacts/spike` (by default) is writable. The
SDK is supplied **at run time**, never baked into an image and never copied into
the repository.

Set it either way:

```bash
export CIQ_SDK_HOST_PATH=/opt/garmin/connectiq-sdk-9.3.0
# or pass --sdk /opt/garmin/connectiq-sdk-9.3.0
```

`CIQ_SDK_ROOT` and `CIQ_SDK_PATH` are accepted as aliases, like the harness.

## Exact command

Check the host first. This builds nothing and starts no container; it validates
Docker and its daemon, the checkout and the SDK layout, and prints the paths.

```bash
cd tests
python -m spike check
```

Run the spike:

```bash
cd tests
CIQ_SDK_HOST_PATH=/opt/garmin/connectiq-sdk-9.3.0 python -m spike run
```

Or with an explicit SDK and artifact directory:

```bash
cd tests
python -m spike run \
  --sdk /opt/garmin/connectiq-sdk-9.3.0 \
  --artifacts /var/tmp/openhab-garmin-spike
```

Inspect the Docker commands without running them:

```bash
cd tests
python -m spike run --dry-run --sdk /opt/garmin/connectiq-sdk-9.3.0
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--button-device fenix6pro` | button device (default `fenix6pro`; `fenix847mm` also works) |
| `--touch-device venu3` | touch device (default `venu3`; `venu2` also works) |
| `--language deu` | language to switch to (default `deu`) |
| `--poll-intervals 0,1000` | polling intervals to measure, in ms |
| `--skip-build` | reuse an already built runner image |
| `--keep-container` | leave a timed-out container for inspection |
| `--json` | print the verdict JSON to stdout as well as writing files |
| `--timeout 1800` | overall probe timeout in seconds |

## What the script does

Inside the runner image the probe:

1. checks Wine, Xvfb (starting it on `:99` when absent), `xdotool`,
   ImageMagick, `monkeydo`, `monkeyc`, the simulator and a JRE;
1. creates a throwaway `WINEPREFIX` (`/tmp/openhab-garmin-spike-prefix` by
   default) and initialises it with `wineboot --init`;
1. compiles `openhabgarmin.prg` for each device with the Linux `monkeyc`;
1. starts `simulator.exe` under Wine, waits for its window and records the
   startup time;
1. launches the PRG with `monkeydo`, captures the console with receive
   timestamps and waits for the first `E2E:VIEW`;
1. captures the simulator window and compares its dimensions with the device
   matrix (watch face only, or window chrome included);
1. injects three Down keys on the button device and three taps on the touch
   device, and counts how many change the screen;
1. drives the native `Simulation` and `Connection` menus, saves a screenshot
   before, during and after each toggle, and checks for `E2E:CONNECTION` mode
   changes; also records what `simulator.ini` allows setting;
1. restarts the simulator with the requested language and requires an
   `E2E:LANGUAGE;code=<language>` line;
1. writes application properties for `pollingInterval=0` and `1000`, measures
   the intervals between `E2E:SITEMAP` lines and runs the escalation window
   twice;
1. captures the window with an overlapping `xmessage` and after moving focus
   away.

Every step records a check with `met`, `not-met` or `skipped` plus evidence.
A capability that cannot be attempted is **skipped**, never passed.

## Verdict

The six pass criteria are quoted from #370:

| # | Criterion |
| --- | --- |
| pc1 | Simulator starts and renders the app under Wine without a Windows licence. |
| pc2 | `monkeydo` launches the app and streams `E2E:` trace lines. |
| pc3 | Button and touch input reliably reach the app. |
| pc4 | Device/menu state can be set and read back, including connection mode and language. |
| pc5 | Screenshots capture the watch face only. |
| pc6 | Timing is tight enough for polling and escalation tests. |

Decision rule:

- **pass** -- every criterion is met. Recommendation: implement
  `WineSimulatorDriver` against `tests/runner/e2e/sim/base.py` and continue on
  Linux only.
- **partial** -- pc1 and pc2 work but at least one capability does not (or was
  not evaluable). Recommendation: record the failing capability, waive the
  affected scenarios explicitly in the coverage matrix, and continue with the
  remaining tiers.
- **fail** -- the simulator does not start or render, or `monkeydo` does not
  stream trace lines. Recommendation: document the failure, then fall back to a
  Windows guest with the same driver contract.

## Outputs

Everything is written under `tests/artifacts/spike/` (git-ignored):

| File | Contents |
| --- | --- |
| `verdict.json` | criteria, every check, evidence paths, measurements, recommendation |
| `verdict.md` | the same as a table, console excerpts, limitations and artifact list |
| `probe.json` | raw in-container observations, written after every check |
| `plan.json` | host, SDK and device details for this run |
| `layout.json` | discovered `simulator.ini`, app-data and settings paths |
| `logs/` | `docker-build.log`, `docker-run.log`, SDK/Wine/console logs per run |
| `shots/` | screenshots for launch, input, menus, language and occlusion |
| `build/` | the debug PRGs used by the run |

The JSON paths are relative to the artifact directory, so a verdict can be
attached to the issue without rewriting links.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | the spike completed and a verdict was written, including `partial`/`fail` |
| `1` | the probe crashed, timed out or produced no `probe.json` |
| `2` | a prerequisite is missing or invalid; one message names every missing item |

A `partial` or `fail` verdict is a successful experiment, not an error. Only
exit code `2` needs an environment fix.

## Manual verification (operator)

The unit tests cover the pure logic, not the experiment. After a run, check:

1. `verdict.md` names each criterion with `met`/`not met`/`skipped` and at least
   one evidence path; a criterion without evidence is not met.
1. Open `shots/<device>-launch.png`. It must show the app, not a blank Wine
   window. If it includes the simulator chrome, `pc5` is `not met` and the
   driver should use the simulator's own Save Screenshot action.
1. Read `logs/console-<device>.log`. `E2E:VIEW`, `E2E:CONNECTION`,
   `E2E:SITEMAP` and `E2E:LANGUAGE` lines must be present for the checks that
   claim them.
1. For `pc3`, open `shots/<device>-input-*.png` in order and confirm the screen
   changes; the verdict records how many of three events changed it.
1. For `pc4`, inspect the `menu-*-before`, `menu-*-open` and `menu-*-after`
   screenshots. If the assumed menu item order is wrong, the verdict says the
   toggle was not verified and the screenshots show what opened instead.
1. For `pc6`, compare `requested_ms_0`/`requested_ms_1000` with the measured
   median and max in `verdict.json`.
1. Re-run once to confirm the verdict is stable. A capability that works once
   but not twice is not reliable enough for the driver.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `error: missing prerequisites: Connect IQ SDK (...)` | `CIQ_SDK_HOST_PATH`/`--sdk` points at the wrong root or the wrong archives are merged |
| `error: missing prerequisites: docker daemon (...)` | Docker is installed but not running, or this user cannot use it |
| `error: missing prerequisites: Java runtime ...` | the runner image was built without the headless JRE; rebuild without `--skip-build` |
| `the probe produced no probe.json` | the container failed before preflight; read `logs/probe.log` |
| `wine.prefix` is `not met` | `wineboot --init` failed; usually a missing `wine32:i386` |
| `simulator.launches` is `not met` | no window appeared; read `logs/simulator.log` and the Wine debug output |
| `screenshot.watchface_only` is `not met` | `import` captured the window including chrome; the dimensions show the size |
| `menu.simulation` is `not met` | the native menu was not scriptable with `alt+s`/arrow keys, or the item order differs; the screenshots show it |
| `language.switch` is `skipped` | no language key was found in `simulator.ini`; inspect `layout.json` |
| `timing.poll_interval` is `skipped` | no writable application-properties file was found; inspect `layout.json` |

## Relationship to the harness

- The runner image, SDK layout and trace grammar are the harness's; see
  [../README.md](../README.md), [../docs/sdk.md](../docs/sdk.md) and
  [../docs/simulator-driver.md](../docs/simulator-driver.md).
- The spike adds a headless JRE to the runner image because `monkeyc` is a Java
  launcher. That is a prerequisite of the harness's own `build` command, not a
  spike-only dependency.
- `WineSimulatorDriver` stays a skeleton in this change. The verdict decides
  whether implementing it is worthwhile, and the evidence shows which driver
  methods are trivial (for example `prepare`'s prefix creation) and which are
  not (native menus and watch-face-only capture).
- Unit tests for the pure logic live in
  `tests/runner/tests/test_spike.py` and run in the existing `harness` job. The
  experiment itself is never scheduled in CI.
