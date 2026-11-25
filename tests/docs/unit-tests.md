# Connect IQ unit tests (Run No Evil)

Status: **pilot, feasible, not yet executed on the E2E guest.** This page records
what was established about Garmin's unit-test framework in this repository, what
the pilot covers, how it runs, and where its limits are. It is the design note
for the tier; the tests themselves live in `source/test/` and are run by
[`tests/unit/run`](../unit/run).

Related: [#140](https://github.com/openhab/openhab-garmin/issues/140) (regression
tests), [#396](https://github.com/openhab/openhab-garmin/issues/396) (why the E2E
harness cannot drive the app today).

## Why this tier exists

The E2E harness under [`tests/`](../README.md) does the environment side well:
it builds the app, loads fixtures, splits responses for the simulator's 16 KiB
limit, provisions languages, collects the `E2E:` trace and reports honest
coverage. What it cannot do is **drive the app**: on the pinned simulator under
Wine, `Up`/`Down` move the screen but no synthetic click or select keysym
reaches the app, so it never leaves its glance page and no scenario can assert
behaviour ([#396](https://github.com/openhab/openhab-garmin/issues/396),
[#399](https://github.com/openhab/openhab-garmin/pull/399)).

Most of what #140 asks for does not need input at all. Parsing, classification,
validation and formatting are pure functions over JSON. Those are exactly what a
unit test can reach, and they are the first thing that breaks when the protocol
code changes. This tier exists to cover them, not to replace the E2E tier.

## Where the tests live and how they are included

| Item | Value |
| --- | --- |
| Sources | `source/test/*.mc`, every test annotated `(:test)` |
| Build inclusion | `srcBase=source/main;source/test` in `monkey.jungle`, so every device build carries them |
| Compiler flag | `monkeyc --unit-test` (`-t`); the CI script already passes it |
| Runner | `monkeydo <prg> <device> -t [name]` |
| Removed from normal builds | yes, by the compiler |

The annotation reference states that `(:test)` "Denotes test cases that are used
as Run No Evil unit tests and are excluded from the application at compile
time." That was verified, not assumed:

| Build (`monkeyc`, fenix847mm, typecheck 3) | Result | PRG size | Test symbols in PRG |
| --- | --- | ---: | --- |
| `--unit-test` | BUILD SUCCESSFUL | 675,708 B | present (`SitemapModelTests`, `UnitTestFixtures`, …) |
| no `--unit-test` | BUILD SUCCESSFUL | 652,124 B | absent |
| `--unit-test --release` | rejected: `ERROR: Unable to build unit tests in release mode.` | – | – |

Consequences worth knowing:

- **No new CI wiring is needed for compilation.** `.github/scripts/monkeyc-build`
  already passes `--unit-test`, so the existing five-device matrix typechecks the
  tests on every pull request. Adding `source/test` to `srcBase` is what makes
  that happen; removing `--unit-test` from that script would silently drop the
  tests from the gate.
- **There is no separate test jungle.** The unit build uses the same
  device-scoped jungle as the app; `tests/unit/run` generates it with the same
  transformer as CI.
- **Tests are in the app's source tree, not in `tests/`.** That is deliberate:
  `tests/` is a portable Docker/Compose unit, and the `sourcePath` is what both
  the app build and the test build share. The alternative (a second jungle that
  appends a test directory) duplicates the device-scoping logic for no gain.
- **A test that needs a device-specific API must be guarded.** Every device
  compiles every test. Today all tests use device-independent APIs; a future
  test that does not must be excluded per device via `monkey.jungle`.
- Verified locally with the committed reduced SDK, Java 25, typecheck 3 and
  `--warn`: **fenix6pro, venu2, venu3, fenix847mm, edge540** all build with
  `--unit-test`. Those are the five devices of the CI build matrix.
- The test build must **not** pass `--release` (the compiler refuses), which is
  convenient: the app's `(:debug)` helpers remain available in a failure log.

## How a run works

```bash
tests/unit/run                 # all tests, fenix847mm
tests/unit/run --device venu2
tests/unit/run --test SitemapModelTests.homepageTitleAndWidgetTypes
```

The script:

1. resolves the SDK (`CIQ_SDK_ROOT` / `CIQ_SDK_PATH` / the newest
   `~/.Garmin/ConnectIQ/Sdks/connectiq-sdk-*`) and checks it against
   `tests/config/sdk.version`;
1. stages device definitions in `~/.Garmin/ConnectIQ/Devices` when they are
   missing (the compiler reads them from there, not from the SDK);
1. writes a throwaway developer key and a device-scoped jungle;
1. compiles with `--unit-test --typecheck 3 --warn`;
1. reuses a running simulator, or starts `windows/bin/simulator.exe` under Wine
   and waits for `127.0.0.1:1234`;
1. runs `monkeydo <prg> <device> -t`;
1. classifies the `RESULTS` block with
   [`tests/unit/parse_results.py`](../unit/parse_results.py).

### What `monkeydo -t` reports

The documented console shape is:

```text
------------------------------------------------------------------------------
Executing test homepageTitleAndWidgetTypes...
DEBUG (14:16): homepage title=My Home widgets=5
PASS
==============================================================================
RESULTS
Test:              Status:
homepageTitleAndWidgetTypes   PASS
Ran 1 test
PASSED (failures=0, errors=0)
Connection Finished
Closing shell and port
```

- A test function returns `true` (pass) or `false` (`FAIL`).
- A thrown exception, including every `Test.assert*`, is `ERROR`, not `FAIL`.
  Both must fail the run.
- `monkeydo -t NAME` runs a single test; the name is the function or the
  qualified method (for a class method, expect `Class.method`; the pilot's
  `--test` example uses that form and the run tells you if it is wrong).
- **No JUnit or other machine-readable report is documented.** If the pinned
  `monkeydo` gains one, `parse_results.py` can be replaced; until then the
  console is the interface.
- **The exit code for a failing test is not documented and was not measured.**
  `tests/unit/run` therefore treats the `RESULTS` block as authoritative and
  only uses the exit code as an additional failure signal. A run with no
  `RESULTS` block, no `PASSED` summary, a `FAIL`/`ERROR` test, or `Ran 0` fails.

The parser is covered by six synthetic logs (pass, error, lowercase `Pass`,
missing block, zero tests, inconsistent summary); it is verified locally, but
the _real_ output has not been observed yet.

### Headless

Unit tests need no input, no touch and no key presses, so the #396 input problem
does not apply. They do need the simulator **process** and an X display, exactly
like the E2E driver: `DISPLAY` (default `:99`) plus Wine. Whether the whole
sequence succeeds on the E2E guest is the first thing the operator run
answers; it was **not** executed here (see [Executed and not executed](#executed-and-not-executed)).

### Network from inside a unit test

The simulator can reach HTTP: the E2E harness records real requests from the app,
so TCP is not the obstacle. The obstacle is the shape of a unit test:

- `Test` functions are synchronous. They take a `Test.Logger` and return a
  `Boolean`; there is no wait, promise or event-loop hook.
- `Communications.makeWebRequest` answers through a callback. The callback runs
  on the event loop, which is not running while a test function executes.
- A busy-wait on `System.getTimer()` would block the loop that delivers the
  callback, so it cannot make progress; the watchdog would fire.

Therefore a test can _issue_ a request but cannot _assert on its response_.
Protocol tests that need a payload must hand one to the parser, as the pilot
does (`JsonAdapter` takes the parsed `JsonObject` directly), or the transport
must be injected behind an interface. **No transport injection is introduced
here**: it is a production change with a risk profile of its own, and it is not
needed for the pilot. If the -402 boundary or the command paths must be tested
below the network, that refactor is the way, and it should be agreed separately
(see [Next candidates](#next-candidates)).

One experiment would settle the callback question empirically, and it is listed
for the operator below: a throwaway test that calls `makeWebRequest` and returns
immediately. If the log shows the callback _after_ the `RESULTS` block, the
event loop does run between tests and a bounded wait might be possible. Until
then, treat "network is faked, not awaited" as the design rule.

## What the tier cannot reach

| Cannot test | Why |
| --- | --- |
| Views, menus, drawing, themes | `getInitialView()` is not called; there is no display context |
| Input (buttons, touch), pickers, confirmations | Nothing dispatches input actions |
| Lifecycle (`onStart`, `onActive`, `onStop`, deferred errors) | The app lifecycle does not run |
| Settings changes while running | Needs a settings mutation and a lifecycle transition |
| Glance and widget content | Separate process/mode; also blocked by #396 |
| Real HTTP responses, response size limits, timeouts | Callback-driven, see above |
| Storage-driven freshness (the 10 s window) | `SitemapStore` takes its timestamp from `Time.now()`; there is no clock injection |
| Timers, polling cadence, escalation over time | Need a running event loop |
| Device/screen variants | Only the device chosen for the build; rendering is not observable |

## Tier split

| Question | Tier |
| --- | --- |
| Does this JSON become the right model / value / error? | **unit** (`source/test`) |
| Does the app send the right request, and react to the right response? | E2E (shim trace) |
| How does it look, and what happens after a button press? | E2E, once input works; manual until then |
| Which errors are full-screen vs toast? | unit for the predicates, E2E for presentation |
| Does the -402 boundary sit at 16,384 bytes? | E2E (`limits-near` / `limits-over`), measurement recorded in [fixture-limits.md](fixture-limits.md) |
| Do all devices compile, and does the glance still exist? | CI matrix (`ci-build.yaml`) |

The E2E harness does **not** depend on this tier and was not changed. There is no
`python -m e2e unit` command: the unit tier needs no Docker, no openHAB, no shim
and no scenario schema, so a sibling script is the natural home. The two tiers
share only the SDK pin (`tests/config/sdk.version`) and the SDK-layout rules in
[sdk.md](sdk.md).

`tests/docs/coverage.md` is generated from `config/scope.yaml` and the scenarios,
so the pilot is not listed there. Several #140 entries are currently **waived**
because they need pixels or input (`sitemap.parameters-colors`,
`sitemap.parameters-range`, …). The unit tier now asserts the _parsing_ half of
those entries; the visual half stays waived. Do not remove a waiver on that
basis alone.

## The pilot

| File | Tests | Regression it catches |
| --- | --- | --- |
| `source/test/UnitTestFixtures.mc` | – | the shared payload, shaped like `garmin_e2e.sitemap` |
| `source/test/SitemapModelTests.mc` | homepage title and widget types; label/state split; Frame and linked page nesting; empty page; unsupported type; item type/unit/state rules | a change to `JsonAdapter`, `SitemapHomepage`, the factory or `Item` that breaks the REST contract |
| `source/test/ErrorClassificationTests.mc` | -402/-403/-1001/404 are full-screen; BLE codes are no-phone and not full-screen; `S:`/`C:` prefixes; empty-response suppression for -400 and null bodies | an error code silently changing class, or the user-facing code changing |
| `source/test/SitemapParameterTests.mc` | Setpoint/Slider defaults, invalid step/range/step-count, releaseOnly default, numeric formatting, ON/OFF mapping normalization, switch display state, named and hex colors | a parameter rule silently relaxing or a mapping/colour silently regressing |

Notes on the pilot data:

- The payload is **hand-built**, not a byte capture of an openHAB response. The
  repository has no captured sitemap JSON, and openHAB generates it. The fixture
  mirrors the element set, nesting and ranges of
  `tests/fixtures/openhab/conf/sitemaps/garmin_e2e.sitemap`, so a fixture change
  there should be reflected here. The E2E tier stays the authority on openHAB's
  serialization; if openHAB changes it, `verify-fixtures` and the scenarios fail
  first.
- The tests call the app's own classes. There is no parallel parser and no
  reimplementation of the classification rules: `ExceptionHandler`'s presentation
  logic is _not_ re-tested, only the exception predicates it consumes.

## CI and cost

| Item | State |
| --- | --- |
| Compile gate | **free and already there**: `ci-build.yaml` builds five devices with `--unit-test`, so the tests are typechecked on every PR |
| Run job | **not possible on GitHub-hosted runners**: no simulator binary in the reduced SDK, no Wine, and the full SDK may not be redistributed or downloaded in CI |
| Run cost on the guest | ~7 s for a full fenix847mm unit-test compile (measured on the authoring machine), plus simulator start (Wine, ~10–20 s) and a few seconds of tests |
| Failure output | `tests/unit/out/monkeydo.log`, `compiler.log`, `simulator.log` |

Running the tier per pull request would require the same kind of environment as
the E2E suite (SDK + Wine + Xvfb) and should be scheduled next to it, not in the
GitHub-hosted jobs. Adding a self-hosted runner job later is a small change:
`tests/unit/run` is already the whole interface.

## Manual verification (operator)

Run on the E2E guest (the Linux host with Docker, Wine and the SDK), in a fresh
clone of the branch, **not** in the runner container (the script needs a writable
checkout and the host's Wine/Xvfb):

```bash
git clone https://github.com/openhab/openhab-garmin.git /home/ansible/pr-unit
cd /home/ansible/pr-unit
git checkout <your-branch>

# the same SDK the E2E stack mounts
export CIQ_SDK_ROOT=/path/to/connectiq-sdk-lin-9.3.0-2026-10-20-95888f262
export DISPLAY=:99          # the Xvfb the E2E driver already uses
tests/unit/run
```

If the simulator is not running, the script starts it under Wine; if it is, the
script reuses it. `--keep` leaves it running.

### What proves success

```text
==> results
  PASS  <test name>          (one line per test)
  Ran 18 tests
PASSED: PASSED (failures=0, errors=0)
UNIT TESTS PASSED
```

and exit code `0`. The test names are the class-qualified function names; the
pilot has 18 tests (6 model, 5 error classification, 7 parameter). Success also
proves the four open questions:

1. `monkeydo -t` runs headless on the guest (no input, no window interaction).
1. The compile from `tests/unit/run` works with the guest's SDK.
1. The documented `RESULTS` format matches the pinned SDK.
1. The tests themselves run — which is the one thing a compile cannot prove.

### What proves failure

| Observation | Meaning |
| --- | --- |
| `FAILED: the log has no RESULTS block` + no `Executing test` lines | the tests did not run: `monkeydo` rejected the PRG or the command line |
| `Encountered an app crash` / `Invalid Font Specified` in the log | `monkeydo -t` also starts the app; the glance (#396) interferes. Report it: the unit build then needs the E2E's watch-app/glance-stub overlay |
| `FAILED: <summary>` or `ERROR <name>` lines | a real regression, or a test that touches an unavailable API |
| `error: simulator did not accept connections` | Wine/Xvfb, not the tests |
| `error: SDK version mismatch` / `no monkeydo` | environment, not the tests |
| exit `1` with `UNIT TESTS FAILED` | as above; the log tail is printed |

Please also capture, once, whether the exit code of `monkeydo` is non-zero on a
failing test (the script prints `monkeydo exited with code N` when it is):

```bash
# after a deliberate failure, e.g. temporarily invert an assertion
tests/unit/run --test SitemapModelTests.homepageTitleAndWidgetTypes; echo "exit=$?"
```

And, if you want to settle the network question:

```bash
# grab monkeydo's own option list; if a --test-report exists, we can use it
monkeydo --help
```

## Next candidates

Cheap and high value, in order:

1. **Command building**: `NativeCommandRequest` / `WebhookCommandRequest` build
   their payload and URL from settings. The URL assembly is pure; the request
   itself is not. A test for the payload shape and the webhook-identifier
   failure is possible without touching production code.
1. **`JsonComparer`**: the "identical response is skipped" rule (#355, #216). It
   is pure and currently only exercised end to end.
1. **Sitemap freshness / escalation window**: `BaseConfig.STATE_EXPIRATION_TIME`
   is consumed by `SitemapStore.isSitemapFresh()` from `Time.now()` with no
   injection point. A tiny `isFresh( ageSeconds )` helper would make the 10 s
   window testable; that is a production refactor and needs agreement.
1. **`SitemapStore.STORAGE_JSON_MAX_SIZE`**: the app's own 81,920-byte cache
   limit is compared against an estimate derived from `System.getSystemStats()`
   deltas during a real request, so it is not testable without a transport or a
   seedable estimate.
1. **The -402 boundary below the transport**: only after an injectable transport
   exists. Until then it belongs to the E2E `limits-near`/`limits-over` pair.

## Recommendation

**Adopt, narrowly, and expand only along the list above.** The tier costs
nothing on pull requests (the compile gate already exists), runs in seconds, and
covers exactly the protocol code that the E2E tier cannot reach while the app
cannot be driven past its glance page. It does not replace any E2E work, and it
does not remove any waiver.

Do not drop it: the pilot already asserts colour parsing, range validation and
mapping normalization that are otherwise unverifiable. Do not grow it into a
framework either — a test that needs a view, a timer or a network response
belongs to the E2E tier or to a follow-up issue.

## Executed and not executed

**Executed here** (Windows workstation, committed reduced SDK, Java 25):

- `monkeyc --unit-test --typecheck 3 --warn` for fenix6pro, venu2, venu3,
  fenix847mm and edge540: all `BUILD SUCCESSFUL`.
- the same build without `--unit-test` (success, test symbols absent) and with
  `--release` (rejected by the compiler);
- `tests/unit/run` end to end against a fake SDK, a fake `monkeydo` output and a
  stub simulator port: pass → exit 0, failure → exit 1, missing `RESULTS` →
  exit 1;
- `parse_results.py` against six synthetic logs.

**Not executed** (no simulator on this workstation):

- `monkeydo -t` itself: no `monkeydo` or `simulator.exe` outside the official SDK,
  and no Wine;
- therefore the real console format, the real exit code, headless behaviour on
  the guest, and whether `-t` also starts the app are **unverified**. The manual
  verification above closes all four.

## Maintenance rules

- Keep `tests/config/sdk.version` and `tests/unit/run`'s checks in step; a
  changed `RESULTS` format must change `parse_results.py` and its synthetic
  cases.
- When `garmin_e2e.sitemap` changes shape, mirror it in
  `source/test/UnitTestFixtures.mc`.
- A test that needs a production change (injection, extraction) is a separate
  pull request with its own risk note. This tier must not weaken the app to be
  testable.
- Never commit a `.prg`, a developer key or any SDK file. `unit.generated.jungle`
  and `tests/unit/out/` are git-ignored.
