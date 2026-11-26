<!-- This draft was never filed. File-only marker; the text below was for a request to deepentropy/oakscriptJS, superseded by upstream fixes. -->

# (draft, superseded) Feature request — let scripts read the remaining Pine v6 symbol/data built-ins

> **FIXED upstream** — `oakscriptJS` issue #211 (commit `5494844`, released in **oakscriptjs 0.21.0**) implemented the library half; `opentrader` issue #414 (upstream `c70a9458`, ported here as `721bca0a`) implemented the app half. Kept for reference, not filed. Revalidated 26/11/2026 against oakscriptjs 0.21.0 and this fork's HEAD `7b48e980`.

## What happened

The draft asked oakscriptJS to expose the Pine v6 `syminfo.*` fields it lacked and to confirm the scope of the
data-request built-ins. Upstream made both changes after this fork filed `deepentropy/opentrader#414`:

- **oakscriptJS #211** — "syminfo: every symbol field of PineScript v6, request.dividends / splits / earnings".
  Maintainer: "Done in 5494844, released in v0.21.0. Measured rules and fixtures in tests/script/issue211.test.ts."
  `syminfo` now declares all **40** Pine v6 fields, including the three that were already in the library
  (`basecurrency`, `root`, `description`), the three derivable ones (`ticker`, `prefix`, `main_tickerid`) and
  the 24 metadata fields. `request.dividends`, `request.splits`, `request.earnings` and the `dividends.*`,
  `splits.*`, `earnings.*` constants are in the API too. A field the host supplies as `null` reads as `na`;
  a field the host does not supply throws, as before.
- **opentrader #414** — "Built, it comes with the next release." The app reads symbol metadata from the
  reference data and plugins, adds the optional Plugin API 10 `data.symbolInfo` fields (`root`, `expiration`,
  `isin`, `baseCurrency`, `country`, `sector`, `industry`, `volumeType`), feeds `request.dividends` / `splits` /
  `earnings` from the chart's corporate actions, and recalculates studies when the data arrives. The maintainer
  notes that `earnings.estimate` / `standardized` and the `*_future_*` values have no data source.

## Revalidated state (oakscriptjs 0.21.0)

Full diff against the Pine v6 reference KB (939 entities): 140 name gaps, down from 186 at 0.20.0. After excluding
94 language-syntax and design-equivalent names (operators, keywords, annotations, type qualifiers, literals,
casts, `library` / `max_bars_back`, the generic `map.new<type,type>` spelling and the 33
`strategy.closedtrades.*(i)` / `opentrades.*(i)` trade-object calls), **46 real API/data gaps** remain:

| Group | Missing |
|---|---|
| `request.*` — 5 | `currency_rate`, `economic`, `financial`, `footprint`, `quandl` (all need external data) |
| Footprint / order flow — 19 | `footprint.*` (9), `volume_row.*` (8) and the two types |
| `ticker.*` — 7 | `new`, `modify`, `inherit`, `kagi`, `renko`, `linebreak`, `pointfigure` |
| Request modifiers — 8 | `backadjustment.*` (3), `settlement_as_close.*` (3), `display.pine_screener`, `display.price_scale` |
| Globals — 4 | `ask`, `bid` (tick charts), `last_bar_time`, `runtime.error(message)` |
| Strategy — 2 | `strategy.closedtrades.first_index`, `strategy.opentrades.capital_held` |
| Inputs — 1 | `input.enum` (the library deliberately has no enum declarations) |

These are outside the original request's core (symbol metadata), and upstream's `skills/oakscript/reference/pine-differences.md`
still documents the request/ticker exclusions as deliberate. Do not file this draft.

## Fork status

This fork declares `oakscriptjs ^0.21.0` and `lightweight-charts-indicators ^0.27.3`, with 0.21.0 installed.
The app half is ported here as `721bca0a` (HEAD `7b48e980`): `script-chart.ts` now builds a `SymbolInfo` with
description, sector, industry, employees, basecurrency, root, expiration and the other fields, and the library
exposes all 40 getters. Both halves of this old draft are therefore done; nothing in it remains to file.
