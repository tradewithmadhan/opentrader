# Next update: cache the Kite history window (single call for price + OI)

Status: **not done — follow-up after the API 6 release (0.3.0)**.

## What we saw in live

Opening an F&O chart with its `OI` indicator makes **two identical Kite REST calls** for the same window:

```
data.bars NFO:...FUT      5 ... → GET /historical/<token>/5?from=…&to=…&oi=1   (uses candles 1..6 = OHLCV)
data.bars NFO:...FUT_OI   5 ... → GET /historical/<token>/5?from=…&to=…&oi=1   (uses candle[6] = OI)
```

Both requests carry `oi=1`; the first throws candle[6] away, the second throws the prices away. There is no
history cache in the plugin, so two app requests become two Kite calls (rate-limit cost, latency).

Live OI is **not** affected: it arrives free in the WebSocket full packet (bytes 48-60), and subscribing the
`_OI` series already maps the base token to full mode — no extra REST call there.

## Planned fix

Cache the parsed history window in `ZerodhaSource`, keyed by `(token, interval, frm, to)`:

- `_history_one()` stores the parsed candles; the OI path reuses them instead of calling Kite again.
- Only the **newest** window is cached (a small bounded LRU, e.g. 8-16 entries) — the app asks overlapping
  ranges as it scrolls, and `bars()` already fetches one Kite-sized chunk per request.
- Keep the cache per token/interval/date-range, invalidate on day rollover (IST) and on master reload.
- Verify with a test that one `data.bars` price + one `data.bars` OI for the same window produces exactly one
  fake-Kite history call, and that scrolling to an older window produces a second call.

Care point: `bars()` may widen/clip the requested window (`_history_one` is called per chunk when a request
needs more than one Kite-sized window). Key the cache on the **exact** `(frm, to)` passed to `_history_one`,
not the app's original request, so a hit really is the same Kite URL.
