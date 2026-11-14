# Issue draft — paste into GitHub (deepentropy/opentrader)

**Title:** Scripts get no session corrections/holidays: special sessions (Sunday Muhurat etc.) are swallowed by the previous trading day in `time("D")` / `time_tradingday` / daily resampling

**Labels suggestion:** `bug`, `oakscript`, `sessions`

---

## Summary

`scriptChartContext()` builds the script chart context from the symbol's sessions but only forwards the plain session string. The symbol info's **corrections and holidays are never passed to scripts**, so on a special session that is not on the regular calendar, Pine-faithful session values break for every OakScript indicator/strategy:

- `time("D")` returns the **previous regular session's day start** on the special session's bars (not that session's own day) — the special session cannot be told apart from the previous session
- `time_tradingday` returns the previous regular session's trading day on those bars
- same-symbol `request.security(sym, "D", …)` resample misses the special session's bars

A script that detects a new day via `time("D")` therefore never sees the special session as its own period: a "previous day" accumulator keeps one merged period (previous session + special session combined), so the next regular session shows MERGED values (e.g. previous-session open, max(prev, special) high, close of the special session) instead of the special session's own OHLC.

## Reproduction

1. Chart NIFTY 50 on a 5-min chart across **Fri 06/11/2026 (regular)** → **Sun 08/11/2026 (special Muhurat session, off the regular Mon–Fri calendar)** → **Mon 09/11/2026 (regular)**.
2. Run a script that reads `time("D")` per bar (or `time_tradingday`), e.g. accumulate the previous trading day's O/H/L/C like a standard daily-pivot indicator.
3. Observe on Monday: `time("D")` on every Sunday-session bar equals Friday's day start, so the accumulator keeps Friday+Sunday as ONE period. Monday's "previous day" O/H/L/C = Friday's open, max(Fri, Sun) high, min(Fri, Sun) low, Sunday's close — TradingView gives the Sunday session's own OHLC (O 25,070.65 / H 25,131.85 / L 24,985.30 / C 24,986.40 on this data).

## Root cause

`src/window/chart/indicators/script-chart.ts` (~line 95):

```ts
const sessions = symbol ? cachedSymbolSessions(symbol) : null;
if (sessions) {
  ctx.timezone = sessions.timeZone;
  ctx.session = extended ? sessions.extendedSpec : sessions.regularSpec;  // plain string only
  ctx.regularSession = sessions.regularSpec;
  ...
}
```

`SymbolSessions` already has the data — `src/data/session/symbol-sessions.ts` (~line 54):

```ts
const spec = (session: string, corrections: string) => new SessionSpec(tz, session, info.holidays, corrections);
this.regular = spec(info.session, info.corrections);   // ← corrections + holidays used, but only in the app's own spec
```

The library is fine: oakscriptjs `TradingCalendar.periods(date)` applies `corrections` before the weekday schedule and works for any date (including a Sunday). oakscriptjs chart contexts accept this already — it just never reaches them:

```ts
session?: SessionSpec | string;   // SessionSpec object carries { session, corrections, holidays, timezone }
```

## Suggested fix

Expose corrections/holidays from `SymbolSessions` and pass a spec object into the script contexts:

```ts
// symbol-sessions.ts
get specInfo() {
  return { session: this.regularSpec, corrections: this.info.corrections, holidays: this.info.holidays, timeZone: this.timeZone };
}

// script-chart.ts
ctx.session = extended
  ? { session: sessions.extendedSpec, corrections: sessions.specInfo.corrections, holidays: sessions.specInfo.holidays }
  : sessions.specInfo;
ctx.regularSession = sessions.specInfo;
```

(Exact shape to taste — the point is forwarding `corrections` + `holidays` so oakscriptjs's `TradingCalendar` gets a `SessionSpec` object instead of a string.)

## Expected vs actual

- **Expected (Pine behavior):** the special session's bars belong to their own trading day; `time("D")` returns that day's session start, `time_tradingday` returns 00:00 UTC of it; daily resampling batches the session.
- **Actual:** the special session's bars carry the previous regular session's day values, so day-boundary logic, daily aggregation and `request.security` on `"D"` and above treat the special session as part of the previous day; standard daily pivots computed in a script show Friday+Sunday-merged values on the next session.

## Notes

- Regular weekday sessions are unaffected (`time("D")` works there today).
- Affects any script that uses day/period boundaries: pivots, session/week aggregations, `timeframe.change`, `request.security` on `"D"` and above.
- The failure is silent (no NaN): downstream scripts keep running with merged values, which is harder to notice than a hard NaN.
- oakscriptjs's `TradingCalendar.periods(date)` already applies `corrections` before the weekday schedule, so passing the data fixes all of `time("D")`, `time_tradingday` and the daily resample at once — no library change needed.
