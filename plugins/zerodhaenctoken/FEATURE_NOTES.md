# Feature note: lazy daily-history loading for plugin symbols (upstream app change)

Status: **not implemented in the plugin**. The eager behaviour is kept intentionally.

## Observed

On a symbol change, OpenTrader asks for the whole daily history in one call:

```
data.bars BSE:TCS 1D: 4651 bars (03/01/2000-22/11/2026, countBack=7015, 54 events)
```

The plugin then splits that window into 1900-day Kite requests (4 calls here), merges them and returns the bars.
All of those requests happen immediately, before the user scrolls.

## Why the plugin cannot make this lazy

- `src/data/datafeed.ts:112` sets `DAILY_FULL_DAYS = 7560`; `dailyConfig("1D")` (line 154) uses it for the initial
  daily load. The app, not the plugin, chooses this window.
- `history` in `data.symbolInfo` is not an "initial window" hint. The backend clamps the requested `from` to
  `now - history` in `src-tauri/src/plugins/data.rs:491` (`reachable()`), so reducing `history.day` would make
  scrolling stop at that depth rather than load older pages later.
- Older pages do exist — `getDailyHistoryBefore` → `plugins::before` (`data.rs:629`) calls `data.bars` with an older
  window — but the initial 7560-day request already covers everything, so they are never needed on a day chart.

## Proposed upstream change

For symbols served by a data plugin, use a small initial daily window (e.g. 400 days, as
`getLastYearDailyBars` already does at `src/data/datafeed.ts:724-733`) and let `getDailyHistoryBefore` page older
windows when the user scrolls. Then this plugin would fetch one 1900-day chunk at open and the rest on demand,
avoiding a burst of Kite calls that can hit its rate limit.

Until that app change lands, the plugin keeps the eager full-history code path.
