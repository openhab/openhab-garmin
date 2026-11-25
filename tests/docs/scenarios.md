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
timeout_s: 90                  # whole-scenario budget, 1..1800
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

Unknown devices, tiers, modes, languages, suites, actions, step keys, settings
property ids and shim rule keys are all rejected at load time with the file, the
key and the reason.

The `openhab.profile` field selects which fixture tree the scenario expects:
`stable` uses `fixtures/openhab/conf`, `dev` additionally uses the
`fixtures/openhab/conf-dev` overlay. It is not a URL selector; both profiles
serve on the URL Compose gives the runner. A scenario whose profile does not
match the running stack fails the fixture guard with the missing sitemap or item
named, instead of failing later on a missing page.

## Build profile: the glance is not covered

Scenarios are compiled with the `without-glance` profile by default. The glance
is what the simulator draws first, and it crashes the app before any request
(`Invalid Font Specified` in `GlanceSitemapView.onUpdate`, issue
[#396](https://github.com/openhab/openhab-garmin/issues/396)). The profile
rebases the generated jungle onto a copy of `source/` whose
`GlanceSitemapView.mc` is an empty stub, so the app reaches its initial sitemap
view and starts polling. The checkout is not modified and the `(:glance)`
annotations stay as they are.

`run` prints the profile at the top and for every build, followed by the full
`monkeyc` command. A green run therefore does not assert anything about the
glance; the glance scope entries are **blocked** in
[coverage.md](coverage.md#summary). Re-include the real glance with
`--with-glance` (for `run` or `build`) when validating on hardware or after
issue #396 is resolved. The reasoning, including why `monkeyc -x glance` cannot
be used, is in
[simulator-driver.md](simulator-driver.md#glance-the-profile-that-keeps-runs-alive).

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
`SimulatorDriver.start(device, settings, mode, language)`. A driver declares
`provisions_language`; when it is false and the language is not `eng`, the engine
reports the run as **skipped** (with `skip_reason` in the report) instead of
executing it in the wrong language. The Wine driver cannot provision a device
language, so only `eng` runs today; the measurements are in
[simulator-driver.md](simulator-driver.md#language-cannot-be-provisioned) and
[coverage.md](coverage.md) marks the rest blocked.

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
| `launch` | `wait_ready` (bool, default true) | exactly one, first |
| `stop` | – | stops the simulator, keeps artifacts |
| `wait` | `ms` | clamped to the remaining scenario budget |
| `reset_shim` | – | clears the request trace only |
| `wait_for_request` | `method`, `path`/`path_prefix`, `min_count`, `timeout_ms` | polls the shim trace |
| `press` | `button` (`START`/`UP`/`DOWN`/`ENTER`/`BACK`/`MENU`), `count` | requires a button-capable device |
| `tap` | `x`, `y`, `count` | requires a touch-capable device |
| `screenshot` | `name` | lower-case slug; stored as `NN-name.png` |
| `command` | `item`, `value`, `via` (`rest`/`shim`) | `rest` talks to openHAB directly |
| `wait_for_event` | `event`, `fields`, `contains`, `timeout_ms` | see trace events below |
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

`shim_requests` matches on `method`, `path`, `path_prefix`, `body_contains`,
`body_equals`, `status` and `reset`, and requires an explicit `min_count` or
`max_count`.

```yaml
- action: assert
  golden:
    name: homepage
    max_diff_ratio: 0.02
```

Goldens live in `tests/goldens/<device>/<name>.png`. Capture them with
`--update-goldens`, review the image, then commit it. A golden assertion is
compared against the screenshot captured earlier in the same scenario.

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

## Determinism rules

- Prefer trace assertions over screenshots; use goldens only to prove rendering.
- One behaviour per scenario. A scenario that fails should name one thing.
- Never assert on wall-clock timings; assert on counts and event ordering.
- Keep the fixture generic. Device-specific rendering belongs to the scenario's
  device selection, not to a separate sitemap.
- Every `shim` rule must have a `name`, so the failure message can say which fault
  was active.

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
