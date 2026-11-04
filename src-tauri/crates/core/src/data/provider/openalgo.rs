/*
 * OpenAlgo provider — the broker's OpenAlgo server (REST + WebSockets) as a
 * `DataProvider`, behind the same trait seam as the OpenTrader gateway.
 *
 * Implementation mirror of the browser source (src/data/providers/openalgo.ts):
 * the same `/api/v1` endpoints, the same interval mapping, the same split-retry
 * history fetches, the same session building from the broker calendar. Where
 * the browser talks to the host directly from the page, this runs in the app's
 * process — no CORS to satisfy and no Settings tab: the config comes from the
 * environment (`.env`, loaded by the host at startup):
 *
 *   OPENALGO_HOST  the server base ("http://127.0.0.1:5000")
 *   OPENALGO_WS    its live stream ("ws://127.0.0.1:5000/ws") — phase 2
 *   OPENALGO_KEY   the API key
 *
 * The key is held in the struct, sent only inside the request payload's
 * `apikey` field (like the source does) and never printed or logged.
 *
 * Live data (phase 1): a REST snapshot poller, the direct sibling of
 * `quote_poll` — it polls the subscribed symbols' `/quotes` and re-emits the
 * identical `TradeTick` / `ChartAggregate` events, so the watchlist, alerts
 * and the chart's minute/day bars light up with no frontend change. The
 * broker's own WebSocket (its `mode: "Quote"` ticks with the same minute
 * buckets and synthetic second bars the source maintains) is the phase-2
 * transport; until it lands the seconds charts live off their REST tail
 * refresh and the second ceilings of `fetch_intervals` serve their history.
 *
 * Select with `DATA_PROVIDER=openalgo`.
 */
use super::capabilities::{
    DataStatus, HistoryFloor, HistoryProbe, ProviderCapabilities, ReferenceCaps, ResolutionCaps,
    StreamCaps,
};
use super::entitlements;
use super::{DataProvider, HistoryProvider, RealtimeProvider, ReferenceProvider};
use crate::data::gateway::user_agent;
use crate::data::market_rest::icon_proxy_url;
use crate::data::market_stream::{ChartAggregate, SubscribeMsg, SubscriptionState, TradeTick, WsHandle};
use crate::data::session::{Subsession, SymbolSession};
use crate::data::symbol::SymbolRef;
use crate::data::types::{
    Candle, DividendEvent, Snapshot, SplitEvent, SymbolSearchResult, TickerInfo,
};
use crate::host::{Event, HostRef};
use crate::net::SharedClient;
use anyhow::{anyhow, bail, Context as _, Result};
use chrono::{Datelike, Days, NaiveDate, TimeZone, Utc};
use chrono_tz::Tz;
use percent_encoding::percent_decode_str;
use serde::Deserialize;
use std::collections::hash_map::Entry::{Occupied, Vacant};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::future::Future;
use std::pin::Pin;
use std::sync::{Mutex, OnceLock, RwLock};
use std::time::{Duration, Instant};
use tokio::sync::mpsc;
use tokio::sync::Mutex as TokioMutex;
use tokio::time::{interval, sleep, MissedTickBehavior};

const NAME: &str = "openalgo";

pub struct OpenAlgoProvider {
    /// Config read once from the environment at startup (`.env`, see the
    /// header). The key never leaves here except inside a request body.
    base: String,
    ws: String,
    key: String,
}

impl OpenAlgoProvider {
    pub fn new() -> Self {
        let read = |name: &str| std::env::var(name).unwrap_or_default().trim().to_string();
        let this = Self { base: read("OPENALGO_HOST"), ws: read("OPENALGO_WS"), key: read("OPENALGO_KEY") };
        // One startup line, values left out: set/absent only, never the key.
        eprintln!(
            "[provider] openalgo: host {}, ws {}, key {}",
            if this.base.is_empty() { "<unset — set OPENALGO_HOST>" } else { "set" },
            if this.ws.is_empty() { "<unset — set OPENALGO_WS>" } else { "set" },
            if this.key.is_empty() { "<unset — set OPENALGO_KEY>" } else { "set" },
        );
        this
    }
}

/// The key requirement, verbatim like the browser source's `requireKey`: a
/// request without it must fail fast and visibly, never silently render as an
/// empty chart.
fn require_key(key: &str) -> Result<String> {
    if key.is_empty() {
        bail!("OpenAlgo API key missing — add it to ../.env (OPENALGO_KEY) and restart");
    }
    Ok(key.to_string())
}

// ── REST client ──────────────────────────────────────────────────────────────
// One pooled, proxy-aware client for the whole process, like the gateway's
// transport (`market_rest::http`). It rebuilds itself on a proxy change, so
// Settings → Network keeps running this provider too.

static REST: SharedClient = SharedClient::new(|b| {
    b.pool_idle_timeout(Duration::from_secs(90))
        .tcp_keepalive(Duration::from_secs(60))
        .user_agent(user_agent())
});

/// One `POST {base}{path}` answer: the OpenAlgo status envelope with `data`
/// typed at the call site; `timezone` of the holiday answer rides `extra`.
#[derive(Deserialize)]
struct OaAnswer<T> {
    #[serde(default)]
    status: Option<String>,
    #[serde(default)]
    message: Option<String>,
    // No `pid` here: `Option<T>` with an explicit `default` would make the
    // derive demand `T: Default` (the imperfect-derive bound); absent is None.
    data: Option<T>,
    #[serde(flatten)]
    extra: HashMap<String, serde_json::Value>,
}

/// `{ apikey, ...body }` — the apikey of the browser source's post() spread
/// in as the first field; the body never carries it twice.
fn with_apikey(key: &str, body: serde_json::Value) -> Result<serde_json::Value> {
    let mut payload = serde_json::json!({ "apikey": require_key(key)? });
    if let (serde_json::Value::Object(p), serde_json::Value::Object(fields)) = (&mut payload, body) {
        p.extend(fields);
    }
    Ok(payload)
}

async fn post<T: for<'de> Deserialize<'de>>(
    base: &str,
    key: &str,
    path: &str,
    body: serde_json::Value,
) -> Result<OaAnswer<T>> {
    let payload = with_apikey(key, body)?;
    let resp = REST
        .get()
        .post(&format!("{base}{path}"))
        .json(&payload)
        .send()
        .await
        .map_err(|e| anyhow!("OpenAlgo {path}: network unreachable ({})", e.without_url()))?;
    let status = resp.status().as_u16();
    if !resp.status().is_success() {
        bail!("OpenAlgo {path}: HTTP {status}");
    }
    let json = resp
        .json::<serde_json::Value>()
        .await
        .map_err(|e| anyhow!("OpenAlgo {path}: bad answer ({})", e.without_url()))?;
    let answer: OaAnswer<T> = serde_json::from_value(json).map_err(|e| anyhow!("OpenAlgo {path}: bad answer ({e})"))?;
    if answer.status.as_deref() == Some("error") {
        // The server's own message, never the request (it carries the key).
        bail!("OpenAlgo {path}: {}", answer.message.clone().unwrap_or_else(|| "request failed".into()));
    }
    Ok(answer)
}

// ── Symbol + interval mapping (unit-tested) ──────────────────────────────────

/// "NSE:SBIN" → ("NSE", "SBIN"); a bare "SBIN" → ("NSE", "SBIN"); the head of
/// a comma list as the source: "SBIN,EQ" → SBIN.
fn split_open_algo(symbol: &str) -> (String, String) {
    let head = symbol.split(',').next().unwrap_or("").trim().to_uppercase();
    match head.split_once(':') {
        Some((exchange, rest)) => (exchange.to_string(), rest.to_string()),
        None => ("NSE".into(), head),
    }
}

/// Broker interval token "1m" → 1, "2h" → 120. `None` when it is not a minute
/// multiple (second tokens read "s").
fn minute_token_to_mult(token: &str) -> Option<u32> {
    split_token(token).and_then(|(num, unit)| match unit {
        "" | "m" | "min" => Some(num),
        "h" | "hour" => Some(num.checked_mul(60)?),
        _ => None,
    })
}

/// Second token "1s" / "10sec" → its multiple; `None` for anything else.
fn second_token_to_mult(token: &str) -> Option<u32> {
    split_token(token).and_then(|(num, unit)| match unit {
        "" | "s" | "sec" => Some(num),
        _ => None,
    })
}

/// Leading digits + the rest of an interval token; `None` without digits.
fn split_token(token: &str) -> Option<(u32, &str)> {
    let t = token.trim();
    let digits = t.len() - t.trim_start_matches(|c: char| c.is_ascii_digit()).len();
    let n = t[..digits].parse::<u32>().ok()?;
    (n >= 1).then_some((n, &t[digits..]))
}

/// A broker `/intervals` payload. List fields arrive one at a time and may be
/// shapes we don't expect — read defensively like the source's `strings()`:
/// the strings of an array, everything else skipped.
#[derive(Deserialize, Default)]
struct OaIntervals {
    #[serde(default)]
    months: serde_json::Value,
    #[serde(default)]
    weeks: serde_json::Value,
    #[serde(default)]
    days: serde_json::Value,
    #[serde(default)]
    hours: serde_json::Value,
    #[serde(default)]
    minutes: serde_json::Value,
    #[serde(default)]
    seconds: serde_json::Value,
}

fn strings(v: &serde_json::Value) -> Vec<&str> {
    match v {
        serde_json::Value::Array(items) => items.iter().filter_map(|x| x.as_str()).collect(),
        _ => Vec::new(),
    }
}

/// A normalized broker interval list: the directly-served minute multiples
/// (`/intervals` minutes + hours merged) and second multiples with their
/// request tokens.
#[derive(Clone)]
struct NormalizedIntervals {
    minutes: HashMap<u32, String>,
    seconds: Vec<u32>,
    second_tokens: HashMap<u32, String>,
    daily: bool,
}

fn normalize_intervals(data: &OaIntervals) -> NormalizedIntervals {
    // Unknown tokens are skipped, never trusted.
    let mut minutes: HashMap<u32, String> = HashMap::new();
    for token in strings(&data.minutes).into_iter().chain(strings(&data.hours)) {
        if let Some(mult) = minute_token_to_mult(token) {
            minutes.entry(mult).or_insert_with(|| token.trim().to_string());
        }
    }
    let mut second_tokens: HashMap<u32, String> = HashMap::new();
    for token in strings(&data.seconds) {
        if let Some(mult) = second_token_to_mult(token) {
            second_tokens.entry(mult).or_insert_with(|| token.trim().to_string());
        }
    }
    let mut seconds: Vec<u32> = second_tokens.keys().copied().collect();
    seconds.sort();
    let daily = strings(&data.days).iter().any(|t| t.trim().eq_ignore_ascii_case("D"));
    NormalizedIntervals { minutes, seconds, second_tokens, daily }
}

/// Fallback minute table (verified live against the broker server), used until
/// the broker's own `/intervals` answers and whenever it doesn't.
fn fallback_minute_table() -> HashMap<u32, String> {
    HashMap::from([
        (1, "1m".into()),
        (3, "3m".into()),
        (5, "5m".into()),
        (10, "10m".into()),
        (15, "15m".into()),
        (20, "20m".into()),
        (30, "30m".into()),
        (60, "1h".into()),
        (120, "2h".into()),
    ])
}

struct IntervalEntry {
    at: Instant,
    normalized: NormalizedIntervals,
}

/// Broker intervals, cached a day; a failing fetch keeps the fallback table in
/// play (the caller never learns about it — history falls back, capabilities
/// serve the verified table). Concurrent callers share one in-flight fetch
/// through a gate: the first fetches, the rest pick up whatever is cached by
/// the time they get in.
static INTERVALS: RwLock<Option<IntervalEntry>> = RwLock::new(None);
/// Held across the network fetch, so it is the async mutex (a std guard held
/// over `.await` would make the whole task non-`Send`).
static INTERVALS_GATE: OnceLock<TokioMutex<()>> = OnceLock::new();

const CACHE_TTL: Duration = Duration::from_secs(24 * 3600);

async fn fetch_intervals(base: &str, key: &str) -> Option<NormalizedIntervals> {
    if let Some(hit) = INTERVALS.read().unwrap().as_ref() {
        if hit.at.elapsed() < CACHE_TTL {
            return Some(hit.normalized.clone());
        }
    }
    let gate = INTERVALS_GATE.get_or_init(|| TokioMutex::new(()));
    let _guard = gate.lock().await;
    // Another caller may have refreshed while we waited for the gate.
    if let Some(hit) = INTERVALS.read().unwrap().as_ref() {
        if hit.at.elapsed() < CACHE_TTL {
            return Some(hit.normalized.clone());
        }
    }
    let Ok(answer) = post::<OaIntervals>(base, key, "/api/v1/intervals", serde_json::json!({})).await else {
        return None;
    };
    // A degenerate payload (a broker hiccup answering {} or []) must never
    // reach the cache: an empty minutes map would break history, scroll-back
    // and the advertised resolutions until expiry, long after the broker
    // recovers. `None` keeps the fallback table.
    let Some(data) = answer.data else { return None };
    let normalized = normalize_intervals(&data);
    if normalized.minutes.is_empty() {
        return None;
    }
    *INTERVALS.write().unwrap() = Some(IntervalEntry { at: Instant::now(), normalized: normalized.clone() });
    Some(normalized)
}

/// Currently known minute-multiplier → broker token (the broker's list when
/// fetched, else the fallback table).
fn minute_table() -> HashMap<u32, String> {
    match INTERVALS.read().unwrap().as_ref() {
        Some(hit) => hit.normalized.minutes.clone(),
        None => fallback_minute_table(),
    }
}

/// Currently known second-multiplier → broker token (the broker's list when
/// fetched; empty when not — unlisted multiples stay unadvertised).
fn second_table() -> HashMap<u32, String> {
    match INTERVALS.read().unwrap().as_ref() {
        Some(hit) => hit.normalized.second_tokens.clone(),
        None => HashMap::new(),
    }
}

/// True when the broker serves daily bars (always, until proven otherwise).
fn daily_served() -> bool {
    match INTERVALS.read().unwrap().as_ref() {
        Some(hit) => hit.normalized.daily,
        None => true,
    }
}

/// App multiplier → broker interval token, from the freshest table.
fn resolve_interval(kind: &str, mult: u32) -> Result<String> {
    let table = if kind == "second" { second_table() } else { minute_table() };
    table
        .get(&mult)
        .cloned()
        .ok_or_else(|| {
            if kind == "second" {
                anyhow!("OpenAlgo: second multiplier {mult} is not served (see /intervals)")
            } else {
                anyhow!("OpenAlgo: minute multiplier {mult} has no interval mapping")
            }
        })
}

// ── History (the source's window caps and split-retry) ───────────────────────

#[derive(Deserialize)]
struct OaBar {
    #[serde(deserialize_with = "num_f64")]
    timestamp: Option<f64>,
    #[serde(deserialize_with = "num_f64")]
    open: Option<f64>,
    #[serde(deserialize_with = "num_f64")]
    high: Option<f64>,
    #[serde(deserialize_with = "num_f64")]
    low: Option<f64>,
    #[serde(deserialize_with = "num_f64")]
    close: Option<f64>,
    #[serde(deserialize_with = "num_f64")]
    volume: Option<f64>,
}

/// A broker number that may arrive as a JSON string (`"1234.5"` — an adapter
/// quirk the browser source rides over with `Number()`): numeric values pass
/// through, numeric strings parse, anything else (and missing) reads `None` —
/// which then drops the row (`to_candle`).
fn num_f64<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<f64>, D::Error> {
    let v = serde_json::Value::deserialize(d)?;
    Ok(match v {
        serde_json::Value::Null => None,
        serde_json::Value::Number(n) => n.as_f64(),
        serde_json::Value::String(s) => s.trim().parse::<f64>().ok(),
        _ => None,
    })
}

/// A `/history` row's candle: `None` when the timestamp or a price is missing
/// (the bar can't be placed or drawn); a missing volume reads 0.
fn to_candle(b: OaBar) -> Option<Candle> {
    let (time, open, high, low, close) =
        (b.timestamp?, b.open?, b.high?, b.low?, b.close?);
    Some(Candle { time, open, high, low, close, volume: b.volume.unwrap_or(0.0) })
}

/// `YYYY-MM-DD` of a date.
fn fmt_date(d: NaiveDate) -> String {
    d.format("%Y-%m-%d").to_string()
}

/// Split `[from, to]` into windows of at most `max_days` calendar dates (the
/// source's `chunkRanges`; the broker caps history requests that way).
fn chunk_ranges(from: NaiveDate, to: NaiveDate, max_days: i64) -> Vec<(NaiveDate, NaiveDate)> {
    let mut out = Vec::new();
    let mut first = from;
    while first <= to {
        let window_end = first
            .checked_add_days(Days::new((max_days - 1).max(0) as u64))
            .unwrap_or(to)
            .min(to);
        out.push((first, window_end));
        first = window_end.checked_add_days(Days::new(1)).unwrap_or(window_end);
    }
    out
}

/// The broker's benign empty: a window with no trading in it (its message says
/// "no data"). Auth and host failures must stay loud — a bad key rendering as
/// an empty chart would be worse than an error.
fn is_no_data(e: &anyhow::Error) -> bool {
    e.to_string().to_lowercase().contains("no data")
}

/// An HTTP 5xx from the history endpoint (the upstream broker API failing on a
/// window rather than answering it).
fn is_http_5xx(e: &anyhow::Error) -> bool {
    let s = &e.to_string();
    let Some(at) = s.find("HTTP 5") else { return false };
    let digits = &s[at + 5..(at + 8).min(s.len())];
    digits.len() == 3 && digits.chars().all(|c| c.is_ascii_digit())
}

/// One history chunk: its bars, or `[]` when the window holds no trading. A
/// 5xx on a window this size is usually the upstream API flapping — one retry
/// on the same window (the source's `historyChunk`), then the split-retry
/// narrows it.
async fn history_chunk(
    base: &str,
    key: &str,
    ticker: &str,
    exchange: &str,
    oa_interval: &str,
    from: &str,
    to: &str,
) -> Result<Vec<OaBar>> {
    let body = serde_json::json!({
        "symbol": ticker,
        "exchange": exchange,
        "interval": oa_interval,
        "start_date": from,
        "end_date": to,
    });
    for attempt in 0..2 {
        match post::<Vec<OaBar>>(base, key, "/api/v1/history", body.clone()).await {
            Ok(answer) => return Ok(answer.data.unwrap_or_default()),
            Err(e) => {
                if is_no_data(&e) {
                    return Ok(Vec::new());
                }
                if is_http_5xx(&e) && attempt == 0 {
                    sleep(Duration::from_millis(300)).await;
                    continue;
                }
                return Err(e);
            }
        }
    }
    unreachable!("the loop always returns or sleeps into its second pass")
}

/// A date window's bars with split-retry: a 5xx (verified live on wide 15m
/// ranges) narrows the window in halves until it serves. A persistently
/// failing range leaves a gap with a warning instead of failing the page —
/// partial data beats an empty chart. Auth/4xx/rate-limiting never enter here.
fn history_window<'a>(
    base: &'a str,
    key: &'a str,
    ticker: &'a str,
    exchange: &'a str,
    oa_interval: &'a str,
    from: NaiveDate,
    to: NaiveDate,
    depth: u32,
) -> Pin<Box<dyn Future<Output = Result<Vec<OaBar>>> + Send + 'a>> {
    // The half-split is naturally recursive and a plain recursive async fn has
    // an infinitely-sized future (E0733); the dyn box is the indirection.
    Box::pin(async move {
        let (from_s, to_s) = (fmt_date(from), fmt_date(to));
        if depth == 0 {
            eprintln!("[openalgo] history {exchange}:{ticker} {oa_interval} {from_s}..{to_s}");
        }
        match history_chunk(base, key, ticker, exchange, oa_interval, &from_s, &to_s).await {
            Ok(bars) => Ok(bars),
            Err(e) if is_http_5xx(&e) => {
                if from >= to || depth >= 5 {
                    eprintln!("[openalgo] history {from_s}..{to_s} failing (HTTP 5xx); leaving a gap");
                    return Ok(Vec::new());
                }
                let mid = from + chrono::Duration::days((to - from).num_days() / 2);
                let next = mid.checked_add_days(Days::new(1)).unwrap_or(to);
                let (left, right) = tokio::join!(
                    history_window(base, key, ticker, exchange, oa_interval, from, mid, depth + 1),
                    history_window(base, key, ticker, exchange, oa_interval, next, to, depth + 1)
                );
                let mut out = left?;
                out.extend(right?);
                Ok(out)
            }
            Err(e) => Err(e),
        }
    })
}

/// History of one interval over a date range, chunked and sorted. Range caps
/// mirror the source: daily and coarser up to a year per call, seconds 3 days,
/// the rest 30 days.
async fn fetch_history(
    base: &str,
    key: &str,
    ticker: &str,
    exchange: &str,
    oa_interval: &str,
    from: NaiveDate,
    to: NaiveDate,
) -> Result<Vec<Candle>> {
    let max_days: i64 = if matches!(oa_interval, "D" | "W" | "M") {
        365
    } else if oa_interval.to_lowercase().ends_with('s') {
        3
    } else {
        30
    };
    let mut out = Vec::new();
    for (a, b) in chunk_ranges(from, to, max_days) {
        for bar in history_window(base, key, ticker, exchange, oa_interval, a, b, 0).await? {
            if let Some(c) = to_candle(bar) {
                out.push(c);
            }
        }
    }
    out.sort_by(|a, b| a.time.total_cmp(&b.time));
    Ok(out)
}

/// Aggregate 2h bars into 4h bars (the broker has no 4h interval), the
/// source's `aggregate2h`. The 4h buckets floor on epoch seconds.
fn aggregate_2h(bars: Vec<Candle>) -> Vec<Candle> {
    const BUCKET: f64 = 14_400.0; // 4h in seconds
    let mut out: Vec<Candle> = Vec::new();
    for b in bars {
        let bucket = (b.time / BUCKET).floor() * BUCKET;
        match out.last_mut() {
            Some(cur) if cur.time == bucket => {
                cur.high = cur.high.max(b.high);
                cur.low = cur.low.min(b.low);
                cur.close = b.close;
                cur.volume += b.volume;
            }
            _ => out.push(Candle { time: bucket, ..b }),
        }
    }
    out
}

/// Snap a broker day bar to its IST calendar date's midnight (verified live:
/// day bars stamp UTC midnight, e.g. 2026-10-08T00:00Z for the Oct-8 NSE
/// session). The app keys daily bars by exchange-local midnight (see
/// `dailyBarStamp` in datafeed.ts); without this the history's last bar sorts
/// after the live day stamp and is dropped as stale — the 1D chart freezes
/// while minute charts tick on. India has no DST, so a fixed offset.
const IST_OFFSET_SEC: i64 = 19_800;

fn snap_day_bar(mut c: Candle) -> Candle {
    // The stamp's IST date (t plus the offset lands inside that IST day), so
    // a snapped bar re-reads its own date: running again changes nothing.
    let t = c.time as i64;
    let day = match chrono::DateTime::from_timestamp(t + IST_OFFSET_SEC, 0) {
        Some(dt) => dt.date_naive(),
        None => return c,
    };
    let snapped =
        day.and_hms_opt(0, 0, 0).map(|n| n.and_utc().timestamp()).unwrap_or(t) - IST_OFFSET_SEC;
    if snapped != t {
        c.time = snapped as f64;
    }
    c
}

// ── Reference mapping ────────────────────────────────────────────────────────

#[derive(Deserialize, Default)]
#[serde(default)]
struct OaSymbolInfo {
    name: Option<String>,
    brsymbol: Option<String>,
    instrumenttype: Option<String>,
    expiry: Option<String>,
    strike: Option<f64>,
    lotsize: Option<f64>,
}

/// A broker instrument's key fields for the display name.
struct Descriptor<'a> {
    instrumenttype: Option<&'a str>,
    strike: Option<f64>,
    expiry: &'a str,
}

/// Display name for a broker instrument. Stocks keep the plain name; a
/// contract with an expiry reads `Name Strike Type Expiry` (e.g. "NIFTY
/// 26100 CE 30-Oct-26"), skipping missing parts. The broker marks "none" as an
/// empty expiry and a negative strike — both read as absent.
fn display_name(name: &str, d: &Descriptor) -> String {
    let expiry = d.expiry.trim();
    if expiry.is_empty() {
        return name.to_string();
    }
    let mut parts = vec![name.to_string()];
    if let Some(strike) = d.strike.filter(|s| *s >= 0.0) {
        parts.push(format!("{strike}"));
    }
    if let Some(kind) = d.instrumenttype.map(str::trim).filter(|k| !k.is_empty()) {
        parts.push(kind.to_string());
    }
    parts.push(expiry.to_string());
    parts.join(" ")
}

/// Company logo URL for a ticker (dharun public logo API, symbol last).
/// Stocks only — indices and derivatives 404, so callers pass EQ rows.
fn logo_url(ticker: &str) -> String {
    format!("https://api.dharun.dev/v1/logos/{}", ticker.to_uppercase())
}

/// Company profile from the public dharun API (sector, industry — none
/// of which the broker /symbol serves). Unknown tickers 404: `None`, never fatal.
#[derive(Deserialize, Default)]
#[serde(default)]
struct OaCompanyProfile {
    sector: Option<String>,
    industry: Option<String>,
}

async fn fetch_company_profile(ticker: &str) -> Option<OaCompanyProfile> {
    let resp = REST
        .get()
        .get(format!("https://api.dharun.dev/v1/companies/{}", ticker.to_uppercase()))
        .send()
        .await
        .ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let json: serde_json::Value = resp.json().await.ok()?;
    let data = json.get("data")?;
    serde_json::from_value(data.clone()).ok()
}

/// Map a broker instrument to the desktop's `TickerInfo` (the desktop struct
/// has no ISIN/point-value fields the browser side fills — richer web shapes,
/// not an architecture difference).
/// Exchanges that price in INR (the web source's set, verbatim).
const INDIAN_EXCHANGES: [&str; 9] = [
    "NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX", "NSE_INDEX", "BSE_INDEX",
];

fn shape_ticker_info(
    ticker: &str,
    exchange: &str,
    d: &OaSymbolInfo,
    profile: Option<&OaCompanyProfile>,
) -> TickerInfo {
    let lotsize = d.lotsize.unwrap_or(1.0);
    let is_eq = d.instrumenttype.as_deref() == Some("EQ");
    // `brsym · exp x · lot n`: each tail only when it applies (equities read
    // none), and the empty-strings the broker marks "none" with fall back.
    let brsym = d
        .brsymbol
        .as_deref()
        .filter(|s| !s.is_empty())
        .unwrap_or(ticker)
        .to_string();
    let desc = format!(
        "{brsym}{}{}",
        d.expiry
            .as_deref()
            .filter(|e| !e.is_empty())
            .map(|e| format!(" · exp {e}"))
            .unwrap_or_default(),
        (lotsize != 1.0)
            .then(|| format!(" · lot {lotsize}"))
            .unwrap_or_default(),
    );
    TickerInfo {
        ticker: ticker.to_string(),
        name: Some(display_name(
            d.name
                .as_deref()
                .filter(|n| !n.is_empty())
                .unwrap_or(ticker),
            &Descriptor { instrumenttype: d.instrumenttype.as_deref(), strike: d.strike, expiry: d.expiry.as_deref().unwrap_or("") },
        )),
        exchange: Some(exchange.to_string()),
        // Sector / industry come from the public company profile (the broker
        // /symbol endpoint doesn't serve them).
        industry: profile.and_then(|p| p.industry.clone()),
        sector: profile.and_then(|p| p.sector.clone()),
        currency: INDIAN_EXCHANGES.contains(&exchange).then(|| "INR".to_string()),
        description: Some(desc),
        homepage_url: None,
        total_employees: None,
        market_cap: None,
        figi: None,
        // Script symbol type (`syminfo.type`): broker equities are stocks; the
        // other instrument types have no measured equivalent.
        symbol_type: is_eq.then(|| "stock".to_string()),
        // Security type as served.
        security_type: is_eq.then(|| "stock".to_string()),
        // The logo rides the ticker-icon proxy so the webview never needs the
        // key to fetch it; None for indices/derivatives (letter fallback).
        icon_url: is_eq.then(|| icon_proxy_url(&logo_url(ticker))),
    }
}

/// Dialog type-filter code → accepted OpenAlgo instrument types. The API
/// reports indices as "INDEX" (verified live); the dialog code stays "IX".
fn type_allows(filter: &str, kind: Option<&str>) -> bool {
    let kind = kind.unwrap_or("");
    match filter {
        "EQ" => kind == "EQ" || kind.is_empty(),
        "FUT" => kind == "FUT",
        "CE" => kind == "CE",
        "PE" => kind == "PE",
        "OPT" => kind == "CE" || kind == "PE",
        "IX" => kind == "IX" || kind == "INDEX",
        // Unlisted filter codes pass every row through, like the source.
        _ => true,
    }
}

#[derive(Deserialize, Default)]
#[serde(default)]
struct OaSearchRow {
    symbol: Option<String>,
    name: Option<String>,
    exchange: Option<String>,
    instrumenttype: Option<String>,
    expiry: Option<String>,
    strike: Option<f64>,
}

fn map_search_rows(rows: Vec<OaSearchRow>, type_filter: Option<&str>) -> Vec<SymbolSearchResult> {
    rows.into_iter()
        .filter(|r| type_filter.map_or(true, |t| type_allows(t, r.instrumenttype.as_deref())))
        .map(|r| SymbolSearchResult {
            ticker: r.symbol.unwrap_or_default(),
            name: Some(display_name(
                r.name.as_deref().unwrap_or(""),
                &Descriptor { instrumenttype: r.instrumenttype.as_deref(), strike: r.strike, expiry: r.expiry.as_deref().unwrap_or("") },
            )),
            market: Some("stocks".into()),
            locale: None,
            exchange: Some(r.exchange.clone().unwrap_or_default()),
            r#type: Some(r.instrumenttype.unwrap_or_default()),
        })
        .collect()
}

// ── Market calendar (timings + holidays → per-exchange sessions) ─────────────
// The broker publishes real trading calendars:
//   POST /api/v1/market/timings  { date } → per-exchange open/close (epoch ms;
//     empty on weekends/full holidays; special-only on special-session days)
//   POST /api/v1/market/holidays { year } → { timezone, data: [{ date,
//     holiday_type, closed_exchanges[], open_exchanges[] }] }
// mapped onto the session grammar (session/subsessions/holidays/corrections;
// corrections win over holidays, so special timings ride as corrections).

#[derive(Deserialize, Default, Clone)]
#[serde(default)]
struct OaTiming {
    exchange: Option<String>,
    /// Epoch ms.
    start_time: Option<f64>,
    end_time: Option<f64>,
}

#[derive(Deserialize, Default, Clone)]
#[serde(default)]
struct OaHoliday {
    date: Option<String>,
    holiday_type: Option<String>,
    closed_exchanges: Option<Vec<String>>,
    open_exchanges: Option<Vec<OaTiming>>,
}

/// Standard regular session per exchange (broker docs table): the base spec;
/// the calendar only overrides it (holidays, special sessions). Bands mirror
/// the source's static session (NSE pre-open 15m, post 30m); unknown bands 0.
struct StdHours {
    open: &'static str,
    close: &'static str,
    pre: i32,
    post: i32,
}

fn standard_session(exchange: &str) -> StdHours {
    match exchange {
        "CDS" | "BCD" => StdHours { open: "0900", close: "1700", pre: 0, post: 0 },
        "MCX" => StdHours { open: "0900", close: "2330", pre: 0, post: 0 },
        // NSE, BSE, NFO, BFO and anything unlisted.
        _ => StdHours { open: "0915", close: "1530", pre: 15, post: 30 },
    }
}

/// Our venue prefix → broker exchange code for calendar matching (index
/// venues follow their cash market).
fn calendar_exchange(prefix: &str) -> String {
    let base = prefix.trim().to_uppercase();
    match base.strip_suffix("_INDEX") {
        Some(stripped) if !stripped.is_empty() => stripped.to_string(),
        _ if base.is_empty() => "NSE".into(),
        _ => base,
    }
}

/// "HHMM" of epoch-ms `ms` in `tz`.
fn ms_to_hhmm(ms: Option<f64>, tz: Tz) -> String {
    let Some(ms) = ms.filter(|v| v.is_finite()).map(|v| v as i64) else {
        return String::new();
    };
    let dt = Utc.timestamp_millis_opt(ms).single().unwrap_or_else(|| Utc::now());
    dt.with_timezone(&tz).format("%H%M").to_string()
}

/// "YYYY-MM-DD" → "YYYYMMDD".
fn iso_to_day(iso: &str) -> String {
    iso.replace('-', "")
}

fn open_exchange_hit<'a>(list: &'a Option<Vec<OaTiming>>, base: &str) -> Option<&'a OaTiming> {
    list.as_ref()?.iter().find(|o| o.exchange.as_deref().map(|e| e.trim().to_uppercase()).as_deref() == Some(base))
}

/// Build the session descriptor for one venue prefix from calendar rows.
/// Rules per (date, exchange): closed-listed (without an open entry) →
/// holidays; an open entry (or fetched special timings) → `spec:date`
/// corrections, which win over holidays; settlement holidays and unmentioned
/// exchanges are ignored (trading is open). Unknown special timings never
/// close a market.
fn build_exchange_session(
    prefix: &str,
    tz_name: &str,
    rows: &[OaHoliday],
    special_timings: &HashMap<String, Vec<OaTiming>>,
) -> SymbolSession {
    let tz = tz_name.parse::<Tz>().unwrap_or(chrono_tz::Asia::Kolkata);
    let base = calendar_exchange(prefix);
    let std = standard_session(&base);
    let to_min = |hhmm: &str| hhmm[..2].parse::<i32>().unwrap_or(0) * 60 + hhmm[2..].parse::<i32>().unwrap_or(0);
    let fmt = |m: i32| format!("{:02}{:02}", ((m / 60) % 24 + 24) % 24, ((m % 60) + 60) % 60);
    let rth = format!("{}-{}", std.open, std.close);
    let eth = if std.pre + std.post > 0 {
        format!("{}-{}", fmt(to_min(std.open) - std.pre), fmt(to_min(std.close) + std.post))
    } else {
        rth.clone()
    };
    let mut closed: Vec<String> = Vec::new();
    let mut specials: HashMap<String, BTreeSet<String>> = HashMap::new();
    let mut add_special = |spec: String, day: String, specials: &mut HashMap<String, BTreeSet<String>>| {
        specials.entry(spec).or_default().insert(day);
    };
    for h in rows {
        // Row dates must read "YYYY-MM-DD" (the source's own guard).
        let Some(date) = h.date.as_deref() else { continue };
        if date.len() != 10 || !date.as_bytes().iter().copied().all(|c| c.is_ascii_digit() || c == b'-') {
            continue;
        }
        let day = iso_to_day(date);
        if let Some(hit) = open_exchange_hit(&h.open_exchanges, &base) {
            let spec = format!("{}-{}", ms_to_hhmm(hit.start_time, tz), ms_to_hhmm(hit.end_time, tz));
            add_special(spec, day, &mut specials);
            continue;
        }
        let closed_list = h
            .closed_exchanges
            .as_ref()
            .map(|l| l.iter().map(|e| e.trim().to_uppercase()).collect::<Vec<_>>())
            .unwrap_or_default();
        if closed_list.iter().any(|e| e == &base) {
            closed.push(day);
            continue;
        }
        if h.holiday_type.as_deref().map(str::trim).map(|t| t.to_uppercase()).as_deref() == Some("SPECIAL_SESSION") {
            if let Some(timings) = special_timings.get(date) {
                let hit = timings
                    .iter()
                    .find(|t| t.exchange.as_deref().map(|e| e.trim().to_uppercase()).as_deref() == Some(base.as_str()));
                if let Some(hit) = hit {
                    let spec = format!("{}-{}", ms_to_hhmm(hit.start_time, tz), ms_to_hhmm(hit.end_time, tz));
                    add_special(spec, day, &mut specials);
                }
            }
            continue;
        }
        // SETTLEMENT_HOLIDAY and unmentioned exchanges: trading is open.
    }
    let corrections = specials
        .iter()
        .map(|(spec, days)| {
            format!("{spec}:{}", days.iter().map(String::as_str).collect::<Vec<_>>().join(","))
        })
        .collect::<Vec<_>>()
        .join(";");
    let mut subsessions = vec![Subsession::new("regular", "Regular Trading Hours", &rth)];
    if eth != rth {
        subsessions.push(Subsession::new("extended", "Extended Trading Hours", &eth));
        subsessions.push(Subsession::new(
            "premarket",
            "Premarket",
            &format!("{}-{}", fmt(to_min(std.open) - std.pre), std.open),
        ));
        subsessions.push(Subsession::new(
            "postmarket",
            "Postmarket",
            &format!("{}-{}", std.close, fmt(to_min(std.close) + std.post)),
        ));
    }
    let mut closed = closed;
    closed.sort();
    closed.dedup();
    SymbolSession {
        timezone: tz_name.to_string(),
        session: rth,
        subsessions,
        holidays: closed.join(","),
        corrections,
        pricescale: 100,
        minmov: 5,
        variable_tick_size: String::new(),
    }
}

/// Holidays (+response timezone) for a year, cached a day. Concurrent callers
/// share one in-flight fetch (a watchlist resolves dozens of symbols at once —
/// without this every symbol fires its own calls).
static HOLIDAYS: OnceLock<Mutex<HashMap<i32, (Instant, String, Vec<OaHoliday>)>>> = OnceLock::new();
/// Held across the network fetch, so it is the async mutex (see INTERVALS_GATE).
static CALENDAR_GATE: OnceLock<TokioMutex<()>> = OnceLock::new();

async fn fetch_holidays(base: &str, key: &str, year: i32) -> Result<(String, Vec<OaHoliday>)> {
    let map = HOLIDAYS.get_or_init(|| Mutex::new(HashMap::new()));
    if let Some((at, timezone, rows)) = map.lock().unwrap().get(&year) {
        if at.elapsed() < CACHE_TTL {
            return Ok((timezone.clone(), rows.clone()));
        }
    }
    let gate = CALENDAR_GATE.get_or_init(|| TokioMutex::new(()));
    let _guard = gate.lock().await;
    {
        let map = map.lock().unwrap();
        if let Some((at, timezone, rows)) = map.get(&year) {
            if at.elapsed() < CACHE_TTL {
                return Ok((timezone.clone(), rows.clone()));
            }
        }
    }
    let answer = post::<Vec<OaHoliday>>(base, key, "/api/v1/market/holidays", serde_json::json!({ "year": year })).await?;
    let timezone = match answer.extra.get("timezone").and_then(|v| v.as_str()) {
        Some(tz) if !tz.is_empty() => tz.to_string(),
        _ => "Asia/Kolkata".into(),
    };
    let rows = answer.data.unwrap_or_default();
    map.lock().unwrap().insert(year, (Instant::now(), timezone.clone(), rows.clone()));
    Ok((timezone, rows))
}

static TIMINGS: OnceLock<Mutex<HashMap<String, (Instant, Vec<OaTiming>)>>> = OnceLock::new();

/// Timings for one date (empty on holidays), cached a day. Shares the
/// calendar gate: the special-day timings ride the same year fetch.
async fn fetch_timings(base: &str, key: &str, date: &str) -> Result<Vec<OaTiming>> {
    let map = TIMINGS.get_or_init(|| Mutex::new(HashMap::new()));
    if let Some((at, rows)) = map.lock().unwrap().get(date) {
        if at.elapsed() < CACHE_TTL {
            return Ok(rows.clone());
        }
    }
    let gate = CALENDAR_GATE.get_or_init(|| TokioMutex::new(()));
    let _guard = gate.lock().await;
    {
        let map = map.lock().unwrap();
        if let Some((at, rows)) = map.get(date) {
            if at.elapsed() < CACHE_TTL {
                return Ok(rows.clone());
            }
        }
    }
    let answer = post::<Vec<OaTiming>>(base, key, "/api/v1/market/timings", serde_json::json!({ "date": date })).await?;
    let rows = answer.data.unwrap_or_default();
    map.lock().unwrap().insert(date.to_string(), (Instant::now(), rows.clone()));
    Ok(rows)
}

/// Per-exchange session descriptors, cached a day (current year; the next one
/// only in December, when January's specials need it).
static SESSIONS: OnceLock<Mutex<HashMap<String, (Instant, SymbolSession)>>> = OnceLock::new();

/// The calendar fetch of one prefix: years + special-day timings into one
/// descriptor. The current year always; the next only in December, when
/// January specials (and the countdown over New Year) need it — the rest of
/// the year one call suffices.
async fn build_fresh_session(base: &str, key: &str, prefix: &str) -> Result<SymbolSession> {
    let now = Utc::now();
    let this_year = now.year();
    let years: &[i32] = if now.month0() == 11 { &[this_year, this_year + 1] } else { &[this_year] };
    let mut ok: Vec<(String, Vec<OaHoliday>)> = Vec::new();
    for year in years {
        if let Ok(fetched) = fetch_holidays(base, key, *year).await {
            ok.push(fetched);
        }
    }
    if ok.is_empty() {
        bail!("OpenAlgo calendar unreachable");
    }
    let (timezone, rows_all) = ok.remove(0);
    let rows = ok.into_iter().fold(rows_all, |mut acc, (_, rows)| {
        acc.extend(rows);
        acc
    });
    let base_code = calendar_exchange(prefix);
    // Special days whose row carries no timings for this exchange: resolve via
    // the timings endpoint (a year holds a handful at most — bounded).
    let mut need_dates: Vec<String> = Vec::new();
    for h in &rows {
        if h.holiday_type.as_deref().map(|t| t.to_uppercase()).as_deref() != Some("SPECIAL_SESSION") {
            continue;
        }
        let Some(date) = h.date.as_deref() else { continue };
        if need_dates.iter().any(|d| d == date) {
            continue;
        }
        let closed_list = h
            .closed_exchanges
            .as_ref()
            .map(|l| l.iter().map(|e| e.trim().to_uppercase()).collect::<Vec<_>>())
            .unwrap_or_default();
        let open_hit = open_exchange_hit(&h.open_exchanges, &base_code);
        if closed_list.iter().any(|e| e == &base_code) || open_hit.is_some() {
            continue;
        }
        need_dates.push(date.to_string());
    }
    need_dates.sort();
    let mut special_timings: HashMap<String, Vec<OaTiming>> = HashMap::new();
    for date in need_dates.iter().take(12) {
        // Unknown timings — the regular session stands.
        if let Ok(rows) = fetch_timings(base, key, date).await {
            special_timings.insert(date.clone(), rows);
        }
    }
    Ok(build_exchange_session(prefix, &timezone, &rows, &special_timings))
}

/// Session of the OpenAlgo feed built from the broker calendar. When the
/// calendar is unreachable, serves the last-known calendar rather than the
/// static session whenever one was ever fetched; the static NSE session as the
/// last-resort fallback (the source's callers do the same).
async fn symbol_session_of(prefix: &str, base: &str, key: &str) -> SymbolSession {
    let map = SESSIONS.get_or_init(|| Mutex::new(HashMap::new()));
    let key_upper = prefix.trim().to_uppercase();
    let cached = map.lock().unwrap().get(&key_upper).cloned();
    if let Some((at, session)) = &cached {
        // `filter` would consume `cached` (the fallback below still wants it).
        if at.elapsed() < CACHE_TTL {
            return session.clone();
        }
    }
    match build_fresh_session(base, key, &key_upper).await {
        Ok(session) => {
            map.lock().unwrap().insert(key_upper, (Instant::now(), session.clone()));
            session
        }
        Err(e) => {
            eprintln!("[openalgo] calendar {key_upper}: {e:#}");
            if let Some((_, session)) = cached {
                session
            } else {
                static_nse_session()
            }
        }
    }
}

/// The static fallback session (NSE equities), the source's last-resort
/// `SESSION`: 09:15-15:30 IST, 15m pre-open, 30m post.
fn static_nse_session() -> SymbolSession {
    SymbolSession {
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
        pricescale: 100,
        minmov: 5,
        variable_tick_size: String::new(),
    }
}

// ── Quotes (snapshot + live) ─────────────────────────────────────────────────

#[derive(Deserialize, Default, Clone)]
#[serde(default)]
struct OaQuote {
    open: Option<f64>,
    high: Option<f64>,
    low: Option<f64>,
    ltp: Option<f64>,
    /// Absent on some REST answers — the baseline falls back through it.
    prev_close: Option<f64>,
    /// WS ticks carry the previous day's close under this key (no
    /// prev_close field exists on them at all).
    close: Option<f64>,
    volume: Option<f64>,
}

/// Last known good previous-close per symbol (WS ticks often omit it).
/// Baseline priority: payload prev_close → remembered → day open → none
/// (change 0, never NaN).
fn prev_close_map() -> &'static Mutex<HashMap<String, f64>> {
    static PREV_CLOSES: OnceLock<Mutex<HashMap<String, f64>>> = OnceLock::new();
    PREV_CLOSES.get_or_init(|| Mutex::new(HashMap::new()))
}

fn remember_prev_close(symbol: &str, prev: Option<f64>) {
    if let Some(prev) = prev.filter(|v| v.is_finite() && *v > 0.0) {
        prev_close_map().lock().unwrap().insert(symbol.to_uppercase(), prev);
    }
}

fn baseline_for(symbol: &str, prev_close: Option<f64>, open: Option<f64>) -> Option<f64> {
    if let Some(pc) = prev_close.filter(|v| v.is_finite() && *v > 0.0) {
        return Some(pc);
    }
    if let Some(cached) = prev_close_map().lock().unwrap().get(&symbol.to_uppercase()).copied() {
        return Some(cached);
    }
    open.filter(|v| v.is_finite() && *v > 0.0)
}

/// Regular-session change vs the baseline: `ltp` vs the closest previous
/// close there is (never NaN).
fn change_for(ltp: Option<f64>, prev: Option<f64>) -> f64 {
    let (ltp, prev) = (ltp.filter(|v| v.is_finite()), prev.filter(|v| v.is_finite()));
    match (ltp, prev) {
        (Some(ltp), Some(prev)) => ltp - prev,
        _ => 0.0,
    }
}

/// The detail header's snapshot from one `/quotes` answer. Cache genuine prev
/// closes only (never open-fallbacks, to avoid locking in the wrong baseline
/// for the rest of the session).
fn shape_snapshot(symbol: &str, q: &OaQuote) -> Snapshot {
    remember_prev_close(symbol, q.prev_close);
    let prev = baseline_for(symbol, q.prev_close.or(q.close), q.open);
    let change = change_for(q.ltp, prev);
    Snapshot {
        ticker: symbol.to_string(),
        last: q.ltp.unwrap_or(0.0),
        change,
        change_percent: prev.filter(|p| *p > 0.0).map_or(0.0, |p| change / p * 100.0),
        day_high: q.high.unwrap_or(0.0),
        day_low: q.low.unwrap_or(0.0),
        day_volume: q.volume.unwrap_or(0.0),
        ext_change_percent: None,
        source: "live".into(),
        updated_ms: Utc::now().timestamp_millis() as f64,
    }
}

// ── Live (phase 1): the REST snapshot poller ─────────────────────────────────
// The direct sibling of `quote_poll` (see the gateway's `spawn`): the same
// subscription plumbing, event types and poll cadence, against `/quotes`.
// `/quotes` answers one symbol per call, so each cycle walks the union of
// every watchlist and charted symbol one POST each — a local server, so
// the cost is beside the point.

/// How often to poll (a UI-freshness choice; `quote_poll` uses the same). The
/// cadence is NOT the tick stream the source's page keeps — the WebSocket
/// (phase 2) replaces small-grain tick shapes.
const POLL_INTERVAL: Duration = Duration::from_secs(5);

/// Shortest wait between two immediate polls asked by subscription changes.
const MIN_GAP: Duration = Duration::from_secs(1);

struct Poller {
    host: HostRef,
    base: String,
    key: String,
    /// Desired state, declared per owner by the frontend via the realtime
    /// commands. Symbols are the full names ("NSE:RELIANCE") the OpenAlgo
    /// server itself names — no ticker-case transformation (the broker's
    /// ticker is the app's ticker).
    state: SubscriptionState,
    /// Last state of each symbol polled: what a view subscribing to an
    /// already-polled symbol gets at once (replayed), and where the forming
    /// minute bucket lives.
    last: HashMap<String, LastQuote>,
    /// Symbols the last immediate poll covered.
    polled: HashSet<String>,
    last_poll: Option<tokio::time::Instant>,
}

/// Everything the poller keeps per symbol: the last answer (what a fresh
/// subscriber is replayed), the day-volume chain, and the forming minute
/// bar with its volume-tracking state.
struct LastQuote {
    quote: OaQuote,
    /// Cumulative day volume, carried through volume-less answers (the
    /// source's lastDayVol fallback).
    day_vol: Option<f64>,
    /// The forming minute bar; it rolls onto a new minute with its start.
    formed: Option<FormingBar>,
    /// The forming bucket's day volume at its first tick with a volume; the
    /// bucket's own volume is the delta since.
    vol_base: Option<f64>,
}

impl LastQuote {
    /// First sighting of a symbol: the answer stored, no bucket yet.
    fn new(q: &OaQuote) -> Self {
        Self { quote: q.clone(), day_vol: None, formed: None, vol_base: None }
    }
}

/// The forming minute bar of a charted symbol.
struct FormingBar {
    start: f64,
    open: f64,
    high: f64,
    low: f64,
}

impl RealtimeProvider for OpenAlgoProvider {
    fn spawn(&self, host: HostRef) -> WsHandle {
        let (tx, rx) = mpsc::channel::<SubscribeMsg>(32);
        // `rt::spawn`, not `tokio::spawn`: called from the app's setup hook,
        // outside any ambient Tokio runtime context (same note as
        // `market_stream::spawn_mode`).
        let (base, key) = (self.base.clone(), self.key.clone());
        crate::rt::spawn(async move {
            run_poller(host, base, key, rx).await;
        });
        WsHandle { tx }
    }
}

async fn run_poller(host: HostRef, base: String, key: String, mut rx: mpsc::Receiver<SubscribeMsg>) {
    let mut p =
        Poller { host, base, key, state: SubscriptionState::default(), last: HashMap::new(), polled: HashSet::new(), last_poll: None };
    let mut ticker = interval(POLL_INTERVAL);
    // If a poll cycle runs long, resume the cadence from now, no bursts.
    ticker.set_missed_tick_behavior(MissedTickBehavior::Delay);

    loop {
        tokio::select! {
            _ = ticker.tick() => {
                p.poll().await;
            }
            msg = rx.recv() => {
                let Some(msg) = msg else { return; }; // mpsc closed → app shutting down
                // Every update already queued is part of the same change.
                let mut burst = vec![msg];
                while let Ok(m) = rx.try_recv() {
                    burst.push(m);
                }
                for m in burst {
                    p.replay(&m);
                    p.state.apply(m);
                }
                // A symbol not polled yet: poll now so the view populates
                // without waiting up to POLL_INTERVAL.
                if !p.state.all_symbols().is_subset(&p.polled) {
                    if let Some(t) = p.last_poll {
                        tokio::time::sleep_until(t + MIN_GAP).await;
                    }
                    p.poll().await;
                }
            }
        }
    }
}

impl Poller {
    /// The last values of the symbols `msg` subscribes, to that owner only,
    /// for the symbols already polled (the others come with the next poll).
    fn replay(&self, msg: &SubscribeMsg) {
        match msg {
            SubscribeMsg::SetChart { owner, symbol: Some(symbol) } => {
                if let Some(st) = self.last.get(symbol) {
                    if let Some(e) = tick_of(symbol, &st.quote) {
                        let _ = e.emit_to(&self.host, owner);
                    }
                    if let Some(e) = chart_bar_of(symbol, st) {
                        let _ = e.emit_to(&self.host, owner);
                    }
                }
            }
            SubscribeMsg::SetWatchlist { owner, symbols } => {
                for symbol in symbols {
                    if let Some(st) = self.last.get(symbol.as_str()) {
                        if let Some(e) = tick_of(symbol, &st.quote) {
                            let _ = e.emit_to(&self.host, owner);
                        }
                    }
                }
            }
            _ => {}
        }
    }

    /// One `/quotes` answer absorbed: the alert engine's sample and the
    /// watchlist tick, then — charted symbols only — the forming minute
    /// bucket and the day bar. Emits broadcast under the subscribed full
    /// name; the frontend filters by symbol (like `quote_poll`).
    fn absorb(&mut self, symbol: &str, q: &OaQuote) {
        remember_prev_close(symbol, q.prev_close);
        // The host's alert rules read every quote (the rules read the day
        // side; there is no minute bar here). The tick broadcasts when it
        // carries a price.
        self.host.on_quote(symbol, q.ltp.unwrap_or(0.0), None);
        if let Some(tick) = tick_of(symbol, q) {
            let _ = tick.emit(&self.host);
        }

        // "moved" is measured against the answer before this one, then the
        // stored quote is replaced. After hours every answer repeats
        // itself — without this gate the poll would repaint flat bars
        // forever. A volume-less answer runs the day side on the last known
        // volume (the source's lastDayVol).
        let (prev_ltp, prev_day_vol) = match self.last.get(symbol) {
            Some(st) => (st.quote.ltp, st.day_vol),
            None => (None, None),
        };
        let day_vol = q.volume.filter(|v| v.is_finite()).or(prev_day_vol);
        let moved = q.ltp != prev_ltp || day_vol != prev_day_vol;

        let charted = self.state.is_chart_symbol(symbol);
        let st = match self.last.entry(symbol.to_string()) {
            Occupied(mut e) => {
                e.get_mut().quote = q.clone();
                e.into_mut()
            }
            Vacant(e) => e.insert(LastQuote::new(q)),
        };
        st.day_vol = day_vol;

        // Chart-only work below: the minute bucket and day bar exist to
        // paint chart bars. Watch-only symbols (quotes/alerts) skip all of
        // it — they only need the day-side quote numbers, never bucket
        // volumes (as the source's isCharted gate).
        let mut agg = None;
        if charted {
            if let Some(ltp) = q.ltp.filter(|v| v.is_finite() && *v > 0.0) {
                let now = Utc::now().timestamp() as f64;
                let start = (now / 60.0).floor() * 60.0;
                let rolled = st.formed.as_ref().map_or(true, |f| f.start != start);
                if rolled {
                    st.formed = Some(FormingBar { start, open: ltp, high: ltp, low: ltp });
                }
                let volume = track_bucket_volume(st, rolled, day_vol, q.volume);
                let (open, high, low) = {
                    let f = st.formed.as_mut().unwrap();
                    f.high = f.high.max(ltp);
                    f.low = f.low.min(ltp);
                    (f.open, f.high, f.low)
                };
                let day = Candle {
                    time: now,
                    // 0-free day side: a missing open/high/low falls back to
                    // the bucket's extremes rather than printing a 0 on the
                    // daily axis.
                    open: q.open.filter(|v| *v > 0.0).unwrap_or(open),
                    high: q.high.filter(|v| *v > 0.0).unwrap_or(high),
                    low: q.low.filter(|v| *v > 0.0).unwrap_or(low),
                    close: ltp,
                    volume: day_vol.unwrap_or(0.0),
                };
                agg = Some(ChartAggregate {
                    symbol: symbol.to_string(),
                    time: start,
                    open,
                    high,
                    low,
                    close: ltp,
                    volume,
                    day: Some(day),
                });
            }
        }
        if moved {
            if let Some(a) = agg {
                let _ = a.emit(&self.host);
            }
        }
    }

    async fn poll(&mut self) {
        // The union of everything we care about, each under its own
        // `/quotes` call — the endpoint serves one symbol per request. Walked
        // sequentially (the answer ordering keeps the day-volume chains of
        // two-listing Tickers clean); the events go back under the
        // subscribed full name.
        let symbols: HashSet<String> = self.state.all_symbols();
        self.last.retain(|k, _| symbols.contains(k));
        self.polled = symbols.clone();
        if symbols.is_empty() {
            return;
        }
        self.last_poll = Some(tokio::time::Instant::now());
        // One failure logged per cycle, not one line per subscribed symbol.
        let mut first_error = None;
        for symbol in symbols {
            let (exchange, ticker) = split_open_algo(&symbol);
            let answer = post::<OaQuote>(
                &self.base,
                &self.key,
                "/api/v1/quotes",
                serde_json::json!({"symbol": ticker, "exchange": exchange}),
            )
            .await;
            match answer {
                Ok(answer) => {
                    if let Some(q) = answer.data {
                        self.absorb(&symbol, &q);
                    }
                }
                Err(e) => first_error = first_error.or(Some(format!("{e:#}"))),
            }
        }
        if let Some(e) = first_error {
            eprintln!("[openalgo] quote poll: first failure was {e}");
        }
    }
}

/// The watchlist quote of one `/quotes` answer: last + change / Chg% / Vol.
/// None without a price — a watchlist row doesn't blank out on an
/// after-hours answer, it just stops moving.
fn tick_of(symbol: &str, q: &OaQuote) -> Option<TradeTick> {
    let ltp = q.ltp.filter(|v| v.is_finite() && *v > 0.0)?;
    let prev = baseline_for(symbol, q.prev_close.or(q.close), q.open);
    let change = change_for(Some(ltp), prev);
    Some(TradeTick {
        symbol: symbol.to_string(),
        price: ltp,
        change: Some(change),
        change_percent: Some(prev.filter(|p| *p > 0.0).map_or(0.0, |p| change / p * 100.0)),
        ext_change_percent: None,
        volume: q.volume,
        source: Some("live".into()),
        trade_price: None,
        trade_time: None,
    })
}

/// The forming bucket's own volume tracker: a fresh (or rolled) minute takes
/// its base from the day volume of its first answer that carries one; a
/// volume-less roll tick delays the base rather than pouring the pre-bucket
/// gap into this bar. The delta never goes below 0.
fn track_bucket_volume(
    st: &mut LastQuote,
    rolled: bool,
    day_vol: Option<f64>,
    q_vol: Option<f64>,
) -> f64 {
    if rolled || st.vol_base.is_none() {
        st.vol_base = q_vol.filter(|v| v.is_finite());
    }
    st.vol_base
        .zip(day_vol)
        .map_or(0.0, |(base, day)| (day - base).max(0.0))
}

/// The chart bar of a stored answer (a fresh chart view's replay): the
/// forming minute bucket plus today's full day bar. None before the first
/// priced answer formed a bucket — the next poll paints.
fn chart_bar_of(symbol: &str, st: &LastQuote) -> Option<ChartAggregate> {
    let f = st.formed.as_ref()?;
    let ltp = st.quote.ltp.filter(|v| v.is_finite() && *v > 0.0)?;
    Some(ChartAggregate {
        symbol: symbol.to_string(),
        time: f.start,
        open: f.open,
        high: f.high,
        low: f.low,
        close: ltp,
        volume: bucket_volume(st),
        day: Some(day_candle_of(st)),
    })
}

/// The stored answer's forming-bucket volume (see `track_bucket_volume`).
fn bucket_volume(st: &LastQuote) -> f64 {
    st.vol_base
        .zip(st.day_vol)
        .map_or(0.0, |(base, day)| (day - base).max(0.0))
}

/// Today's day bar of a stored answer: the 0-free day side with the bucket's
/// own extremes as the fallback (a missing quote side must not paint a 0 on
/// the daily axis), the ltp as close, the day volume.
fn day_candle_of(st: &LastQuote) -> Candle {
    let (open, high, low) = st
        .formed
        .as_ref()
        .map_or((0.0, 0.0, 0.0), |f| (f.open, f.high, f.low));
    let q = &st.quote;
    Candle {
        time: Utc::now().timestamp() as f64,
        open: q.open.filter(|v| *v > 0.0).unwrap_or(open),
        high: q.high.filter(|v| *v > 0.0).unwrap_or(high),
        low: q.low.filter(|v| *v > 0.0).unwrap_or(low),
        close: q.ltp.unwrap_or(0.0),
        volume: st.day_vol.unwrap_or(0.0),
    }
}

#[async_trait::async_trait]
impl HistoryProvider for OpenAlgoProvider {
    async fn daily_aggs(
        &self,
        sym: &SymbolRef,
        from: NaiveDate,
        to: NaiveDate,
        adjusted: bool,
    ) -> Result<Vec<Candle>> {
        // `/history` carries no adjust flag (the capability says so), so the
        // bars are as the broker serves them either way.
        let _ = adjusted;
        let (exchange, ticker) = split_open_algo(&sym.full());
        let bars = fetch_history(&self.base, &self.key, &ticker, &exchange, "D", from, to).await?;
        Ok(bars.into_iter().map(snap_day_bar).collect())
    }

    async fn minute_aggs(
        &self,
        sym: &SymbolRef,
        mult: u32,
        from: NaiveDate,
        to: NaiveDate,
        _adjusted: bool,
    ) -> Result<Vec<Candle>> {
        let (exchange, ticker) = split_open_algo(&sym.full());
        fetch_intervals(&self.base, &self.key).await;
        if mult == 240 {
            // No 4h interval: aggregate from 2h.
            if !minute_table().contains_key(&120) {
                bail!("OpenAlgo: 240m needs the broker 2h interval, which is not served");
            }
            return Ok(aggregate_2h(
                fetch_history(&self.base, &self.key, &ticker, &exchange, "2h", from, to).await?,
            ));
        }
        let oa = resolve_interval("minute", mult)?;
        fetch_history(&self.base, &self.key, &ticker, &exchange, &oa, from, to).await
    }

    async fn second_aggs(
        &self,
        sym: &SymbolRef,
        mult: u32,
        from: NaiveDate,
        to: NaiveDate,
        _adjusted: bool,
    ) -> Result<Vec<Candle>> {
        let (exchange, ticker) = split_open_algo(&sym.full());
        fetch_intervals(&self.base, &self.key).await;
        let oa = resolve_interval("second", mult)?;
        fetch_history(&self.base, &self.key, &ticker, &exchange, &oa, from, to).await
    }

    /// Seconds live tail: bars newer than `since_sec` — the seconds chart's
    /// refresh loop (the WebSocket's second bars are phase 2).
    async fn second_tail(
        &self,
        sym: &SymbolRef,
        mult: u32,
        since_sec: f64,
        _adjusted: bool,
    ) -> Result<Vec<Candle>> {
        let (exchange, ticker) = split_open_algo(&sym.full());
        fetch_intervals(&self.base, &self.key).await;
        let oa = resolve_interval("second", mult)?;
        let from = chrono::DateTime::from_timestamp(since_sec as i64, 0)
            .map_or_else(Utc::now, |d| d)
            .date_naive();
        let to = Utc::now().date_naive();
        let out =
            fetch_history(&self.base, &self.key, &ticker, &exchange, &oa, from, to).await?;
        Ok(out.into_iter().filter(|b| b.time > since_sec).collect())
    }
}

#[async_trait::async_trait]
impl ReferenceProvider for OpenAlgoProvider {
    async fn ticker_info(&self, sym: &SymbolRef) -> Result<TickerInfo> {
        let (exchange, ticker) = split_open_algo(&sym.full());
        // The profile (sector/industry) comes from a separate public API;
        // both requests fire together (the source's Promise.all).
        let (answer, profile) = tokio::join!(
            post::<OaSymbolInfo>(
                &self.base,
                &self.key,
                "/api/v1/symbol",
                serde_json::json!({"symbol": ticker, "exchange": exchange}),
            ),
            fetch_company_profile(&ticker),
        );
        let d = answer?
            .data
            .ok_or_else(|| anyhow!("OpenAlgo /api/v1/symbol: no data for {ticker}"))?;
        Ok(shape_ticker_info(&ticker, &exchange, &d, profile.as_ref()))
    }

    /// Trading sessions from the broker calendar (timings + holidays, special
    /// sessions included); never an error — the static session stands in when
    /// the calendar can't be reached.
    async fn symbol_session(&self, sym: &SymbolRef) -> Result<SymbolSession> {
        let (exchange, _) = split_open_algo(&sym.full());
        Ok(symbol_session_of(&exchange, &self.base, &self.key).await)
    }

    async fn ticker_snapshot(&self, sym: &SymbolRef) -> Result<Snapshot> {
        let (exchange, ticker) = split_open_algo(&sym.full());
        let answer =
            post::<OaQuote>(&self.base, &self.key, "/api/v1/quotes", serde_json::json!({"symbol": ticker, "exchange": exchange})).await?;
        let data = answer
            .data
            .ok_or_else(|| anyhow!("OpenAlgo /api/v1/quotes: no data for {ticker}"))?;
        // The snapshot keys on the bare ticker (the source's tickerSnapshot).
        Ok(shape_snapshot(&ticker, &data))
    }

    async fn search(&self, query: &str, type_filter: Option<&str>) -> Result<Vec<SymbolSearchResult>> {
        let q = query.trim();
        if q.is_empty() {
            return Ok(Vec::new());
        }
        let answer = post::<Vec<OaSearchRow>>(&self.base, &self.key, "/api/v1/search", serde_json::json!({ "query": q })).await?;
        Ok(map_search_rows(answer.data.unwrap_or_default(), type_filter))
    }

    /// The broker serves no corporate-action lists — the source answers the
    /// same (`dividends`/`splits` resolve to a bare `[]`), so the adjustment
    /// toggle hides and charts show as-traded prices.
    async fn dividends(&self, _sym: &SymbolRef) -> Result<Vec<DividendEvent>> {
        Ok(Vec::new())
    }

    async fn splits(&self, _sym: &SymbolRef) -> Result<Vec<SplitEvent>> {
        Ok(Vec::new())
    }
    /// No news — the trait's default empty list (the source's news lookup is
    /// also a bare list).
    async fn latest_news(&self, _sym: &SymbolRef, _limit: u32) -> Vec<crate::data::types::NewsItem> {
        Vec::new()
    }

    /// The branding icon for the ticker-icon proxy: only the dharun logos host
    /// decode, fetch, serve — same restriction as the gateway's `fetch_icon`
    /// (so the handler can't be hired into an open proxy).
    async fn icon(&self, encoded: &str) -> Result<(Vec<u8>, String)> {
        let bare = percent_decode_str(encoded)
            .decode_utf8()
            .context("ticker-icon: path is not valid percent-encoded UTF-8")?;
        const LOGOS: &str = "https://api.dharun.dev/v1/logos/";
        if !bare.starts_with(LOGOS) {
            bail!("ticker-icon: refusing a URL outside the logos host");
        }
        let resp = REST.get().get(bare.as_ref()).send().await?;
        if !resp.status().is_success() {
            bail!("ticker logo: HTTP {}", resp.status().as_u16());
        }
        let content_type = resp
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("image/png")
            .to_string();
        let bytes = resp.bytes().await?;
        Ok((bytes.to_vec(), content_type))
    }
}

#[async_trait::async_trait]
impl DataProvider for OpenAlgoProvider {
    fn name(&self) -> &'static str {
        NAME
    }

    fn capabilities(&self) -> ProviderCapabilities {
        // Served sizes: the broker's own interval list when fetched (the
        // 24h cache — no I/O here beyond a map read); the verified fallback
        // table otherwise. 240m is synthesized server-side from 2h, so it is
        // advertised only when 2h is.
        let mut minutes: Vec<u32> = minute_table().keys().copied().collect();
        minutes.sort();
        if minutes.contains(&120) && !minutes.contains(&240) {
            minutes.push(240);
            minutes.sort();
        }
        let mut seconds: Vec<u32> = second_table().keys().copied().collect();
        seconds.sort();
        ProviderCapabilities {
            name: self.name().to_string(),
            resolutions: ResolutionCaps {
                seconds,
                minutes,
                daily: daily_served(),
                weekly_monthly_from_daily: true,
            },
            max_bars_per_request: 50_000,
            // `/history` has no adjust flag; prices adjusted_toggle off.
            adjusted_toggle: false,
            // The broker has no extended-hours bars (india's session has no
            // pre/post feed), unlike the gateway.
            extended_hours: false,
            reference: ReferenceCaps {
                search: true,
                search_type_filter: true,
                snapshot: true,
                dividends: false,
                splits: false,
                news: false,
                icons: true,
            },
            entitlements: None,
        }
    }

    /// Fingerprint of the configured credential (never the secret itself), or
    /// `None` — a missing host or key leaves nothing for the client to probe.
    fn credential_id(&self) -> Option<String> {
        if self.base.is_empty() || self.key.is_empty() {
            return None;
        }
        Some(entitlements::fingerprint(&format!("openalgo|{}|{}", self.base, self.key)))
    }

    /// The broker has no plan system: it streams everything it serves. One
    /// `/intervals` probe (the history half of the capabilities' timeline)
    /// costs nothing and refreshes the served sizes; the floors keep the
    /// app's scroll-back inside the source's actual retention (30 calendar
    /// days intraday, 365 daily).
    async fn probe_history(&self) -> Result<HistoryProbe> {
        let _ = fetch_intervals(&self.base, &self.key).await;
        let floor_date = |days_back: i64| {
            Utc::now()
                .date_naive()
                .checked_sub_days(Days::new(days_back as u64))
                .map(fmt_date)
        };
        Ok(HistoryProbe {
            data_status: DataStatus::Streaming,
            raw_status: "streaming".into(),
            delay_sec: 0,
            floor: HistoryFloor { second: floor_date(30), minute: floor_date(30), day: floor_date(365) },
        })
    }

    /// The phase-1 live feed is the REST poller — no push channels. The
    /// broker's WebSocket (minute bars + trades) is the phase-2 transport;
    /// when it lands those caps flip true.
    async fn probe_stream(&self, _data_status: DataStatus) -> Result<StreamCaps> {
        Ok(StreamCaps { minute_bars: false, second_bars: false, trades: false, quotes: false })
    }
}

// ── Tests (no network) ───────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn symbols_split_like_the_source() {
        assert_eq!(split_open_algo("NSE:SBIN"), ("NSE".into(), "SBIN".into()));
        // Bare tickers default to NSE.
        assert_eq!(split_open_algo("sbin"), ("NSE".into(), "SBIN".into()));
        // The head of a comma list is the ticker ("RELIANCE,EQ" forms).
        assert_eq!(
            split_open_algo("NFO:GOLD,2026-12-04,XXL"),
            ("NFO".into(), "GOLD".into())
        );
        // Index venues keep their underscore code.
        assert_eq!(
            split_open_algo("NSE_INDEX:NIFTY"),
            ("NSE_INDEX".into(), "NIFTY".into())
        );
    }

    #[test]
    fn interval_tokens_parse() {
        // Minute tokens (verified live table: "1m"–"2h").
        assert_eq!(minute_token_to_mult("1m"), Some(1));
        assert_eq!(minute_token_to_mult("3m"), Some(3));
        assert_eq!(minute_token_to_mult("15min"), Some(15));
        assert_eq!(minute_token_to_mult("2h"), Some(120));
        assert_eq!(minute_token_to_mult("1h"), Some(60));
        assert_eq!(minute_token_to_mult("1"), Some(1));
        assert_eq!(minute_token_to_mult("D"), None);
        assert_eq!(minute_token_to_mult("1s"), None);
        // Second tokens.
        assert_eq!(second_token_to_mult("1s"), Some(1));
        assert_eq!(second_token_to_mult("5s"), Some(5));
        assert_eq!(second_token_to_mult("10sec"), Some(10));
        assert_eq!(second_token_to_mult("5m"), None);
        assert_eq!(second_token_to_mult("D"), None);
    }

    #[test]
    fn intervals_normalize_from_a_broker_payload() {
        let data: OaIntervals = serde_json::from_str(
            r#"{
              "minutes": ["1m","3m","5m","15m"], "hours": ["1h","2h"],
              "seconds": ["1s","5s"], "days": ["D"],
              "unknown": ["garbage"]
            }"#,
        )
        .unwrap();
        let n = normalize_intervals(&data);
        assert!(n.minutes.contains_key(&1) && n.minutes.contains_key(&120));
        assert_eq!(n.minutes.get(&1).unwrap(), "1m");
        assert_eq!(n.seconds, vec![1, 5]);
        assert_eq!(n.second_tokens.get(&5).unwrap(), "5s");
        assert!(n.daily);
    }

    #[test]
    fn bad_interval_payloads_normalize_to_empty_or_daily() {
        let data: OaIntervals = serde_json::from_str(r#"{"minutes": {}}"#).unwrap();
        let n = normalize_intervals(&data);
        assert!(n.minutes.is_empty());
        assert!(!n.daily);
    }

    #[test]
    fn history_windows_chunk_to_the_broker_caps() {
        let from = NaiveDate::from_ymd_opt(2026, 10, 1).unwrap();
        let to = NaiveDate::from_ymd_opt(2026, 12, 31).unwrap();
        // Intraday: 30-date windows.
        let chunks = chunk_ranges(from, to, 30);
        assert_eq!(chunks.len(), 3);
        assert_eq!(chunks[0], (from, NaiveDate::from_ymd_opt(2026, 10, 30).unwrap()));
        assert_eq!(*chunks.last().unwrap(), (NaiveDate::from_ymd_opt(2026, 12, 22).unwrap(), to));
        // Daily: 365-date windows — the range stays one.
        let chunks = chunk_ranges(NaiveDate::from_ymd_opt(2025, 1, 1).unwrap(), to, 365);
        assert_eq!(chunks.len(), 2);
        // A short range: one window, exact bounds.
        let chunks = chunk_ranges(from, NaiveDate::from_ymd_opt(2026, 10, 3).unwrap(), 30);
        assert_eq!(chunks, vec![(from, NaiveDate::from_ymd_opt(2026, 10, 3).unwrap())]);
    }

    #[test]
    fn aggregates_2h_into_4h_buckets() {
        let mk = |time: f64, c: f64| Candle { time, open: c, high: c, low: c, close: c, volume: 10.0 };
        let bars = vec![
            mk(0.0, 1.0),
            mk(7200.0, 2.0),   // same 4h bucket
            mk(14400.0, 3.0),  // next bucket
            mk(14400.0 + 3600.0, 4.0),
        ];
        let out = aggregate_2h(bars);
        assert_eq!(out.len(), 2);
        assert_eq!((out[0].open, out[0].close, out[0].volume), (1.0, 2.0, 20.0));
        assert_eq!((out[1].open, out[1].close, out[1].volume), (3.0, 4.0, 20.0));
        // 4h bucket floor: a 1h bar inside the bucket keeps its floor time.
        assert_eq!(out[1].time, 14400.0);
    }

    #[test]
    fn day_bars_snap_to_ist_midnight() {
        let utc_midnight = NaiveDate::from_ymd_opt(2026, 10, 8).unwrap().and_hms_opt(0, 0, 0).unwrap().and_utc().timestamp() as f64;
        // Broker's stamp for Oct-8 (verified live: 2026-10-08T00:00Z).
        let bar = Candle { time: utc_midnight, open: 1.0, high: 1.0, low: 1.0, close: 1.0, volume: 1.0 };
        assert_eq!(snap_day_bar(bar.clone()).time, utc_midnight - 19_800.0);
        // Already-snapped bars stay put.
        assert_eq!(snap_day_bar(snap_day_bar(bar)).time, utc_midnight - 19_800.0);
    }

    #[test]
    fn display_names_read_the_contract_parts() {
        let mk = |instrumenttype: Option<&str>, strike: Option<f64>, expiry: &str| Descriptor {
            instrumenttype,
            strike,
            expiry,
        };
        // Stock: the plain name.
        assert_eq!(display_name("Reliance Industries Ltd.", &mk(Some("EQ"), None, "")), "Reliance Industries Ltd.");
        // Contract: Name Strike Type Expiry.
        assert_eq!(display_name("NIFTY", &mk(Some("CE"), Some(26100.0), "30-Oct-26")), "NIFTY 26100 CE 30-Oct-26");
        // Missing parts skipped; negative strikes read absent.
        assert_eq!(display_name("NIFTY", &mk(Some("FUT"), Some(-1.0), "30-Oct-26")), "NIFTY FUT 30-Oct-26");
        assert_eq!(display_name("NIFTY", &mk(None, None, "01-Jan-27")), "NIFTY 01-Jan-27");
    }

    #[test]
    fn search_rows_filter_by_type() {
        let row = |symbol: Option<&str>, kind: Option<&str>| OaSearchRow {
            symbol: symbol.map(str::to_string),
            name: Some("the name".into()),
            exchange: Some("NSE".into()),
            instrumenttype: kind.map(str::to_string),
            expiry: None,
            strike: None,
        };
        let rows = vec![row(Some("RELIANCE"), Some("EQ")), row(Some("NIFTY"), Some("INDEX")), row(Some("NIFTYFUT"), Some("FUT"))];
        // No filter: everything through.
        assert_eq!(map_search_rows(rows.clone(), None).len(), 3);
        // IX accepts INDEX entries (verified live: the API answers INDEX).
        assert_eq!(map_search_rows(rows.clone(), Some("IX")).len(), 1);
        // EQ accepts the empty instrumenttype the /symbol endpoint answers.
        let empty_kind = vec![row(Some("BARE"), Some("")), row(Some("OPT"), Some("PE"))];
        assert_eq!(map_search_rows(empty_kind, Some("EQ")).len(), 1);
        // Unknown codes pass everything.
        assert_eq!(map_search_rows(rows, Some("ZZ")).len(), 3);
    }

    #[test]
    fn sessions_build_from_a_calendar() {
        // Epoch-ms fixtures that read naturally in IST: 14 400 000 ms =
        // 04:00 UTC = 09:30 IST, 18 000 000 ms = 05:00 UTC = 10:30 IST.
        let timings = |exchange: &str| OaTiming {
            exchange: Some(exchange.into()),
            start_time: Some(14_400_000.0),
            end_time: Some(18_000_000.0),
        };
        let mut specials: HashMap<String, Vec<OaTiming>> = HashMap::new();
        // A special day whose timings arrive on the /timings call instead.
        specials.insert("2026-10-25".into(), vec![timings("NSE")]);
        let rows = vec![
            // Full holiday: NSE listed in closed_exchanges.
            OaHoliday {
                date: Some("2026-10-02".into()),
                holiday_type: Some("TRADING_HOLIDAY".into()),
                closed_exchanges: Some(vec!["NSE".into(), "BSE".into()]),
                open_exchanges: None,
            },
            // Special session with its own per-exchange timings (open hit).
            OaHoliday {
                date: Some("2026-10-24".into()),
                holiday_type: Some("SPECIAL_SESSION".into()),
                closed_exchanges: Some(vec!["BSE".into()]),
                open_exchanges: Some(vec![timings("NSE")]),
            },
            // Special session without open entries → the /timings hit.
            OaHoliday {
                date: Some("2026-10-25".into()),
                holiday_type: Some("SPECIAL_SESSION".into()),
                closed_exchanges: None,
                open_exchanges: None,
            },
            // Settlement holiday: a trading day for NSE.
            OaHoliday {
                date: Some("2026-10-26".into()),
                holiday_type: Some("SETTLEMENT_HOLIDAY".into()),
                closed_exchanges: Some(vec!["BSE".into()]),
                open_exchanges: None,
            },
        ];
        let s = build_exchange_session("NSE", "Asia/Kolkata", &rows, &specials);
        assert_eq!(s.timezone, "Asia/Kolkata");
        assert_eq!(s.session, "0915-1530");
        // Closed days; the settlement holiday is NOT one for NSE.
        assert_eq!(s.holidays, "20261002");
        // Corrections win over holidays: both special days share the same
        // spec, so they ride one correction row ("spec:day,day").
        assert_eq!(s.corrections, "0930-1030:20261024,20261025");
        // Price grid of Indian venues (the source's).
        assert_eq!((s.pricescale, s.minmov), (100, 5));
        // Index venues follow their cash market.
        let idx = build_exchange_session("NSE_INDEX", "Asia/Kolkata", &rows, &HashMap::new());
        assert_eq!(idx.session, "0915-1530");
        assert_eq!(idx.holidays, "20261002");
        // MCX keeps its evening session.
        let mcx = build_exchange_session("MCX", "Asia/Kolkata", &[], &HashMap::new());
        assert_eq!(mcx.session, "0900-2330");
    }

    #[test]
    fn error_recognizers_match_the_source() {
        let e = anyhow!("OpenAlgo /api/v1/history: HTTP 502");
        assert!(is_http_5xx(&e) && !is_no_data(&e));
        let e = anyhow!("OpenAlgo /api/v1/history: no data available");
        assert!(is_no_data(&e) && !is_http_5xx(&e));
        let e = anyhow!("OpenAlgo /api/v1/history: HTTP 401");
        assert!(!is_http_5xx(&e));
    }

    #[test]
    fn credentials_fingerprint_hides_the_secret() {
        let p = OpenAlgoProvider::new();
        match p.credential_id() {
            Some(fp) => {
                // 16 hex like the gateway's; the raw key never in it.
                assert_eq!(fp.len(), 16);
                assert!(fp.chars().all(|c| c.is_ascii_hexdigit()));
                assert!(!fp.contains(&p.key));
            }
            None => {
                // Without config there is nothing to probe — that's the
                // point of this arm in .env-less runs.
            }
        }
    }

    #[test]
    fn fallback_tables_cover_the_picker() {
        let t = fallback_minute_table();
        for mult in [1, 3, 5, 10, 15, 20, 30, 60, 120] {
            assert!(t.contains_key(&mult), "missing {mult}");
        }
        // The capabilities advertise a synthesized 240 only from a served 2h.
        assert!(t.contains_key(&120));
    }
}
