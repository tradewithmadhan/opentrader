# Upstream proposal — for filing on deepentropy/opentrader

> **FIXED upstream** in `3dec00db` (issue #386), adopted in this fork. Kept for reference.


Status: **filed upstream** as [deepentropy/opentrader#386](https://github.com/deepentropy/opentrader/issues/386) on 22/11/2026. Observed against a third-party broker data plugin (Plugin API 3).

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

### The rule that should govern exhaustion

A plugin already declares, per symbol, how much history it can serve: `history` in `data.symbolInfo`
(`{"minute": …, "day": …}`, calendar days per family). The app reads it in `reachable()`
(`src-tauri/src/plugins/data.rs:511`) to clamp how far back it asks, but the frontend never uses it to decide
that history is finished. That is the missing piece.

`historyExhausted` should be set **only when the loaded series has reached the depth the plugin declared for that
symbol** — i.e. the oldest loaded bar is at or beyond `now − history[family]` (with a small tolerance for
sessions/holidays) — and the source has returned no older bars. A short, non-empty page is a normal partial answer
(a plugin may return fewer, newest bars) and must simply be prepended; the next `beforeSec` becomes the new oldest
bar and the app asks again. A zero-bar answer while the oldest bar is still **younger** than the declared depth
must be treated as a transient/short answer (retry with backoff), not as exhaustion. A page discarded by the
stale-page guard at line 346 must never touch `historyExhausted`.

Concretely, for the `NSE:TCS` case above: `history.day = 8124` days (25/Aug/2004). Two pages brought the oldest bar
to ~2016, which is still well inside the declared depth. The app should keep asking for older pages until the
oldest bar reaches 25/Aug/2004; only then is an empty answer genuine exhaustion.

### Implementation note

The Rust side already holds the per-symbol `Info` (with `history`) in `src-tauri/src/plugins/data.rs`; the TS
`TickerInfo` returned to the frontend does not currently expose it. Either surface the declared depth to the
frontend, or let `plugins::before` distinguish "no older bars because the declared depth was reached" from
"no older bars returned inside the declared depth", so the chart does not have to guess.
