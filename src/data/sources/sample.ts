/*
 * Sample source — the deterministic browser feed behind the `DataSource`
 * socket. Thin adapter only: every method delegates to `../sample-feed`
 * (generators, live engine, caps object); no data logic lives here.
 *
 * TEMPLATE for a real source (e.g. `real.ts`): copy this file, keep the
 * method names/signatures, replace each body with a network call. Nothing
 * else in the app changes.
 */
import type { ProviderCapabilities } from "../../bindings";
import type { Funding } from "../funding";
import {
  sampleAggregatesBefore,
  sampleCapabilities,
  sampleDailyHistory,
  sampleDailyHistoryBefore,
  sampleDividends,
  sampleLatestNews,
  sampleMinuteHistory,
  sampleOnChartAggregate,
  sampleOnSecondAggregate,
  sampleOnTradeTick,
  sampleSearch,
  sampleSecondHistory,
  sampleSecondHistoryTail,
  sampleSetChartSubscription,
  sampleSetWatchlistSubscription,
  sampleSnapshot,
  sampleSplits,
  sampleSymbolSession,
  sampleTickerInfo,
} from "../sample-feed";
import type { DataSource, MarketSessionDef, SourceSeeds } from "./types";

/** Session served by the sample feed (NSE equities). */
const SESSION: MarketSessionDef = {
  tz: "Asia/Kolkata",
  openMin: 9 * 60 + 15,
  closeMin: 15 * 60 + 30,
  preMin: 15,
  postMin: 30,
  mintick: 0.05,
};

/** Public gateway base (same URL the backend uses) for the funding status. */
const FUNDING_URL = "https://opentrader-gateway.cloudflare-breeder165.workers.dev";

const SAMPLE_SEEDS: SourceSeeds = {
  defaultSymbol: "NSE:RELIANCE",
  starterTabs: [
    { symbol: "NSE:RELIANCE", interval: "1D" },
    { symbol: "NSE:INFY", interval: "60" },
    { symbol: "NSE:TATAMOTORS", interval: "240" },
  ],
  // One row per venue so every exchange shows live data out of the box.
  // (Sample generates for any ticker; the openalgo source serves the same
  // rows from the broker where each was verified.)
  watchlistGroups: [
    { name: "INDEX", tickers: ["NSE_INDEX:NIFTY", "NSE_INDEX:BANKNIFTY", "BSE_INDEX:SENSEX"] },
    {
      name: "NSE",
      tickers: ["NSE:RELIANCE", "NSE:TCS", "NSE:INFY", "NSE:HDFCBANK", "NSE:SBIN"],
    },
    {
      name: "BSE",
      tickers: ["BSE:RELIANCE", "BSE:INFY", "BSE:TCS", "BSE:TATASTEEL"],
    },
    {
      name: "NFO",
      tickers: ["NFO:NIFTY27OCT26FUT", "NFO:BANKNIFTY27OCT26FUT"],
    },
    {
      name: "MCX",
      tickers: ["MCX:GOLD04DEC26FUT"],
    },
  ],
};

export const sampleSource: DataSource = {
  name: "sample",

  // ── History ────────────────────────────────────────────────────
  dailyAggs: (symbol, days, adjusted) => Promise.resolve(sampleDailyHistory(symbol, days, adjusted)),
  minuteAggs: (symbol, days, intervalMin, adjusted) =>
    Promise.resolve(sampleMinuteHistory(symbol, days, intervalMin, adjusted)),
  secondAggs: (symbol, mult, days, adjusted) =>
    Promise.resolve(sampleSecondHistory(symbol, mult, days, adjusted)),
  secondTail: (symbol, mult, sinceSec, adjusted) =>
    Promise.resolve(sampleSecondHistoryTail(symbol, mult, sinceSec, adjusted)),
  aggregatesBefore: (symbol, timespan, mult, beforeSec, spanDays, adjusted) =>
    Promise.resolve(sampleAggregatesBefore(symbol, timespan, mult, beforeSec, spanDays, adjusted)),
  dailyBefore: (symbol, beforeSec, spanDays, adjusted) =>
    Promise.resolve(sampleDailyHistoryBefore(symbol, beforeSec, spanDays, adjusted)),

  // ── Reference ──────────────────────────────────────────────────
  tickerInfo: (symbol) => Promise.resolve(sampleTickerInfo(symbol)),
  tickerSnapshot: (symbol) => Promise.resolve(sampleSnapshot(symbol)),
  // Per-venue sessions from the feed's static calendar (mirrors the backend's
  // symbol_session) — preferred over the backend command by the feed.
  symbolSessions: (symbol) => Promise.resolve(sampleSymbolSession(symbol)),
  search: (query, type) => Promise.resolve(sampleSearch(query, type)),
  dividends: (symbol) => Promise.resolve(sampleDividends(symbol)),
  splits: (symbol) => Promise.resolve(sampleSplits(symbol)),
  latestNews: (symbol, limit) => Promise.resolve(sampleLatestNews(symbol, limit)),

  // ── Live ───────────────────────────────────────────────────────
  setChartSubscription: (symbol, pane = "0") => {
    sampleSetChartSubscription(symbol, pane);
    return Promise.resolve();
  },
  setWatchlistSubscription: (symbols) => {
    sampleSetWatchlistSubscription(symbols);
    return Promise.resolve();
  },
  onChartAggregate: (fn) => Promise.resolve(sampleOnChartAggregate(fn)),
  onSecondAggregate: (fn) => Promise.resolve(sampleOnSecondAggregate(fn)),
  onTradeTick: (fn) => Promise.resolve(sampleOnTradeTick(fn)),

  // ── Meta ───────────────────────────────────────────────────────
  capabilities: () => Promise.resolve(sampleCapabilities() as ProviderCapabilities),
  watchCapabilities: () => Promise.resolve(() => {}),
  session: () => SESSION,
  seeds: () => SAMPLE_SEEDS,
  historyLimits: () => null,
  // Funding status direct from the public gateway endpoint (same URL the
  // backend uses) so Settings > About works with no backend. `null` when
  // the gateway has no cost set (404); anything else rejects and the block
  // stays hidden, like a failed backend command.
  fundingStatus: async () => {
    let res: Response;
    try {
      res = await fetch(`${FUNDING_URL}/funding/v1`);
    } catch (e) {
      throw new Error(`funding request: ${e instanceof Error ? e.message : e}`);
    }
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`funding ${res.status}`);
    const d = (await res.json()) as Partial<Funding> & { links?: Partial<Funding["links"]> };
    if (typeof d.month !== "string") throw new Error("funding body: bad month");
    return {
      month: d.month,
      currency: typeof d.currency === "string" ? d.currency : "",
      total: Number(d.total) || 0,
      raised: Number(d.raised) || 0,
      remaining: Number(d.remaining) || 0,
      links: {
        github: d.links?.github ?? "",
        bmc: d.links?.bmc ?? "",
      },
    };
  },
};
