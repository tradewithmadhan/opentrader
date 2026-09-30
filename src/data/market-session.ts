/*
 * Market session state from the wall clock, in the ACTIVE PROVIDER's session
 * terms (timezone + regular/pre/post bands from `activeSession()` in
 * `./datafeed`, which reads the provider caps). Previously US-equity only
 * (`usMarketSession` below, kept for reference/compat).
 *
 * One source for the watchlist status dot + Ext column, the Details market
 * state and the chart's pre/post-market price label. Holidays are not
 * modelled (weekends only).
 */

import { activeSession } from "./datafeed";

export type MarketSession = "open" | "pre" | "post" | "closed";

const fmtCache = new Map<string, Intl.DateTimeFormat>();

function partsFmt(tz: string): Intl.DateTimeFormat {
  let f = fmtCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    fmtCache.set(tz, f);
  }
  return f;
}

/** Session state at `now` under the active provider's session definition:
 *  pre = [open-pre, open), open = [open, close), post = [close, close+post),
 *  else closed (overnight / weekend). */
export function providerMarketSession(now: Date = new Date()): MarketSession {
  const sess = activeSession();
  const parts = partsFmt(sess.tz).formatToParts(now);
  const get = (t: string): string => parts.find((p) => p.type === t)?.value ?? "";
  const wd = get("weekday");
  if (wd === "Sat" || wd === "Sun") return "closed";
  const mins = Number(get("hour")) * 60 + Number(get("minute"));
  if (mins >= sess.openMin && mins < sess.closeMin) return "open";
  if (mins >= sess.openMin - sess.preMin && mins < sess.openMin) return "pre";
  if (mins >= sess.closeMin && mins < sess.closeMin + sess.postMin) return "post";
  return "closed";
}

/*
 * US-equity market session from the wall clock (America/New_York, Mon–Fri):
 * pre-market 04:00–09:30, regular 09:30–16:00, post-market 16:00–20:00, else
 * closed (overnight / weekend). Holidays are not modelled. Kept for
 * reference and for US-specific surfaces; prefer `providerMarketSession()`
 * for exchange-correct status.
 */

const fmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

export function usMarketSession(now: Date = new Date()): MarketSession {
  const parts = fmt.formatToParts(now);
  const get = (t: string): string => parts.find((p) => p.type === t)?.value ?? "";
  const wd = get("weekday");
  if (wd === "Sat" || wd === "Sun") return "closed";
  const mins = Number(get("hour")) * 60 + Number(get("minute"));
  if (mins >= 9 * 60 + 30 && mins < 16 * 60) return "open";
  if (mins >= 4 * 60 && mins < 9 * 60 + 30) return "pre";
  if (mins >= 16 * 60 && mins < 20 * 60) return "post";
  return "closed";
}
