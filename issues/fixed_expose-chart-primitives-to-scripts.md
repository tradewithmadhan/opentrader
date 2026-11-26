<!-- Filed as https://github.com/deepentropy/opentrader/issues/410 (25/11/2026), feature request (label: enhancement). File-only marker: the issue body carries no status line, and this markdown (minus this comment) is exactly what is published. -->

# Feature request — expose chart primitives to OakScript scripts

> **FIXED upstream** in `d1edcde7, 7218e3e9 / 410` (issue #410: visible-range reads, pane-anchored drawings via `xloc.pane`), adopted in this fork. Kept for reference.

**Area:** Script editor / scripts · **Importance:** I would use it regularly · **Labels:** enhancement

---

### What do you want to do?

I want an OakScript indicator to be able to draw the way the app's own studies do — specifically a **right-anchored profile**: horizontal bars at each price/strike level, with one side in one colour extending left and the other in a second colour at the right, plus a dashed line at the current price, staying pinned to the chart's right edge next to the price axis as I scroll and zoom. The concrete case is an **options open-interest profile** (CE green on the right, PE red extending left), like the built-in "Visible range volume profile" (VRVP).

Today a script can only emit `plots`, `lines`, `boxes`, `labels`, `polylines` and `tables`, all anchored to bar coordinates. It cannot use any of the screen-space / canvas primitives the app's built-in studies use (`VbpPrimitive`, `TpoPrimitive`), so a fixed-width drawing cannot stay pinned to the pane's right edge.

### Proposed behaviour

1. Give scripts a general way to emit a **primitive drawing** (a script-facing `primitive()` / `drawPrimitive()` output carrying a kind + data), which the host attaches to the main series the way it attaches `VbpPrimitive` / `TpoPrimitive` today. This is a general capability, not just for profiles.
2. At minimum, expose the **profile primitives** (`vbp`, `tpo`) with their draw shapes (`VbpDraw` / `TpoDraw`), a Left/Right placement option and the `relative` screen-space mode, so a script can build a profile from its own hard-coded or computed data.
3. Ideally, add Pine parity for visible-range built-ins: `chart.right_visible_bar_time` and `chart.left_visible_bar_time`, and let a script opt into recalculation when the visible range changes (e.g. `indicator({ usesVisibleRange: true })`), so scripts can implement their own right-anchored or screen-space drawings without app-source patches.

A generic "primitives for scripts" mechanism is preferred over one-off result fields, so indicator authors can build profiles, anchored drawings and other canvas overlays from an indicator file.

### Do you know another charting app that does this?

TradingView Pine Script exposes `chart.right_visible_bar_time` / `left_visible_bar_time`, and studies like its Volume Profile are drawn anchored to the visible range's right edge and stay pinned while scrolling. The reference screenshot for the OI profile shows candles on the left and the CE/PE bars at the right edge with a dashed current-price line.

### Workarounds you use today

- The app's built-in **Visible range volume profile** anchors right correctly, but it plots **volume**, not custom OI data — a script cannot feed it its own values.
- A script can import `visibleRangeOf(bars, chart)` from `lightweight-charts-indicators/helpers`, which reads the app's internal `chart.rightVisibleBarTime`, but user-script indicators do not recalculate on scroll (only studies flagged `usesVisibleRange` do), so the value goes stale as soon as the chart is panned.
- `box.new(..., extend="right")` pins a box's right edge to the pane, but stretches the box from its left bar to the edge, so a fixed-width segment pinned to the right is not achievable.
- Upstream already has OI data plumbing + a **time-series** Open Interest indicator (#383), but that draws OI over time in its own pane, not a right-anchored strike profile, and it still cannot be turned into one from a script.

### Anything else

- The OakScriptJS inventory (https://github.com/deepentropy/oakscriptJS/blob/main/docs/inventory.md) reports 99.7% function completion but has **no `chart` namespace** — `chart.right_visible_bar_time` / `left_visible_bar_time` are built-in variables, not functions, and are not listed even in the "Standalone" bucket. The high score does not imply Pine parity for `chart.*` built-ins.
- App source of the relevant primitives (for the developers): `src/window/chart/profiles/vbp-primitive.ts` (`VbpPrimitive`), `tpo-primitive.ts` (`TpoPrimitive`), attached in `src/window/chart/indicators/indicator-layer.ts` when a result has `vbp` / `tpo`; scroll recalculation in `indicator-controller.ts` via `usesVisibleRange`.
- I verified a script's `result` schema (`plots`, `lines`, `boxes`, `labels`, `polylines`, `tables`, …) has no primitive field, the `chart` global has only `fg_color`, `bg_color`, `point`, and no drawing primitive is exported from an importable package entry (`lightweight-charts-indicators/render` exports only the renderer helpers).
- Environment: OpenTrader desktop fork based on `deepentropy/opentrader`; `oakscriptjs` 0.19.0, `lightweight-charts-indicators` 0.26.4 (same versions upstream/main). Reproduced on NSE_INDEX:NIFTY / NFO futures, 5m and 1D.
