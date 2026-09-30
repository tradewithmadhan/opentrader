# OpenTrader Gateway — Endpoint Reference

> The gateway (`deepentropy/gateway`, a Cloudflare Worker) is the one door to
> market data. It holds the real Massive key and shares one upstream WebSocket
> between all running apps. The app authenticates with its own compiled-in
> token (`OPENTRADER_GATEWAY_TOKEN`, via `src-tauri/build.rs` from the build
> environment or the repo-root `.env`) **in place of** the Massive key.
>
> Base (see `src-tauri/src/data/gateway.rs`):
>
> ```
> BASE   = https://opentrader-gateway.cloudflare-breeder165.workers.dev
> WS_URL = wss://opentrader-gateway.cloudflare-breeder165.workers.dev/stocks
> ```
>
> Auth: every REST call below appends `?apiKey=<token>` (shown in full on the
> first endpoint, abbreviated after). Without a token the build runs but every
> data call fails with `NO_TOKEN`. The gateway rewrites Massive hosts inside
> REST bodies (`next_url`, branding `icon_url`) to its own origin.
>
> Shapes below mirror the Rust structs in `src-tauri/src/data/massive_rest.rs`
> (`AggBar`, `SnapEntry`, …), `src-tauri/src/screener/*` and
> `src-tauri/src/commands/funding.rs`. Sample values are illustrative.

## 1. Aggregates (OHLCV history)

### `GET /v2/aggs/ticker/{ticker}/range/{mult}/{timespan}/{from}/{to}`

History for one ticker. Used by daily / minute / second charts, scroll-back
pagers and the entitlement probe. Code: `fetch_aggs` (`massive_rest.rs:101`).

**Pass:**

| Param | Values |
|---|---|
| `ticker` | Uppercase, e.g. `AAPL` |
| `mult` | Bucket width, `≥ 1` |
| `timespan` | `second` \| `minute` \| `hour` \| `day` |
| `from` / `to` | `YYYY-MM-DD` (or unix-ms strings) |
| `adjusted` | `true` = split-adjusted (default UI state) |
| `sort` | `desc` (keeps the most recent bars) — `asc` for the probe |
| `limit` | `50000` (caps *base* units read; oldest bars truncate first) |
| `apiKey` | App token |

**Get** (`t` = bar start, UNIX **milliseconds**):

```json
{
  "status": "OK",
  "resultsCount": 3,
  "results": [
    { "o": 2815.9, "h": 2857.7, "l": 2804.45, "c": 2825.5, "v": 3517068, "t": 1701432600000 },
    { "o": 2825.6, "h": 2840.1, "l": 2819.0, "c": 2838.2, "v": 2988441, "t": 1701519000000 }
  ]
}
```

Error shape (entitlement 403s explain themselves in `message`, not `error`):

```json
{ "status": "NOT_AUTHORIZED", "message": "…reason…", "resultsCount": 0 }
```

**Probe variant** (entitlement floor): same endpoint,
`range/1/{day|minute|second}/{today-30y}/{today}?adjusted=true&sort=asc&limit=1`.
The first bar's date marks the plan floor; the response `status` word marks the
plan (`"DELAYED"` = 15-min-delayed key, `"OK"` = real-time). ~0.4 s.

## 2. Splits & dividends (chart markers)

### `GET /v3/reference/splits?ticker={t}&execution_date.lte={today}&order=desc&sort=execution_date&limit=1000&apiKey=`

**Get:**

```json
{
  "results": [
    { "execution_date": "2020-08-31", "split_from": 1, "split_to": 4 }
  ]
}
```

### `GET /v3/reference/dividends?ticker={t}&ex_dividend_date.lte={today}&order=desc&sort=ex_dividend_date&limit=1000&apiKey=`

**Get:**

```json
{
  "results": [
    { "ex_dividend_date": "2024-02-09", "cash_amount": 0.24 }
  ]
}
```

Both are decorative: any failure contributes no markers.

## 3. News (chart lollipop)

### `GET /v2/reference/news?ticker={t}&order=desc&sort=published_utc&limit={n}&apiKey=`

**Get** (newest first):

```json
{
  "results": [
    {
      "id": "abc123",
      "title": "Apple unveils …",
      "publisher": { "name": "Reuters" },
      "published_utc": "2026-09-29T14:02:00Z",
      "article_url": "https://…",
      "description": "…"
    }
  ]
}
```

## 4. Reference: ticker info, search, list

### `GET /v3/reference/tickers/{ticker}?apiKey=` — company info (WatchlistDetail)

**Get** (`results` is a single object):

```json
{
  "results": {
    "name": "Apple Inc.",
    "primary_exchange": "XNAS",
    "sic_description": "Electronic Computers",
    "currency_name": "usd",
    "description": "…",
    "homepage_url": "https://www.apple.com",
    "total_employees": 161000,
    "market_cap": 3415000000000,
    "composite_figi": "BBG000B9XRY4",
    "branding": { "icon_url": "https://opentrader-gateway.cloudflare-breeder165.workers.dev/v3/reference/tickers/AAPL/branding/icon?apiKey=…" }
  }
}
```

### `GET /v3/reference/tickers?ticker.gte={Q}&ticker.lte={Q}ZZZZZZ&active=true&limit=1000[&type={code}]&apiKey=` — symbol search

Pure **prefix** match via the ticker range (`search=` would also fuzzy-match
company names). Optional `type` constrains server-side (`CS`, `ETF`, …).

**Get:**

```json
{
  "results": [
    { "ticker": "AAPL", "name": "Apple Inc.", "market": "stocks", "locale": "us", "primary_exchange": "XNAS", "type": "CS" }
  ]
}
```

### `GET /v3/reference/tickers?market=stocks&active=true&limit=1000&apiKey=` — full universe (screener)

Same shape as search, paged: follow `next_url` (it points at the gateway
**without** the token — re-append `&apiKey=`), up to ~13 pages of 1000.
~40-page safety cap in code.

## 5. Snapshots (quotes + live bars)

### `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}?apiKey=` — one ticker

**Get** (`ticker` is a single object):

```json
{
  "ticker": {
    "ticker": "AAPL",
    "todaysChange": 12.4,
    "todaysChangePerc": 0.44,
    "updated": 1720000000000000000,
    "day":   { "o": 2810.0, "h": 2857.7, "l": 2804.4, "c": 2825.5, "v": 3517068 },
    "prevDay": { "o": 2790.0, "h": 2816.0, "l": 2788.0, "c": 2813.1, "v": 42001155 },
    "min":   { "c": 2825.5, "t": 1720000000000 }
  }
}
```

The app shapes this into `{ last, change, changePercent, dayHigh, dayLow,
dayVolume, extChangePercent, source: "live" | "prev" }`: `last` is the
**regular-session** close, `min.c` carries the extended-hours tick, `min.t ==
0` means no minute bar (market closed).

### `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers={A,B,…}&apiKey=` — bulk (watchlist)

Same entry shape under `"tickers": [ … ]`. Tickers uppercased, comma-joined.
Unknown/halted tickers are silently omitted.

### `GET /v2/snapshot/locale/us/markets/stocks/tickers?apiKey=` — full market (screener poll)

No ticker list: the whole delayed market in one call, every 10 s while a
screener panel is open.

### `GET /v2/aggs/grouped/locale/us/market/stocks/{YYYY-MM-DD}?adjusted=true&apiKey=` — one date, all closes

```json
{ "results": [ { "T": "AAPL", "c": 2813.1 } ] }
```

Used for the prior-session-close baseline of closed/pre-open snapshots.

## 6. Screener state (gateway-computed)

### `GET /screener/v1/state?apiKey=` — daily indicator values per ticker

Unlike §1–5 this is **computed by the gateway**, not a Massive passthrough:
indicator values at the last close for every ticker (contract v1), so a filter
like `close > EMA200` costs one multiply-add instead of a history download.
Cached on disk as `screener_state.json`.

**Get:**

```json
{
  "version": 1,
  "asof": "2026-09-29",
  "tickers": ["AAPL", "MSFT"],
  "num": { "close": [2825.5, 511.2], "ema200": [2701.0, null] },
  "text": { "sector": ["Technology", "Technology"] }
}
```

`404`/`403` = not served yet → state fields read null ("—" in the table).

## 7. Funding (public, no token)

### `GET /funding/v1` — running cost vs donations (Settings > About)

The only endpoint that takes **no** `apiKey`. `404` = no cost set this month.

**Get:**

```json
{
  "month": "2026-09",
  "currency": "USD",
  "total": 42.0,
  "raised": 18.5,
  "remaining": 23.5,
  "links": { "github": "https://…", "bmc": "https://…" }
}
```

## 8. Branding icons (token stays server-side)

`icon_url` values in reference bodies are rewritten to the gateway origin.
The webview never fetches them directly: it uses a `ticker-icon://` proxy URL
and the Rust backend re-attaches the token when serving the bytes — the token
never appears in the DOM or request logs.

## 9. WebSocket (live stream)

```
wss://opentrader-gateway.cloudflare-breeder165.workers.dev/stocks
```

Speaks the Massive protocol with the app token in place of the key, sharing
one upstream connection across apps. The gateway picks the delayed or
real-time feed itself.

**Send / receive:**

```json
// auth
{ "action": "auth", "params": "<token>" }
// subscribe / unsubscribe (comma-joined channels)
{ "action": "subscribe", "params": "A.AAPL,AM.AAPL,T.AAPL" }
{ "action": "unsubscribe", "params": "T.AAPL" }
// inbound: JSON arrays, one element per event
[
  { "ev": "status", "status": "auth_success", "message": "authenticated" },
  { "ev": "A", "sym": "AAPL", "s": 1720000000, "o": 100.0, "h": 100.5, "l": 99.9, "c": 100.4, "v": 1200 },
  { "ev": "AM", "sym": "AAPL", "s": 1719999960, "o": 99.0, "h": 101.0, "l": 98.5, "c": 100.4, "v": 45230 },
  { "ev": "T", "sym": "AAPL", "p": 100.42, "s": 50, "t": 1720000000123 }
]
```

Channel prefixes: `A.` = 1-second bars, `AM.` = minute bars, `T.` = trades,
`Q.` = quotes. The app uses `A.*` (seconds charts) today; `AM`/`T` are
retained but unspawned. Reconnect with exponential backoff (1 s → 30 s).
Entitlement probing subscribes `AM./A./T./Q.` for `AAPL` and reads which the
server grants.
