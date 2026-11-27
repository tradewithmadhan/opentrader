<!-- Filed as https://github.com/deepentropy/opentrader/issues/420 (27/11/2026), bug report, using the shape of .github/ISSUE_TEMPLATE/bug_report.yml (label: bug) — the account cannot add labels, so it is unlabeled. File-only marker: the published body starts after this comment and carries no Status line. -->

> **FIXED upstream** in `a639bbcd / 420` (issue #420), adopted in this fork. Kept for reference.

### What happened

In a layout with two (or more) charts, I maximize one chart so it fills the window. When I switch to another tab and come back to that tab, the maximize is gone and the layout shows all charts again. The symbols, intervals and studies of the layout are still there, but the maximized state is not.

### What you expected

Coming back to a tab should restore the chart view I left there — the same chart should still be maximized, just as the charts' symbols and studies are restored. Switching tabs should not reset the layout's maximize state.

### Steps to reproduce

From opening the app:

1. Use a tab with a two-chart layout (in the captured baseline: layout `2h`, chart 1 `NSE_INDEX:NIFTY` on 5 minutes, candles, RTH; chart 2 `NFO:NIFTY26D0825900CE` on 5 minutes, candles, RTH; data plugin Zerodha (enctoken) 0.7.0).
2. Maximize one of the two charts so only that chart fills the window.
3. Switch to the other open tab.
4. Switch back to the first tab.
5. Both charts are shown again; the maximize is reset.

### How often does it happen?

Every time, as reported by the user.

### Where the chart's data comes from

A data plugin — Zerodha (enctoken) 0.7.0.

### Diagnostic info

Captured with `get_diagnostics` on 27/11/2026, **before** maximizing a chart:

```text
### Diagnostic info

- App: OpenTrader 0.5.1 (built 26/11/2026), desktop
- System: Windows 10 Pro (kernel 19045), x86_64, Intel(R) Core(TM) i5-4590 CPU @ 3.30GHz (4 threads), 8.5 GB RAM, web view 158.0.4370.33
- Window: 1536×824 at 125% scaling (screen 1536×864), app zoom 100%, language en
- Tabs: 2 open; this tab: layout 2h, 2 charts
- Chart 1 (focused): NSE_INDEX:NIFTY, 5, candle, RTH session, plugin Zerodha (enctoken) 0.7.0, 683 bars loaded
  - Studies: Volume; Previous Day Week Highs & Lows (dailyLookback=4, areHeadsEnabled=true) [hidden]; Exponential moving average [hidden]; Pivot points std_custom [hidden]; MadhaN Pivot (Study) (labels=false, cam_2=true, cam_t_2=true); Volume-weighted average price [hidden]; Monthly Levels (Expiry H/L) [hidden]; Square Root Levels (Daily) [hidden]; Time price opportunity [hidden]; MA Cloud [hidden]; OI Chain Profile (max_bar_length_bars=28, bar_height_of_strike_step=18, call_put_separation_of_strike_step=28, call_green_left=#00E676, put_green_right=#00E676, show_bar_values=true, show_current_price_line=true, positive_colour_right=#00E676, negative_colour_left=#FF4B4B, anchor_bars_past_last_bar=30, expiry_yymmm=26DEC, ce_pe_separation_of_strike_step=28, ce_colour_right=#00E676, pe_colour_left=#FF4B4B); 5s Volume Spike (volume_threshold=6500, symbol=NFO:NIFTY1!, current_day_only=false, min_circle_size_px=18); Syminfo Fields [hidden]
- Chart 2: NFO:NIFTY26D0825900CE, 5, candle, RTH session, plugin Zerodha (enctoken) 0.7.0, 683 bars loaded
  - Studies: Previous Day Week Highs & Lows (dailyLookback=5, areHeadsEnabled=true) [hidden]; Exponential moving average [hidden]; MadhaN Pivot (Study) (cam=false, cam_2=true, cam_t_2=true, fibo_t_2=true) [hidden]; Option Side Switcher (CE↔PE); Open Interest
- Bar Replay: off
- Plugins: Zerodha (enctoken) 0.7.0 (datafeed, broker; connected)
- Proxy: none; app update: up-to-date
- Recent errors: none
- Taken: 27/11/2026 09:54:53
```

### App version

0.5.1, desktop.

### Screenshots or video

Not captured: the available tools cannot maximize a chart or switch tabs. Add a screenshot of the maximized chart before leaving the tab and another after coming back, with the layout visible.

### Anything else

No error message was shown and the diagnostic report lists no recent errors. The symbols and studies of the layout survive the tab switch; only the maximize state is lost. I searched the existing issues — no open report for this; #84 was about chart snapshots in multi-chart layouts, a different problem.

### Workarounds you use today

Maximize the chart again each time I come back to the tab.
