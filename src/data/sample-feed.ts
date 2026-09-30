/*
 * Sample feed — deterministic, zero-backend market data for browser dev.
 *
 * Raw generators + live engine only (no app wiring): `sources/sample.ts`
 * exposes them behind the `DataSource` socket, and `sources/index.ts`
 * selects it outside the Tauri shell — so the full UI (charts, watchlist,
 * alerts, search, events) boots in a plain browser with `npm run dev`.
 *
 * Coverage (mirrors the `DataProvider` trait — see
 * `docs/new-provider-requirements.md`):
 *   history: daily / minute / second / second-tail / scroll-back pagers
 *   live:    1s timer emitting ChartAggregate / SecondAggregate / TradeTick
 *   reference: ticker info / snapshot / symbol search
 *   events:  dividends (quarterly), splits AND bonus issues (as SplitEvent),
 *            synthetic news headlines
 *   meta:    static capabilities + floor, IST session, NSE calendar, seeds
 *
 * Deterministic history (seeded by symbol+date) + non-deterministic live ticks.
 * Any ticker works; a small static universe backs search and reference panels.
 */

import type {
  Candle,
  DividendEvent,
  SplitEvent,
  SymbolSearchResult,
} from "../bindings";
import type { NewsItem, Snapshot, TickerInfo } from "./datafeed-rest";
import type {
  ChartAggregate,
  SecondAggregate,
  TradeTick,
} from "./datafeed-live";

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

/**
 * Error-path testing hook: any `BAD_*` symbol throws from the bar/snapshot/
 * info entry points (decorative paths return `[]` per contract), exercising
 * chart error states and search fallback. Real providers: replace with the
 * unknown-symbol error from the vendor API.
 */
const ERROR_TICKER_RE = /^BAD_/;

function assertSampleSymbol(symbol: string): void {
  if (ERROR_TICKER_RE.test(bare(symbol))) {
    throw new Error(`no sample data for ${bare(symbol)}`);
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
  return addDays(etDateStr(Date.now()), -SAMPLE_FLOOR_DAYS[family]);
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

// ── Seeded RNG ───────────────────────────────────────────────────────────────

function hashStr(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussian(rng: () => number): number {
  let u = 0;
  let v = 0;
  while (u === 0) u = rng();
  while (v === 0) v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

const norm = (s: string): string => s.trim().toUpperCase();
const bare = (s: string): string => {
  const h = norm(s).split(",")[0].trim();
  return h.includes(":") ? (h.split(":").pop() as string) : h;
};

// ── ET time helpers (session math mirrors datafeed.ts conventions) ──────────

const RTH_TZ = "America/New_York";

// ── IST time helpers + NSE calendar ──────────────────────────────────────────
// Sample mode models the NSE equity session (09:15–15:30 IST); trading-day
// enumeration, holiday skips and intraday buckets all run on IST wall time.

const IST_TZ = "Asia/Kolkata";
/** NSE regular session: 09:15–15:30 IST, in minutes since IST midnight. */
export const IST_OPEN_MIN = 9 * 60 + 15; // 555
export const IST_CLOSE_MIN = 15 * 60 + 30; // 930

const istPartsFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: IST_TZ,
  hour12: false,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  weekday: "short",
});

function istParts(utcMs: number): { y: number; m: number; d: number; hh: number; mm: number; wd: string } {
  const p: Record<string, string> = {};
  for (const x of istPartsFmt.formatToParts(new Date(utcMs))) {
    if (x.type !== "literal") p[x.type] = x.value;
  }
  return {
    y: +p.year,
    m: +p.month,
    d: +p.day,
    hh: +p.hour === 24 ? 0 : +p.hour,
    mm: +p.minute,
    wd: p.weekday,
  };
}

/** IST calendar date string for a UTC instant. */
function istDateStr(utcMs: number): string {
  const p = istParts(utcMs);
  return `${p.y}-${String(p.m).padStart(2, "0")}-${String(p.d).padStart(2, "0")}`;
}

/** UTC millis of an IST wall-clock time. Single probe: UTC = wall-as-UTC minus
 *  the zone offset measured at that instant (exact for IST, which has no DST).
 *  NOTE: the classic two-probe loop overshoots by exactly one offset — the
 *  correction at the true answer is still -offset, so a second pass breaks a
 *  converged result. */
function istWallToUtc(y: number, m: number, d: number, hh: number, mm: number): number {
  const wallAsUtc = Date.UTC(y, m - 1, d, hh, mm);
  const p = istParts(wallAsUtc);
  const asUtc = Date.UTC(p.y, p.m - 1, p.d, p.hh, p.mm);
  return wallAsUtc - (asUtc - wallAsUtc);
}

/**
 * NSE trading holidays (full-day closures), YYYY-MM-DD.
 *
 * TEMPLATE NOTE (real providers: replace with the exchange calendar fetch):
 * compiled from NSE circulars for 2024–2026 — VERIFY YEARLY against
 * nseindia.com (special closures like elections are announced ad-hoc).
 * Weekends need no entry (skipped separately).
 */
const NSE_HOLIDAYS_2024_2026: string[] = [
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

const NSE_HOLIDAYS = new Set(NSE_HOLIDAYS_2024_2026);

/** True for an NSE trading day: weekday and not a listed holiday. */
export function isSampleTradingDay(dateStr: string): boolean {
  return !isWeekend(dateStr) && !NSE_HOLIDAYS.has(dateStr);
}

const etPartsFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: RTH_TZ,
  hour12: false,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  weekday: "short",
});

function etParts(utcMs: number): { y: number; m: number; d: number; hh: number; mm: number; wd: string } {
  const p: Record<string, string> = {};
  for (const x of etPartsFmt.formatToParts(new Date(utcMs))) {
    if (x.type !== "literal") p[x.type] = x.value;
  }
  return {
    y: +p.year,
    m: +p.month,
    d: +p.day,
    hh: +p.hour === 24 ? 0 : +p.hour,
    mm: +p.minute,
    wd: p.weekday,
  };
}

/** ET calendar date string for a UTC instant (used for floor/snapshot keys). */
function etDateStr(utcMs: number): string {
  const p = etParts(utcMs);
  return `${p.y}-${String(p.m).padStart(2, "0")}-${String(p.d).padStart(2, "0")}`;
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

/** Last `n` NSE trading days (weekends + NSE holidays skipped) ending at `end`
 *  (default today), as IST calendar dates. */
function tradingDaysBack(n: number, end?: string): string[] {
  const out: string[] = [];
  let cur = end ?? istDateStr(Date.now());
  while (out.length < Math.max(1, n)) {
    if (isSampleTradingDay(cur)) out.unshift(cur);
    cur = addDays(cur, -1);
  }
  return out;
}

/** NSE trading days in [from, to] (inclusive IST dates). */
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
  const h = hashStr(`model|${ticker}`);
  const rng = mulberry32(h);
  void rng;
  return {
    base: 25 + (h % 297500) / 100,
    vol: 0.008 + ((h >>> 8) % 200) / 10000,
    dayVol: 500000 + ((h >>> 16) % 40000000),
  };
}

// ── Corporate actions (splits AND bonus issues share the SplitEvent shape) ──

export function sampleSplits(symbol: string): SplitEvent[] {
  if (ERROR_TICKER_RE.test(bare(symbol))) return [];
  const t = bare(symbol);
  const h = hashStr(`splits|${t}`);
  const out: SplitEvent[] = [];
  const today = istDateStr(Date.now());
  const stamp = (dateStr: string): number => {
    const [y, m, d] = parseDate(dateStr);
    return Date.UTC(y, m - 1, d, 12) / 1000;
  };
  const kind = h % 7;
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
  if (ERROR_TICKER_RE.test(bare(symbol))) return [];
  const t = bare(symbol);
  const h = hashStr(`div|${t}`);
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
    const amt = 0.2 + ((h >> (i % 24)) % 230) / 100;
    out.push({ date: Date.UTC(y, m, 15, 12) / 1000, amount: Math.round(amt * 100) / 100 });
  }
  return out.sort((a, b) => b.date - a.date);
}

// ── Daily series ─────────────────────────────────────────────────────────────

const round2 = (n: number): number => Math.round(n * 100) / 100;

function genDailyUnadjusted(ticker: string, dates: string[]): Candle[] {
  const t = bare(ticker);
  const m = modelFor(t);
  const rng = mulberry32(hashStr(`daily|${t}|${dates[0] ?? "x"}|${dates.length}`));
  const closes: number[] = [];
  let px = m.base * 0.55;
  for (let i = 0; i < dates.length; i++) {
    px = Math.max(1, px * (1 + gaussian(rng) * m.vol + 0.0004));
    closes.push(px);
  }
  const scale = m.base / closes[closes.length - 1];
  return dates.map((ds, i) => {
    const [y, mo, d] = parseDate(ds);
    const prev = i === 0 ? closes[0] / (1 + gaussian(rng) * m.vol * 0.3) : closes[i - 1];
    const open = prev * scale;
    const close = closes[i] * scale;
    const spread = Math.abs(gaussian(rng)) * m.vol * 0.6 * close;
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
  const end = istDateStr((beforeSec - 1) * 1000);
  const dates = clampDates(tradingDaysBack(spanDays, end), "day");
  if (dates.length === 0) return [];
  const bars = applySplits(genDailyUnadjusted(symbol, dates), sampleSplits(symbol), adjusted);
  return bars.filter((b) => b.time < beforeSec);
}

// ── Intraday (minute) series ─────────────────────────────────────────────────

function genMinutesUnadjusted(ticker: string, dates: string[], multMin: number): Candle[] {
  const t = bare(ticker);
  const m = modelFor(t);
  // One extra leading day anchors the first session open.
  const ext = tradingDaysBack(1, addDays(dates[0], -1));
  const daily = genDailyUnadjusted(t, [...ext, ...dates]);
  const closeByDate = new Map<string, number>();
  [...ext, ...dates].forEach((ds, i) => closeByDate.set(ds, daily[i].close));
  const out: Candle[] = [];
  // NSE regular session 09:15–15:30 IST (375 minutes).
  const perDay = Math.floor(375 / multMin);
  dates.forEach((ds) => {
    const [y, mo, d] = parseDate(ds);
    const anchor = closeByDate.get(addDays(ds, -1)) ?? closeByDate.get(ds) ?? m.base;
    const rng = mulberry32(hashStr(`min|${t}|${ds}|${multMin}`));
    let px = anchor * (1 + gaussian(rng) * m.vol * 0.15);
    const vBase = m.dayVol / 375;
    for (let i = 0; i < perDay; i++) {
      const startMin = IST_OPEN_MIN + i * multMin;
      const hh = Math.floor(startMin / 60);
      const mm = startMin % 60;
      const time = istWallToUtc(y, mo, d, hh, mm) / 1000;
      const open = px;
      const steps = Math.max(1, multMin);
      let high = open;
      let low = open;
      for (let s = 0; s < steps; s++) {
        px = Math.max(0.5, px * (1 + gaussian(rng) * m.vol * 0.09));
        high = Math.max(high, px);
        low = Math.min(low, px);
      }
      // U-shaped volume: heavier near 09:15 open and 15:30 close IST.
      const tod = hh * 60 + mm;
      const shape = 1 + 1.6 * Math.exp(-Math.pow(tod - IST_OPEN_MIN, 2) / 4000) + 1.2 * Math.exp(-Math.pow(tod - IST_CLOSE_MIN, 2) / 6000);
      out.push({
        time,
        open: round2(open),
        high: round2(high),
        low: round2(low),
        close: round2(px),
        volume: Math.round(vBase * multMin * shape * (0.5 + rng())),
      });
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
    genMinutesUnadjusted(symbol, dates, Math.max(1, intervalMin)),
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
  const end = istDateStr((beforeSec - 1) * 1000);
  const dates = clampDates(tradingDaysBack(spanDays, end), timespan === "second" ? "second" : "minute");
  if (dates.length === 0) return [];
  const bars =
    timespan === "second"
      ? genSecondsUnadjusted(symbol, dates, mult)
      : genMinutesUnadjusted(symbol, dates, mult);
  return applySplits(bars, sampleSplits(symbol), adjusted).filter((b) => b.time < beforeSec);
}

// ── Second series (RTH 09:30–16:00 ET) ───────────────────────────────────────

function genSecondsUnadjusted(ticker: string, dates: string[], multSec: number): Candle[] {
  const t = bare(ticker);
  const m = modelFor(t);
  const ext = tradingDaysBack(1, addDays(dates[0], -1));
  const daily = genDailyUnadjusted(t, [...ext, ...dates]);
  const byDate = new Map<string, { o: number; c: number }>();
  [...ext, ...dates].forEach((ds, i) =>
    byDate.set(ds, { o: i === 0 ? daily[0].open : daily[i - 1].close, c: daily[i].close }),
  );
  const out: Candle[] = [];
  // NSE regular session 09:15–15:30 IST (22,500 seconds).
  const perDay = Math.floor(22500 / multSec);
  for (const ds of dates) {
    const [y, mo, d] = parseDate(ds);
    const ref = byDate.get(ds) ?? { o: m.base, c: m.base };
    const rng = mulberry32(hashStr(`sec|${t}|${ds}|${multSec}`));
    // Walk open→close so the day shape stays plausible.
    const drift = (ref.c - ref.o) / perDay;
    let px = ref.o;
    const sessionOpen = istWallToUtc(y, mo, d, 9, 15) / 1000;
    for (let i = 0; i < perDay; i++) {
      const time = sessionOpen + i * multSec;
      const open = px;
      let high = open;
      let low = open;
      const steps = Math.min(multSec, 5);
      for (let s = 0; s < steps; s++) {
        px = Math.max(0.5, px + drift / steps + gaussian(rng) * m.vol * 0.02 * px);
        high = Math.max(high, px);
        low = Math.min(low, px);
      }
      out.push({
        time,
        open: round2(open),
        high: round2(high),
        low: round2(low),
        close: round2(px),
        volume: Math.round((m.dayVol / 22500) * multSec * (0.4 + rng() * 1.2)),
      });
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
  return applySplits(genSecondsUnadjusted(symbol, dates, mult), sampleSplits(symbol), adjusted);
}

export function sampleSecondHistoryTail(
  symbol: string,
  mult: number,
  sinceSec: number,
  adjusted: boolean,
): Candle[] {
  assertSampleSymbol(symbol);
  const today = istDateStr(Date.now());
  const dates = tradingDaysInRange(addDays(today, -1), today);
  const bars = applySplits(genSecondsUnadjusted(symbol, dates, mult), sampleSplits(symbol), adjusted);
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
  { ticker: "SENSEX", name: "BSE SENSEX Index", exchange: "BSE", type: "IX", sector: "Index" },
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
  if (!rows.some((r) => r.ticker === q)) {
    rows.unshift({
      ticker: q,
      name: `${q} (sample)`,
      market: "stocks",
      locale: "us",
      primaryExchange: "NSE",
      type: "EQ",
    });
  }
  return rows.slice(0, 50);
}

export function sampleTickerInfo(symbol: string): TickerInfo {
  assertSampleSymbol(symbol);
  const t = bare(symbol);
  const m = modelFor(t);
  const known = UNIVERSE.find((r) => r.ticker === t);
  const exchange = known?.exchange ?? "NSE";
  return {
    ticker: t,
    name: known?.name ?? `${t} (sample)`,
    exchange,
    industry: known?.sector ?? "Sample",
    sector: known?.sector ?? "Sample",
    currency: exchange === "NSE" || exchange === "BSE" ? "INR" : "USD",
    description: `${known?.name ?? t} — deterministic sample instrument for browser development.`,
    homepageUrl: null,
    totalEmployees: 10000 + (hashStr(`emp|${t}`) % 150000),
    marketCap: Math.round(m.base * (100000000 + (hashStr(`mc|${t}`) % 900000000))),
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
  if (isSampleMarketClosed()) {
    // Prior completed session as the quote (regular-session close, not a tick).
    const change = round2(lastBar.close - prev.close);
    return {
      ticker: t,
      last: lastBar.close,
      change,
      changePercent: prev.close > 0 ? round2((change / prev.close) * 100) : 0,
      dayHigh: lastBar.high,
      dayLow: lastBar.low,
      dayVolume: lastBar.volume,
      extChangePercent: null,
      source: "prev",
      updatedNs: lastBar.time * 1e9,
    };
  }
  // Intraday drift keyed by hour so refetches move the quote (sample market is
  // always "open").
  const h = new Date().getUTCHours();
  const drift = ((hashStr(`snap|${t}|${etDateStr(Date.now())}|${h}`) % 200) - 100) / 10000;
  const last = round2(lastBar.close * (1 + drift));
  const change = round2(last - prev.close);
  return {
    ticker: t,
    last,
    change,
    changePercent: prev.close > 0 ? round2((change / prev.close) * 100) : 0,
    dayHigh: Math.max(lastBar.high, last),
    dayLow: Math.min(lastBar.low, last),
    dayVolume: Math.round(lastBar.volume * (0.35 + ((h % 12) / 12) * 0.65)),
    extChangePercent: null,
    source: "live",
    updatedNs: Date.now() * 1e6,
  };
}

export function sampleLatestNews(symbol: string, limit: number): NewsItem[] {
  if (ERROR_TICKER_RE.test(bare(symbol))) return [];
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
    description: `${title}. Synthetic headline for frontend development.`,
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
      const st = liveStateFor(sym);
      const m = modelFor(sym);
      const prev = st.price;
      st.price = Math.max(0.5, round2(st.price * (1 + gaussian(Math.random) * m.vol * 0.06)));
      const tickV = Math.round((m.dayVol / 22500) * (0.3 + Math.random() * 1.4));
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
