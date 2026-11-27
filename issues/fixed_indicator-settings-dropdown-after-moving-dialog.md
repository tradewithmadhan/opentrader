<!-- Filed as https://github.com/deepentropy/opentrader/issues/416 (26/11/2026), bug report, using the shape of .github/ISSUE_TEMPLATE/bug_report.yml (label: bug) — the account cannot add labels, so it is unlabeled. File-only marker: the published body starts after this comment and carries no Status line. -->

### What happened

> **FIXED upstream** in `d8bce6f3 / 416` (issue #416), adopted in this fork. Kept for reference.

The drop-down selectors inside an indicator's settings window work while the window is where it opened. After the
settings window is dragged somewhere else, clicking any of those drop-downs does not show the list. The user's
example: open *Pivot points standard*, drag the settings window to another place, then try to open any drop-down —
nothing appears.

### What you expected

Every selector in the indicator settings window should open its list wherever the window is on screen, just as it
does before the window is moved.

### Steps to reproduce

From opening the chart:

1. Open a chart that has **Pivot points standard** (the built-in study; in the captured baseline it is on
   `NSE:CRYSTALCP`, 1D, candles, RTH, with `pivotType=Traditional`, `pivotAnchor=Auto`,
   `maxHistoricalPivots=15`, `isDailyBased=true`, `showLabels=true`, `showPrices=true`, `positionLabels=Left`,
   `linewidth=1`).
2. Open that study's settings window.
3. Use one of its drop-downs (for example *Pivot type*, *Pivot anchor* or *Label position*) — the list opens.
4. Drag the settings window to a different place on the screen.
5. Click any of the same drop-downs again — the list does not appear.

### How often does it happen?

Every time after the window has been moved, as reported by the user.

### Where the chart's data comes from

Not chart related — the problem is in the settings window's own controls. (The captured baseline uses a data
plugin: Zerodha (enctoken) 0.6.9.)

### Diagnostic info

Captured with `get_diagnostics` on 26/11/2026, **before** the settings window was moved (the tools cannot move it):

```text
### Diagnostic info

- App: OpenTrader 0.5.1 (built 26/11/2026), desktop
- System: Windows 10 Pro (kernel 19045), x86_64, Intel(R) Core(TM) i5-4590 CPU @ 3.30GHz (4 threads), 8.5 GB RAM, web view 158.0.4370.33
- Window: 1536×824 at 125% scaling (screen 1536×864), app zoom 100%, language en
- Tabs: 2 open; this tab: layout s, 1 chart
- Chart 1 (focused): NSE:CRYSTALCP, 1D, candle, RTH session, plugin Zerodha (enctoken) 0.6.9, 1 bars loaded
  - Studies: Volume; Monthly Levels (Expiry H/L) (show_history_levels=false) [hidden]; Exponential moving average [hidden]; Square Root Levels (Daily) [hidden]; MadhaN Pivot (Study) [hidden]; OI Chain Profile (show_current_price_line=false, positive_colour_right=#00E676, negative_colour_left=#FF4B4B, anchor_bars_past_last_bar=30, expiry_yymmm=26DEC, ce_pe_separation_of_strike_step=28, ce_colour_right=#00E676, pe_colour_left=#FF4B4B) [hidden]; Monthly Levels (Expiry H/L) [hidden]; Pivot points standard [hidden]
- Bar Replay: off
- Plugins: Zerodha (enctoken) 0.6.9 (datafeed, broker; connected)
- Proxy: none; app update: up-to-date
- Recent errors: none
- Taken: 26/11/2026 18:43:27
```

### App version

0.5.1, desktop.

### Screenshots or video

Not captured: the available tools cannot open or move the indicator settings window. Add a screenshot of the whole
app with the settings window dragged away from its original position and a drop-down clicked (list missing), plus
one with the window unmoved showing the list open.

### Anything else

No error message was shown and the diagnostic report lists no recent errors. Moving the window back appears to be
the only workaround. Searched the existing issues — no open report for drop-downs in a moved dialog; the closest
old item is #277, a closed refactor of the shared dialog/popover controls.

Guess (one line): the popup may be positioned against the page instead of against the moved settings window.
