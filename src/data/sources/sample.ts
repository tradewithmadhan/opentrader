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

/** Static funding mirror (gateway blocks browser CORS): the app-level
 *  funding status, shared as the fallback for any source without its own.
 *  Shape matches the backend's FundingStatus. */
export const SAMPLE_FUNDING = {
  month: "2026-10",
  currency: "USD",
  total: 440,
  raised: 0,
  remaining: 440,
  links: {
    github: "https://github.com/sponsors/deepentropy",
    bmc: "https://buymeacoffee.com/opentrader",
  },
};

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
    {
      name: "US",
      tickers: ["NASDAQ:AAPL", "NASDAQ:MSFT", "NASDAQ:NVDA", "NYSE:JPM", "NYSE:SPX"],
    },
    {
      name: "EU",
      tickers: ["LSE:SHEL", "XETRA:SAP", "LSE:FTSE"],
    },
    {
      name: "APAC",
      tickers: ["TSE:7203", "HKEX:0700", "ASX:CBA", "TSE:NIKKEI"],
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
  // Funding status for Settings > About. The public gateway endpoint
  // rejects browser origins (CORS), so the sample serves a static mirror
  // of the response instead of fetching it. `null` shape matches a gateway
  // with no cost set (404) — here costs are always set.
  fundingStatus: async () => SAMPLE_FUNDING,
};
