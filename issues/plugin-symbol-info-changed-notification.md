# Add a `data.symbolInfoChanged` notification so a plugin can invalidate cached symbol info

Status: **FILED** as https://github.com/deepentropy/opentrader/issues/400 (24/11/2026) as a feature request.
User confirmed the notification-based approach and explicitly rejected a time-based/next-morning TTL.

## Problem

`src/data/datafeed-rest.ts` caches a successful `getTickerInfo` answer for one hour (`INFO_TTL_MS = 60 * 60_000`).
The whole cache is cleared by `forgetTickerInfo()` when data plugins change (`pluginsChanged`, the #371 follow-up), but
there is no way for an **already-connected** plugin to tell the app that its reference data changed for one symbol or
a set of symbols.

Consequences:

- A plugin can reload its instrument master during the day (new contracts, a corrected description/tick/lot, a
  corporate-action change). Charts and watchlist rows already opened keep the old `symbolInfo` for up to an hour.
- The only workarounds today are to force a plugin reconnect (which clears everything) or restart the app.
- The watchlist asks `getTickerInfo` for every row when names are shown, so the stale data is visible on many rows at
  once, and a full cache flush makes all of them ask again.

This is not a request to change the TTL. Keep the one-hour cache and keep `forgetTickerInfo()` on `pluginsChanged` —
that flush is what fixed the stale "unknown symbol" case in #371.

## Requested behaviour

A data plugin can send a notification, e.g.:

```json
{"jsonrpc":"2.0","method":"data.symbolInfoChanged","params":{"symbols":["NFO:NIFTY26NOVFUT","NFO:NIFTY26NOV25000CE"]}}
```

- The app drops the cached `getTickerInfo` answer for exactly those symbols (whole symbols, as the plugin serves
  them) and bumps `tickerInfoEpoch`, so the watchlist name effect and the Details panel re-ask only those rows.
- An empty or absent `symbols` list can mean "all symbols of this plugin" — the same convention as
  `data.eventsChanged` (`{ exchanges, symbols: string[] | null }`).
- A plugin whose reference data is stable never needs to send it; most plugins, including a stable broker account,
  would send nothing in normal operation.

## Why this fits the existing protocol

The same channel already exists for other plugin-owned data:

| Notification | Payload | Effect |
|---|---|---|
| `data.eventsChanged` | `{ exchanges, symbols }` | re-read a plugin's chart events |
| `pluginFieldsChanged` | `{ symbol }` | re-read a symbol's instrument values |
| `pluginWatchlistsChanged` | (no payload) | re-read the plugin's lists |
| **proposed `data.symbolInfoChanged`** | `{ symbols }` | re-read only the changed symbols' info |

Routing would follow `data.rs`'s existing `HubEvent::Notification` match (where `data.eventsChanged` and
`pluginFieldsChanged` are already handled), and the bindings would gain the matching event type and listener.

## Verification suggestion

- Unit test: after the notification for one symbol, only that symbol's `getTickerInfo` is invoked again; unrelated
  cached answers survive; `tickerInfoEpoch` bumps so reactive consumers re-run.
- Unit test: an empty `symbols` list invalidates only that plugin's symbols, not other plugins' or gateway symbols.
- Existing `src/data/__tests__/ticker-info-cache.test.ts` (not-found, `pluginsChanged`, in-flight request) must keep
  passing unchanged.

## Note on the earlier idea

A "cache until the next trading morning" TTL was considered and is deliberately **not** requested here: a
time-based rule cannot know when a particular plugin actually changed its data, and would still leave stale info for
part of the day. An explicit plugin signal is both cheaper and correct.
