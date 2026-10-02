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
 * Sessions come from the broker calendar (`/market/timings` + `/market/holidays`,
 * incl. SPECIAL_SESSION days) — see `openAlgoSymbolSession`; the static SESSION
 * below is only the last-resort fallback.
 */
import type { Candle, SymbolSearchResult, SymbolSession } from "../../bindings";
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

/** App minute-multiplier → OpenAlgo interval. Fallback table used until the
 *  broker's own list arrives (`fetchIntervals`) and when it is unreachable;
 *  verified live against the broker. */
const FALLBACK_MINUTE_INTERVAL: Record<number, string> = {
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

/** Broker interval tokens: "1m" → 1, "2h" → 120, "D" → day. Null when the
 *  token is not a minute multiple (exported for tests). */
export function minuteTokenToMult(token: string): number | null {
  const m = /^(\d+)\s*(m|min|h|hour)?$/i.exec(token.trim());
  if (!m) return null;
  const n = parseInt(m[1], 10);
  if (!Number.isFinite(n) || n < 1) return null;
  const unit = (m[2] ?? "m").toLowerCase();
  return unit.startsWith("h") ? n * 60 : n;
}

export type OaIntervals = {
  months?: unknown;
  weeks?: unknown;
  days?: unknown;
  hours?: unknown;
  minutes?: unknown;
  seconds?: unknown;
};

export type NormalizedIntervals = {
  /** Directly served minute multiples → broker tokens. */
  minutes: Map<number, string>;
  /** Served second multiples (informational: second history stays
   *  unimplemented, so caps keep `seconds: []`). */
  seconds: number[];
  daily: boolean;
};

/** Normalize a broker `/intervals` payload (exported for tests). Unknown
 *  tokens are skipped, never trusted. */
export function normalizeIntervals(data: OaIntervals): NormalizedIntervals {
  const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((t): t is string => typeof t === "string") : []);
  const minutes = new Map<number, string>();
  for (const token of [...strings(data.minutes), ...strings(data.hours)]) {
    const mult = minuteTokenToMult(token);
    if (mult != null && !minutes.has(mult)) minutes.set(mult, token.trim());
  }
  const seconds: number[] = [];
  for (const token of strings(data.seconds)) {
    const m = /^(\d+)\s*s(?:ec)?$/i.exec(token.trim());
    if (m) {
      const n = parseInt(m[1], 10);
      if (Number.isFinite(n) && n >= 1 && !seconds.includes(n)) seconds.push(n);
    }
  }
  seconds.sort((a, b) => a - b);
  return { minutes, seconds, daily: strings(data.days).some((t) => t.trim().toUpperCase() === "D") };
}

const INTERVAL_TTL_MS = 24 * 3600 * 1000;
let intervalCache: { at: number; normalized: NormalizedIntervals } | null = null;

/** Broker intervals, cached a day. Falls back to null when unreachable —
 *  callers then use the `FALLBACK_MINUTE_INTERVAL` table (today's behavior). */
export async function fetchIntervals(): Promise<NormalizedIntervals | null> {
  const hit = intervalCache;
  if (hit && Date.now() - hit.at < INTERVAL_TTL_MS) return hit.normalized;
  try {
    const res = await post<{ status?: string; data?: OaIntervals }>(openAlgoConfig(), "/api/v1/intervals", {});
    if (!res.data || typeof res.data !== "object") throw new Error("OpenAlgo /intervals: bad payload");
    const normalized = normalizeIntervals(res.data);
    intervalCache = { at: Date.now(), normalized };
    return normalized;
  } catch {
    return null;
  }
}

/** Currently known minute-multiplier → broker token (broker list when
 *  fetched, else the fallback table). */
function minuteTable(): Map<number, string> {
  if (intervalCache) return intervalCache.normalized.minutes;
  return new Map(Object.entries(FALLBACK_MINUTE_INTERVAL).map(([k, v]) => [Number(k), v]));
}

export function resolveInterval(kind: "second" | "minute", mult: number): string {
  if (kind === "second") return `${mult}s`;
  const hit = minuteTable().get(mult);
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

// ── Market calendar (timings + holidays → per-exchange sessions) ─────────────
// The broker publishes real trading calendars:
//   POST /api/v1/market/timings  { date } → per-exchange open/close (epoch ms;
//     empty on weekends/full holidays; special-only on special-session days)
//   POST /api/v1/market/holidays { year } → { timezone, data: [{ date,
//     holiday_type, closed_exchanges[], open_exchanges[] }] }
// mapped onto the session grammar (session/subsessions/holidays/corrections;
// corrections win over holidays, so special timings ride as corrections).

export type OaTiming = { exchange: string; start_time: number; end_time: number };
export type OaHoliday = {
  date: string;
  description?: string;
  holiday_type: string;
  closed_exchanges: string[];
  open_exchanges: OaTiming[];
};

/** Standard regular session per exchange (broker docs table): the base spec;
 *  the calendar only overrides it (holidays, special sessions). Bands mirror
 *  the static SESSION (NSE pre-open 15m, post 30m); unknown bands stay 0. */
const STANDARD_SESSION: Record<string, { open: string; close: string; pre: number; post: number }> = {
  NSE: { open: "0915", close: "1530", pre: 15, post: 30 },
  BSE: { open: "0915", close: "1530", pre: 15, post: 30 },
  NFO: { open: "0915", close: "1530", pre: 15, post: 30 },
  BFO: { open: "0915", close: "1530", pre: 15, post: 30 },
  CDS: { open: "0900", close: "1700", pre: 0, post: 0 },
  BCD: { open: "0900", close: "1700", pre: 0, post: 0 },
  MCX: { open: "0900", close: "2330", pre: 0, post: 0 },
};

/** Our venue prefix → broker exchange code for calendar matching (index
 *  venues follow their cash market). Exported for tests. */
export function calendarExchange(prefix: string): string {
  const base = prefix.toUpperCase().replace(/_INDEX$/, "");
  return base || "NSE";
}

const tzFmtCache = new Map<string, Intl.DateTimeFormat>();
function tzDayTime(tz: string): Intl.DateTimeFormat {
  let f = tzFmtCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    tzFmtCache.set(tz, f);
  }
  return f;
}

function tzPart(tz: string, ms: number, type: string): string {
  const parts = tzDayTime(tz).formatToParts(new Date(ms));
  return parts.find((p) => p.type === type)?.value ?? "";
}

/** Epoch ms → "HHMM" in `tz` (exported for tests). */
export function msToHHMM(ms: number, tz: string): string {
  return `${tzPart(tz, ms, "hour").padStart(2, "0")}${tzPart(tz, ms, "minute").padStart(2, "0")}`;
}

/** Epoch ms → "YYYYMMDD" in `tz` (exported for tests). */
export function msToDay(ms: number, tz: string): string {
  return `${tzPart(tz, ms, "year")}${tzPart(tz, ms, "month").padStart(2, "0")}${tzPart(tz, ms, "day").padStart(2, "0")}`;
}

/** "YYYY-MM-DD" → "YYYYMMDD" (exported for tests). */
export function isoToDay(iso: string): string {
  return iso.replace(/-/g, "");
}

export type CalendarBuild = {
  prefix: string;
  timezone: string;
  holidays: OaHoliday[];
  /** Per-date timings for special days whose row carries no per-exchange
   *  timings (SPECIAL_SESSION without open entries). */
  specialTimings: Map<string, OaTiming[]>;
};

/** Build the provider-style session descriptor for one venue prefix from
 *  calendar rows (exported for tests). Rules per (date, exchange):
 *  closed-listed (without an open entry) → holidays; an open entry (or
 *  fetched special timings) → `spec:date` corrections, which win over
 *  holidays; settlement holidays and unmentioned exchanges are ignored
 *  (trading is open). Unknown special timings never close a market. */
export function buildExchangeSession(build: CalendarBuild): SymbolSession {
  const base = calendarExchange(build.prefix);
  const std = STANDARD_SESSION[base] ?? STANDARD_SESSION.NSE;
  const toMin = (hhmm: string): number => parseInt(hhmm.slice(0, 2), 10) * 60 + parseInt(hhmm.slice(2), 10);
  const fmt = (m: number): string =>
    `${String(Math.floor(m / 60)).padStart(2, "0")}${String(((m % 60) + 60) % 60).padStart(2, "0")}`;
  const rth = `${std.open}-${std.close}`;
  const eth =
    std.pre + std.post > 0 ? `${fmt(toMin(std.open) - std.pre)}-${fmt(toMin(std.close) + std.post)}` : rth;
  const closed: string[] = [];
  const special = new Map<string, string[]>(); // spec → dates
  const addSpecial = (spec: string, day: string): void => {
    const list = special.get(spec) ?? [];
    list.push(day);
    special.set(spec, list);
  };
  for (const h of build.holidays) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(h.date ?? "")) continue;
    const day = isoToDay(h.date);
    const closedList = (h.closed_exchanges ?? []).map((e) => String(e).toUpperCase());
    const openList = h.open_exchanges ?? [];
    const openHit = openList.find((o) => String(o.exchange ?? "").toUpperCase() === base);
    if (openHit != null) {
      addSpecial(
        `${msToHHMM(openHit.start_time, build.timezone)}-${msToHHMM(openHit.end_time, build.timezone)}`,
        day,
      );
      continue;
    }
    if (closedList.includes(base)) {
      closed.push(day);
      continue;
    }
    if (String(h.holiday_type ?? "").toUpperCase() === "SPECIAL_SESSION") {
      const timings = build.specialTimings.get(h.date) ?? [];
      const hit = timings.find((t) => String(t.exchange ?? "").toUpperCase() === base);
      if (hit != null) {
        addSpecial(
          `${msToHHMM(hit.start_time, build.timezone)}-${msToHHMM(hit.end_time, build.timezone)}`,
          day,
        );
      }
      continue;
    }
    // SETTLEMENT_HOLIDAY and unmentioned exchanges: trading is open.
  }
  const corrections = [...special.entries()]
    .map(([spec, days]) => `${spec}:${[...new Set(days)].sort().join(",")}`)
    .join(";");
  const subsessions: SymbolSession["subsessions"] = [
    { id: "regular", description: "Regular Trading Hours", session: rth, corrections: "" },
  ];
  if (eth !== rth) {
    subsessions.push(
      { id: "extended", description: "Extended Trading Hours", session: eth, corrections: "" },
      {
        id: "premarket",
        description: "Premarket",
        session: `${fmt(toMin(std.open) - std.pre)}-${std.open}`,
        corrections: "",
      },
      {
        id: "postmarket",
        description: "Postmarket",
        session: `${std.close}-${fmt(toMin(std.close) + std.post)}`,
        corrections: "",
      },
    );
  }
  return {
    timezone: build.timezone,
    session: rth,
    subsessions,
    holidays: [...new Set(closed)].sort().join(","),
    corrections,
    pricescale: 100,
    minmov: 5,
    variableTickSize: "",
  };
}

const HOLIDAY_TTL_MS = 24 * 3600 * 1000;

const holidaysCache = new Map<number, { at: number; timezone: string; rows: OaHoliday[] }>();

/** Holidays (+response timezone) for a year, cached a day. */
export async function fetchHolidays(year: number): Promise<{ timezone: string; rows: OaHoliday[] }> {
  const hit = holidaysCache.get(year);
  if (hit && Date.now() - hit.at < HOLIDAY_TTL_MS) return { timezone: hit.timezone, rows: hit.rows };
  const cfg = openAlgoConfig();
  const res = await post<{ status?: string; timezone?: string; data?: OaHoliday[] }>(cfg, "/api/v1/market/holidays", {
    year,
  });
  const rows = Array.isArray(res.data) ? res.data : [];
  const timezone = typeof res.timezone === "string" && res.timezone ? res.timezone : "Asia/Kolkata";
  holidaysCache.set(year, { at: Date.now(), timezone, rows });
  return { timezone, rows };
}

const timingsCache = new Map<string, { at: number; rows: OaTiming[] }>();

/** Timings for one date (empty on holidays), cached a day. */
export async function fetchTimings(date: string): Promise<OaTiming[]> {
  const hit = timingsCache.get(date);
  if (hit && Date.now() - hit.at < HOLIDAY_TTL_MS) return hit.rows;
  const cfg = openAlgoConfig();
  const res = await post<{ status?: string; data?: OaTiming[] }>(cfg, "/api/v1/market/timings", { date });
  const rows = Array.isArray(res.data) ? res.data : [];
  timingsCache.set(date, { at: Date.now(), rows });
  return rows;
}

const sessionCache = new Map<string, { at: number; session: SymbolSession }>();

/** Per-exchange session descriptor, built from the broker calendar (current
 *  year + next, so December keeps January's specials). When the calendar is
 *  unreachable, serves the last-known calendar rather than the static session
 *  whenever one was ever fetched; throws only when nothing was ever fetched
 *  (callers fall back to the static session). */
export async function openAlgoSymbolSession(symbol: string): Promise<SymbolSession> {
  const { exchange } = splitOpenAlgo(symbol);
  const prefix = exchange.toUpperCase();
  const hit = sessionCache.get(prefix);
  if (hit && Date.now() - hit.at < HOLIDAY_TTL_MS) return hit.session;
  try {
    const session = await buildFreshSession(prefix);
    sessionCache.set(prefix, { at: Date.now(), session });
    return session;
  } catch {
    if (hit) return hit.session;
    throw new Error("OpenAlgo calendar unreachable");
  }
}

/** Fetch years + special-day timings and build one prefix's descriptor. */
async function buildFreshSession(prefix: string): Promise<SymbolSession> {
  const thisYear = new Date().getUTCFullYear();
  const settled = await Promise.allSettled([fetchHolidays(thisYear), fetchHolidays(thisYear + 1)]);
  const ok = settled.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
  if (ok.length === 0) throw new Error("OpenAlgo calendar unreachable");
  const timezone = ok[0].timezone;
  const rows = ok.flatMap((f) => f.rows);
  // Special days whose row carries no timings for this exchange: resolve via
  // the timings endpoint (a year holds a handful at most — bounded).
  const base = calendarExchange(prefix);
  const needDates = [
    ...new Set(
      rows
        .filter((h) => String(h.holiday_type ?? "").toUpperCase() === "SPECIAL_SESSION")
        .map((h) => h.date)
        .filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d ?? "")),
    ),
  ].filter((d) => {
    const h = rows.find((r) => r.date === d);
    const closedList = (h?.closed_exchanges ?? []).map((e) => String(e).toUpperCase());
    const openList = h?.open_exchanges ?? [];
    return (
      !closedList.includes(base) && !openList.some((o) => String(o.exchange ?? "").toUpperCase() === base)
    );
  });
  const specialTimings = new Map<string, OaTiming[]>();
  for (const d of needDates.slice(0, 12)) {
    try {
      specialTimings.set(d, await fetchTimings(d));
    } catch {
      /* unknown timings — regular session stands */
    }
  }
  const session = buildExchangeSession({ prefix, timezone, holidays: rows, specialTimings });
  return session;
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

/** Subscribed full "EXCHANGE:TICKER" names (uppercased, deduped): tick
 *  identity resolves against this set. */
function allSymbols(): Set<string> {
  const out = new Set<string>();
  const add = (s: string) => out.add(s.split(",")[0].trim().toUpperCase());
  for (const s of chartSubs.values()) add(s);
  for (const set of watchSubs.values()) for (const s of set) add(s);
  return out;
}

/** Tick identity for a broker echo: the subscribed name it belongs to, or
 *  null when no subscription matches (stale/foreign echoes are dropped, as
 *  before). Pure: exported for tests. Rules, in order: an explicitly
 *  qualified echo wins when subscribed; the echoed exchange + ticker wins
 *  when subscribed; a lone subscribed listing for the ticker wins (the
 *  common single-venue case with bare echoes); a bare subscription wins for
 *  a bare echo. An ambiguous bare echo (two listings watched) resolves to
 *  nothing rather than attributing one listing's ticks to the other. */
export function resolveTickIdentity(
  echoSymbol: string,
  echoExchange: string,
  subscribed: Iterable<string>,
): string | null {
  const subs = new Set<string>();
  for (const s of subscribed) subs.add(s.split(",")[0].trim().toUpperCase());
  if (subs.size === 0) return null;
  const head = echoSymbol.split(",")[0].trim().toUpperCase();
  if (head.includes(":") && subs.has(head)) return head;
  const bare = splitOpenAlgo(head).ticker;
  if (!bare) return null;
  const ex = (echoExchange ?? "").trim().toUpperCase();
  if (ex && subs.has(`${ex}:${bare}`)) return `${ex}:${bare}`;
  const singles = [...subs].filter((s) => splitOpenAlgo(s).ticker === bare);
  if (singles.length === 1) return singles[0];
  if (subs.has(bare)) return bare;
  return null;
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

function onQuoteTick(symbol: string, exchange: string, q: OaQuote): void {
  // Full-name ingress: the tick is attributed to its subscribed listing
  // (see resolveTickIdentity); buckets, baselines and emits all key on the
  // resolved name, so two listings of one ticker never share a stream.
  const sym = resolveTickIdentity(symbol, exchange, allSymbols());
  if (!sym) return;
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
  mintick: 0.05,
};

const OPENALGO_SEEDS = {
  defaultSymbol: "NSE:RELIANCE",
  starterTabs: [
    { symbol: "NSE:RELIANCE", interval: "1D" },
    { symbol: "NSE:INFY", interval: "60" },
    { symbol: "NSE:SBIN", interval: "240" },
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
    await fetchIntervals();
    if (intervalMin === 240) {
      // No 4h interval: aggregate client-side from 2h.
      if (!minuteTable().has(120)) {
        throw new Error("OpenAlgo: 240m needs the broker 2h interval, which is not served");
      }
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
    await fetchIntervals();
    // Reuse the chunked fetcher over explicit calendar bounds.
    const oa = mult === 240 ? "2h" : resolveInterval("minute", mult);
    if (mult === 240 && !minuteTable().has(120)) {
      throw new Error("OpenAlgo: 240m needs the broker 2h interval, which is not served");
    }
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

  // Sessions from the broker calendar (timings + holidays, incl. special
  // sessions) — preferred over the backend command by the feed.
  symbolSessions: (symbol) => openAlgoSymbolSession(symbol),

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

  capabilities: async () => {
    // Served sizes come from the broker's own interval list when reachable;
    // otherwise the verified fallback table (today's behavior). 240m is
    // synthesized client-side from 2h, so it is advertised only with it.
    const fetched = await fetchIntervals();
    const direct = fetched
      ? [...fetched.minutes.keys()].sort((a, b) => a - b)
      : [1, 3, 5, 10, 15, 30, 60, 120, 240];
    const minutes = direct.includes(120) && !direct.includes(240) ? [...direct, 240] : direct;
    return {
      name: "openalgo",
      resolutions: {
        seconds: [] as number[],
        minutes,
        daily: fetched ? fetched.daily : true,
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
    };
  },

  watchCapabilities: () => Promise.resolve(() => {}),
  session: () => SESSION,
  seeds: () => OPENALGO_SEEDS,
  historyLimits: () => ({ second: 0, minute: 30, day: 365 }),
};
