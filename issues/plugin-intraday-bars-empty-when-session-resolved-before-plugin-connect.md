# Intraday chart stays empty when the symbol's session was resolved before the data plugin connected

Status: **FILED** as https://github.com/deepentropy/opentrader/issues/409 (25/11/2026), on the user's explicit instruction. Do not make further changes to the live issue without the user's instruction.

Fork reference: `tradewithmadhan/opentrader`, branch `sample-web-clean`. Observed with app 0.4.33 and a data plugin serving `NSE_INDEX:NIFTY` (Plugin API 8). The plugin answers every request; this is an app-side issue.

Paste everything below the line into the issue body.

---

### Steps to reproduce

1. Start the app with no data plugin installed/enabled, with a saved layout that contains a chart pane on `NSE_INDEX:NIFTY`, interval `15`, chart type `candle`, session `RTH`.
2. The layout is restored before any data plugin connects.
3. Install/enable the data plugin and connect it. The plugin needs a few seconds to load its instrument master.
4. Watchlists fill and show quotes, but the restored NIFTY pane stays empty.

### Expected

The 15-minute chart shows its bars, the same as when `NSE_INDEX:NIFTY` is selected *after* the plugin is connected.

### Actual

The pane is blank and diagnostics says `bars not loaded`. Changing the interval to `1D` shows bars at once; changing back to `15`, or to any minute or hour interval, shows none. A different pane whose symbol was resolved after the plugin connected (`NSE:RELIANCE`, `15`, candle) has 481 bars loaded in the same app at the same time.

The plugin's log shows it answered the requests. Examples from the same session:

```
2026-11-25 13:57:54 [info] [api] data.bars NSE_INDEX:NIFTY 5: 732 bars (11/11/2026–25/11/2026, countBack=836)
2026-11-25 14:03:36 [info] [api] data.bars NSE_INDEX:NIFTY 15: 505 bars (14/10/2026–25/11/2026, countBack=806)
```

The app receives those bars and does not display them.

### How often

Every time: when the pane is restored before the plugin connects.

### Diagnostic info

- App: OpenTrader 0.4.33 (built 25/11/2026), desktop
- System: Windows 10 Pro (kernel 19045), x86_64, Intel(R) Core(TM) i5-4590 CPU @ 3.30GHz (4 threads), 8.5 GB RAM, web view 158.0.4370.33
- Window: 1536×824 at 125% scaling (screen 1536×864), app zoom 100%, language en
- Tabs: 2 open; this tab: layout s, 1 chart
- Chart 1 (focused): NSE_INDEX:NIFTY, 15, candle, RTH session, plugin Zerodha (enctoken) 0.6.5, bars not loaded
  - Studies: Volume; Monthly Levels (Expiry H/L) (show_history_levels=false) [hidden]; Exponential moving average [hidden]; Square Root Levels (Daily) [hidden]; MadhaN Pivot (Study) [hidden]
- Bar Replay: off
- Plugins: Zerodha (enctoken) 0.6.5 (datafeed, broker; connected)
- Proxy: none; app update: up-to-date
- Recent errors: none
- Taken: 25/11/2026 14:08:13

### Screenshot

During the failure the pane was blank, with only the header `NIFTY 50 · 15 · NSE_INDEX` and `Vol 20` visible (observed 25/11/2026 14:08:13, same moment as the diagnostics above). No screenshot file was retained; after reopening the app the same pane shows its 15-minute bars.

### Additional context

Reopening the app makes the pane load correctly — the problem only occurs in an app run where the layout was restored before the plugin connected. It would be better if the pane recovered by itself when the plugin connects, without needing an app restart.

The same thing happens on a **plugin update**: the chart already holds symbols from the old plugin, whose sessions were resolved before the updated plugin connected. Those panes keep showing empty intraday bars until the app is restarted, even though the updated plugin is connected and answering requests.

### My guess at the cause

The symbol's session is resolved before the plugin connects and cached from the built-in US-equity fallback, and that cached session is not re-asked when the plugin connects because it was not originally resolved as a plugin symbol — so the intraday path's session filter drops every bar of the actual session.

### Suggested fix

When the set of plugins changes, re-ask `getSymbolSession` for every symbol whose session has been resolved, not only those already known as plugin symbols (`src/data/session/symbol-sessions.ts`: `refreshPluginSessions` iterates only `pluginInfos`, which is filled only when a resolve came back with plugin intervals). Replace the cached `SymbolSessions` when the answer changed and emit `chart-reload-data` so the chart's bars resource (keyed on symbol + interval + session + reload counter in `src/window/chart/ChartView.tsx`) is fetched again. Also re-run the chart's live-symbol subscription, since its effect currently depends only on the symbol and drops the subscription when no plugin route exists at mount.
