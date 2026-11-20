# Feature request / bug — for filing on tradewithmadhan/opentrader (or deepentropy/opentrader)

> Status: **FIXED upstream** in `5565d13b` (#371, "Data plugins: restored charts and watchlists wait for a plugin still starting"). Adopted in this fork via cherry-pick.

Fork reference: `tradewithmadhan/opentrader`, branch `sample-web-clean`. Observed with the OpenAlgo plugin (0.1.21, app 0.4.28). The plugin's per-category log (`openalgo-api.log`) shows the app connects at 19:08:10 but the first `data.bars` request doesn't arrive until 19:09:01 — 51 seconds later. Until then the chart shows no data. Switching the symbol or watchlist triggers the fetch immediately.

Paste everything below the line into the issue body.

---

### What I'm trying to do

Have charts and watchlists load their data from a connected data plugin **on startup**, without requiring a manual symbol change or watchlist switch.

### What happens

After restarting the app:
- The plugin connects at 19:08:10 (hello + configure).
- The app restores the layout (charts, watchlists, panels).
- `data.symbolInfo` calls start at 19:08:24 (14 s later).
- `data.bars` calls start at 19:09:01 (51 s later) — and only for some symbols.
- Until a user interacts (changes symbol, switches watchlist), several charts/watchlists show no data.

The plugin answers every request it receives (verified in `openalgo-api.log`). The delay is app-side: the chart's `createResource` / `history` effect does not fire on the initial mount, or fires before the plugin is ready.

### Root cause (likely)

1. **Plugin readiness race**: the app restores charts before the plugin is connected. The chart's first `getBars` either fails (plugin not connected → retried) or is skipped because `props.symbol` is undefined on first mount.
2. **`firstSymbol` guard** (`src/window/chart/view/symbol-load.ts`, lines 82–88): the first symbol load returns early (`firstSymbol = false; return;`) without triggering a fetch. The actual first load depends on the `history` resource firing, which may not happen if the plugin isn't ready.
3. **No retry trigger**: if the initial fetch fails because the plugin wasn't connected, nothing re-triggers it when the plugin connects later. The user must interact to fire the effect.

### How it should work

1. The app should **wait for connected data plugins** before restoring charts / requesting bars. Or, if charts are restored first, they should retry `getBars` once the plugin connects.
2. The `firstSymbol` guard should not prevent the initial data fetch — it should still trigger `getBars` (or the `history` resource) on first mount.
3. If a plugin connects after the chart is already mounted, the chart should re-fetch bars automatically (not require a manual symbol change).

### Why this matters

- On every app restart, the user must manually interact (change symbol, switch watchlist) to load data from the plugin.
- With a slower plugin (OpenAlgo), the delay is 30–60 s even when it does fetch, and some symbols never load until interacted with.

### Suggested implementation area

- `src/window/chart/ChartView.tsx` (lines 246–266): the `history` resource should fire on first mount even when the plugin isn't connected yet (retry via `retryTransient`).
- `src/window/chart/view/symbol-load.ts` (lines 78–88): the `firstSymbol` guard should not skip the initial data fetch.
- Rust plugin host (`src-tauri/src/plugins/mod.rs` / `hub.rs`): ensure plugins are connected before charts are restored, or emit a "plugin connected" event that charts listen for to trigger a re-fetch.
- `src/data/datafeed.ts` / `datafeed-rest.ts`: re-trigger pending fetches when a plugin connects.

### Additional context

- This is an app-side issue. The plugin answers every request it receives; the problem is the app not requesting data on startup (or requesting it late).
- Plugin-side workarounds (e.g. pushing data unprompted) are not possible — the app must initiate `data.bars` / `data.symbolInfo` requests.