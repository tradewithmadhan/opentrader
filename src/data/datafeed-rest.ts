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
  /** Symbol type as scripts read it (`syminfo.type`): "stock", "fund", "dr", "warrant"; null = unknown. */
  symbolType: string | null;
  /** Security type as served ("stock", "etf", "adr"…). */
  securityType: string | null;
  /** India uses ISIN (no FIGI): the company-profile code, if known. */
  isin?: string | null;
  /** Contract multiplier (lot size); null when the source reports none. */
  pointValue?: number | null;
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
  updatedMs: number;
};

/** Reference data changes at most once a day: an answer is reused for an hour. */
const INFO_TTL_MS = 60 * 60_000;

/** Ticker info requests, one per symbol: the answer serves every caller for
 *  INFO_TTL_MS. A failure is not kept (the next call routes anew). */
const infoCache = new Map<string, { at: number; p: Promise<TickerInfo> }>();

/** Reference data of `symbol`, routed to the active source. */
export function getTickerInfo(symbol: string): Promise<TickerInfo> {
  const key = symbol.toUpperCase();
  const hit = infoCache.get(key);
  if (hit && Date.now() - hit.at < INFO_TTL_MS) return hit.p;
  const entry = { at: Date.now(), p: source().tickerInfo(symbol) };
  infoCache.set(key, entry);
  entry.p.then(
    () => { entry.at = Date.now(); },
    () => { if (infoCache.get(key) === entry) infoCache.delete(key); },
  );
  return entry.p;
}

export async function getTickerSnapshot(symbol: string, _wanted?: () => boolean): Promise<Snapshot> {
  // `_wanted` (upstream's retry-loop cancel hook) is accepted for call-site
  // compatibility with upstream; the source socket's snapshot is a one-shot
  // coalesced request, so there is no retry loop to stop.
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

/** Charts of the same symbol refresh their headline on the same period: one
 *  request serves them all for NEWS_SHARE_MS. */
const NEWS_SHARE_MS = 60_000;
const newsCache = new Map<string, { at: number; p: Promise<NewsItem[]> }>();

/** Newest `limit` headlines tagged with `symbol`; `[]` on backend failure. */
/** Newest `limit` headlines tagged with `symbol`, routed to the active source
 *  (browsers without headlines keep re-asking: a failure is not kept). */
export function getLatestNews(symbol: string, limit = 1): Promise<NewsItem[]> {
  const key = `${symbol}|${limit}`;
  const hit = newsCache.get(key);
  if (hit && Date.now() - hit.at < NEWS_SHARE_MS) return hit.p;
  const entry = { at: Date.now(), p: source().latestNews(symbol, limit) };
  newsCache.set(key, entry);
  entry.p.then(
    () => { entry.at = Date.now(); },
    () => { if (newsCache.get(key) === entry) newsCache.delete(key); },
  );
  return entry.p;
}
