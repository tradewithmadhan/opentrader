/*
 * Tauri source — the real backend behind the `DataSource` socket.
 *
 * Every method below is the pre-refactor call body from `datafeed-rest.ts`,
 * `datafeed-live.ts` and `datafeed.ts`, moved here unchanged (including the
 * `{ status, data }` envelope unwrapping, which now lives here instead of at
 * each call site). No data logic was altered in the move.
 */
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { commands, events, type Candle, type ProviderCapabilities } from "../../bindings";
import type { ChartAggregate, SecondAggregate, TradeTick } from "../datafeed-live";
import type { NewsItem, Snapshot, TickerInfo } from "../datafeed-rest";
import type { DataSource, MarketSessionDef, SourceSeeds } from "./types";
import { bareSymbol } from "./types";

function unwrap<T>(res: { status: "ok"; data: T } | { status: "error"; error: string }): T {
  if (res.status === "error") throw new Error(res.error);
  return res.data;
}

const US_SESSION: MarketSessionDef = { tz: "America/New_York", openMin: 9 * 60 + 30, closeMin: 16 * 60 };

const US_SEEDS: SourceSeeds = {
  defaultSymbol: "INTC",
  starterTabs: [
    { symbol: "INTC", interval: "1D" },
    { symbol: "AAPL", interval: "60" },
    { symbol: "TSLA", interval: "240" },
  ],
  watchlistGroups: [
    { name: "INDEX", tickers: ["AMEX:SPY", "NASDAQ:QQQ", "AMEX:DIA", "AMEX:IWM"] },
    {
      name: "LARGE CAPS",
      tickers: [
        "NASDAQ:AAPL",
        "NASDAQ:MSFT",
        "NASDAQ:NVDA",
        "NASDAQ:AMZN",
        "NASDAQ:GOOGL",
        "NASDAQ:META",
        "NASDAQ:TSLA",
      ],
    },
  ],
};

export const tauriSource: DataSource = {
  name: "massive",

  // ── History ────────────────────────────────────────────────────
  async dailyAggs(symbol: string, days: number, adjusted: boolean): Promise<Candle[]> {
    return unwrap(await commands.getDailyHistory(bareSymbol(symbol), days, adjusted));
  },
  async minuteAggs(symbol: string, days: number, intervalMin: number, adjusted: boolean): Promise<Candle[]> {
    return unwrap(await commands.getMinuteHistory(bareSymbol(symbol), days, intervalMin, adjusted));
  },
  async secondAggs(symbol: string, mult: number, days: number, adjusted: boolean): Promise<Candle[]> {
    return invoke<Candle[]>("get_second_history", { symbol: bareSymbol(symbol), mult, days, adjusted });
  },
  async secondTail(symbol: string, mult: number, sinceSec: number, adjusted: boolean): Promise<Candle[]> {
    return invoke<Candle[]>("get_second_history_tail", { symbol: bareSymbol(symbol), mult, sinceSec, adjusted });
  },
  async aggregatesBefore(
    symbol: string,
    timespan: "second" | "minute",
    mult: number,
    beforeSec: number,
    spanDays: number,
    adjusted: boolean,
  ): Promise<Candle[]> {
    return invoke<Candle[]>("get_aggregates_before", {
      symbol: bareSymbol(symbol),
      timespan,
      mult,
      beforeSec,
      spanDays,
      adjusted,
    });
  },
  async dailyBefore(
    symbol: string,
    beforeSec: number,
    spanDays: number,
    adjusted: boolean,
  ): Promise<Candle[]> {
    return invoke<Candle[]>("get_daily_history_before", {
      symbol: bareSymbol(symbol),
      beforeSec,
      spanDays,
      adjusted,
    });
  },

  // ── Reference ──────────────────────────────────────────────────
  async tickerInfo(symbol: string): Promise<TickerInfo> {
    return invoke<TickerInfo>("get_ticker_info", { symbol: bareSymbol(symbol) });
  },
  async tickerSnapshot(symbol: string): Promise<Snapshot> {
    return invoke<Snapshot>("get_ticker_snapshot", { symbol: bareSymbol(symbol) });
  },
  async search(query: string, type: string | null) {
    return unwrap(await commands.searchTickers(query, type));
  },
  async dividends(symbol: string) {
    return unwrap(await commands.getDividends(bareSymbol(symbol)));
  },
  async splits(symbol: string) {
    return unwrap(await commands.getSplits(bareSymbol(symbol)));
  },
  async latestNews(symbol: string, limit: number): Promise<NewsItem[]> {
    return invoke<NewsItem[]>("get_latest_news", { symbol: bareSymbol(symbol), limit });
  },

  // ── Live ───────────────────────────────────────────────────────
  async setChartSubscription(symbol: string | null, pane = "0"): Promise<void> {
    await invoke("set_chart_subscription", { pane, symbol: symbol == null ? null : bareSymbol(symbol) });
  },
  async setWatchlistSubscription(symbols: string[]): Promise<void> {
    await invoke("set_watchlist_subscription", { symbols });
  },
  onChartAggregate(fn: (ev: ChartAggregate) => void): Promise<UnlistenFn> {
    return listen<ChartAggregate>("chart-aggregate", (e) => fn(e.payload));
  },
  onSecondAggregate(fn: (ev: SecondAggregate) => void): Promise<UnlistenFn> {
    return listen<SecondAggregate>("second-aggregate", (e) => fn(e.payload));
  },
  onTradeTick(fn: (ev: TradeTick) => void): Promise<UnlistenFn> {
    return listen<TradeTick>("trade-tick", (e) => fn(e.payload));
  },

  // ── Meta ───────────────────────────────────────────────────────
  async capabilities(): Promise<ProviderCapabilities | null> {
    try {
      return await commands.getProviderCapabilities();
    } catch {
      return null;
    }
  },
  watchCapabilities(onUpdate: (c: ProviderCapabilities) => void): Promise<UnlistenFn> {
    // Listen first so a change emitted during the initial fetch is not lost;
    // an event is always newer than the fetch reply, so it wins.
    return events.providerCapabilities.listen((e: { payload: ProviderCapabilities }) => onUpdate(e.payload));
  },
  session(): MarketSessionDef {
    return US_SESSION;
  },
  seeds(): SourceSeeds {
    return US_SEEDS;
  },
};
