# Upstream proposal — for filing on deepentropy/opentrader

Title of the issue: **Chart history: the 7560-bar daily preload bypasses every history cap on the plugin path, and requests are not paced to a source's limits**

Fork reference: `tradewithmadhan/opentrader`, branch `sample-web-clean`. Observed with a live
OpenAlgo data plugin (Plugin API 2) against a self-hosted deployment; plugin log from 17/11/2026.
This replaces two earlier drafts — corrected against the current code (upstream 0.4.26):
`clampLookback` no longer exists (the mechanism is now `clamp_to_floor` + `BarFamily` in
`crates/core/src/history/mod.rs`), and the "history is fetched for every open tab" claim is false
(verified from the log: only the focused chart's symbol gets `data.bars`; other tabs load
`symbolInfo` + quotes only).

Paste everything below the line into the issue body.

---

### Summary

Every symbol change on a daily chart fires **two** daily history requests: a quick-paint ask
(`countBack: 10`) and a full preload (`countBack: 7560` — `DAILY_FULL_DAYS` in
`src/data/datafeed.ts:110`). The preload exists so scroll-back is instant, and for the **built-in**
provider it is safe: the core clamps the window to the probed history floor (`clamp_to_floor` +
`BarFamily`, `crates/core/src/history/mod.rs`). But **plugin-routed bars bypass all of it**:
`src-tauri/src/plugins/data.rs::bars` forwards the frontend's `count` untouched (only clamped to
`MAX_BARS`), and `historyLimits()?.day` — the source capability that exists precisely for this
(`src/data/sources/types.ts:117`) — is only read to render a "history exhausted" note
(`src/window/chart/view/symbol-load.ts:316`), never to cap or pace the requests themselves.

The deeper problem: the app has no notion of a **request budget**. A source behind a rate-limited
gateway cannot tell the app "I can serve at most N bars per request and afford ~M requests per
symbol load" — so the app asks 7560 bars and pages aggressively, and the plugin absorbs it by
firing a burst of backend calls the app never sees.

### Observed — one symbol change (live log, 17/11/2026)

```
data.bars {"countBack":10,   "resolution":"1D", "ticker":"NFO:NIFTY23NOV26FUT", ...}   // quick paint
data.bars {"countBack":7560, "resolution":"1D", "ticker":"NFO:NIFTY23NOV26FUT", ...}   // contract listed 3 weeks earlier: answers 57 bars
data.bars {"countBack":2880, "resolution":"5",  ...}                                   // answered ~780 (plugin's synthesis cap)
data.bars {"countBack":2880, "resolution":"5",  ...}                                   // page-back, because countBack wasn't served
data.bars {"countBack":10,   "resolution":"1D", "ticker":"NFO:NIFTY23NOV26FUT", ...}   // same symbol re-selected 65 s later: identical ask again
```

- The 7560-bar preload is sent for a contract with **57 days** of history; a shallow plugin must
  still pay a full synthesis scan for a 30-year window on every symbol change.
- Because the plugin's per-call synthesis is capped (~780 bars), the 2880-bar intraday ask comes
  back short and the datafeed pages — one symbol change becomes 5 requests where 2 would do.
- With the plugin's internal chunking (each daily year chunk = 1 probe of the daily endpoint +
  1 hourly synthesis call), the 7560 ask alone turns into **~12 backend `/history` calls**;
  one symbol change totals **~15–19 calls within a second**. On a rate-limited deployment that
  burst is what the gateway sees and throttles — and the app cannot know it happened, because
  the limits live inside the plugin.
- Re-selecting the same symbol re-asks the identical quick-paint window; nothing dedupes it.

### What's needed

**1. Honor `historyLimits()?.day` when building the daily ask.** The capability is already plumbed
end to end; use it in `datafeed.ts` to size the up-front lookback per source (7560 stays the
fallback for a source that declares nothing) instead of only annotating exhaustion.

**2. The plugin-path equivalent of `clamp_to_floor`.** Let a plugin declare its history floor /
lookback cap so `plugins/data.rs::bars` clamps `countBack` exactly like the core does for
built-in providers — a Plugin API 2 field (or reuse of `historyLimits` over the plugin wire).

**3. A rate-limit-aware request budget for history.** Extend the source capability so a source can
declare what one request costs it, and have the host plan around it:

```ts
historyLimits(): {
  day?: number;            // existing: max lookback
  maxBarsPerRequest?: number;  // largest window one request should cover
  maxRequestsPerLoad?: number; // soft budget of history requests per symbol load
  requestsPerSecond?: number;  // pacing for paging / retries
} | null
```

The host then:
- sizes the up-front preload to what the budget can serve (e.g. `min(DAILY_FULL_DAYS,
  maxBarsPerRequest × maxRequestsPerLoad)`) instead of a fixed 7560;
- sizes and paces scroll-back pages to what the source declared **and actually returned** (a page
  that came back short is re-asked with the served count, not the original ask);
- serializes/queues a symbol's history requests instead of firing the quick-paint, the preload
  and the intraday window concurrently.

Why this shape: today the chunking and pacing live *inside* the plugin, invisible to the host —
the plugin must either fire a burst (rate-limited, throttled, slow) or answer short (and the app
pages it, firing more). A declared budget turns the invisible burst into a planned 2–4 requests,
and every source that declares nothing keeps today's behaviour.

**4. Dedupe unchanged repeat asks.** Re-selecting a symbol re-asks an identical window seconds
later; serving it from the in-flight or just-completed load (per tab, per resolution) removes one
request class entirely.

### Notes

- Only the focused chart fetches history (verified) — no per-tab change is needed; the cost is
  per symbol change, not per open tab.
- Plugin-side mitigations already shipped in the fork's OpenAlgo plugin (year-chunk caching,
  floor detection that stops the daily walk) soften the repeats but not the first burst; the
  request budget is what removes it.

Happy to test a build against a live deployment.
