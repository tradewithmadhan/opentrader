# Upstream proposal — for filing on deepentropy/opentrader

Title of the issue: **Data plugins: let a plugin serve its symbols' contract size (point value) — plus small host gaps an Indian-broker plugin hits**

Fork reference: `tradewithmadhan/opentrader`, branch `sample-web-clean`. The OpenAlgo plugin
(`plugins/openalgo`, Plugin API 2 — data + trading over one REST + WebSocket gateway) is built and
live-tested against a self-hosted deployment; everything below was needed to make its symbols behave
like the built-in providers' do.

Paste everything below the line into the issue body.

---

### Why this market needs a point value

On Indian exchanges a contract's size lives in the instrument master as a **lot size** (NIFTY futures
65, an equity 1). The app's whole economics chain already keys on it as *point value*: the Symbol info
dialog's "Point value" row (`src/data/symbol-info.ts`), the strategy tester's P&L and reports
(`src/backtester/broker/*`, `xlsx-report.ts`, `trades-csv.ts`), and the ticket's risk-based sizing
(`ticket.ts`'s `unitsForRisk`/`riskValue`). A source without it silently prices a futures position as
one unit per point — wrong by 65×.

The gateway and REST-reference providers have no lot concept, so their field reads `None` and the
dialog shows 1. A data plugin for a gateway that *does* expose lot sizes has nowhere to put it: the
host's `Info` deserializer ignores unknown fields, so a plugin sending `pointValue` loses it silently.

### Change 1 — `TickerInfo.point_value`, end to end (already on the fork)

The host struct first — `src-tauri/crates/core/src/data/market_rest/reference.rs`
(fork commit `a1b92339`):

```rust
/// Contract multiplier: one point of price moves point_value currency —
/// the instrument's lot size on OpenAlgo sources, None elsewhere (those
/// sources have no lot concept; the dialog shows 1).
pub point_value: Option<f64>,
```

Sources that have no such concept fill `None` (`reference.rs`'s REST fetch, the gateway provider).
The bindings regenerate from the struct.

Then the frontend that reads it — `src/data/symbol-info.ts` + `src/window/chart/SymbolInfoDialog.tsx`
(fork commit `735bf8ef`): `InfoInput` gains `pointValue?: number | null`, the dialog passes the
ticker info's value through, and the "Point value" row shows it with the old hardcoded `"1"` as the
fallback when absent:

```ts
second.push({ title: "Point value", value: i.pointValue != null ? String(i.pointValue) : "1" });
```

Tests in `src/data/__tests__/symbol-info.test.ts` cover the futures/index/equity rows. (Two sibling
fork commits round the dialog out for this market and may interest upstream independently:
`801cc3dd` — Type labels for futures, options and indices; `a3bc2ca3` — ISIN shown in FIGI's place.)

### Change 2 — the plugin data route passes `pointValue` through

`src-tauri/src/plugins/data.rs` (fork commit `678e8678`):

1. `Info` (the `data.symbolInfo` answer) gains, after `holidays`:

   ```rust
   /// The profit of a 1-point move in one unit (the plugin's `pointValue`): the fork's
   /// OpenAlgo plugin maps its contract's lot size here, like the built-in provider does.
   #[serde(default)]
   pub point_value: Option<f64>,
   ```

2. `ticker_info` maps it instead of hardcoding `None`:

   ```rust
   // The plugin's `pointValue` (its lot size); None when it does not say.
   point_value: i.point_value,
   ```

3. A test pins both the read and the default:

   ```rust
   #[test]
   fn symbol_info_reads_the_plugins_point_value() { … } // Some(65.0) when sent, None when absent
   ```

Compatibility: `#[serde(default)]` keeps every existing plugin working; plugins that don't send
`pointValue` are indistinguishable from today. Verified live against OpenAlgo: the plugin reads
`lotsize` from the instrument master and serves `NFO:NIFTY…FUT` → 65.0, `NSE:TCS` → 1.0, index → 1.0.

### Change 3 — search carries the dialog's Type filter to plugins

`src-tauri/src/plugins/data.rs` (fork commit `6f1d295b`). The search dialog has Type chips
(EQ / FUT / CE / PE / IX, …); the built-in providers filter server-side, but the plugin route asked
`data.search` with only `{query}` and then threw the plugin's per-row type away:

1. Ask with the filter — the plugin filters on it (it knows `instrumenttype`; the host just carries it):

   ```rust
   hub.request(&plugin, "data.search", json!({ "query": query, "type": type_filter }), INFO_TIMEOUT)
   ```

2. Keep the plugin's per-row type: `FoundSymbol.kind` → `SymbolSearchResult.r#type`
   (empty stays `None`), so the chips filter plugin rows and the row's Type column reads right.
   `market_of(kind)` was already mapped; only `r#type` was dropped. Test included
   (`search_rows_carry_the_plugins_type_and_market`).

### Change 4 — say why a chart is blank (host-side ask logging)

Same file and commit. Against a live gateway, "the source answered no bars" with nothing anywhere
made every empty chart a guess. Three log lines into the plugin's log (the plugin prints its own
traffic next to them):

1. Every `data.*` ask and its failure (`ask`):

   ```rust
   super::log(app, &r.plugin, format!("{method} {params}"));
   … Err(e) => super::log(app, &r.plugin, format!("[error] {method} failed: {e}"))
   ```

2. `bars`: when no served interval matches the ask —
   `history {ticker} {unit}x{mult}: none of it is among the intervals the plugin serves (…)`;
   when the source answers nothing — `history {ticker} {resolution}: the source answered no bars`.

No behavior change; logging only.

### What a plugin can already send today (no host change needed)

For completeness — the OpenAlgo plugin uses these `data.symbolInfo` fields that the current upstream
host already parses, and they work end to end:

- `holidays` — comma-joined `YYYYMMDD` (the session grammar of `src/data/session/spec.ts`); the host
  builds them into `SymbolSession`. The plugin day-caches one year's rows of
  `POST /market/holidays` per exchange (index venues follow their cash market: `NSE_INDEX` reads the
  calendar's `NSE` rows), keeping only rows that list the exchange in `closed_exchanges` — special
  and settlement sessions stay open, as the built-in providers treat them.
- `extended` — a wider traded day than `session`, built as the extended subsession.
- `intervals` — the served list the hour-bars gate and the host's divisor picker read (#318, #332).

`pointValue` is the **only** field a current upstream build silently drops.

### Footprint

- Change 1: one struct field + one `None` fill in `reference.rs`; `InfoInput.pointValue`, the dialog
  pass-through and the row's read in `symbol-info.ts` / `SymbolInfoDialog.tsx`, with tests.
- Change 2: one struct field, one line in `ticker_info`, one test in `data.rs`.
- Change 3: the search signature, one json field, one `r#type` line, one test in `data.rs`.
- Change 4: three log lines in `data.rs`.

All additive; no manifest, protocol or existing-plugin change. If useful I can send the fork's
patches as reference PRs, and the OpenAlgo plugin (`plugins/openalgo`, data + trading over one
account's exchanges) as the worked example.

### One suggestion while touching the ticket

The live order ticket's risk math still hardcodes `pointValue: 1` (`ticket-model.ts`,
`OrderPresets.tsx`) even though `TickerInfo.point_value` reaches the frontend. Wiring the focused
symbol's ticker info into the ticket ctx would make risk-% and risk-amount sizing correct for futures
on every provider, not just the ones with lot sizes.

### A fork-side note (not an ask)

Our branch's cherry-pick of the session-cache change dropped `intervals: i.intervals.clone()` from
`session()`; `upstream/main` still has it and the 0.4.24 frontend reads `sessions.intervals`
(`datafeed.ts`). We'll re-sync on our side — flagging it only so the line's purpose is clear if
anyone else hits plugin interval-picker gating.
