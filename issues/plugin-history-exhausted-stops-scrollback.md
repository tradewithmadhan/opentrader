# Upstream proposal — for filing on deepentropy/opentrader

Status: **open**. Observed 22/11/2026 against the Zerodha enctoken data plugin (Plugin API 3).

Title of the issue: **Chart history: a partial older page marks plugin history exhausted and permanently stops scroll-back**

Fork reference: `tradewithmadhan/opentrader`, branch `sample-web-clean`. The facts below come from the running app and its plugin log; the app code cited exists upstream.

Paste everything below the line into the issue body.

---

### What we saw

On a day chart of `NSE:TCS` (listed 25/Aug/2004), the plugin declares `history.day = 8124` days in `data.symbolInfo`, so the app is allowed to ask back to the listing date. The plugin log shows exactly two `data.bars` requests:

```
11:13:41  data.bars NSE:TCS 1D: 1290 bars (25/08/2004-22/11/2026, countBack=7015)
11:13:42  data.bars NSE:TCS 1D: 1286 bars (25/08/2004-08/09/2021, countBack=5658)
```
No further request reaches the plugin. The loaded series stops at the oldest bar of page 2 (~2016), and scrolling further does nothing. Other symbols show the same two-page pattern; a 30-minute chart stops at the end of its second page (~2015).

### What the plugin does

`data.bars` returns **fewer, newest bars** than the requested window: one Kite-sized chunk per call (≤1900 days for `1D`, ≤30 days for intraday). This is explicitly allowed by PROTOCOL.md:

> You may return fewer, the newest ones: the app then asks for the older ones before the oldest bar you returned when the chart needs them.

Both pages above are healthy, non-empty responses with ~1280 bars each. The plugin has older data it would serve on a third request; that request is never made.

### The app behaviour

`src/window/chart/view/history.ts` sets `historyExhausted` when a landed page contains no bar strictly older than the current oldest:

```ts
const older = rows.filter((r) => (r.time as number) < beforeSec);
if (older.length === 0) { historyExhausted = true; return; }   // lines 347-351
```
The same test appears again in the sync/depth loader at lines 679-684. Once the flag is true, `maybeLoadOlder()` returns at line 290 and `startPrefetch()` returns at line 267, so no older page is requested. The flag is cleared only by `reload()` (line 935).

A page can also be discarded at line 346 when the oldest bar moved while it was in flight:
```ts
if (((isDaily ? cx.rawDaily[0]?.time : loaded()[0]?.time) as number | undefined) !== beforeSec) return;
```

Likely cause (guess): with read-ahead pipelining, a discarded or out-of-order page leaves a subsequent page with no strictly-older bins, which sets the flag even though the source has more history.

### Requested change

Do not treat a short-but-non-empty older page as end of history for plugin symbols. Treat a page as exhausted only when the backend actually returns zero bars, or when the oldest bar fails to advance across two consecutive pages. Keep the stale-page guard from latching `historyExhausted` on a discarded page.
