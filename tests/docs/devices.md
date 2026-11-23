# Device matrix and variant coverage

The E2E suite runs on a representative subset of the 93 products declared in
`manifest.xml`. The subset is derived from `monkey.jungle`: every product is
reduced to a build-variant tuple, and `tests/config/devices.yaml` must assign at
least one device to every tuple that occurs. This is enforced by
`tests/runner/tests/test_variants.py`, which needs neither Docker nor the
Connect IQ SDK.

The matrix answers one question: does a build variant exist that no E2E run
exercises? It does not claim that every supported watch model was tested.

## Dimensions

`tests/runner/e2e/variants.py` resolves the jungle and produces these values:

| Dimension | Values | Source in `monkey.jungle` |
| --- | --- | --- |
| `ciq_tier` | `pre400`, `400plus`, `510plus` | `annotCiqPre400`, `annotCiq400Plus`, `annotCiq510Plus`; the most specific marker wins because `annotCiq510Plus` also carries `exclForCiq400Plus` |
| `input` | `button`, `touch` | `annotButton`, `annotButtonWithSettingsMenuItem`, `annotTouch` |
| `screen` | `round`, `rectangular` | `annotScreenRound`, `annotScreenRectangular` |
| `menu` | `actionmenu`, `fullmenu` | `annotActionMenu`, `annotFullMenu` |
| `edge_resources` | `none`, `short`, `tall` | `resources/edge/icons-interface-short` and `-tall` in `resourcePath` |
| `settings_entry` | `footer`, `menu-item` | `exclForSettingsFooter` (button default) vs `exclForSettingsMenuItem` (touch, or button Edge with the issue #253/#260 workaround) |
| `config` | `default`, `fenix6pro`, `venu2sq`, `venux1`, `25deg-select`, `27deg-select`, `no-left-padding`, `no-left-padding-25deg`, `no-left-padding-27deg`, `edge-x40-touch`, `edge-x40-button`, `edge-x50-touch`, `edge-x50-button`, `edge-x50-550`, `edge-x50-850`, `edge-x50-1060` | `source/config/*` directories in `sourcePath` |

These are the names mapped in `CONFIG_VARIANTS`; only the ones that occur in the
jungle appear in the coverage table below. `27deg-select` and
`edge-x50-button` are mapped but currently unused.

`input` is the compiled annotation, not the hardware. A watch with a touch
screen that builds the button annotations (fenix 7, fr965, edge 850) is a
`button` variant; "touch and buttons both work" is a device capability recorded
by the `input:` field in `devices.yaml` and exercised by UI scenarios.

`config` names are deliberately explicit in `CONFIG_VARIANTS`. A new
`source/config` directory fails resolution until it is named there, which forces
a deliberate matrix decision instead of silently creating an uncovered variant.

## Resolution

For each product in `manifest.xml`, the resolver:

1. Parses `key = value` lines and comments from `monkey.jungle`.
2. Expands `$(name)` recursively, including references to other device sections
   such as `venu3.excludeAnnotations = $(approachs50.excludeAnnotations)`, and
   fails on unknown names and cycles.
3. Applies the `base.*` defaults (`excludeAnnotations`, `sourcePath`) when a
   device does not define the property itself.
4. Splits `;`-separated annotation, resource and source lists.
5. Reduces the result to the seven dimensions above.

The manifest and jungle must declare the same product set: a product added to
one and not the other fails the check. The current figures are 93 products and
17 distinct combinations.

`screen` values in `devices.yaml` come from `display.location` in the Connect IQ
SDK 9.3.0 device definitions, the same data the simulator uses. They are not
part of variant coverage but are needed for tap coordinates.

## Coverage table

One device per combination; no combination is waived.

| CIQ | input | screen | menu | edge | settings | config | products | device |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 400plus | touch | rectangular | actionmenu | none | menu-item | venu2sq | 2 | venusq2 |
| 400plus | touch | rectangular | fullmenu | tall | menu-item | edge-x40-touch | 1 | edgeexplore2 |
| 400plus | touch | round | actionmenu | none | menu-item | default | 4 | venu2 |
| 510plus | button | rectangular | fullmenu | short | menu-item | edge-x40-button | 3 | edge540 |
| 510plus | button | rectangular | fullmenu | short | menu-item | edge-x50-550 | 1 | edge550 |
| 510plus | button | rectangular | fullmenu | short | menu-item | edge-x50-850 | 1 | edge850 |
| 510plus | button | rectangular | fullmenu | tall | menu-item | edge-x50-1060 | 1 | edge1060 |
| 510plus | button | round | actionmenu | none | footer | default | 29 | fenix7 |
| 510plus | button | round | actionmenu | none | footer | no-left-padding | 17 | fenix847mm |
| 510plus | button | round | actionmenu | none | footer | no-left-padding-25deg | 3 | fr965 |
| 510plus | button | round | actionmenu | none | footer | no-left-padding-27deg | 5 | fr265 |
| 510plus | touch | rectangular | actionmenu | none | menu-item | venux1 | 1 | venux1 |
| 510plus | touch | rectangular | fullmenu | tall | menu-item | edge-x40-touch | 1 | edge1040 |
| 510plus | touch | rectangular | fullmenu | tall | menu-item | edge-x50-touch | 3 | edge1050 |
| 510plus | touch | round | actionmenu | none | menu-item | default | 9 | venu3 |
| pre400 | button | round | actionmenu | none | footer | 25deg-select | 1 | fr945lte |
| pre400 | button | round | actionmenu | none | footer | fenix6pro | 11 | fenix6pro |

`python -m e2e variants` prints this table from the current files; the table
above is the expected output, not a duplicate source of truth.

## Tiers

Every matrix device runs the `protocol` tier: it is cheap and asserts on the
shim request trace and on `E2E:` trace events, so it proves that the compiled
variant starts and talks to openHAB.

The `ui` tier is expensive (menu navigation plus screenshot goldens), so it runs
on six devices that cover every interaction and viewport value:

| Device | Covers in the UI tier |
| --- | --- |
| fenix6pro | pre-4.0, button-only, round, ActionMenu, no Edge resources, settings footer |
| venu2 | 4.0-5.0, touch-only, round, ActionMenu, no Edge resources, settings menu item |
| fenix7 | 5.1+, button build with touch, default Config layout; the matrix default |
| fenix847mm | 5.1+, left-aligned menu layout, 454 px round screen, touch and buttons |
| edge540 | 5.1+, button Edge, rectangular, full menu, short resources |
| edge1050 | 5.1+, touch Edge, rectangular, full menu, tall resources |

The coverage test enforces that every value of `ciq_tier`, `input`, `screen`,
`menu`, `edge_resources` and `settings_entry` appears on at least one UI device.

`config` is intentionally not a UI requirement. The protocol tier represents
every config variant, while UI goldens would have to be captured and reviewed
for every config variant that occurs, including input-hint-only differences.
Promote a config variant to the UI tier when a regression is suspected there; do
not do it as routine coverage.

## Waivers

A combination with no device can be waived in `devices.yaml`:

```yaml
waivers:
  - variants:
      - ciq_tier:510plus
      - input:button
      - screen:round
      - menu:actionmenu
      - edge_resources:none
      - settings_entry:footer
      - config:default
    reason: >-
      Explain why no E2E device can run this combination.
```

A waiver must list all seven dimensions, must match a combination that exists in
`monkey.jungle`, must have a reason, and must not duplicate a combination that a
device already covers. Stale and unnecessary waivers fail the check so they do
not accumulate.

The approved matrix currently has no waivers: one device per combination is
affordable because the protocol tier is cheap.

## Capabilities that are not variants

- **Touch and buttons on the same device.** A capability, recorded by
  `input: touch+button`. It does not change the compiled variant, which stays
  `button` for these devices.
- **Wi-Fi and offline operation.** Harness modes (`mode: wifi`, `mode: offline`)
  driven by app settings, not hardware detection. Any 5.1+ device can run them;
  the scenarios that need them declare the mode.
- **Glance.** Glance code is guarded by the `(:glance)` annotation and compiled
  only for devices that support it. The pre-4.0 rows exercise the builds without
  it, so no separate device is needed.

Do not add a `variants:` tag for a capability: the coverage check treats an
unknown dimension as an error.

## Simulator and physical devices

Variant coverage is simulator coverage. All 17 devices are defined in the SDK
9.3.0 simulator and need no physical hardware. Physical-device-only checks are
not invented: if one is added later, record it in the device's `notes` and do
not count it towards variant coverage until it is modeled as a tier or waiver.

## Re-evaluation rule

Re-run the check and update this document when any of these change:

- `manifest.xml` gains or loses a product;
- `monkey.jungle` gains a device section, annotation, Edge resource marker or
  `source/config` path;
- `tests/config/devices.yaml` changes;
- the pinned SDK changes the device definitions (re-verify `screen` values).

The check fails, rather than silently narrowing coverage, when:

- a manifest product has no jungle section, or the reverse;
- a combination has no device and no waiver;
- a device's `variants:` tuple differs from the jungle (including after an
  inherited section such as `venu3` or `edge840` changes);
- a `source/config` directory has no name in `CONFIG_VARIANTS`;
- a waiver is stale, unnecessary or incomplete;
- a UI-relevant dimension value has no UI device.

The product count assertion in `test_variants.py` is intentional: adding devices
should require re-reading this page and updating the table.

## Running the check

```bash
cd tests/runner
python -m pytest -q tests/test_variants.py   # the automated coverage check
python -m e2e variants                        # human-readable table, exit 1 on gaps
python -m e2e variants --json                 # machine-readable document
```
