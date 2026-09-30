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
  sampleTickerInfo,
} from "../sample-feed";
import type { DataSource, MarketSessionDef, SourceSeeds } from "./types";

const IST_SESSION: MarketSessionDef = { tz: "Asia/Kolkata", openMin: 9 * 60 + 15, closeMin: 15 * 60 + 30 };

const SAMPLE_SEEDS: SourceSeeds = {
  defaultSymbol: "RELIANCE",
  starterTabs: [
    { symbol: "RELIANCE", interval: "1D" },
    { symbol: "INFY", interval: "60" },
    { symbol: "TATAMOTORS", interval: "240" },
  ],
  watchlistGroups: [
    { name: "INDEX", tickers: ["NSE:NIFTY", "NSE:BANKNIFTY", "BSE:SENSEX"] },
    {
      name: "LARGE CAPS",
      tickers: [
        "NSE:RELIANCE",
        "NSE:TCS",
        "NSE:INFY",
        "NSE:HDFCBANK",
        "NSE:ICICIBANK",
        "NSE:SBIN",
        "NSE:TATAMOTORS",
      ],
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
  session: () => IST_SESSION,
  seeds: () => SAMPLE_SEEDS,
};
