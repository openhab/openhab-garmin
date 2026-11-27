# Scenarios

A scenario is declarative YAML executed by the harness. Nothing in a scenario file
is code, and the schema is validated strictly: an unknown key is an error, not a
warning, because a silently ignored typo turns an intended failure test into a
happy-path test that still passes.

## Top level

```yaml
name: launch-and-poll          # unique, kebab-case; matches the file name
description: One line.
tier: ui                       # protocol | ui
suite: smoke                   # smoke | regression | extended; must match the directory
device: fenix7                 # id from tests/config/devices.yaml, or "any"
mode: ble                      # ble | wifi | offline (communication mode at start)
fixture: default               # tests/fixtures/app/settings/<name>.yaml
openhab:
  profile: stable              # stable | dev
  sitemap: garmin_e2e          # sitemap name the app is configured with
  items:                       # item states seeded over REST before launch
    E2E_Light: "ON"            # quote ON/OFF: YAML 1.1 parses bare ON as a boolean
language: eng                  # eng | deu | fre | ita | spa | pol
                               # or: languages: [eng, deu, fre, ita, spa, pol]
timeout_s: 300                 # whole-scenario budget, 1..1800; see below
steps: [...]                   # non-empty; the first step must be `launch`
covers:                        # optional; #140 scope ids, see Scope coverage below
  - sitemap.element-types
manual_checks:                 # optional; exercised but not machine-asserted
  - The rendered text is review-only.

# optional
settings:                      # merged onto the fixture, validated against settings.xml
  pollingInterval_0: 250
shim:                          # fault script, see tests/shim/README.md
  rules: [...]
notes: Free-form operator note.
```

**Two properties a scenario always wins over its fixture.** `openhab.sitemap`
becomes the compiled `sitemap_0`, and the harness's shim URL becomes `url_0`,
whatever the settings fixture says. The fixture supplies the behavioural
defaults (polling interval, suppress-empty, webhook) and nothing else, because a
fixture default sitemap compiles a page the scenario does not assert — the app
then polls one sitemap while the scenario waits for another, which reads as a
missing request rather than a configuration mismatch. The build cache key is the
**composed** settings, so two scenarios sharing a fixture but not a sitemap
cannot share a PRG.

Unknown devices, tiers, modes, languages, suites, actions, step keys, settings
property ids and shim rule keys are all rejected at load time with the file, the
key and the reason.

### The whole-scenario budget must fit a launch

`timeout_s` covers the whole scenario, and opening the app is the expensive part:
the Wine driver may settle for up to **90 s** before pressing and then spend up
to **180 s** retrying the launch sequence, because input delivery is
intermittent. A launch step can therefore consume **270 s** before the app's
first view, and a scenario that budgets less fails a slow-but-legitimate run for
a reason that has nothing to do with the app.

Every scenario here budgets **300 s** (the launch worst case plus room for its
own steps). The numbers live in one place — `LAUNCH_SETTLE_WORST_CASE_S`,
`LAUNCH_RETRY_WORST_CASE_S` and `LAUNCH_SCENARIO_MINIMUM_S` in
`e2e/scenarios.py` — and two CI checks keep them honest:
`test_the_launch_minimum_mirrors_the_driver` fails when they drift from
`e2e.sim.wine`, and `test_every_launch_scenario_budgets_for_the_launch_worst_case`
fails when a scenario drops below the minimum. Do not lower the retry budget to
fit a shorter scenario: the retries exist because delivery is intermittent.

The `openhab.profile` field selects which fixture tree the scenario expects:
`stable` uses `fixtures/openhab/conf`, `dev` additionally uses the
`fixtures/openhab/conf-dev` overlay. It is not a URL selector; both profiles
serve on the URL Compose gives the runner.

### openHAB profiles: a complete run is one pass per profile

Profiles are **exclusive**: one openHAB serves one fixture tree, and the `dev`
overlay is the only place constructs the stable pin cannot parse may live. No
single `run --all` can cover every scenario: the dev run skips the eight
`stable`-targeted scenarios and the stable run skips both dev scenarios, so a
complete pass is **stable plus dev** and neither run alone is a pass.

```bash
cd tests/compose

# pass 1 — the default (stable) stack
docker compose up -d --wait
python -m e2e run --all

# pass 2 — the dev overlay on the same port
docker compose -f compose.yaml -f compose.dev.yaml -f compose.host.yaml \
  -f compose.host.dev.yaml down                     # volumes persist
COMPOSE_PROFILES=dev docker compose -f compose.yaml -f compose.dev.yaml \
  -f compose.host.yaml -f compose.host.dev.yaml up -d
# only if openhab-dev predates the port being free (see below)
docker compose -f compose.yaml -f compose.dev.yaml -f compose.host.yaml \
  -f compose.host.dev.yaml restart openhab-dev
until curl -fsS http://127.0.0.1:8080/rest/ | grep -q '"runtimeInfo"'; do sleep 2; done
python -m e2e run --all
```

**Restart `openhab-dev` if the container predates the port being free.** Both
profiles publish the same port. A dev container created while stable still held
it fails to bind and its Java runtime dies, yet the container keeps reporting
`Up (healthy)`: the healthcheck proves only that the port answers, not that
_this_ openHAB serves. A run against that state silently measures the stable
instance and detects `stable`, which reads like a profile misconfiguration
rather than a dead container. `down` the old stack first (volumes persist),
then `restart openhab-dev` so it starts with the port free, and poll `GET
/rest/` for JSON before running.

A run identifies the profile from the live stack: `GET /rest/` must answer with
JSON **and** the runtime version (`runtimeInfo.version`, not the REST API's
top-level `version`, which is `8` on every profile) must equal the pinned tag's
version (`tests/config/openhab.version`: `stable` = 5.2.5, `dev` = 5.3.0.M5). If
the REST API does not answer, or the runtime version matches no pin, the run
**fails** with the observed and expected versions instead of skipping: a dead
container or a foreign image is an environment failure, not another profile.
The check lives in detection rather than `preflight` because `preflight` runs
before the stack exists and cannot see `/rest/`.

`run --profile stable|dev` **asserts** the profile instead of selecting it. It
fails before any scenario when the stack serves the other one, names the served
version and the command that starts the wanted stack, and exits `2`. Omit it to
run whatever is up; a dry run cannot check it because there is no stack.

A scenario whose profile does not match the running stack is **skipped with its
reason**, not failed: the engine asks the running instance which profile it
serves (the pinned runtime version from `runtimeInfo.version` on `/rest/`,
cross-checked against the served item and sitemap sets), and reports

```text
SKIPPED regression/dev-profile-command-confirmation: needs the 'dev' openHAB
profile, but the running stack serves 'stable' (stable: 0 item(s)/0 sitemap(s)
missing, dev: 0 item(s)/1 sitemap(s) missing). Profiles are exclusive, so run
this scenario against the 'dev' stack: set COMPOSE_PROFILES=dev and start
openhab-dev …
```

Every run states what it covered, in the console summary, `report.json`
(`profiles.covered` / `profiles.skipped`) and `summary.md`
(`profiles covered: stable; scenarios needing another profile were skipped: dev
(a complete pass is one run per profile)`). A green `run --all` on one stack
therefore means "everything that _can_ run here passed", not "the suite is
complete"; the two summaries together are the complete result.

**A skipped scenario never counts as coverage.** If a scope entry is covered only
by scenarios that were skipped in this run, the run says so instead of counting
it:

```text
scope entries covered only by skipped scenarios (not exercised by this run):
commands.confirmation
```

`report.json` carries the same list as `unverified_scope`, and `summary.md` adds
it under the profile line. `coverage.md` still describes what the suite covers
when both profiles have run; the run summary describes what _this_ pass actually
exercised, and the two must not be confused.

## Build profiles: the glance and widget modes are not covered

Scenarios are compiled with the `watch-app` profile by default. The app is a
widget, and a widget shown on the glance page starts in glance mode (32 KB
budget) until it is selected; the driver selects it with DOWN then SELECT, which
opens the app. E2E builds still use `watch-app` because a direct launch is the
measured path: the profile writes a generated manifest declaring
`type="watch-app"` and rebases the generated jungle onto a copy of `source/`
whose `GlanceSitemapView.mc` is an empty stub, so the real glance is not
exercised. The `Invalid Font Specified` crash that once justified the exclusion
was the empty device font pack, which the runner now stages; the earlier latch
hypothesis is retired, because the app opens from the glance on DOWN then
SELECT. Whether the real glance renders under `--with-glance` has not been
measured. The checkout is not modified and the `(:glance)` annotations stay as
they are.

`run` prints the profile at the top and for every build, followed by the full
`monkeyc` command. A green run therefore asserts nothing about the widget or
glance modes; those scope entries are **not yet verified** (waived with that
reason, not blocked by a defect) in [coverage.md](coverage.md#summary).
`--without-glance` keeps the widget manifest (the app may still start in glance
mode); `--with-glance` compiles the real glance view when validating on hardware
or when capturing the glance for the first time. The reasoning, including why
`monkeyc -x glance` cannot be used, is in
[simulator-driver.md](simulator-driver.md#glance-the-build-profiles).

## Language matrix

A scenario declares either one `language` or a `languages` list, never both:

```yaml
languages: [eng, deu, fre, ita, spa, pol]
```

The matrix form expands at load time into one scenario per language, with
`-<language>` appended to the name. `extended/localisation-sitemap.yaml` with
that list becomes `extended/localisation-sitemap-eng` through
`extended/localisation-sitemap-pol`, so artifacts and reports do not collide.
The file's `name` is the _base name_: `find_scenarios("localisation-sitemap")`
returns the whole matrix, while `find_scenario` requires one expanded id.

Step parameters may use the `${language}` placeholder. It is replaced with the
expanded language before the run, which keeps a single file assertable against
the app's own locale:

```yaml
- action: wait_for_event
  event: LANGUAGE
  fields:
    code: "${language}"
```

The `LANGUAGE` trace event is emitted by `OHApp.onStart` from
`System.getDeviceSettings().systemLanguage`, so asserting it proves which language
the simulator really started in instead of assuming the requested one. The engine
passes the scenario language to
`SimulatorDriver.start(device, settings, mode, language)`. A driver answers
`can_provision_language(language)` per code; when it cannot, the engine reports
the run as **skipped** (with `skip_reason` in the report) instead of executing it
in the wrong language. The Wine driver provisions a language with the simulator's
numeric `Language` index, taken from the measured map in
`tests/config/simulator-languages.json` (currently `deu: 7`); codes without a
measured index stay blocked until `python -m e2e language-scan --write` measures
them. See [simulator-driver.md](simulator-driver.md#language-is-a-numeric-index)
and the blocked-variant count in [coverage.md](coverage.md).

## Scope coverage

`tests/config/scope.yaml` is the machine-readable form of the issue #140
regression scope. Each bullet has a stable id; a scenario declares the ids it
covers:

```yaml
covers:
  - errors.critical
  - errors.recovery
```

`covers` is optional, but every checked-in scenario declares at least one entry.
The loader rejects an unknown id, a duplicate and an empty list, so a typo fails
at load time instead of silently leaving a scope bullet unproven. The id is
validated against the same file the coverage report uses, not against a copy.

Use `manual_checks` for something the scenario exercises but cannot assert
today, for example a screenshot-only rendering check:

```yaml
manual_checks:
  - The switch must not flip back during the hold window; compare the screenshots.
```

The coverage report shows those checks next to the scope entry, so a scenario
cannot quietly claim more than it asserts.

`tests/docs/coverage.md` is generated from the scope file, the `covers` keys and
`tests/config/devices.yaml`. It lists, per scope id, the scenarios with their
tier and device, or an explicit waiver with a reason. Regenerate and verify it
with:

```bash
cd tests/runner
python -m e2e coverage --write    # rewrite tests/docs/coverage.md
python -m e2e coverage --check    # fail on a gap or a stale document
```

`--check` fails when a scope id has neither a scenario nor a waiver, when a
scenario references an unknown id, and when the checked-in document is stale.
It needs neither Docker nor the Connect IQ SDK, so the `harness` CI job runs it.
A deliberate gap goes in the `waivers` list in `scope.yaml` with a reason; a
waiver for an entry that a scenario covers is an error.

The report says plainly that every scenario is host-only: it needs Linux, Docker,
the SDK and the simulator to prove anything. The CI check proves the mapping, not
a run.

## Steps

| Action | Parameters | Notes |
| --- | --- | --- |
| `launch` | `wait_ready` (bool, default true) | exactly one, first; `wait_ready` also opens the app when the simulator left it on the glance page (see [simulator-driver.md](simulator-driver.md#opening-the-app-from-the-glance-page)) |
| `stop` | – | stops the simulator, keeps artifacts |
| `wait` | `ms` | clamped to the remaining scenario budget |
| `reset_shim` | – | clears the request trace only |
| `install_faults` | `rules` | installs (replaces) fault rules mid-run, same grammar as the top-level `shim.rules`; see [Asserting on an injected fault](#asserting-on-an-injected-fault) |
| `wait_for_request` | `method`, `path`/`path_prefix`, `matched_rule`, `faulted`, `min_count`, `timeout_ms` | polls the shim trace |
| `press` | `button` (`START`/`UP`/`DOWN`/`ENTER`/`BACK`/`MENU`), `count`, `expect` (`input`/`effect`/`none`), `timeout_ms` | requires a button-capable device; `expect` measures the press, see [Measured presses](#measured-presses) |
| `tap` | `x`, `y`, `count` | requires a touch-capable device |
| `screenshot` | `name`, `stable`, `interval_ms`, `timeout_ms`, `stable_diff_ratio` | lower-case slug; stored as `NN-name.png`. `stable: true` recaptures until two frames agree, because a `VIEW` line is emitted before the view is painted |
| `command` | `item`, `value`, `via` (`rest`/`shim`) | `rest` talks to openHAB directly |
| `wait_for_event` | `event`, `fields`, `contains`, `timeout_ms` | see trace events below |
| `wait_for_view` | `category`/`view`, `depth`, `timeout_ms` | shorthand for a `VIEW` event; put it before a screenshot |
| `assert` | `shim_requests`, `golden`, `no_error_view`, `never_error_view`, `description` | at least one condition |

### `assert`

```yaml
- action: assert
  description: Exactly one command is sent, to the selected item.
  shim_requests:
    method: POST
    path_prefix: /rest/items/E2E_Toggle
    min_count: 1
    max_count: 1
    body_equals: "ON"
    status: 200
```

`shim_requests` matches on `method`, `path`, `path_contains`, `path_prefix`,
`body_contains`, `body_equals`, `status`, `reset`, `matched_rule` and `faulted`,
and requires an explicit `min_count` or `max_count`. `path_contains` is the
substring form for a request target including its query string; `path` is exact,
so it cannot be combined with either of the others. A failure prints the
expectation, the observed status/fault counts and the whole trace, so "the fault
was not served" and "the request never arrived" are different messages.

### Command payloads

A REST command is **JSON-wrapped**, not raw: `POST /rest/items/<item>` with
`Content-Type: application/json` and the body `{"value":"ON"}`
(`source/main/data/communications/web-requests/command/NativeCommandRequest.mc`).
openHAB 5.0 and newer accept that; older versions only took raw content, which
the Garmin SDK cannot send, so "fixing" the app back to a raw body would break
the command path on the versions the app supports. Scenarios assert the exact
JSON payload — it is the only thing pinning that contract.

The webhook path is a `GET`, so it has no body: the command travels in the query
as `action=sendCommand&itemName=<item>&command=<cmd>`
(`WebhookCommandRequest`). `webhook-command-path` pins the whole query with
`path_contains` instead of a bare path match.

### Asserting on an injected fault

A recorded request carries what the app **sent** and what the shim **served**,
and nothing else. `body_equals`/`body_contains` apply to the request body, which
is empty for a `GET` poll; the served body is never recorded. A scenario that
means "the shim answered with the malformed body" must therefore assert on the
fault that produced it:

```yaml
- action: assert
  description: The shim served the truncated body instead of openHAB.
  shim_requests:
    method: GET
    path_prefix: /rest/sitemaps/garmin_e2e
    min_count: 2
    status: 200            # what the shim answered with
    matched_rule: truncated # the rule that replaced the upstream response
```

`faulted: true`/`false` asks the same question without naming a rule, and is the
right check for "this poll reached openHAB unmodified".

```yaml
- action: assert
  golden:
    name: homepage
    max_diff_ratio: 0.02
```

Goldens live in `tests/goldens/<device>/<name>.png`, one set per device: a
fenix and an Edge are not comparable images. Capture them with
`--update-goldens`, review the image, then commit it. A golden assertion is
compared against the screenshot captured earlier in the same scenario.

A golden is a **drift detector, not a correctness proof**: a match says the app
still draws what a reviewer once approved on that simulator build. It does not
say the layout, truncation or glyphs are right on a device, and that question
is answered only by a human looking at a capture from Windows or from hardware.
Because the window geometry is simulator-specific, a golden is valid for one
simulator build and is re-captured, not compared, after an upgrade.

**The golden is the whole simulator window, and the app's screen is never
assumed to be centred, square or even inside a known rectangle.** Devices differ
in all three ways, and the window capture handles each without a rectangle:

| Device | Screen shape | Art canvas | Declared `display.location` | Layout |
| --- | --- | --- | --- | --- |
| `fenix7` | round | 409×560 | 260×260 at (77,146) | bezel all round |
| `edge540` | rectangle | 382×518 | 246×322 at (69,78) | rectangle with bezel above and below |
| `edge1050` | rectangle | 752×961 | 480×800 at (136,6) | bezel almost gone at the top |
| `edge1060` | rectangle | 675×984 | 480×800 at (97,1) | **top cut**: 1 px from the top, no bezel above |

`fenix7` and `edge1060` are captured (`tests/goldens/<device>/homepage.png`), and
the two prove the rule at its extremes: a round phone-free watch and a top-cut
rectangle whose display starts one pixel from the top of the art. Each is verified
with the scenario's device override:

```bash
python -m e2e run smoke/launch-and-poll --driver wine --device edge1060
```

`edge1050` (y=6) is **blocked** for a different reason — its `simulator.json`
declares only `start` and `lap` hit areas, with no `up`/`down`/`enter`, so the
harness's DOWN, DOWN, SELECT launch sequence cannot open the app on it at all.
That is a device-definition gap to record, not a golden-design problem;
`edge1060` declares the navigation keys and works.

`display.location` is a **device-space declaration, not window pixels**, and the
two differ measurably: `fenix7` declares 260×260 at (77,146) and renders
309×309 at (53,141) — a ~1.19× scale and an offset that is not a centre fit.
Nothing may derive a capture rectangle from it. That mismatch is why the earlier
screen-crop code was wrong, and why the golden is the window.

Each capture is taken with the pointer parked at a fixed window point (the
simulator's status strip prints the pointer position), so two captures of the
same view differ only by the strip's live counters: measured 157 pixels (0.06%)
against a 0.2% tolerance, whereas a wrong view differs by about 1.6%.

Window geometry is **not** pinned. `WindowX`, `WindowY`, `WindowWidth` and
`WindowHeight` are in `simulator.ini` and were measured ignored on start (a
409×600 window stayed 409×600 whatever they said), and normalising by trimming
and rescaling does not reconcile simulators: Wine and the native build give
39.7% RMSE after trim-and-resize. Together those measurements are the reason a
window golden is valid for **one simulator build**: when the simulator version
changes and the window size changes with it, the goldens must be **re-captured
deliberately** — the size check in the comparison is what tells you to, and it
reports the old and new dimensions explicitly instead of failing obscurely.

A golden must also declare the state it depicts, wait for it to be **painted**,
and contain rendered content:

```yaml
- action: wait_for_view          # the request is recorded before the menu paints
  category: sitemap
  view: HomepageMenu
- action: screenshot
  name: homepage
  stable: true                   # capture until two consecutive frames agree
- action: assert
  golden:
    name: homepage
    max_diff_ratio: 0.002
    expect:                      # required: the capture must be this view
      category: sitemap
      view: HomepageMenu
    min_ink_components: 40       # required: measured on a reviewed capture
```

`E2E:VIEW` is emitted when a view is **constructed**, not when it is painted, so
`wait_for_view` alone is not enough: the first attempt at this still captured the
glance icon with the menu already reported. `stable: true` captures repeatedly
until two consecutive frames differ by less than `stable_diff_ratio` (0.1% by
default), which measures the paint instead of guessing a sleep; the guest settled
in two attempts, 153 pixels apart.

`expect` is mandatory and checked against the live trace before anything is
written or compared: a capture of the glance, a loading view or any other state
fails closed. `min_ink_components` is the pixel backstop — a count of distinct
ink blobs — measured on review; on a whole window the states are close (glance
64, menu 76) so the view check is the enforcement and this floor only catches a
window that painted nothing at all.

Capturing needs the Connect IQ device font pack: the harness refuses
`--update-goldens` when `$HOME/.Garmin/ConnectIQ/Fonts` is missing, empty or has
no `.cft`/`.ttf` faces, because the app would crash with `Invalid Font Specified`
and the capture would freeze that crash as the expected state. The pack is
licensed Garmin content: mount or copy it, never commit it. See
[sdk.md](sdk.md#device-fonts).

A golden is therefore only valid if it was captured where the pack was present.
Without it the app crashes on its first draw, so a capture taken in a broken
environment locks a **blank screen** in as the reference every future run is
measured against — exactly the failure the capture-time guard exists to prevent.

A golden must also be **re-captured**, not compared, whenever something that
shapes the pixels changes: the device definition in `Devices/<device>`, the
app's theme resources, or the fixture sitemap the scenario renders. Otherwise a
correct test fails for a correct reason, and the tempting "fix" is to regenerate
the baseline without looking. Capture on purpose, review the image, and say in
the commit why the baseline moved.

`no_error_view` is a statement about the view the app is showing _now_, not
about the whole run:

```yaml
- action: assert
  description: The error view was dismissed and the sitemap is back.
  no_error_view: true
```

`no_error_view: true` fails when the current view is the error view;
`no_error_view: false` fails unless the error view is the current view. The
current view is the `category` of the newest `VIEW` trace event, so an error
view that was shown and then dismissed satisfies `true` again. That is what
makes a recovery assertion — critical error, then a valid response, then the
sitemap — provable instead of screenshot-only.

`never_error_view: true` is the historical check, for scenarios that must prove
an error view never appeared at all, for example a healthy run or a temporary
error that must stay a toast:

```yaml
- action: assert
  description: A command error must never raise the full-screen error view.
  never_error_view: true
```

It fails when any `ERRORVIEW` event was observed, even if the error view was
dismissed later. Use it when "it never happened" is the point, and
`no_error_view` when "it is not happening now" is the point. `ERRORVIEW` is
emitted when the app shows _and_ when it updates the error view, and never when
it dismisses it, so it cannot answer the current-state question on its own.

### Faults after a warm-up

The escalation rule is measured against the age of the last successful response
(`SitemapStore.isSitemapFresh`, `STATE_EXPIRATION_TIME = 10 s`), not against a
failure counter. Two consequences:

- A **cold start has no stored sitemap**, so the first failed poll is already
  stale and goes straight to the full-screen error view. A scenario that wants
  to observe a _temporary_ toast must let the app complete one successful poll
  first: warm up with `wait_for_event` on `SITEMAP;result=success`, then
  `install_faults`.
- A scenario that asserts the **critical** error view can keep the pre-launch
  `shim` block; the fault then hits the first poll.

`regression/empty-response` and `regression/http-503-recovery` warm up first;
`regression/malformed-json` does not, and says so at the top of the file.

## Trace events

The app emits, in debug builds only, one line per event:

```text
E2E:<EVENT>;field=<value>;field=<value>
```

| Event | Fields (in emission order) |
| --- | --- |
| `VIEW` | `category` (`sitemap`/`settings`/`error`/`sync`/`glance`), `view`, `depth` |
| `CONNECTION` | `mode` (`BLE`/`WIFI_CHECK_PENDING`/`WIFI`/`OFFLINE`) |
| `SITEMAP` | `result` (`success`/`unchanged`/`error`), `code`, `bytes` |
| `COMMAND` | `result` (`success`/`error`), `code`, `item`, `value` |
| `INPUT` | `button` (`select`/`back`/`next-page`/`previous-page`), `view`, `depth` |
| `SELECT` | `kind` (element type), `result` (`pushed`/`empty-page`/`page-not-ready`/`no-action`/`offline`/`command`/`settings`), `depth` |
| `ERROR` | `kind` (`temporary`/`critical`/`fatal`), `id`, `code`, `source` (`sitemap`/`command`/`ui`) |
| `TOAST` | `id` |
| `ERRORVIEW` | `id`, `code` |
| `LANGUAGE` | `code` (`eng`/`deu`/... or `unknown`) |

`code` is an HTTP status, an SDK error code (possibly negative, or prefixed such
as `S:-104` or `UI:CONFIG`), or `-` when unavailable. `COMMAND.item` and
`COMMAND.value` are percent-encoded (space, `|`, `%`, CR, LF); a scenario may
assert either the encoded or the human-readable value. Unknown events and fields
in the stream are ignored; unknown _field names in a scenario_ for a known event
are rejected at load time.

The parser (`e2e/trace.py`) splits on `;`, then on the first `=`, keeps unknown
events and fields, and timestamps each line on receipt. Lines that are not trace
lines are ignored. Assertions must use structured fields, not rendered text: the
toast text is localised and is not a stable assertion surface.

`VIEW` is emitted on every view-stack change (push, pop, switch and reset), so
the newest `VIEW` event describes the view that is on screen now — including the
change that dismisses the error view. `ERRORVIEW` only records that the error
view was shown or updated, never that it was cleared. `TraceLog` exposes this
split as `current_view_category()`/`is_showing_error_view()` against
`has_error_view()`.

```yaml
- action: wait_for_event
  event: ERROR
  fields:
    kind: critical
  timeout_ms: 30000
```

`contains` remains available for a raw substring, but it is discouraged outside
diagnostics.

### What `SITEMAP;result=unchanged` does and does not say

`result=unchanged` is emitted only after a complete sitemap JSON was received,
parsed and compared equal to the stored one. Its `code=200` is a **literal in
the trace format**, not the HTTP status of that response, and `bytes` is the
app's memory estimate for the parsed JSON, not the size on the wire. A trace
line reading `SITEMAP;result=unchanged;code=200;bytes=27168` therefore does not
prove the app received a 200 or a 27 KiB body; a rejected or failed response is
traced as `SITEMAP;result=error` plus an `ERROR` event instead.

### Reading a failed wait

A `wait_for_event`/`wait_for_view` failure prints the per-event counts, the last
events of the class it was waiting for (with the values a near-miss field
actually carried) and the last few events overall:

```text
no trace event matching event=ERROR kind=temporary within 30.0s
  trace: SITEMAP x11, VIEW x1, ERROR x1
  last ERROR event(s):
    E2E:ERROR;kind=critical;id=communication.sitemap;code=503;source=sitemap
  ERROR.kind seen: critical (expected 'temporary')
  last events:
    ...
```

That is the difference between "the app never emitted an `ERROR`" and "it
emitted a different kind", which a tail of the last dozen lines cannot show.
A `wait_for_request` timeout prints the same event counts and the last
`SITEMAP`/`COMMAND`/`ERROR` event next to the app's current view.

## Determinism rules

- Prefer trace assertions over screenshots; use goldens only to prove rendering.
- One behaviour per scenario. A scenario that fails should name one thing.
- Never assert on wall-clock timings; assert on counts and event ordering.
- Keep the fixture generic. Device-specific rendering belongs to the scenario's
  device selection, not to a separate sitemap.
- Every `shim` rule must have a `name`, so the failure message can say which fault
  was active.

## Presses and input delivery

A press is only evidence when it reached the app. Three faults look identical in
a screenshot: the simulator never delivered the press, the app received it
without the menu delegate being called, and the delegate ran on an item that
opened nothing.

`press` can therefore measure itself:

```yaml
- action: press
  button: ENTER
  expect: input    # or: effect | none
```

### Measured presses

```yaml
- action: press
  button: ENTER
  expect: input    # or: effect | none
```

`expect: input` requires the app's own evidence that it handled the press: an
`INPUT`, `SELECT` or `COMMAND` trace event. `expect: effect` accepts that **or**
a screen change measured from a _settled_ baseline (`SETTLE_DIFF_RATIO`); an
unsettled screen is not a baseline, so a press that lands while the app is still
painting proves nothing. A view change alone is not delivery either — the app
arriving on a page changes the view — which is why `input` exists and why the
scenarios that must reach the menu delegate use it. `expect: none` requires that
the press do nothing; without `expect` the press is not measured, which is fine
when the next step asserts the effect anyway.

A press that fails its expectation reports the mechanism the driver used, each
attempt's evidence, and the app's current state, so a dead input path fails where
the press happens instead of in a later timeout.

#### A press that was not acknowledged is re-issued once

The guest loses the first click after a view appears, so `press_measured`
re-issues a press that did not satisfy `expect` — once, and never one that did.
Every attempt is kept in the step message:

```text
attempt 1: mouse (… (378,180)); effect=none; settled=True; no app event
attempt 2: mouse (… (378,180)); effect=view sitemap/PageMenu and app INPUT 1, SELECT 1; settled=True; INPUT x1, SELECT x1
```

Those two lines are the verdict. No app event on an attempt means the press never
reached the app; `INPUT x1` without `SELECT x1` means it did and the item did not
open anything. Without the counts, a lost click and a select that opened nothing
would need the same fix.

A repaint used to be enough for `expect: effect`, and that masked exactly this: an
`ENTER` sent while the homepage was still being built changed 4337 px — the app
finishing its own render — and was reported as a delivered press with
`INPUT x0, SELECT x0`. It no longer counts, and the scenarios wait for
`HomepageMenu` before pressing.

Two trace events separate "no input" from "input with no effect":

- `E2E:INPUT;button=<select|back|next-page|previous-page>;view=...;depth=...`
  is emitted by the menu input delegate, so it only appears when the app
  received the press. Scrolling between entries is handled inside `Menu2` and
  never reaches the delegate: a `DOWN` that moves the highlight is observable
  only as a repaint, which is what `expect: effect` measures. The page buttons
  are reported as `next-page`/`previous-page` when the highlight runs past the
  first or last entry.
- `E2E:SELECT;kind=<element type>;result=<outcome>;depth=...` is emitted when a
  menu item handled a select, and says what it decided. `INPUT` without
  `SELECT` means the press arrived but no item handled it; `result=page-not-ready`
  names a container that had no page to open; `result=empty-page` precedes the
  "empty page" toast.

Two probes exercise this on a healthy homepage and fail loudly, so an input-path
regression names itself:

- `smoke/input-delivery` — DOWN first (proves delivery on pixels), then ENTER,
  then `INPUT`/`SELECT`/`PageMenu`;
- `smoke/input-delivery-first-press` — ENTER as the very first in-app input, the
  order the command scenarios use, with the attempt record naming whether the
  first click was lost.

They are harness probes, not app behaviour:
`python -m e2e run smoke/input-delivery`.

## Navigation rules

- **A `Frame` is a submenu.** `MenuItemFactory` turns every `SitemapFrame` (and
  `SitemapGroup`) into a `ContainerMenuItem`, so on the homepage one select
  **opens** the frame page and a second select activates its first element.
  Scenarios that need an element inside a frame therefore press twice, with a
  `wait_for_event` on `VIEW;view=PageMenu` between them:

  ```yaml
  - action: press
    button: ENTER
  - action: wait_for_event
    description: The frame page is rendered before selecting its element.
    event: VIEW
    fields: {category: sitemap, view: PageMenu}
    timeout_ms: 30000
  - action: press
    button: ENTER
  ```

- **Count every entry, including static text.** A `Text` with no item and no
  children still occupies a menu index even though it cannot be activated. When
  paging by count, count it, and say the order in a comment.
- **Wait for the page before paging.** The app builds a frame's children
  asynchronously after the page is shown (see
  `localisation-error-long-value`), so a `DOWN` sent immediately can run against
  a half-built menu. Wait for the `PageMenu` view first.
- **Prefer a dedicated fixture page over a long page plus counting.** A sitemap
  per scenario boundary (as `garmin_commands` and `garmin_dynamic` do after
  #400) makes the first element deterministic; growing one page until a scenario
  needs seven page-downs is how navigation drifts silently.

## Adding a scenario

1. Pick the suite: `smoke` (does the stack work), `regression` (documented behaviour
   that once broke), `extended` (long-running or fault-heavy).
1. Choose the tier: `protocol` if the shim trace and trace events can prove it;
   `ui` if rendering is the point.
1. Use `languages` instead of `language` when the behaviour must hold in every
   shipped language, and assert `LANGUAGE;code=${language}`. A language matrix
   of a `ui` scenario costs one simulator run per language; keep the rest of the
   scenario deterministic so the only variable is the locale. Only `eng` is
   runnable today (see the language matrix section above).
1. Reuse an existing fixture. Add items only if the sitemap needs them, and keep
   `fixtures/openhab/conf/items` and `fixtures/openhab/conf/sitemaps` in sync —
   `python -m e2e setup` fails when a sitemap refers to an undefined item.
1. Add `covers` with the #140 scope ids the scenario proves, and
   `manual_checks` for anything it exercises but cannot assert. Update
   `tests/config/scope.yaml` first if the bullet has no id yet.
1. Run `python -m pytest` in `tests/runner`: the new file is loaded and validated by
   `test_repository_scenarios_load`, and `test_coverage.py` checks the mapping.
1. Run `python -m e2e coverage --write` to refresh `tests/docs/coverage.md`.
1. Run it in dry-run to check the wiring, then against the stack.
