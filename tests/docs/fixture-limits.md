# Fixture size limit

The Connect IQ simulator enforces a smaller JSON response limit than a physical
watch. This page records the measured boundary, what it means for the app, and
the fixture-authoring rule the harness enforces.

## Measured limit

| Item | Value |
| --- | --- |
| Environment | Connect IQ SDK 9.3.0, Windows `simulator.exe` (simulator UI 5.2.0), `fenix7`, 2026-11-25 |
| Accepted response | **16,384 bytes** |
| Rejected response | **16,385 bytes** |
| Rejection | `Communications.NETWORK_RESPONSE_TOO_LARGE` (-402) |
| App trace | `E2E:ERROR;kind=critical;id=communication.sitemap;code=-402;source=sitemap` and `E2E:ERRORVIEW;id=S:-402;code=-402` |

How it was measured:

1. Built the app for `fenix7` with the `fast-polling` fixture and a compiled
   `url_0` pointing at a local HTTP server plus `sitemap_0` set to a test name.
2. Started the Windows simulator, switched **Settings > Glance Launch Mode >
   Launch in Normal Mode**, and pushed the PRG with `monkeydo`.
3. Served a valid sitemap JSON body of an exact byte length and binary-searched
   the length until the app's trace flipped from a successful sitemap to the
   `S:-402` error view.
4. Confirmed the endpoints explicitly: 16,384 bytes produced
   `E2E:SITEMAP;result=success;code=200`; 16,385 bytes produced the critical
   `-402` error and the full-screen error view.

The existing `extended/oversized-response` and
`extended/localisation-error-memory-class` scenarios use a 2 MiB body, which is
also rejected; they do not tell us where the boundary is.

## Simulator versus hardware

The 16,384-byte boundary is the **simulator's** limit. Real Garmin watches are
reported to accept larger sitemap responses; the app's -402 handling is the same
on both, but no hardware measurement is recorded here.

Because the pinned E2E guest runs the simulator, 16 KiB is the binding limit for
every scenario and every fixture in this repository. Do not infer a hardware
limit from these numbers.

The app has a second, separate limit: `SitemapStore.STORAGE_JSON_MAX_SIZE` is
81,920 bytes of **estimated** JSON size for the offline cache. It is looser than
the network limit for these fixtures, not tighter: the 11,113-byte
`garmin_e2e` payload estimated 24,944 bytes and the 15,712-byte near-limit
payload estimated 30,872 bytes, both cacheable. The network limit is what binds
first.

## Fixture rule

- A normal fixture's `GET /rest/sitemaps/<name>` body must be **at most
  12,288 bytes** (75% of the measured limit). This leaves room for item states,
  labels and a future openHAB serialization change without silently turning a
  working scenario into a -402 case.
- Split a sitemap by scenario boundary instead of growing it. The current split
  is element/rendering (`garmin_e2e`), commands (`garmin_commands`), dynamic
  visibility/colors (`garmin_dynamic`), localisation (`garmin_localisation`,
  `garmin_localisation_long`), unsupported widgets (`garmin_unsupported`) and
  the dev-only confirmation page (`garmin_confirm`).
- The two generated limit fixtures are exempt from the 12,288-byte authoring
  budget because they deliberately straddle the hard boundary:
  `garmin_limit_near` must be at most 16,384 bytes and `garmin_limit_over` must
  be above it. The fixture guard checks both sides, so a generator change cannot
  silently turn the near scenario into a -402 case or the over scenario into a
  passing one.
- `tests/fixtures/openhab/expected.yaml` holds the budget and the boundary
  wiring; `tests/fixtures/openhab/generate_size_fixtures.py` regenerates the two
  limit sitemaps.

Current measured REST body sizes (stable profile; dev adds `garmin_confirm`):

| Sitemap | Bytes | Notes |
| --- | ---: | --- |
| `garmin_e2e` | 11,113 | elements, nesting, ranges, icons, empty page |
| `garmin_commands` | 8,369 | toggle, generic switch, rollershutter, player, gates |
| `garmin_dynamic` | 7,704 | group control, colors, visibility |
| `garmin_localisation` | 3,822 | localisation menu |
| `garmin_localisation_long` | 1,014 | long dynamic error value |
| `garmin_unsupported` | 1,250 | unsupported widget path |
| `garmin_confirm` | 1,037 | dev overlay only |
| `garmin_limit_near` | 15,712 | 672 bytes below the measured limit |
| `garmin_limit_over` | 21,947 | above the measured limit |

The single pre-split `garmin_e2e` sitemap was **26,609 bytes**, so even after the
DSL fixes it could never reach the app on the pinned simulator: the response was
rejected with -402 before parsing. That is the real-world finding behind this
split, not just a fixture housekeeping change.

## Scenarios

- `extended/limits-near-limit` starts with `garmin_limit_near` and asserts that
  the sitemap is served, the `HomepageMenu` renders and no error view appears.
- `extended/limits-over-limit` starts with `garmin_limit_over` and asserts the
  critical `S:-402` error and the full-screen error view.

With the same SDK 9.3.0 simulator, the near payload produced
`E2E:SITEMAP;result=success;code=200` followed by
`E2E:VIEW;category=sitemap;view=HomepageMenu`; the over payload produced
`E2E:ERROR;kind=critical;id=communication.sitemap;code=-402;source=sitemap` and
`E2E:ERRORVIEW;id=S:-402;code=-402`. Those are exactly the events the two
scenarios assert.

## Measuring and enforcing

The CI `fixtures` job runs:

```bash
python -m e2e verify-fixtures --profile stable --log openhab.log \
  --strict --sizes --dump-dir ../compose/sitemaps
```

`--sizes` fetches every expected sitemap, prints the REST body sizes, enforces
`max_sitemap_bytes` and the near/over boundary, and writes the payloads to the
artifact directory. `e2e setup` runs the same size check. `e2e run` checks the
item and sitemap sets before every scenario but skips the per-sitemap size
fetches; CI is the size guard.
