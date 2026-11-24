# Let a plugin reset its cached symbol info when it decides (drop the 1-hour TTL)

Status: **FILED and corrected** as https://github.com/deepentropy/opentrader/issues/400 (24/11/2026). The original
body wrongly kept the 1-hour TTL and proposed a per-symbol list; the user rejected both, and the body was replaced on
24/11/2026 with the text below after explicit confirmation. Do not make further changes to the live issue without
the user's instruction.

## Problem

`src/data/datafeed-rest.ts` caches a successful `getTickerInfo` answer for one hour (`INFO_TTL_MS = 60 * 60_000`).

Ticker info (description, name, type, exchange, currency, session, tick scale, point value) is static during a live
market session. A 1000-symbol watchlist, and several watchlists across tabs, therefore make the app ask the plugin for
**all symbols again every hour** with nothing having changed. That is a large, pointless burst of `data.symbolInfo`
requests.

A fixed longer TTL is not the answer either: any timer re-asks everything on a schedule, regardless of whether the
source changed. Only the plugin knows when its reference data was reloaded (a new day's instrument master, a settings
change, a new contract list).

## Requested behaviour

1. **Remove the time-based expiry for successful `getTickerInfo` answers.** Keep them until the source says they are
   stale. Failed and "unknown symbol" answers keep their current short handling.
2. The plugin can send a simple notification, with **no per-symbol list**:

   ```json
   {"jsonrpc":"2.0","method":"data.symbolInfoChanged","params":{}}
   ```

   On receipt the app clears the cached symbol info **for that plugin's symbols** and bumps `tickerInfoEpoch`, so the
   watchlist name effect and the Details panel re-ask those symbols once. Symbols of other plugins and of the app's own
   gateway are untouched.
3. Keep the existing `forgetTickerInfo()` on `pluginsChanged` — that flush is what fixed the stale "unknown symbol"
   case in #371 and must not regress.

The plugin decides when to send it — typically once, after its daily instrument master reload, i.e. at the next day's
session. No timer in the app, no per-symbol bookkeeping in the plugin, no payload to construct.

## Why this shape

- The notification is a **reset signal**, not a delta: the plugin already knows which exchange set it serves, so it
  doesn't have to enumerate thousands of symbols in a message.
- It matches the existing protocol pattern of no-payload invalidation notifications, e.g. `pluginWatchlistsChanged`.
- It is cheap: one notification per day per plugin instead of 1000 requests per hour per watchlist.

## Routing sketch (for discussion, not a patch)

- `src-tauri/src/plugins/data.rs`: handle `HubEvent::Notification { method: "data.symbolInfoChanged", plugin, .. }`
  alongside `data.eventsChanged` / `pluginFieldsChanged`; drop the plugin's cached infos and emit a changed event so
  `forgetTickerInfo()`-equivalent refreshing happens in the windows.
- `src/bindings.ts` / `bindings-fallback.ts`: add the event listener type.
- `src/data/datafeed-rest.ts`: remove `INFO_TTL_MS` expiry for successful answers; subscribe to the new event and
  invalidate, then bump `tickerInfoEpoch`.

## Verification suggestion

- Unit test: a successful answer is still returned after a long interval with no notification.
- Unit test: `data.symbolInfoChanged` from plugin A clears only plugin A's cached symbols and bumps `tickerInfoEpoch`,
  so the watchlist/Details effects re-ask once; plugin B and gateway symbols survive.
- Existing `src/data/__tests__/ticker-info-cache.test.ts` (not-found, `pluginsChanged`, in-flight request) must keep
  passing unchanged.
