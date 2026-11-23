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

## Status

This directory is the foundation tracked by
[#365](https://github.com/openhab/openhab-garmin/issues/365), part of
[#140](https://github.com/openhab/openhab-garmin/issues/140).

| Piece | State |
| --- | --- |
| Compose stack, fixtures, scenarios, runner, shim | complete and statically validated |
| `pytest` unit tests for scenario loading, shim grammar, trace parsing, Compose validation, SDK probing, dry-run engine | complete, run without Docker |
| openHAB 5.2.5 (stable) and 5.3.0 milestone (dev) image digests | pinned in `config/openhab.version` |
| Protocol-tier scenarios (shim trace + `E2E:` trace events) | ready; need the debug trace build from [#364](https://github.com/openhab/openhab-garmin/issues/364) |
| Wine simulator driver | contract fixed, implementation skeleton only (see [docs/simulator-driver.md](docs/simulator-driver.md)) |

Nothing here provisions a VM, changes infrastructure, or ships the Connect IQ SDK.

> **Device matrix is provisional.** `config/devices.yaml` is a candidate set that
> gives scenarios stable device ids; it is _not_ the approved build-variant
> matrix. Variant coverage (button/touch watches, CIQ tiers, button/touch Edge,
> short/tall Edge resources, round/rectangular, ActionMenu vs full menu,
> device-specific `Config`/`GlanceConfig` paths, Wi-Fi/glance capabilities) is
> tracked in [#366](https://github.com/openhab/openhab-garmin/issues/366), which
> owns the variant-to-device table and the automated coverage check. The
> `variants:` field in `devices.yaml` is the extension point for that table.

## Prerequisites

- Linux host with Docker and Docker Compose v2 (or a VM/WSL2 guest; macOS works for
  the stack, but the simulator needs Wine and is only supported on Linux).
- The Connect IQ SDK **mounted, never committed**: `CIQ_SDK_HOST_PATH` points at it.
  Layout and licence rules: [docs/sdk.md](docs/sdk.md).
- Python 3.10+ on the host for the unit tests and the CLI (`tests/runner`,
  declared as `requires-python` in `runner/pyproject.toml`; CI uses 3.12).

## Continuous integration

`.github/workflows/e2e-harness.yaml` runs the checks that need neither Docker nor
the SDK on every pull request and `main` push that touches `tests/`,
`manifest.xml`, `monkey.jungle` or the workflow itself:

1. `python -m pytest` in `tests/runner`
1. `python -m e2e pin-images --write` then `python -m e2e validate-compose --check-pins`
1. `python -m e2e contract-test --driver null`
1. `python -m e2e run --all --dry-run`

The job installs the package **editable** (`pip install --editable tests/runner`)
because `e2e/paths.py` derives `tests/` and the repository root from the package
location; a copy in `site-packages` would look for the fixtures outside the
checkout. It also runs `python -m e2e pin-images --write` before
`validate-compose --check-pins`, because `--check-pins` compares the git-ignored
`tests/compose/.env` with `tests/config/openhab.version`, and the committed
digests make that write offline.

## Quick start

```bash
cd tests/compose
cp .env.example .env
export CIQ_SDK_HOST_PATH=/opt/garmin/connectiq-sdk-9.3.0   # or edit .env

cd ../runner
python -m pip install -r requirements.txt
python -m pip install --editable .    # optional: `python -m e2e` from anywhere
python -m e2e pin-images --write      # writes @sha256 pins into ../compose/.env
python -m e2e validate-compose        # no Docker needed

cd ../compose
docker compose up -d                  # openhab-stable, shim, runner
docker compose ps                     # wait for the healthchecks

cd ../runner
python -m e2e status                  # SDK, openHAB, shim, pins
python -m e2e setup                   # fixture sanity check over REST
```

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

## CLI

| Command | Purpose |
| --- | --- |
| `setup` | validate Compose, SDK, openHAB readiness and that the fixture sitemap resolves |
| `run` | run scenario names, `--suite`, or `--all`; writes JSON, JUnit XML and Markdown |
| `reset` | clear the shim's script and request trace (optionally reset item states) |
| `status` | health of every component, plus pin problems |
| `list-scenarios` | what `run --all` would execute |
| `contract-test` | exercise a simulator driver against the driver contract |
| `validate-compose` | static Compose validation; `--check-pins` also requires digests |
| `pin-images` | write or verify image digests (`--refresh` re-resolves from Docker Hub) |
| `sdk-check` | validate the mounted SDK without starting anything |
| `build` | compile the PRG for one or more devices with the Linux `monkeyc` |

Exit codes: `0` success, `1` scenario failures, `2` configuration or environment
error.

`run` flags worth knowing:

- `--device <id>` overrides the scenario's device (see `config/devices.yaml`).
- `--driver wine|null`; `--dry-run` implies `null`.
- `--update-goldens` writes new golden images instead of comparing; review the
  screenshots before committing them.
- `--skip-events` skips trace-event assertions (for builds without `(:debug)`).
- `--prg <file>` reuses a build instead of compiling.

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
| regression | `command-confirmation` | ui | cancelling a confirmed command sends no request |
| extended | `oversized-response` | protocol | 2 MiB body is reported as a critical error |
| extended | `slow-response` | protocol | 12 s responses escalate and then recover |
| extended | `connection-reset` | protocol | dropped connections are temporary errors |
| extended | `dynamic-visibility` | ui | `visibility` reacts to a state change while the app is open |
| extended | `wifi-mode` | protocol | Wi-Fi mode renders the cached sitemap and does not poll |
| extended | `offline-mode` | protocol | offline mode sends nothing |

`protocol` scenarios assert on the shim trace and on `E2E:` trace events and need
no screenshots. `ui` scenarios navigate the app and compare screenshots with
reviewed goldens.

Every scenario currently runs on the matrix default device, so the device dimension
is not exercised yet. The representative device subset and its variant coverage are
issue [#366](https://github.com/openhab/openhab-garmin/issues/366); this repository
does not claim coverage completeness.

Scenario authoring, the full YAML schema and the trace-line format are documented
in [docs/scenarios.md](docs/scenarios.md).

### Scenario order

`wifi-mode` and `offline-mode` render a _cached_ sitemap, so they must run after a
BLE scenario has populated the device cache. Any `smoke/` scenario does that.

## Fixtures

```text
fixtures/openhab/conf/items/garmin_e2e.items       every supported item type + one unsupported
fixtures/openhab/conf/sitemaps/garmin_e2e.sitemap  every supported element and parameter
fixtures/openhab/conf/sitemaps/garmin_unsupported.sitemap  the "no supported widget" path
fixtures/app/settings/*.yaml                       Connect IQ property sets
```

The baseline sitemap starts from the one posted in
[#140](https://github.com/openhab/openhab-garmin/issues/140#issuecomment-...) and
covers `Frame`, `Default`, `Text`, `Group`, `Switch`, `Selection`, `Setpoint`,
`Slider` (including `releaseOnly`), nesting, `mappings`, `labelcolor`/`valuecolor`,
`visibility`, `icon`/`staticIcon`, `commandConfirmMessage`, empty pages and the
full state/command matrix (toggle, generic switch, rollershutter, player).

Fixture files are the source of truth:

- item **structure** lives in the `.items`/`.sitemap` files;
- item **state** is set over REST (`PUT /rest/items/{name}/state`), which is what
  `openhab.items` in a scenario does;
- **runtime structural changes** (adding an element while the app runs) belong to
  REST UI components (`/rest/ui/components/ui:sitemap`), not to these files.

The two fixture directories are mounted **read-only** into a writable conf volume,
so the container can create its own files without ever writing into the checkout.

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

## Profiles

- `stable` is the default: `docker compose up -d`.
- `dev` is opt-in and needs the override file, because the shim's upstream must
  follow the profile:

  ```bash
  COMPOSE_PROFILES=dev docker compose -f compose.yaml -f compose.dev.yaml up -d
  ```

  Using `--profile dev` without the override file starts a shim that points at a
  service that is not running; `validate-compose` rejects that override shape.

## What was and was not executed

This change was reviewed on a workstation, so the validation split is:

### Executed

```bash
cd tests/runner
python -m pytest -q                     # 191 passed
python -m e2e validate-compose          # compose: valid
python -m e2e pin-images --write        # digests written from config/openhab.version
python -m e2e pin-images --check        # pins: ok
python -m e2e validate-compose --check-pins
python -m e2e contract-test --driver null       # 14 passed
python -m e2e run smoke/launch-and-poll --dry-run
python -m e2e run --all --dry-run       # 14 scenarios, artifacts + JUnit written
python -m e2e list-scenarios            # 14 scenarios
python -m e2e sdk-check                 # fails with the documented message (no SDK here)
```

Two checks used a synthetic SDK tree (a temporary directory with `version.txt`,
`linux/bin/monkeyc` and `windows/bin/simulator.exe`) to exercise the paths that
need an SDK without requiring one:

```bash
CIQ_SDK_ROOT=<synthetic tree> python -m e2e sdk-check           # exit 0, layout printed
CIQ_SDK_ROOT=<synthetic tree> python -m e2e contract-test --driver wine
# 3 pass (interface, events, stop), 11 not-implemented, exit 2 - the skeleton
# reports what is missing instead of pretending to run
```

### Not executed (no Docker daemon or SDK on the workstation)

- `docker compose config` and `docker compose build`.
- Starting openHAB and verifying the fixture sitemap renders in the app.
- Building the PRG with `monkeyc` and running a real scenario.
- The Wine driver: it is a skeleton by design.

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
   `commandConfirmMessage` cancel (sends nothing), and a `Rollershutter`/`Player`
   control (full-screen UP/DOWN/STOP and PLAY/PAUSE/NEXT/PREVIOUS).

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `compose: services.runner must mount the Connect IQ SDK` | `CIQ_SDK_HOST_PATH` is unset or empty in `tests/compose/.env` |
| Container exits with code 78 | SDK missing, unreadable, or its `version.txt` does not match `config/sdk.version` |
| `pins: OPENHAB_STABLE_IMAGE is not pinned` | run `python -m e2e pin-images --write` |
| `setup: sitemap ... references item(s) that are not defined` | fixture `.items` file drifted from the sitemap |
| openHAB healthy but no sitemap | the `garmin_e2e` fixture failed to parse; check `docker compose logs openhab-stable` |
| Everything times out on the dev profile | you started it without `-f compose.dev.yaml` |

## Further reading

- [docs/architecture.md](docs/architecture.md) – components, data flow, assertion layers
- [docs/scenarios.md](docs/scenarios.md) – scenario schema and trace events
- [docs/sdk.md](docs/sdk.md) – SDK layout, version pinning, licence handling
- [docs/simulator-driver.md](docs/simulator-driver.md) – driver contract and Wine plan
- [shim/README.md](shim/README.md) – shim control API and fault-script format
