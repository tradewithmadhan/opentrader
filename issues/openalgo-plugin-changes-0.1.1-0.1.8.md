# OpenAlgo plugin 0.1.1 - 0.1.8: every change, and what each means for upstream

Fork: `tradewithmadhan/opentrader`, branch `sample-web-clean`. The plugin is live-verified against
`shoonya.captainvizhuthugal.dpdns.org` (OpenAlgo deployment, shoo... a Shoonya broker behind it).
Dates dd/mm/yyyy. Commits: `a93d7bf1` 0.1.1, `d66a68ff` 0.1.3, `0cf71ed7` 0.1.4, `354958d1` 0.1.5,
`14e052c9` 0.1.6, `f31c6ed7` 0.1.7, `51224f5b` 0.1.8.

## The changes, in order

| Ver | What | Why it was needed | Side |
|-----|------|-------------------|------|
| 0.1.1 | The dharun company-profile GET is bounded (8 s) and an unreachable host backs the whole lookup off globally (60 s) | api.dharun.dev hung the first symbolInfo of every new symbol; the host's window is 15 s | plugin |
| 0.1.2 | Instrument masters are served from today's disk cache before any network pull; request paths (`symbolInfo`, tick) never wait for a pull (`rows_peek`) | A restart pulled 12 segments serialized (18-54 s each); symbolInfo missed its window for minutes. Disk-first answers in ~4 s | plugin |
| 0.1.3 | The profile GET connects by hand (HTTP/1.0) and tries the resolved addresses IPv4 first | The host machine's IPv6 path to Cloudflare black-holes the connect: urllib's DNS order cost two dead 8 s attempts (~16 s) per new symbol, past the 15 s window. Now ~0.2 s | plugin (environment) |
| 0.1.4 | The WS pong is a masked frame | The server pings every ~20 s; the unmasked pong is a protocol error, answered with `1002 "incorrect masking"` — the stream died at 19 s forever and the watchlist fell back to REST | plugin |
| 0.1.5 | Bar formation follows the built-in provider: the minute bucket is ltp-only, its volume is the day volume's delta since the bucket's first tick with one; the day bar keeps the WS day side; prev close from the tick's `close` field | WS ticks carry day OHLC + cumulative day volume + prev close, and the old code folded the day fields into every minute bar (day-high candles, day-total volumes) | plugin |
| 0.1.6 | The WS frame reader returns None for an incomplete read; the loop breaks back to recv | The loop answered an incomplete read by re-parsing the same buffer forever — the stream sat connected but deaf after its first frame (no subscribe ever sent, no pings answered) and a thread burned a full core (the app-wide slowness). **WebSocket data had never reached the app** | plugin |
| 0.1.7 | `broker.state` is pushed whenever its connected flag or its books move, and at least every 30 s; the books poll reads at once on the start | The connect-time push carries connected=False (no book read yet) and with no open positions nothing re-pushed — the host kept "Broker not connected" and the ticket refused a working plugin | plugin, **host contract** |
| 0.1.8 | `place()` checks the master's lotsize first: a non-multiple is refused locally with the next valid lot ("try 65 or more") | The ticket sends units (qty 1 on a lot-65 future); the broker rejected it in a round trip. The ticket has no lot stepper | plugin, **host gap** |

## What to raise upstream (host side)

1. **Product selector in the order ticket** — drafted in `issues/order-ticket-product-selection.md`.
2. **Lot-size quantity stepping in the ticket** — the lot size is already in the ticket's context
   (`pointValue`, see `issues/upstream-openalgo-plugin-changes.md`'s Change 1, adopted upstream).
   The qty input should step/snap by it (arrow keys and the risk-based sizing), instead of every
   broker plugin pre-rejecting wrong quantities like 0.1.8 does.
3. **The `broker.state` push contract** — the host treats the last pushed state as the truth and
   never re-asks. A plugin following the template's pattern (state on connect, then only on
   activity) leaves an account without open positions permanently "Broker not connected". Options:
   the host asks the plugin for its state when the ticket opens / a connection event fires, or the
   plugin template's stream loop documents and includes a periodic state push. Whichever is chosen,
   the template (`docs`/template plugin) should encode it so the next broker plugin does not hit
   the same trap.
4. **Stale watchlist symbols spam "Unknown symbol"** — every `data.symbolInfo` for a watchlist
   entry that no longer exists (an expired options contract, a renamed symbol) warns and errors
   again on every retry, for the whole session. The host could remember the miss and stop retrying
   (or surface "these watchlist entries no longer exist" once with a cleanup action).
5. **(Already drafted separately)** the history-request shapes: see
   `issues/upstream-history-performance.md` (7560 daily bars up front) and
   `issues/upstream-app-side-history.md` (upfront history volume).

## Plugin-side only (informational, no upstream action)

- 0.1.1/0.1.2/0.1.3: they exist because a slow deployment + tight host windows + a broken local
  IPv6 path need aggressive caching and address handling. No host change needed.
- 0.1.4/0.1.5/0.1.6: plain plugin bugs against the verified WS protocol (auth, masked pong,
  `market_data` shapes, ~20 s server pings) and the built-in provider's bar rules. The deployment's
  protocol facts for any other OpenAlgo plugin: bare master symbols on the WS (the `-EQ` suffix is
  the display form), `market_data.data.{ltp, open, high, low, close(=prev day close), volume}`,
  server ping every ~20 s expecting a masked pong, `unsubscribe_all` + whole-set `subscribe` on
  every subscription change.
