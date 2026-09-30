/*
 * DataSource — the single socket every market-data source plugs into.
 *
 * Method names mirror the backend `DataProvider` trait on purpose
 * (`src-tauri/src/data/provider/mod.rs`; see `docs/new-provider-requirements.md`):
 * a new source — Tauri backend, sample feed, or a future direct-HTTP feed —
 * implements this interface in ONE file under `sources/` and registers it in
 * `./index.ts`. No other file is touched (that is the whole test).
 *
 * Conventions (same as the trait):
 *  - history returns time-ascending `Candle[]`; chart families throw on
 *    unknown symbols, scroll-back pagers return `[]` when exhausted;
 *  - decorative paths (dividends/splits/news) return `[]` on failure;
 *  - live listeners filter by symbol themselves; subscription calls only
 *    declare desired state.
 */
import type { UnlistenFn } from "@tauri-apps/api/event";
import type {
  Candle,
  DividendEvent,
  ProviderCapabilities,
  SplitEvent,
  SymbolSearchResult,
} from "../../bindings";
import type { ChartAggregate, SecondAggregate, TradeTick } from "../datafeed-live";
import type { NewsItem, Snapshot, TickerInfo } from "../datafeed-rest";

/** Exchange session for regular-hours filtering (mirrors the trait's
 *  market-session contract; see `activeSession()` in `../datafeed`). */
export type MarketSessionDef = { tz: string; openMin: number; closeMin: number };

/** Seed content for a fresh profile (tabs + watchlist). */
export type SourceSeeds = {
  defaultSymbol: string;
  starterTabs: { symbol: string; interval: string }[];
  watchlistGroups: { name: string; tickers: string[] }[];
};

export interface DataSource {
  /** Stable id — matches the backend `DataProvider::name()` / frontend
   *  adapter key (`massive`, `sample`, …). */
  readonly name: string;

  // ── History (chart families throw on unknown symbols) ──────────────
  dailyAggs(symbol: string, days: number, adjusted: boolean): Promise<Candle[]>;
  minuteAggs(symbol: string, days: number, intervalMin: number, adjusted: boolean): Promise<Candle[]>;
  secondAggs(symbol: string, mult: number, days: number, adjusted: boolean): Promise<Candle[]>;
  secondTail(symbol: string, mult: number, sinceSec: number, adjusted: boolean): Promise<Candle[]>;
  aggregatesBefore(
    symbol: string,
    timespan: "second" | "minute",
    mult: number,
    beforeSec: number,
    spanDays: number,
    adjusted: boolean,
  ): Promise<Candle[]>;
  dailyBefore(symbol: string, beforeSec: number, spanDays: number, adjusted: boolean): Promise<Candle[]>;

  // ── Reference ──────────────────────────────────────────────────────
  tickerInfo(symbol: string): Promise<TickerInfo>;
  tickerSnapshot(symbol: string): Promise<Snapshot>;
  search(query: string, type: string | null): Promise<SymbolSearchResult[]>;
  dividends(symbol: string): Promise<DividendEvent[]>;
  splits(symbol: string): Promise<SplitEvent[]>;
  latestNews(symbol: string, limit: number): Promise<NewsItem[]>;

  // ── Live ───────────────────────────────────────────────────────────
  setChartSubscription(symbol: string | null, pane?: string): Promise<void>;
  setWatchlistSubscription(symbols: string[]): Promise<void>;
  onChartAggregate(fn: (t: ChartAggregate) => void): Promise<UnlistenFn>;
  onSecondAggregate(fn: (t: SecondAggregate) => void): Promise<UnlistenFn>;
  onTradeTick(fn: (t: TradeTick) => void): Promise<UnlistenFn>;

  // ── Meta ───────────────────────────────────────────────────────────
  /** Static caps + entitlements, or null while unknown (gates fall back). */
  capabilities(): Promise<ProviderCapabilities | null>;
  /** Push later capability changes (entitlement probe settling); noop when static. */
  watchCapabilities(onUpdate: (c: ProviderCapabilities) => void): Promise<UnlistenFn>;
  session(): MarketSessionDef;
  seeds(): SourceSeeds;
}
