#!/usr/bin/env python3
"""OpenTrader plugin: OpenAlgo — one account's market segments and its trading, over the OpenAlgo API.

One deployment of OpenAlgo (self-hosted REST + WebSocket) serves several exchange segments of one broker
account (NSE, BSE, NFO, MCX…). As a Plugin-API-2 plugin with `exchanges`, it is spoken to with whole symbols
("NSE:SBIN-EQ"): the exchange prefix is the app symbol's exchange, the ticker is OpenAlgo's symbol.

Settings (Settings > Plugins): apiUrl (REST base), wsUrl (full WebSocket URL, any path included), apiKey
(never logged), exchanges (the segments to serve), product (the default product type of orders).

Run it:
  - during development:  python plugin.py           (reads the connection file the app writes, see README)
  - when installed:      the app starts the built executable and passes the connection in the environment.

The connection layer (everything below the source) is OpenTrader's plugin template, kept as it is — with three
documented adaptations so a long request (a day of history) does not hold up the quote stream: the source locks
its own parts (no outer lock in `handle`), and the stream reads the source's live-price cache.
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN_ID = "openalgo"  # the manifests' id
PLUGIN_VERSION = "0.1.0"
API = 2  # the Plugin API version this plugin speaks (whole symbols)
DEV_CONNECTION_FILE = "OpenTraderPlugins/openalgo-dev.json"  # under {localAppData}, as in opentrader-plugin.dev.json
DEBUG = os.environ.get("OPENALGO_DEBUG") == "1"  # OPENALGO_DEBUG=1: raw stream traffic on stdout (the key is redacted)

TZ_IST = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")

# ── the market's clock: bars and sessions are keyed in the exchange's time zone (India: one) ──
def ist_midnight(ts):
    """The epoch seconds of `ts`'s trading day's midnight in IST (a daily bar's start)."""
    day = datetime.fromtimestamp(ts, TZ_IST).date()
    return int(datetime(day.year, day.month, day.day, tzinfo=TZ_IST).timestamp())


def ist_date(ts):
    return datetime.fromtimestamp(ts, TZ_IST).strftime("%Y-%m-%d")


def local_app_data():
    if sys.platform == "win32":
        return os.environ["LOCALAPPDATA"]
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support")
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def num(v, default=None):
    """A broker number that may arrive as a JSON string (`"1234.5"`) or not at all."""
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)) and v == v:  # not NaN
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return default
    return default


def first_of(d, *keys, default=None):
    """The first key of `keys` present in `d` (OpenAlgo field names differ a little per version)."""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _clean(text, secret):
    """A message with the API key removed (nothing secret reaches the log)."""
    return text.replace(secret, "…") if secret and secret in text else text


class PluginError(Exception):
    """An error answered to the app; `message` is shown to the user."""

    def __init__(self, message, code=-32000):
        super().__init__(message)
        self.code = code


# ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
# 1. The source. Every method is called from the app's requests; keep the return shapes. Prices are floats,
#    times UNIX seconds (bars, quotes) or milliseconds (account state). Each part locks its own state: a slow
#    /history call must not hold up the quote stream.
# ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

# Standard regular session per exchange, in the market's clock (broker docs table). The plugin does not build a
# full holiday calendar: bars show the trading days the history answers with.
SESSIONS = {
    "CDS": "0900-1700",
    "BCD": "0900-1700",
    "MCX": "0900-2330",
    # NSE, BSE, NFO, BFO, NCO, BCO and anything unlisted.
    "_": "0915-1530",
}

# The app resolution → OpenAlgo interval, when /intervals says nothing (verified against a deployment).
FALLBACK_MINUTES = {1: "1m", 3: "3m", 5: "5m", 10: "10m", 15: "15m", 20: "20m", 30: "30m", 60: "1h", 120: "2h"}
FALLBACK_HOURS = {1: "1h", 2: "2h"}
FALLBACK_SECONDS = {}

# The index segments of the master: pulled alongside their cash segment so index search is warm. Unlike the
# cash exchanges they are not charting aliases — an index keeps its own OpenAlgo prefix (NSE_INDEX:NIFTY), as
# the app's built-in OpenAlgo provider does (verified live: /history answers exchange=NSE_INDEX).
INDEX_SEGMENTS = {"NSE": "NSE_INDEX", "BSE": "BSE_INDEX", "MCX": "MCX_INDEX"}

KINDS = {  # instrument type → the app's search-row type (the dialog's tabs read it)
    "EQ": "stock", "BE": "stock", "SM": "stock", "MF": "fund", "MFP": "fund",
    "IDX": "index", "IND": "index",
    "FUT": "futures", "FUTCUR": "futures",
    "CE": "options", "PE": "options", "OPT": "options", "OPTFUT": "options", "OPTCUR": "options",
    "CUR": "forex",
}

FILTER_KINDS = {  # the dialog's Type chip code → the app kind it asks for (mirrors the provider's type_allows)
    "EQ": "stock", "STOCK": "stock",
    "FUT": "futures", "FUTURES": "futures",
    "CE": "options", "PE": "options", "OPT": "options", "OPTIONS": "options",
    "IX": "index", "INDEX": "index", "INDICES": "index",
}


def display_name(name, row):
    """A master row as the dialog shows it. Stocks keep the plain name; a contract with an expiry reads
    `Name Strike TYPE Expiry` (e.g. "NIFTY 26100 CE 30-Oct-26"), skipping missing parts — the broker marks
    "none" as an empty expiry and a negative strike, both read as absent. Same shape as the app provider."""
    expiry = str(first_of(row, "expiry", "expiry_date") or "").strip()
    if not expiry:
        return name
    parts = [name] if name else []
    strike = num(first_of(row, "strike", "strike_price"))
    if strike is not None and strike >= 0:
        parts.append(f"{strike:g}")
    kind = str(first_of(row, "instrumenttype", "instrument_type") or "").strip()
    if kind:
        parts.append(kind)
    parts.append(expiry)
    return " ".join(parts)

PRICE_TYPES = {"market": "MARKET", "limit": "LIMIT", "stop": "STOP", "stoplimit": "STOP-LIMIT"}
PRICE_KINDS = {  # the source's price type → the wire's (sl-m / sl-l are some adapters' spellings)
    "market": "market", "limit": "limit", "stop": "stop", "stoplimit": "stopLimit",
    "sl-m": "stop", "slm": "stop", "sl-l": "stopLimit", "sll": "stopLimit",
}


class Instruments:
    """The instrument master, one pull per exchange per day (an in-memory cache backed by a disk cache:
    the master files are big and the plugin restarts often)."""

    BACKOFF = 600.0  # seconds a failed pull (a segment the deployment does not serve, 429, the net down) waits

    def __init__(self, source):
        self.source = source  # the fetch goes through the source (it owns the settings)
        self.lock = threading.RLock()
        self.rows = {}  # master exchange → [row dict]
        self.day = {}  # master exchange → the day it was fetched
        self.cache_dir = Path(local_app_data()) / "OpenTraderPlugins" / "openalgo-cache"
        self.worker = None  # the segments are fetched one after another (a deployment rate-limits parallel pulls)
        self.loading = {}  # master exchange → the Event of its running pull (callers wait instead of refetching)
        self.unavailable = {}  # master exchange → until when (monotonic) a failed pull is not retried
        self.catalog = None  # the combined search catalog of every segment, built once and searched per keystroke

    def master_exchanges(self, exchanges):
        """The configured segments (the OpenAlgo segments themselves), plus the index tables of the cash ones
        the setting does not name."""
        out, seen = [], set()
        for e in exchanges:
            for seg in (e, INDEX_SEGMENTS.get(e, e)):
                if seg not in seen:
                    seen.add(seg)
                    out.append(seg)
        return out

    def rows_for(self, exchange):
        """The master rows of a segment, fetched at most once a day. One pull per segment at a time: the other
        callers wait for the running one instead of fetching again (search and the prefetch race otherwise)."""
        today = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        while True:
            with self.lock:
                if self.day.get(exchange) == today:
                    return self.rows.get(exchange) or []
                if time.monotonic() < self.unavailable.get(exchange, 0.0):
                    return self.rows.get(exchange) or []
                event = self.loading.get(exchange)
                if event is None:
                    event = self.loading[exchange] = threading.Event()
                    mine = True
                else:
                    mine = False
            if not mine:
                event.wait(120)  # the running pull decides; then the loop re-checks
                continue
            try:
                fetched = self._fetch(exchange)
                with self.lock:
                    if fetched is None:
                        self.unavailable[exchange] = time.monotonic() + self.BACKOFF
                    else:
                        self.unavailable.pop(exchange, None)
                        self.rows[exchange], self.day[exchange] = fetched, today
                        self.catalog = None  # the combined search catalog is stale
            finally:
                with self.lock:
                    self.loading.pop(exchange, None)
                event.set()
            with self.lock:
                return self.rows.get(exchange) or []

    def rows_peek(self, exchange):
        """The rows already here (memory, or today's disk cache) — search never starts a pull or waits for one."""
        today = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        with self.lock:
            if self.day.get(exchange) == today:
                return self.rows.get(exchange) or []
        disk = self._read_disk(exchange, today)
        if disk is not None:
            with self.lock:
                self.rows[exchange], self.day[exchange] = disk, today
                self.catalog = None  # the combined search catalog is stale
            return disk
        with self.lock:
            return self.rows.get(exchange) or []

    def catalog_get(self, exchanges):
        """The search catalog: every segment's rows flattened into one precomputed list
        `(symbol upper, name upper, broker symbol upper, ticker, type, description)`, so a keystroke scans
        one list instead of the segments' raw dicts. Rebuilt when a segment's rows change (a pull lands, the
        disk cache loads)."""
        with self.lock:
            if self.catalog is not None:
                return self.catalog
        out = []
        for exchange in self.master_exchanges(exchanges):
            for row in self.rows_peek(exchange):
                symbol = str(row.get("symbol") or "")
                if not symbol:
                    continue
                out.append((symbol.upper(), str(row.get("name") or "").upper(),
                            str(first_of(row, "brsymbol", "broker_symbol") or "").upper(), f"{exchange}:{symbol}",
                            self.source._kind_of(exchange, row), display_name(row.get("name") or symbol, row)))
        with self.lock:  # a concurrent rebuild is fine: both see the same rows, the last one wins
            self.catalog = out
        return out

    def prefetch(self, exchanges):
        """Fetch every segment in the background, one after another (started on configure), so search and symbols
        are warm. One worker: parallel pulls earn a 429 from the deployment."""
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.worker = threading.Thread(
                target=self._prefetch_all, args=(list(self.master_exchanges(exchanges)),), daemon=True)
            self.worker.start()
    def _prefetch_all(self, exchanges):
        for i, exchange in enumerate(exchanges):
            if i:
                time.sleep(2.0)  # a small gap between segments keeps the pulls under the rate limit
            try:
                self.rows_for(exchange)
            except Exception:  # noqa: BLE001 - a failed pull retries on the next use; the worker must not die
                pass

    def _fetch(self, exchange):
        return self.source._fetch_instruments(exchange)

    def _disk_path(self, exchange, day):
        return self.cache_dir / f"{exchange}-{day}.json"

    def _read_disk(self, exchange, day):
        try:
            with open(self._disk_path(exchange, day), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _write_disk(self, exchange, day, rows):
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._disk_path(exchange, day), "w", encoding="utf-8") as f:
                json.dump(rows, f)
        except OSError:
            pass


class OpenAlgoSource:
    POLL_EVERY = 2.0  # the books refresh at most this often (the WebSocket moves the prices between polls)

    def __init__(self):
        self.plugin = None  # set by Plugin: the connection the log lines go through
        self.lock = threading.RLock()
        self.settings = {}
        self.exchanges = []
        self.instruments = Instruments(self)
        self.intervals = None  # the normalized /intervals tables, or None until fetched
        self.intervals_day = None
        # live prices from the WebSocket: whole symbol → the last quote's fields
        self.quotes = {}
        self.minute_bars = {}  # whole symbol → the forming minute bar [time,o,h,l,c,v]
        self.day_bars = {}  # whole symbol → the forming day bar
        self.ws = None
        self.ws_thread = None
        self.ws_generation = 0
        self.ws_subscribed = set()  # what the stream last subscribed (compared with `subscribed`)
        self.subscribed = set()
        self.ws_shape_warned = False  # the "no price read" warn says its piece only once
        # the account, as the books last answered
        self.funds = None
        self.positions = []
        self.orders = []
        self.executions = []
        self.last_poll = 0.0
        self.rest_ok = False

    # ── settings ──
    def configure(self, settings):
        """The user's values of the manifest's settings (all strings)."""
        with self.lock:
            api_changed = (
                settings.get("apiUrl") != self.settings.get("apiUrl")
                or settings.get("wsUrl") != self.settings.get("wsUrl")
                or settings.get("apiKey") != self.settings.get("apiKey")
            )
            previous_exchanges = self.exchanges
            self.settings = dict(settings)
            self.exchanges = [e.strip().upper() for e in settings.get("exchanges", "").split(",") if e.strip()]
            gen = self.ws_generation + 1
        if self.instruments.master_exchanges(self.exchanges) != self.instruments.master_exchanges(previous_exchanges):
            self.instruments.catalog = None  # a different setting searches different segments
        self.instruments.prefetch(self.exchanges)
        if api_changed or not self.ws_thread_alive():
            self._start_ws(gen)

    def ws_thread_alive(self):
        t = self.ws_thread
        return bool(t and t.is_alive())

    def log(self, message, level="info"):
        """A line in the plugin's log: through the connection when it is up, else to stdout (run by hand)."""
        p = self.plugin
        if p and p.alive:
            p.log(message, level)
        else:
            print(message, flush=True)

    def _start_ws(self, gen):
        """(Re)start the stream thread: a new generation makes the old loop leave (settings changed)."""
        self.ws_generation = gen
        t = threading.Thread(target=self._ws_loop, args=(gen,), daemon=True)
        self.ws_thread = t
        t.start()

    def _base(self):
        return self.settings.get("apiUrl", "").rstrip("/")

    def _key(self):
        return self.settings.get("apiKey", "")

    # ── REST ──
    def _post(self, path, body, whole=False):
        """One OpenAlgo /api/v1 call; returns its `data` — or the whole answer with `whole`
        (placeorder's order id sits at the top level, verified live)."""
        base, key = self._base(), self._key()
        if not base or not key:
            raise PluginError("The OpenAlgo plugin has no API URL or key yet — fill them in Settings > Plugins")
        payload = dict(body)
        payload["apikey"] = key
        request = urllib.request.Request(
            f"{base}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as r:
                answer = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                answer = json.loads(e.read().decode("utf-8"))
            except (OSError, ValueError):
                raise PluginError(f"OpenAlgo answered HTTP {e.code}") from e
        except (OSError, ValueError) as e:
            self.rest_ok = False
            raise PluginError(f"OpenAlgo is not reachable ({_clean(str(e), key)})") from e
        if str(answer.get("status", "")).lower() not in ("success", "ok"):
            raise PluginError(f"OpenAlgo: {answer.get('message') or 'the request failed'}")
        self.rest_ok = True
        return answer if whole else answer.get("data")

    def _fetch_instruments(self, exchange):
        """The master of one segment, straight from the source."""
        key = self._key()
        base = self._base()
        if not base or not key:
            return None
        url = f"{base}/api/v1/instruments?apikey={urllib.parse.quote(key)}&exchange={exchange}&format=json"
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                answer = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self.log(f"instruments {exchange}: HTTP {e.code}"
                     + (" (rate limited; the pull retries on the next use)" if e.code == 429 else ""), "warn")
            return None
        except (OSError, ValueError) as e:
            self.log(f"instruments {exchange}: not reachable ({_clean(str(e), key)})", "warn")
            return None
        if str(answer.get("status", "")).lower() not in ("success", "ok"):
            self.log(f"instruments {exchange}: {answer.get('message') or 'no answer'}", "warn")
            return None
        data = answer.get("data")
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):  # some adapters wrap the rows in an object
            rows = next((v for v in data.values() if isinstance(v, list)), [])
        else:
            rows = []
        self.log(f"instruments {exchange}: {len(rows)} rows")
        return rows

    # ── market data ──
    def known(self, ticker):
        """True when `ticker` ("NSE:SBIN-EQ") is one of the plugin's symbols."""
        exchange, sep, symbol = ticker.partition(":")
        return bool(sep and symbol) and exchange in self.exchanges

    def tick(self, ticker):
        """One price step of `ticker`, from the master's tick size (the ticket's bid/ask spread)."""
        row = self._instrument_row(ticker)
        size = num(first_of(row or {}, "tick_size", "ticksize"))
        return size or 0.05

    def _instrument_row(self, ticker):
        exchange, _, symbol = ticker.partition(":")
        for row in self.instruments.rows_for(exchange):  # the row lives in the segment the symbol names itself
            if row.get("symbol") == symbol:
                return row
        return None

    def _kind_of(self, segment, row):
        """The app's search-row type: the index segments are indexes; the rest map by instrumenttype."""
        if segment.upper().endswith("_INDEX"):
            return "index"
        kind = str(first_of(row, "instrumenttype", "instrument_type") or "").upper()
        return KINDS.get(kind, "stock")

    def search(self, query, type_filter=None):
        """Up to 50 rows `{ticker, description, type}`, ticker whole ("NSE:SBIN-EQ").

        The combined catalog (every segment in one precomputed list) is scanned as a whole: a symbol-prefix
        match outranks a symbol match, then a name or broker-symbol match (the app provider's tiers), and the
        budget is spread over the exchanges round-robin — a broad query must not spend the whole budget on
        NSE's 10 000 rows and starve BSE or MCX. `type_filter` is the dialog's Type chip (EQ, FUT, CE, PE,
        IX, …): only the rows of that kind answer. Only what is already here is searched: a segment still
        loading joins the catalog when its pull lands."""
        q = query.strip().upper()
        prefix, _, rest = q.partition(":")
        if rest and (prefix in self.exchanges or prefix in INDEX_SEGMENTS.values()):
            q = rest.strip()  # "NSE:SBIN" searches for SBIN; the app filters the rows by its prefix
        want = FILTER_KINDS.get(str(type_filter or "").strip().upper())
        if q:
            tiers = ({}, {}, {})  # exchange → [(symbol, ticker, kind, description)], per tier
            for symbol_up, name_up, br_up, ticker, kind, description in self.instruments.catalog_get(self.exchanges):
                if want and kind != want:
                    continue
                hit = (symbol_up, ticker, kind, description)
                exchange = ticker.split(":", 1)[0]
                if symbol_up.startswith(q):
                    tiers[0].setdefault(exchange, []).append(hit)
                elif q in symbol_up:
                    tiers[1].setdefault(exchange, []).append(hit)
                elif q in name_up or q in br_up:
                    tiers[2].setdefault(exchange, []).append(hit)
            for bucket in tiers:
                for hits in bucket.values():
                    hits.sort()
        else:  # an empty query: the catalog's own order (the segments in the setting's order)
            first = {}
            for symbol_up, _, _, ticker, kind, description in self.instruments.catalog_get(self.exchanges):
                if want and kind != want:
                    continue
                first.setdefault(ticker.split(":", 1)[0], []).append((symbol_up, ticker, kind, description))
            tiers = (first, {}, {})
        out, seen = [], set()
        for bucket in tiers:
            segs = [bucket[e] for e in self.instruments.master_exchanges(self.exchanges) if e in bucket]
            while segs and len(out) < 50:
                for hits in list(segs):
                    while hits:
                        _, ticker, kind, description = hits.pop(0)
                        if ticker in seen:
                            continue
                        seen.add(ticker)
                        out.append({"ticker": ticker, "description": description, "type": kind})
                        break
                    if not hits:
                        segs.remove(hits)
        return out

    def symbol_info(self, ticker):
        """The symbol's shape: description, type, currency, sessions, tick size, the intervals served."""
        row = self._instrument_row(ticker)
        if row is None:
            raise PluginError(f"Unknown symbol {ticker}", -32602)
        exchange = ticker.split(":", 1)[0]
        tick = num(first_of(row, "tick_size", "ticksize")) or 0.05
        pricescale = max(2, round(1 / tick)) if tick < 1 else 100
        return {
            "description": row.get("name") or ticker.partition(":")[2],
            "type": self._kind_of(exchange, row),
            "currency": "INR",
            "timezone": "Asia/Kolkata",
            "session": SESSIONS.get(exchange, SESSIONS["_"]),
            "pricescale": pricescale,
            "minmov": 1,
            "intervals": self._served_intervals(),
        }

    def _served_intervals(self):
        """The resolutions the plugin maps straight onto the source (the app builds the others)."""
        tables = self._interval_tables()
        out = ["1", "1D"]
        for mult in sorted(tables["minutes"]):
            if str(mult) not in out:
                out.append(str(mult))
        for mult in sorted(tables["hours"]):
            hour = int(mult) * 60
            if str(hour) not in out:
                out.append(str(hour))
        for mult in sorted(tables["seconds"]):
            out.append(f"{mult}S")
        return out

    def _interval_tables(self):
        """The resolution→token tables of /api/v1/intervals, refreshed daily; the fallback tables when cold."""
        with self.lock:
            today = datetime.now(TZ_IST).strftime("%Y-%m-%d")
            if self.intervals is not None and self.intervals_day == today:
                return self.intervals
        try:
            data = self._post("/api/v1/intervals", {}) or {}
        except PluginError:
            data = None
        tables = {
            "minutes": dict(FALLBACK_MINUTES),
            "hours": dict(FALLBACK_HOURS),
            "seconds": dict(FALLBACK_SECONDS),
            "days": ["D"],
            "weeks": ["W"],
            "months": ["M"],
        }
        if isinstance(data, dict):
            def table_of(v, unit):
                """mult → token, either shape: a dict ({'1': '1m'}) or a list of tokens whose numbers are
                the multipliers of `unit` (['1m', '3m', …] — verified live)."""
                if isinstance(v, dict):
                    return {int(k): str(x) for k, x in v.items() if str(x).strip()}
                out = {}
                if isinstance(v, list):
                    for token in v:
                        token = str(token).strip()
                        if token[:-1].isdigit() and token[-1:].lower() == unit:
                            out[int(token[:-1])] = token
                return out
            if data.get("minutes"):
                tables["minutes"] = table_of(data["minutes"], "m")
            if data.get("hours"):
                tables["hours"] = table_of(data["hours"], "h")
            if data.get("seconds"):
                tables["seconds"] = table_of(data["seconds"], "s")
            for key in ("days", "weeks", "months"):
                if isinstance(data.get(key), list) and data[key]:
                    tables[key] = [str(x) for x in data[key]]
        with self.lock:
            self.intervals, self.intervals_day = tables, today
        return tables

    def _interval_for(self, resolution):
        """The app's resolution string ("15", "60", "15S", "1D", "1W") → the source's interval token."""
        tables = self._interval_tables()
        if resolution[-1:] in ("S", "s") and resolution[:-1].isdigit():
            token = tables["seconds"].get(int(resolution[:-1]))
            if token:
                return token
            raise PluginError(f"Resolution {resolution} is not served by your OpenAlgo deployment", -32602)
        if resolution in ("1D", "D") and "D" in tables["days"]:
            return "D"
        if resolution in ("1W", "W") and "W" in tables["weeks"]:
            return "W"
        if resolution in ("1M", "M") and "M" in tables["months"]:
            return "M"
        if resolution.isdigit():
            mult = int(resolution)
            if mult % 60 == 0 and mult // 60 in tables["hours"]:
                return tables["hours"][mult // 60]
            token = tables["minutes"].get(mult)
            if token:
                return token
            raise PluginError(f"Resolution {resolution} is not served by your OpenAlgo deployment", -32602)
        raise PluginError(f"Resolution {resolution} is not served", -32602)

    def bars(self, ticker, resolution, frm, to, count_back):
        exchange, _, symbol = ticker.partition(":")
        interval = self._interval_for(resolution)
        try:
            if interval in ("D", "W", "M"):
                return self._daily(exchange, symbol, interval, frm, to, count_back)
            return self._window(exchange, symbol, interval, False, frm, to, count_back, 0)
        except PluginError as e:
            self.log(f"history {ticker} {interval}: {e}", "warn")  # the chart only says "no data"; the log says why
            raise

    def _daily(self, exchange, symbol, interval, frm, to, count_back):
        """A day window's bars. Deployments whose D endpoint answers only its last day (verified live: this one
        answers a single row for every window) get their days synthesized from the intraday bars it serves
        fully — hourly when the intervals table has one, else the finest minute table, chunked."""
        # synthesizing across years would fetch tens of thousands of intraday bars (very slow); the deployment's
        # intraday history only reaches back about a year anyway, so cap the synthesis window to that.
        if to - frm > 365 * 86400:
            frm = to - 365 * 86400
        bars = self._window(exchange, symbol, interval, True, frm, to, count_back, 0)
        if len(bars) >= 2 or to - frm <= 3 * 86400:
            return bars
        tables = self._interval_tables()
        hour = tables["hours"].get(1)
        if hour:
            try:
                return self._days_of(self._window(exchange, symbol, hour, False, frm, to, 0, 0), count_back)
            except PluginError as e:
                self.log(f"history {exchange}:{symbol} {interval}: synthesizing from {hour} failed ({e}); the day bar alone", "warn")
                return bars
        minute = min(tables["minutes"], default=0)
        if not minute:
            return bars
        try:
            intraday, start = [], frm
            chunk = 30 * 86400  # the source's minute range cap (the app provider's, mirrored)
            while start <= to:
                end = min(start + chunk, to)
                intraday += self._window(exchange, symbol, tables["minutes"][minute], False, start, end, 0, 0)
                start = end + 1
            return self._days_of(intraday, count_back)
        except PluginError as e:
            self.log(f"history {exchange}:{symbol} {interval}: synthesizing from {tables['minutes'][minute]} failed ({e}); the day bar alone", "warn")
            return bars

    def _days_of(self, intraday, count_back):
        """Intraday bars folded into days: open the first, close the last, high/low the extremes, volume
        summed; a day bar sits at its trading day's midnight in the market's clock."""
        days = []
        for b in sorted(intraday, key=lambda x: x[0]):
            day = ist_midnight(int(b[0]))
            if days and days[-1][0] == day:
                days[-1][2] = max(days[-1][2], b[2])
                days[-1][3] = min(days[-1][3], b[3])
                days[-1][4], days[-1][5] = b[4], (days[-1][5] or 0) + (b[5] or 0)
            else:
                days.append([day, b[1], b[2], b[3], b[4], b[5] or 0])
        return days[-count_back:] if count_back else days

    def _window(self, exchange, symbol, interval, daily, frm, to, count_back, depth):
        """A history window, split in half and retried when the source answers only part of it
        (OpenAlgo caps a window's bars; the halves reach past the cap)."""
        if depth > 10:
            return []
        body = {
            "symbol": symbol, "exchange": exchange, "interval": interval,
            "start_date": ist_date(frm), "end_date": ist_date(to),
        }
        try:
            data = self._post("/api/v1/history", body)
        except PluginError:
            if depth == 0:
                raise  # the first window's error is what reaches the user
            return []  # a retried half failing is normal (the cap)
        rows = data if isinstance(data, list) else (data or {}).get("candles") or []
        bars = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            ts, o, h, low, c = num(r.get("timestamp")), num(r.get("open")), num(r.get("high")), num(r.get("low")), num(r.get("close"))
            v = num(r.get("volume"), 0)
            if ts is None or o is None or h is None or low is None or c is None:
                continue
            t = int(ts) // 1000 if ts > 1e12 else int(ts)
            if daily:  # a daily bar sits at its trading day's midnight in the market's clock
                t = ist_midnight(t)
            if bars and bars[-1][0] == t:  # a repeated stamp (an adapter quirk): widen the older bar
                bars[-1] = [t, bars[-1][1], max(bars[-1][2], h), min(bars[-1][3], low), c, (bars[-1][5] or 0) + (v or 0)]
            else:
                bars.append([t, o, h, low, c, v or 0])
        bars.sort(key=lambda b: b[0])
        if len(bars) > count_back:
            bars = bars[-count_back:]
        if len(bars) >= 2 and len(bars) < count_back and to - frm > 3 * 86400:
            if bars[0][0] > frm + 1 or bars[-1][0] < to - 1:  # the answer does not span the window: split it
                mid = frm + (to - frm) // 2
                lower = self._window(exchange, symbol, interval, daily, frm, mid, count_back, depth + 1)
                upper = self._window(exchange, symbol, interval, daily, mid + 1, to, count_back, depth + 1)
                merged = list(lower)
                have = {b[0] for b in merged}
                merged += [b for b in upper + bars if b[0] not in have]
                merged.sort(key=lambda b: b[0])
                return merged[-count_back:]
        return bars

    def quote(self, ticker):
        """The last price and the forming minute / day bar, from the WebSocket cache."""
        with self.lock:
            q = self.quotes.get(ticker)
            minute = list(self.minute_bars.get(ticker) or [])
            day = list(self.day_bars.get(ticker) or [])
        if not q:
            return None
        now = int(time.time())
        return {"ticker": ticker, "time": now, "price": q["price"], "prevClose": q.get("prevClose"),
                "minute": self._complete(minute, now // 60 * 60, q),
                "day": self._complete(day, ist_midnight(now), q)}

    def _complete(self, bar, start, q):
        """A forming bar's shape [time,o,h,l,c,v], seeded from the quote's day fields when it is empty."""
        p = q["price"]
        if not bar or bar[0] != start:
            return [start, q.get("open") or p, q.get("high") or p, q.get("low") or p, p, q.get("volume") or 0]
        bar[2], bar[3] = max(bar[2], q.get("high") or p), min(bar[3], q.get("low") or p)
        bar[4], bar[5] = p, q.get("volume") or bar[5] or 0
        return bar

    # ── broker ──
    def resolve_symbol(self, symbol, ticker):
        """The plugin's ticker of a chart symbol, or None. Its own symbols trade as they are."""
        return symbol if self.known(symbol) else None

    def place(self, p):
        """A new order; returns its id, raises PluginError to refuse (shown as a rejected order)."""
        ticker, side, kind = p["ticker"], p["side"], p["type"]
        qty = num(p.get("qty"))
        if not self.known(ticker):
            raise PluginError(f"{ticker} is not one of this account's symbols")
        if not qty or qty <= 0:
            raise PluginError("The quantity must be above 0")
        exchange, _, symbol = ticker.partition(":")
        body = {
            "symbol": symbol, "exchange": exchange,
            "product": (self.settings.get("product", "MIS").upper() or "MIS"),
            "pricetype": PRICE_TYPES.get(kind, "LIMIT"),
            "quantity": int(qty),
            "price": num(p.get("limitPrice")) or 0,
            "trigger_price": num(p.get("stopPrice")) or 0,
            # the API wants these two even though they say nothing new here: action repeats the side
            # and strategy is a free tag (verified live — without them placeorder answers a field error)
            "action": side.upper(),
            "strategy": self.settings.get("strategy") or "OpenTrader",
        }
        answer = self._post("/api/v1/placeorder", body, whole=True) or {}
        data = answer.get("data")
        order_id = str(first_of(answer, "orderid", "order_id")
                       or first_of(data if isinstance(data, dict) else {}, "orderid", "order_id") or "")
        if not order_id:
            raise PluginError("OpenAlgo accepted the order but gave no order id")
        now_ms = int(time.time() * 1000)
        with self.lock:
            self.orders = [o for o in self.orders if o["id"] != order_id] + [
                {"id": order_id, "ticker": ticker, "side": side, "type": kind, "qty": qty, "filledQty": 0,
                 "limitPrice": num(p.get("limitPrice")), "stopPrice": num(p.get("stopPrice")),
                 "status": "placing", "duration": "day", "updatedMs": now_ms}]
        self.force_poll()
        return order_id, [{"kind": "placed", "ticker": ticker, "side": side, "type": kind, "qty": qty}]

    def modify(self, p):
        with self.lock:
            order = next((o for o in self.orders if o["id"] == p["orderId"]), None)
        if not order or order["status"] not in ("placing", "working"):
            raise PluginError("The order is no longer working")
        exchange, _, symbol = order["ticker"].partition(":")
        body = {
            "orderid": order["id"], "symbol": symbol, "exchange": exchange,
            "product": (self.settings.get("product", "MIS").upper() or "MIS"),
            "pricetype": PRICE_TYPES.get(order["type"], "LIMIT"),
            "quantity": int(order["qty"]),
            "price": num(p.get("limitPrice")) or num(order.get("limitPrice")) or 0,
            "trigger_price": num(p.get("stopPrice")) or num(order.get("stopPrice")) or 0,
            "action": order["side"].upper(),
            "strategy": self.settings.get("strategy") or "OpenTrader",
            # the API wants the field even though the plugin never sets one (verified live)
            "disclosed_quantity": 0,
        }
        self._post("/api/v1/modifyorder", body)
        with self.lock:
            order.update(qty=num(p.get("qty")) or order["qty"],
                         limitPrice=num(p.get("limitPrice")), stopPrice=num(p.get("stopPrice")),
                         updatedMs=int(time.time() * 1000))
            order = dict(order)
        self.force_poll()
        return [{"kind": "modified", "ticker": order["ticker"], "side": order["side"], "type": order["type"], "qty": order["qty"]}]

    def cancel(self, order_id):
        with self.lock:
            order = next((o for o in self.orders if o["id"] == order_id), None)
        if not order or order["status"] not in ("placing", "working"):
            raise PluginError("The order is no longer working")
        self._post("/api/v1/cancelorder", {"orderid": order_id, "strategy": self.settings.get("strategy") or "OpenTrader"})
        self.force_poll()
        return [{"kind": "cancelled", "ticker": order["ticker"], "side": order["side"], "type": order["type"], "qty": order["qty"]}]

    def close_position(self, position_id, qty):
        with self.lock:
            pos = next((x for x in self.positions if x["id"] == position_id), None)
        if pos is None:
            raise PluginError("No such position")
        exchange, _, symbol = pos["ticker"].partition(":")
        if qty is None:  # the whole position: the source's own close
            self._post("/api/v1/closeposition", {"symbol": symbol, "exchange": exchange,
                                                 "product": (self.settings.get("product", "MIS").upper() or "MIS"),
                                                 "strategy": self.settings.get("strategy") or "OpenTrader"})
            events = [{"kind": "placed", "ticker": pos["ticker"], "side": "sell" if pos["side"] == "buy" else "buy",
                       "type": "market", "qty": pos["qty"]}]
        else:  # a part of it: a market order the other way (its answer carries the events)
            events = self.place({"ticker": pos["ticker"], "side": "sell" if pos["side"] == "buy" else "buy",
                                 "type": "market", "qty": qty})[1]
        self.force_poll()
        return events

    def set_position_brackets(self, position_id, take_profit, stop_loss):
        raise PluginError("OpenAlgo does not move a position's take profit / stop loss from the plugin yet", -32601)

    def state(self):
        """The whole account, as `broker.state` sends it."""
        with self.lock:
            positions = [dict(x) for x in self.positions]
            orders = sorted(self.orders, key=lambda o: o.get("updatedMs") or 0)[-200:]
            executions = list(self.executions[-200:])
            funds = dict(self.funds) if isinstance(self.funds, dict) else {}
            connected = bool(self.rest_ok)
        for pos in positions:  # the open P&L moves with the WebSocket price
            q = self.quotes.get(pos["ticker"])
            if q and pos.get("avgPrice"):
                pos["last"] = q["price"]
                pos["unrealizedPnl"] = round((q["price"] - pos["avgPrice"]) * pos["qty"] * (1 if pos["side"] == "buy" else -1), 2)
        balance = num(first_of(funds, "availablecash", "cash", "totalfundvalue"), 0.0) or 0.0
        unrealized = num(first_of(funds, "m2munrealized", "unrealized"), 0.0) or 0.0
        realized = num(first_of(funds, "m2mrealized", "realized"), 0.0) or 0.0
        margin_used = num(first_of(funds, "marginsutilized", "marginsused", "marginused"), 0.0) or 0.0
        known = {"availablecash", "cash", "totalfundvalue", "m2munrealized", "m2mrealized", "unrealized", "realized",
                 "marginsutilized", "marginsused", "marginused"}
        account = {
            "id": "OPENALGO", "name": "OpenAlgo account", "currency": "INR", "isDemo": False,
            "balance": round(balance, 2), "equity": round(balance + unrealized, 2),
            "unrealizedPnl": round(unrealized, 2), "realizedPnl": round(realized, 2),
            "marginUsed": round(margin_used, 2), "marginFree": round(balance, 2),
            "values": [{"label": k, "value": str(v)} for k, v in funds.items() if k not in known][:8],
        }
        return {
            "connected": connected,
            "accounts": [{"id": account["id"], "name": account["name"]}],
            "account": account,
            "positionMode": "netting",
            "capabilities": {"orderTypes": ["market", "limit", "stop", "stopLimit"], "brackets": False,
                             "positionBrackets": False, "partialClose": True, "modifyOrders": True},
            "positions": positions,
            "orders": orders,
            "executions": executions,
        }

    def on_tick(self):
        """Every second: the books polled (throttled), status changes becoming events."""
        if not self._base() or not self._key():
            return []
        now = time.time()
        with self.lock:
            due = now - self.last_poll >= self.POLL_EVERY
            work = bool(self.positions) or any(o.get("status") in ("placing", "working") for o in self.orders)
        if not (due and (work or now - self.last_poll > 15)):
            return []
        self.last_poll = now
        try:
            return self._refresh_books()
        except PluginError as e:
            self.log(str(e), "warn")
            return []

    def force_poll(self):
        """The books read at once after an order command (its answer may already be stale)."""
        self.last_poll = 0.0

    def _refresh_books(self):
        """Funds, positions, orders and executions read fresh; status changes become events."""
        funds = self._post("/api/v1/funds", {})
        orderbook = self._list(self._post("/api/v1/orderbook", {}), "orders")
        positionbook = self._list(self._post("/api/v1/positionbook", {}), "positions")
        try:
            tradebook = self._list(self._post("/api/v1/tradebook", {}), "trades")
        except PluginError:
            tradebook = []  # some deployments gate the trade book; the books still work
        events = []
        with self.lock:
            self.funds = funds if isinstance(funds, dict) else {}
            previous = {o["id"]: o.get("status") for o in self.orders}
            orders = [o for o in (self._wire_order(r) for r in orderbook) if o]
            self.orders = orders
            self.positions = [p for p in (self._wire_position(r) for r in positionbook) if p and p["qty"] > 0]
            self.executions = [e for e in (self._wire_execution(r) for r in tradebook) if e]
            for order in orders:
                was = previous.get(order["id"])
                if was in (None, "placing") and order["status"] == "filled":
                    events.append({"kind": "executed", "ticker": order["ticker"], "side": order["side"],
                                   "type": order["type"], "qty": order["qty"], "price": order.get("avgFillPrice")})
                elif was in (None, "placing", "working") and order["status"] == "cancelled":
                    events.append({"kind": "cancelled", "ticker": order["ticker"], "side": order["side"],
                                   "type": order["type"], "qty": order["qty"]})
                elif was in (None, "placing", "working") and order["status"] == "rejected":
                    events.append({"kind": "rejected", "ticker": order["ticker"], "side": order["side"],
                                   "type": order["type"], "qty": order["qty"], "message": order.get("message")})
        return events

    def _list(self, data, key):
        """The books answer as a list, or as an object with the rows in it (an adapter quirk)."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            rows = data.get(key)
            if isinstance(rows, list):
                return rows
        return []

    STATUS_MAP = {
        "open": "working", "pending": "working", "trigger pending": "working", "new": "working",
        "complete": "filled", "completed": "filled", "filled": "filled", "executed": "filled",
        "cancelled": "cancelled", "canceled": "cancelled",
        "rejected": "rejected", "reject": "rejected",
    }

    def _wire_order(self, r):
        exchange = str(first_of(r, "exchange", "exchseg") or "").upper()
        symbol = str(first_of(r, "symbol", "tsym") or "")
        if not symbol:
            return None
        status = str(first_of(r, "order_status", "status", "orderstatus") or "").lower().strip()
        kind = str(first_of(r, "pricetype", "price_type", "prc_type") or "limit").lower().replace("-", "").replace(" ", "")
        side = str(first_of(r, "transaction_type", "side", "action") or "buy").lower()
        return {
            "id": str(first_of(r, "orderid", "order_id") or ""),
            "ticker": f"{exchange}:{symbol}",
            "side": "sell" if side.startswith("s") else "buy",
            "type": PRICE_KINDS.get(kind, "limit"),
            "qty": num(first_of(r, "quantity", "qty"), 0) or 0,
            "filledQty": num(first_of(r, "filled_quantity", "filledqty", "filled"), 0) or 0,
            "limitPrice": num(first_of(r, "price")),
            "stopPrice": num(first_of(r, "trigger_price", "triggerprice")),
            "avgFillPrice": num(first_of(r, "average_price", "avg_price", "averageprice")),
            "status": self.STATUS_MAP.get(status, "working"),
            "duration": "day",
            "message": first_of(r, "reason", "message", "remarks"),
            "updatedMs": self._ms(first_of(r, "updatetime", "update_time", "timestamp")),
        }

    def _wire_position(self, r):
        exchange = str(first_of(r, "exchange", "exchseg") or "").upper()
        symbol = str(first_of(r, "symbol", "tsym") or "")
        if not symbol:
            return None
        qty = num(first_of(r, "netqty", "quantity", "qty"), 0) or 0
        return {
            "id": f"{exchange}:{symbol}",
            "ticker": f"{exchange}:{symbol}",
            "side": "sell" if qty < 0 else "buy",
            "qty": abs(qty),
            "avgPrice": num(first_of(r, "average_price", "avg_price", "buyavgprice", "sellavgprice"), 0) or 0,
            "last": None,
            "unrealizedPnl": num(first_of(r, "pnl", "unrealized", "m2m")),
            "realizedPnl": num(first_of(r, "realized", "m2mrealized")),
            "takeProfit": None,
            "stopLoss": None,
        }

    def _wire_execution(self, r):
        exchange = str(first_of(r, "exchange", "exchseg") or "").upper()
        symbol = str(first_of(r, "symbol", "tsym") or "")
        if not symbol:
            return None
        side = str(first_of(r, "transaction_type", "side") or "buy").lower()
        return {
            "id": str(first_of(r, "tradeid", "executionid", "orderid") or ""),
            "orderId": str(first_of(r, "orderid", "order_id") or ""),
            "ticker": f"{exchange}:{symbol}",
            "side": "sell" if side.startswith("s") else "buy",
            "qty": num(first_of(r, "quantity", "qty"), 0) or 0,
            "price": num(first_of(r, "average_price", "avg_price", "price"), 0) or 0,
            "timeMs": self._ms(first_of(r, "timestamp", "trade_time", "updatetime")),
            "commission": num(first_of(r, "commission")),
            "realizedPnl": num(first_of(r, "realized", "pnl")),
        }

    def _ms(self, v):
        """A broker timestamp as UNIX milliseconds (0 when unreadable)."""
        n = num(v)
        if n is None:
            return 0
        return int(n * 1000) if n < 1e12 else int(n)

    # ── the WebSocket: live prices ──
    def _ws_loop(self, generation):
        """Connect to `wsUrl`, subscribe the wanted tickers, keep the price cache fresh. Retries quietly."""
        while generation == self.ws_generation:
            url, key = self.settings.get("wsUrl", ""), self._key()
            if not url or not key:
                time.sleep(5)
                continue
            try:
                self._ws_once(url, key, generation)
            except Exception as e:  # noqa: BLE001 - the stream is best effort; it retries
                with self.lock:
                    self.ws = None
                self.log(f"OpenAlgo stream reconnecting ({_clean(str(e), key)})", "warn")
            if generation != self.ws_generation:
                return
            time.sleep(3)

    def _ws_once(self, url, key, generation):
        """One WebSocket connection.

        OpenAlgo's stream speaks a small JSON dialect; both directions adapt in this one place — the subscribe
        payload below and `_ws_message` are the two things to verify against your deployment on first run.
        """
        import base64
        import ssl
        import struct

        parsed = urllib.parse.urlparse(url)
        secure = parsed.scheme == "wss"
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if secure else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        sock = socket.create_connection((host, port), timeout=10)
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        challenge = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {challenge}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        response = b""
        while b"\r\n\r\n" not in response:
            part = sock.recv(4096)
            if not part:
                raise PluginError("the stream closed during the handshake")
            response += part
        if b"101" not in response.split(b"\r\n", 1)[0]:
            raise PluginError(f"the stream refused the handshake ({response.splitlines()[0].decode(errors='replace')})")
        sock.settimeout(1.0)  # the read loop wakes every second: the subscription check and the settings swap
        with self.lock:
            self.ws = sock
            self.ws_subscribed = set()
        self.log(f"OpenAlgo stream connected ({urllib.parse.urlsplit(url).netloc})")

        def send_text(text):
            data = text.encode("utf-8")
            header = bytearray([0x81])
            n = len(data)
            if n < 126:
                header.append(0x80 | n)
            elif n < 65536:
                header.append(0x80 | 126)
                header += struct.pack(">H", n)
            else:
                header.append(0x80 | 127)
                header += struct.pack(">Q", n)
            mask = os.urandom(4)
            header += mask
            sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

        def send_control(opcode):
            mask = os.urandom(4)
            sock.sendall(bytes(bytearray([opcode, 0x80]) + mask))

        # The verified protocol (the app's built-in provider, working live): authenticate with the key on open,
        # then the whole set — `unsubscribe_all` and `subscribe` with the venue halves, no key in the frames.
        def resync(wanted):
            send_text(json.dumps({"action": "unsubscribe_all"}))
            if wanted:
                symbols = [{"exchange": t.split(":", 1)[0], "symbol": t.split(":", 1)[1]}
                           for t in sorted(wanted) if ":" in t]
                text = json.dumps({"action": "subscribe", "mode": "Quote", "symbols": symbols})
                if DEBUG:
                    print(f"[ws >] {_clean(text, key)}", flush=True)
                send_text(text)
            with self.lock:
                self.ws_subscribed = set(wanted)

        send_text(json.dumps({"action": "authenticate", "api_key": key}))
        if DEBUG:
            print("[ws >] authenticate (key redacted)", flush=True)
        last_ping = 0.0
        told_empty = False
        buffer = b""
        while generation == self.ws_generation:
            try:
                part = sock.recv(65536)
                if not part:
                    raise PluginError("the stream closed")
                buffer += part
            except socket.timeout:
                pass  # wake: the subscription check, the ping, then read again
            except OSError:
                raise PluginError("the stream closed")
            while True:
                frame, buffer = self._ws_frame(buffer)
                if frame is None:
                    break
                if frame == ():
                    continue  # ping answered, control frame, or an incomplete read
                self._ws_message(frame)
            now = time.time()
            with self.lock:
                wanted = set(self.subscribed)
            if wanted != self.ws_subscribed:
                resync(wanted)  # the whole set again: the protocol has no incremental add / drop
            if not wanted and not told_empty:
                told_empty = True  # the price cache stays empty until something subscribes
                self.log("stream: the app has not subscribed symbols yet — open a chart or add one to the watchlist")
            if now - last_ping > 20:
                last_ping = now  # the provider pings every 20 s; a silent socket dies quietly otherwise
                send_control(0x89)

    def _ws_frame(self, buffer):
        """One WebSocket text frame from `buffer`: the payload bytes, or () for none yet / a control frame."""
        import struct

        if len(buffer) < 2:
            return (), buffer
        n = buffer[1] & 0x7F
        offset, length = 2, n
        if n == 126:
            if len(buffer) < 4:
                return (), buffer
            length = struct.unpack(">H", buffer[2:4])[0]
            offset = 4
        elif n == 127:
            if len(buffer) < 10:
                return (), buffer
            length = struct.unpack(">Q", buffer[2:10])[0]
            offset = 10
        if len(buffer) < offset + length:
            return (), buffer
        opcode = buffer[0] & 0x0F
        payload = buffer[offset:offset + length]
        rest = buffer[offset + length:]
        if opcode == 0x8:
            raise PluginError("the stream closed")  # close frame
        if opcode == 0x9:  # ping → pong
            try:
                sock = self.ws
                if sock:
                    sock.sendall(bytes(bytearray([0x8A, 0x80 | len(payload)]) + payload))
            except OSError:
                pass
            return (), rest
        if opcode == 0x1:
            return payload, rest
        if opcode == 0x2 and DEBUG:  # binary: the shapes above assume JSON text; show what arrived instead
            print(f"[ws <] binary frame: {payload[:60].hex()}", flush=True)
        return (), rest  # binary, pong, fragments: not ours

    def _ws_message(self, data):
        """One stream message into the price cache.

        The verified shape (the app's built-in provider, working live):
        `{"type": "market_data", "symbol": ..., "exchange": ..., "data": {open, high, low, ltp, volume,
        prev_close?, close}}` — on ticks `close` is the previous day's close and `prev_close` is often
        absent, so the two fall back through each other and the last quote. Run with OPENALGO_DEBUG=1 to
        see the raw traffic.
        """
        if DEBUG:
            print(f"[ws <] {_clean(data.decode('utf-8', 'replace'), self._key())[:300]}", flush=True)
        try:
            message = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(message, dict) or message.get("type") != "market_data":
            return  # an authenticate answer, an error, a heartbeat: not a price
        r = message.get("data")
        if not isinstance(r, dict):
            return
        exchange = str(message.get("exchange") or "").upper()
        symbol = str(message.get("symbol") or "")
        if exchange and symbol:
            ticker = f"{exchange}:{symbol}"
        elif symbol:
            ticker = symbol.upper() if ":" in symbol else self._subscribed_match(symbol.upper())
            if not ticker:
                return  # an ambiguous bare echo: no listing is attributed (the provider's rule)
        else:
            return
        price = num(first_of(r, "ltp", "last_price", "last", "price"))
        if price is None:
            if not self.ws_shape_warned:
                self.ws_shape_warned = True  # once: a market_data message without a price
                self.log("stream: a market_data message arrived without a price (run with OPENALGO_DEBUG=1 "
                         "to see the raw traffic)", "warn")
            return
        ts = num(first_of(r, "ltt", "timestamp", "ts", "time"))
        t = int(ts) if ts and ts < 1e12 else (int(ts / 1000) if ts else int(time.time()))
        prev = num(first_of(r, "prev_close", "previous_close")) or num(first_of(r, "close"))
        quote = {"time": t, "price": price,
                 "open": num(first_of(r, "open")), "high": num(first_of(r, "high")),
                 "low": num(first_of(r, "low")), "close": num(first_of(r, "close")),
                 "volume": num(first_of(r, "volume"), 0) or 0,
                 "prevClose": prev or (self.quotes.get(ticker) or {}).get("prevClose")}
        with self.lock:
            self.quotes[ticker] = quote
            start = t // 60 * 60
            bar = self.minute_bars.get(ticker)
            if not bar or bar[0] != start:
                self.minute_bars[ticker] = [start, price, price, price, price, quote["volume"]]
            else:
                bar[2], bar[3], bar[4], bar[5] = max(bar[2], price), min(bar[3], price), price, quote["volume"] or bar[5] or 0
            start = ist_midnight(t)
            bar = self.day_bars.get(ticker)
            if not bar or bar[0] != start:
                self.day_bars[ticker] = [start, quote["open"] or price, quote["high"] or price,
                                         quote["low"] or price, price, quote["volume"]]
            else:
                bar[2], bar[3], bar[4], bar[5] = (max(bar[2], quote["high"] or price), min(bar[3], quote["low"] or price),
                                                  price, quote["volume"] or bar[5] or 0)

    def _subscribed_match(self, bare):
        """The subscribed whole symbol a bare echo belongs to, when exactly one matches (the provider's
        identity rule: an ambiguous bare echo resolves to nothing rather than to the wrong listing)."""
        with self.lock:
            matches = [t for t in self.subscribed if t.split(":", 1)[-1] == bare]
        return matches[0] if len(matches) == 1 else None


# ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
# 2. The connection to OpenTrader. OpenTrader's plugin template, kept as it is — with two documented adaptations
#    for this source: no outer lock in `handle` (the source locks its own parts, so a long history request runs
#    beside the quote stream), and the subscription forwarded to the source's WebSocket.
# ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def connection():
    """Where to connect: the environment when the app started us, else the connection file (development)."""
    addr = os.environ.get("OPENTRADER_PLUGIN_ADDR")
    if addr:
        host, port = addr.rsplit(":", 1)
        return host, int(port), os.environ["OPENTRADER_PLUGIN_ID"], os.environ["OPENTRADER_PLUGIN_TOKEN"]
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(local_app_data(), DEV_CONNECTION_FILE)
    with open(path, encoding="utf-8") as f:
        c = json.load(f)
    return c["host"], int(c["port"]), c["plugin"], c["token"]


class Plugin:
    def __init__(self, source):
        self.source = source
        source.plugin = self  # the source's log lines go through this connection
        self.sock = None
        self.send_lock = threading.Lock()
        self.tickers = set()  # data.subscribe
        self.watched = set()  # broker.watchQuote
        self.alive = False

    # ── JSON-RPC ──
    def send(self, message):
        line = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self.send_lock:
            self.sock.sendall(line)

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def log(self, message, level="info"):
        """A line in the plugin's log in Settings (stdout reaches it too, but without a level)."""
        if self.alive:
            self.notify("log", {"level": level, "message": message})
        else:
            print(message, flush=True)

    def push_state(self):
        self.notify("broker.state", self.source.state())

    def events(self, events):
        for e in events:
            self.notify("broker.event", e)
        if events:
            self.push_state()

    def handle(self, method, p):
        """One request of the app: returns its result or raises. No outer lock: the source locks its parts, so a
        slow history request runs beside the quote stream."""
        s = self.source
        if method == "data.search":
            return {"symbols": s.search(p.get("query", ""), p.get("type"))[:50]}
        if method == "data.symbolInfo":
            return s.symbol_info(p["ticker"])
        if method == "data.bars":
            bars = s.bars(p["ticker"], p["resolution"], int(p["from"]), int(p["to"]), int(p.get("countBack", 2000)))
            self.log(f"data.bars {p['ticker']} {p['resolution']}: {len(bars)} bars")
            return {"bars": bars}
        if method == "broker.resolveSymbol":
            return {"ticker": s.resolve_symbol(p.get("symbol"), p.get("ticker"))}
        if method == "broker.selectAccount":
            return {}
        if method == "broker.place":
            order_id, events = s.place(p)
        elif method == "broker.modify":
            order_id, events = None, s.modify(p)
        elif method == "broker.cancel":
            order_id, events = None, s.cancel(p["orderId"])
        elif method == "broker.closePosition":
            order_id, events = None, s.close_position(p["positionId"], p.get("qty"))
        elif method == "broker.setPositionBrackets":
            s.set_position_brackets(p["positionId"], p.get("takeProfit"), p.get("stopLoss"))
            order_id, events = None, []
        else:
            raise PluginError(f"Unknown method {method}", -32601)
        self.events(events)
        if not events:
            self.push_state()
        return {"orderId": order_id} if method == "broker.place" else {}

    def on_request(self, message):
        method, p = message["method"], message.get("params") or {}
        self.log(f"{method} {json.dumps(p, default=str)[:200]}")  # every ask, so a silent failure can't hide
        try:
            result = self.handle(method, p)
            self.send({"jsonrpc": "2.0", "id": message["id"], "result": result})
        except PluginError as e:
            self.log(f"{method} failed: {e}", "warn")
            self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": e.code, "message": str(e)}})
        except (KeyError, TypeError, ValueError) as e:
            self.log(f"{method} failed: bad parameters ({e})", "warn")
            self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32602, "message": f"Bad parameters: {e}"}})
        except Exception as e:  # noqa: BLE001 - any failure is answered, never left hanging
            secret = self.source.settings.get("apiKey", "")
            self.log(f"{method} failed: {_clean(str(e), secret)}", "warn")
            self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": _clean(str(e), secret)}})

    def on_notification(self, method, p):
        if method == "plugin.configure":
            self.source.configure(p.get("settings", {}))
            self.forward_subscription()
        elif method == "data.subscribe":
            self.tickers = set(p.get("tickers", []))
            self.log(f"data.subscribe: {len(self.tickers)} tickers")
            self.forward_subscription()
        elif method == "broker.watchQuote":
            self.watched = set(p.get("tickers", []))
            self.log(f"broker.watchQuote: {len(self.watched)} tickers")
            self.forward_subscription()

    def forward_subscription(self):
        """The stream's subscription is the union of the data symbols and the ticket's."""
        self.source.subscribed = self.tickers | self.watched

    # ── streaming: live prices, the ticket's bid / ask, the books ──
    def stream(self):
        while self.alive:
            try:
                quotes = []
                for t in sorted(self.tickers | self.watched):
                    if self.source.known(t):
                        q = self.source.quote(t)
                        if q:
                            quotes.append(q)
                events = self.source.on_tick()
                for q in quotes:
                    if q["ticker"] in self.tickers:
                        self.notify("data.quote", q)
                    if q["ticker"] in self.watched:
                        price, tick = q["price"], self.source.tick(q["ticker"])
                        half = tick / 2 if tick else 0.0
                        self.notify("broker.quote", {"ticker": q["ticker"], "bid": round(price - half, 10),
                                                     "ask": round(price + half, 10), "last": price})
                self.events(events)
                if self.source.positions:
                    self.push_state()  # the open P&L moves with the price
            except OSError:
                return
            time.sleep(1)

    # ── one connection ──
    def run_once(self):
        host, port, plugin, token = connection()
        self.sock = socket.create_connection((host, port), timeout=10)
        reader = self.sock.makefile("r", encoding="utf-8", newline="\n")
        self.send({"jsonrpc": "2.0", "id": 1, "method": "hello",
                   "params": {"plugin": plugin, "token": token, "api": API, "version": PLUGIN_VERSION}})
        welcome = json.loads(reader.readline() or "{}")
        if "error" in welcome:
            raise PluginError(welcome["error"].get("message", "refused"))
        self.sock.settimeout(None)
        self.alive = True
        self.log(f"Connected to {welcome.get('result', {}).get('app', 'OpenTrader')} (Plugin API {welcome.get('result', {}).get('api')})")
        self.push_state()
        threading.Thread(target=self.stream, daemon=True).start()
        for line in reader:
            if not line.strip():
                continue
            message = json.loads(line)
            if "method" in message and "id" in message:
                # Requests run on their own thread: a slow history request must not hold up the others.
                threading.Thread(target=self.on_request, args=(message,), daemon=True).start()
            elif "method" in message:
                self.on_notification(message["method"], message.get("params") or {})
        self.alive = False

    def run(self):
        """Connect. Started by the app, end when the connection ends (the app starts the program again when it
        wants it). Run by hand, connect again whenever the app closes the connection or is not there yet."""
        started_by_app = "OPENTRADER_PLUGIN_ADDR" in os.environ
        while True:
            try:
                self.run_once()
                print("The app closed the connection", flush=True)
            except PluginError as e:
                print(f"Refused: {e}", flush=True)
            except (OSError, ValueError) as e:
                print(f"Not connected ({e})", flush=True)
            self.alive = False
            if started_by_app:
                sys.exit(1)
            time.sleep(3)


if __name__ == "__main__":
    Plugin(OpenAlgoSource()).run()
