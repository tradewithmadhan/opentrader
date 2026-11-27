# Bug — seconds bars of a data plugin never update live, including 5S lower-timeframe data read by scripts

> **FIXED upstream** in `179426ac / 421` (issue #421), adopted in this fork. Kept for reference.

Status: **FILED** as https://github.com/deepentropy/opentrader/issues/421 (27/11/2026), on the user's explicit instruction. Prepared from a live desktop session with a data plugin serving second resolutions. Do not make further changes to the live issue without the user's instruction.

Paste everything below the line into the issue body.

---

### Steps to reproduce

1. Install a data plugin that serves second intervals (observed with Zerodha (enctoken) 0.7.0, which serves `5S` and answers `data.bars` for it).
2. Open a chart on a seconds interval, e.g. `NFO:NIFTY1!` on `5S`, candle, RTH session.
3. Watch the last bar while the market is open.
4. Separately, add an OakScript indicator on a 5-minute chart that reads the chart symbol's 5-second bars with `request.security_lower_tf(syminfo.tickerid, "5S", () => volume)` and draws a marker for a volume spike.

### Expected

The newest 5-second bar updates as ticks arrive, and a script reading 5-second lower-timeframe data gets the new bars as they form — like minute charts do.

### Actual

The `5S` chart advances only in bursts of about 30 seconds: the plugin log shows a `data.bars` tail request roughly every half minute, and the chart's last bar jumps to the newest time only on that poll. Between polls it is frozen even though the plugin is streaming ticks.

The script's 5-second lower-timeframe data does not update at all: its markers stay on the last bars loaded when the study ran, and do not move when a new 5-second bar completes or when a new spike occurs.

Minute charts of the same symbol (`5`, etc.) do update on every tick, and the same plugin's watchlists and quotes are live. So the data is flowing from the plugin; only the seconds bars are affected.

### Diagnostic info

- App: OpenTrader 0.5.2 (built 27/11/2026), desktop
- System: Windows 10 Pro (kernel 19045), x86_64, Intel(R) Core(TM) i5-4590 CPU @ 3.30GHz (4 threads), 8.5 GB RAM, web view 158.0.4370.33
- Window: 1536×824 at 125% scaling (screen 1536×864), app zoom 100%, language en
- Tabs: 2 open; this tab: layout s, 1 chart
- Chart 1 (focused): NFO:NIFTY1!, 5S, candle, RTH session, plugin Zerodha (enctoken) 0.7.0, 7278 bars loaded
  - Studies: Volume; Exponential moving average [hidden]; Square Root Levels (Daily) [hidden]; MadhaN Pivot (Study) [hidden]; Monthly Levels (Expiry H/L); Pivot points standard [hidden]
- Bar Replay: off
- Plugins: Zerodha (enctoken) 0.7.0 (datafeed, broker; connected)
- Proxy: none; app update: up-to-date
- Recent errors: none
- Taken: 27/11/2026 13:03:58

### Evidence from the plugin log

```
2026-11-27 12:38:28 [info] [api] data.bars NFO:NIFTY1! 5S: 127 bars (27/11/2026–27/11/2026, countBack=128)
2026-11-27 12:38:59 [info] [api] data.bars NFO:NIFTY1! 5S: 126 bars (27/11/2026–27/11/2026, countBack=128)
2026-11-27 12:39:29 [info] [api] data.bars NFO:NIFTY1! 5S: 128 bars (27/11/2026–27/11/2026, countBack=129)
```

Two consecutive reads of the chart's bars showed its newest bar move from 12:39:23 to 12:39:54 on those polls, not per tick.

### Screenshot

The `5S` chart was open with candles ending at the last poll and no movement between polls. A screenshot can be attached when filing.

### How often

Every time, while the market is open, for seconds intervals served by a data plugin.

### Observed in the app source

`subscribeBars()` in `src/data/datafeed.ts` updates bars from `chartAggregate` through `bucketLiveTick()` and from `secondAggregate` through `bucketSecondBar()`; `bucketTrade()` returns null for the second family. The plugin host emits `ChartAggregate` for each `data.quote` but no `SecondAggregate`, and `data.watchTicks` is sent only for tick intervals (`src-tauri/src/plugins/data.rs`). Seconds charts therefore poll `data.bars` every 30 seconds (`src/window/chart/view/live.ts`), and a study's other-symbol bars use the same `subscribeBars()` path (`src/window/chart/indicators/other-symbols.ts`), so lower-timeframe seconds bars are not live-merged either.

### My guess at the cause

Plugin quotes become `ChartAggregate` events that update minute charts, but seconds bars need `SecondAggregate` events, which the plugin host does not emit, and streamed plugin `data.tick` is excluded from the seconds buckets — so both the seconds chart and a script's lower-timeframe seconds bars are left on the 30-second history poll.
