# Ticker info: keep a successful answer until the next trading morning, or until a plugin says it changed

Status: **draft — not filed**. Confirm with the user before posting to
https://github.com/deepentropy/opentrader/issues (feature request / optimisation, one problem).

## What was observed

- The watchlist asks `getTickerInfo` for **every row** when company names are shown
  (`src/window/right-rail/watchlist/cells.ts:53-69`, introduced 15/10/2026). A 1000-symbol list means 1000 symbol-info
  requests at once; prices themselves come only from the WebSocket tick stream, so this traffic is purely for names.
- The app caches a successful answer for **one hour** (`src/data/datafeed-rest.ts:94`,
  `const INFO_TTL_MS = 60 * 60_000`), then asks again for every symbol.
- `forgetTickerInfo()` clears the whole cache on `pluginsChanged` (22/11/2026, the #371 follow-up), so a plugin
  reconnect re-asks for every watchlist symbol again regardless of the TTL. This is deliberate and useful — it must
  stay for the case it fixed (a plugin that answered "unknown" while starting).

Live log from 23/11/2026 (Zerodha plugin, API 7) shows the burst at connect: `data.symbolInfo` for all ~30 symbols of
the active list within one second, then again after the master finished loading. The plugin answers them in parallel
from local cached data (no slow calls after the fork's plugin fixes), so the load is survivable — but it is avoidable
traffic, and at 1000 symbols it is a real cost.

## Why a longer cache is reasonable

The fields in a successful `getTickerInfo` answer (description, name, type, exchange, currency, session, tick scale,
point value) are reference data. They are static for a trading day for an individual instrument: a future/option's
description carries its own expiry and strike; there is no intra-day field in this answer that changes. The plugin
protocol also has separate, already-increasing channels for values that do change (`data.quote.fields` for instrument
values, `data.eventsChanged`, `pluginFieldsChanged`).

## Requested behaviour

1. A **successful** `getTickerInfo` answer is kept until the **next trading morning in the symbol's time zone**
   (or a day boundary in the user's local day if a session calendar is not available) instead of one hour.
2. A plugin can explicitly invalidate earlier: a notification such as `data.symbolInfoChanged` with either the whole
   set of its symbols or a list of changed symbols. The app then drops those entries and bumps `tickerInfoEpoch`, so
   the watchlist/Details effects re-ask only what changed.
3. Keep the existing `forgetTickerInfo()` on `pluginsChanged` — the new TTL must not reintroduce the stale
   "unknown symbol" bug from #371. Failed/unknown answers should keep their current short handling.

## Suggested implementation sketch (for discussion, not a patch)

- `datafeed-rest.ts`: derive the expiry from the next session start rather than a fixed duration; keep `FAILED_TTL_MS`
  for errors and the existing "not found" handling.
- `src-tauri/src/plugins/data.rs` + `bindings`: new `data.symbolInfoChanged` notification, routed like the existing
  `data.eventsChanged` / `pluginFieldsChanged`, calling `forgetTickerInfo()` for the affected symbols.
- A plugin sends it when its reference data actually changes (e.g. an instrument master reload that alters a symbol).
  Most plugins, including a stable Kite account, would never need to send it in normal operation.

## Verification suggestion

- Unit test: a successful answer is still cached after 90 minutes but not after the next trading morning.
- Unit test: `data.symbolInfoChanged` for one symbol makes only that symbol be asked again; the epoch bumps so
  reactive consumers re-run.
- Existing `src/data/__tests__/ticker-info-cache.test.ts` (not-found + pluginsChanged + in-flight cases) must keep
  passing unchanged.
