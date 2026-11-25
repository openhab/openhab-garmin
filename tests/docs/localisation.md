# Localisation scenarios

These scenarios check that the app's own user-facing strings render in every
shipped language: six bundles (`eng`, `deu`, `fre`, `ita`, `spa`, `pol`), 94
keys each (85 runtime plus 9 settings-scope). They cover normal sitemap chrome, the settings menu, toasts,
and full-screen errors, with accented and non-ASCII characters, and they look
for truncation, clipping and meaning-hiding ellipses.

This page records what is covered, what the harness can actually assert, and how
to extend it. Issue [#140](https://github.com/openhab/openhab-garmin/issues/140)
tracks the work; [#366](https://github.com/openhab/openhab-garmin/issues/366) owns
the device matrix. The scenarios choose devices but do not modify
`config/devices.yaml` or the variant resolver.

## What runs

Every localisation scenario declares the matrix once:

```yaml
languages: [eng, deu, fre, ita, spa, pol]
```

and the loader expands it into one run per language (`...-eng` through
`...-pol`). The `${language}` placeholder in step parameters is replaced for
each run, which is what makes a single file assert `E2E:LANGUAGE;code=<lang>`.
The schema is documented in [scenarios.md](scenarios.md).

A language is only runnable once its index is measured: the simulator stores the
device language as a numeric `simulator.ini` value (`Language=7` is German), so
the driver writes that index and fails the run when the app reports another
language. The map is `tests/config/simulator-languages.json` and
`python -m e2e language-scan --prg <built.prg> --write` measures the rest; until
a code has an index its runs are reported **blocked**, not guessed. See
[simulator-driver.md](simulator-driver.md#language-is-a-numeric-index).

| Scenario | Surface | Machine-asserted | Review-only |
| --- | --- | --- | --- |
| `localisation-sitemap` | homepage title, frame labels, control labels, connection indicator, Settings entry, Player control view | language, views, no error view | text fit and wrapping |
| `localisation-settings-menu` | all seven settings entries, two-line Wi-Fi item, server URL and sitemap values | language, settings view, no error view | text fit and wrapping |
| `localisation-toast-empty-page` | `EmptyPage` info toast | language, TOAST, no error view | toast text and fit |
| `localisation-toast-offline-command` | offline command toast | language, `TOAST;id=UI:03`, no POST | toast text and fit |
| `localisation-error-config-url` | `ErrorConfigUrlMissing` full-screen error on `edge540` | language, `ERRORVIEW;id=UI:CONFIG` | wrapping and clipping |
| `localisation-error-config-url-round` | the same string on the round `fenix7` | language, `ERRORVIEW;id=UI:CONFIG` | wrapping inside the inscribed square |
| `localisation-error-memory-class` | memory-class critical error (`S:-402`, `ErrorSitemapTooLargeDetail`) | language, `ERROR;kind=critical`, `ERRORVIEW;id=S:-402` | wrapping and clipping |
| `localisation-error-multiline` | multi-line detail (`-400` / `ErrorInvalidResponseDetail` family) | language, critical error, ERRORVIEW exists | the newline layout and text |
| `localisation-error-long-value` | long dynamic label in `ErrorElementNotSupported` (dedicated `garmin_localisation_long` sitemap) | language, ERRORVIEW exists | wrapping of the long value |
| `localisation-glyphs` | connection indicator, empty-page toast, Player hints | language, views, TOAST, no error view | every glyph in those screens |

The empty page in `garmin_localisation.sitemap` is a `Frame` without a block, not
an empty `{}` block: the sitemap DSL rejects a linkable block with no child, and
openHAB serializes a childless `Frame` as `"widgets": []`. The app treats that
empty array as a page with no content and shows the `EmptyPage` toast, so the
scenario still exercises the intended path.

The language runs are wired to prove `LANGUAGE` comes from
`System.getDeviceSettings().systemLanguage`; a `wait_for_event` on
`LANGUAGE;code=<lang>` fails when the app reports another language.

> **Blocked: the harness cannot provision a language.** The simulator applies
> its device language from its own UI. `Language` in `simulator.ini` is written
> and preserved but not applied, and `LANG`/`LANGUAGE`/`LC_ALL` make no
> difference — the app reports `eng` in every case, and `CIQ_LOG.YML` confirms
> `Language-Code: eng` (measurements in
> [simulator-driver.md](simulator-driver.md#language-cannot-be-provisioned)).
> The engine therefore reports the `deu`/`fre`/`ita`/`spa`/`pol` runs as
> **skipped** with a reason, and [coverage.md](coverage.md) marks them
> **blocked** rather than counting them as evidence. Only `eng` is runnable
> until the simulator's Simulation menu (device settings) or a persistence file
> it reads becomes available.

## Strings covered

Runtime strings, as opposed to settings-scope strings (see below):

| Area | String ids |
| --- | --- |
| Sitemap chrome and control view | `Loading`, `NotAvailable`, `Settings`, `ConnectionModePhoneBle`, `ConnectionModeWifiChecking`, `ConnectionModeWifi`, `ConnectionModeOffline`, `ConnectionModeInvalid`, `PlayerPlay`, `PlayerPause`, `PlayerNext`, `PlayerPrevious` |
| Settings menu | `Settings`, `SettingsAppVersion`, `SettingsServer`, `SettingsSitemap`, `SettingsConnectIqVersion`, `SettingsConnectivityMode`, `SettingsLastSitemapUpdate`, `SettingsUpdateSitemap` |
| Toasts | `EmptyPage`, `ErrorOfflineCommand` |
| Longest errors | `ErrorConfigUrlMissing` (130 French, 129 Spanish, 120 German/Italian), `ErrorSitemapTooLargeDetail` (memory-class path) |
| Multi-line errors | `SettingsUpdateSitemap`, `ErrorSitemapTooLargeDetail`, `ErrorInvalidResponseDetail` |
| Dynamic values | the long label in `ErrorElementNotSupported` |
| Glyph carriers | `EmptyPage`, connection modes, player commands, settings labels, error messages |

`scope="settings"` strings are rendered by the phone app in Garmin Connect IQ,
not on the watch. They are deliberately excluded from watch truncation checks:
`AppSettingUrl`, `AppSettingSitemapName`, `AppSettingRestApi`,
`AppSettingWebhook`, `AppSettingUserName`, `AppSettingPassword`,
`AppSettingSuppressEmptyResponses`, `AppSettingPollingInterval`,
`AppSettingPostCommandHoldTime`.

The longest runtime strings are the truncation risks: German
`ErrorOutOfMemory` (124), German `ErrorConfigUrlMissing` (120), French
`ErrorConfigUrlMissing` (130), French `ErrorOutOfMemory` (122), Spanish
`ErrorConfigUrlMissing` (129), Italian `ErrorConfigUrlMissing` (120), Polish
`ErrorOutOfMemory` (108). The config-URL and memory-class scenarios put those
messages in the full-screen `ErrorView`.

Five strings contain intentional line breaks (`&#10;`):
`SettingsUpdateSitemap`, `ErrorNoPhoneDetail`, `ErrorInvalidResponseDetail`,
`ErrorSitemapTooLargeDetail` and `ErrorRequestFailedDetail`. The settings
scenario covers the first, the multiline scenario the third, and the
memory-class scenario the fourth. `ErrorNoPhoneDetail` (the `-104` no-phone
path) is not reachable through the shim, and `ErrorRequestFailedDetail` is
exercised only in English by `regression/http-503-recovery`; both are called
out as residual gaps below rather than claimed as covered.

## Glyph coverage

The translated runtime strings use these characters:

| Language | Non-ASCII characters | Main carriers |
| --- | --- | --- |
| `eng` | none | control group: proves nothing is substituted |
| `deu` | `Ö ß ä ö ü` | `Loading` (`Lädt`), `SettingsUpdateSitemap` (`über`), `PlayerPrevious` (`Zurück`), `ErrorConfigUrlMissing` (`Öffne`), `ErrorOutOfMemory` (`nötig`) |
| `fre` | `À É à è é ê` | `Settings` (`Paramètres`), `SettingsUpdateSitemap` (`à jour`), `ConnectionModePhoneBle` (`Téléphone`), `PlayerPrevious` (`Précédent`), `ErrorInvalidResponseDetail` (`Réponse`) |
| `ita` | `à è` | `SettingsConnectivityMode` (`Modalità`), `EmptyPage` (`è vuota`), `ErrorElementNotSupported` (`è`) |
| `spa` | `Ú á é í ñ ó` | `SettingsAppVersion` (`Versión`), `SettingsLastSitemapUpdate` (`Última`), `ConnectionModePhoneBle` (`Teléfono`), `EmptyPage` (`página está vacía`) |
| `pol` | `Ł Ż ą ć ę ł ń ó ś ź ż` | `Loading` (`Ładowanie`), `SettingsConnectivityMode` (`łączenia`), `SettingsUpdateSitemap` (`Odśwież`), `PlayerPlay` (`Odtwórz`), `PlayerNext` (`Następny`), `ErrorRequestFailedDetail` (`Żądanie`), `ErrorInvalidResponseDetail` (`odpowiedź`) |

The screenshots named `glyphs-*`, `localisation-*` and `error-*` are the
evidence. Inspect them for fallback boxes (a rectangle, question mark or blank
cell) and for missing diacritics such as `ł` rendering as `l` or `ß` as `B`.

## Device choice

Truncation is a width problem, so the primary device is the narrowest supported
UI device in the approved matrix: `edge540`, 246 x 322. It is narrower than
the 260 px round watches, is button-only (deterministic `press` navigation), and
uses the Edge `Config`, whose menu fonts are the largest (`FONT_MEDIUM` before
falling back to smaller fonts). The settings menu is a regular last homepage
entry on button-based Edge devices, so reaching it does not depend on the
page-scroll delegate.

`fenix7` (260 px round) is added only for
`localisation-error-config-url-round`. `ErrorView` sizes its text area to the
largest square inscribed in the circle, about 184 px at 260 px, which is
narrower than the `edge540` rectangle even though the nominal screen is wider.
That makes the round error view the worst case for the longest error string.

The matrix is owned by #366 and its coverage test
(`tests/runner/tests/test_variants.py`). These scenarios do not edit it and do
not claim variant completeness.

## Machine-asserted versus review-only

Machine-asserted today:

- the simulator language (`LANGUAGE;code=<lang>`) — the assertion exists, but
  only `eng` is provisionable, so the other languages are skipped as blocked;
- that the expected view is shown (`VIEW;category=...;view=...`);
- that the full-screen error view is the current view, including its stable id
  (`ERRORVIEW;id=UI:CONFIG`, `ERRORVIEW;id=S:-402`);
- that no error view appears where none is expected, checked against the whole
  run rather than only the final state (`never_error_view`);
- that an offline command sends no request (shim trace).

Not machine-asserted yet: any judgement about the rendered text. The harness has
no OCR, no region hash and no font-coverage check, and a golden image would only
prove "the same pixels as last time", not "the glyph is present and the text is
not clipped". Text checks are therefore declared as screenshot artifacts and
marked **review-only** here and in the scenario headers. Do not describe them as
automated coverage.

Review procedure for an operator:

1. Run the English run, for example
   `python -m e2e run extended/localisation-error-config-url-eng`. The other
   language runs report themselves as blocked until language provisioning
   exists.
1. Open the PNGs in `tests/artifacts/extended/<scenario>/`.
1. Compare the rendered message with
   `resources/strings/<lang>/strings-<lang>.xml`; for a language the harness
   cannot run, take the screenshot by hand in the simulator UI.
1. Check the whole message is present, that no line ends in an ellipsis the
   translation does not contain, and that accented characters are not fallback
   boxes.
1. Repeat for each language and each scenario in the table above.

## Residual gaps

- **`ErrorOutOfMemory` text.** The app throws this only below 10 KiB free
  memory, which the shim cannot force. The memory-class scenario covers the
  same critical/full-screen path deterministically with `S:-402`; the literal
  `ErrorOutOfMemory` wrapping remains a manual low-memory check.
- **`ErrorNoPhoneDetail`.** The `-104` no-phone error is an SDK result, not an
  HTTP response, so the shim cannot produce it. The multiline scenario covers
  the same prefix/newline/value layout through the `-400` path.
- **`ErrorRequestFailedDetail`.** Not covered per language. The English
  `regression/http-503-recovery` scenario exercises it; the exact HTTP-code
  variant depends on the device and phone OS.
- **Text assertions.** Everything visual is review-only until the harness gains
  a region or OCR assertion.
- **Round-screen menus.** Only the error view is checked on `fenix7`; menu
  clipping on round screens is left to the #366 device work.

## Adding a string

1. Find the resource id in `resources/strings/<lang>/strings-<lang>.xml` and the
   screen that renders it.
1. If the screen is already captured by a localisation scenario, add the id to
   the coverage table and, when it uses a character not in the glyph table, add
   that character and its carrier screen.
1. If the screen is not captured, extend the scenario's navigation. Prefer the
   existing `garmin_localisation` sitemap; add an item only if the screen needs
   one, and keep `garmin_localisation.sitemap` and `garmin_e2e.items` in sync.
1. If the string is `scope="settings"`, do not add a watch truncation check.
1. Run `python -m pytest` in `tests/runner` and
   `python -m e2e run --all --dry-run`.
1. Run the scenario on the devices named above and review the screenshots.

## Adding a language

1. Add the resource bundle in the app under `resources/strings/<code>/`; this
   harness does not own the translations.
1. Add the code to `LANGUAGES` in `tests/runner/e2e/scenarios.py`.
1. Add it to the `languages:` list in every `localisation-*.yaml` file.
1. Extend the language and glyph tables above, and say which new characters
   need review.
1. Update the language count assertion in
   `tests/runner/tests/test_scenarios.py`.
1. Confirm the simulator can select the language. The `LANGUAGE` assertion
   fails if it cannot; do not relax it to `unknown`.
1. Run the unit tests and the dry run again.
