# OpenTrader plugin: OpenAlgo

Your OpenAlgo deployment in OpenTrader: the OpenAlgo segments of your broker account (NSE, BSE, NFO, BFO, MCX,
CDS, BCD, NCO, NCDEX — and the index segments NSE_INDEX, BSE_INDEX, MCX_INDEX) on the charts, and its trading
from the charts. One file, Python 3.9+, standard library only. Plugin API 3 (whole symbols, `NSE:SBIN-EQ`):
the app speaks whole symbols — the exchange prefix is the app's exchange, the ticker is OpenAlgo's symbol.
The **API key** is a password setting, kept in the system credential store and never shown again, so it must
be re-entered once when upgrading from 0.1.27. Logs and the instrument cache live in the plugin's own data
folder (removed when the plugin is removed). The wire behavior
mirrors the app's built-in OpenAlgo provider (verified live), index symbols included: an index keeps its own
prefix, `NSE_INDEX:NIFTY`.

## What it does

- **Data**: search across every segment your settings name (the index segments chart under their cash exchange's
  prefix, `NSE:NIFTY`), symbol info with the exchange's session and tick size, history bars of every interval your
  OpenAlgo serves (`/api/v1/intervals` is read daily, with a known fallback table), live quotes from the
  WebSocket with forming minute and day bars.
- **Trading**: funds, positions, orders and executions from the books (`/funds`, `/orderbook`, `/positionbook`,
  `/tradebook`), place / modify / cancel orders, close a position (whole, via `/closeposition`, or a part, as a
  market order the other way). Market, limit, stop and stop-limit; the product type of orders without one comes
  from the plugin's `product` setting (MIS by default).
- Status changes (filled, cancelled, rejected) become events in the app; every order live-confirms, because
  OpenAlgo has no demo mode (`isDemo: false`).

## Install (development)

```powershell
cd plugins\openalgo
python build.py dev        # dist/openalgo-dev.zip
```

1. OpenTrader → Settings → Plugins → Install from file → `dist\openalgo-dev.zip`.
2. Fill the settings: **API URL** (REST base, `http://127.0.0.1:5000`), **WebSocket URL** (full URL including any
   path, `wss://your-domain/ws`), **API key** (masked, saved in the credential store), the exchanges to serve
   (the OpenAlgo segments exactly as the instrument master names them — the index segments included), and the
   default product.
3. Run the plugin by hand (the app writes where to connect):
   ```powershell
   python plugin.py
   ```
   It reconnects by itself; the app starts the built executable instead when a release package is installed.

Editing is cheap: a dev build of the app watches the plugin's source files and restarts the running program
on save (`Changed on disk — restarting with the new code` in the log) — save and it comes back. Running by
hand, Ctrl-C and rerun. A release build watches nothing.

Verify, in order: the connection line in the plugin's log → search (`NSE:`) → open `NSE:SBIN-EQ` → bars → live
quotes → Trade → Connect broker → an order from the ticket (small quantity first) → the account manager.

## Publish

```powershell
python build.py release    # needs: python -m pip install pyinstaller
```

`dist/openalgo-<version>.zip` (the manifest plus the one executable, built for the system PyInstaller runs on)
with its sha256 printed. Bump `version` in `opentrader-plugin.json` and `PLUGIN_VERSION` in `plugin.py` together.

## The WebSocket protocol (verified against the app's built-in provider)

No guessing left: the stream speaks this exactly.

1. On open: `{"action": "authenticate", "api_key": "<key>"}` — the answer's `supported_features` is read
   before subscribing (a bad key shows up as a close, and the loop backs off).
2. The whole subscription set, on every change: `{"action": "unsubscribe_all"}`, then
   `{"action": "subscribe", "mode": 3, "depth": 5, "symbols": [{"exchange": "NSE", "symbol": "SBIN-EQ"}, …]}`
   — the venue halves, the key not in the frames. Mode 3 is the five-level depth stream: its best bid / ask
   is the order ticket's ladder, and its levels are the ladder's size cells and totals. The auth answer's
   `supported_features.depth` decides this; a broker without it gets `"mode": "Quote"` instead, and the
   ladder falls back to the tick-size spread.
3. A ping frame every 20 s; the server's pings are answered with pongs.
4. Incoming: `{"type": "market_data", "symbol": …, "exchange": …, "data": {open, high, low, ltp, volume,
   prev_close?, close, depth?}}` — on ticks `close` is the previous day's close and `prev_close` is often
   absent, so the two fall back through each other and the last quote. `depth` is `{buy: [...], sell: [...]}`,
   five levels each (`{price, quantity, orders}`): each level's `price` and `quantity` reach the app
   as a `broker.depth` snapshot with `bids` / `asks` (`{price, qty}`, best first), sent for the tickers of
   `broker.watchDepth` (the DOM tab). The first buy / sell level is also the ticket's bid / ask in
   `broker.quote`. A bare echo resolves through the subscription when exactly one listing matches; an
   ambiguous one is dropped (the provider's identity rule).

`OPENALGO_DEBUG=1` prints all of this raw (`[ws >]` outgoing with the key redacted, `[ws <]` incoming) on the
console while you run `plugin.py` by hand.

## Files

| file | what it is |
| --- | --- |
| `plugin.py` | the plugin: the source plus OpenTrader's template connection layer |
| `opentrader-plugin.json` | the release manifest |
| `opentrader-plugin.dev.json` | the development manifest (external runtime) |
| `build.py` | the packager: `python build.py dev` / `release` |
| `tests/` | the regression suites (`python tests/run_all.py`): an honest fake deployment, no account or network |

## Notes

- The API key is sent only inside request payloads; every log line and error goes through a redaction
  (`_clean`) that strips it before it leaves the process.
- The instrument master is pulled once per segment per day, cached in memory and on disk under the plugin's
  data folder (`OPENTRADER_PLUGIN_DATA`, with a `{localAppData}\OpenTraderPlugins\openalgo` fallback when the
  plugin is run by hand).
- A long history request runs beside the quote stream: the source locks its own parts, so the template's outer
  lock is not taken in `handle` (two more deviations are documented at the top of `plugin.py`).
- Dates in the app's UI read dd/mm/yyyy; history windows and daily bars are keyed to IST midnight.

## Future: browser login for a direct broker plugin (not used by OpenAlgo)

OpenAlgo authenticates with an API key, so this plugin never opens a browser. **If a future direct-broker plugin
needs an OAuth / browser login, it can be done entirely from the plugin side with API 3 — no app change is
required.** The pattern (the same RFC 8252 loopback flow the app uses for Community sign-in):

1. Bind a loopback HTTP server on `127.0.0.1:0` (a free port) inside the plugin process.
2. Open the broker's login URL in the system browser with
   `redirect_uri=http://127.0.0.1:<port>/callback` (Python `webbrowser.open`).
3. The browser redirects back with a one-time `code`; the plugin reads it from the request query, answers the
   browser with a small "you can close this tab" page, and exchanges the code for the access/refresh token.
4. Keep the token in `OPENTRADER_PLUGIN_DATA` (API 3 gives a per-plugin folder, removed with the plugin) with
   restrictive file permissions. The app's credential store is not reachable from a plugin programmatically —
   password settings are for values the user types in Settings — so the plugin's own data folder is the storage.
5. Use `plugin.prompt` (message-only) to show "Approve in the browser…" while waiting, and `plugin.promptDone`
   when the token arrives. `plugin.promptAnswer` is only for a code the user types back manually.

Scope and risks: works for brokers with an OAuth redirect-URI login; brokers with only API-key + TOTP don't need
it. The broker must allow `http://127.0.0.1` as a redirect URI (standard for native apps). It is desktop-only —
a web build cannot open a system browser or a local listener. Programmatic token storage in the app's
credential store would need a new app/protocol feature (a plugin-facing auth API), which upstream has not added;
adopt that instead if it appears, to avoid a fork-only protocol.
