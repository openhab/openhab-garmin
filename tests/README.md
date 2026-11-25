# End-to-end test harness

The complete, portable test unit for the Garmin app lives here: one Docker Compose
stack, fixtures, scenarios and a Python runner. It is designed to run unchanged on
any Linux host (including CI) and to be deployed by an environment-specific
infrastructure repository, not by this one.

```text
app (Wine simulator in `runner`)  ->  shim  ->  openHAB
```

The shim sits between the app and openHAB so every request the app makes is
recorded and can be replaced with a scripted fault.

Every published port is bound to the loopback address defined in
[`config/network.yaml`](config/network.yaml), so the stack is not reachable from
the network. External access, including TLS termination, is the operator's
reverse proxy: no certificate, private key or CA material is stored in this
repository or baked into the environment.

## Status

This directory is the foundation tracked by
[#365](https://github.com/openhab/openhab-garmin/issues/365), part of
[#140](https://github.com/openhab/openhab-garmin/issues/140).

| Piece | State |
| --- | --- |
| Compose stack, fixtures, scenarios, runner, shim | complete and statically validated |
| `pytest` unit tests for scenario loading, shim grammar, trace parsing, Compose validation, SDK probing, dry-run engine | complete, run without Docker |
| openHAB 5.2.5 (stable) and 5.3.0 milestone (dev) image digests | pinned in `config/openhab.version` |
| Device matrix covering all 17 build variants from `monkey.jungle` and `manifest.xml` | approved in [#366](https://github.com/openhab/openhab-garmin/issues/366); enforced by `runner/tests/test_variants.py` |
| Protocol-tier scenarios (shim trace + `E2E:` trace events) | ready; need the debug trace build from [#364](https://github.com/openhab/openhab-garmin/issues/364) |
| Connect IQ unit-test tier (`source/test`, run by `tests/unit/run`) | pilot: 18 tests over sitemap parsing, error classification and element parameters. Compiled by the existing device matrix (it already passes `--unit-test`); verified on the guest (18/18 PASS on fenix7, about two minutes). See [docs/unit-tests.md](docs/unit-tests.md). |
| Wine simulator driver | implemented against the contract, with the compiled-properties model and the guest-measured start order; the proof is a driver run on the guest with `--prg` (see [docs/simulator-driver.md](docs/simulator-driver.md)). Its one environmental gap is input, and that question is closed by measurement: UI-level assertions are not available on this host (see [docs/simulator-input.md](docs/simulator-input.md)) |
| Visual-truth design (drive-and-look) | design note: a Windows host is rejected on licence and capacity; the feasible no-input route is off-screen rendered assertions (draw smoke plus real font metrics), because SDK 9.3.0 has no pixel readback; Xorg/uinput is a bounded spike. See [docs/visual-truth.md](docs/visual-truth.md). |
| Wine feasibility spike | ready to run on an approved Linux host with Docker and the SDK (see [spike/README.md](spike/README.md), issue [#370](https://github.com/openhab/openhab-garmin/issues/370)) |
| #140 scope coverage map | checked in as `config/scope.yaml` and `docs/coverage.md`; enforced by `python -m e2e coverage --check`. The glance entries are **blocked** by [#396](https://github.com/openhab/openhab-garmin/issues/396) and E2E builds exclude the glance by default (`--with-glance` re-enables it). |
| Stack health in every run | complete: restart counts, working set and OOM evidence are classified as `valid`/`invalid`/`unknown`; see [docs/diagnostics.md](docs/diagnostics.md) |
| Host preflight (`python -m e2e preflight`) | complete: Docker, Compose, SDK, ports, pins, layout and resources; the static groups run in CI |

Nothing here provisions a VM, changes infrastructure, or ships the Connect IQ SDK.

> **Device matrix.** `config/devices.yaml` is the approved representative
> subset: one device per build-variant combination, 17 devices in total, all
> running the cheap `protocol` tier and six of them also running `ui`. The
> variant table, the waiver rules and the re-evaluation rule are documented in
> [docs/devices.md](docs/devices.md); `python -m e2e variants` prints the current
> mapping and exits non-zero when a combination has no device.

## Prerequisites

- Linux host with Docker and Docker Compose v2 (or a VM/WSL2 guest; macOS works for
  the stack, but the simulator needs Wine and is only supported on Linux).
- The Connect IQ SDK **mounted, never committed**: `CIQ_SDK_HOST_PATH` points at it.
  Layout and licence rules: [docs/sdk.md](docs/sdk.md). The runner copies it to a
  writable path at container start and links its device definitions where
  `monkeyc` looks; the mount itself is never written.
- Python 3.10+ on the host for the unit tests and the CLI (`tests/runner`,
  declared as `requires-python` in `runner/pyproject.toml`; CI uses 3.12).

## Host preflight

Run this on the host, from `tests/runner`, before the first `docker compose up`:

```bash
python -m e2e preflight
```

It is read-only: it starts nothing, stops nothing and writes nothing, and each
check reports one `PASS`/`FAIL`/`WARN`/`SKIP` line with a short reason. The exit
code is non-zero when a hard check fails, so provisioning can stop before the
confusing failure that would otherwise appear in the middle of a run.

| Group | What it checks |
| --- | --- |
| `docker` | `docker` is on `PATH`, the daemon answers with its server version, Docker Compose v2 is at least 2.24 (`compose.host.yaml` uses `!reset`), and the base plus host overlay parse with `docker compose config`. |
| `compose` | The base, dev and host overlays parse through the static loader and the loopback wiring matches `config/network.yaml`. No Docker needed. |
| `sdk` | `CIQ_SDK_HOST_PATH` (or `CIQ_SDK_ROOT` / `CIQ_SDK_PATH`) points at the SDK, `linux/bin/monkeyc` and `linux/bin/monkeydo` exist for the Linux side, `windows/bin/simulator.exe` exists for the Wine side, and `version.txt` matches `config/sdk.version`. |
| `pins` | `compose/.env` pins the images in `config/openhab.version`; this reuses the `validate-compose --check-pins` validation rather than duplicating it. |
| `ports` | The loopback ports in `config/network.yaml` can be bound on `127.0.0.1`. A listener is named with its process when `ss` can determine it. |
| `layout` | The fixtures, scenarios, config, Compose files and `monkey.jungle` the runner mounts are present, and the artifacts directory is writable. |
| `resources` | RAM, CPU count and free disk are reported against the documented minimums. |

Resource minimums, documented here because the preflight check enforces them:

| Resource | Minimum (hard failure) | Recommended (warning only) |
| --- | --- | --- |
| RAM | 2 GiB | 4 GiB |
| CPU | 1 | 2 |
| Free disk | 5 GiB | 20 GiB |

Skip the groups a workstation cannot satisfy:

```bash
# no Docker, no SDK, no free loopback ports: static checks only
python -m e2e preflight --skip docker,sdk,ports

# machine-readable, including the skipped checks
python -m e2e preflight --json
```

`--skip` accepts repeated flags and comma-separated groups. A skipped group is
reported as `SKIP`, never as a pass. `--json` emits `ok`, `exit_code`,
`skipped_groups`, `summary` and one `checks` entry per check (`id`, `group`,
`status`, `reason`, and `details` where available), so the schema is stable for
automation.

CI runs `python -m e2e preflight --skip docker,sdk,ports` after
`pin-images --write`, so the static groups (`compose`, `pins`, `layout` and
`resources`) cannot rot.

### What a failure means

- `docker.*`: fix the host or the Compose installation before `compose up`.
- `sdk.layout`: the variable is unset, the layout is incomplete, or the version
  differs from the pin; set `CIQ_SDK_HOST_PATH` as described in
  [docs/sdk.md](docs/sdk.md).
- `pins.images`: `compose/.env` is missing or has drifted; run
  `python -m e2e pin-images --write`.
- `ports.loopback`: something already owns a stack port; the reason names the
  process when `ss` can see it.
- `layout.*`: the checkout is incomplete or the artifacts path is not writable.
- `resources.host`: a `FAIL` is below the documented minimum, a `WARN` is below
  the recommendation.

Preflight cannot prove that the stack runs. It never launches openHAB, the
shim, the runner or Wine, so the simulator-under-Wine feasibility check stays in
[spike/README.md](spike/README.md) and is not duplicated here.

## Continuous integration

`.github/workflows/e2e-harness.yaml` runs the checks that need neither Docker nor
the SDK on every pull request and `main` push that touches `tests/`,
`manifest.xml`, `monkey.jungle` or the workflow itself:

1. `python -m pytest` in `tests/runner`
1. `python -m e2e pin-images --write` then `python -m e2e validate-compose --check-pins`
1. `python -m e2e preflight --skip docker,sdk,ports` (static groups only)
1. `python -m e2e contract-test --driver null`
1. `python -m e2e run --all --dry-run`
1. `python -m e2e coverage --check` (every #140 scope id has a scenario or a
   waiver, no scenario names an unknown id, and `docs/coverage.md` is current)

The job installs the package **editable** (`pip install --editable tests/runner`)
because `e2e/paths.py` derives `tests/` and the repository root from the package
location; a copy in `site-packages` would look for the fixtures outside the
checkout. It also runs `python -m e2e pin-images --write` before
`validate-compose --check-pins`, because `--check-pins` compares the git-ignored
`tests/compose/.env` with `tests/config/openhab.version`, and the committed
digests make that write offline.

The Connect IQ unit-test tier ([docs/unit-tests.md](docs/unit-tests.md)) has no
job in this workflow:

- its **compile gate is the device matrix** in `ci-build.yaml`, which already
  passes `--unit-test` for every device, so the tests in `source/test` are
  typechecked on every pull request;
- **running** it needs the simulator under Wine and the operator's SDK, which a
  GitHub-hosted runner does not have. Run `tests/unit/run` on the E2E guest,
  next to the E2E suite.

### openHAB fixture job

The static job cannot prove that the fixtures parse: a healthy openHAB whose
models were all rejected still answers HTTP 200 and returns `[]` from
`/rest/items` and `/rest/sitemaps` (issue #394). The same workflow therefore has
a second job, `fixtures`, with a `stable` and a `dev` matrix leg:

1. derive `tests/compose/.env` from the committed pins;
1. start the pinned openHAB image and wait for its healthcheck;
1. read `/openhab/userdata/logs/openhab.log` from the container;
1. run `python -m e2e verify-fixtures --profile <profile> --log openhab.log
   --strict --sizes --dump-dir ../compose/sitemaps --wait 180`.

The job is per pull request, not scheduled. The workflow is already
path-filtered to `tests/`, `manifest.xml`, `monkey.jungle` and the workflow
itself, so app-only pull requests pay nothing, while a fixture change is exactly
when the cost is justified. A cold leg is one image pull plus a 60-90 s start;
the two legs run in parallel. The dev leg is required, not optional: the stable
pin cannot parse `confirmCmd`, and the only such fixture lives in the dev
overlay.

## Quick start

```bash
cd tests/compose
cp .env.example .env
export CIQ_SDK_HOST_PATH=/opt/garmin/connectiq-sdk-9.3.0   # or edit .env

cd ../runner
python -m pip install -r requirements.txt
python -m pip install --editable .    # optional: `python -m e2e` from anywhere
python -m e2e pin-images --write      # writes @sha256 pins into ../compose/.env
python -m e2e network-env             # ports in .env.example match config/network.yaml
python -m e2e validate-compose        # no Docker needed

cd ../compose
docker compose up -d                  # init staging, openhab-stable, shim, runner
docker compose ps                     # wait for the healthchecks

cd ../runner
python -m e2e status                  # SDK, openHAB, shim, pins
python -m e2e setup                   # fixture sets over REST, then the sitemap
python -m e2e verify-fixtures         # the fixture guard on its own
```

`e2e run` runs the same guard before every scenario, so a scenario can no longer
reach an openHAB whose models were rejected; `setup` and `verify-fixtures` expose
it for a manual check and for CI.

`docker compose up -d` starts a one-shot `openhab-stable-init` (or
`openhab-dev-init`) first: it copies `tests/fixtures/openhab/conf/` into the
conf volume and seeds it with the image's own `dist/conf` skeleton, and openHAB
starts only after it exits successfully. The fixtures are never mounted
read-only underneath `/openhab`, because the image's entrypoint runs
`chown -R openhab:openhab /openhab` under `set -e` and aborts on a read-only
file system. The checkout is never written by the stack.

Re-asserting the fixture state after a run:

```bash
docker compose -f ../compose/compose.yaml run --rm --no-deps openhab-stable-init
docker compose -f ../compose/compose.yaml restart openhab-stable
```

`docker compose down -v` removes the conf, userdata and artifacts volumes; the
next `up` seeds them from scratch. Artifacts live in the `e2e-artifacts` volume,
mounted at `/artifacts` in the runner (never inside the read-only `/work`
checkout); reports, JUnit XML, screenshots and build output are all written
there.

Then run a scenario:

```bash
docker compose -f ../compose/compose.yaml exec runner \
  python -m e2e run smoke/launch-and-poll
```

On a workstation without the stack (no Docker, no SDK), the wiring can still be
exercised:

```bash
cd tests/runner
python -m e2e run smoke/launch-and-poll --dry-run
```

A dry run uses the null driver, skips every network-dependent step, and reports
those steps as **skipped**. It never reports them as passed.

### Build profiles: the app is a plain watch app for tests

The app is a widget, and a widget shown on the glance page starts in **glance
mode** (32 KB budget) until it is selected; this simulator does not select it,
so `getInitialView()` never runs. On top of that, the real glance crashes the
simulator on its first draw (`Invalid Font Specified`, tracked as
[#396](https://github.com/openhab/openhab-garmin/issues/396)).

Every `run` and `build` therefore uses the **`watch-app`** profile unless told
otherwise: the generated jungle is rebased onto a copy of `source/` in which
`GlanceSitemapView` is replaced by an empty stub, and `project.manifest` points
at a generated copy of `manifest.xml` declaring `type="watch-app"`. That is the
right app type for a direct launch. It does not yet produce one: the app's own
glance latch (`AppLifecycle.setGlance()` set by `getGlanceView()`) keeps the
process on the glance page under the simulator, which is the app-side cause
tracked in #396. The checkout is never modified.

```text
profile: watch-app: the app is declared as a plain watch app in a generated manifest and its glance view is stubbed, so the simulator launches getInitialView() directly; widget and glance modes are NOT covered (issue #396)
```

`--without-glance` keeps the widget manifest (the app may still start in glance
mode), and `--with-glance` compiles the real glance view. Re-include the real
glance on hardware, or once the issue is resolved:

```bash
python -m e2e run smoke/launch-and-poll --with-glance
python -m e2e build --device fenix847mm --with-glance
```

`monkeyc -x glance` is not an alternative: `-x` is rejected together with `-f`,
and `OHApp` (the manifest entry) plus shared classes are annotated `(:glance)`
too, so excluding the annotation removes the entry point and fails the build.
See [docs/simulator-driver.md](docs/simulator-driver.md#glance-the-build-profiles).

## CLI

| Command | Purpose |
| --- | --- |
| `setup` | validate Compose, SDK, openHAB readiness, the expected fixture sets and the fixture byte budget, then resolve the fixture sitemap |
| `verify-fixtures` | assert `/rest/items` and `/rest/sitemaps` contain the expected sets; `--strict` also fails on a `DSL model ... has errors` line, `--log` points at the openHAB log, `--sizes` fetches every sitemap and enforces the byte budget, `--dump-dir` writes the payloads |
| `run` | run scenario names, `--suite`, or `--all`; writes JSON, JUnit XML, Markdown and the stack-health artifact |
| `reset` | clear the shim's script and request trace (optionally reset item states) |
| `status` | health of every component, plus pin problems |
| `list-scenarios` | what `run --all` would execute |
| `variants` | the resolved build-variant table and device coverage; exits non-zero on a gap |
| `coverage` | map #140 scope entries to scenarios; `--write` regenerates `docs/coverage.md`, `--check` fails on a gap or a stale document |
| `contract-test` | exercise a simulator driver against the driver contract; `--prg` adds the cases that need a launched application and its name selects the device (a disagreeing `--device` is refused). `--key BUTTON=KEYSYM` tries a different input key; `--shim-url` adds the shim requests each measured press caused |
| `language-scan` | measure the simulator's numeric `Language` index per language code on the guest (`--prg`, `--indices 0-24`, `--write`) and record it in `tests/config/simulator-languages.json`; this is what makes a non-English scenario runnable |
| `preflight` | read-only host check before the stack starts; `--skip` a group and `--json` for automation |
| `validate-compose` | static Compose validation; `--check-pins` also requires digests |
| `network-env` | render or verify the generated loopback network block in `compose/.env.example` |
| `pin-images` | write or verify image digests (`--refresh` re-resolves from Docker Hub) |
| `sdk-check` | validate the mounted SDK without starting anything |
| `link-devices` | copy the SDK's device definitions to `$HOME/.Garmin/ConnectIQ/Devices`, where `monkeyc` looks for them; the runner entrypoint runs it at container start |
| `health-snapshot` | record the stack's restart counts and memory before/after a run; `--phase after --artifacts` classifies the finished run |
| `build` | compile the PRG for one or more devices with a jungle generated for that device, compiling the `--settings` fixture's property defaults in (the build asserts the values `monkeyc` emitted; `--jungle` uses a supplied one instead and cannot carry the override; `--developer-key` overrides the throwaway key generated per build). `--watch-app` (default), `--without-glance` and `--with-glance` select the build profile (see [Build profiles](#build-profiles-the-app-is-a-plain-watch-app-for-tests)) |

Exit codes: `0` success, `1` scenario failures, `2` configuration or environment
error, `3` invalid environment (a container restarted or was killed during the
run, so the results are not attributable to the app).

`run` flags worth knowing:

- `--device <id>` overrides the scenario's device (see `config/devices.yaml`).
- `--driver wine|null`; `--dry-run` implies `null`.
- `--key BUTTON=KEYSYM` (repeatable) sends that X keysym for a button instead of
  the driver's default path, e.g. `--key ENTER=Return`.
- `--update-goldens` writes new golden images instead of comparing; review the
  screenshots before committing them. It needs write access to `tests/goldens`,
  which the read-only checkout mount does not have, so capture goldens from a
  checkout on the host, not inside the runner container.
- `--developer-key <file>` signs the build with a key you provide; without it a
  throwaway RSA key is generated per invocation under `<artifacts>/keys/` and
  deleted afterwards. It is never committed and never reused.
- `--skip-events` skips trace-event assertions (for builds without `(:debug)`).
- `--prg <file>` reuses a build instead of compiling.
- `--jungle <file>` compiles a supplied jungle instead of the device-scoped one
  generated from `monkey.jungle`; it needs an SDK that defines every device the
  file names (see [docs/sdk.md](docs/sdk.md#device-scoped-jungle)).

## Stack health

Every run classifies the environment alongside the assertions, because a
scenario that fails on a stack which restarted underneath it says nothing about
the app. The block is in `report.json`, `health.json`, `junit.xml` (a zero-test
`environment` testsuite) and the `## Environment health` section of
`summary.md`, and a failed scenario's bundle carries it as `environment.json`.

| Verdict | Meaning | Exit code |
| --- | --- | --- |
| `valid` | no container restarted, was recreated, was killed or stopped during the run window | `0`, `1` on scenario failures |
| `invalid` | the stack changed during the window; the environment, not the app, is suspect | `3` |
| `unknown` | the window was not measured (dry run, or the runner has no Docker access) | `0`, `1` on scenario failures |

An invalid run is not converted into a scenario failure, and no heuristic
promotes a suspicious-looking environment (a pre-existing restart count, an
unhealthy healthcheck, no memory limit) into a defect. The signals, the exact
Docker commands, the classification rules and the triage procedure are in
[docs/diagnostics.md](docs/diagnostics.md).

The runner container has no Docker socket, so a run executed inside the stack
records `unknown` and the window is captured on the host instead:

```bash
cd tests/runner
python -m e2e health-snapshot --phase before --output health-window.json
docker compose -f ../compose/compose.yaml exec runner python -m e2e run --all
docker compose -f ../compose/compose.yaml cp runner:/artifacts ./artifacts
python -m e2e health-snapshot --phase after \
  --output health-window.json --artifacts ./artifacts
```

The last command re-renders the run's artifacts with the real verdict and exits
`3` when the run is invalid. Where Docker is reachable (a checkout on a Docker
host), `python -m e2e run` records the window directly.

## Wine spike (#370)

`spike/` is a separate, opt-in experiment, not part of the harness run. It
builds the same `runner` image, starts the simulator under Xvfb, launches the
PRG with `monkeydo` on one button and one touch device, drives input and the
`Simulation`/`Connection` menus, switches the language, measures polling and the
escalation window, and writes a per-criterion verdict.

```bash
cd tests
CIQ_SDK_HOST_PATH=/opt/garmin/connectiq-sdk-9.3.0 python -m spike run
```

The result is `tests/artifacts/spike/verdict.json` and `verdict.md`; both mark
each #370 criterion `met`/`not-met`/`skipped` with evidence. The spike is never
scheduled in CI; only its pure logic is covered by
`runner/tests/test_spike.py`. Layout, flags, outputs and the decision rule are in
[spike/README.md](spike/README.md).

## Scenarios

| Suite | Scenario | Tier | What it covers |
| --- | --- | --- | --- |
| smoke | `launch-and-poll` | ui | app starts, sitemap poll is proxied and answered, homepage renders |
| smoke | `toggle-switch` | ui | selecting a toggle sends exactly one `POST /rest/items/E2E_Toggle` with `ON` |
| smoke | `nested-text` | ui | nested `Text` pages open; polling continues while a subpage is shown |
| regression | `http-503-recovery` | protocol | three 503s stay temporary; the app recovers without a restart |
| regression | `empty-response` | protocol | empty 200 bodies surface as a temporary error |
| regression | `malformed-json` | protocol | truncated JSON is critical, polling continues, a valid response recovers |
| regression | `unsupported-widget` | ui | `Default` on a `Color` item shows an error and sends nothing |
| regression | `command-confirmation` | ui | on the dev profile, cancelling a confirmed command sends no request |
| regression | `dev-profile-command-confirmation` | ui | on the dev profile the confirmation parameter is present and nothing is sent before confirming |
| regression | `command-error-temporary` | ui | a failed command is a temporary toast and polling continues |
| regression | `critical-error-recovery` | protocol | HTTP 404 shows the critical view and a valid poll recovers it |
| regression | `post-command-hold` | ui | one command is sent and the 2 s hold window does not disturb polling |
| regression | `webhook-command-path` | ui | with the REST API disabled the command uses the webhook path |
| extended | `escalation-window` | protocol | persistent failure escalates to critical, then recovers |
| extended | `openhab-restart` | protocol | an openHAB outage window stays temporary and the app returns by itself |
| extended | `oversized-response` | protocol | 2 MiB body is reported as a critical error |
| extended | `slow-response` | protocol | slow responses are applied without an error view |
| extended | `connection-reset` | protocol | dropped connections are temporary errors |
| extended | `dynamic-visibility` | ui | `visibility` reacts to a state change while the app is open |
| extended | `limits-near-limit` | ui | a sitemap just below the simulator JSON limit renders normally |
| extended | `limits-over-limit` | ui | a sitemap above the simulator JSON limit shows the `S:-402` error view |
| extended | `wifi-mode` | protocol | Wi-Fi mode renders the cached sitemap and does not poll |
| extended | `offline-mode` | protocol | offline mode sends nothing |
| extended | `localisation-sitemap` | ui | homepage, control labels and connection indicator in all six languages |
| extended | `localisation-settings-menu` | ui | every settings entry, including the two-line Wi-Fi item, in all six languages |
| extended | `localisation-glyphs` | ui | accented strings on normal screens, captured for glyph review |
| extended | `localisation-toast-empty-page` | ui | translated empty-page toast in all six languages |
| extended | `localisation-toast-offline-command` | ui | offline-command toast, with no command sent |
| extended | `localisation-error-config-url` | ui | longest error string, missing URL, on the narrowest device |
| extended | `localisation-error-config-url-round` | ui | the same error on a round screen |
| extended | `localisation-error-memory-class` | ui | oversized-response critical error view |
| extended | `localisation-error-multiline` | ui | multi-line error detail in the full-screen error view |
| extended | `localisation-error-long-value` | ui | long dynamic value wraps in an error message |

The `localisation-*` scenarios are one file each but run once per shipped
language (`eng`, `deu`, `fre`, `ita`, `spa`, `pol`), so the table above is 60
simulator runs. Coverage, the device rationale and the machine-asserted versus
review-only split are documented in [docs/localisation.md](docs/localisation.md).

Every scenario also declares the issue #140 scope entries it covers with a
`covers` list, and `manual_checks` for anything it exercises but cannot assert.
The generated [docs/coverage.md](docs/coverage.md) lists, per scope id, the
scenarios with their tier and device, or an explicit waiver with a reason;
`python -m e2e coverage --check` fails when an entry has neither and when a
scenario names an unknown id.

`protocol` scenarios assert on the shim trace and on `E2E:` trace events and need
no screenshots. `ui` scenarios navigate the app and compare screenshots with
reviewed goldens.

Scenarios declare a device or `any` for the matrix default. The representative
subset, its variant coverage and the per-device tiers are in
[docs/devices.md](docs/devices.md). Run `python -m e2e variants` to see which
device covers each build variant; the coverage test fails when a variant from
`monkey.jungle` has no device.

Most scenarios run on the matrix default device. The localisation scenarios
select the narrowest `ui` device (`edge540`) and, for the round-screen error
view, `fenix7` explicitly; the rationale is in
[docs/localisation.md](docs/localisation.md).

Scenario authoring, the full YAML schema and the trace-line format are documented
in [docs/scenarios.md](docs/scenarios.md).

### Scenario order

`wifi-mode` and `offline-mode` render a _cached_ sitemap, so they must run after a
BLE scenario has populated the device cache. Any `smoke/` scenario does that.
The split `garmin_e2e` payload estimates 24,944 bytes, below the app's 81,920-byte
storage-cache limit, so it remains cacheable; the network limit (16 KiB) is the
tighter constraint. See [docs/fixture-limits.md](docs/fixture-limits.md).

## Fixtures

```text
fixtures/openhab/expected.yaml                     expected sets, byte budget and limit wiring
fixtures/openhab/generate_size_fixtures.py         regenerates the two limit sitemaps
fixtures/openhab/conf/items/garmin_e2e.items       every supported item type + one unsupported
fixtures/openhab/conf/sitemaps/garmin_e2e.sitemap  elements, nesting, ranges, icons, empty page
fixtures/openhab/conf/sitemaps/garmin_commands.sitemap  toggle, mappings, rollershutter, player, gates
fixtures/openhab/conf/sitemaps/garmin_dynamic.sitemap  group, colors, visibility
fixtures/openhab/conf/sitemaps/garmin_unsupported.sitemap  the "no supported widget" path
fixtures/openhab/conf/sitemaps/garmin_localisation.sitemap  stable localisation menu
fixtures/openhab/conf/sitemaps/garmin_localisation_long.sitemap  long-dynamic-value error path
fixtures/openhab/conf/sitemaps/garmin_limit_near.sitemap  just below the simulator JSON limit
fixtures/openhab/conf/sitemaps/garmin_limit_over.sitemap  above the simulator JSON limit
fixtures/openhab/conf-dev/sitemaps/garmin_confirm.sitemap  dev-only `confirmCmd` case
fixtures/app/settings/*.yaml                       Connect IQ property sets (`${E2E_SHIM_URL}`)
```

The fixture set started from the sitemap posted in
[#140](https://github.com/openhab/openhab-garmin/issues/140#issuecomment-...) and
covers `Frame`, `Default`, `Text`, `Group`, `Switch`, `Selection`, `Setpoint`,
`Slider` (including `releaseOnly`), nesting, `mappings`, `labelcolor`/`valuecolor`,
`visibility`, `icon`/`staticIcon`, empty pages and the full state/command matrix
(toggle, generic switch, rollershutter, player). It is split into small
sitemaps because the Connect IQ simulator rejects a JSON response above
**16,384 bytes** with `-402`; the old single `garmin_e2e` payload was 26,609
bytes and could never reach the app on the pinned simulator. The measured
boundary, the simulator-versus-hardware caveat and the full size table are in
[docs/fixture-limits.md](docs/fixture-limits.md).

### Stable and dev fixture trees

`fixtures/openhab/conf` must parse on the **stable** pin (openHAB 5.2.5). One
rejected element makes openHAB ignore the whole model, so a construct the stable
image does not understand makes every other scenario in that file unexecutable.
`fixtures/openhab/conf-dev` is an overlay that `openhab-dev-init` copies on top
of the common tree for the dev profile only.

That split exists because command confirmation was added in openHAB core 5.3:
the stable pin has neither the `confirmCmd` sitemap parameter nor the
`commandConfirmMessage` REST field. Both confirmation scenarios therefore use
`garmin_confirm.sitemap` on the dev profile. The rest of the fixture uses valid
DSL equivalents: `staticIcon=<icon>` (not `staticIcon=true`) marks the icon
static, and an empty page is a `Frame` without a block (openHAB serializes it as
`"widgets": []`, which the app turns into the empty-page toast).

### Fixture guard

`fixtures/openhab/expected.yaml` lists every item and, per profile, every
sitemap. `e2e verify-fixtures`, `e2e setup` and every non-dry scenario compare it
with `GET /rest/items` and `GET /rest/sitemaps`, retry for a short cold-start
window, and fail with the openHAB `DSL model ... has errors` lines when the sets
do not match. The guard derives the declared names from the DSL files and fails
when the manifest has drifted, so adding a fixture without listing it is caught
before openHAB is involved.

The manifest also carries `max_sitemap_bytes` (12,288) and the near/over limit
wiring. `e2e verify-fixtures --sizes` (and `e2e setup`) fetches every sitemap,
prints its REST body size, fails a normal sitemap above the budget, and asserts
that `garmin_limit_near` is at or below the measured 16,384-byte simulator limit
while `garmin_limit_over` is above it. The CI `fixtures` job runs this check and
uploads the payloads as artifacts. See
[docs/fixture-limits.md](docs/fixture-limits.md).

The runner reads the manifest from `/e2e-fixtures`, which Compose mounts from
`../fixtures` relative to `tests/compose` (the same source the staging service
copies). If `E2E_REPO_ROOT` points the runner's `/work` mount at a different
checkout, the two trees differ and the guard fails with an explicit message. The
stack must be started from the checkout under test; `tests/compose/.env` is read
from the Compose project directory, and `openhab-<profile>-init` stages
`../fixtures` relative to the Compose file.

Fixture files are the source of truth:

- item **structure** lives in the `.items`/`.sitemap` files;
- item **state** is set over REST (`PUT /rest/items/{name}/state`), which is what
  `openhab.items` in a scenario does;
- **runtime structural changes** (adding an element while the app runs) belong to
  REST UI components (`/rest/ui/components/ui:sitemap`), not to these files.

The fixture directories (the common `conf` tree and, for the dev profile, the
`conf-dev` overlay) are mounted **read-only** into a writable conf volume, so
the container can create its own files without ever writing into the checkout.

The app settings fixtures write `url_0: ${E2E_SHIM_URL}/`, not a literal host.
`E2E_SHIM_URL` is the same variable Compose passes to the runner, so the app
always addresses the shim the runner resets and inspects: `http://shim:8080` on
the bridge profile and `http://127.0.0.1:8081` on the host overlay. Outside a
stack it falls back to the loopback URL from `config/network.yaml`.

## Deployment profiles and networking

The stack has one deployment axis (which openHAB runs) and one networking axis
(whether the services use a bridge network or the host network). The defaults
are bridge + stable, which works on a developer machine.

| Combination | Command | Services address each other as |
| --- | --- | --- |
| bridge, stable (default) | `docker compose up -d` | Compose service aliases, container port 8080 |
| bridge, dev | `COMPOSE_PROFILES=dev docker compose -f compose.yaml -f compose.dev.yaml up -d` | `openhab-dev:8080`, `shim:8080` |
| host, stable (dedicated VM) | `docker compose -f compose.yaml -f compose.host.yaml up -d` | `http://127.0.0.1:<port>` |
| host, dev (dedicated VM) | `COMPOSE_PROFILES=dev docker compose -f compose.yaml -f compose.dev.yaml -f compose.host.yaml -f compose.host.dev.yaml up -d` | `http://127.0.0.1:<dev port>` |

All four files are statically validated; the host overlays are checked to be
hostname-free and loopback-only.

### The loopback rule

- `config/network.yaml` is the **only** place that defines the host and ports.
  It renders the `network` block in `compose/.env.example`
  (`python -m e2e network-env --write`), which Compose consumes as variables.
  `python -m pytest` and `validate-compose` fail if the block drifts.
- The bridge profile publishes `${E2E_LOOPBACK_HOST}:<port>` and never a
  wildcard address, so a developer can reach the stack at `127.0.0.1:8081` but
  the machine's LAN cannot.
- The host overlays set `network_mode: host` on every service, reset the
  inherited port publications with Compose's `!reset` tag (Docker Compose 2.24+),
  bind the shim with `SHIM_BIND=${E2E_LOOPBACK_HOST}`, and give openHAB
  `-Dorg.ops4j.pax.web.listening.address=${E2E_LOOPBACK_HOST}` because its
  default is `0.0.0.0`.

### External access is not the stack's concern

Nothing here terminates TLS or exposes a public listener. If the host must be
reachable from outside, the operator puts a reverse proxy (or an SSH tunnel) in
front of the loopback port. No certificate, private key or CA material is
referenced by this directory, and none should be added to it.

### Changing a port

```bash
# edit config/network.yaml
cd tests/runner
python -m e2e network-env --write      # refresh compose/.env.example
python -m pytest                       # fixtures and overlays must still agree
```

`python -m e2e network-env` (no `--write`) is the check-only form.

### Dev profile override

`dev` is opt-in and needs `compose.dev.yaml`, because the shim's upstream must
follow the profile. Using `--profile dev` without it starts a shim that points
at a service that is not running; `validate-compose` rejects that override
shape. On the host overlay, `compose.host.dev.yaml` is applied last so its
loopback URLs for `openhab-dev` win over both earlier files.

## Image pinning

`tests/config/openhab.version` holds the tags **and** the multi-architecture
digests:

| Role | Image | Why |
| --- | --- | --- |
| stable | `openhab/openhab:5.2.5-debian` | current stable release, the default target |
| dev | `openhab/openhab:5.3.0.M5-debian` | next milestone, opt-in; breaking changes appear here first |
| runner base | `ubuntu:24.04` | Wine/Xvfb/xdotool packages the runner needs |
| shim base | `python:3.12-slim-bookworm` | stdlib-only shim |

`python -m e2e pin-images --write` copies those pins into `tests/compose/.env`
(which is git-ignored because it also carries a machine-specific SDK path).
`--check` fails when a pin is missing or has drifted from `config/openhab.version`,
and `validate-compose --check-pins` combines both checks.

To bump a version: change the tag _and_ digest in `config/openhab.version` (or run
`pin-images --write --refresh` after editing the tag), record the resolution date
in the file's header, then run the smoke suite against both profiles.

The profile and networking matrix, the loopback rule and the port-change
procedure are documented in
[Deployment profiles and networking](#deployment-profiles-and-networking)
above.

## What was and was not executed

The harness has been reviewed on workstations without Docker or the SDK, so the
validation split is:

### Executed

The loopback-network change was validated on a workstation with neither Docker nor
the SDK:

```bash
cd tests/runner
python -m pytest -q                     # 395 passed
python -m e2e network-env               # network: ok
python -m e2e validate-compose          # compose: valid (bridge, dev, host, host-dev)
python -m e2e run --all --dry-run       # 81 scenarios, artifacts + JUnit written
python -m e2e list-scenarios            # 81 scenarios
python -m e2e coverage --check          # 65 scope entries, 36 covered, 29 waived, 0 empty
```

The Compose, image-pin, driver-contract and SDK checks are unchanged by this
change and are not re-run here; the `harness` CI job runs them on every pull
request.

Two checks used a synthetic SDK tree (a temporary directory with `version.txt`,
`linux/bin/monkeyc` and `windows/bin/simulator.exe`) to exercise the paths that
need an SDK without requiring one:

```bash
CIQ_SDK_ROOT=<synthetic tree> python -m e2e sdk-check           # exit 0, layout printed
CIQ_SDK_ROOT=<synthetic tree> python -m e2e contract-test --driver wine
# 3 pass (interface, events, stop), 11 not-implemented, exit 2 - the skeleton
# reports what is missing instead of pretending to run
```

### Stack health (#383)

The health classification was validated on the same workstation with no Docker
daemon, which is the constraint it was written for:

```bash
cd tests/runner
python -m pytest -q                     # 436 passed, including tests/test_health.py
python -m e2e run --all --dry-run       # 81 scenarios; every report has an unknown environment block
python -m e2e coverage --check          # unchanged
```

`tests/test_health.py` parses recorded `docker inspect` and `docker stats` output
from `tests/fixtures/health/`, drives the probe's command plan with a scripted
runner, and asserts each verdict and its rendering. No test starts a container.

### Live-run fixes (#367 follow-up)

The first run of the stack on a real host found five defects that no
workstation check could reach: the artifacts volume nested inside the read-only
`/work` mount, openHAB's `chown -R` aborting on read-only fixture mounts, a
missing `libharfbuzz` in the headless JRE, `monkeyc` writing into the read-only
SDK mount, and `e2e build` having no developer key. The fixes are verified
without Docker:

```bash
cd tests/runner
python -m pytest -q                            # 513 passed
python -m e2e pin-images --write
python -m e2e validate-compose --check-pins     # compose: valid
python -m e2e run --all --dry-run               # 81 scenarios, artifacts + JUnit written
python -m e2e coverage --check                  # 65 entries, 36 covered, 29 waived, 0 empty
```

`tests/test_compose.py` reconstructs both Compose failures as documents (a
volume nested in a read-only mount, a read-only mount under `/openhab`) and
asserts they are rejected; `tests/test_runner_image.py` pins the
Dockerfile/entrypoint contract; `tests/test_build.py` covers the throwaway key;
and `tests/test_sdk.py` covers device-definition discovery. No test starts a
container.

The remaining proof is a stack run on the dedicated host: it compiles the PRG,
starts openHAB and drives a scenario. Nothing here claims that has already
happened for this change.

### Not executed (no Docker daemon or SDK on the workstation)

- `docker compose config` and `docker compose build`.
- Starting openHAB and verifying the fixture sitemap renders in the app.
- Building the PRG with `monkeyc` and running a real scenario.
- The Wine driver: it needs Wine, the simulator and an X display, so only a run
  on the guest proves it. `contract-test --driver wine --prg <built.prg>` is
  that proof; see [docs/simulator-driver.md](docs/simulator-driver.md).

Run the first three before merging a change that touches `compose/`, `runner/Dockerfile`
or the fixtures.

## Manual verification (operator)

Do this once after the stack is up, and again after touching fixtures or the shim.

1. **Stack health** – `python -m e2e setup` in the runner container. Expected:
   `openHAB: ready`, `shim: ready`, `sitemap: 'garmin_e2e' resolves, N item
   reference(s) all defined`, `setup: ok`. A missing item is a fixture bug; the
   message names it.
1. **Fixture rendering (button device, Fenix 7 in the simulator)** – start the app,
   wait for the first poll. Expected: the glance shows `My Home`; the homepage lists
   `Group`, `Widgets`, `Generic Switch`, `Text`, `Default`, `Setpoint and Slider`,
   `Colors`, `Icons`, `Dynamic`, `Empty`, `Entrance Gates`. Open each frame and
   confirm no error view appears; `Empty` shows the “This page is empty” toast.
1. **Touch device (Venu 3 / Edge 1050)** – repeat step 2 by tapping instead of
   pressing. Expected: identical content and ordering; Edge devices show the
   Settings entry at the end of the homepage.
1. **Command path** – open `Widgets` → `Toggle Switch`, press ENTER. Expected:
   the shim trace gains exactly one `POST /rest/items/E2E_Toggle` with body `ON`
   (`python -m e2e reset` then re-check with `GET /__shim__/requests`).
1. **Fault handling** – install a fault script and confirm the documented error
   class:

   ```bash
   curl -X POST localhost:8081/__shim__/script -H 'Content-Type: application/json' \
     -d '{"version":1,"rules":[{"name":"503","match":{"path_prefix":"/rest/sitemaps/"},"response":{"status":503}}]}'
   ```

   Expected: a toast for a temporary error within one poll interval; after 10 s of
   continuous failure, the full-screen critical error view; the app recovers when
   `POST /__shim__/reset` clears the script.
1. **Empty and malformed bodies** – repeat step 5 with `"body": ""` and with
   `"body": "{\"homepage\": ["`. Expected: empty → temporary error; malformed →
   critical error view while polling continues.
1. **Edge cases** – `Empty` page toast (step 2), `visibility` toggling while the app
   is open (change `E2E_Visible` over REST and watch the `Dynamic` frame),
   `commandConfirmMessage` cancel on the **dev** profile (start it with
   `COMPOSE_PROFILES=dev docker compose -f compose.yaml -f compose.dev.yaml up -d`,
   then use `garmin_confirm.sitemap`; the stable pin cannot express the
   parameter), and a `Rollershutter`/`Player` control (full-screen
   UP/DOWN/STOP and PLAY/PAUSE/NEXT/PREVIOUS).

### Operator verification: loopback and host overlay

Run these on the dedicated Linux host after starting the stack; they are the
manual counterpart to `tests/runner/tests/test_compose.py` and
`tests/runner/tests/test_network.py`.

1. **Rendered configuration binds loopback.** With the stack running,
   `docker compose config` (same `-f` files as the start command) must show
   every published port prefixed with `127.0.0.1:`. In the host overlay the
   `ports` entries are gone and each service has `network_mode: host`.
1. **No wildcard listener.** `ss -ltnp` on the host: ports 8080/8081 (and 8443
   when openHAB runs) must be bound to `127.0.0.1`, never `0.0.0.0` or `[::]`.
   Repeat from another machine with `curl http://<host>:8081/__shim__/health`;
   the connection must be refused or time out.
1. **Loopback wiring works.** Inside the runner container,
   `python -m e2e status` must report `openHAB: available` and
   `shim: available` with `127.0.0.1` URLs. In the host profile, run
   `curl http://127.0.0.1:8081/__shim__/health` on the host as well.
1. **App and runner agree.** `python -m e2e run smoke/launch-and-poll` in the
   host profile: the app polls `127.0.0.1:8081` and the shim trace records only
   those requests.
1. **Port change propagates.** Edit a port in `config/network.yaml`, run
   `python -m e2e network-env --write`, recreate the stack and confirm both the
   published port and the app's URL moved together. `python -m pytest` must
   pass before the change is committed.
1. **No TLS material is needed.** Nothing in the start commands references a
   certificate or key. External access is provided by the operator's reverse
   proxy in front of `127.0.0.1`.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `compose: services.runner must mount the Connect IQ SDK` | `CIQ_SDK_HOST_PATH` is unset or empty in `tests/compose/.env` |
| `compose: services.runner.E2E_ARTIFACTS=... is inside the read-only mount` | the artifacts volume was moved back under `/work`; it must be its own mount (`/artifacts`) |
| `error: no Connect IQ device definitions were found` | the SDK ships no `Devices/` directory and `CIQ_DEVICES_HOST_PATH` is empty; see [docs/sdk.md](docs/sdk.md) |
| `UnsatisfiedLinkError: libharfbuzz.so.0` | the runner image is missing the font stack; rebuild it (`docker compose build runner`) |
| `Read-only file system` while writing into the SDK | the SDK copy in `entrypoint.sh` was bypassed; the mount is read-only on purpose |
| openHAB `Exited (1)` with `chown: ... Read-only file system` | something is mounted read-only under `/openhab`; the fixtures must be staged by `openhab-<profile>-init` |
| `The private key was not specified` | `e2e build`/`run` could not generate the throwaway key; install `openssl` or pass `--developer-key` |
| Container exits with code 78 | SDK missing, unreadable, or its `version.txt` does not match `config/sdk.version` |
| `pins: OPENHAB_STABLE_IMAGE is not pinned` | run `python -m e2e pin-images --write` |
| `setup: sitemap ... references item(s) that are not defined` | fixture `.items` file drifted from the sitemap |
| openHAB healthy but no sitemap | the `garmin_e2e` fixture failed to parse; `python -m e2e verify-fixtures --strict --log <openhab.log>` prints the `DSL model ... has errors` lines, or check `docker compose logs openhab-stable` |
| `verify-fixtures` reports `does not match the checkout fixture tree` | `E2E_REPO_ROOT` points the runner at a different checkout than the Compose project's `../fixtures`; start the stack from the checkout under test |
| `verify-fixtures` reports `missing sitemap(s): garmin_confirm` on a dev scenario | the stack was started without `-f compose.dev.yaml`, so `openhab-dev-init` did not stage the dev overlay |
| `verify-fixtures --sizes` reports a sitemap above the byte budget | split it along scenario boundaries; the simulator rejects a response above 16,384 bytes with `-402` (see [docs/fixture-limits.md](docs/fixture-limits.md)) |
| `verify-fixtures --sizes` reports the near/over fixtures no longer straddle the limit | regenerate them with `python tests/fixtures/openhab/generate_size_fixtures.py` and update the measured sizes in `docs/fixture-limits.md` |
| Everything times out on the dev profile | you started it without `-f compose.dev.yaml` |
| `compose: services.shim.SHIM_BIND resolves to '0.0.0.0'` | the host overlay was edited to hardcode a bind address; use `${E2E_LOOPBACK_HOST}` |
| `the generated network block has drifted` | run `python -m e2e network-env --write` after editing `config/network.yaml` |
| `unknown flag: reset` / `!reset` parse error | Docker Compose is older than 2.24; upgrade it or use the bridge profile |
| Nothing is reachable from another machine | by design: ports are loopback-only, put a reverse proxy in front |
| `environment: unknown` in a run report | the runner has no Docker socket, or the run was a dry run; capture the window on the host, see [docs/diagnostics.md](docs/diagnostics.md) |
| Exit code 3 from `run` or `health-snapshot` | the stack changed during the run: the environment is suspect, re-run before filing an app defect |

## Further reading

- [docs/architecture.md](docs/architecture.md) – components, data flow, assertion layers
- [docs/diagnostics.md](docs/diagnostics.md) – stack health: restart counts, working set, OOM evidence and run triage
- [docs/devices.md](docs/devices.md) – build-variant dimensions, the device matrix and its coverage check
- [docs/scenarios.md](docs/scenarios.md) – scenario schema, language matrix, trace events and `covers`
- [docs/coverage.md](docs/coverage.md) – generated #140 scope-to-scenario matrix with waivers and manual checks
- [docs/localisation.md](docs/localisation.md) – language coverage, device choice and review-only assertions
- [docs/sdk.md](docs/sdk.md) – SDK layout, version pinning, licence handling
- [docs/simulator-driver.md](docs/simulator-driver.md) – driver contract and Wine plan
- [docs/visual-truth.md](docs/visual-truth.md) – what visual truth one Ubuntu VM can give: off-screen assertions, no-input captures, AI review and the rejected Windows host
- [docs/unit-tests.md](docs/unit-tests.md) – Connect IQ unit tests: feasibility, tier split, limits and operator commands
- [spike/README.md](spike/README.md) – the #370 Wine feasibility spike and its verdict
- [shim/README.md](shim/README.md) – shim control API and fault-script format
