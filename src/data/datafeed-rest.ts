/*
 * Datafeed REST wrappers — typed shims over the active `DataSource` (see
 * `sources/`): history/scroll-back pagers plus ticker info/snapshot. Which
 * source serves a call is the selector's concern. Hand-written (parallel to
 * bindings.ts) so the chart can wire new commands before bindings.ts
 * regenerates on the next debug-build run.
 */
import type { Candle } from "../bindings";
import { source } from "./sources";

/** In-flight coalescing: concurrent fetches for the same symbol share one
 *  request. A symbol switch fires info/snapshot from the chart legend, the
 *  detail panel and watchlist names at once — without this that's 3 identical
 *  calls. Only the pending promise is shared (never results), so no stale
 *  data is possible. */
const inflight = new Map<string, Promise<unknown>>();
function coalesce<T>(key: string, run: () => Promise<T>): Promise<T> {
  const hit = inflight.get(key) as Promise<T> | undefined;
  if (hit) return hit;
  const p = run().finally(() => {
    if (inflight.get(key) === p) inflight.delete(key);
  });
  inflight.set(key, p);
  return p;
}

/** Fetch `mult`-second OHLC bars for `symbol` over the trailing `days`.
 *  Throws on backend error (the caller wraps in try/catch). */
export async function getSecondHistory(
  symbol: string,
  mult: number,
  days: number,
  adjusted = true,
): Promise<Candle[]> {
  return source().secondAggs(symbol, mult, days, adjusted);
}

/** Live-tail refresh for the seconds frames: `mult`-second bars strictly newer
 *  than `sinceSec`. The 30s refresh loop merges these instead of refetching the
 *  whole 1-3 day window. Throws on backend error. */
export async function getSecondHistoryTail(
  symbol: string,
  mult: number,
  sinceSec: number,
  adjusted = true,
): Promise<Candle[]> {
  return source().secondTail(symbol, mult, sinceSec, adjusted);
}

/** Scroll-back pager for the REST frames (seconds + minutes): one older window
 *  of `mult`-`timespan` bars strictly before `beforeSec` (the time, in seconds,
 *  of the chart's oldest loaded bar). `spanDays` sizes the window. Returns `[]`
 *  when no older bars exist (history exhausted). Throws on backend error. */
export async function getAggregatesBefore(
  symbol: string,
  timespan: "second" | "minute",
  mult: number,
  beforeSec: number,
  spanDays: number,
  adjusted = true,
): Promise<Candle[]> {
  return source().aggregatesBefore(symbol, timespan, mult, beforeSec, spanDays, adjusted);
}

/** Scroll-back pager for the daily family (1D/1W/1M): older daily candles
 *  strictly before `beforeSec`, from the same S3 source as the initial daily
 *  load. The caller keeps the underlying daily bars and re-aggregates 1W/1M.
 *  Returns `[]` when exhausted. Throws on backend error. */
export async function getDailyHistoryBefore(
  symbol: string,
  beforeSec: number,
  spanDays: number,
  adjusted = true,
): Promise<Candle[]> {
  return source().dailyBefore(symbol, beforeSec, spanDays, adjusted);
}

// ── WatchlistDetail data (Feature 6a) ────────────────────────────────────

export type TickerInfo = {
  ticker: string;
  name: string | null;
  exchange: string | null;
  industry: string | null;
  sector: string | null;
  currency: string | null;
  description: string | null;
  homepageUrl: string | null;
  totalEmployees: number | null;
  marketCap: number | null;
  figi: string | null;
  /** Branding icon via the `ticker-icon` proxy (Rust adds the token) — usable as <img src>. */
  iconUrl: string | null;
};

export type Snapshot = {
  ticker: string;
  last: number;
  change: number;
  changePercent: number;
  dayHigh: number;
  dayLow: number;
  dayVolume: number;
  /** Pre/post-market move vs the regular close; null mid-session. */
  extChangePercent: number | null;
  source: "live" | "prev";
  updatedNs: number;
};

export async function getTickerInfo(symbol: string): Promise<TickerInfo> {
  return coalesce(`info:${symbol.toUpperCase()}`, () => source().tickerInfo(symbol));
}

export async function getTickerSnapshot(symbol: string): Promise<Snapshot> {
  return coalesce(`snap:${symbol.toUpperCase()}`, () => source().tickerSnapshot(symbol));
}

/** One headline for the chart's "Latest news" lollipop (newest first). */
export type NewsItem = {
  id: string;
  title: string;
  publisher: string;
  /** Publish time, UNIX ms. */
  published: number;
  url: string | null;
  description: string | null;
};

/** Newest `limit` headlines tagged with `symbol`; `[]` on backend failure. */
export async function getLatestNews(symbol: string, limit = 1): Promise<NewsItem[]> {
  return source().latestNews(symbol, limit);
}
