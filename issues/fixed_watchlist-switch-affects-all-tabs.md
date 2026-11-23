# Bug report — FILED on deepentropy/opentrader as issue #387

> **FIXED upstream** in `9a39afb6` (issue #387), adopted in this fork. Kept for reference.


> Filed 22/11/2026: https://github.com/deepentropy/opentrader/issues/387

> Drafted 22/11/2026. Filled in the fields of upstream's `.github/ISSUE_TEMPLATE/bug_report.yml`. Paste the section below into the GitHub issue form. The diagnostic block was taken with 2 tabs open; the watchlist shown at capture was "Mad" (switched from "India" to reproduce). Restore your own watchlist after.

Paste everything below the line into the issue form (each heading = the template field).

---

## What happened

With more than one chart tab open, switching the watchlist in one tab changes the watchlist shown in **all** the other tabs too. Each tab cannot keep its own watchlist selection. For example, with two tabs open I switched the shown watchlist from "India" to "Mad" while the first tab was focused, and the second tab followed the change.

## What you expected

Each tab should remember its own shown watchlist. Switching the watchlist in one tab should not change which one the other tabs show.

## Steps to reproduce

1. Open the app with two chart tabs (e.g. one with NIFTY BANK on 1D candles, another with any other chart).
2. In the first tab, show watchlist "India" in the watchlist panel.
3. Switch to the second tab, show watchlist "Mad".
4. Switch back to the first tab and choose watchlist "Mad" there too (or any third watchlist).
5. Switch to the second tab: it now shows the watchlist just chosen in the first tab, rather than keeping its own.

## How often

Every time.

## Where the chart's data comes from

Not chart related

## Area of the app

Watchlist

## Diagnostic info

```
### Diagnostic info

- App: OpenTrader 0.4.30 (built 21/11/2026), desktop
- System: Windows 10 Pro (kernel 19045), x86_64, Intel(R) Core(TM) i5-4590 CPU @ 3.30GHz (4 threads), 8.5 GB RAM, web view 157.0.4341.60
- Window: 1536×824 at 125% scaling (screen 1536×864), app zoom 100%, language en
- Tabs: 2 open; this tab: layout s, 1 chart
- Chart 1 (focused): NSE_INDEX:BANKNIFTY, 1D, candle, RTH session, plugin Zerodha (enctoken) 0.2.23, 1290 bars loaded
  - Studies: Volume
- Bar Replay: off
- Plugins: Zerodha (enctoken) 0.2.23 (datafeed, broker; connected)
- Recent errors (oldest first):
  - 22/11/2026 16:41:27 rejection ×3: string: state not managed for field `…` on command `…`. You must call `…` before using this command
- Taken: 22/11/2026 16:43:31
```

## App version

0.4.30

## Screenshots or video

The app's `screenshot` tool captures only the chart tab, so it does not show the watchlist panel where this bug is visible. A screenshot of the watchlist panel with two tabs open can be attached here if needed; the reproduction is deterministic from the steps above.

## Performance problems only

## Script source (if a script is involved)

## Anything else

Started after moving to multiple tabs; no error message accompanies it.
