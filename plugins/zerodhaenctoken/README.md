# Zerodha (enctoken) plugin

A Zerodha Kite account in OpenTrader: instruments, history and live ticks, plus trading from the charts and the
order ticket.

The plugin uses Kite's *personal* API (`https://kite.zerodha.com/oms`) with an **enctoken** — the same access
OpenAlgo's `zerodhaenctoken` broker uses. No API key or paid Kite Connect subscription is needed. Kite's REST
`quote`/`ltp`/`ohlc` endpoints do not work with an enctoken, so live prices and the order book come from the Kite
ticker WebSocket; history, the instrument master, the funds/positions/orders books and order commands all use the
personal API.

## Get an enctoken

1. Log in at [kite.zerodha.com](https://kite.zerodha.com) in Chrome/Edge.
2. Open Developer Tools (F12) → **Application** → **Cookies** → `kite.zerodha.com`.
3. Copy the value of the `enctoken` cookie.
4. Paste it into **Settings > Plugins > Zerodha (enctoken) > Enctoken**. It is stored in the system credential
   store, is never shown again, and is redacted from the plugin log.

The token expires when the Kite session ends (usually overnight). When that happens the plugin asks for a fresh
one with a dialog; repeat the steps above. Your user id is read from the Kite profile, so you only need to fill
it in if that lookup fails.

**Re-enter the enctoken after installing a new release version.** It is a `password` setting kept in the system
credential store per plugin version; upgrading does not carry the value over. Until you re-enter it the log says
`enctoken=empty`, history refuses to run and no ticks arrive, while symbol search still works from the public
instrument CSV. The dev manifest deliberately uses a plain text field so this doesn't happen during development.

## Develop with the app running

1. `python build.py dev` and install `dist/zerodhaenctoken-dev.zip` from **Settings > Plugins > Install from file**.
   The "not verified" warning is expected for a plugin outside OpenTrader's catalog.
2. `python plugin.py` in a terminal. It reads the connection file the app writes, connects, and stays connected.
3. Check in the app: search `NSE:REL` or `NSE_INDEX:NIFTY`, open a symbol, change the interval, watch the last bar
   move with each tick, and scroll back through history.
4. Settings > Plugins shows the plugin's log. Nothing secret is written there.

## Symbols

Symbols are `<segment>:<Kite tradingsymbol>`, for example `NSE:RELIANCE`, `NFO:NIFTY25NOVFUT`,
`NSE_INDEX:NIFTY`, `MCX:CRUDEOIL25NOVFUT`. The segments served are NSE, BSE, NFO, BFO, CDS, BCD, MCX, NCO,
NSE_INDEX, BSE_INDEX, MCX_INDEX and GLOBAL_INDEX; narrow the **Segments to serve** setting if your account does
not have all of them.

Two source quirks are handled: Kite leaves `instrument_type` blank on index rows, so index segments are typed from
the segment; and futures rows carry `strike = 0`, which is omitted from descriptions (e.g. `BANKEX FUT 26-NOV-26`).

Two caveats about the source data:

- **NCO** has no minute bars in Kite's personal API — only daily. NCO symbols therefore advertise only `1D`
  (weeks/months are built by the app). NCO spot underlyings such as `NCO:GOLD` also have no history at all; use an
  NCO future like `NCO:GOLD26DECFUT`.
- **BCD** and **GLOBAL_INDEX** are declared segments but Kite's public CSV contains no rows for them on this
  account, so they show no symbols.

## Company details and logo

Kite's instrument master and personal API provide no logo, sector or industry — only a company `name`. For **NSE/BSE
cash equities** the plugin fills in `icon`, `name`, `sector`, `industry` and `longDescription` from the same external
company-info service used by the OpenAlgo plugin (`api.dharun.dev`). None of these calls carries credentials, and a
failed lookup is cached for 60 seconds and never fails symbol info. Set **Company details and logo** to off to skip
them entirely — useful with a large watchlist.

## Request headers

REST calls send the ordinary browser headers from a logged-in Kite web session — a Chrome `User-Agent`,
`Accept-Language`, `Origin`, `Referer`, `Sec-Fetch-*`, `Sec-CH-UA*`, `Cache-Control`, `Pragma`, `Priority` and
`X-Kite-Version: 3.0.0` — plus `Authorization: enctoken …` on `/oms` calls and `X-Kite-Userid` from the profile.
The public instrument CSV gets the browser headers but no auth. **No `Cookie` header or Cloudflare clearance token
is ever sent or stored**; the enctoken is the session. Only `gzip` is advertised (Python has no br/zstd decoder).
The WebSocket handshake carries the same `User-Agent`/`Accept-Language` plus the required `Upgrade` and
`Sec-WebSocket-*` headers.

## Settings

| Key | Default | Purpose |
|---|---|---|
| `enctoken` | empty | The masked browser session token (see above). |
| `userId` | empty | Kite login; blank reads it from `/oms/user/profile`. |
| `exchanges` | all 12 | Comma-separated segments served. |
| `product` | `MIS` | Default product on the order ticket (MIS / CNC / NRML). |
| `historyDaysMinute` | `30` | Days per intraday history request (Kite tolerates more; larger windows are split and merged). |
| `historyDaysDaily` | `1900` | Days per day-history request (Kite's hard limit is 2000; 1900 leaves headroom). |
| `companyDetails` | `true` | External logo/company details for cash equities. Each ticker is fetched at most once and cached in `cache/company-profiles.json` across restarts; failures back off and never delay a symbol's data. |
| `quoteIntervalMs` | `0` | Minimum ms between a symbol's chart updates; 0 = every tick. |
| `maxQuotesPerSecond` | `200` | Total chart/watchlist quote budget across all symbols; 0 = unlimited. A large watchlist cannot flood the app; the ticket and DOM are not counted. |
| `apiUrl` / `wsUrl` / `masterUrl` | Kite defaults | Endpoints; change only if you proxy them. |
| `logApi` / `logWs` | `true` | Per-category file logs. |
| `logDebug` | `false` | Also log every `broker.state` push and subscription detail. |

### Large watchlists

A watchlist of thousands of symbols subscribes them all in one batch and Kite can push hundreds of ticks a second.
`maxQuotesPerSecond` (default 200) caps the total `data.quote` messages sent to the app; symbols that miss the
budget get priority in the next frame, so the whole list rotates instead of a few liquid names taking every slot.
The order ticket and DOM still receive every tick. Switch the list back to a small one (or raise this setting) if
you want faster price refresh on every row. An empty `broker.watchQuote`/`watchDepth` is held for 1.5 s before it is
applied: switching charts sends an empty watch followed immediately by the new symbol, and dropping full mode in
between would churn Kite's `mode` frames and blank the ticket's bid/ask.

The app's periodic **newest-bar refresh** (`data.bars` with `countBack=1` for `1D`/`1`) is answered from the live tick
cache: Kite's tick already carries the forming bar's open/high/low, last price and cumulative volume, so those
refreshes — including the `_OI` series' — make **no** Kite history call. Older windows still come from the history
endpoint. The perf log counts them as `cached_bars`.

## Intervals

The plugin serves **1, 3, 5, 10, 15, 30 minutes and 1 day** from Kite's history. The app builds 2/4-minute bars from
1-minute ones, hours from 15-minute ones, and weeks/months from daily ones. Seconds (1/5/10/15/30/45 S) are also
offered: on each first request the plugin probes Kite for a seconds history interval and, when Kite serves one, uses
it. Kite Connect documents no seconds interval; when the personal OMS refuses it, second bars are built from the live
tick stream going forward — **there is no backfill**, so a fresh seconds chart fills from the moment it is opened.
The log line `seconds history: 5s via Kite interval '…'` shows which source won.

## Trading

Install/enable the plugin, then **Trade > Connect broker… > Zerodha (enctoken)** and **Trade > Turn trading on**.
Orders to this real-money account (`isDemo: false`) are confirmed by OpenTrader first.

- Market, limit, stop and stop-limit orders, with modify, cancel and close-position, using Kite's `regular`
  variety. Modifying or cancelling an order uses the **variety reported for that order** (e.g. `amo`), not a fixed
  path, so after-market orders can be cancelled too. Time in force is Day or IOC. The product type (MIS/CNC on cash, MIS/NRML on derivatives) is a selector
  on the order ticket, defaulting to the **Default product** setting — add it in Settings if you want to change it.
- Position brackets are not offered: Kite has no broker-side bracket modification.
- **Quantities** are in units. For NFO/BFO one lot is its lot size (e.g. NIFTY 75), so the ticket steps by that.
  Kite reports MCX quantities in *contracts*; the plugin converts to units using each contract's real market lot
  (CRUDEOIL 100, GOLDM 100, …), so one lot of crude is 100 units in OpenTrader and one contract at Kite.
- **Holdings** (Plugin API 7): Kite's `/oms/portfolio/holdings` is offered as a **Holdings** tab in the account
  manager after *Trade history* — quantity, product, average price, live last price, invested value, current value,
  unrealized P&L and %, with T1 quantity and ISIN hidden in Column setup. Rows sort by current value, and the
  page refreshes with each account state. A click on a symbol opens its chart.
- The order book, trades, positions and margins come from `/oms`; fills are pushed by the Kite ticker as order
  postbacks and also picked up by a poll (every second while an order or position is open, otherwise every ten).
- The POST body mirrors Kite's own web app: `variety`, `tradingsymbol`, `exchange`, `transaction_type`,
  `order_type`, `quantity`, `price`, `product`, `validity`, `disclosed_quantity`, `trigger_price`, `squareoff`,
  `stoploss`, `trailing_stoploss`, `user_id` and a single `tag` — matching the Kite web app's form (no
  `market_protection`). Orders are real money: verify
  with a small order when the market opens.

## History request caps

A single Kite day-history call accepts at most **2000 days** (verified live — a wider window errors with
`interval exceeds max limit: 2000 days`); intraday calls tolerate more but are kept small. The plugin splits any
window into chunks of `historyDaysDaily` (default 1900) or `historyDaysMinute` (default 30), merges the results by
bar time, drops duplicates and keeps the newest `countBack`. Blank, zero, negative or non-numeric values fall back
to the defaults, so a bad setting cannot produce a failing request.

The separate `history` depth declared in `symbolInfo` (30 years for both day and minute families; NCO has no minute
history) tells the app **how far back it may ask**. Without a generous value the chart stops scrolling at that
depth even though chunking could fetch more — Kite serves day bars back to at least 2000 and minute bars back to at
least 2024 (verified live).

For NSE symbols the depth is tightened per symbol from NSE's official listing lists (`EQUITY_L.csv` and
`SME_EQUITY_L.csv`, fetched once a day and cached): a stock listed 30 days ago declares 30 days of history instead
of 30 years, so the app never asks for years before its listing. Symbols without a listing row, and other
exchanges, use the configured maxima. The two listing lists are small and their cache is pruned with the other
day-stamped files.

To avoid a burst of Kite calls (rate limits), each `data.bars` request returns only the **newest chunk** of the
asked window — one day call (≤1900 days) or one intraday call (≤30 days). The protocol lets a plugin return fewer,
newest bars; the app then asks for older pages as the user scrolls, so the full per-symbol history is still
reachable one chunk at a time.

## Corporate-action events

When a **daily (`1D`) bar** request is made, the plugin also reads Kite's `/oms/instruments/events/{token}` for that
instrument from 1990 to today (in 3-year chunks — Kite caps the range at 1095 days), caches the result per day in the plugin data
folder, logs a summary, and returns it in the `data.bars` result as an extra `events` field:

```json
{"bars": [...], "events": [{"time": 1729267200, "kind": "split", "label": "1:1 bonus",
                            "action": "bonus", "date": "2024-10-28", "meta": {...}}]}
```

`bonus`/`split` map to the app's `split` marker and `dividend` to `dividend`; `time` is the ex-date at IST midnight.
Kite already serves price-adjusted history, so these are **markers only** — never applied to OHLC. A symbol with no
events costs one cached fetch per day; minute-bar requests do not fetch them.

The app's chart-event pipeline currently reads dividends/splits/fundamentals from its own provider and ignores this
extra field, so nothing is drawn yet. The data is on the wire for the upstream app change needed to show the markers
on the chart.

## Symbol search

Search matches the ticker, the company/underlying name **and** the built description. A multi-word query is
normalised and its words must all appear in the searchable text, so `NIFTY 25000` finds the option whose
tradingsymbol is `NIFTY25NOV25000CE` and whose description is `NIFTY 25000 CE 25-NOV-26`. Results are ranked
ticker-prefix → ticker-substring → name/description, spread across the configured segments, 50 at a time.

## Plugin API 7

The plugin speaks **API 7** and uses the upstream features adopted in this fork:

- **Watchlists** (API 4): two sample lists are offered under the plugin's name in *Open list…* — a set of NSE blue
  chips and a set of indices. They are built from the instrument master, capped at 1000 symbols per list, and sent
  again each time the master is loaded. Replace `watchlists()` (and `SAMPLE_BLUE_CHIPS` / `SAMPLE_INDICES`) with
  your own sets, e.g. the members of an index or the contracts of the month.
- **Events** (API 5): `data.events` answers with Kite's corporate actions in the app's shape — dividends with an
  `amount`, bonuses and splits as `from`/`to` (a 1:1 bonus is `1 → 2`). Earnings are not supplied by the personal
  API; add them in `events_for()` when you have a source. Events are fetched in 3-year chunks, cached per day and
  logged.
- **Extra series** (API 6): F&O symbols declare an `OI` series. `data.bars` on `<symbol>_OI` returns Kite's open
  interest (requested with `oi=1`) as a 5-value series, and the ticker's live OI is pushed as a `data.quote` while
  the series is subscribed. Add more series in `EXTRA_SERIES` and serve `<symbol>_<name>`.
- **Broker pages** (API 7): Kite's delivery holdings are sent as the *Holdings* tab in `broker.state.pages` (see Trading).
- **Instrument values** (API 6): `symbolInfo.fields` lists expiry, lot size, tick size and, for NSE stocks, the
  listing date plus **ISIN**, **Face value** and **Series** from NSE's `EQUITY_L.csv` / `SME_EQUITY_L.csv`. Live OI
  quotes carry `Open Interest` / day high / low fields. Add your own values in `EXTRA_FIELDS`.

## Tests

`python plugins/zerodhaenctoken/tests/run_all.py` runs the suites against an honest fake Kite deployment (real
handshake, real binary packet layouts, the real history caps, 403/429 edges and the OMS order routes). No internet
or account needed, and **no test ever places a live order**.

## Large watchlists

OpenTrader subscribes to every symbol of the **active** watchlist (not just the rows on screen). With a 2,400-symbol
list that is a continuous stream of ticks, so two settings help:

- **Company details and logo** (on by default): turn off to skip the external sector/industry/logo lookups. Those
  are in-memory caches; with the list shown, every app restart would otherwise re-request them for thousands of
  symbols.
- **Minimum ms between a symbol's chart updates** (0 by default, i.e. every tick): set 100–250 ms to cap chart
  updates per symbol. The forming bars still accumulate every tick; the order ticket and DOM are unaffected.

The plugin also writes all notifications from one tick frame in a single socket write, resolves tokens from a
lock-free map, and caps its subscription at Kite's 3,000 instruments per connection (it logs a warning and takes
the first 3,000 if the app asks for more). The simplest lever remains making a **smaller watchlist active** — only
the active one streams.

An `NSE EQ` watchlist of 2,422 symbols was created in OpenTrader from NSE's official `EQUITY_L.csv` (`SERIES = EQ`).
Watchlists are app-side data, not a plugin capability: that list is a one-time setup and a fresh install will not
recreate it. Only the active list is subscribed; switch to a smaller list to cut the live tick rate immediately.

## Logs

The plugin writes two files so you (or an AI agent) can read what happened live, independently of OpenTrader's
in-memory plugin log:

- `%LOCALAPPDATA%\OpenTraderPlugins\zerodhaenctoken\zerodha-api.log` — settings, master load, every `data.bars`
  request (symbol, interval, bar count, window, event count) and history failures, books
  (positions/orders/trades counts), order commands and order postbacks.
- `%LOCALAPPDATA%\OpenTraderPlugins\zerodhaenctoken\zerodha-ws.log` — stream connect/reconnect and subscription
  summaries.

The plugin prunes its caches on startup: it keeps today's and yesterday's master for the current version and
today's event files, and deletes older or older-version files (a line is logged when it does). This stops the
~30 MB master from accumulating one copy per day.

Every 30 seconds `zerodha-api.log` gets a `[perf]` line with the counters needed to judge a large watchlist:
ticks received, `data.quote` / `broker.quote` / `broker.depth` messages, batched socket writes, throttle skips,
cached symbols, subscribed tokens (and how many are in `full` mode) and stream reconnects. For a live performance
test, leave the big watchlist active on a liquid chart during market hours and watch those numbers and the OS
CPU/memory of `opentrader.exe` and the plugin process.

When installed as a release build, the files live in the per-plugin data folder OpenTrader provides
(`OPENTRADER_PLUGIN_DATA`). Each log rolls at ~1 MB. The enctoken is redacted from every line. Enable
**Log verbose details** in Settings to also record every `broker.state` push; turn a category off with
**Log REST/API to file** / **Log WebSocket to file** if you don't want it.

## Build

`python -m pip install pyinstaller`, then `python build.py release`. It writes `dist/zerodhaenctoken-<version>.zip`
with the manifest and the built program, and prints its sha256. Build on the system you publish for; the manifest
names one `entry` and its `platforms`. The package is unsigned, so installing it from a file reports "not
verified" — expected for a plugin outside OpenTrader's catalog.

## Verification status

Live-verified on the production build: instrument master (135,388 rows), profile, history and bars on all 10
segments that have instruments, the four OMS books, `broker.state`, WebSocket handshake and real binary ticks, order
postbacks, the browser header set, and all settings. The regression suites (49 tests) run against an honest fake
Kite deployment and never place a live order. **Order placement has only been tested against the fake** — verify it
with a small order when the market is open. Rotate the enctoken if it was ever shared outside the plugin settings.
