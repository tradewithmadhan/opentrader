<!-- Filed as https://github.com/deepentropy/opentrader/issues/411 (25/11/2026), bug report (label: bug). File-only marker: the issue body carries no status line, and this markdown (minus this comment) is exactly what is published. -->

# Bug — a study that throws a runtime error never recalculates (stuck until remove + re-add)

> **FIXED upstream** in `1fcb3034 / 411` (issue #411), adopted in this fork. Kept for reference.

**Labels:** bug

---

### What happened

When a study throws a runtime error, it keeps showing that error and does not calculate again when the chart symbol or timeframe changes — even when the new symbol would make the study succeed. For example, the built-in **Open Interest** study on a symbol without OI data fails with a "No open interest data for …" error; after switching to a symbol that does have OI, the study stays empty with the same error. The only way to recover is to remove the study and add it again. Changing the timeframe does not clear it either.

### What you expected

A study that failed should recalculate whenever its inputs or chart context change — especially on a symbol or timeframe change — so a symbol-specific error clears itself once data is available. It should not need removing and re-adding. (Another charting app re-runs a study on the new symbol instead of latching the old error.)

### Steps to reproduce

1. Open a stock that has no open-interest series, e.g. `NASDAQ:AAPL`, on 5 minutes, candles, regular hours.
2. Add the built-in **Open Interest** study (no settings changed).
   - It fails with a runtime error similar to: `No open interest data for NASDAQ:AAPL_OI.` (tooltip: "Runtime error").
3. Switch the chart symbol to one your data source supplies OI for (an index future or an option contract).
4. Observe: the Open Interest study still shows the old error / an empty pane; it does not recompute with the new symbol.
5. Change the timeframe — the error still does not clear.
6. Remove the study and add it again on the same symbol — it now computes correctly and shows OI values.

The same happens with user scripts that request another symbol's series (e.g. `request.security(syminfo.tickerid + "_OI", ...)`): once they throw, the error latches until remove + re-add.

### How often does it happen?

Every time

### Where the chart's data comes from

A data plugin (Zerodha (enctoken) 0.6.5)

### Area of the app

Indicators

### Diagnostic info

```markdown
### Diagnostic info

- App: OpenTrader 0.4.33 (built 25/11/2026), desktop
- System: Windows 10 Pro (kernel 19045), x86_64, Intel(R) Core(TM) i5-4590 CPU @ 3.30GHz (4 threads), 8.5 GB RAM, web view 158.0.4370.33
- Window: 1536×824 at 125% scaling (screen 1536×864), app zoom 100%, language en
- Tabs: 2 open; this tab: layout s, 1 chart
- Chart 1 (focused): NSE_INDEX:NIFTY, 5, candle, RTH session, plugin Zerodha (enctoken) 0.6.5, 750 bars loaded
  - Studies: Volume; Monthly Levels (Expiry H/L) (show_history_levels=false) [hidden]; Exponential moving average [hidden]; Square Root Levels (Daily) [hidden]; MadhaN Pivot (Study) [hidden]; OI Profile (max_ce_bar_length_bars=28, anchor_bars_past_last_bar=90, width_bars_each_side=20, gap_from_last_bar=2) [hidden]; OI Diverging Profile [hidden]; OI Chain Profile (expiry_token=26D01, strike_step=50, positive_colour_right=#00E676, negative_colour_left=#FF4B4B, anchor_bars_past_last_bar=30, expiry_yymmm=26DEC, ce_pe_separation_of_strike_step=28, ce_colour_right=#00E676, pe_colour_left=#FF4B4B); Option Side Switcher (CE↔PE)
- Bar Replay: off
- Plugins: Zerodha (enctoken) 0.6.5 (datafeed, broker; connected)
- Proxy: none; app update: up-to-date
- Recent errors: none
- Taken: 25/11/2026 16:12:41
```

### App version

0.4.33

### Screenshots or video

Screenshots show the built-in Open Interest study with an empty pane after it fails on a symbol without OI data, and still empty after switching to a symbol whose data source supplies OI — until the study is removed and re-added. The user's original capture shows the study's "Runtime error — No open interest data …" tooltip.

### Performance problems only

(empty — not a performance issue)

### Script source (if a script is involved)

Not script-specific — the built-in **Open Interest** study reproduces it. It also affects user scripts that request another symbol's series (e.g. `request.security(syminfo.tickerid + "_OI", ...)`): once they throw, the error latches until remove + re-add.

### Anything else

The error appears to be latched in the study layer: `IndicatorLayer.calculate()` stores a thrown error in `this.error` and the legend/tooltip reads it back; `setScriptChart()` only updates the chart context and does not clear the error or force a recalculation. A related path marks another symbol as `failed` and sets `INVALID_SYMBOL`, which shows the same latch behaviour. (This is a guess at the cause; the behaviour above is what was observed.)
