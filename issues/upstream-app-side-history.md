# Upstream proposal — for filing on deepentropy/opentrader

Title of the issue: **Chart data loading: reduce upfront history, only fetch bars for the focused/active chart**

Paste everything below the line into the issue body.

---

### Summary

The chart datafeed requests 30 years of daily bars up-front (`DAILY_FULL_DAYS = 7560` in `src/data/datafeed.ts`) for every daily chart, and the app fetches history for every open chart tab, not just the focused one. For data sources whose history is shallow or expensive to synthesize (OpenAlgo plugins on affected deployments), this makes every chart open slow and multiplies the cost across tabs. Two frontend changes would fix it.

### Background

Observed with the OpenAlgo data plugin: opening 6 charts produces 6 serial `data.bars {countBack: 7560}` requests, each taking ~4.5 s (the plugin has to synthesize daily bars from hourly because the broker's daily endpoint answers only its last day). Total ≈ 30 s of serial work per "open charts" action.

Note: there are no watchlist mini-charts/sparklines in the codebase — the watchlist only uses live quotes pushed via `data.subscribe`. So the bars requests come from open chart tabs, not the watchlist.

### 1. Reduce the default chart history range

**Location:** `src/data/datafeed.ts` line 113 — `const DAILY_FULL_DAYS = 7560;`

Used by `DAILY_INTERVALS` (1D/1W/1M) and `dailyFamily()` (custom ND/NW/NM). The comment says the whole available daily history is loaded up-front so scrolling back is instant. 30 years is far more than any provider serves cheaply, and for shallow providers it is actively harmful.

**Suggestion:** lower the default, or derive it from `source().historyLimits()?.day` (falling back to 7560 for the gateway). The built-in OpenAlgo provider already caps at 365 via `historyLimits`; the up-front lookback should respect the same cap so a shallow provider never receives a 7560-day request.

### 2. Only fetch bars for the focused/active chart

History is requested for every open chart tab, not just the focused one. The plugin cannot tell which request is "active" — it just answers `data.bars`.

**Suggestion:** only call `getBars` for the focused/active chart pane; keep other tabs' bars stale until they become active (or until the user scrolls). Lazy-load on focus change.

### Why this matters for plugins

A data plugin serves symbols under `<exchange>:<ticker>` and answers `data.bars {ticker, resolution, from, to, countBack}`. It has no way to know the visible range or which symbol is active, and it cannot refuse a 7560-day request without breaking the chart. The only levers are the app's request size and request count. The plugin-side mitigations (capping synthesis to 365 days, dropping stale symbols from the WebSocket subscribe frame) are workarounds; these two changes are what actually remove the cost.

### Related

- Companion issue: chart history performance on shallow providers (the 7560 default and `clampLookback` not taking effect for plugin-routed symbols).
- `src/data/datafeed.ts` — `DAILY_FULL_DAYS`, `clampLookback`, `getBars`, `getBarsBefore`.
- `src-tauri/src/plugins/data.rs` — `plugin::recent` / `plugin::bars` (passes `days` straight through as `countBack`).