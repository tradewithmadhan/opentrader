# Note: lazy daily-history loading

Status: **implemented plugin-side**. No upstream app change is required.

## The problem

On a symbol change, OpenTrader asks for a wide daily window in one call:

```
data.bars BSE:TCS 1D: 4651 bars (03/01/2000-22/11/2026, countBack=7015, 54 events)
```

Filling that whole window means several 1900-day Kite requests at once, which risks Kite's rate limit. The app
hardcodes `DAILY_FULL_DAYS = 7560` (`src/data/datafeed.ts:112`), and `history` in `data.symbolInfo` is a hard
scroll-back clamp (`reachable()` in `src-tauri/src/plugins/data.rs:511`), so lowering the declared depth would stop
scrolling rather than defer loading.

## The fix

The plugin protocol explicitly allows a `data.bars` answer with **fewer, newest bars**: "You may return fewer, the
newest ones: the app then asks for the older ones before the oldest bar you returned when the chart needs them" (PROTOCOL.md, `data.bars`). So `bars()` now fetches only the newest chunk of the requested window:

- day: at most `historyDaysDaily` days (default 1900) — one Kite call
- intraday: at most `historyDaysMinute` days (default 30) — one Kite call

When the user scrolls back, the chart calls `getBarsBefore` (`src/window/chart/view/history.ts:230`) →
`plugins::before` (`data.rs:629`) with an older `to`, and the plugin serves the next older chunk. The per-symbol
`history` depth still comes from NSE's listing dates (capped at 30 years of days / 10 years of minutes), so the app
keeps allowing scroll-back all the way to each symbol's listing.

Result: one Kite request on symbol change, one per scroll page, no rate-limit burst, full listing-based history still reachable.
