# Upstream proposal — for filing on deepentropy/opentrader

Title of the issue: **Chart history: the 7560-bar daily preload reaches data plugins uncapped and unpaced — give sources a way to declare their per-timeframe limits and rate budget**

Fork reference: `tradewithmadhan/opentrader`, branch `sample-web-clean`. Everything below was
measured against a live broker-gateway deployment (OpenAlgo) through a Plugin API 2 data plugin
(log from 17/11/2026). Our fork also carries a built-in OpenAlgo provider and a `historyLimits()`
source capability that upstream does not have — those are cited as **precedent** in "What we
built in our fork", not as existing upstream code; the facts and the suggested enhancement stand
on their own.

Paste everything below the line into the issue body.

---

### The facts (verifiable against a gateway deployment)

1. **Gateway history endpoints cap per interval family, and the caps are not one number.** On the
   measured deployment: minute tables serve ~30 calendar days per request, second tables ~3,
   daily ~365. A "bar count" also means different wall time per timeframe — 2880 bars ≈ 38 days
   at 5m but ≈ 11 years at 1D — so any per-request limit has to be per family.
2. **The deployment rate-limits history behind one shared budget.** Bursts are throttled
   (429/5xx), and the rate limit counts every endpoint — it does not care which timeframe asked.
3. **Some deployments' daily endpoint answers only its last day.** Daily bars must then be
   synthesized from intraday rows — on the measured deployment, ~2 backend calls per year of
   daily history, plus 2 to detect where history ends.
4. Some deployments answer a **single stray row for any daily window** (not an error, not
   history) — a client has to be suspicious of short daily answers, not just empty ones.

### What the app does with that today

`DAILY_FULL_DAYS = 7560` (`src/data/datafeed.ts:110`) loads ~30 years of daily bars up-front on
every symbol change, as a quick-paint ask (`countBack: 10`) plus the full preload. For the
built-in provider path this is safe — the core clamps the window to the probed history floor
(`clamp_to_floor` + `BarFamily`, `crates/core/src/history/mod.rs`). But **plugin-routed bars
bypass all clamping**: `src-tauri/src/plugins/data.rs:329` forwards the frontend's `countBack`
touched only by `MAX_BARS = 50_000`. A plugin has no way to declare "my daily history floor is
here / my minute endpoint caps at 30 days/request / I can afford N requests per second" — so it
absorbs the 7560-bar ask internally and fires a burst of backend calls the app never sees.

### Observed — one symbol change (live plugin log, 17/11/2026)

```
data.bars {"countBack":10,   "resolution":"1D", "ticker":"NFO:NIFTY23NOV26FUT", ...}   // quick paint
data.bars {"countBack":7560, "resolution":"1D", "ticker":"NFO:NIFTY23NOV26FUT", ...}   // contract listed 3 weeks earlier: answers 57 bars
data.bars {"countBack":2880, "resolution":"5",  ...}                                   // answered ~780 (plugin's internal synthesis cap)
data.bars {"countBack":2880, "resolution":"5",  ...}                                   // page-back, re-asking the ORIGINAL count
data.bars {"countBack":10,   "resolution":"1D", "ticker":"NFO:NIFTY23NOV26FUT", ...}   // same symbol re-selected 65 s later: identical ask again
```

- The 7560-bar preload is sent for a contract with **57 days** of history; the plugin still pays
  a full synthesis scan for a 30-year window on every symbol change.
- With the plugin's internal chunking, the 7560 ask alone turns into **~12 backend `/history`
  calls**; one symbol change totals **~15–19 calls within a second** — exactly the burst a
  rate-limited gateway throttles. The app cannot know or pace it, because the limits live
  inside the plugin.
- The intraday ask comes back short (the plugin's per-call cap), so the datafeed pages back —
  re-asking `countBack: 2880` instead of the ~780 that were served.
- Re-selecting the same symbol re-asks the identical quick-paint window; nothing dedupes it.

Notes: only the focused chart fetches history (verified from the log — other open tabs load
`symbolInfo` + quotes only), so the cost is per symbol change, not per open tab.

### Suggested enhancement

**1. A history capability on the data-source contract, per timeframe family + one rate budget.**

```ts
type HistoryRequest = {
  lookbackDays?: number;      // how far back this family goes at all
  maxDaysPerRequest?: number; // largest calendar window one request should cover
  maxBarsPerRequest?: number; // …or in bars, whichever the endpoint caps by
};
type HistoryLimits = {
  second?: HistoryRequest; minute?: HistoryRequest; day?: HistoryRequest;
  requestsPerSecond?: number; // the gateway's rate limit — one budget across all families
} | null;
```

The host then, per the resolution being loaded:
- sizes the up-front daily preload to what the family's budget can serve
  (e.g. `min(DAILY_FULL_DAYS, maxDaysPerRequest × maxRequestsPerLoad)`) instead of a fixed 7560;
- sizes and paces scroll-back pages to what the family declared **and actually returned** (a page
  that came back short is re-asked with the served count, not the original ask);
- honors `requestsPerSecond` across families — the quick-paint, the preload and the intraday
  window of one symbol change queue through one pacer instead of firing concurrently;
- backs off on 429/5xx instead of paging harder into a throttled endpoint;
- treats a 1-row daily answer for a wide window as the "no daily history" signal some
  deployments emit (falls back to synthesis once, not on every reload).

Sources that declare nothing keep today's behaviour, so the built-in/gateway path is unaffected.

**2. The plugin-path equivalent of `clamp_to_floor`.** Let a plugin declare its history floor /
per-family caps over the plugin wire, and clamp `countBack` in `plugins/data.rs` exactly like
the core already does for built-in providers. Today a plugin cannot refuse a 7560-day ask
without answering "no data".

**3. Dedupe unchanged repeat asks.** Re-selecting a symbol re-asks an identical window seconds
later; serving it from the in-flight or just-completed load (per tab, per resolution) removes
one request class entirely.

### What we built in our fork (precedent, not a request)

The fork ships a built-in OpenAlgo provider with `historyLimits() => ({ second: 30, minute: 30,
day: 365 })` (a plain lookback — the per-request caps above generalize it), and the OpenAlgo
plugin does the chunking internally: daily synthesized in year chunks, each cached, the walk
stopping at the source's intraday floor. That softened repeats but not the first burst — the
capabilities above are what remove it, and they belong in the app so every plugin benefits
instead of each one re-implementing limits blind.

Happy to test a build against a live deployment.
