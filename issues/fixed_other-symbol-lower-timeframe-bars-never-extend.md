# Bug — `request.security_lower_tf` lower-timeframe bars never extend on scroll-back, for the chart symbol or another symbol

> **FIXED upstream** in `4c4516c4 / 424` (issue #424), adopted in this fork. Kept for reference.

Status: **FILED** as https://github.com/deepentropy/opentrader/issues/424 (27/11/2026), on the user's explicit instruction. Do not make further changes to the live issue without the user's instruction.

Paste everything below the line into the issue body.

---

### Steps to reproduce

1. Open a 5-minute chart with a data plugin serving second and minute bars.
2. Add an indicator that reads lower-timeframe data with `request.security_lower_tf`, e.g.
   `request.security_lower_tf(sym, "5S", () => volume)`, plotting the sum/count of the returned arrays per chart bar. `sym` may be the chart symbol (`""` / `syminfo.tickerid`) **or another symbol** — both reproduce it.
3. Let it load, then scroll the chart back into older history.

### Expected

The lower-timeframe bars extend to the same span as the chart's loaded bars, so the study keeps working over the older section.

### Actual

The chart loads older bars, but the lower-timeframe data stops at the window loaded on the first run. Two measurements:

- another symbol at 1 minute on a 5-minute chart: 2,000 chart bars (19–27 Nov), lower volume on only **375** of them (1,875 lower bars), covering 20–27 Nov;
- the **chart's own symbol** at 5 seconds on the same chart: lower volume on only **216** of 2,000 bars, covering 25–27 Nov out of 16 Oct–27 Nov.

A plain `request.security` at the **chart's** timeframe does extend correctly on scroll-back, so the problem is specific to the lower-timeframe path, not to which symbol is read.

### Diagnostic info

- App: OpenTrader 0.5.2 (built 27/11/2026), desktop
- System: Windows 10 Pro (kernel 19045), x86_64, Intel(R) Core(TM) i5-4590 CPU @ 3.30GHz (4 threads), 8.5 GB RAM, web view 158.0.4370.33
- Window: 1536×824 at 125% scaling (screen 1536×864), app zoom 100%, language en
- Tabs: 2 open; this tab: layout 2h, 2 charts
- Chart 1 (focused): NSE_INDEX:NIFTY, 1, candle, RTH session, plugin Zerodha (enctoken) 0.7.1, 1875 bars loaded
  - Studies: Volume; Previous Day Week Highs & Lows (dailyLookback=4, areHeadsEnabled=true) [hidden]; Exponential moving average [hidden]; Pivot points std_custom [hidden]; MadhaN Pivot (Study) (labels=false, p_ohlc=true, cam_2=true, cam_t_2=true, p_ohlc_2=true); Volume-weighted average price (showBand1=false) [hidden]; Monthly Levels (Expiry H/L) [hidden]; Square Root Levels (Daily) [hidden]; MA Cloud [hidden]; OI Chain Profile (value=Prev day OI, show_bar_values=true, baseline_strike_offset=0, baseline_side=Call, show_current_price_line=true, positive_colour_right=#00E676, negative_colour_left=#FF4B4B, anchor_bars_past_last_bar=30, expiry_yymmm=26DEC, ce_pe_separation_of_strike_step=28, ce_colour_right=#00E676, pe_colour_left=#FF4B4B); Syminfo Fields [hidden]; 5s Volume Spike (volume_threshold=6500, symbol=NFO:NIFTY1!, current_day_only=false) [hidden]; Other Symbol Volume Test (volume_symbol=NSE:RELIANCE); Other Symbol Lower Volume Test (volume_symbol=NSE:RELIANCE)
- Chart 2: NFO:NIFTY26D0125900PE, 1, candle, RTH session, plugin Zerodha (enctoken) 0.7.1, 1925 bars loaded
  - Studies: Previous Day Week Highs & Lows (dailyLookback=5, areHeadsEnabled=true) [hidden]; Exponential moving average [hidden]; MadhaN Pivot (Study) (cam=false, cam_2=true, cam_t_2=true, fibo_t_2=true) [hidden]; Option Side Switcher (CE↔PE); Volume
- Bar Replay: off
- Plugins: Zerodha (enctoken) 0.7.1 (datafeed, broker; connected)
- Proxy: none; app update: up-to-date
- Recent errors: none
- Taken: 27/11/2026 20:05:16

(The measurements above were taken in the same session on a 5-minute chart reading `NFO:NIFTY1!` at 1 minute; the diagnostic block is from the same chart after the symbols were changed while investigating.)

### Screenshot

The lower-volume pane showed columns only over the recent section and nothing over the older chart bars. A screenshot can be attached when filing.

### Observed in the source

`request.security_lower_tf` reads bars through `chartCtx.bars(symbol, timeframe)` (`oakscriptjs/src/script/index.ts:4177`). The chart controller wires that accessor to `store.bars(...)`, not `store.lowerBars(...)` (`src/window/chart/indicators/indicator-layer.ts:830-833`). `store.bars()` creates a **non-lower** entry with `wantFrom: Infinity` (`other-symbols.ts:229-238`), and `extend()` then refuses to page it back when the requested timeframe is below the chart's: `if (!e.lower && seconds && this.chartSeconds && seconds < this.chartSeconds) return;` (`other-symbols.ts:446`). The lower-specific accessor `store.lowerBars()` exists (`:843-845`) but is not the one this request uses. So these entries load only their initial window and never grow on scroll-back.

### Requested behaviour

Route lower-timeframe requests from `request.security_lower_tf` through the lower-bar path (or mark their entries as lower with a finite `wantFrom` derived from the chart's span), so older lower-timeframe bars are loaded as the chart scrolls back — for the chart's own symbol and for another symbol alike, with the same caps that apply to other lower-timeframe data. A plain `request.security` at the chart's timeframe should keep working as it does today.

### How often

Every time a study uses `request.security_lower_tf` at a timeframe below the chart's — whether the source symbol is the chart's own or another one — and the chart is scrolled into older history.
