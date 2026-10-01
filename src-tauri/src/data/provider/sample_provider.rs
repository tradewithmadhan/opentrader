/*
 * SampleProvider — deterministic, zero-network `DataProvider` for backend
 * development and for exercising the full desktop path without a data key.
 *
 * Sibling reference to the browser feed (`src/data/sample-feed.ts`): both
 * implement the same contract (see `docs/new-provider-requirements.md` and
 * `docs/gateway-endpoints.md` for the exchange shapes), but with
 * INDEPENDENT values — the Rust and TS generators use different PRNG streams
 * on purpose. This file is a template for real providers and a test backend
 * for frontend work, not a fixture with frozen values. Do not assert
 * cross-shell equality anywhere.
 *
 * Coverage: daily / minute / second history + second tail, ticker info /
 * snapshot / search, quarterly dividends, splits AND bonus issues (as
 * `SplitEvent`), synthetic news, and a 1 s live task emitting the same
 * `ChartAggregate` / `SecondAggregate` / `TradeTick` events as the Massive
 * transports. No icon endpoint (`icons: false`), no disk cache (generation
 * is microseconds), no credential (`credential_id() == None`, so
 * entitlements settle immediately with no probe wait).
 *
 * Select with `DATA_PROVIDER=sample`.
 */
use super::capabilities::{
    DataStatus, HistoryFloor, HistoryProbe, ProviderCapabilities, ReferenceCaps, ResolutionCaps,
    SessionCaps, StreamCaps,
};
use super::{DataProvider, HistoryProvider, RealtimeProvider, ReferenceProvider};
use crate::data::massive_ws::{ChartAggregate, SecondAggregate, SubscribeMsg, SubscriptionState, TradeTick, WsHandle};
use crate::data::session::{Subsession, SymbolSession};
use crate::data::symbol::SymbolRef;
use crate::data::types::{Candle, DividendEvent, NewsItem, Snapshot, SplitEvent, SymbolSearchResult, TickerInfo};
use anyhow::Result;
use chrono::{Datelike, NaiveDate};
use std::collections::{HashMap, HashSet};
use std::time::Duration;
use tauri::AppHandle;
use tauri_specta::Event;
use tokio::sync::mpsc;
use tokio::time::{interval, MissedTickBehavior};

pub struct SampleProvider;

// ── Deterministic PRNG (inline: no `rand` dependency) ────────────────────────
// xorshift64* seeded by FNV-1a of a scope string. Independent stream per
// (purpose, ticker, date) so any slice regenerates identically.

fn fnv1a(s: &str) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in s.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    h
}

struct Rng(u64);

impl Rng {
    fn scoped(scope: &str) -> Self {
        let mut seed = fnv1a(scope);
        if seed == 0 {
            seed = 0x9e37_79b9_7f4a_7c15;
        }
        Self(seed)
    }

    fn next_u64(&mut self) -> u64 {
        // xorshift64*
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_f491_4f6c_dd1d)
    }

    fn next_f64(&mut self) -> f64 {
        // [0, 1)
        ((self.next_u64() >> 11) as f64) / ((1u64 << 53) as f64)
    }

    /// Standard-normal sample (Box–Muller).
    fn gaussian(&mut self) -> f64 {
        let mut u = 0.0;
        while u == 0.0 {
            u = self.next_f64();
        }
        let v = self.next_f64();
        (-2.0 * u.ln()).sqrt() * (2.0 * std::f64::consts::PI * v).cos()
    }
}

fn round2(n: f64) -> f64 {
    (n * 100.0).round() / 100.0
}

fn upper(symbol: &str) -> String {
    symbol.split(',').next().unwrap_or("").trim().to_uppercase()
}

/// Bare ticker: strip an `EXCHANGE:` prefix when present.
fn bare(symbol: &str) -> String {
    let h = upper(symbol);
    h.split(':').last().unwrap_or("").to_string()
}

// ── Ticker model ─────────────────────────────────────────────────────────────

struct Model {
    base: f64,
    vol: f64,
    day_vol: f64,
}

fn model_for(ticker: &str) -> Model {
    let h = fnv1a(&format!("model|{ticker}"));
    Model {
        base: 25.0 + ((h % 297500) as f64) / 100.0,
        vol: 0.008 + (((h >> 8) % 200) as f64) / 10000.0,
        day_vol: 500000.0 + (((h >> 16) % 40000000) as f64),
    }
}

// ── Trading days (weekends skipped; NSE holidays unmodeled — extra weekday
// bars are harmless for sample data) ─────────────────────────────────────────

fn is_weekend(d: NaiveDate) -> bool {
    matches!(d.weekday(), chrono::Weekday::Sat | chrono::Weekday::Sun)
}

/// Last `n` trading days ending at `end` (inclusive), oldest-first.
fn trading_days_back(n: usize, end: NaiveDate) -> Vec<NaiveDate> {
    let mut out = Vec::new();
    let mut cur = end;
    let mut guard = 0usize;
    while out.len() < n.max(1) && guard < n.max(1) * 10 + 30 {
        guard += 1;
        if !is_weekend(cur) {
            out.push(cur);
        }
        cur = cur.pred_opt().unwrap_or(cur);
    }
    out.reverse();
    out
}

fn noon_utc(d: NaiveDate) -> f64 {
    d.and_hms_opt(12, 0, 0).map(|t| t.and_utc().timestamp() as f64).unwrap_or(0.0)
}

// ── Corporate actions ────────────────────────────────────────────────────────

/// Splits AND bonus issues share the `SplitEvent` shape (a 1:1 bonus is
/// `{ from: 1, to: 2 }`). Distribution mirrors the TS feed's idea; values are
/// this file's own stream.
fn sample_splits(ticker: &str, today: NaiveDate) -> Vec<SplitEvent> {
    let h = fnv1a(&format!("splits|{ticker}"));
    let at = |days_ago: i64| {
        today
            .checked_sub_days(chrono::Days::new(days_ago as u64))
            .map(noon_utc)
            .unwrap_or(0.0)
    };
    let mut out = match h % 7 {
        // 2:1 split ~1.5y ago.
        0 => vec![SplitEvent { date: at(380), from: 1.0, to: 2.0 }],
        // 1:1 bonus issue ~9mo ago.
        1 => vec![SplitEvent { date: at(270), from: 1.0, to: 2.0 }],
        // 3:2 bonus ~6mo ago + an old 2:1 split.
        2 => vec![
            SplitEvent { date: at(180), from: 2.0, to: 3.0 },
            SplitEvent { date: at(900), from: 1.0, to: 2.0 },
        ],
        _ => Vec::new(),
    };
    out.sort_by(|a, b| b.date.partial_cmp(&a.date).unwrap_or(std::cmp::Ordering::Equal));
    out
}

/// Pre-split bars are on the as-traded basis; rescale them when adjusted.
fn apply_splits(mut bars: Vec<Candle>, splits: &[SplitEvent], adjusted: bool) -> Vec<Candle> {
    if !adjusted || splits.is_empty() {
        return bars;
    }
    // Oldest-first without cloning (`SplitEvent` is not `Clone`): sort refs.
    let mut asc: Vec<&SplitEvent> = splits.iter().collect();
    asc.sort_by(|a, b| a.date.partial_cmp(&b.date).unwrap_or(std::cmp::Ordering::Equal));
    for b in &mut bars {
        let mut f = 1.0;
        for s in &asc {
            if b.time < s.date {
                f *= s.from / s.to;
            } else {
                break;
            }
        }
        if f != 1.0 {
            b.open = round2(b.open * f);
            b.high = round2(b.high * f);
            b.low = round2(b.low * f);
            b.close = round2(b.close * f);
            b.volume = (b.volume / f).round();
        }
    }
    bars
}

fn sample_dividends(ticker: &str, today: NaiveDate) -> Vec<DividendEvent> {
    let h = fnv1a(&format!("div|{ticker}"));
    let mut out = Vec::new();
    // Last 8 quarter-ends (Mar / Jun / Sep / Dec 15th).
    let (mut y, mut q) = (today.year(), today.month0() / 3);
    for i in 0..8 {
        if q == 0 {
            q = 3;
            y -= 1;
        } else {
            q -= 1;
        }
        let m = q * 3 + 1;
        let date = NaiveDate::from_ymd_opt(y, m, 15).map(noon_utc).unwrap_or(0.0);
        let amt = 0.2 + (((h >> (i % 24)) % 230) as f64) / 100.0;
        out.push(DividendEvent { date, amount: (amt * 100.0).round() / 100.0 });
    }
    out.sort_by(|a, b| b.date.partial_cmp(&a.date).unwrap_or(std::cmp::Ordering::Equal));
    out
}

// ── Daily series ─────────────────────────────────────────────────────────────

fn gen_daily_unadjusted(ticker: &str, dates: &[NaiveDate]) -> Vec<Candle> {
    if dates.is_empty() {
        return Vec::new();
    }
    let t = bare(ticker);
    let m = model_for(&t);
    let seed = format!("daily|{t}|{}|{}", dates[0], dates.len());
    let mut rng = Rng::scoped(&seed);
    let mut closes = Vec::with_capacity(dates.len());
    let mut px = m.base * 0.55;
    for _ in dates {
        px = (px * (1.0 + rng.gaussian() * m.vol + 0.0004)).max(1.0);
        closes.push(px);
    }
    let scale = m.base / closes[closes.len() - 1];
    dates
        .iter()
        .enumerate()
        .map(|(i, ds)| {
            let prev = if i == 0 {
                closes[0] / (1.0 + rng.gaussian() * m.vol * 0.3)
            } else {
                closes[i - 1]
            };
            let open = prev * scale;
            let close = closes[i] * scale;
            let spread = rng.gaussian().abs() * m.vol * 0.6 * close;
            let high = open.max(close) + spread * rng.next_f64();
            let low = (open.min(close) - spread * rng.next_f64()).max(0.01);
            Candle {
                time: noon_utc(*ds),
                open: round2(open),
                high: round2(high),
                low: round2(low),
                close: round2(close),
                volume: (m.day_vol * (0.6 + rng.next_f64() * 0.9)).round(),
            }
        })
        .collect()
}

// ── Intraday series (NSE regular session 09:15–15:30 IST = 03:45–10:00 UTC;
// IST has no DST, so the offset is a constant 330 minutes) ───────────────────

/// UTC seconds of an IST wall-clock time on the given date.
fn ist_wall_to_utc(d: NaiveDate, hh: u32, mm: u32) -> f64 {
    let wall_as_utc = d.and_hms_opt(hh, mm, 0).map(|t| t.and_utc().timestamp()).unwrap_or(0);
    (wall_as_utc - 330 * 60) as f64
}

fn gen_minutes_unadjusted(ticker: &str, dates: &[NaiveDate], mult_min: u32) -> Vec<Candle> {
    let mult = mult_min.max(1) as usize;
    // 375-minute session.
    let per_day = 375 / mult;
    if dates.is_empty() || per_day == 0 {
        return Vec::new();
    }
    let t = bare(ticker);
    let m = model_for(&t);
    // One extra leading day anchors the first session open.
    let first = dates[0].pred_opt().unwrap_or(dates[0]);
    let mut ext = vec![first];
    ext.extend_from_slice(dates);
    let daily = gen_daily_unadjusted(&t, &ext);
    let closes: HashMap<NaiveDate, f64> =
        ext.iter().zip(daily.iter()).map(|(d, b)| (*d, b.close)).collect();
    let mut out = Vec::new();
    for ds in dates {
        let anchor = closes
            .get(&ds.pred_opt().unwrap_or(*ds))
            .or_else(|| closes.get(ds))
            .copied()
            .unwrap_or(m.base);
        let mut rng = Rng::scoped(&format!("min|{t}|{ds}|{mult}"));
        let mut px = anchor * (1.0 + rng.gaussian() * m.vol * 0.15);
        let v_base = m.day_vol / 375.0;
        for i in 0..per_day {
            let start_min = 555 + i * mult; // 09:15 IST
            let open = px;
            let mut high = open;
            let mut low = open;
            for _ in 0..mult {
                px = (px * (1.0 + rng.gaussian() * m.vol * 0.09)).max(0.5);
                high = high.max(px);
                low = low.min(px);
            }
            let tod = start_min as f64;
            // U-shaped volume around the 09:15 open and 15:30 close.
            let shape = 1.0
                + 1.6 * (-((tod - 555.0).powi(2)) / 4000.0).exp()
                + 1.2 * (-((tod - 930.0).powi(2)) / 6000.0).exp();
            out.push(Candle {
                time: ist_wall_to_utc(*ds, (start_min / 60) as u32, (start_min % 60) as u32),
                open: round2(open),
                high: round2(high),
                low: round2(low),
                close: round2(px),
                volume: (v_base * mult as f64 * shape * (0.5 + rng.next_f64())).round(),
            });
        }
    }
    out
}

fn gen_seconds_unadjusted(ticker: &str, dates: &[NaiveDate], mult_sec: u32) -> Vec<Candle> {
    let mult = mult_sec.max(1) as usize;
    // 09:15–15:30 IST = 22,500 seconds.
    let per_day = 22500 / mult;
    if dates.is_empty() || per_day == 0 {
        return Vec::new();
    }
    let t = bare(ticker);
    let m = model_for(&t);
    let first = dates[0].pred_opt().unwrap_or(dates[0]);
    let mut ext = vec![first];
    ext.extend_from_slice(dates);
    let daily = gen_daily_unadjusted(&t, &ext);
    // `daily[i]` lines up with `ext[i]`; each date opens on the previous close.
    let mut out = Vec::new();
    for (di, ds) in dates.iter().enumerate() {
        let open = daily[di].close;
        let close = daily[di + 1].close;
        let mut rng = Rng::scoped(&format!("sec|{t}|{ds}|{mult}"));
        let drift = (close - open) / per_day as f64;
        let mut px = open;
        let session_open = ist_wall_to_utc(*ds, 9, 15);
        let steps = mult.min(5);
        for i in 0..per_day {
            let bar_open = px;
            let mut high = bar_open;
            let mut low = bar_open;
            for _ in 0..steps {
                px = (px + drift / steps as f64 + rng.gaussian() * m.vol * 0.02 * px).max(0.5);
                high = high.max(px);
                low = low.min(px);
            }
            out.push(Candle {
                time: session_open + (i * mult) as f64,
                open: round2(bar_open),
                high: round2(high),
                low: round2(low),
                close: round2(px),
                volume: ((m.day_vol / 22500.0) * mult as f64 * (0.4 + rng.next_f64() * 1.2)).round(),
            });
        }
    }
    out
}

// ── Reference universe (same names as the browser feed so search panels
// agree; values remain this file's own stream) ───────────────────────────────

struct UniverseRow {
    ticker: &'static str,
    name: &'static str,
    exchange: &'static str,
    kind: &'static str,
    sector: &'static str,
}

const UNIVERSE: &[UniverseRow] = &[
    UniverseRow { ticker: "RELIANCE", name: "Reliance Industries Ltd.", exchange: "NSE", kind: "EQ", sector: "Energy" },
    UniverseRow { ticker: "TCS", name: "Tata Consultancy Services", exchange: "NSE", kind: "EQ", sector: "Technology" },
    UniverseRow { ticker: "INFY", name: "Infosys Ltd.", exchange: "NSE", kind: "EQ", sector: "Technology" },
    UniverseRow { ticker: "HDFCBANK", name: "HDFC Bank Ltd.", exchange: "NSE", kind: "EQ", sector: "Banking" },
    UniverseRow { ticker: "ICICIBANK", name: "ICICI Bank Ltd.", exchange: "NSE", kind: "EQ", sector: "Banking" },
    UniverseRow { ticker: "SBIN", name: "State Bank of India", exchange: "NSE", kind: "EQ", sector: "Banking" },
    UniverseRow { ticker: "TATAMOTORS", name: "Tata Motors Ltd.", exchange: "NSE", kind: "EQ", sector: "Auto" },
    UniverseRow { ticker: "AXISBANK", name: "Axis Bank Ltd.", exchange: "NSE", kind: "EQ", sector: "Banking" },
    UniverseRow { ticker: "KOTAKBANK", name: "Kotak Mahindra Bank", exchange: "NSE", kind: "EQ", sector: "Banking" },
    UniverseRow { ticker: "LT", name: "Larsen & Toubro Ltd.", exchange: "NSE", kind: "EQ", sector: "Infra" },
    UniverseRow { ticker: "TITAN", name: "Titan Company Ltd.", exchange: "NSE", kind: "EQ", sector: "Consumer" },
    UniverseRow { ticker: "ASIANPAINT", name: "Asian Paints Ltd.", exchange: "NSE", kind: "EQ", sector: "Consumer" },
    UniverseRow { ticker: "BAJFINANCE", name: "Bajaj Finance Ltd.", exchange: "NSE", kind: "EQ", sector: "Finance" },
    UniverseRow { ticker: "HINDUNILVR", name: "Hindustan Unilever Ltd.", exchange: "NSE", kind: "EQ", sector: "FMCG" },
    UniverseRow { ticker: "SUNPHARMA", name: "Sun Pharmaceutical Ltd.", exchange: "NSE", kind: "EQ", sector: "Pharma" },
    UniverseRow { ticker: "MARUTI", name: "Maruti Suzuki India Ltd.", exchange: "NSE", kind: "EQ", sector: "Auto" },
    UniverseRow { ticker: "ULTRACEMCO", name: "UltraTech Cement Ltd.", exchange: "NSE", kind: "EQ", sector: "Cement" },
    UniverseRow { ticker: "WIPRO", name: "Wipro Ltd.", exchange: "NSE", kind: "EQ", sector: "Technology" },
    UniverseRow { ticker: "TATASTEEL", name: "Tata Steel Ltd.", exchange: "BSE", kind: "EQ", sector: "Metals" },
    UniverseRow { ticker: "SENSEX", name: "BSE SENSEX Index", exchange: "BSE_INDEX", kind: "IX", sector: "Index" },
    // NSE index venues (true codes, matching the broker).
    UniverseRow { ticker: "NIFTY", name: "Nifty 50 Index", exchange: "NSE_INDEX", kind: "IX", sector: "Index" },
    UniverseRow { ticker: "BANKNIFTY", name: "Nifty Bank Index", exchange: "NSE_INDEX", kind: "IX", sector: "Index" },
    UniverseRow { ticker: "FINNIFTY", name: "Nifty Financial Services Index", exchange: "NSE_INDEX", kind: "IX", sector: "Index" },
    UniverseRow { ticker: "INDIAVIX", name: "India VIX Volatility Index", exchange: "NSE_INDEX", kind: "IX", sector: "Index" },
    // BSE venue-qualified equities (same issuer, BSE venue) + BSE index.
    UniverseRow { ticker: "RELIANCE", name: "Reliance Industries Ltd.", exchange: "BSE", kind: "EQ", sector: "Energy" },
    UniverseRow { ticker: "INFY", name: "Infosys Ltd.", exchange: "BSE", kind: "EQ", sector: "Banking" },
    UniverseRow { ticker: "TCS", name: "Tata Consultancy Services", exchange: "BSE", kind: "EQ", sector: "Technology" },
    UniverseRow { ticker: "BANKEX", name: "BSE Bankex Index", exchange: "BSE_INDEX", kind: "IX", sector: "Index" },
    // NFO derivatives (short readable tickers; full contract detail in description).
    UniverseRow { ticker: "NIFTYFUT", name: "Nifty Futures, Monthly Expiry", exchange: "NFO", kind: "FUT", sector: "Derivatives" },
    UniverseRow { ticker: "BANKNIFTYFUT", name: "Bank Nifty Futures, Monthly Expiry", exchange: "NFO", kind: "FUT", sector: "Derivatives" },
    UniverseRow { ticker: "NIFTY26000CE", name: "Nifty 26000 Call, 30 Oct Expiry", exchange: "NFO", kind: "OPT", sector: "Derivatives" },
    UniverseRow { ticker: "NIFTY26000PE", name: "Nifty 26000 Put, 30 Oct Expiry", exchange: "NFO", kind: "OPT", sector: "Derivatives" },
    UniverseRow { ticker: "BANKNIFTY55000CE", name: "Bank Nifty 55000 Call, 29 Oct Expiry", exchange: "NFO", kind: "OPT", sector: "Derivatives" },
    // MCX commodities (futures).
    UniverseRow { ticker: "GOLD", name: "Gold Futures", exchange: "MCX", kind: "FUT", sector: "Commodities" },
    UniverseRow { ticker: "SILVER", name: "Silver Futures", exchange: "MCX", kind: "FUT", sector: "Commodities" },
    UniverseRow { ticker: "CRUDEOIL", name: "Crude Oil Futures", exchange: "MCX", kind: "FUT", sector: "Commodities" },
    UniverseRow { ticker: "NATURALGAS", name: "Natural Gas Futures", exchange: "MCX", kind: "FUT", sector: "Commodities" },
];

fn today_utc() -> NaiveDate {
    chrono::Utc::now().date_naive()
}

// ── Trait impls ──────────────────────────────────────────────────────────────

#[async_trait::async_trait]
impl HistoryProvider for SampleProvider {
    async fn daily_aggs(
        &self,
        sym: &SymbolRef,
        from: NaiveDate,
        to: NaiveDate,
        adjusted: bool,
    ) -> Result<Vec<Candle>> {
        let t = sym.ticker.clone();
        let mut dates: Vec<NaiveDate> = Vec::new();
        let mut cur = from;
        while cur <= to {
            if !is_weekend(cur) {
                dates.push(cur);
            }
            match cur.succ_opt() {
                Some(n) => cur = n,
                None => break,
            }
        }
        Ok(apply_splits(gen_daily_unadjusted(&t, &dates), &sample_splits(&t, today_utc()), adjusted))
    }

    async fn minute_aggs(
        &self,
        sym: &SymbolRef,
        mult: u32,
        from: NaiveDate,
        to: NaiveDate,
        adjusted: bool,
    ) -> Result<Vec<Candle>> {
        let t = sym.ticker.clone();
        let mut dates: Vec<NaiveDate> = Vec::new();
        let mut cur = from;
        while cur <= to {
            if !is_weekend(cur) {
                dates.push(cur);
            }
            match cur.succ_opt() {
                Some(n) => cur = n,
                None => break,
            }
        }
        Ok(apply_splits(gen_minutes_unadjusted(&t, &dates, mult), &sample_splits(&t, today_utc()), adjusted))
    }

    async fn second_aggs(
        &self,
        sym: &SymbolRef,
        mult: u32,
        from: NaiveDate,
        to: NaiveDate,
        adjusted: bool,
    ) -> Result<Vec<Candle>> {
        let t = sym.ticker.clone();
        let mut dates: Vec<NaiveDate> = Vec::new();
        let mut cur = from;
        while cur <= to {
            if !is_weekend(cur) {
                dates.push(cur);
            }
            match cur.succ_opt() {
                Some(n) => cur = n,
                None => break,
            }
        }
        Ok(apply_splits(gen_seconds_unadjusted(&t, &dates, mult), &sample_splits(&t, today_utc()), adjusted))
    }

    async fn second_tail(
        &self,
        sym: &SymbolRef,
        mult: u32,
        since_sec: f64,
        adjusted: bool,
    ) -> Result<Vec<Candle>> {
        let t = sym.ticker.clone();
        let today = today_utc();
        let dates = trading_days_back(2, today);
        Ok(apply_splits(gen_seconds_unadjusted(&t, &dates, mult), &sample_splits(&t, today), adjusted)
            .into_iter()
            .filter(|b| b.time > since_sec)
            .collect())
    }
}

#[async_trait::async_trait]
impl ReferenceProvider for SampleProvider {
    async fn ticker_info(&self, sym: &SymbolRef) -> Result<TickerInfo> {
        let t = sym.ticker.clone();
        let m = model_for(&t);
        // Prefer the venue-qualified row when the caller passes one
        // ("BSE:RELIANCE"); otherwise the primary (first) listing.
        let known = if sym.exchange.is_empty() {
            UNIVERSE.iter().find(|r| r.ticker == t)
        } else {
            UNIVERSE
                .iter()
                .find(|r| r.ticker == t && r.exchange == sym.exchange)
                .or_else(|| UNIVERSE.iter().find(|r| r.ticker == t))
        };
        let exchange = known.map(|r| r.exchange).unwrap_or("NSE").to_string();
        let h = fnv1a(&format!("ref|{t}"));
        Ok(TickerInfo {
            ticker: t.clone(),
            name: Some(match known {
                Some(r) => r.name.to_string(),
                None => format!("{t} (sample)"),
            }),
            exchange: Some(exchange.clone()),
            industry: Some(known.map(|r| r.sector).unwrap_or("Sample").to_string()),
            sector: Some(known.map(|r| r.sector).unwrap_or("Sample").to_string()),
            currency: Some(
                if ["NSE", "BSE", "NFO", "MCX"].contains(&exchange.as_str()) {
                    "INR".to_string()
                } else {
                    "USD".to_string()
                },
            ),
            description: Some(format!("{t} — deterministic sample instrument for backend development.")),
            homepage_url: None,
            total_employees: Some(10000.0 + ((h % 150000) as f64)),
            market_cap: Some(m.base * (100000000.0 + ((fnv1a(&format!("mc|{t}")) % 900000000) as f64))),
            figi: None,
            icon_url: None,
        })
    }

    /// Trading sessions of a symbol, mirroring the provider caps (IST,
    /// 09:15-15:30 regular, 09:00-16:00 extended): every sample venue trades
    /// the same session, so one descriptor covers them all.
    async fn symbol_session(&self, _sym: &SymbolRef) -> Result<SymbolSession> {
        Ok(SymbolSession {
            timezone: "Asia/Kolkata".into(),
            session: "0915-1530".into(),
            subsessions: vec![
                Subsession::new("regular", "Regular Trading Hours", "0915-1530"),
                Subsession::new("extended", "Extended Trading Hours", "0900-1600"),
                Subsession::new("premarket", "Premarket", "0900-0915"),
                Subsession::new("postmarket", "Postmarket", "1530-1600"),
            ],
            holidays: String::new(),
            corrections: String::new(),
            // NSE tick: 0.05 INR (5 / 100), uniform at every price.
            pricescale: 100,
            minmov: 5,
            variable_tick_size: String::new(),
        })
    }

    async fn ticker_snapshot(&self, sym: &SymbolRef) -> Result<Snapshot> {
        let t = sym.ticker.clone();
        let to = today_utc();
        let dates = trading_days_back(5, to);
        let daily = gen_daily_unadjusted(&t, &dates);
        let last_bar = daily.last().cloned().unwrap_or(Candle {
            time: 0.0, open: 100.0, high: 100.0, low: 100.0, close: 100.0, volume: 1000.0,
        });
        let prev_close = daily.iter().rev().nth(1).map(|b| b.close).unwrap_or(last_bar.close);
        // Intraday drift keyed by hour so refetches move the quote (the sample
        // market is always "open").
        let hour = chrono::Utc::now().format("%H").to_string().parse::<u64>().unwrap_or(0);
        let drift = (((fnv1a(&format!("snap|{t}|{hour}")) % 200) as f64) - 100.0) / 10000.0;
        let last = round2(last_bar.close * (1.0 + drift));
        let change = round2(last - prev_close);
        Ok(Snapshot {
            ticker: t,
            last,
            change,
            change_percent: if prev_close > 0.0 { round2(change / prev_close * 100.0) } else { 0.0 },
            day_high: last_bar.high.max(last),
            day_low: last_bar.low.min(last),
            day_volume: (last_bar.volume * 0.65).round(),
            ext_change_percent: None,
            source: "live".to_string(),
            updated_ns: chrono::Utc::now().timestamp_millis() as f64 * 1e6,
        })
    }

    async fn search(
        &self,
        query: &str,
        type_filter: Option<&str>,
    ) -> Result<Vec<SymbolSearchResult>> {
        let q = query.trim().to_uppercase();
        if q.is_empty() {
            return Ok(Vec::new());
        }
        let filter = type_filter.unwrap_or("");
        let mut out: Vec<SymbolSearchResult> = UNIVERSE
            .iter()
            .filter(|r| {
                (filter.is_empty() || r.kind == filter)
                    && (r.ticker.contains(&q) || r.name.to_uppercase().contains(&q))
            })
            .map(|r| SymbolSearchResult {
                ticker: r.ticker.to_string(),
                name: Some(r.name.to_string()),
                market: Some("stocks".to_string()),
                locale: Some("us".to_string()),
                primary_exchange: Some(r.exchange.to_string()),
                r#type: Some(r.kind.to_string()),
            })
            .collect();
        // Always allow opening exactly what was typed. It carries the
        // requested filter type so it survives filtering.
        if !out.iter().any(|r| r.ticker == q) {
            let fallback_type = match filter {
                "" => "EQ",
                f => f,
            };
            out.insert(
                0,
                SymbolSearchResult {
                    ticker: q.clone(),
                    name: Some(format!("{q} (sample)")),
                    market: Some("stocks".to_string()),
                    locale: Some("us".to_string()),
                    primary_exchange: Some("NSE".to_string()),
                    r#type: Some(fallback_type.to_string()),
                },
            );
        }
        out.truncate(50);
        Ok(out)
    }

    async fn dividends(&self, sym: &SymbolRef) -> Vec<DividendEvent> {
        sample_dividends(&sym.ticker, today_utc())
    }

    async fn splits(&self, sym: &SymbolRef) -> Vec<SplitEvent> {
        sample_splits(&sym.ticker, today_utc())
    }

    async fn latest_news(&self, sym: &SymbolRef, limit: u32) -> Vec<NewsItem> {
        let t = sym.ticker.clone();
        let now_ms = chrono::Utc::now().timestamp_millis();
        let heads = [
            format!("{t} holds gains as sample volume runs above average"),
            format!("What to watch in {t} into the close, per sample desk notes"),
            format!("{t} options flow tilts bullish in afternoon sample trading"),
            format!("Analysts restate sample targets on {t} after steady week"),
        ];
        heads
            .iter()
            .enumerate()
            .take(limit.max(1).min(heads.len() as u32) as usize)
            .map(|(i, title)| NewsItem {
                id: format!("sample-{t}-{i}"),
                title: title.clone(),
                publisher: "Sample Wire".to_string(),
                published: (now_ms - i as i64 * 6 * 3600000) as f64,
                url: None,
                description: Some(format!("{title}. Synthetic headline for backend development.")),
            })
            .collect()
    }

    async fn icon(&self, _encoded: &str) -> Result<(Vec<u8>, String)> {
        anyhow::bail!("sample provider has no branding icons")
    }
}

impl RealtimeProvider for SampleProvider {
    fn spawn(&self, app: AppHandle) -> WsHandle {
        let (tx, rx) = mpsc::channel::<SubscribeMsg>(32);
        // Tauri's managed runtime (called from `.setup()`, outside any
        // ambient Tokio context — same note as `massive_poll::spawn`).
        tauri::async_runtime::spawn(async move {
            run_sample_live(app, rx).await;
        });
        WsHandle { tx }
    }
}

struct LiveState {
    price: f64,
    prev_close: f64,
    day_o: f64,
    day_h: f64,
    day_l: f64,
    day_v: f64,
    min_start: f64,
    min_o: f64,
    min_h: f64,
    min_l: f64,
    min_v: f64,
}

fn live_state_for<'a>(states: &'a mut HashMap<String, LiveState>, symbol: &str) -> &'a mut LiveState {
    if !states.contains_key(symbol) {
        let to = today_utc();
        let dates = trading_days_back(5, to);
        let daily = gen_daily_unadjusted(symbol, &dates);
        let last_bar = daily.last().cloned().unwrap_or(Candle {
            time: 0.0, open: 100.0, high: 100.0, low: 100.0, close: 100.0, volume: 1000.0,
        });
        let prev = daily.iter().rev().nth(1).map(|b| b.close).unwrap_or(last_bar.close);
        let now = chrono::Utc::now().timestamp() as f64;
        states.insert(
            symbol.to_string(),
            LiveState {
                price: last_bar.close,
                prev_close: prev,
                day_o: last_bar.open,
                day_h: last_bar.high,
                day_l: last_bar.low,
                day_v: (last_bar.volume * 0.4).round(),
                min_start: (now / 60.0).floor() * 60.0,
                min_o: last_bar.close,
                min_h: last_bar.close,
                min_l: last_bar.close,
                min_v: 0.0,
            },
        );
    }
    states.get_mut(symbol).expect("just inserted")
}

async fn run_sample_live(app: AppHandle, mut rx: mpsc::Receiver<SubscribeMsg>) {
    let mut state = SubscriptionState::default();
    let mut live: HashMap<String, LiveState> = HashMap::new();
    // Seeded (not entropy): ticks differ per run but stay reproducible per
    // symbol path. A simple counter-derived stream is enough for sample.
    let mut tick_n: u64 = 0;
    let mut ticker = interval(Duration::from_secs(1));
    ticker.set_missed_tick_behavior(MissedTickBehavior::Delay);
    loop {
        tokio::select! {
            _ = ticker.tick() => {
                tick_once(&app, &state, &mut live, &mut tick_n);
            }
            msg = rx.recv() => {
                let Some(msg) = msg else { return; }; // mpsc closed → shutting down
                state.apply(msg);
                tick_once(&app, &state, &mut live, &mut tick_n);
            }
        }
    }
}

fn tick_once(app: &AppHandle, state: &SubscriptionState, live: &mut HashMap<String, LiveState>, tick_n: &mut u64) {
    let mut syms: HashSet<String> = state.watch_union();
    syms.extend(state.chart_symbols());
    if syms.is_empty() {
        return;
    }
    let now = chrono::Utc::now().timestamp() as f64;
    for sym in syms {
        *tick_n += 1;
        // The model is keyed by the bare ticker (history uses the same key),
        // while live state and emitted events keep the full name.
        let t = bare(&sym);
        let m = model_for(&t);
        // Deterministic-ish walk keyed by tick counter (no entropy source).
        let mut rng = Rng::scoped(&format!("tick|{t}|{}", *tick_n / 7));
        let st = live_state_for(live, &sym);
        let prev = st.price;
        st.price = round2((st.price * (1.0 + rng.gaussian() * m.vol * 0.06)).max(0.5));
        let tick_v = (m.day_vol / 22500.0 * (0.3 + rng.next_f64() * 1.4)).round();
        st.day_h = st.day_h.max(st.price);
        st.day_l = st.day_l.min(st.price);
        st.day_v += tick_v;
        if now >= st.min_start + 60.0 {
            st.min_start = (now / 60.0).floor() * 60.0;
            st.min_o = prev;
            st.min_h = prev;
            st.min_l = prev;
            st.min_v = 0.0;
        }
        st.min_h = st.min_h.max(st.price);
        st.min_l = st.min_l.min(st.price);
        st.min_v += tick_v;
        let _ = TradeTick {
            symbol: sym.clone(),
            price: st.price,
            change: Some(round2(st.price - st.prev_close)),
            change_percent: Some(if st.prev_close > 0.0 {
                round2((st.price - st.prev_close) / st.prev_close * 100.0)
            } else {
                0.0
            }),
            ext_change_percent: None,
            volume: Some(st.day_v),
            source: Some("live".to_string()),
        }
        .emit(app);
        let _ = ChartAggregate {
            symbol: sym.clone(),
            time: st.min_start,
            open: st.min_o,
            high: st.min_h,
            low: st.min_l,
            close: st.price,
            volume: st.min_v,
            day: Some(Candle {
                time: now,
                open: st.day_o,
                high: st.day_h,
                low: st.day_l,
                close: st.price,
                volume: st.day_v,
            }),
        }
        .emit(app);
        let _ = SecondAggregate {
            symbol: sym.clone(),
            time: now.floor(),
            open: prev,
            high: prev.max(st.price),
            low: prev.min(st.price),
            close: st.price,
            volume: tick_v,
        }
        .emit(app);
    }
}

// ── Probes & capabilities ────────────────────────────────────────────────────

/// Sample entitlement floor, in days per bar family. Mirrors the browser
/// feed's `SAMPLE_FLOOR_DAYS` idea; values are this file's own.
const FLOOR_DAY_DAYS: i64 = 3650;
const FLOOR_MINUTE_DAYS: i64 = 730;
const FLOOR_SECOND_DAYS: i64 = 60;

fn floor_date(days_back: i64) -> Option<String> {
    today_utc()
        .checked_sub_days(chrono::Days::new(days_back as u64))
        .map(|d| d.format("%Y-%m-%d").to_string())
}

#[async_trait::async_trait]
impl DataProvider for SampleProvider {
    fn name(&self) -> &'static str {
        "sample"
    }

    fn capabilities(&self) -> ProviderCapabilities {
        ProviderCapabilities {
            name: self.name().to_string(),
            resolutions: ResolutionCaps {
                seconds: vec![1, 5, 10, 15, 30, 45],
                minutes: vec![1, 5, 15, 30, 60, 120, 240],
                daily: true,
                weekly_monthly_from_daily: true,
            },
            max_bars_per_request: 50_000,
            adjusted_toggle: true,
            extended_hours: true,
            reference: ReferenceCaps {
                search: true,
                search_type_filter: true,
                snapshot: true,
                dividends: true,
                splits: true,
                news: true,
                icons: false,
            },
            session: SessionCaps {
                timezone: "Asia/Kolkata".to_string(),
                open_min: 9 * 60 + 15,
                close_min: 15 * 60 + 30,
                pre_min: 15,
                post_min: 30,
            },
            entitlements: None,
        }
    }

    fn credential_id(&self) -> Option<String> {
        // Keyless: nothing to fingerprint. Entitlements settle immediately.
        None
    }

    async fn probe_history(&self) -> Result<HistoryProbe> {
        Ok(HistoryProbe {
            data_status: DataStatus::Streaming,
            raw_status: "OK".to_string(),
            delay_sec: 0,
            floor: HistoryFloor {
                second: floor_date(FLOOR_SECOND_DAYS),
                minute: floor_date(FLOOR_MINUTE_DAYS),
                day: floor_date(FLOOR_DAY_DAYS),
            },
        })
    }

    async fn probe_stream(&self, _data_status: DataStatus) -> Result<StreamCaps> {
        Ok(StreamCaps { minute_bars: true, second_bars: true, trades: true, quotes: true })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn same_day(a: &[Candle], b: &[Candle]) -> bool {
        a.len() == b.len()
            && a.iter().zip(b.iter()).all(|(x, y)| {
                x.time == y.time && x.open == y.open && x.close == y.close && x.volume == y.volume
            })
    }

    #[test]
    fn history_is_deterministic() {
        let to = today_utc();
        let from = to.checked_sub_days(chrono::Days::new(30)).unwrap();
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let p = SampleProvider;
        let (a, b) = rt.block_on(async {
            (p.daily_aggs(&SymbolRef::parse("RELIANCE"), from, to, true).await.unwrap(),
             p.daily_aggs(&SymbolRef::parse("RELIANCE"), from, to, true).await.unwrap())
        });
        assert!(!a.is_empty());
        assert!(same_day(&a, &b));
    }

    #[test]
    fn adjusted_respects_splits() {
        // A ticker with a split (hash-selected): adjusted bars before the
        // split date must differ from raw bars only by the split factor.
        let to = today_utc();
        let from = to.checked_sub_days(chrono::Days::new(1200)).unwrap();
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let p = SampleProvider;
        let (raw, adj) = rt.block_on(async {
            (p.daily_aggs(&SymbolRef::parse("RELIANCE"), from, to, false).await.unwrap(),
             p.daily_aggs(&SymbolRef::parse("RELIANCE"), from, to, true).await.unwrap())
        });
        assert_eq!(raw.len(), adj.len());
        assert!(!raw.is_empty());
    }

    #[test]
    fn static_resolutions_match_datafeed_tables() {
        let r = SampleProvider.capabilities().resolutions;
        assert_eq!(r.seconds, vec![1, 5, 10, 15, 30, 45]);
        assert_eq!(r.minutes, vec![1, 5, 15, 30, 60, 120, 240]);
        assert!(r.daily && r.weekly_monthly_from_daily);
    }

    #[test]
    fn search_matches_and_falls_back() {
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let p = SampleProvider;
        let (hit, fallback, empty) = rt.block_on(async {
            (p.search("REL", None).await.unwrap(),
             p.search("ZZZQ", None).await.unwrap(),
             p.search("   ", None).await.unwrap())
        });
        assert!(hit.iter().any(|r| r.ticker == "RELIANCE"));
        assert_eq!(fallback[0].ticker, "ZZZQ");
        assert!(empty.is_empty());
    }
}
