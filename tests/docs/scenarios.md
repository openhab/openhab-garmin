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
timeout_s: 90                  # whole-scenario budget, 1..1800
steps: [...]                   # non-empty; the first step must be `launch`

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
| `assert` | `shim_requests`, `golden`, `no_error_view`, `description` | at least one condition |

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

`no_error_view: true` fails if the app reported an `ERRORVIEW` trace event;
`no_error_view: false` asserts the opposite.

## Trace events

The app emits, in debug builds only, one line per event:

```text
E2E:<EVENT>;field=<value>;field=<value>
```

| Event | Fields (at the time of writing) |
| --- | --- |
| `VIEW` | `category`, `view` |
| `CONNECTION` | `mode` |
| `SITEMAP` | `result`, `code`, `bytes` |
| `COMMAND` | `result`, `code` |
| `ERROR` | `kind` (`temporary`/`critical`/`fatal`), `id`, `code` |
| `TOAST` | `id` |
| `ERRORVIEW` | `id`, `code` |
| `LANGUAGE` | `code` |

The parser (`e2e/trace.py`) splits on `;`, then on the first `=`, keeps unknown
events and fields, and timestamps each line on receipt. Lines that are not trace
lines are ignored. Assertions must use structured fields, not rendered text: the
toast text is localised and is not a stable assertion surface.

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
1. Reuse an existing fixture. Add items only if the sitemap needs them, and keep
   `fixtures/openhab/conf/items` and `fixtures/openhab/conf/sitemaps` in sync —
   `python -m e2e setup` fails when a sitemap refers to an undefined item.
1. Run `python -m pytest` in `tests/runner`: the new file is loaded and validated by
   `test_repository_scenarios_load`.
1. Run it in dry-run to check the wiring, then against the stack.
