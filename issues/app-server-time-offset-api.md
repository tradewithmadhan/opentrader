# Feature request — a common server-time/offset API so charts and plugins use corrected time

Status: **FILED** as https://github.com/deepentropy/opentrader/issues/422 (27/11/2026), on the user's explicit instruction. Do not make further changes to the live issue without the user's instruction.

Paste everything below the line into the issue body.

---

### What I'm trying to do

Keep chart bars and live events aligned with the exchange when the computer's clock is wrong, for **every** data plugin, without each plugin inventing its own clock correction.

### How it works today

A plugin's history bars carry real exchange timestamps, but live quotes are stamped by the plugin's local clock (`data.quote.time`), and the app falls back to its own `Utc::now()` when that field is missing. There is no server-time call, no offset, and no correction step anywhere in `src/` or `src-tauri/`.

If the PC clock is off:

- the forming bar is stamped at the wrong time and does not join the historical series;
- after the #421 fix, seconds bars and a script's `request.security_lower_tf(…, "5S", …)` bars get their trade time from that same quote timestamp, so they land on the wrong bar too;
- session countdown, quote times and day boundaries shift.

The two processes are on the same PC so they agree with each other; the error is against the exchange. Windows normally keeps the clock synced, which is why this is usually invisible — but it is not guaranteed.

### How another charting app does it

TradingView's Charting Library datafeed implements `getServerTime(callback)`. The library calls it and the callback receives the data feed's current **server/exchange UNIX time in seconds**; the chart uses it to align its time scale instead of trusting the browser clock. Exchange-provided timestamps then flow into the bars, so a user's wrong local clock does not move them.

### Requested behaviour

1. Add a server-time exchange to the plugin protocol: the app can ask a connected data plugin for its current server/exchange time (e.g. an app → plugin `data.serverTime` request answered with a UNIX time), and/or a plugin can announce its current server time in a notification.
2. The app computes one offset (`server time − local time`) from the best available source — the data plugin first, otherwise a time service it can reach itself — refreshes it periodically, and keeps using the local clock in between with that offset applied.
3. Charts and live data use the corrected time for forming bars, quote/tick timestamps, session countdown and day/session boundaries, so all data plugins benefit from one implementation.
4. Expose the corrected time to plugins as well (for example in the script/engine context or a small helper in the protocol), so a plugin that stamps its own quotes does not have to repeat the offset logic.
5. Keep a graceful fallback: when no plugin or time source answers, behave exactly as today (local clock), with no visible failure.

### Why it matters

A wrong PC clock currently misplaces live bars and, since #421, also the seconds bars and script lower-timeframe bars that now update from plugin quotes. A single app-level offset fixes every plugin and every chart, keeps live data aligned with the exchange-provided history, and removes the need for plugin-specific clock workarounds.

### Additional context

We verified that the HTTP `Date` header from servers we already call (e.g. NSE's archive host) gives a server epoch within about a second of local time over a normal request, so a plugin can supply an accurate server time from connections it already makes, without a new network dependency.
