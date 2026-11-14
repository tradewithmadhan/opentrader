# OpenTrader plugin: OpenAlgo

Your OpenAlgo deployment in OpenTrader: the OpenAlgo segments of your broker account (NSE, BSE, NFO, BFO, MCX,
CDS, BCD, NCO, NCDEX — and the index segments NSE_INDEX, BSE_INDEX, MCX_INDEX) on the charts, and its trading
from the charts. One file, Python 3.9+, standard library only. Plugin API 2, so the app speaks whole symbols
(`NSE:SBIN-EQ`): the exchange prefix is the app's exchange, the ticker is OpenAlgo's symbol. The wire behavior
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
   path, `wss://your-domain/ws`), **API key**, the exchanges to serve (the OpenAlgo segments exactly as the
   instrument master names them — the index segments included), and the default product.
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

1. On open: `{"action": "authenticate", "api_key": "<key>"}` — no answer is awaited (a bad key shows up as a
   close, and the loop backs off).
2. The whole subscription set, on every change: `{"action": "unsubscribe_all"}`, then
   `{"action": "subscribe", "mode": "Quote", "symbols": [{"exchange": "NSE", "symbol": "SBIN-EQ"}, …]}` —
   the venue halves, the key not in the frames.
3. A ping frame every 20 s; the server's pings are answered with pongs.
4. Incoming: `{"type": "market_data", "symbol": …, "exchange": …, "data": {open, high, low, ltp, volume,
   prev_close?, close}}` — on ticks `close` is the previous day's close and `prev_close` is often absent,
   so the two fall back through each other and the last quote. A bare echo resolves through the subscription
   when exactly one listing matches; an ambiguous one is dropped (the provider's identity rule).

`OPENALGO_DEBUG=1` prints all of this raw (`[ws >]` outgoing with the key redacted, `[ws <]` incoming) on the
console while you run `plugin.py` by hand.

## Files

| file | what it is |
| --- | --- |
| `plugin.py` | the plugin: the source plus OpenTrader's template connection layer |
| `opentrader-plugin.json` | the release manifest |
| `opentrader-plugin.dev.json` | the development manifest (external runtime) |
| `build.py` | the packager: `python build.py dev` / `release` |

## Notes

- The API key is sent only inside request payloads; every log line and error goes through a redaction
  (`_clean`) that strips it before it leaves the process.
- The instrument master is pulled once per segment per day, cached in memory and on disk under
  `{localAppData}\OpenTraderPlugins\openalgo-cache`.
- A long history request runs beside the quote stream: the source locks its own parts, so the template's outer
  lock is not taken in `handle` (two more deviations are documented at the top of `plugin.py`).
- Dates in the app's UI read dd/mm/yyyy; history windows and daily bars are keyed to IST midnight.
