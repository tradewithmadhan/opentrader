/*
 * OpenAlgo source — live broker data in the browser behind the `DataSource`
 * socket. Talks to an OpenAlgo server (REST + WebSocket) directly from the
 * page; no Tauri backend involved.
 *
 * Configuration lives in OPENALGO_* consts below (local-only values — the
 *  browser cannot read process env, so neither the bat nor vite config can
 *  supply them at runtime). Traffic goes direct to the host, which must allow
 *  the page origin (CORS).
 *
 * Coverage notes vs the sample source: seconds are NOT served (broker
 * `intervals.seconds` is empty — caps say so, the picker greys out);
 * `adjusted_toggle` is false (`/history` has no adjust flag);
 * `SecondAggregate` is synthesized per quote tick (documented approximation);
 * 240m aggregates client-side from 2h; 1W/1M aggregate client-side from daily.
 */
import type { Candle, SymbolSearchResult } from "../../bindings";
import type { Snapshot, TickerInfo } from "../datafeed-rest";import type { ChartAggregate, SecondAggregate, TradeTick } from "../datafeed-live";
import type { DataSource, MarketSessionDef } from "./types";
// ── Config ───────────────────────────────────────────────────────────────────
// LOCAL-ONLY credentials, hardcoded here (never committed without review).
// Rationale: the browser cannot read process env, so neither the bat nor
// vite config can supply these at runtime — they live in this file instead.
// Traffic goes direct to the host below, which must allow the page origin
// (CORS); without that the browser blocks every request regardless of code.

const OPENALGO_BASE = "https://flattrade.captainvizhuthugal.dpdns.org";
const OPENALGO_WS = "wss://flattrade.captainvizhuthugal.dpdns.org/ws";
const OPENALGO_KEY = "fe377b1281f4bd931a949a7f04964bf4f34ff08ab0433652c856ac813591254b";

export type OpenAlgoConfig = { base: string; ws: string; key: string };

export function openAlgoConfig(): OpenAlgoConfig {
  return { base: OPENALGO_BASE, ws: OPENALGO_WS, key: OPENALGO_KEY };
}

function requireKey(cfg: OpenAlgoConfig): string {
  if (!cfg.key) {
    throw new Error("OpenAlgo API key missing — set OPENALGO_KEY in sources/openalgo.ts.");
  }
  return cfg.key;
}

async function post<T>(cfg: OpenAlgoConfig, path: string, body: Record<string, unknown>): Promise<T> {
  // Direct to the configured host (see header comment re CORS).
  const payload = { apikey: requireKey(cfg), ...body };
  let res: Response;
  try {
    res = await fetch(`${cfg.base}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    throw new Error(`OpenAlgo ${path}: network unreachable (${e instanceof Error ? e.message : e})`);
  }
  if (!res.ok) throw new Error(`OpenAlgo ${path}: HTTP ${res.status}`);
  const json = (await res.json()) as { status?: string; message?: string };
  if (json.status === "error") throw new Error(`OpenAlgo ${path}: ${json.message ?? "request failed"}`);
  return json as T;
}

// ── Symbol + interval mapping (exported for tests) ───────────────────────────

export function splitOpenAlgo(symbol: string): { exchange: string; ticker: string } {
  const head = symbol.split(",")[0].trim().toUpperCase();
  if (head.includes(":")) {
    const [exchange, ...rest] = head.split(":");
    return { exchange, ticker: rest.join(":") };
  }
  return { exchange: "NSE", ticker: head };
}

/** App minute-multiplier → OpenAlgo interval. Seconds map 1:1 (broker serves
 *  none today — caps advertise `seconds: []`, so datafeed never calls). */
export const MINUTE_INTERVAL: Record<number, string> = {
  1: "1m",
  3: "3m",
  5: "5m",
  10: "10m",
  15: "15m",
  20: "20m",
  30: "30m",
  60: "1h",
  120: "2h",
};

export function resolveInterval(kind: "second" | "minute", mult: number): string {
  if (kind === "second") return `${mult}s`;
  const hit = MINUTE_INTERVAL[mult];
  if (!hit) throw new Error(`OpenAlgo: minute multiplier ${mult} has no interval mapping`);
  return hit;
}

export type OaBar = {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  oi?: number;
};

export function toCandle(b: OaBar): Candle {
  return {
    time: b.timestamp,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
    volume: b.volume ?? 0,
  };
}

const fmtDate = (d: Date): string =>
  `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;

/** Split a [start, end] UTC-millis range into ≤ maxDays-day `YYYY-MM-DD`
 *  windows (intraday history is capped at 30 days per request). Day-aligned
 *  so an exact multiple never spills a 1-day tail chunk. */
export function chunkRanges(startMs: number, endMs: number, maxDays: number): [string, string][] {
  const out: [string, string][] = [];
  const day = 86400000;
  let first = Math.floor(startMs / day);
  const last = Math.floor(endMs / day);
  while (first <= last) {
    const windowEnd = Math.min(first + maxDays - 1, last);
    out.push([fmtDate(new Date(first * day)), fmtDate(new Date(windowEnd * day))]);
    first = windowEnd + 1;
  }
  return out;
}

type HistoryData = { status?: string; data?: OaBar[] };

async function fetchHistory(
  symbol: string,
  exchange: string,
  oaInterval: string,
  startMs: number,
  endMs: number,
): Promise<Candle[]> {
  // Per-request range limits: intraday intervals cap at 30 days (chunked);
  // daily and longer allow up to 1 year in one call.
  const maxDays = oaInterval === "D" || oaInterval === "W" || oaInterval === "M" ? 365 : 30;
  const cfg = openAlgoConfig();
  const out: Candle[] = [];
  for (const [from, to] of chunkRanges(startMs, endMs, maxDays)) {
    const res = await post<HistoryData>(cfg, "/api/v1/history", {
      symbol,
      exchange,
      interval: oaInterval,
      start_date: from,
      end_date: to,
    });
    for (const b of res.data ?? []) out.push(toCandle(b));
  }
  out.sort((a, b) => a.time - b.time);
  return out;
}

/** Aggregate 2h bars into 4h bars (broker has no 4h interval). */
export function aggregate2h(bars: Candle[]): Candle[] {
  const out: Candle[] = [];
  let cur: Candle | null = null;
  for (const b of bars) {
    const bucket = Math.floor(b.time / 14400) * 14400;
    if (!cur || cur.time !== bucket) {
      if (cur) out.push(cur);
      cur = { ...b, time: bucket };
    } else {
      cur.high = Math.max(cur.high, b.high);
      cur.low = Math.min(cur.low, b.low);
      cur.close = b.close;
      cur.volume += b.volume;
    }
  }
  if (cur) out.push(cur);
  return out;
}

// ── Reference mapping (exported for tests) ───────────────────────────────────

export type OaQuote = {
  open: number;
  high: number;
  low: number;
  ltp: number;
  ask?: number;
  bid?: number;
  /** Absent on some WS ticks — see baselineFor. */
  prev_close?: number;
  volume: number;
  oi?: number;
};

export function shapeSnapshot(ticker: string, q: OaQuote): Snapshot {
  // Cache genuine prev closes only (never open-fallbacks, to avoid locking
  // in the wrong baseline for the rest of the session).
  const pc: number | undefined = q.prev_close;
  if (pc != null && Number.isFinite(pc) && pc > 0) rememberPrevClose(ticker, pc);
  const prev = baselineFor(ticker, q.prev_close, q.open);
  const change =
    Number.isFinite(q.ltp) && prev != null && Number.isFinite(prev) ? q.ltp - prev : 0;
  return {
    ticker,
    last: q.ltp,
    change,
    changePercent: prev != null && prev > 0 ? (change / prev) * 100 : 0,
    dayHigh: q.high,
    dayLow: q.low,
    dayVolume: q.volume,
    extChangePercent: null,
    source: "live",
    updatedNs: Date.now() * 1e6,
  };
}

/** Last known good previous-close per symbol (WS ticks often omit it).
 *  Baseline priority: payload prev_close → remembered → day open → none
 *  (change 0, never NaN). */
const prevCloseCache = new Map<string, number>();

export function rememberPrevClose(symbol: string, prev: number): void {
  if (Number.isFinite(prev) && prev > 0) prevCloseCache.set(symbol.toUpperCase(), prev);
}

export function baselineFor(symbol: string, prevClose: number | undefined, open: number): number | null {
  if (Number.isFinite(prevClose) && (prevClose as number) > 0) return prevClose as number;
  const cached = prevCloseCache.get(symbol.toUpperCase());
  if (cached != null) return cached;
  if (Number.isFinite(open) && open > 0) return open;
  return null;
}

const INDIAN_EXCHANGES = new Set(["NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX", "NSE_INDEX", "BSE_INDEX"]);

export type OaSymbolInfo = {
  name?: string;
  symbol?: string;
  brsymbol?: string;
  exchange?: string;
  instrumenttype?: string;
  expiry?: string;
  strike?: number;
  lotsize?: number;
  tick_size?: number;
};

export function shapeTickerInfo(ticker: string, exchange: string, d: OaSymbolInfo): TickerInfo {
  const lotsize = d.lotsize ?? 1;
  return {
    ticker,
    name: d.name || ticker,
    exchange,
    industry: null,
    sector: null,
    currency: INDIAN_EXCHANGES.has(exchange) ? "INR" : null,
    description:
      `${d.brsymbol || ticker}` +
      (d.expiry ? ` · exp ${d.expiry}` : "") +
      (lotsize !== 1 ? ` · lot ${lotsize}` : ""),
    homepageUrl: null,
    totalEmployees: null,
    marketCap: null,
    figi: null,
    iconUrl: null,
  };
}

/** Dialog type-filter code → accepted OpenAlgo instrument types. The API
 *  reports indices as "INDEX" (verified live); the dialog code stays "IX". */
const TYPE_MAP: Record<string, Set<string>> = {
  EQ: new Set(["EQ", ""]),
  FUT: new Set(["FUT"]),
  CE: new Set(["CE"]),
  PE: new Set(["PE"]),
  OPT: new Set(["CE", "PE"]),
  IX: new Set(["IX", "INDEX"]),
};

export type OaSearchRow = {
  symbol: string;
  name?: string;
  exchange?: string;
  instrumenttype?: string;
  expiry?: string;
  strike?: number;
  lotsize?: number;
  market?: string;
};

export function mapSearchRows(rows: OaSearchRow[], type: string | null): SymbolSearchResult[] {
  const allow = type ? TYPE_MAP[type] : null;
  return rows
    .filter((r) => !allow || allow.has(r.instrumenttype ?? ""))
    .map((r) => ({
      ticker: r.symbol,
      name: r.name ?? "",
      market: "stocks",
      locale: null,
      primaryExchange: r.exchange ?? "",
      type: r.instrumenttype ?? "",
    }));
}

// ── Live WS engine ───────────────────────────────────────────────────────────
// Quote ticks double as the tick shape (same fields).

const chartSubs = new Map<string, string>();
const watchSubs = new Map<string, Set<string>>();
const chartListeners = new Set<(t: ChartAggregate) => void>();
const secondListeners = new Set<(t: SecondAggregate) => void>();
const tickListeners = new Set<(t: TradeTick) => void>();
const minuteBuckets = new Map<string, { start: number; o: number; h: number; l: number; v: number }>();

let ws: WebSocket | null = null;
let wsWanted = false;
let backoffMs = 1000;
let resyncTimer: ReturnType<typeof setTimeout> | null = null;

function allSymbols(): Set<string> {
  // Bare tickers: the broker echoes symbols bare, so matching runs bare even
  // though subscriptions carry full "EXCHANGE:TICKER" names (see fullSymbols).
  const out = new Set<string>();
  const add = (s: string) => out.add(splitOpenAlgo(s).ticker);
  for (const s of chartSubs.values()) add(s);
  for (const set of watchSubs.values()) for (const s of set) add(s);
  return out;
}

/** Full as-given names (deduped) for subscribe frames, where the venue half
 *  is required. */
function fullSymbols(): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const add = (s: string) => {
    const u = s.toUpperCase();
    if (!seen.has(u)) {
      seen.add(u);
      out.push(s);
    }
  };
  for (const s of chartSubs.values()) add(s);
  for (const set of watchSubs.values()) for (const s of set) add(s);
  return out;
}

function scheduleResync(): void {
  if (resyncTimer !== null) return;
  resyncTimer = setTimeout(() => {
    resyncTimer = null;
    sendSubscribe();
  }, 300);
}

function sendSubscribe(): void {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const syms = fullSymbols().map((s) => {
    const { exchange, ticker } = splitOpenAlgo(s);
    return { exchange, symbol: ticker };
  });
  ws.send(JSON.stringify({ action: "unsubscribe_all" }));
  if (syms.length > 0) {
    ws.send(JSON.stringify({ action: "subscribe", mode: "Quote", symbols: syms }));
  }
}

function ensureWs(): void {
  wsWanted = true;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    scheduleResync();
    return;
  }
  let cfg: OpenAlgoConfig;
  try {
    cfg = openAlgoConfig();
    requireKey(cfg);
  } catch {
    return; // no key yet — listeners stay silent until configured
  }
  try {
    ws = new WebSocket(cfg.ws);
  } catch {
    return;
  }
  ws.onopen = () => {
    backoffMs = 1000;
    ws?.send(JSON.stringify({ action: "authenticate", api_key: cfg.key }));
    sendSubscribe();
  };
  ws.onmessage = (ev) => {
    let msg: any = null;
    try {
      msg = JSON.parse(String(ev.data));
    } catch {
      return;
    }
    if (msg?.type !== "market_data" || !msg?.data) return;
    onQuoteTick(String(msg.symbol ?? ""), String(msg.exchange ?? ""), msg.data as OaQuote);
  };
  ws.onclose = () => {
    ws = null;
    if (!wsWanted) return;
    const wait = backoffMs;
    backoffMs = Math.min(backoffMs * 2, 30000);
    setTimeout(() => {
      if (wsWanted) ensureWs();
    }, wait);
  };
  ws.onerror = () => {
    try {
      ws?.close();
    } catch {
      /* onclose handles backoff */
    }
  };
}

function onQuoteTick(symbol: string, _exchange: string, q: OaQuote): void {
  // Bare matching: the broker echoes bare tickers while our subscriptions
  // carry full "EXCHANGE:TICKER" names (see allSymbols/fullSymbols).
  const sym = splitOpenAlgo(symbol).ticker;
  if (!sym || !allSymbols().has(sym)) return;
  const now = Math.floor(Date.now() / 1000);
  const pc: number | undefined = q.prev_close;
  if (pc != null && Number.isFinite(pc) && pc > 0) rememberPrevClose(sym, pc);
  const prev = baselineFor(sym, q.prev_close, q.open);
  const change =
    Number.isFinite(q.ltp) && prev != null && Number.isFinite(prev) ? q.ltp - prev : 0;
  const tick: TradeTick = {
    symbol: sym,
    price: q.ltp,
    change,
    changePercent: prev != null && prev > 0 ? (change / prev) * 100 : 0,
    extChangePercent: null,
    volume: q.volume,
    source: "live",
  };
  for (const fn of tickListeners) fn(tick);
  // Minute bucket for ChartAggregate (quotes carry day OHLC, not minute bars).
  const start = Math.floor(now / 60) * 60;
  let b = minuteBuckets.get(sym);
  if (!b || b.start !== start) {
    b = { start, o: q.ltp, h: q.ltp, l: q.ltp, v: 0 };
    minuteBuckets.set(sym, b);
  }
  b.h = Math.max(b.h, q.ltp);
  b.l = Math.min(b.l, q.ltp);
  const agg: ChartAggregate = {
    symbol: sym,
    time: start,
    open: b.o,
    high: b.h,
    low: b.l,
    close: q.ltp,
    volume: b.v,
    day: { time: now, open: q.open, high: q.high, low: q.low, close: q.ltp, volume: q.volume },
  };
  for (const fn of chartListeners) fn(agg);
  // No 1s channel on this broker: one synthetic second bar per quote tick.
  // Volume unknown per tick (day total) — 0 adds nothing downstream.
  const sec: SecondAggregate = {
    symbol: sym,
    time: now,
    open: q.ltp,
    high: q.ltp,
    low: q.ltp,
    close: q.ltp,
    volume: 0,
  };
  for (const fn of secondListeners) fn(sec);
}

// ── DataSource ───────────────────────────────────────────────────────────────

/** Session served by the OpenAlgo feed (NSE equities). */
const SESSION: MarketSessionDef = {
  tz: "Asia/Kolkata",
  openMin: 9 * 60 + 15,
  closeMin: 15 * 60 + 30,
  preMin: 15,
  postMin: 30,
};

const OPENALGO_SEEDS = {
  defaultSymbol: "RELIANCE",
  starterTabs: [
    { symbol: "RELIANCE", interval: "1D" },
    { symbol: "INFY", interval: "60" },
    { symbol: "SBIN", interval: "240" },
  ],
  watchlistGroups: [
    // Index venues verified live (NSE:NIFTY does NOT exist on the broker).
    // Derivative contracts below are near-month as verified; they roll
    // monthly — refresh the symbols when they expire.
    { name: "INDEX", tickers: ["NSE_INDEX:NIFTY", "NSE_INDEX:BANKNIFTY", "BSE_INDEX:SENSEX"] },
    {
      name: "NSE",
      tickers: ["NSE:RELIANCE", "NSE:TCS", "NSE:INFY", "NSE:HDFCBANK", "NSE:SBIN"],
    },
    {
      name: "BSE",
      tickers: ["BSE:RELIANCE", "BSE:INFY", "BSE:TATASTEEL"],
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

function todayRange(days: number): [number, number] {
  const end = Date.now();
  return [end - days * 86400000, end];
}

export const openalgoSource: DataSource = {
  name: "openalgo",

  dailyAggs: async (symbol, days, _adjusted) => {
    const { exchange, ticker } = splitOpenAlgo(symbol);
    const [from, to] = todayRange(Math.min(days, 3650));
    return fetchHistory(ticker, exchange, "D", from, to);
  },

  minuteAggs: async (symbol, days, intervalMin, _adjusted) => {
    const { exchange, ticker } = splitOpenAlgo(symbol);
    if (intervalMin === 240) {
      // No 4h interval: aggregate client-side from 2h.
      const [from, to] = todayRange(days);
      return aggregate2h(await fetchHistory(ticker, exchange, "2h", from, to));
    }
    const oa = resolveInterval("minute", intervalMin);
    const [from, to] = todayRange(days);
    return fetchHistory(ticker, exchange, oa, from, to);
  },

  secondAggs: async () => {
    throw new Error("OpenAlgo broker serves no second bars (intervals.seconds is empty)");
  },

  secondTail: async () => {
    throw new Error("OpenAlgo broker serves no second bars (intervals.seconds is empty)");
  },

  aggregatesBefore: async (symbol, timespan, mult, beforeSec, spanDays, _adjusted) => {
    if (timespan === "second") {
      throw new Error("OpenAlgo broker serves no second bars (intervals.seconds is empty)");
    }
    const { exchange, ticker } = splitOpenAlgo(symbol);
    const endMs = beforeSec * 1000 - 86400000;
    const startMs = endMs - spanDays * 86400000;
    // Reuse the chunked fetcher over explicit calendar bounds.
    const oa = mult === 240 ? "2h" : resolveInterval("minute", mult);
    const out: Candle[] = [];
    for (const [from, to] of chunkRanges(startMs, endMs, 30)) {
      const res = await post<HistoryData>(openAlgoConfig(), "/api/v1/history", {
        symbol: ticker,
        exchange,
        interval: oa,
        start_date: from,
        end_date: to,
      });
      for (const b of res.data ?? []) out.push(toCandle(b));
    }
    out.sort((a, b) => a.time - b.time);
    const rows = out.filter((b) => b.time < beforeSec);
    return mult === 240 ? aggregate2h(rows) : rows;
  },

  dailyBefore: async (symbol, beforeSec, spanDays, _adjusted) => {
    const { exchange, ticker } = splitOpenAlgo(symbol);
    const endMs = beforeSec * 1000 - 86400000;
    const startMs = endMs - spanDays * 86400000;
    const out = await fetchHistory(ticker, exchange, "D", startMs, endMs);
    return out.filter((b) => b.time < beforeSec);
  },

  tickerInfo: async (symbol) => {
    const { exchange, ticker } = splitOpenAlgo(symbol);
    const cfg = openAlgoConfig();
    const res = await post<{ status?: string; data?: OaSymbolInfo }>(cfg, "/api/v1/symbol", {
      symbol: ticker,
      exchange,
    });
    if (!res.data) throw new Error(`OpenAlgo /symbol: no data for ${ticker}`);
    return shapeTickerInfo(ticker, exchange, res.data);
  },

  tickerSnapshot: async (symbol) => {
    const { exchange, ticker } = splitOpenAlgo(symbol);
    const cfg = openAlgoConfig();
    const res = await post<{ status?: string; data?: OaQuote }>(cfg, "/api/v1/quotes", {
      symbol: ticker,
      exchange,
    });
    if (!res.data) throw new Error(`OpenAlgo /quotes: no data for ${ticker}`);
    return shapeSnapshot(ticker, res.data);
  },

  search: async (query, type) => {
    const q = query.trim();
    if (!q) return [];
    const cfg = openAlgoConfig();
    const res = await post<{ status?: string; data?: OaSearchRow[] }>(cfg, "/api/v1/search", { query: q });
    return mapSearchRows(res.data ?? [], type);
  },

  dividends: async () => [],
  splits: async () => [],
  latestNews: async () => [],

  setChartSubscription: async (symbol, pane = "0") => {
    if (symbol) chartSubs.set(pane, symbol);
    else chartSubs.delete(pane);
    ensureWs();
  },

  setWatchlistSubscription: async (symbols) => {
    watchSubs.set("browser", new Set(symbols));
    ensureWs();
  },

  onChartAggregate: (fn) => {
    chartListeners.add(fn);
    ensureWs();
    return Promise.resolve(() => {
      chartListeners.delete(fn);
    });
  },

  onSecondAggregate: (fn) => {
    secondListeners.add(fn);
    ensureWs();
    return Promise.resolve(() => {
      secondListeners.delete(fn);
    });
  },

  onTradeTick: (fn) => {
    tickListeners.add(fn);
    ensureWs();
    return Promise.resolve(() => {
      tickListeners.delete(fn);
    });
  },

  capabilities: async () => ({
    name: "openalgo",
    resolutions: {
      seconds: [] as number[],
      minutes: [1, 3, 5, 10, 15, 30, 60, 120, 240],
      daily: true,
      weeklyMonthlyFromDaily: true,
    },
    maxBarsPerRequest: 50000,
    adjustedToggle: false,
    extendedHours: false,
    session: {
      timezone: "Asia/Kolkata",
      openMin: 555,
      closeMin: 930,
      preMin: 15,
      postMin: 30,
    },
    reference: {
      search: true,
      searchTypeFilter: true,
      snapshot: true,
      dividends: false,
      splits: false,
      news: false,
      icons: false,
    },
    entitlements: null,
  }),

  watchCapabilities: () => Promise.resolve(() => {}),
  session: () => SESSION,
  seeds: () => OPENALGO_SEEDS,
  historyLimits: () => ({ second: 0, minute: 30, day: 365 }),
};
