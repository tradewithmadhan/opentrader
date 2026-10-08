/*
 * Sample data provider - everything sample in ONE file: the deterministic
 * engine (universe, generators, live ticks, calendar, caps, seeds), the
 * DataSource socket object, and the presentation adapter.
 *
 * To add a real vendor: copy this file, implement the engine + both
 * objects, and register both in sources/index.ts and providers/index.ts.
 * Nothing else in the app changes.
 */

import type {
  Candle,
  DividendEvent,
  SplitEvent,
  SymbolSearchResult,
  SymbolSession,
} from "../../bindings";
import type { NewsItem, Snapshot, TickerInfo } from "../datafeed-rest";
import type {
  ChartAggregate,
  SecondAggregate,
  TradeTick,
} from "../datafeed-live";
import type { ProviderCapabilities } from "../../bindings";
import type { DataSource, MarketSessionDef, SourceSeeds } from "../sources/types";
import type { SymbolRow, SymbolCategoryId, TypeFilter } from "../symbol-search";

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

/** True outside the Tauri shell — the single switch for the whole sample path. */
export function useSampleFeed(): boolean {
  return typeof window === "undefined" || !("__TAURI_INTERNALS__" in window);
}

/**
 * Closed-market testing hook: `?market=closed` freezes the sample market.
 * The live timer never starts and snapshots report the prior completed
 * session (`source: "prev"`), exercising closed-market rendering in the
 * watchlist, detail panel and legend. Template note: a real provider gets
 * this state for free from its snapshot/time logic — this flag only exists
 * so the sample can simulate both states deterministically.
 */
export function isSampleMarketClosed(): boolean {
  try {
    if (typeof window === "undefined") return false;
    return new URLSearchParams(window.location.search).get("market") === "closed";
  } catch {
    return false;
  }
}

/** Listed tickers only: like a real vendor, unknown symbols error instead
 *  of generating (history/info/snapshot throw; decorative paths return []). */
function isKnownSymbol(t: string): boolean {
  return UNIVERSE.some((r) => r.ticker === t);
}

/** Venue of a listed ticker: the qualified row when the exchange matches,
 *  else the primary (first) listing. */
function venueFor(ticker: string, exchange: string): string {
  const hit =
    UNIVERSE.find((r) => r.ticker === ticker && (!exchange || r.exchange === exchange)) ??
    UNIVERSE.find((r) => r.ticker === ticker);
  return hit?.exchange ?? "NSE";
}

/** Venue of a possibly qualified symbol. */
function venueOf(symbol: string): string {
  const t = bare(symbol);
  const parts = norm(symbol).split(",")[0].trim().split(":");
  return venueFor(t, parts.length > 1 ? parts[0] : "");
}

/** Slow volatility regime for a ticker-month ("YYYY-MM"): 0.7–1.3× base vol.
 *  Integer math, so every feed agrees exactly. */
function regimeFor(ticker: string, yearMonth: string): number {
  return 0.7 + (0.6 * Number(fnv1a(`reg|${ticker}|${yearMonth}`) % 1000n)) / 1000;
}

function assertSampleSymbol(symbol: string): void {
  const t = bare(symbol);
  if (!isKnownSymbol(t)) {
    throw new Error(`no sample data for ${t}`);
  }
}

/** Sample entitlement floor, in trading days per bar family — mirrors the
 *  backend's probed `history_floor` (see `docs/new-provider-requirements.md`
 *  §5). Shared by the published capabilities and the history clamping below
 *  so the two can never drift. */
export const SAMPLE_FLOOR_DAYS = { day: 3650, minute: 730, second: 60 } as const;

export type SampleBarFamily = keyof typeof SAMPLE_FLOOR_DAYS;

/** Oldest servable ET date (YYYY-MM-DD) for a bar family. */
export function sampleFloorDate(family: SampleBarFamily): string {
  return addDays(utcDateStr(Date.now()), -SAMPLE_FLOOR_DAYS[family]);
}

/** Drop dates older than the family's floor (string compare is chronological
 *  for YYYY-MM-DD). Past-the-floor pages come back empty so the renderer
 *  latches "history exhausted", exactly like the backend. */
function clampDates(dates: string[], family: SampleBarFamily): string[] {
  const floor = sampleFloorDate(family);
  return dates.filter((d) => d >= floor);
}

/** Static capabilities object for the sample provider — same shape as the
 *  backend's `ProviderCapabilities` (see `docs/new-provider-requirements.md`
 *  §5), published into the `providerCapabilities` signal by
 *  `providers/capabilities.ts` in sample mode. */
export function sampleCapabilities(): {
  name: string;
  resolutions: { seconds: number[]; minutes: number[]; daily: boolean; weeklyMonthlyFromDaily: boolean };
  maxBarsPerRequest: number;
  adjustedToggle: boolean;
  extendedHours: boolean;
  session: {
    timezone: string;
    openMin: number;
    closeMin: number;
    preMin: number;
    postMin: number;
  };
  reference: {
    search: boolean;
    searchTypeFilter: boolean;
    snapshot: boolean;
    dividends: boolean;
    splits: boolean;
    news: boolean;
    icons: boolean;
  };
  entitlements: {
    dataStatus: string;
    rawStatus: string;
    delaySec: number;
    historyFloor: { second: string; minute: string; day: string };
    stream: { minuteBars: boolean; secondBars: boolean; trades: boolean; quotes: boolean };
    checkedAt: string;
  };
} {
  return {
    name: "sample",
    resolutions: {
      seconds: [1, 5, 10, 15, 30, 45],
      minutes: [1, 5, 15, 30, 60, 120, 240],
      daily: true,
      weeklyMonthlyFromDaily: true,
    },
    maxBarsPerRequest: 50000,
    adjustedToggle: true,
    extendedHours: true,
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
      dividends: true,
      splits: true,
      news: true,
      icons: false,
    },
    entitlements: {
      dataStatus: "streaming",
      rawStatus: "OK",
      delaySec: 0,
      historyFloor: {
        second: sampleFloorDate("second"),
        minute: sampleFloorDate("minute"),
        day: sampleFloorDate("day"),
      },
      stream: { minuteBars: true, secondBars: true, trades: true, quotes: true },
      checkedAt: new Date().toISOString(),
    },
  };
}

// ── Seeded RNG ─────────────────────────────────────────────────────────────
// Bit-identical mirrors of the Rust backend's generator (see
// `src-tauri/src/data/provider/sample_provider.rs`): FNV-1a 64-bit scoped
// hashes feed xorshift64* streams, so both feeds emit the same numbers for
// the same (purpose, ticker, date) scope. BigInt keeps the full 64 bits
// (a JS number cannot); the float pipeline below consumes only exact
// operations, so history matches the backend bit-for-bit up to the libm
// transcendental calls (log/cos/sqrt/exp), which round2() erases.

const FNV_OFFSET = 0xcbf29ce484222325n;
const FNV_PRIME = 0x100000001b3n;
const U64_MASK = 0xffffffffffffffffn;
const XS_MULT = 0x2545f4914f6cdd1dn;

export function fnv1a(s: string): bigint {
  let h = FNV_OFFSET;
  for (let i = 0; i < s.length; i++) {
    h ^= BigInt(s.charCodeAt(i));
    h = (h * FNV_PRIME) & U64_MASK;
  }
  return h;
}

/** xorshift64* stream as [0, 1) doubles — exact: the output word's top 53
 *  bits over 2^53 (both exactly representable, power-of-two division). */
export function xorshift64(seed: bigint): () => number {
  let x = seed === 0n ? 0x9e3779b97f4a7c15n : seed;
  return () => {
    x ^= x >> 12n;
    x = (x ^ (x << 25n)) & U64_MASK;
    x ^= x >> 27n;
    x &= U64_MASK;
    const out = (x * XS_MULT) & U64_MASK;
    return Number(out >> 11n) / 2 ** 53;
  };
}

/** Standard-normal sample (Box–Muller, mirrors the backend). */
function gaussian(rng: () => number): number {
  let u = 0;
  while (u === 0) u = rng();
  const v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

const norm = (s: string): string => s.trim().toUpperCase();
const bare = (s: string): string => {
  const h = norm(s).split(",")[0].trim();
  return h.includes(":") ? (h.split(":").pop() as string) : h;
};

// ── IST time helpers + sample calendar ─────────────────────────────────────
// Sample mode models the NSE equity session (09:15–15:30 IST) by default;
// each venue below carries its own hours, time zone and presentation while
// intraday buckets run on exchange-local wall time and day enumeration
// follows UTC calendar dates like the backend (weekends + NSE holidays
// skipped where they apply).

/** NSE regular session: 09:15–15:30 IST, in minutes since IST midnight. */
export const IST_OPEN_MIN = 9 * 60 + 15; // 555
/** NSE regular session close, in minutes since IST midnight. */
export const IST_CLOSE_MIN = 15 * 60 + 30; // 930

/** Session + presentation contract per sample venue (mirrors the backend's
 *  table). Index venues follow their cash market. Lunch-break venues carry
 *  two intervals; generation and specs handle both. */
export type VenueInfo = {
  exchange: string;
  timezone: string;
  intervals: [number, number][];
  pre: number;
  post: number;
  currency: string;
  pricescale: number;
  minmov: number;
  nseHolidays: boolean;
};

const VENUES: VenueInfo[] = [
  { exchange: "NSE", timezone: "Asia/Kolkata", intervals: [[555, 375]], pre: 15, post: 30, currency: "INR", pricescale: 100, minmov: 5, nseHolidays: true },
  { exchange: "BSE", timezone: "Asia/Kolkata", intervals: [[555, 375]], pre: 15, post: 30, currency: "INR", pricescale: 100, minmov: 5, nseHolidays: true },
  { exchange: "NSE_INDEX", timezone: "Asia/Kolkata", intervals: [[555, 375]], pre: 15, post: 30, currency: "INR", pricescale: 100, minmov: 5, nseHolidays: true },
  { exchange: "BSE_INDEX", timezone: "Asia/Kolkata", intervals: [[555, 375]], pre: 15, post: 30, currency: "INR", pricescale: 100, minmov: 5, nseHolidays: true },
  { exchange: "NFO", timezone: "Asia/Kolkata", intervals: [[555, 375]], pre: 15, post: 30, currency: "INR", pricescale: 100, minmov: 5, nseHolidays: true },
  { exchange: "MCX", timezone: "Asia/Kolkata", intervals: [[540, 870]], pre: 0, post: 0, currency: "INR", pricescale: 100, minmov: 5, nseHolidays: true },
  { exchange: "NASDAQ", timezone: "America/New_York", intervals: [[570, 390]], pre: 330, post: 240, currency: "USD", pricescale: 100, minmov: 1, nseHolidays: false },
  { exchange: "NYSE", timezone: "America/New_York", intervals: [[570, 390]], pre: 330, post: 240, currency: "USD", pricescale: 100, minmov: 1, nseHolidays: false },
  { exchange: "LSE", timezone: "Europe/London", intervals: [[480, 510]], pre: 0, post: 0, currency: "GBP", pricescale: 100, minmov: 1, nseHolidays: false },
  { exchange: "XETRA", timezone: "Europe/Berlin", intervals: [[540, 510]], pre: 0, post: 0, currency: "EUR", pricescale: 100, minmov: 1, nseHolidays: false },
  { exchange: "TSE", timezone: "Asia/Tokyo", intervals: [[540, 150], [750, 150]], pre: 0, post: 0, currency: "JPY", pricescale: 1, minmov: 1, nseHolidays: false },
  { exchange: "HKEX", timezone: "Asia/Hong_Kong", intervals: [[570, 150], [780, 180]], pre: 0, post: 0, currency: "HKD", pricescale: 100, minmov: 1, nseHolidays: false },
  { exchange: "ASX", timezone: "Australia/Sydney", intervals: [[600, 360]], pre: 0, post: 0, currency: "AUD", pricescale: 100, minmov: 1, nseHolidays: false },
];

/** Venue contract for an exchange code. Throws on unknown codes (callers
 *  resolve through `venueOf`, which only yields listed venues). */
export function venueInfo(exchange: string): VenueInfo {
  const hit = VENUES.find((v) => v.exchange === exchange);
  if (!hit) throw new Error(`unknown sample venue: ${exchange}`);
  return hit;
}

/** UTC calendar date string for a UTC instant — the backend's `today_utc`
 *  basis for split anchors, floors and "today"-derived keys. */
function utcDateStr(utcMs: number): string {
  const t = new Date(utcMs);
  return `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, "0")}-${String(t.getUTCDate()).padStart(2, "0")}`;
}

const tzFmtCache = new Map<string, Intl.DateTimeFormat>();
function tzParts(tz: string, utcMs: number): { y: number; m: number; d: number; hh: number; mm: number } {
  let f = tzFmtCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
    tzFmtCache.set(tz, f);
  }
  const p: Record<string, string> = {};
  for (const x of f.formatToParts(new Date(utcMs))) {
    if (x.type !== "literal") p[x.type] = x.value;
  }
  return { y: +p.year, m: +p.month, d: +p.day, hh: +p.hour === 24 ? 0 : +p.hour, mm: +p.minute };
}

/** UTC millis of an exchange-local wall-clock time. Single probe: UTC =
 *  wall-as-UTC minus the zone offset measured at that instant (DST-aware;
 *  session opens never land in a transition hour). */
function tzWallToUtc(tz: string, y: number, m: number, d: number, hh: number, mm: number): number {
  const wallAsUtc = Date.UTC(y, m - 1, d, hh, mm);
  const p = tzParts(tz, wallAsUtc);
  const asUtc = Date.UTC(p.y, p.m - 1, p.d, p.hh, p.mm);
  return wallAsUtc - (asUtc - wallAsUtc);
}

/**
 * NSE trading holidays (full-day closures), YYYY-MM-DD — mirrors the
 * backend's list; verified yearly against NSE circulars for 2024–2026.
 */
const SAMPLE_HOLIDAYS: string[] = [
  // 2024
  "2024-01-22", "2024-03-08", "2024-03-25", "2024-03-29",
  "2024-04-11", "2024-04-17", "2024-05-01", "2024-05-20",
  "2024-06-17", "2024-07-17", "2024-08-15", "2024-10-02",
  "2024-10-31", "2024-11-01", "2024-11-15", "2024-12-25",
  // 2025
  "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10",
  "2025-04-14", "2025-04-18", "2025-05-01", "2025-08-15",
  "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22",
  "2025-11-05", "2025-12-25",
  // 2026
  "2026-03-04", "2026-03-20", "2026-03-31", "2026-04-03",
  "2026-04-14", "2026-05-01", "2026-05-27", "2026-06-26",
  "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-24",
  "2026-12-25",
];

const SAMPLE_HOLIDAY_SET = new Set(SAMPLE_HOLIDAYS);

/** True for a sample trading day: weekdays only, mirroring the backend
 *  (no holiday list on either side — weekends skipped separately). */
export function isSampleTradingDay(dateStr: string): boolean {
  return !isWeekend(dateStr) && !SAMPLE_HOLIDAY_SET.has(dateStr);
}

/** Holidays as "YYYYMMDD,..." for session descriptors. */
function holidaySpec(): string {
  return SAMPLE_HOLIDAYS.map((d) => d.replace(/-/g, "")).join(",");
}

/** Per-venue session descriptor for a listed symbol (mirrors the backend's
 *  symbol_session): venue hours and time zone, lunch breaks included. */
export function sampleSymbolSession(symbol: string): SymbolSession {
  const t = bare(symbol);
  if (!isKnownSymbol(t)) throw new Error(`no sample data for ${t}`);
  const info = venueInfo(venueOf(symbol));
  const fmt = (m: number): string =>
    `${String(Math.floor(m / 60)).padStart(2, "0")}${String(((m % 60) + 60) % 60).padStart(2, "0")}`;
  const rth = info.intervals.map(([o, len]) => `${fmt(o)}-${fmt(o + len)}`).join(",");
  const subsessions: SymbolSession["subsessions"] = [
    { id: "regular", description: "Regular Trading Hours", session: rth, corrections: "" },
  ];
  if (info.pre + info.post > 0) {
    const firstOpen = info.intervals[0][0];
    const lastClose = Math.max(...info.intervals.map(([o, len]) => o + len));
    subsessions.push(
      { id: "extended", description: "Extended Trading Hours", session: `${fmt(firstOpen - info.pre)}-${fmt(lastClose + info.post)}`, corrections: "" },
      { id: "premarket", description: "Premarket", session: `${fmt(firstOpen - info.pre)}-${fmt(firstOpen)}`, corrections: "" },
      { id: "postmarket", description: "Postmarket", session: `${fmt(lastClose)}-${fmt(lastClose + info.post)}`, corrections: "" },
    );
  }
  return {
    timezone: info.timezone,
    session: rth,
    subsessions,
    holidays: info.nseHolidays ? holidaySpec() : "",
    corrections: "",
    pricescale: info.pricescale,
    minmov: info.minmov,
    variableTickSize: "",
  };
}

function parseDate(s: string): [number, number, number] {
  const [y, m, d] = s.split("-").map(Number);
  return [y, m, d];
}

function addDays(dateStr: string, n: number): string {
  const [y, m, d] = parseDate(dateStr);
  const t = new Date(Date.UTC(y, m - 1, d) + n * 86400000);
  return `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, "0")}-${String(
    t.getUTCDate(),
  ).padStart(2, "0")}`;
}

function isWeekend(dateStr: string): boolean {
  const [y, m, d] = parseDate(dateStr);
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return wd === 0 || wd === 6;
}

/** Last `n` sample trading days (weekends skipped) ending at `end`
 *  (default today), as UTC calendar dates like the backend. */
function tradingDaysBack(n: number, end?: string): string[] {
  const out: string[] = [];
  let cur = end ?? utcDateStr(Date.now());
  while (out.length < Math.max(1, n)) {
    if (isSampleTradingDay(cur)) out.unshift(cur);
    cur = addDays(cur, -1);
  }
  return out;
}

/** Sample trading days in [from, to] (inclusive UTC dates). */
function tradingDaysInRange(from: string, to: string): string[] {
  const out: string[] = [];
  let cur = from;
  while (cur <= to) {
    if (isSampleTradingDay(cur)) out.push(cur);
    cur = addDays(cur, 1);
  }
  return out;
}

// ── Per-ticker model ─────────────────────────────────────────────────────────

type TickerModel = {
  base: number;
  vol: number;
  dayVol: number;
};

function modelFor(ticker: string): TickerModel {
  const h = fnv1a(`model|${ticker}`);
  return {
    base: 25 + Number(h % 297500n) / 100,
    vol: 0.008 + Number((h >> 8n) % 200n) / 10000,
    dayVol: 500000 + Number((h >> 16n) % 40000000n),
  };
}

// ── Corporate actions (splits AND bonus issues share the SplitEvent shape) ──

export function sampleSplits(symbol: string): SplitEvent[] {
  if (!isKnownSymbol(bare(symbol))) return [];
  const t = bare(symbol);
  const h = fnv1a(`splits|${t}`);
  const out: SplitEvent[] = [];
  const today = utcDateStr(Date.now());
  const stamp = (dateStr: string): number => {
    const [y, m, d] = parseDate(dateStr);
    return Date.UTC(y, m - 1, d, 12) / 1000;
  };
  const kind = Number(h % 7n);
  if (kind === 0) {
    // 2:1 split ~1.5y ago.
    out.push({ date: stamp(addDays(today, -380)), from: 1, to: 2 });
  } else if (kind === 1) {
    // 1:1 bonus issue ~9mo ago (renders as "2:1" via the to:from label).
    out.push({ date: stamp(addDays(today, -270)), from: 1, to: 2 });
  } else if (kind === 2) {
    // 3:2 bonus ~6mo ago + an old 2:1 split.
    out.push({ date: stamp(addDays(today, -180)), from: 2, to: 3 });
    out.push({ date: stamp(addDays(today, -900)), from: 1, to: 2 });
  }
  return out.sort((a, b) => b.date - a.date);
}

/** Pre-split bars are on the as-traded basis; scale them when adjusted. */
function applySplits(bars: Candle[], splits: SplitEvent[], adjusted: boolean): Candle[] {
  if (!adjusted || splits.length === 0) return bars;
  const asc = splits.slice().sort((a, b) => a.date - b.date);
  return bars.map((b) => {
    let f = 1;
    for (const s of asc) {
      if (b.time < s.date) f *= s.from / s.to;
      else break;
    }
    if (f === 1) return b;
    return {
      time: b.time,
      open: round2(b.open * f),
      high: round2(b.high * f),
      low: round2(b.low * f),
      close: round2(b.close * f),
      volume: Math.round(b.volume / f),
    };
  });
}

export function sampleDividends(symbol: string): DividendEvent[] {
  if (!isKnownSymbol(bare(symbol))) return [];
  const t = bare(symbol);
  const h = fnv1a(`div|${t}`);
  const out: DividendEvent[] = [];
  const now = new Date();
  let y = now.getUTCFullYear();
  let q = Math.floor(now.getUTCMonth() / 3);
  for (let i = 0; i < 8; i++) {
    q -= 1;
    if (q < 0) {
      q = 3;
      y -= 1;
    }
    const m = q * 3 + 1;
    // 1-indexed month like the backend's from_ymd_opt (m ∈ {1,4,7,10}).
    const amt = 0.2 + Number((h >> BigInt(i % 24)) % 230n) / 100;
    out.push({ date: Date.UTC(y, m - 1, 15, 12) / 1000, amount: Math.round(amt * 100) / 100 });
  }
  return out.sort((a, b) => b.date - a.date);
}

// ── Daily series ─────────────────────────────────────────────────────────────

const round2 = (n: number): number => Math.round(n * 100) / 100;

function genDailyUnadjusted(ticker: string, dates: string[]): Candle[] {
  const t = bare(ticker);
  const m = modelFor(t);
  const rng = xorshift64(fnv1a(`daily|${t}|${dates[0] ?? "x"}|${dates.length}`));
  const closes: number[] = [];
  let px = m.base * 0.55;
  for (let i = 0; i < dates.length; i++) {
    px = Math.max(1, px * (1 + gaussian(rng) * m.vol + 0.0004));
    closes.push(px);
  }
  const scale = m.base / closes[closes.length - 1];
  return dates.map((ds, i) => {
    const [y, mo, d] = parseDate(ds);
    const vol = m.vol * regimeFor(t, ds.slice(0, 7));
    // Overnight gap on its own stream so the walk is untouched.
    const gapRng = xorshift64(fnv1a(`gap|${t}|${ds}`));
    const gap = gaussian(gapRng) * vol * 0.4;
    const prev = i === 0 ? closes[0] / (1 + gaussian(rng) * vol * 0.3) : closes[i - 1];
    const open = prev * scale * (1 + gap);
    const close = closes[i] * scale;
    const spread = Math.abs(gaussian(rng)) * vol * 0.6 * close;
    const high = Math.max(open, close) + spread * rng();
    const low = Math.min(open, close) - spread * rng();
    return {
      time: Date.UTC(y, mo - 1, d, 12) / 1000,
      open: round2(open),
      high: round2(high),
      low: round2(Math.max(0.01, low)),
      close: round2(close),
      volume: Math.round(m.dayVol * (0.6 + rng() * 0.9)),
    };
  });
}

export function sampleDailyHistory(symbol: string, days: number, adjusted: boolean): Candle[] {
  assertSampleSymbol(symbol);
  const dates = clampDates(tradingDaysBack(days), "day");
  if (dates.length === 0) return [];
  return applySplits(genDailyUnadjusted(symbol, dates), sampleSplits(symbol), adjusted);
}

export function sampleDailyHistoryBefore(
  symbol: string,
  beforeSec: number,
  spanDays: number,
  adjusted: boolean,
): Candle[] {
  assertSampleSymbol(symbol);
  const end = utcDateStr((beforeSec - 1) * 1000);
  const dates = clampDates(tradingDaysBack(spanDays, end), "day");
  if (dates.length === 0) return [];
  const bars = applySplits(genDailyUnadjusted(symbol, dates), sampleSplits(symbol), adjusted);
  return bars.filter((b) => b.time < beforeSec);
}

// ── Intraday (minute) series ─────────────────────────────────────────────────

function genMinutesUnadjusted(ticker: string, venue: string, dates: string[], multMin: number): Candle[] {
  const t = bare(ticker);
  const m = modelFor(t);
  const info = venueInfo(venue);
  const totalMin = info.intervals.reduce((a, [, len]) => a + len, 0);
  // One extra leading calendar day anchors the first session open
  // (mirrors the backend — even a weekend, its close is anchor-only).
  const ext = [addDays(dates[0], -1)];
  const daily = genDailyUnadjusted(t, [...ext, ...dates]);
  const closeByDate = new Map<string, number>();
  [...ext, ...dates].forEach((ds, i) => closeByDate.set(ds, daily[i].close));
  const out: Candle[] = [];
  // Volume shape anchors: first interval open → last interval close.
  const dayOpen = info.intervals[0][0];
  const dayClose = Math.max(...info.intervals.map(([o, len]) => o + len));
  dates.forEach((ds) => {
    const [y, mo, d] = parseDate(ds);
    const anchor = closeByDate.get(addDays(ds, -1)) ?? closeByDate.get(ds) ?? m.base;
    const rng = xorshift64(fnv1a(`min|${t}|${ds}|${multMin}`));
    const vol = m.vol * regimeFor(t, ds.slice(0, 7));
    let px = anchor * (1 + gaussian(rng) * vol * 0.15);
    const vBase = m.dayVol / totalMin;
    for (const [openMin, lenMin] of info.intervals) {
      const perDay = Math.floor(lenMin / multMin);
      for (let i = 0; i < perDay; i++) {
        const startMin = openMin + i * multMin;
        const hh = Math.floor(startMin / 60);
        const mm = startMin % 60;
        const time = tzWallToUtc(info.timezone, y, mo, d, hh, mm) / 1000;
        const open = px;
        const steps = Math.max(1, multMin);
        let high = open;
        let low = open;
        for (let s = 0; s < steps; s++) {
          px = Math.max(0.5, px * (1 + gaussian(rng) * vol * 0.09));
          high = Math.max(high, px);
          low = Math.min(low, px);
        }
        // U-shaped volume: heavier near the session open and close.
        const tod = hh * 60 + mm;
        const shape = 1 + 1.6 * Math.exp(-Math.pow(tod - dayOpen, 2) / 4000) + 1.2 * Math.exp(-Math.pow(tod - dayClose, 2) / 6000);
        out.push({
          time,
          open: round2(open),
          high: round2(high),
          low: round2(low),
          close: round2(px),
          volume: Math.round(vBase * multMin * shape * (0.5 + rng())),
        });
      }
    }
  });
  return out;
}

export function sampleMinuteHistory(
  symbol: string,
  days: number,
  intervalMin: number,
  adjusted: boolean,
): Candle[] {
  assertSampleSymbol(symbol);
  const dates = clampDates(tradingDaysBack(days), "minute");
  if (dates.length === 0) return [];
  return applySplits(
    genMinutesUnadjusted(symbol, venueOf(symbol), dates, Math.max(1, intervalMin)),
    sampleSplits(symbol),
    adjusted,
  );
}

export function sampleAggregatesBefore(
  symbol: string,
  timespan: "second" | "minute",
  mult: number,
  beforeSec: number,
  spanDays: number,
  adjusted: boolean,
): Candle[] {
  assertSampleSymbol(symbol);
  const end = utcDateStr((beforeSec - 1) * 1000);
  const dates = clampDates(tradingDaysBack(spanDays, end), timespan === "second" ? "second" : "minute");
  if (dates.length === 0) return [];
  const bars =
    timespan === "second"
      ? genSecondsUnadjusted(symbol, venueOf(symbol), dates, mult)
      : genMinutesUnadjusted(symbol, venueOf(symbol), dates, mult);
  return applySplits(bars, sampleSplits(symbol), adjusted).filter((b) => b.time < beforeSec);
}

// ── Second series (venue session grid) ──────────────────────────────────────

function genSecondsUnadjusted(ticker: string, venue: string, dates: string[], multSec: number): Candle[] {
  const t = bare(ticker);
  const m = modelFor(t);
  const info = venueInfo(venue);
  const totalSec = info.intervals.reduce((a, [, len]) => a + len, 0) * 60;
  // One extra leading calendar day anchors the first session open
  // (mirrors the backend — even a weekend, its close is anchor-only).
  const ext = [addDays(dates[0], -1)];
  const daily = genDailyUnadjusted(t, [...ext, ...dates]);
  const byDate = new Map<string, { o: number; c: number }>();
  [...ext, ...dates].forEach((ds, i) =>
    // i ≥ 1 for every requested date (ext prepends one); the ext row's own
    // open is anchor-only, matching the backend's ext-day close.
    byDate.set(ds, { o: i === 0 ? daily[0].close : daily[i - 1].close, c: daily[i].close }),
  );
  const out: Candle[] = [];
  const totalBars = Math.floor(totalSec / multSec);
  for (const ds of dates) {
    const [y, mo, d] = parseDate(ds);
    const ref = byDate.get(ds) ?? { o: m.base, c: m.base };
    const rng = xorshift64(fnv1a(`sec|${t}|${ds}|${multSec}`));
    const vol = m.vol * regimeFor(t, ds.slice(0, 7));
    // Walk open→close so the day shape stays plausible (one drift over the
    // whole day, bars following each interval in turn).
    const drift = (ref.c - ref.o) / totalBars;
    let px = ref.o;
    const steps = Math.min(multSec, 5);
    for (const [openMin, lenMin] of info.intervals) {
      const sessionOpen =
        tzWallToUtc(info.timezone, y, mo, d, Math.floor(openMin / 60), openMin % 60) / 1000;
      const perDay = Math.floor((lenMin * 60) / multSec);
      for (let i = 0; i < perDay; i++) {
        const time = sessionOpen + i * multSec;
        const open = px;
        let high = open;
        let low = open;
        for (let s = 0; s < steps; s++) {
          px = Math.max(0.5, px + drift / steps + gaussian(rng) * vol * 0.02 * px);
          high = Math.max(high, px);
          low = Math.min(low, px);
        }
        out.push({
          time,
          open: round2(open),
          high: round2(high),
          low: round2(low),
          close: round2(px),
          volume: Math.round((m.dayVol / totalSec) * multSec * (0.4 + rng() * 1.2)),
        });
      }
    }
  }
  return out;
}

export function sampleSecondHistory(
  symbol: string,
  mult: number,
  days: number,
  adjusted: boolean,
): Candle[] {
  assertSampleSymbol(symbol);
  const dates = clampDates(tradingDaysBack(days), "second");
  if (dates.length === 0) return [];
  return applySplits(genSecondsUnadjusted(symbol, venueOf(symbol), dates, mult), sampleSplits(symbol), adjusted);
}

export function sampleSecondHistoryTail(
  symbol: string,
  mult: number,
  sinceSec: number,
  adjusted: boolean,
): Candle[] {
  assertSampleSymbol(symbol);
  const today = utcDateStr(Date.now());
  const dates = tradingDaysInRange(addDays(today, -1), today);
  const bars = applySplits(genSecondsUnadjusted(symbol, venueOf(symbol), dates, mult), sampleSplits(symbol), adjusted);
  return bars.filter((b) => b.time > sinceSec);
}

// ── Reference ────────────────────────────────────────────────────────────────

type UniverseRow = {
  ticker: string;
  name: string;
  exchange: string;
  type: string;
  sector: string;
};

const UNIVERSE: UniverseRow[] = [
  { ticker: "RELIANCE", name: "Reliance Industries Ltd.", exchange: "NSE", type: "EQ", sector: "Energy" },
  { ticker: "TCS", name: "Tata Consultancy Services", exchange: "NSE", type: "EQ", sector: "Technology" },
  { ticker: "INFY", name: "Infosys Ltd.", exchange: "NSE", type: "EQ", sector: "Technology" },
  { ticker: "HDFCBANK", name: "HDFC Bank Ltd.", exchange: "NSE", type: "EQ", sector: "Banking" },
  { ticker: "ICICIBANK", name: "ICICI Bank Ltd.", exchange: "NSE", type: "EQ", sector: "Banking" },
  { ticker: "SBIN", name: "State Bank of India", exchange: "NSE", type: "EQ", sector: "Banking" },
  { ticker: "TATAMOTORS", name: "Tata Motors Ltd.", exchange: "NSE", type: "EQ", sector: "Auto" },
  { ticker: "AXISBANK", name: "Axis Bank Ltd.", exchange: "NSE", type: "EQ", sector: "Banking" },
  { ticker: "KOTAKBANK", name: "Kotak Mahindra Bank", exchange: "NSE", type: "EQ", sector: "Banking" },
  { ticker: "LT", name: "Larsen & Toubro Ltd.", exchange: "NSE", type: "EQ", sector: "Infra" },
  { ticker: "TITAN", name: "Titan Company Ltd.", exchange: "NSE", type: "EQ", sector: "Consumer" },
  { ticker: "ASIANPAINT", name: "Asian Paints Ltd.", exchange: "NSE", type: "EQ", sector: "Consumer" },
  { ticker: "BAJFINANCE", name: "Bajaj Finance Ltd.", exchange: "NSE", type: "EQ", sector: "Finance" },
  { ticker: "HINDUNILVR", name: "Hindustan Unilever Ltd.", exchange: "NSE", type: "EQ", sector: "FMCG" },
  { ticker: "SUNPHARMA", name: "Sun Pharmaceutical Ltd.", exchange: "NSE", type: "EQ", sector: "Pharma" },
  { ticker: "MARUTI", name: "Maruti Suzuki India Ltd.", exchange: "NSE", type: "EQ", sector: "Auto" },
  { ticker: "ULTRACEMCO", name: "UltraTech Cement Ltd.", exchange: "NSE", type: "EQ", sector: "Cement" },
  { ticker: "WIPRO", name: "Wipro Ltd.", exchange: "NSE", type: "EQ", sector: "Technology" },
  { ticker: "TATASTEEL", name: "Tata Steel Ltd.", exchange: "BSE", type: "EQ", sector: "Metals" },
  { ticker: "SENSEX", name: "BSE SENSEX Index", exchange: "BSE_INDEX", type: "IX", sector: "Index" },
  // NSE indices (true index venues, matching the broker).
  { ticker: "NIFTY", name: "Nifty 50 Index", exchange: "NSE_INDEX", type: "IX", sector: "Index" },
  { ticker: "BANKNIFTY", name: "Nifty Bank Index", exchange: "NSE_INDEX", type: "IX", sector: "Index" },
  { ticker: "FINNIFTY", name: "Nifty Financial Services Index", exchange: "NSE_INDEX", type: "IX", sector: "Index" },
  { ticker: "INDIAVIX", name: "India VIX Volatility Index", exchange: "NSE_INDEX", type: "IX", sector: "Index" },
  // BSE venue-qualified equities (same issuer, BSE venue) + BSE index.
  { ticker: "RELIANCE", name: "Reliance Industries Ltd.", exchange: "BSE", type: "EQ", sector: "Energy" },
  { ticker: "INFY", name: "Infosys Ltd.", exchange: "BSE", type: "EQ", sector: "Banking" },
  { ticker: "TCS", name: "Tata Consultancy Services", exchange: "BSE", type: "EQ", sector: "Technology" },
  { ticker: "BANKEX", name: "BSE Bankex Index", exchange: "BSE_INDEX", type: "IX", sector: "Index" },
  // NFO derivatives (short readable tickers; full contract detail in description).
  { ticker: "NIFTYFUT", name: "Nifty Futures, Monthly Expiry", exchange: "NFO", type: "FUT", sector: "Derivatives" },
  { ticker: "BANKNIFTYFUT", name: "Bank Nifty Futures, Monthly Expiry", exchange: "NFO", type: "FUT", sector: "Derivatives" },
  { ticker: "NIFTY26000CE", name: "Nifty 26000 Call, 30 Oct Expiry", exchange: "NFO", type: "OPT", sector: "Derivatives" },
  { ticker: "NIFTY26000PE", name: "Nifty 26000 Put, 30 Oct Expiry", exchange: "NFO", type: "OPT", sector: "Derivatives" },
  { ticker: "BANKNIFTY55000CE", name: "Bank Nifty 55000 Call, 29 Oct Expiry", exchange: "NFO", type: "OPT", sector: "Derivatives" },
  // MCX commodities (futures).
  { ticker: "GOLD", name: "Gold Futures", exchange: "MCX", type: "FUT", sector: "Commodities" },
  { ticker: "SILVER", name: "Silver Futures", exchange: "MCX", type: "FUT", sector: "Commodities" },
  { ticker: "CRUDEOIL", name: "Crude Oil Futures", exchange: "MCX", type: "FUT", sector: "Commodities" },
  { ticker: "NATURALGAS", name: "Natural Gas Futures", exchange: "MCX", type: "FUT", sector: "Commodities" },
  // Dated near-month contracts served by the broker-backed seeds.
  { ticker: "NIFTY27OCT26FUT", name: "Nifty Futures, 27 Oct 2026 Expiry", exchange: "NFO", type: "FUT", sector: "Derivatives" },
  { ticker: "BANKNIFTY27OCT26FUT", name: "Bank Nifty Futures, 27 Oct 2026 Expiry", exchange: "NFO", type: "FUT", sector: "Derivatives" },
  { ticker: "GOLD04DEC26FUT", name: "Gold Futures, 04 Dec 2026 Expiry", exchange: "MCX", type: "FUT", sector: "Commodities" },
  // US equities (America/New_York).
  { ticker: "AAPL", name: "Apple Inc.", exchange: "NASDAQ", type: "EQ", sector: "Technology" },
  { ticker: "MSFT", name: "Microsoft Corp.", exchange: "NASDAQ", type: "EQ", sector: "Technology" },
  { ticker: "NVDA", name: "NVIDIA Corp.", exchange: "NASDAQ", type: "EQ", sector: "Technology" },
  { ticker: "TSLA", name: "Tesla Inc.", exchange: "NASDAQ", type: "EQ", sector: "Auto" },
  { ticker: "JPM", name: "JPMorgan Chase & Co.", exchange: "NYSE", type: "EQ", sector: "Banking" },
  { ticker: "XOM", name: "Exxon Mobil Corp.", exchange: "NYSE", type: "EQ", sector: "Energy" },
  { ticker: "NDX", name: "Nasdaq 100 Index", exchange: "NASDAQ", type: "IX", sector: "Index" },
  { ticker: "SPX", name: "S&P 500 Index", exchange: "NYSE", type: "IX", sector: "Index" },
  // UK equities (Europe/London).
  { ticker: "SHEL", name: "Shell plc", exchange: "LSE", type: "EQ", sector: "Energy" },
  { ticker: "HSBA", name: "HSBC Holdings plc", exchange: "LSE", type: "EQ", sector: "Banking" },
  { ticker: "AZN", name: "AstraZeneca plc", exchange: "LSE", type: "EQ", sector: "Pharma" },
  { ticker: "FTSE", name: "FTSE 100 Index", exchange: "LSE", type: "IX", sector: "Index" },
  // EU equities (Xetra, Europe/Berlin).
  { ticker: "SAP", name: "SAP SE", exchange: "XETRA", type: "EQ", sector: "Technology" },
  { ticker: "SIE", name: "Siemens AG", exchange: "XETRA", type: "EQ", sector: "Infra" },
  { ticker: "DAX", name: "DAX Index", exchange: "XETRA", type: "IX", sector: "Index" },
  // Japanese equities (Asia/Tokyo, lunch break).
  { ticker: "7203", name: "Toyota Motor Corp.", exchange: "TSE", type: "EQ", sector: "Auto" },
  { ticker: "6758", name: "Sony Group Corp.", exchange: "TSE", type: "EQ", sector: "Technology" },
  { ticker: "9984", name: "SoftBank Group Corp.", exchange: "TSE", type: "EQ", sector: "Finance" },
  { ticker: "NIKKEI", name: "Nikkei 225 Index", exchange: "TSE", type: "IX", sector: "Index" },
  // Hong Kong equities (Asia/Hong_Kong, lunch break).
  { ticker: "0700", name: "Tencent Holdings Ltd.", exchange: "HKEX", type: "EQ", sector: "Technology" },
  { ticker: "0939", name: "China Construction Bank Corp.", exchange: "HKEX", type: "EQ", sector: "Banking" },
  { ticker: "0388", name: "Hong Kong Exchanges & Clearing Ltd.", exchange: "HKEX", type: "EQ", sector: "Finance" },
  { ticker: "HSI", name: "Hang Seng Index", exchange: "HKEX", type: "IX", sector: "Index" },
  // Australian equities (Australia/Sydney).
  { ticker: "CBA", name: "Commonwealth Bank of Australia", exchange: "ASX", type: "EQ", sector: "Banking" },
  { ticker: "BHP", name: "BHP Group Ltd.", exchange: "ASX", type: "EQ", sector: "Metals" },
  { ticker: "ASX200", name: "S&P/ASX 200 Index", exchange: "ASX", type: "IX", sector: "Index" },
];

export function sampleSearch(query: string, typeFilter: string | null): SymbolSearchResult[] {
  const q = query.trim().toUpperCase();
  if (!q) return [];
  const rows = UNIVERSE.filter(
    (r) =>
      (!typeFilter || r.type === typeFilter) &&
      (r.ticker.includes(q) || r.name.toUpperCase().includes(q)),
  ).map((r) => ({
    ticker: r.ticker,
    name: r.name,
    market: "stocks",
    locale: "us",
    primaryExchange: r.exchange,
    type: r.type,
  }));
  // Always allow opening exactly what was typed (custom/unknown tickers work).
  // It carries the requested filter type so it survives filtering and lands
  // on the matching tab.
  if (!rows.some((r) => r.ticker === q)) {
    const fallbackType = typeFilter || "EQ";
    rows.unshift({
      ticker: q,
      name: `${q} (sample)`,
      market: "stocks",
      locale: "us",
      primaryExchange: "NSE",
      type: fallbackType,
    });
  }
  return rows.slice(0, 50);
}

export function sampleTickerInfo(symbol: string): TickerInfo {
  assertSampleSymbol(symbol);
  const t = bare(symbol);
  const m = modelFor(t);
  // Prefer the venue-qualified row when the caller passes one
  // ("BSE:RELIANCE"); otherwise the primary (first) listing. Live series
  // stay keyed by bare ticker (one series per issuer).
  const venue = norm(symbol).split(",")[0].trim().split(":");
  const known =
    (venue.length > 1
      ? UNIVERSE.find((r) => r.ticker === t && r.exchange === venue[0])
      : undefined) ?? UNIVERSE.find((r) => r.ticker === t);
  const exchange = known?.exchange ?? "NSE";
  return {
    ticker: t,
    name: known?.name ?? `${t} (sample)`,
    exchange,
    industry: known?.sector ?? "Sample",
    sector: known?.sector ?? "Sample",
    currency: venueInfo(known?.exchange ?? "NSE").currency,
    description: `${known?.name ?? t} — deterministic sample instrument for browser development.`,
    homepageUrl: null,
    totalEmployees: 10000 + Number(fnv1a(`ref|${t}`) % 150000n),
    marketCap: Math.round(m.base * (100000000 + Number(fnv1a(`mc|${t}`) % 900000000n))),
    figi: null,
    iconUrl: null,
  };
}

export function sampleSnapshot(symbol: string): Snapshot {
  assertSampleSymbol(symbol);
  const t = bare(symbol);
  const daily = sampleDailyHistory(t, 5, true);
  const lastBar = daily[daily.length - 1];
  const prev = daily[daily.length - 2] ?? lastBar;
  // Intraday drift keyed by hour so refetches move the quote (the sample
  // market is always "open", mirroring the backend).
  const h = new Date().getUTCHours();
  const drift = (Number(fnv1a(`snap|${t}|${h}`) % 200n) - 100) / 10000;
  const last = round2(lastBar.close * (1 + drift));
  const change = round2(last - prev.close);
  return {
    ticker: t,
    last,
    change,
    changePercent: prev.close > 0 ? round2((change / prev.close) * 100) : 0,
    dayHigh: Math.max(lastBar.high, last),
    dayLow: Math.min(lastBar.low, last),
    dayVolume: Math.round(lastBar.volume * 0.65),
    extChangePercent: null,
    source: "live",
    updatedNs: Date.now() * 1e6,
  };
}

export function sampleLatestNews(symbol: string, limit: number): NewsItem[] {
  if (!isKnownSymbol(bare(symbol))) return [];
  const t = bare(symbol);
  const now = Date.now();
  const heads = [
    `${t} holds gains as sample volume runs above average`,
    `What to watch in ${t} into the close, per sample desk notes`,
    `${t} options flow tilts bullish in afternoon sample trading`,
    `Analysts restate sample targets on ${t} after steady week`,
  ];
  return heads.slice(0, Math.max(1, Math.min(limit, heads.length))).map((title, i) => ({
    id: `sample-${t}-${i}`,
    title,
    publisher: "Sample Wire",
    published: now - i * 6 * 3600000,
    url: null,
    description: `${title}. Synthetic headline for backend development.`,
  }));
}

// ── Live engine (1s timer; same event shapes as the backend) ─────────────────

type LiveState = {
  price: number;
  prevClose: number;
  dayO: number;
  dayH: number;
  dayL: number;
  dayV: number;
  minStart: number;
  minO: number;
  minH: number;
  minL: number;
  minC: number;
  minV: number;
};

const chartSubs = new Map<string, string>();
const watchSubs = new Map<string, Set<string>>();
const chartListeners = new Set<(t: ChartAggregate) => void>();
const secondListeners = new Set<(t: SecondAggregate) => void>();
const tickListeners = new Set<(t: TradeTick) => void>();
const liveStates = new Map<string, LiveState>();
let liveTimer: ReturnType<typeof setInterval> | null = null;
// Seeded (not entropy): ticks differ per run but stay reproducible per
// symbol path, mirroring the backend. One counter across symbols.
let tickN = 0;

function liveStateFor(symbol: string): LiveState {
  const t = norm(symbol);
  const hit = liveStates.get(t);
  if (hit) return hit;
  const daily = sampleDailyHistory(t, 5, true);
  const lastBar = daily[daily.length - 1];
  const prev = daily[daily.length - 2] ?? lastBar;
  const now = Math.floor(Date.now() / 1000);
  const st: LiveState = {
    price: lastBar.close,
    prevClose: prev.close,
    dayO: lastBar.open,
    dayH: lastBar.high,
    dayL: lastBar.low,
    dayV: Math.round(lastBar.volume * 0.4),
    minStart: Math.floor(now / 60) * 60,
    minO: lastBar.close,
    minH: lastBar.close,
    minL: lastBar.close,
    minC: lastBar.close,
    minV: 0,
  };
  liveStates.set(t, st);
  return st;
}

function ensureLiveTimer(): void {
  if (liveTimer !== null || isSampleMarketClosed()) return;
  liveTimer = setInterval(() => {
    const syms = new Set<string>();
    for (const s of chartSubs.values()) syms.add(norm(s));
    for (const set of watchSubs.values()) for (const s of set) syms.add(norm(s));
    if (syms.size === 0) return;
    const now = Math.floor(Date.now() / 1000);
    for (const sym of syms) {
      // Unknown symbols error at lookup time; the live loop only follows
      // listed ones (mirrors the backend — counter untouched).
      if (!isKnownSymbol(bare(sym))) continue;
      tickN += 1;
      const st = liveStateFor(sym);
      // Model + stream keyed by the bare ticker (history uses the same key);
      // reseeded counter stream mirrors the backend (deterministic per run).
      const t = bare(sym);
      const m = modelFor(t);
      const rng = xorshift64(fnv1a(`tick|${t}|${Math.floor(tickN / 7)}`));
      const vol = m.vol * regimeFor(t, utcDateStr(Date.now()).slice(0, 7));
      const prev = st.price;
      st.price = Math.max(0.5, round2(st.price * (1 + gaussian(rng) * vol * 0.06)));
      const tickV = Math.round((m.dayVol / 22500) * (0.3 + rng() * 1.4));
      st.dayH = Math.max(st.dayH, st.price);
      st.dayL = Math.min(st.dayL, st.price);
      st.dayV += tickV;
      if (now >= st.minStart + 60) {
        st.minStart = Math.floor(now / 60) * 60;
        st.minO = prev;
        st.minH = prev;
        st.minL = prev;
        st.minV = 0;
      }
      st.minH = Math.max(st.minH, st.price);
      st.minL = Math.min(st.minL, st.price);
      st.minC = st.price;
      st.minV += tickV;
      const agg: ChartAggregate = {
        symbol: sym,
        time: st.minStart,
        open: st.minO,
        high: st.minH,
        low: st.minL,
        close: st.price,
        volume: st.minV,
        day: {
          time: now,
          open: st.dayO,
          high: st.dayH,
          low: st.dayL,
          close: st.price,
          volume: st.dayV,
        },
      };
      const sec: SecondAggregate = {
        symbol: sym,
        time: now,
        open: prev,
        high: Math.max(prev, st.price),
        low: Math.min(prev, st.price),
        close: st.price,
        volume: tickV,
      };
      const tick: TradeTick = {
        symbol: sym,
        price: st.price,
        change: round2(st.price - st.prevClose),
        changePercent:
          st.prevClose > 0 ? round2(((st.price - st.prevClose) / st.prevClose) * 100) : 0,
        extChangePercent: null,
        volume: st.dayV,
        source: "live",
      };
      for (const fn of chartListeners) fn(agg);
      for (const fn of secondListeners) fn(sec);
      for (const fn of tickListeners) fn(tick);
    }
  }, 1000);
}

export function sampleSetChartSubscription(symbol: string | null, pane = "0"): void {
  if (symbol) chartSubs.set(pane, symbol);
  else chartSubs.delete(pane);
  ensureLiveTimer();
}

export function sampleSetWatchlistSubscription(symbols: string[]): void {
  watchSubs.set("browser", new Set(symbols));
  ensureLiveTimer();
}

export function sampleOnChartAggregate(fn: (t: ChartAggregate) => void): () => void {
  chartListeners.add(fn);
  ensureLiveTimer();
  return () => {
    chartListeners.delete(fn);
  };
}

export function sampleOnSecondAggregate(fn: (t: SecondAggregate) => void): () => void {
  secondListeners.add(fn);
  ensureLiveTimer();
  return () => {
    secondListeners.delete(fn);
  };
}

export function sampleOnTradeTick(fn: (t: TradeTick) => void): () => void {
  tickListeners.add(fn);
  ensureLiveTimer();
  return () => {
    tickListeners.delete(fn);
  };
}

/** Test hook (smokes): inject a trade tick into the sample engine's
 *  listeners, bypassing the live timer. `__`-prefixed like the other
 *  closed-market testing hook above. */
export function __emitSampleTick(t: TradeTick): void {
  for (const fn of tickListeners) fn(t);
}




/** Bare tickers with no "EXCHANGE:" prefix default to this exchange. */
export const defaultExchange = "NSE";

/** Alias → display exchange label. Index venues pass through under their true
 *  codes (matching the broker); unmapped codes fall through unchanged. */
const EXCHANGE_NAMES: Record<string, string> = {
  NSE: "NSE",
  BSE: "BSE",
  NFO: "NFO",
  BFO: "BFO",
  MCX: "MCX",
  NSE_INDEX: "NSE_INDEX",
  BSE_INDEX: "BSE_INDEX",
};

/** Listing country (ISO 3166 alpha-2) by venue — every sample market. */
const EXCHANGE_COUNTRIES: Record<string, string> = {
  NSE: "IN",
  BSE: "IN",
  NSE_INDEX: "IN",
  BSE_INDEX: "IN",
  NFO: "IN",
  MCX: "IN",
  NASDAQ: "US",
  NYSE: "US",
  LSE: "GB",
  XETRA: "DE",
  TSE: "JP",
  HKEX: "HK",
  ASX: "AU",
};

/** Map a vendor exchange code to its display name, falling back to the raw
 *  value when there's no mapping (or it's already a friendly name). */
export function exchangeName(code: string | null | undefined): string {
  if (!code) return "";
  return EXCHANGE_NAMES[code.toUpperCase()] ?? code;
}

/** Country of a listing by its venue code (undefined when not known). */
export function countryOfVenue(code: string | null | undefined): string | undefined {
  if (!code) return undefined;
  return EXCHANGE_COUNTRIES[code.toUpperCase()];
}

/** Exchange prefix of a symbol's full name. Sample codes are already prefixes
 *  (NSE, NSE_INDEX, …), so this is the uppercase code (the default exchange
 *  when unknown). */
export function exchangeCode(code: string | null | undefined): string {
  if (!code) return defaultExchange;
  return code.toUpperCase().replace(/\s+/g, "");
}

// ── Symbol search ────────────────────────────────────────────────────────────

/** "All types" dropdown options — sample `type` codes. null = no filter. */
export const typeFilters: TypeFilter[] = [
  { label: "All types", code: null },
  { label: "Equity", code: "EQ" },
  { label: "Futures", code: "FUT" },
  { label: "Options", code: "OPT" },
  { label: "Index", code: "IX" },
];

/** Sample security type → the short marketType label shown on the chip. */
const TYPE_LABELS: Record<string, string> = {
  EQ: "stock",
  FUT: "futures",
  OPT: "options",
  IX: "index",
};

/** Sample security type → category-tab override. Everything not listed lands
 *  in Stocks via the market mapping below. */
const TYPE_CATEGORY: Partial<Record<string, SymbolCategoryId>> = {
  FUT: "futures",
  OPT: "options",
  IX: "indices",
};

/** Map one raw search result into a dialog row. The vendor half of symbol
 *  search; ../symbol-search applies the generic ranking. */
export function searchResultToRow(r: SymbolSearchResult): SymbolRow {
  const ex = r.primaryExchange || "";
  const marketType = (r.type && TYPE_LABELS[r.type]) || "stock";
  return {
    symbolName: ex ? `${ex}:${r.ticker}` : r.ticker,
    ticker: r.ticker,
    description: r.name ?? "",
    marketType,
    exchange: ex,
    exchangeTooltip: ex || undefined,
    primaryExchange: !!ex,
    category: (r.type && TYPE_CATEGORY[r.type]) || "stocks",
    country: countryOfVenue(ex),
  };
}

// ── Adapter object ───────────────────────────────────────────────────────────
// `name` must match the provider id the backend (or sample feed) reports so
// the frontend can select it via `syncProvider()` in ./index.
import type { FrontendProvider } from "./index";

export const sample: FrontendProvider = {
  name: "sample",
  defaultExchange,
  exchangeName,
  exchangeCode,
  searchResultToRow,
  typeFilters,
};

