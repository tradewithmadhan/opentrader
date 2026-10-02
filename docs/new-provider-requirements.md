# New Data Provider — Requirements Checklist

> Goal: add a new market-data vendor beside the current one without changing
> any Tauri command, `bindings.ts` shape, or chart UI.
> Reference impl: `src-tauri/src/data/provider/massive.rs`
> (`massive_rest` / `massive_poll` / `massive_ws.rs`).
> Frontend mirror: `src/data/providers/massive.ts` + `index.ts` registry.
> Select via `DATA_PROVIDER=<name>` env (`provider/mod.rs::build()`, default `massive`).

## 0. Identity & config

- [ ] `name()` — stable id, matches the `DATA_PROVIDER` value and the frontend
      adapter key (e.g. `"sample"`).
- [ ] `credential_id()` — fingerprint of secret material (never the secret
      itself), `None` if keyless. Keys the `provider_caps.json` entitlement cache.
- [ ] Config source documented: env names, defaults, compiled-in vs runtime
      (endpoint URLs, key storage, per-user vs shared credential).
- [ ] Symbol convention: how the `ticker` string maps to vendor fields
      (bare ticker vs `EXCHANGE:SYMBOL`, casing, derivative formats).
      Define `defaultExchange` and the supported exchange/market list.

## 1. Historical data — `HistoryProvider` (all return `Vec<Candle>`, oldest-first)

### `Candle` shape (fixed, `data/types.rs`)

`{ time: f64 (UNIX seconds, bar start), open, high, low, close, volume }` —
`f64`, not `i64` (specta forbids 64-bit ints in the TS export).

### Methods

- [ ] `daily_aggs(ticker, from: NaiveDate, to: NaiveDate, adjusted: bool)` —
      inclusive calendar range. Empty result surfaces as a load error.
- [ ] `minute_aggs(ticker, mult: u32, from, to, adjusted)` — `mult` is the
      bucket width in minutes. Vendors lacking a multiplier should omit it
      from caps (see §5) or aggregate client-side from a finer bar.
- [ ] `second_aggs(ticker, mult, from, to, adjusted)` — `mult` is the bucket
      width in seconds. Omit unsupported multipliers from caps.
- [ ] `second_tail(ticker, mult, since_sec: f64, adjusted)` — bars with
      `time > since_sec` only (live refresh loop; refetch-and-filter is fine).

### Commands reusing them (no change needed)

`get_daily_history(symbol, days, adjusted)`,
`get_minute_history(symbol, days, interval_min, adjusted)`,
`get_second_history(symbol, mult, days, adjusted)`,
`get_second_history_tail(...)`,
`get_aggregates_before(symbol, timespan, mult, before_sec, span_days, adjusted)`
(returns strictly `time < before_sec`; `[]` means exhausted),
`get_daily_history_before(...)` (same).

### Rules

- [ ] Uppercase/normalize the ticker on entry; `[from, to]` arrives already
      floor-clamped by `commands/history.rs` — do not re-clamp.
- [ ] `adjusted = true` means split-adjusted (the default UI state). If the
      vendor has no adjustment flag, document the price basis and set
      `adjusted_toggle = false`.
- [ ] Document per-family range limits (max lookback window, paging caps).

## 2. Live data — `RealtimeProvider::spawn(app) -> WsHandle`

- [ ] Owns the streaming strategy (REST poll vs WebSocket) behind one
      long-lived task. Emits typed events, accepts subscription updates over
      `WsHandle { tx: mpsc<SubscribeMsg> }`.
- [ ] Handles `SubscribeMsg::{ SetChart { owner: "<window>:<pane>", symbol? },
      SetWatchlist { owner: "<window>", symbols }, DropOwner { owner } }` —
      union across owners, minimal subscribe/unsubscribe diff, replay on
      reconnect, exponential backoff on failure.
- [ ] Emits (same JSON shapes as `bindings.ts`):
  - `ChartAggregate { symbol, time (sec, bar start), open, high, low, close,
    volume, day?: Candle | null }` → event `chart-aggregate`
  - `SecondAggregate { symbol, time, open, high, low, close, volume }` →
    event `second-aggregate` (volume covers that one second only)
  - `TradeTick { symbol, price, change?, changePercent?, extChangePercent?,
    volume?, source? }` → event `trade-tick` (`null`s allowed on raw-trade paths)
- [ ] Commands (unchanged): `set_chart_subscription(pane, symbol?)`,
      `set_watchlist_subscription(symbols[])`.
- [ ] Poll-only, WS-only, or hybrid transports are all acceptable as long as
      the three event shapes above are emitted.

## 3. Reference data — `ReferenceProvider`

- [ ] `ticker_info(ticker) -> TickerInfo { ticker, name?, exchange?, industry?,
      sector?, currency?, description?, homepage_url?, total_employees?,
      market_cap?, figi?, icon_url? }` → `get_ticker_info`.
      All fields except `ticker` are optional (UI renders `—` for missing).
- [ ] `ticker_snapshot(ticker) -> Snapshot { ticker, last, change,
      change_percent, day_high, day_low, day_volume, ext_change_percent?,
      source: "live" | "prev", updated_ns }` → `get_ticker_snapshot`.
      `last` is the regular-session close (not an extended-hours tick);
      fall back to the prior session with `source: "prev"` when shut.
- [ ] `search(query, type_filter?) -> Vec<SymbolSearchResult { ticker, name?,
      market?, locale?, primary_exchange?, type? }>` → `search_tickers`.
      `market`: `stocks | crypto | fx | indices | otc`;
      `type`: vendor security-type code (`CS`, `ETF`, `EQ`, `FUT`, …).
- [ ] `dividends(ticker) -> Vec<DividendEvent { date (f64 sec), amount }>` →
      `get_dividends`. Empty on failure (decorative marker).
- [ ] `splits(ticker) -> Vec<SplitEvent { date, from, to }>` → `get_splits`.
      **Bonus issues map to this shape** (e.g. a 1:1 bonus =
      `{ from: 1, to: 2 }`). Empty allowed.
- [ ] `latest_news(ticker, limit) -> Vec<NewsItem { id, title, publisher,
      published (f64 ms), url?, description? }>`, newest-first →
      `get_latest_news` (limit clamped 1–50). Empty allowed.
- [ ] `icon(encoded) -> (bytes, content_type)` → served via the
      `ticker-icon://` protocol so secrets never reach the DOM.
      May fail if the vendor has no branding icons (set `icons: false`).

## 4. Calendar / market timing / session

- [ ] Holiday calendar for date math (`trading_calendar.rs` currently encodes
      one market's weekends + holidays). New markets add their holiday table
      and reuse `trading_days_before / trading_days_in_range /
      last_trading_days`, or parameterize the module.
- [ ] Regular session hours documented (backend serves full bars; the frontend
      filters RTH vs extended hours in `market-session.ts`).
- [ ] Time convention: bar `time` is always UNIX seconds (UTC). Display
      timezone is frontend-only (`timezones.ts`).
- [ ] `extended_hours` capability flag set honestly (false for quote-only
      vendors).

## 5. Capabilities & entitlements — `DataProvider` remainder + `commands/meta.rs`

- [ ] `capabilities() -> ProviderCapabilities { name, resolutions: { seconds:
      u32[], minutes: u32[], daily: bool, weekly_monthly_from_daily: bool },
      max_bars_per_request, adjusted_toggle, extended_hours,
      reference: { search, search_type_filter, snapshot, dividends, splits,
      news, icons },
      session: { timezone (IANA), open_min, close_min (minutes since local
      midnight — drives the frontend RTH filter; NOT the transport default) },
      entitlements: None }` — static, no I/O.
      The resolution lists must equal the frontend `datafeed.ts` lookback
      tables (enforced by test in the reference impl).
- [ ] `probe_history() -> HistoryProbe { data_status: Streaming |
      DelayedStreaming | Endofday | Unknown, raw_status, delay_sec,
      floor: { second?, minute?, day? (YYYY-MM-DD) } }` — oldest available
      bar per family; drives scroll-back clamping.
- [ ] `probe_stream(status) -> StreamCaps { minute_bars, second_bars, trades,
      quotes }` — which live channels the credential may use. Must not fight
      the live task for single-connection keys.
- [ ] `get_data_provider(): string`, `get_provider_capabilities():
      ProviderCapabilities` (static caps + cached entitlements, no I/O), and
      the `provider-capabilities` event on probe steps — all generic, no
      per-provider code.
- [ ] Entitlement cache (`provider_caps.json`, keyed by provider + credential
      fingerprint) applies automatically; stale = earlier UTC day → re-probe.
      History commands wait at most `FLOOR_WAIT` (5s) only when no cache exists.

## 6. Frontend — one file: `src/data/sources/<name>.ts`

A provider is a single module holding its engine, its `DataSource` socket
object, and its `FrontendProvider` presentation adapter (see
`sources/sample.ts`). Register both objects: one line in
`sources/index.ts::REGISTRY`, one line in `providers/index.ts::REGISTRY`.
Nothing else in the app changes.

- [ ] `export const <name>Source: DataSource` — history, reference, live,
      meta, seeds; optional `symbolSessions` / `fundingStatus` slots where
      the source has its own calendar or public endpoints.
- [ ] `export const <name>: FrontendProvider { name, defaultExchange,
      exchangeName(code), exchangeCode(code), searchResultToRow(r),
      typeFilters }`. `name` must match the backend `DataProvider::name()`;
      `syncProvider()` selects it via `get_data_provider()`.
- [ ] `exchangeName`: vendor code → display label (identity function if codes
      are already friendly).
- [ ] `searchResultToRow`: map one raw search result →
      `SymbolRow { symbolName: "EX:TICKER", ticker, description, marketType,
      exchange, category }`. Document security-type → category routing.
- [ ] `typeFilters`: security-type dropdown options for the symbol-search
      filter chip.

## 7. Non-goals (must NOT change per provider)

`commands/*`, `data/types.rs` (struct definitions move here only when a second
provider lands — today re-exported from vendor modules), `capabilities.rs`,
`entitlements.rs`, `bindings.ts` shapes, the `datafeed.ts` contract,
`ChartView` (props-driven already).

## 8. Verification per provider

- [ ] `cargo test export_bindings` — generated bindings unchanged.
- [ ] `cargo test --lib` — existing tests green.
- [ ] `DATA_PROVIDER=<name> npm run tauri dev` → `get_data_provider`
      returns the new name; `get_provider_capabilities` shows its static caps;
      entitlements settle (immediately when `credential_id` is `None`).
- [ ] Chart loads daily + minute (+ seconds if capped); scroll-back pages and
      then latches exhausted past the floor; watchlist ticks move; search finds
      symbols; detail panel shows a snapshot; dividends / splits / news degrade
      to empty without errors.
- [ ] Flip back to the previous provider — identical UI, different data
      (proves the swap is env-only).
