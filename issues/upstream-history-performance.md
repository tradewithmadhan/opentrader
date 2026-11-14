# Upstream proposal — for filing on deepentropy/opentrader

Title of the issue: **Chart history: requesting 7560 daily bars up-front is too much for shallow-history providers (OpenAlgo)**

Paste everything below the line into the issue body.

---

### Summary

The datafeed requests `DAILY_FULL_DAYS = 7560` (≈30 years) of daily bars up-front for every daily chart (`src/data/datafeed.ts`). For providers whose daily history is shallow or broken (OpenAlgo on some deployments answers **one row** for any daily window), this forces expensive synthesis/fetching and makes every chart open slow. The `clampLookback` mechanism exists and is supposed to cap this, but it is not taking effect for plugin-routed symbols, so the plugin receives `countBack: 7560`.

### Observed behaviour

Plugin log (OpenAlgo plugin) on chart open:

```
data.bars {"countBack": 7560, "from": 879920131.0, "resolution": "1D", "ticker": "NFO:NIFTY23NOV26FUT", "to": 1794636931.0}
```

Every daily chart load asks for 7560 daily bars. On a deployment whose `/api/v1/history` daily endpoint answers only its last day, the plugin has to synthesize daily bars from hourly bars (≈4.5 s per symbol), so 6 open charts ≈ 30 s of serial work. The built-in OpenAlgo provider already caps at 365 days via `historyLimits: () => ({ second: 30, minute: 30, day: 365 })`, but the plugin path does not honour the same cap.

### Root cause

1. `src/data/datafeed.ts` line 113: `const DAILY_FULL_DAYS = 7560;` — 30 years requested up-front for every daily chart, on the assumption that the full series is in memory and scrolling back is instant.
2. `clampLookback("day", cfg.days)` is supposed to clamp to `historyLimits().day` (365 for OpenAlgo), but for plugin-routed symbols the plugin still receives `countBack: 7560`. Either `historyLimits()` returns `null` on the plugin path or `clampLookback` is bypassed (e.g. `spanDays` override in scroll-back paging).
3. History is fetched for **every symbol the app touches** (open charts, and possibly watchlist sparklines), not just the focused/active chart — multiplying the cost.

### Suggested changes

1. **Make `clampLookback` effective for plugin-routed symbols.** Verify `source().historyLimits()` is non-null on the plugin data path, or apply the cap inside the Rust `plugin::recent` / `plugin::bars` (in `src-tauri/src/plugins/data.rs`) so a shallow provider never receives 7560.
2. **Reduce `DAILY_FULL_DAYS` or make it provider-aware.** Shallow-history providers (OpenAlgo, and any plugin whose `historyLimits().day` is small) should not request 7560 days. Consider deriving the up-front lookback from `historyLimits()` instead of a hard-coded 7560.
3. **Only fetch history for the active/selected chart.** Avoid requesting bars for every watchlist row / background tab; fetch on demand when a symbol becomes active.
4. **OpenAlgo daily endpoint.** On affected deployments `/api/v1/history` with `interval: "D"` answers exactly one row for any window. Server-side daily aggregation (or a different endpoint) would remove the need for client-side synthesis.

### Plugin-side mitigations already applied (not part of this issue)

The OpenAlgo plugin now caps daily synthesis to 365 days (both the D call and the intraday synthesis window), drops stale index symbols from the WebSocket subscribe frame (they broke ticks for every other symbol), and fixes `closeposition` to use only the `strategy` field. These are workarounds; the upstream changes above are what actually remove the cost.

### Alternatives considered

- Caching bars in the plugin (rejected by the user): helps re-opens but not the initial burst, and risks stale data.
- Lowering `DAILY_FULL_DAYS` globally: changes behaviour for the gateway provider too, which serves full history cheaply — better to make it provider-aware.