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
import http.client
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN_ID = "openalgo"  # the manifests' id
PLUGIN_VERSION = "0.1.26"
API = 2  # the Plugin API version this plugin speaks (whole symbols)
DEV_CONNECTION_FILE = "OpenTraderPlugins/openalgo-dev.json"  # under {localAppData}, as in opentrader-plugin.dev.json
DEBUG = os.environ.get("OPENALGO_DEBUG") == "1"  # OPENALGO_DEBUG=1: raw stream traffic on stdout (the key is redacted)

TZ_IST = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")

# The oldest bars the deployment serves: its intraday floor, which its daily (D) bars reach too.
# `history` (below, the app's #347 contract) is measured from it: the app then asks nothing older
# and only as many bars as fit the window.
HISTORY_FLOOR = datetime(2021, 11, 13, tzinfo=TZ_IST)

# ── the market's clock: bars and sessions are keyed in the exchange's time zone (India: one) ──
def ist_midnight(ts):
    """The epoch seconds of `ts`'s trading day's midnight in IST (a daily bar's start)."""
    day = datetime.fromtimestamp(ts, TZ_IST).date()
    return int(datetime(day.year, day.month, day.day, tzinfo=TZ_IST).timestamp())


def ist_date(ts):
    return datetime.fromtimestamp(ts, TZ_IST).strftime("%Y-%m-%d")


def calendar_exchange(prefix):
    """Our venue prefix → the exchange code the calendar rows name (index venues follow their
    cash market: the /market/holidays rows list NSE, not NSE_INDEX)."""
    base = prefix.strip().upper()
    return base[:-6] if base.endswith("_INDEX") and len(base) > 6 else (base or "NSE")


def ms_to_hhmm(ms):
    """Epoch milliseconds as the exchange's "HHMM" in IST; empty when the field is missing."""
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        return ""
    if ms != ms or ms in (float("inf"), float("-inf")):
        return ""
    return datetime.fromtimestamp(ms / 1000, TZ_IST).strftime("%H%M")


def in_market_hours():
    """Whether the Indian market can move right now: the pre-open and regular session on a weekday
    (IST). A holiday polls a few calls that answer stale quotes — harmless."""
    now = datetime.now(TZ_IST)
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return 9 * 60 <= minute <= 15 * 60 + 40


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


def book_levels(depth):
    """The five-level book as `{"buy": [{price, qty}], "sell": [...]}`, best first, or None
    when neither side has a usable level (a Quote-mode tick, a broker without depth)."""
    if not isinstance(depth, dict):
        return None
    book = {}
    for side in ("buy", "sell"):
        levels = []
        for level in depth.get(side) or []:
            if not isinstance(level, dict):
                continue
            price = num(level.get("price"))
            qty = num(first_of(level, "quantity", "qty"))
            if price is not None and qty is not None:
                levels.append({"price": price, "qty": qty})
        book[side] = levels
    return book if (book["buy"] or book["sell"]) else None


def _clean(text, secret):
    """A message with the API key removed (nothing secret reaches the log)."""
    return text.replace(secret, "…") if secret and secret in text else text


class PluginError(Exception):
    """An error answered to the app; `message` is shown to the user."""

    def __init__(self, message, code=-32000):
        super().__init__(message)
        self.code = code


# -32005 (busy, app's #347 contract): the app asks again later, waiting longer each time, instead
# of paging harder into the endpoint that said no. Used when the gateway rate-limits us.
BUSY_CODE = -32005


def is_rate_limit_message(msg):
    """A gateway message that spells the rate limit out (some adapters answer HTTP 200 with
    `"status": "error"` and "Rate limit …" in the message instead of an HTTP 429)."""
    return "rate limit" in str(msg or "").lower()


# ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
# 1. The source. Every method is called from the app's requests; keep the return shapes. Prices are floats,
#    times UNIX seconds (bars, quotes) or milliseconds (account state). Each part locks its own state: a slow
#    /history call must not hold up the quote stream.
# ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

# Standard regular session per exchange, in the market's clock (broker docs table). The calendar
# (holidays + timings) only overrides it; the static table is the last-resort answer.
SESSIONS = {
    "CDS": "0900-1700",
    "BCD": "0900-1700",
    "MCX": "0900-2330",
    # NSE, BSE, NFO, BFO, NCO, BCO and anything unlisted.
    "_": "0915-1530",
}
STD_SESSIONS = {  # open, close, pre minutes, post minutes — the built-in provider's bands (NSE pre-open 15m, post 30m)
    "CDS": ("0900", "1700", 0, 0),
    "BCD": ("0900", "1700", 0, 0),
    "MCX": ("0900", "2330", 0, 0),
}
STD_DEFAULT = ("0915", "1530", 15, 30)

# The app resolution → OpenAlgo interval, when /intervals says nothing (verified against a deployment).
FALLBACK_MINUTES = {1: "1m", 3: "3m", 5: "5m", 10: "10m", 15: "15m", 20: "20m", 30: "30m", 60: "1h", 120: "2h"}
FALLBACK_HOURS = {1: "1h", 2: "2h"}
FALLBACK_SECONDS = {}

# The index segments of the master: pulled alongside their cash segment so index search is warm. Unlike the
# cash exchanges they are not charting aliases — an index keeps its own OpenAlgo prefix (NSE_INDEX:NIFTY), as
# the app's built-in OpenAlgo provider does (verified live: /history answers exchange=NSE_INDEX).
INDEX_SEGMENTS = {"NSE": "NSE_INDEX", "BSE": "BSE_INDEX", "MCX": "MCX_INDEX"}

KINDS = {  # instrument type → the app's search-row / symbol-info type (the dialog's tabs and
    # the "Type" row read it; the strings must match the app's `securityTypeLabel` vocabulary
    # exactly — "option" is singular there, "options" would render no Type at all).
    "EQ": "stock", "BE": "stock", "SM": "stock", "MF": "fund", "MFP": "fund",
    "IDX": "index", "IND": "index",
    "FUT": "futures", "FUTCUR": "futures",
    "CE": "option", "PE": "option", "OPT": "option", "OPTFUT": "option", "OPTCUR": "option",
    "CUR": "forex",
}

FILTER_KINDS = {  # the dialog's Type chip code → the app kind it asks for (mirrors the provider's type_allows)
    "EQ": "stock", "STOCK": "stock",
    "FUT": "futures", "FUTURES": "futures",
    "CE": "option", "PE": "option", "OPT": "option", "OPTIONS": "option", "OPTION": "option",
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

PRICE_TYPES = {"market": "MARKET", "limit": "LIMIT", "stop": "SL-M", "stoplimit": "SL", "stop_limit": "SL"}
PRICE_KINDS = {  # the source's price type → the wire's (sl-m / sl-l are some adapters' spellings)
    "market": "market", "limit": "limit", "stop": "stop", "stoplimit": "stopLimit",
    "sl-m": "stop", "slm": "stop", "sl-l": "stopLimit", "sll": "stopLimit",
}

# Time in force (#346): the app's duration ids ↔ the API's validity values. The ticket offers the
# three the deployment takes; an id it doesn't send (or leaves out) keeps the API's DAY default.
VALIDITY = {"day": "DAY", "ioc": "IOC", "gtc": "GTC"}
DURATION_IDS = {"DAY": "day", "IOC": "ioc", "GTC": "gtc"}


# Index names that live on the *_INDEX segments, not on NSE/BSE themselves. A watchlist that still
# carries the stale "NSE:BANKNIFTY" form must not be sent to the broker (it breaks the whole subscribe
# frame and takes every other symbol's ticks down with it) — drop it from the subscription.
_INDEX_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "NIFTY50",
                "SENSEX", "BANKEX", "SENSEX50", "NIFTY100", "NIFTY200", "NIFTY500"}

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
        """The master rows of a segment: today's memory or disk cache first (a restart re-reads, no
        pull), then the worker's pull. One pull per segment at a time: the other callers wait for the
        running one instead of fetching again (the prefetch races itself otherwise)."""
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
                fetched = self._read_disk(exchange, today)  # today's disk cache: no network
                from_disk = fetched is not None
                if not from_disk:
                    fetched = self._fetch(exchange)
                with self.lock:
                    if fetched is None:
                        self.unavailable[exchange] = time.monotonic() + self.BACKOFF
                    else:
                        self.unavailable.pop(exchange, None)
                        self.rows[exchange], self.day[exchange] = fetched, today
                        self.catalog = None  # the combined search catalog is stale
                if fetched is not None and not from_disk:
                    self._write_disk(exchange, today, fetched)  # a restart re-reads, no pull
            finally:
                with self.lock:
                    self.loading.pop(exchange, None)
                event.set()
            with self.lock:
                return self.rows.get(exchange) or []

    def landed(self, exchange):
        """Whether today's master of a segment is in (memory or disk) — an empty landing counts:
        a segment the deployment does not serve answers success with no rows (verified live)."""
        today = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        with self.lock:
            if self.day.get(exchange) == today:
                return True
        return self._read_disk(exchange, today) is not None

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
    POLL_EVERY = 60.0  # the books refresh on order_update pushes; this is only a slow fallback
    # TODO (Option B): make this a setting ("pollInterval", default 60, 0 = disabled). When 0,
    # skip the periodic branch in on_tick() entirely and rely only on force_poll() + the
    # order_update push. Reduces 4 API calls/min on slower deployments. User-requested.

    def __init__(self):
        self.plugin = None  # set by Plugin: the connection the log lines go through
        self.lock = threading.RLock()
        self.settings = {}
        self._rest_pool = []  # idle keep-alive connections (see "REST" below)
        self._rest_lock = threading.Lock()
        self.exchanges = []
        self.instruments = Instruments(self)
        self.intervals = None  # the normalized /intervals tables, or None until fetched
        self.intervals_day = None
        # live prices from the WebSocket: whole symbol → the last quote's fields
        self.quotes = {}
        self.minute_bars = {}  # whole symbol → the forming minute bar [time,o,h,l,c,v]
        self.day_bars = {}  # whole symbol → the forming day bar
        self.day_vol = {}  # whole symbol → the last day volume (ticks without one carry it on)
        self.vol_base = {}  # whole symbol → the day volume at the minute bucket's first priced tick
        self.ws = None
        self.ws_thread = None
        self.ws_generation = 0
        self.ws_subscribed = set()  # what the stream last subscribed (compared with `subscribed`)
        self.subscribed = set()
        self.last_rest_poll = 0.0  # REST quote polling cadence (the WebSocket is the primary source)
        self.last_ws_tick = 0.0  # when the last market_data message with a price arrived
        self.ws_shape_warned = False  # the "no price read" warn says its piece only once
        self.ws_quiet_warned = False  # the "connected but no tick" warn says its piece only once
        self._daily_cache = {}  # (exchange, symbol, interval, start date, end date) → a folded year of day bars
        self._holiday_rows = {}  # year → (IST day fetched, the raw calendar rows of every exchange)
        self._timings = {}  # date → (IST day fetched, [timing rows])
        self._sessions = {}  # (calendar exchange, IST day) → the built session dict
        self._calendar_failed = {}  # year → monotonic time of the last failed holidays fetch
        self._symbol_rows = {}  # ticker → (IST day, the live /symbol row or None)
        self._profiles = {}  # ticker → (IST day, the company profile or None)
        self._profile_failed_until = 0.0  # while the companies API is unreachable, lookups skip it
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

    def log(self, message, level="info", category="api"):
        """A line in the plugin's log: through the connection when it is up, else to stdout (run by hand).
        Also appended to the per-category file (openalgo-api.log / openalgo-ws.log) when enabled in settings,
        so an AI agent or the user can read it without the in-memory ring buffer."""
        p = self.plugin
        if p and p.alive:
            p.log(message, level, category)
        else:
            print(message, flush=True)
            if self._log_enabled(category):
                ts = datetime.now(TZ_IST).strftime("%Y-%m-%d %H:%M:%S")
                self._write_log(category, f"{ts} [{level}] {_clean(str(message), self._key())}")

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

    def _log_dir(self):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "OpenTraderPlugins"

    def _log_enabled(self, category):
        # category: "api" or "ws". Settings arrive as text ("true"/"false"); fall back to the
        # manifest defaults when the key is missing (e.g. run by hand, or an older app build).
        if category == "ws":
            if self.settings.get("logWs") is None:
                return True
            return str(self.settings.get("logWs")).strip().lower() == "true"
        if category == "debug":
            if self.settings.get("logDebug") is None:
                return os.environ.get("OPENALGO_DEBUG") == "1"
            return str(self.settings.get("logDebug")).strip().lower() == "true" or os.environ.get("OPENALGO_DEBUG") == "1"
        if self.settings.get("logApi") is None:
            return True
        return str(self.settings.get("logApi")).strip().lower() == "true"

    def _log_path(self, category):
        name = "openalgo-ws.log" if category == "ws" else "openalgo-api.log"
        return self._log_dir() / name

    def _write_log(self, category, line):
        try:
            d = self._log_dir()
            d.mkdir(parents=True, exist_ok=True)
            p = self._log_path(category)
            # rolling cap: keep the last ~500 KB once the file passes ~1 MB
            try:
                if p.exists() and p.stat().st_size > 1024 * 1024:
                    data = p.read_bytes()
                    p.write_bytes(data[-512 * 1024:])
            except OSError:
                pass
            with p.open("a", encoding="utf-8") as f:
                f.write(line.rstrip("\n") + "\n")
        except OSError:
            pass  # logging must never break the plugin

    # ── day-stamped disk cache (the mirror of the built-in provider's daily files:
    #    an app restart inside a day makes no network call for these) ──
    def _daily_path(self, name):
        return Path(local_app_data()) / "OpenTraderPlugins" / "openalgo-cache" / "daily" / f"{name}.json"

    def _read_daily(self, name, today):
        """The day-stamped file's data when it was pulled today, else None."""
        try:
            with open(self._daily_path(name), encoding="utf-8") as f:
                file = json.load(f)
        except (OSError, ValueError):
            return None
        if isinstance(file, dict) and file.get("date") == today:
            return file.get("data")
        return None

    def _write_daily(self, name, today, data):
        try:
            path = self._daily_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"date": today, "data": data}, f)
        except OSError:
            pass

    # ── REST ──
    # Keep-alive transport. urllib opened a fresh TCP connection per call (it sends
    # "Connection: close"); through the gateway's tunnel an intermittently dropped SYN made
    # Windows walk its retransmit ladder — 21 s (one stalled connect) to 42 s (two in a row)
    # per chart load while the server itself answered in 30-60 ms once connected. One pooled
    # connection per base is reused across calls: requests run on their own threads, so the
    # pool hands connections out under a lock, and a connection the server closed while idle
    # (or a connect that stalls) is retried once on a fresh one.

    REST_TIMEOUT = 10.0  # connect + read of one /api/v1 call (the masters pull passes its own)
    # A deployment's first history call after an idle spell walks a cold path (verified live: once
    # 42 s for a 30-day D window, 0.1 s right after) — the app gives data.bars 60 s, so history
    # gets 55 instead; quotes, books and orders keep the short default.
    HISTORY_TIMEOUT = 55.0
    REST_POOL = 6  # idle connections kept; beyond this a connection is used once and closed

    def _rest_open(self, scheme, host, timeout):
        """A new connection; tests patch this."""
        cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        return cls(host, timeout=timeout)

    def _rest_take(self, scheme, host, timeout):
        """A pooled connection, or a new one when the pool is empty. The caller gives it back."""
        with self._rest_lock:
            if self._rest_pool:
                return self._rest_pool.pop()
        return self._rest_open(scheme, host, timeout)

    def _rest_give(self, conn):
        with self._rest_lock:
            if len(self._rest_pool) < self.REST_POOL:
                self._rest_pool.append(conn)
                return
        try:
            conn.close()
        except OSError:
            pass

    def _rest(self, method, path, body, timeout=None):
        """One call on a pooled connection: `(status, body bytes)`, or PluginError. A broken
        connection is retried once — but only before anything was sent (a failed order POST
        must not be sent twice) or on a GET (reading again is safe)."""
        base = self._base()
        if not base:
            raise PluginError("The OpenAlgo plugin has no API URL yet — fill it in Settings > Plugins")
        scheme, sep, host = base.partition("://")
        if not sep:
            scheme, host = "http", base
        payload = None if body is None else (
            body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("utf-8"))
        last = None
        for _ in (0, 1):
            sent = False
            conn = self._rest_take(scheme, host, self.REST_TIMEOUT if timeout is None else timeout)
            # A bodyless GET declares no body type: Flask/Werkzeug answers one carrying
            # `Content-Type: application/json` with HTTP 400 ("the request … could not
            # understand"), which is what the masters pull is (verified live on 0.1.17).
            headers = {"Accept": "application/json"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            try:
                conn.request(method, path, body=payload, headers=headers)
                sent = True
                r = conn.getresponse()
                data = r.read()
                if r.will_close:
                    conn.close()
                else:
                    self._rest_give(conn)
                return r.status, data
            except (http.client.HTTPException, OSError) as e:
                last = e
                try:
                    conn.close()
                except OSError:
                    pass
                if sent and method != "GET":
                    break  # the request may have reached the server: never send it twice
        raise PluginError(f"OpenAlgo is not reachable ({_clean(str(last), self._key())})")

    def _post(self, path, body, whole=False, timeout=None):
        """One OpenAlgo /api/v1 call; returns its `data` — or the whole answer with `whole`
        (placeorder's order id sits at the top level, verified live)."""
        key = self._key()
        if not self._base() or not key:
            raise PluginError("The OpenAlgo plugin has no API URL or key yet — fill them in Settings > Plugins")
        payload = dict(body)
        payload["apikey"] = key
        status, raw = self._rest("POST", path, payload, timeout)
        if status in (429, 503):
            # The gateway's rate limit (or a proxy in front of it): busy, the app backs off.
            raise PluginError(f"OpenAlgo answered HTTP {status} — rate limited, the app will ask again later", BUSY_CODE)
        try:
            answer = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            raise PluginError(f"OpenAlgo answered HTTP {status}") from None
        if str(answer.get("status", "")).lower() not in ("success", "ok"):
            msg = str(answer.get("message") or "the request failed")
            # -32005 (busy, #347): the app asks again later, waiting longer each time, instead of
            # paging harder into the throttled endpoint. Some adapters spell the limit out in the
            # JSON message instead of answering HTTP 429.
            if status in (429, 503) or is_rate_limit_message(msg):
                raise PluginError(f"OpenAlgo: {msg} — the app will ask again later", BUSY_CODE)
            raise PluginError(f"OpenAlgo: {msg}")
        self.rest_ok = True
        return answer if whole else answer.get("data")

    def _fetch_instruments(self, exchange):
        """The master of one segment, straight from the source."""
        key = self._key()
        base = self._base()
        if not base or not key:
            return None
        path = f"/api/v1/instruments?apikey={urllib.parse.quote(key)}&exchange={exchange}&format=json"
        try:
            status, raw = self._rest("GET", path, None, timeout=60)
        except PluginError as e:
            self.log(f"instruments {exchange}: not reachable ({_clean(str(e), key)})", "warn")
            return None
        if status == 429:
            self.log(f"instruments {exchange}: HTTP 429 (rate limited; the pull retries on the next use)", "warn")
            return None
        try:
            answer = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            self.log(f"instruments {exchange}: HTTP {status}", "warn")
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

    def known(self, ticker):
        """True when `ticker` ("NSE:SBIN-EQ") is one of the plugin's symbols."""
        exchange, sep, symbol = ticker.partition(":")
        if not (sep and symbol) or exchange not in self.exchanges:
            return False
        # stale index entries ("NSE:BANKNIFTY") are not real NSE/BSE symbols
        if exchange in ("NSE", "BSE") and symbol.upper() in _INDEX_NAMES:
            return False
        return True

    def tick(self, ticker):
        """One price step of `ticker`, from the master's tick size (the ticket's bid/ask spread).
        The lookup never waits for a pull: until the master lands, the app's default tick stands."""
        row = self._instrument_row(ticker)
        size = num(first_of(row or {}, "tick_size", "ticksize"))
        return size or 0.05

    def _instrument_row(self, ticker):
        exchange, _, symbol = ticker.partition(":")
        for row in self.instruments.rows_peek(exchange):  # the row lives in the segment the symbol names itself; a pull is never awaited
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
        IX, …): only the rows of that kind answer. Until every configured segment's day-pull has landed
        (memory or disk), the live /search answers instead — the cold-start window, the same rule the app
        provider follows; a failed live call falls back to the rows already here."""
        q = query.strip().upper()
        prefix, _, rest = q.partition(":")
        if rest and (prefix in self.exchanges or prefix in INDEX_SEGMENTS.values()):
            q = rest.strip()  # "NSE:SBIN" searches for SBIN; the app filters the rows by its prefix
        want = FILTER_KINDS.get(str(type_filter or "").strip().upper())
        if q and not all(self.instruments.landed(e) for e in self.instruments.master_exchanges(self.exchanges)):
            live = self._live_search(q, want)
            if live is not None:
                return live
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

    def _live_search(self, q, want):
        """The live /search for the cold window (before the day's master lands), the app provider's
        fallback: up to 500 rows back, mapped by the same kind/display shape the catalog uses, the
        Type chip applied, 50 kept. None when the call fails — the caller serves the rows it has."""
        try:
            data = self._post("/api/v1/search", {"query": q})
        except PluginError as e:
            self.log(f"live search {q}: {e}; the landed rows serve", "warn")
            return None
        # `_post` already unwrapped the answer's `data`; a nested dict shape is accepted too.
        rows = data if isinstance(data, list) else \
            (data.get("rows") if isinstance(data, dict) else None)
        if not isinstance(rows, list):
            return None
        out = []
        for row in rows[:500]:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue
            exchange = str(row.get("exchange") or "").strip().upper()
            # The live rows carry the ticker whole (verified) but a bare symbol + exchange
            # field is accepted too — the same ticker either way.
            ticker = symbol if ":" in symbol else (f"{exchange}:{symbol}" if exchange else symbol)
            kind = self._kind_of(ticker.split(":", 1)[0], row)
            if want and kind != want:
                continue
            out.append({"ticker": ticker, "description": display_name(row.get("name") or symbol, row),
                        "type": kind})
            if len(out) >= 50:
                break
        return out

    def symbol_info(self, ticker):
        """The symbol's shape: names, type, currency, the calendar session (holidays, special
        timings), tick size, the point value (the money one unit makes per point — a lot-sized
        contract says its lot to `broker.instrument` instead), the company profile and the
        intervals served."""
        row = self._symbol_row(ticker)
        if row is None:
            # -32004: the app stops asking for it (an expired contract left in a watchlist) and shows it in red.
            raise PluginError(f"Unknown symbol {ticker}", -32004)
        exchange = ticker.split(":", 1)[0]
        symbol = ticker.partition(":")[2]
        tick = num(first_of(row, "tick_size", "ticksize")) or 0.05
        pricescale = max(2, round(1 / tick)) if tick < 1 else 100
        name = display_name(row.get("name") or symbol, row)
        lot = num(first_of(row, "lotsize", "lot_size")) or 1.0
        cal = self._calendar_session(exchange)
        if cal is None:  # the calendar cannot be reached: the static session stands (never an error)
            cal = {"session": SESSIONS.get(exchange, SESSIONS["_"]), "extended": None,
                   "holidays": "", "corrections": ""}
        info = {
            "name": name,
            "description": name,  # a host without `name` still shows the display name
            "longDescription": self._long_description(symbol, row, lot),
            "type": self._kind_of(exchange, row),
            "currency": "INR",
            "timezone": "Asia/Kolkata",
            "session": cal["session"],
            "extended": cal["extended"],
            "holidays": cal["holidays"],
            "corrections": cal["corrections"],
            "pricescale": pricescale,
            "minmov": 1,
            # Money one unit makes per point: a future counted in contracts gives its multiplier
            # (the master's lot size); the host divides the broker's quantities by the same lot.
            "pointValue": lot,
            "intervals": self._served_intervals(),
            # How far back, in calendar days, the source serves each family of bars (#347): the app
            # asks nothing older and no more bars than fit the window. Days and minutes reach the
            # floor; no seconds are served.
            "history": {"minute": self._history_days(), "day": self._history_days()},
        }
        # The profile and the logo name the bare symbol (the built-in provider's calls read
        # `companies/TCS`, `logos/TCS` — the venue prefix would 404 them).
        profile = self._company_profile(symbol)
        if profile:
            if profile.get("sector"):
                info["sector"] = profile["sector"]
            if profile.get("industry"):
                info["industry"] = profile["industry"]
        # The logo for equities only (the built-in provider's gate: index venues and derivatives
        # fall back to the letter icon).
        if str(first_of(row, "instrumenttype", "instrument_type") or "").strip().upper() == "EQ" \
                and not exchange.upper().endswith("_INDEX"):
            info["icon"] = f"https://api.dharun.dev/v1/logos/{symbol.upper()}"
        return info

    def _long_description(self, symbol, row, lot):
        """The dialog's description: the broker symbol with each tail only when it applies
        (`TCS-EQ`, `NIFTY FUT 23-NOV-26 · exp 30-Oct-26 · lot 65`) — the built-in's shape."""
        out = str(first_of(row, "brsymbol", "broker_symbol") or "").strip() or symbol
        expiry = str(first_of(row, "expiry", "expiry_date") or "").strip()
        if expiry:
            out += f" · exp {expiry}"
        if lot != 1.0:
            out += f" · lot {lot:g}"
        return out

    def _lot_of(self, ticker):
        """The master's lot size for `ticker` (1 when unknown or an equity)."""
        row = self._symbol_row(ticker)
        lot = num(first_of(row, "lotsize", "lot_size")) if row else None
        return lot if lot and lot > 0 else 1.0

    def _units(self, ticker, contracts):
        """Contracts to broker units (× lot)."""
        return (contracts or 0) * self._lot_of(ticker)

    def _contracts(self, ticker, units):
        """Broker units to contracts (÷ lot); units unchanged when the lot is unknown."""
        lot = self._lot_of(ticker)
        if not lot or lot <= 1:
            return units or 0
        return (units or 0) / lot

    def _symbol_row(self, ticker):
        """The master's row, or the live /symbol answer when the master doesn't list the symbol (a
        fresh listing before the master's next pull). The lookup never waits for a pull: rows already
        here (memory or today's disk cache) answer, so the dialog works while the masters warm.
        Only a real answer is day-cached: a miss stays None (never fatal), and a rate limit is
        raised — cached as a miss it read as "Unknown symbol" for the rest of the day."""
        row = self._instrument_row(ticker)
        if row is not None:
            return row
        today = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        with self.lock:
            hit = self._symbol_rows.get(ticker)
            if hit is not None and hit[0] == today:
                return hit[1]
        row = None
        try:
            exchange, _, symbol = ticker.partition(":")
            data = self._post("/api/v1/symbol", {"symbol": symbol, "exchange": exchange})
            if isinstance(data, dict):
                row = data
        except PluginError as e:
            if e.code == BUSY_CODE:
                # Rate limited is not an answer about the symbol. Cached as a miss it read as
                # "Unknown symbol" (-32004, final: the app unsubscribes it) for the rest of the
                # day, which is how a startup burst dropped symbols; raised, it is -32005 and the
                # app asks again later (#347).
                raise
            self.log(f"symbol {ticker}: {e}", "warn")
        if row is None:
            return None  # a miss stays None, never fatal — and never cached: the app caches its own
        with self.lock:
            self._symbol_rows[ticker] = (today, row)
        return row

    def _company_profile(self, ticker):
        """{sector, industry} from the public companies API the built-in provider reads (the broker
        /symbol serves neither). Day-cached in memory; a miss is None, never fatal. The API answers
        on its IPv6 address first, and a broken IPv6 path black-holes the connect until the timeout
        — so when it cannot be reached, the whole lookup backs off for a minute: one symbol's dialog
        pays the wait, not every one."""
        today = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        with self.lock:
            hit = self._profiles.get(ticker)
            if hit is not None and hit[0] == today:
                return hit[1]
            dead = time.monotonic() < self._profile_failed_until
        profile = None
        if not dead:
            try:
                data = self._get_json(f"https://api.dharun.dev/v1/companies/{ticker.upper()}")
                row = data.get("data") if isinstance(data, dict) else None
                if isinstance(row, dict):
                    profile = {k: str(row[k]) for k in ("sector", "industry") if row.get(k)}
            except PluginError:
                pass  # the source answered (a 404 for anything but a listed company): no profile
            except OSError:
                with self.lock:
                    self._profile_failed_until = time.monotonic() + 60.0
                self.log("the companies API is not reachable; symbol profiles are paused for a minute", "warn")
        with self.lock:
            self._profiles[ticker] = (today, profile)
        return profile

    def _get_json(self, url, timeout=8):
        """One GET that must answer 2xx JSON; raises PluginError on an HTTP answer and OSError when
        the host cannot be reached (the caller decides what a dead host costs the request). The
        public profile API 403s unknown clients — a browser User-Agent goes along. Hand-rolled
        (HTTP/1.0: no chunked bodies, no keep-alive) because the resolved addresses are tried by
        hand, IPv4 first: the API answers on two IPv6 addresses that come first in the DNS order,
        and a broken IPv6 path black-holes each connect until the timeout — the library's order
        made every first lookup to a new symbol cost two dead attempts (~16 s) and miss the app's
        request window."""
        import ssl
        parts = urllib.parse.urlsplit(url)
        host, target = parts.hostname, parts.path or "/"
        if parts.query:
            target += f"?{parts.query}"
        tls = ssl.create_default_context()
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        attempts = [i for i in infos if i[0] == socket.AF_INET] + [i for i in infos if i[0] != socket.AF_INET]
        answer, last = None, None
        for family, _, _, _, addr in attempts:
            try:
                with socket.socket(family, socket.SOCK_STREAM) as sock:
                    sock.settimeout(timeout)
                    sock.connect(addr)
                    with tls.wrap_socket(sock, server_hostname=host) as secure:
                        secure.sendall((f"GET {target} HTTP/1.0\r\nHost: {host}\r\n"
                                        "User-Agent: Mozilla/5.0\r\nAccept: application/json\r\n\r\n").encode("ascii"))
                        answer = b""
                        while True:
                            chunk = secure.recv(65536)
                            if not chunk:
                                break
                            answer += chunk
                break
            except OSError as e:
                last = e  # this address is dead (a black-holed IPv6, a refused IPv4): try the next
        if answer is None:
            raise last if last is not None else PluginError("no address to connect to")
        head, _, body = answer.partition(b"\r\n\r\n")
        if not head.startswith(b"HTTP/"):
            raise PluginError("bad answer")
        status = int(head.split(b" ", 2)[1])
        if status != 200:
            raise PluginError(f"HTTP {status}")
        return json.loads(body.decode("utf-8"))

    def _calendar_session(self, exchange):
        """The venue's session from the broker calendar (the built-in provider's rule): the standard
        hours as the base spec; closed days into `holidays`; special sessions into `corrections`
        (`spec:date,date;…`), which win over holidays; settlement holidays and unmentioned exchanges
        keep trading. Years fetched: the current one, the next only in December (January's specials
        and the countdown over New Year need it). None when the calendar cannot be reached — the
        static session stands in, never an error."""
        base = calendar_exchange(exchange)
        now = datetime.now(TZ_IST)
        key = (base, now.strftime("%Y-%m-%d"))
        with self.lock:
            hit = self._sessions.get(key)
        if hit is not None:
            return hit
        std = STD_SESSIONS.get(base, STD_DEFAULT)
        to_min = lambda hhmm: int(hhmm[:2]) * 60 + int(hhmm[2:])
        fmt = lambda m: f"{((m // 60) % 24 + 24) % 24:02d}{(m % 60) % 60:02d}"
        rth = f"{std[0]}-{std[1]}"
        eth = rth if std[2] + std[3] == 0 else f"{fmt(to_min(std[0]) - std[2])}-{fmt(to_min(std[1]) + std[3])}"
        years = [now.year] + ([now.year + 1] if now.month == 12 else [])
        rows, ok = [], False
        for year in years:
            got = self._holiday_rows_of(base, year, now)
            if got is not None:
                ok = True
                rows.extend(got)
        if not ok:
            return None
        # A timings row for this exchange on the day makes it a special session (`spec:date`).
        # SPECIAL_SESSION days whose row carries no timings resolve through /market/timings —
        # a year holds a handful at most, so the lookups stay bounded.
        closed, opens, need = [], {}, {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            iso = str(row.get("date") or "").strip()
            if len(iso) != 10 or not all(c.isdigit() or c == "-" for c in iso):
                continue  # the row dates must read "YYYY-MM-DD" (the source's own guard)
            day = iso.replace("-", "")
            hit = next((t for t in (row.get("open_exchanges") or [])
                        if str(t.get("exchange") or "").strip().upper() == base), None) \
                if isinstance(row.get("open_exchanges"), list) else None
            if hit:
                spec = f"{ms_to_hhmm(hit.get('start_time'))}-{ms_to_hhmm(hit.get('end_time'))}"
                opens.setdefault(spec, set()).add(day)
                continue
            closed_list = [str(e).strip().upper() for e in row.get("closed_exchanges")] \
                if isinstance(row.get("closed_exchanges"), list) else []
            if base in closed_list:
                closed.append(day)
                continue
            if str(row.get("holiday_type") or "").strip().upper() == "SPECIAL_SESSION":
                need[iso] = day
        for iso, day in sorted(need.items())[:12]:
            for t in self._timings_of(base, iso) or []:
                if str(t.get("exchange") or "").strip().upper() == base:
                    spec = f"{ms_to_hhmm(t.get('start_time'))}-{ms_to_hhmm(t.get('end_time'))}"
                    opens.setdefault(spec, set()).add(day)
                    break
        corrections = ";".join(f"{spec}:{','.join(sorted(days))}" for spec, days in sorted(opens.items()))
        out = {"session": rth, "extended": eth if eth != rth else None,
               "holidays": ",".join(sorted(set(closed))), "corrections": corrections}
        with self.lock:
            self._sessions[key] = out
        return out

    def _holiday_rows_of(self, base, year, now):
        """The raw calendar rows of one year (every exchange — the filtering is the builder's job),
        from memory, the day-stamped disk file, or the source; None when it cannot be reached."""
        with self.lock:
            hit = self._holiday_rows.get(year)
            if hit is not None and hit[0] == now.strftime("%Y-%m-%d"):
                return hit[1]
            if time.monotonic() < self._calendar_failed.get(year, 0.0):
                return None  # a failed fetch waits a minute before it is tried again
        today = now.strftime("%Y-%m-%d")
        disk = self._read_daily(f"holidays-{year}", today)
        if isinstance(disk, list):
            with self.lock:
                self._holiday_rows[year] = (today, disk)
            return disk
        try:
            data = self._post("/api/v1/market/holidays", {"year": year}, whole=True)
        except PluginError as e:
            with self.lock:
                self._calendar_failed[year] = time.monotonic() + 60.0
            self.log(f"holiday calendar {base} {year}: {e}; the static session stands", "warn")
            return None
        rows = data.get("data") if isinstance(data, dict) else (data if isinstance(data, list) else None)
        rows = rows if isinstance(rows, list) else []
        self._write_daily(f"holidays-{year}", today, rows)
        with self.lock:
            self._holiday_rows[year] = (today, rows)
        return rows

    def _timings_of(self, base, date):
        """One date's per-exchange timings (empty on holidays), from memory, the day-stamped disk
        file, or the source; None when it cannot be reached (the regular session stands)."""
        today = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        with self.lock:
            hit = self._timings.get(date)
            if hit is not None and hit[0] == today:
                return hit[1]
        disk = self._read_daily(f"timings-{date}", today)
        if isinstance(disk, list):
            with self.lock:
                self._timings[date] = (today, disk)
            return disk
        try:
            data = self._post("/api/v1/market/timings", {"date": date}, whole=True)
        except PluginError as e:
            self.log(f"timings {base} {date}: {e}; the regular session stands", "warn")
            return None
        rows = data.get("data") if isinstance(data, dict) else (data if isinstance(data, list) else None)
        rows = rows if isinstance(rows, list) else []
        self._write_daily(f"timings-{date}", today, rows)
        with self.lock:
            self._timings[date] = (today, rows)
        return rows

    def _history_days(self):
        """The calendar days of bars the source serves (from the floor to today); the app's
        `history` depth for the day and minute families."""
        return (datetime.now(TZ_IST) - HISTORY_FLOOR).days + 1

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
        """The resolution→token tables of /api/v1/intervals, refreshed daily (memory, then the day-stamped
        disk file — a restart re-reads, no network); the fallback tables when cold."""
        today = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        with self.lock:
            if self.intervals is not None and self.intervals_day == today:
                return self.intervals
        tables = self._read_daily("intervals", today)
        if isinstance(tables, dict):
            # The disk round-trip turned the int multiplier keys into strings ("1": "1m") —
            # back to ints, the callers index by the multiplier (`hours.get(1)`, `min(minutes)`).
            tables = {k: {int(m): t for m, t in v.items() if str(m).lstrip("-").isdigit()}
                      if isinstance(v, dict) else v for k, v in tables.items()}
        if not isinstance(tables, dict):
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
                self._write_daily("intervals", today, tables)
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
        """A day window's bars, from the source's own D (day / week / month) endpoint: it serves the
        whole range in one call like the intraday ones (verified live: 5 days → 5 rows, 30 days → 22
        rows, each at its trading day's midnight). A window wider than a year is walked in year
        chunks — cached — so the host does not refetch the same years on every step."""
        if to - frm > 365 * 86400:
            return self._daily_long(exchange, symbol, interval, frm, to, count_back)
        return self._window(exchange, symbol, interval, True, frm, to, count_back, 0)

    def _daily_long(self, exchange, symbol, interval, frm, to, count_back):
        """A window longer than a year: year chunks walked back from `to`, each cached, until `frm`,
        enough bars, or the source's daily history ends (an empty chunk, or an older one with only a
        stray row or two — a row an adapter echoed, not history)."""
        out = []
        end = to
        first = True
        while end >= frm and (not count_back or len(out) < count_back):
            start = max(frm, end - 365 * 86400 + 1)
            bars = self._daily_chunk(exchange, symbol, interval, start, end)
            if not bars:
                if start > frm:
                    self.log(f"history {exchange}:{symbol} {interval}: the source has no daily bars "
                             f"before {ist_date(end)} — daily history stops there")
                break
            if not first and len(bars) < 10:
                self.log(f"history {exchange}:{symbol} {interval}: {len(bars)} day bar(s) in the year to "
                         f"{ist_date(end)} — the source's daily history ends there")
                break
            first = False
            out = bars + out
            end = start - 86400
        return out[-count_back:] if count_back else out

    def _daily_chunk(self, exchange, symbol, interval, frm, to):
        """One ≤365-day daily window, cached per (symbol, interval, the window's IST dates)."""
        key = (exchange, symbol, interval, ist_date(frm), ist_date(to))
        with self.lock:
            cached = self._daily_cache.get(key)
        if cached is not None:
            return cached
        bars = self._daily(exchange, symbol, interval, frm, to, 0)
        with self.lock:
            if len(self._daily_cache) > 600:
                self._daily_cache.clear()
            self._daily_cache[key] = bars
        return bars

    def _cap(self, key, default):
        """A per-request history cap from the settings, in days (a deployment that changes its
        caps is a setting, not a build). A blank, bad or non-positive value keeps the default."""
        try:
            v = int(float(self.settings.get(key) or default))
        except (TypeError, ValueError):
            return default
        return v if v > 0 else default

    def _window(self, exchange, symbol, interval, daily, frm, to, count_back, depth, max_days=None):
        """History window with date-chunking (per-request caps) + split-retry on 5xx."""
        if max_days is None:
            if interval.upper() in ("D", "W", "M"):
                max_days = self._cap("historyDaysDaily", 365)
            elif interval.lower().endswith("s"):
                max_days = self._cap("historyDaysSeconds", 3)
            else:
                max_days = self._cap("historyDaysMinute", 30)
        if depth > 0 or (to - frm) // 86400 + 1 <= max_days:
            return self._window_one(exchange, symbol, interval, daily, frm, to, count_back, depth)
        out = []
        start = frm
        while start <= to:
            end = min(start + (max_days - 1) * 86400, to)
            out += self._window_one(exchange, symbol, interval, daily, start, end, 0, 0)
            start = end + 86400
        seen = set()
        merged = []
        for b in sorted(out, key=lambda x: x[0]):
            if b[0] in seen:
                continue
            seen.add(b[0])
            merged.append(b)
        if count_back and len(merged) > count_back:
            merged = merged[-count_back:]
        return merged

    def _window_one(self, exchange, symbol, interval, daily, frm, to, count_back, depth):
        """A single history window: one per-request call, paged to its span when the source's
        answer does not cover it; split-retried on 5xx by `_window`'s chunks."""
        return self._window_page(exchange, symbol, interval, daily, frm, to, count_back, depth, 12)

    def _window_fetch(self, exchange, symbol, interval, daily, frm, to, count_back, depth):
        """One per-request call: `(bars, the call's window)` or None on a failed retry."""
        body = {
            "symbol": symbol, "exchange": exchange, "interval": interval,
            "start_date": ist_date(frm), "end_date": ist_date(to),
        }
        try:
            data = self._post("/api/v1/history", body, timeout=self.HISTORY_TIMEOUT)
        except PluginError:
            if depth == 0:
                raise  # the first window's error is what reaches the user
            return None  # a retried half failing is normal (the cap)
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
        return bars

    @staticmethod
    def _session_bounds(exchange, ts):
        """(open, close) of ts's calendar-day session, from the static table, IST epoch seconds.
        The tables are one daily window per exchange; holidays are not known here (a probe into
        one costs a single empty call, which the paging below survives)."""
        spec = SESSIONS.get(exchange, SESSIONS["_"])
        o_h, o_m, c_h, c_m = int(spec[:2]), int(spec[2:4]), int(spec[5:7]), int(spec[7:9])
        day = ist_midnight(ts)
        return day + (o_h * 60 + o_m) * 60, day + (c_h * 60 + c_m) * 60

    def _first_bar_at(self, exchange, ts):
        """The earliest bar stamp the source can hold at or after ts: a bar grid starts at the
        session's open, so an off-hours ts (evening, pre-open) waits for the next open — and
        Saturdays and Sundays roll to Monday, the static table has no weekend."""
        open_ts, close_ts = self._session_bounds(exchange, ts)
        if open_ts <= ts < close_ts and datetime.fromtimestamp(ts, TZ_IST).weekday() < 5:
            return ts
        day = ist_midnight(ts)
        for _ in range(4):  # at most a weekend to cross
            open_ts, _ = self._session_bounds(exchange, day)
            if open_ts >= ts and datetime.fromtimestamp(open_ts, TZ_IST).weekday() < 5:
                return open_ts
            day += 86400
        return ts  # unreachable

    def _last_bar_cover(self, exchange, ts):
        """The newest moment a bar can cover at or before ts: its session's close — the prior
        weekday's close when ts sits before that open (a pre-open or early-morning `to`)."""
        open_ts, close_ts = self._session_bounds(exchange, ts)
        if ts >= open_ts:
            return close_ts  # in the session or after its close: the close covers ts's side
        day = ist_midnight(ts) - 86400
        for _ in range(4):
            if datetime.fromtimestamp(day, TZ_IST).weekday() < 5:
                _, close_ts = self._session_bounds(exchange, day)
                return close_ts
            day -= 86400
        return close_ts  # unreachable

    def _window_page(self, exchange, symbol, interval, daily, frm, to, count_back, depth, guard):
        """The window's bars, paged to its span in session time. One call is the normal answer:
        the API serves a whole date range at once (verified live: 30 days of 1m came back as
        7,981 rows in one call). Paging only runs when that answer does not span the window —
        history that starts inside it, a source that truncates its rows — and then walks one
        call per page toward the uncovered side until the window spans (or the source's history
        ends). The span is judged in session time: raw window edges
        misfire (an evening `frm`'s first bar is the next session's 09:15; an after-close `to`
        sits between bar stamps), and complete answers were split for nothing. A bar at T
        covers [T, T+period), the period the last two bars' gap."""
        bars = self._window_fetch(exchange, symbol, interval, daily, frm, to, count_back, depth)
        if bars is None:
            return []
        first = self._first_bar_at(exchange, frm)
        last = min(self._last_bar_cover(exchange, to), to)
        while len(bars) >= 2 and to - frm > 3 * 86400 and guard > 0 and depth < 9 \
                and (not count_back or len(bars) < count_back):
            gap = max(bars[-1][0] - bars[-2][0], 1)
            if bars[0][0] - first >= gap:
                # the answer's oldest bar sits a period or more after the window's earliest
                # possible one: there may be bars between — page older
                older = self._window_fetch(exchange, symbol, interval, daily, frm, bars[0][0] - 1, count_back, depth + 1)
                if not older:
                    break  # the source's history ends inside the window
                have = {b[0] for b in older}
                merged = older + [b for b in bars if b[0] not in have]
                if len(merged) == len(bars):
                    break  # the page repeated the covered rows: the source ends here
                bars = merged
            elif bars[-1][0] + 2 * gap <= last:
                # room for one more complete bar before the window's covered end — page newer;
                # a mid-gap or after-close `to` leaves none (no probe into dead time)
                newer = self._window_fetch(exchange, symbol, interval, daily, bars[-1][0] + gap, to, count_back, depth + 1)
                if not newer:
                    break
                have = {b[0] for b in bars}
                merged = bars + [b for b in newer if b[0] not in have]
                if len(merged) == len(bars):
                    break
                bars = merged
            else:
                break  # the window spans
            if len(bars) > count_back:
                bars = bars[-count_back:]
            guard -= 1
        return bars

    def quote(self, ticker):
        """The last price and the forming minute / day bar, from the WebSocket cache. A depth
        subscription (mode 3) also carries the best bid / ask, which the order ticket's ladder
        shows; a plain quote stream has none."""
        with self.lock:
            q = self.quotes.get(ticker)
            minute = list(self.minute_bars.get(ticker) or [])
            day = list(self.day_bars.get(ticker) or [])
        if not q:
            return None
        now = int(time.time())
        return {"ticker": ticker, "time": now, "price": q["price"], "prevClose": q.get("prevClose"),
                "bid": q.get("bid"), "ask": q.get("ask"),
                "minute": self._minute_bar(minute, now // 60 * 60, q["price"]),
                "day": self._complete(day, ist_midnight(now), q)}

    def best_bid_ask(self, ticker):
        """The stream's best bid / ask from the last depth message, or (None, None) when the
        broker sent none (a Quote-mode subscription, or a REST answer without depth)."""
        with self.lock:
            q = self.quotes.get(ticker) or {}
        return q.get("bid"), q.get("ask")

    def book(self, ticker):
        """The last depth tick's five buy / sell levels (best first), or None when the
        broker has sent no book (the ladder keeps its best price only)."""
        with self.lock:
            q = self.quotes.get(ticker) or {}
            depth = q.get("depth")
            return {side: list((depth or {}).get(side) or []) for side in ("buy", "sell")} if depth else None

    def _minute_bar(self, bar, start, price):
        """The forming minute bar [time,o,h,l,c,v]: the last price only — the WS day side
        (its open / high / low / volume) never touches a minute bar, the way the built-in
        provider keeps its bucket. The volume is the bucket's own delta, maintained in
        `_ws_message`; a bar seeded here (no ticks in this minute yet) carries none."""
        if not bar or bar[0] != start:
            return [start, price, price, price, price, 0]
        bar = list(bar)
        bar[2], bar[3], bar[4] = max(bar[2], price), min(bar[3], price), price
        return bar

    def _complete(self, bar, start, q):
        """The forming day bar [time,o,h,l,c,v] from the quote's day fields (open / high / low /
        volume are the day's, both on WS ticks and REST answers): close the last price, and a
        missing day side falls back to what the bar (or the price) has rather than a 0."""
        p = q["price"]
        if not bar or bar[0] != start:
            return [start, q.get("open") or p, q.get("high") or p, q.get("low") or p, p, q.get("volume") or 0]
        bar = list(bar)
        bar[2], bar[3] = max(bar[2], q.get("high") or p), min(bar[3], q.get("low") or p)
        bar[4], bar[5] = p, q.get("volume") or bar[5] or 0
        return bar

    # ── broker ──
    def has_quotes(self):
        """Whether any subscribed ticker has a cached price (the watchlist is not blank)."""
        with self.lock:
            return any(self.quotes.get(t) for t in self.subscribed)

    def poll_quotes_rest(self):
        """Refresh the quote cache from REST /quotes (fallback when the WebSocket is
        not connected), mirroring the built-in provider's poller."""
        for ticker in list(self.subscribed):
            if not self.known(ticker):
                continue
            exchange, _, symbol = ticker.partition(":")
            try:
                answer = self._post("/api/v1/quotes", {"symbol": symbol, "exchange": exchange})
            except PluginError:
                continue
            if not isinstance(answer, dict):
                continue
            price = num(answer.get("ltp"))
            if price is None or price <= 0:
                continue
            with self.lock:
                q = self.quotes.get(ticker) or {}
                q["price"] = price
                if answer.get("prev_close") is not None:
                    q["prevClose"] = num(answer.get("prev_close"))
                elif answer.get("close") is not None:
                    q["prevClose"] = num(answer.get("close"))
                if answer.get("open") is not None:
                    q["open"] = num(answer.get("open"))
                if answer.get("high") is not None:
                    q["high"] = num(answer.get("high"))
                if answer.get("low") is not None:
                    q["low"] = num(answer.get("low"))
                if answer.get("volume") is not None:
                    q["volume"] = num(answer.get("volume"))
                if answer.get("bid") is not None:
                    q["bid"] = num(answer.get("bid"))
                if answer.get("ask") is not None:
                    q["ask"] = num(answer.get("ask"))
                depth = answer.get("depth")
                if isinstance(depth, dict):
                    buys, sells = depth.get("buy") or [], depth.get("sell") or []
                    if buys and isinstance(buys[0], dict):
                        q["bid"] = num(buys[0].get("price"))
                    if sells and isinstance(sells[0], dict):
                        q["ask"] = num(sells[0].get("price"))
                    levels = book_levels(depth)
                    if levels:
                        q["depth"] = levels
                self.quotes[ticker] = q

    def resolve_symbol(self, symbol, ticker):
        """The plugin's ticker of a chart symbol, or None. Its own symbols trade as they are."""
        return symbol if self.known(symbol) else None

    PRODUCTS = {"MIS": "Intraday", "CNC": "Delivery", "NRML": "Carry forward"}

    def _product_options(self, exchange, row):
        """The product types the segment takes: a derivative is intraday (MIS) or carry-forward
        (NRML), an equity intraday (MIS) or delivery (CNC); anything unrecognised offers all three
        and the broker rejects what it does not take."""
        kind = str(first_of(row, "instrumenttype", "instrument_type") or "").upper()
        if "FUT" in kind or "OPT" in kind or exchange in ("MCX", "NCDEX", "CDS"):
            return ["MIS", "NRML"]
        if kind == "EQ":
            return ["MIS", "CNC"]
        return ["MIS", "CNC", "NRML"]

    def _product_field(self, options):
        """A ticket field the app shows as its selector: the product type with each order. The
        default is the settings' product (the app falls back to the first option when the segment
        does not take it)."""
        default = (self.settings.get("product", "MIS") or "MIS").upper()
        return {"id": "product", "label": "Product",
                "options": [{"value": v, "label": self.PRODUCTS.get(v, v)} for v in options],
                "default": default}

    def instrument(self, ticker):
        """`broker.instrument`: the quantities the broker takes (units) and the money one unit makes
        per point, from the master's row. A lot-sized contract says its lot (qtyMin = qtyStep); the
        point value stays 1 (Indian contracts are multiplier 1 — one unit is one share's worth, the
        old lot-size-as-point-value made the risk and P&L 65x too big). The product choice is the
        segment's: an equity is intraday or delivery, a derivative intraday or carry-forward."""
        row = self._symbol_row(ticker)
        if row is None:
            raise PluginError(f"Unknown symbol {ticker}", -32004)
        exchange = ticker.split(":", 1)[0].upper()
        return {
            "qtyMin": 1, "qtyStep": 1,
            "ticketFields": [self._product_field(self._product_options(exchange, row))],
        }

    def place(self, p):
        """A new order; returns its id, raises PluginError to refuse (shown as a rejected order)."""
        ticker, side, kind = p["ticker"], p["side"], p["type"]
        qty = num(p.get("qty"))
        if not self.known(ticker):
            raise PluginError(f"{ticker} is not one of this account's symbols")
        if not qty or qty <= 0:
            raise PluginError("The quantity must be above 0")
        # The broker's quantity is in lots on the derivatives (the master's lotsize); the app
        # checks whole lots first (`broker.instrument`) — this one stands when that answer was
        # not asked yet. The wording is the app's ("65 is one lot: use 65 or 130").
        row = self._symbol_row(ticker)
        lot = num(first_of(row, "lotsize", "lot_size")) if row else None
        if lot is None:
            lot = 1.0
        # Contracts are whole lots: a fractional contract is refused with the host's wording.
        if abs(qty - round(qty)) > 1e-9:
            below = max(int(qty // 1), 1)
            raise PluginError(f"1 is one lot: use {below:g} or {below + 1:g}")
        # The product picked in the ticket (`fields.product`) wins over the settings default.
        picked = str((p.get("fields") or {}).get("product") or "").upper()
        product = picked if picked in self.PRODUCTS else (self.settings.get("product", "MIS").upper() or "MIS")
        # The time in force picked in Extra settings (`duration`); the app sends it only when the
        # capabilities declare durations. IOC only ever comes with a market or limit order.
        duration = str(p.get("duration") or "").lower()
        if duration not in VALIDITY:
            duration = "day"
        exchange, _, symbol = ticker.partition(":")
        body = {
            "symbol": symbol, "exchange": exchange,
            "product": product,
            "pricetype": ("SL" if (kind == "stop" and num(p.get("limitPrice"))) else PRICE_TYPES.get(kind, "LIMIT")),
            "quantity": int(qty * lot),
            "price": num(p.get("limitPrice")) or 0,
            "trigger_price": num(p.get("stopPrice")) or 0,
            # OpenAlgo's placeorder does not accept a validity field (verified live:
            # "Unknown field: validity"). The duration picked in the ticket is tracked
            # locally (order["duration"]) but not sent to the broker.
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
                 "status": "placing", "duration": duration, "updatedMs": now_ms,
                 # The ticket fields it was placed with: `broker.modify` sends them back.
                 "fields": {"product": product}}]
        self.force_poll()
        return order_id, [{"kind": "placed", "ticker": ticker, "side": side, "type": kind, "qty": qty}]

    def modify(self, p):
        with self.lock:
            order = next((o for o in self.orders if o["id"] == p["orderId"]), None)
        if not order or order["status"] not in ("placing", "working"):
            raise PluginError("The order is no longer working")
        exchange, _, symbol = order["ticker"].partition(":")
        # A modified order keeps the fields it was placed with (the host sends them back; the
        # placed order's own win over what came, the settings default stands for an order placed
        # before 0.1.9 without any).
        placed = str((order.get("fields") or {}).get("product") or "").upper()
        sent = str((p.get("fields") or {}).get("product") or "").upper()
        product = placed if placed in self.PRODUCTS else (sent if sent in self.PRODUCTS else (self.settings.get("product", "MIS").upper() or "MIS"))
        # A modified order keeps the duration it was placed with; a sent one wins (an IOC order can
        # be turned into a day order by changing the price, but the broker may refuse).
        sent_dur = str(p.get("duration") or "").lower()
        duration = sent_dur if sent_dur in VALIDITY else str(order.get("duration") or "day").lower()
        if duration not in VALIDITY:
            duration = "day"
        body = {
            "orderid": order["id"], "symbol": symbol, "exchange": exchange,
            "product": product,
            "pricetype": ("SL" if (order["type"] == "stop" and (num(p.get("limitPrice")) or num(order.get("limitPrice")))) else PRICE_TYPES.get(order["type"], "LIMIT")),
            "quantity": int((num(p.get("qty")) or order["qty"]) * (num(first_of(self._symbol_row(order["ticker"]), "lotsize", "lot_size")) or 1.0)),
            "price": num(p.get("limitPrice")) or num(order.get("limitPrice")) or 0,
            "trigger_price": num(p.get("stopPrice")) or num(order.get("stopPrice")) or 0,
            # OpenAlgo's modifyorder does not accept a validity field (verified live).
            "action": order["side"].upper(),
            "strategy": self.settings.get("strategy") or "OpenTrader",
            # the API wants the field even though the plugin never sets one (verified live)
            "disclosed_quantity": 0,
        }
        self._post("/api/v1/modifyorder", body)
        with self.lock:
            order.update(qty=num(p.get("qty")) or order["qty"],
                         limitPrice=num(p.get("limitPrice")), stopPrice=num(p.get("stopPrice")),
                         duration=duration,
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
        # OpenAlgo's /api/v1/closeposition closes *every* position for the strategy,
        # not just this one. Close the specific position with a market order the other
        # way instead, so only this position's quantity is squared off.
        close_qty = pos["qty"] if qty is None else qty
        events = self.place({"ticker": pos["ticker"], "side": "sell" if pos["side"] == "buy" else "buy",
                             "type": "market", "qty": close_qty})[1]
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
                units = pos["qty"] * self._lot_of(pos["ticker"])
                pos["unrealizedPnl"] = round((q["price"] - pos["avgPrice"]) * units * (1 if pos["side"] == "buy" else -1), 2)
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
            # The product type with each order: the ticket shows the three the deployment takes
            # (a segment's `broker.instrument` narrows them); the settings' product is the default.
            "capabilities": {"orderTypes": ["market", "limit", "stop", "stopLimit"], "brackets": False,
                             "positionBrackets": False, "partialClose": True, "modifyOrders": True,
                             "ticketFields": [self._product_field(["MIS", "CNC", "NRML"])],
                             "durations": [{"id": "day", "label": "Day", "default": True},
                                           {"id": "ioc", "label": "Immediate or Cancel", "orderTypes": ["market", "limit"]},
                                           {"id": "gtc", "label": "Good Till Cancel"}]},
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
            fresh = self.last_poll == 0.0  # the first read is at once — it is what lets the
            # broker state report connected within seconds of the start, not a minute in
            work = bool(self.positions) or any(o.get("status") in ("placing", "working") for o in self.orders)
        if not ((due or fresh) and (work or now - self.last_poll > 60 or fresh)):
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
            "qty": self._contracts(f"{exchange}:{symbol}", num(first_of(r, "quantity", "qty"), 0) or 0),
            "filledQty": self._contracts(f"{exchange}:{symbol}", num(first_of(r, "filled_quantity", "filledqty", "filled"), 0) or 0),
            "limitPrice": num(first_of(r, "price")),
            "stopPrice": num(first_of(r, "trigger_price", "triggerprice")),
            "avgFillPrice": num(first_of(r, "average_price", "avg_price", "averageprice")),
            "status": self.STATUS_MAP.get(status, "working"),
            "duration": DURATION_IDS.get(str(first_of(r, "validity", "validity_type") or "").upper().strip(), "day"),
            "message": first_of(r, "reason", "message", "remarks"),
            "updatedMs": self._ms(first_of(r, "updatetime", "update_time", "timestamp")),
        }

    def _wire_position(self, r):
        exchange = str(first_of(r, "exchange", "exchseg") or "").upper()
        symbol = str(first_of(r, "symbol", "tsym") or "")
        if not symbol:
            return None
        qty = num(first_of(r, "netqty", "quantity", "qty"), 0) or 0
        ticker = f"{exchange}:{symbol}"
        return {
            "id": ticker,
            "ticker": ticker,
            "side": "sell" if qty < 0 else "buy",
            "qty": self._contracts(ticker, abs(qty)),
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
        ticker = f"{exchange}:{symbol}"
        return {
            "id": str(first_of(r, "tradeid", "executionid", "orderid") or ""),
            "orderId": str(first_of(r, "orderid", "order_id") or ""),
            "ticker": ticker,
            "side": "sell" if side.startswith("s") else "buy",
            "qty": self._contracts(ticker, num(first_of(r, "quantity", "qty"), 0) or 0),
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
                self.log(f"OpenAlgo stream reconnecting ({_clean(str(e), key)})", "warn", "ws")
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
            self.last_ws_tick = time.time()  # the connect counts as life: the quiet clock starts here,
            # not at 0 — otherwise the first data.subscribe after a cold start reads the quiet check
            # as "no tick since forever" and fires the REST fallback the same second
        self.log(f"OpenAlgo stream connected ({urllib.parse.urlsplit(url).netloc})", "info", "ws")

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

        # Market depth (mode 3, five levels) gives the ticket's ladder a real bid / ask; the
        # auth answer says whether this broker streams it. Until the answer, ask for it.
        depth = True

        # The verified protocol (the app's built-in provider, working live): authenticate with the key on open,
        # then the whole set — `unsubscribe_all` and `subscribe` with the venue halves, no key in the frames.
        def resync(wanted):
            send_text(json.dumps({"action": "unsubscribe_all"}))
            if wanted:
                symbols = [{"exchange": t.split(":", 1)[0], "symbol": t.split(":", 1)[1]}
                           for t in sorted(wanted) if ":" in t]
                request = {"action": "subscribe", "mode": 3 if depth else "Quote", "symbols": symbols}
                if depth:
                    request["depth"] = 5
                text = json.dumps(request)
                if DEBUG or self._log_enabled("debug"):
                    msg = f"[ws >] {_clean(text, key)}"
                    print(msg, flush=True)
                    self._write_log("ws", f"{datetime.now(TZ_IST).strftime('%Y-%m-%d %H:%M:%S')} [debug] {msg}")
                send_text(text)
            with self.lock:
                self.ws_subscribed = set(wanted)

        send_text(json.dumps({"action": "authenticate", "api_key": key}))
        if DEBUG or self._log_enabled("debug"):
            msg = "[ws >] authenticate (key redacted)"
            print(msg, flush=True)
            self._write_log("ws", f"{datetime.now(TZ_IST).strftime('%Y-%m-%d %H:%M:%S')} [debug] {msg}")
        # The auth answer lists the broker's features: `depth` decides the subscription mode.
        # Read it before subscribing; a stream that does not answer in time keeps depth on.
        buffer = b""
        sock.settimeout(3.0)
        try:
            while True:
                part = sock.recv(65536)
                if not part:
                    raise PluginError("the stream closed during authentication")
                buffer += part
                frame, buffer = self._ws_frame(buffer)
                if frame is None or frame == ():
                    continue
                try:
                    answer = json.loads(frame.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if answer.get("type") == "auth":
                    features = answer.get("supported_features") or {}
                    depth = bool(features.get("depth", False))
                    self.log(f"stream: authenticated ({', '.join(sorted(k for k, on in features.items() if on)) or 'no features listed'})", "info", "ws")
                    break
        except socket.timeout:
            pass  # an older stream does not answer: the subscription says whether it takes depth
        sock.settimeout(1.0)
        # Order updates (fills, cancels, rejections) are pushed on the same socket
        # after authenticating — account-level, no symbols involved.
        send_text(json.dumps({"action": "subscribe_orders", "request_id": "orders-1"}))
        if DEBUG or self._log_enabled("debug"):
            msg = "[ws >] subscribe_orders"
            print(msg, flush=True)
            self._write_log("ws", f"{datetime.now(TZ_IST).strftime('%Y-%m-%d %H:%M:%S')} [debug] {msg}")
        last_ping = 0.0
        told_empty = False
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
                    continue  # ping answered or a control frame: there may be more in the buffer
                self._ws_message(frame)
            now = time.time()
            with self.lock:
                wanted = set(self.subscribed)
            if wanted != self.ws_subscribed:
                resync(wanted)  # the whole set again: the protocol has no incremental add / drop
            if not wanted and not told_empty:
                told_empty = True  # the price cache stays empty until something subscribes
                self.log("stream: the app has not subscribed symbols yet - open a chart or add one to the watchlist", "info", "ws")
            if now - last_ping > 20:
                last_ping = now  # the provider pings every 20 s; a silent socket dies quietly otherwise
                send_control(0x89)

    def _ws_frame(self, buffer):
        """One WebSocket text frame from `buffer`: the payload bytes, () for a control frame — or
        None while the buffer holds no complete frame yet (the caller goes back to recv)."""
        import struct

        if len(buffer) < 2:
            return None, buffer
        n = buffer[1] & 0x7F
        offset, length = 2, n
        if n == 126:
            if len(buffer) < 4:
                return None, buffer
            length = struct.unpack(">H", buffer[2:4])[0]
            offset = 4
        elif n == 127:
            if len(buffer) < 10:
                return None, buffer
            length = struct.unpack(">Q", buffer[2:10])[0]
            offset = 10
        if len(buffer) < offset + length:
            return None, buffer
        opcode = buffer[0] & 0x0F
        payload = buffer[offset:offset + length]
        rest = buffer[offset + length:]
        if opcode == 0x8:
            raise PluginError("the stream closed")  # close frame
        if opcode == 0x9:  # ping → pong. Client frames are masked: an unmasked pong is a protocol
            try:           # error, and the server closes the socket with 1002 "incorrect masking"
                sock = self.ws  # right after its first ping (verified live) — the stream then
                if sock:        # reconnects forever and the watchlist falls back to REST polling
                    mask = os.urandom(4)
                    sock.sendall(bytes(bytearray([0x8A, 0x80 | len(payload)]) + mask)
                                 + bytes(payload[i] ^ mask[i % 4] for i in range(len(payload))))
            except OSError:
                pass
            return (), rest
        if opcode == 0x1:
            return payload, rest
        if opcode == 0x2 and (DEBUG or self._log_enabled("debug")):  # binary: the shapes above assume JSON text; show what arrived instead
            msg = f"[ws <] binary frame: {payload[:60].hex()}"
            print(msg, flush=True)
            self._write_log("ws", f"{datetime.now(TZ_IST).strftime('%Y-%m-%d %H:%M:%S')} [debug] {msg}")
        return (), rest  # binary, pong, fragments: not ours

    def _ws_message(self, data):
        """One stream message into the price cache.

        The verified shape (the app's built-in provider, working live):
        `{"type": "market_data", "symbol": ..., "exchange": ..., "data": {open, high, low, ltp, volume,
        prev_close?, close}}` — on ticks `close` is the previous day's close and `prev_close` is often
        absent, so the two fall back through each other and the last quote. Run with OPENALGO_DEBUG=1 to
        see the raw traffic.
        """
        if DEBUG or self._log_enabled("debug"):
            msg = f"[ws <] {_clean(data.decode('utf-8', 'replace'), self._key())[:300]}"
            print(msg, flush=True)
            self._write_log("ws", f"{datetime.now(TZ_IST).strftime('%Y-%m-%d %H:%M:%S')} [debug] {msg}")
        try:
            message = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(message, dict):
            return
        # Order updates arrive on the same socket (account-level push).
        if message.get("type") == "order_update":
            self._handle_order_update(message)
            return
        if message.get("type") != "market_data":
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
                         "to see the raw traffic)", "warn", "ws")
            return
        self.last_ws_tick = time.time()  # the socket feeds prices; the REST fallback stands down
        ts = num(first_of(r, "ltt", "timestamp", "ts", "time"))
        t = int(ts) if ts and ts < 1e12 else (int(ts / 1000) if ts else int(time.time()))
        prev = num(first_of(r, "prev_close", "previous_close")) or num(first_of(r, "close"))
        quote = {"time": t, "price": price,
                 "open": num(first_of(r, "open")), "high": num(first_of(r, "high")),
                 "low": num(first_of(r, "low")), "close": num(first_of(r, "close")),
                 "volume": num(first_of(r, "volume"), 0) or 0,
                 "bid": num(first_of(r, "bid")), "ask": num(first_of(r, "ask")),
                 "prevClose": prev or (self.quotes.get(ticker) or {}).get("prevClose")}
        # A depth subscription (mode 3) carries five buy / sell levels; the ladder wants the
        # best of each (and a top-level bid / ask, when the broker sends one instead), and the
        # whole book for the size columns.
        book = r.get("depth")
        if isinstance(book, dict):
            buys, sells = book.get("buy") or [], book.get("sell") or []
            if buys and isinstance(buys[0], dict):
                quote["bid"] = num(buys[0].get("price"))
            if sells and isinstance(sells[0], dict):
                quote["ask"] = num(sells[0].get("price"))
            levels = book_levels(book)
            if levels:
                quote["depth"] = levels
        with self.lock:
            self.quotes[ticker] = quote
            # Bar formation follows the built-in provider's rules (its `absorb_tick` /
            # `track_bucket_volume`): the tick volume is the cumulative DAY volume, carried
            # through the ticks that arrive without one; the forming minute bucket is ltp-only
            # and its own volume is the day volume's delta since the bucket's first tick with a
            # volume — the day fields (open / high / low / volume) belong to the day bar alone.
            if quote["volume"]:
                self.day_vol[ticker] = quote["volume"]
            day_vol = self.day_vol.get(ticker) or 0
            now = int(time.time())
            start = now // 60 * 60
            bar = self.minute_bars.get(ticker)
            if not bar or bar[0] != start:
                self.minute_bars[ticker] = [start, price, price, price, price, 0]
                # a rolled bucket takes its base from this tick's day volume; a volume-less
                # roll delays the base rather than pouring the pre-bucket gap into the bar
                if quote["volume"]:
                    self.vol_base[ticker] = quote["volume"]
            else:
                if quote["volume"] and not self.vol_base.get(ticker):
                    self.vol_base[ticker] = quote["volume"]
                bar[2], bar[3], bar[4] = max(bar[2], price), min(bar[3], price), price
                base = self.vol_base.get(ticker)
                bar[5] = max(0, day_vol - base) if base else 0
            start = ist_midnight(now)
            bar = self.day_bars.get(ticker)
            if not bar or bar[0] != start:
                self.day_bars[ticker] = [start, quote["open"] or price, quote["high"] or price,
                                         quote["low"] or price, price, day_vol]
            else:
                bar[2], bar[3] = max(bar[2], quote["high"] or price), min(bar[3], quote["low"] or price)
                bar[4], bar[5] = price, day_vol
        # Pushed the moment it lands, not on the stream loop's next pass: a tick a second made the
        # chart repaint once a second however many the socket carried.
        if self.plugin is not None:
            self.plugin.push_quote(ticker)

    def _handle_order_update(self, message):
        """An order transition pushed by the broker (fill, cancel, reject). Refresh the
        books and tell the app, instead of waiting for the next REST poll."""
        order_id = str(message.get("orderid") or message.get("order_id") or "")
        status_raw = str(message.get("order_status") or "").lower().replace(" ", "").replace("-", "")
        STATUS_MAP = {
            "complete": "filled", "filled": "filled",
            "cancelled": "cancelled", "canceled": "cancelled",
            "rejected": "rejected",
            "open": "working", "triggerpending": "working",
            "pending": "working",
        }
        new_status = STATUS_MAP.get(status_raw)
        filled = num(message.get("filled_quantity") or message.get("filled_qty"))
        avg = num(message.get("average_price") or message.get("avg_price"))
        reason = str(message.get("rejection_reason") or "")
        events = []
        with self.lock:
            order = next((o for o in self.orders if o["id"] == order_id), None)
            if order is not None:
                was = order.get("status")
                if new_status:
                    order["status"] = new_status
                if filled is not None:
                    order["filledQty"] = filled
                if avg is not None and avg > 0:
                    order["avgFillPrice"] = avg
                order["updatedMs"] = int(time.time() * 1000)
                if was in (None, "placing") and order["status"] == "filled":
                    events.append({"kind": "executed", "ticker": order["ticker"], "side": order["side"],
                                   "type": order["type"], "qty": order["qty"], "price": order.get("avgFillPrice")})
                elif was in (None, "placing", "working") and order["status"] == "cancelled":
                    events.append({"kind": "cancelled", "ticker": order["ticker"], "side": order["side"],
                                   "type": order["type"], "qty": order["qty"]})
                elif was in (None, "placing", "working") and order["status"] == "rejected":
                    events.append({"kind": "rejected", "ticker": order["ticker"], "side": order["side"],
                                   "type": order["type"], "qty": order["qty"], "message": reason})
        # refresh the books so positions/funds follow the fill
        try:
            self._refresh_books()
        except PluginError:
            pass
        if self.plugin is not None:
            try:
                self.plugin.events(events)
                self.plugin.push_state()
            except Exception:  # noqa: BLE001
                pass
        self.log("order_update %s -> %s" % (order_id, new_status or status_raw))

    def _subscribed_match(self, bare):
        """The subscribed whole symbol a bare echo belongs to, when exactly one matches (the provider's
        identity rule: an ambiguous bare echo resolves to nothing rather than to the wrong listing)."""
        with self.lock:
            matches = [t for t in self.subscribed if t.split(":", 1)[-1] == bare]
        return matches[0] if len(matches) == 1 else None
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
        self.pushed_connected = False  # what the last broker.state push reported
        self.last_state_push = 0.0

    # ── JSON-RPC ──
    def send(self, message):
        line = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self.send_lock:
            self.sock.sendall(line)

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def log(self, message, level="info", category="api"):
        """A line in the plugin's log in Settings (stdout reaches it too, but without a level).
        Also appended to the per-category file (openalgo-api.log / openalgo-ws.log) when enabled
        in settings, so an AI agent or the user can read it from disk."""
        if self.alive:
            self.notify("log", {"level": level, "message": message})
        else:
            print(message, flush=True)
        src = getattr(self, "source", None)
        if src is not None and getattr(src, "_log_enabled", None):
            try:
                if src._log_enabled(category):
                    ts = datetime.now(TZ_IST)
                    src._write_log(category, f"{ts.strftime('%Y-%m-%d %H:%M:%S')} [{level}] {_clean(str(message), src._key())}")
            except Exception:
                pass

    def push_state(self):
        self.notify("broker.state", self.source.state())

    def push_quote(self, ticker):
        """One WebSocket tick straight to the app: the chart's last price waits for no poll. The
        stream loop keeps pushing every second too (the REST fallback's quotes, the books), so a
        repeated price here is harmless."""
        if not self.alive:
            return
        q = self.source.quote(ticker)
        if not q:
            return
        try:
            if ticker in self.tickers:
                self.notify("data.quote", q)
            if ticker in self.watched:
                price = q["price"]
                bid, ask = self.source.best_bid_ask(ticker)
                if bid is None or ask is None:  # a quote stream without depth: the tick-size spread
                    tick = self.source.tick(ticker)
                    half = tick / 2 if tick else 0.0
                    bid, ask = round(price - half, 10), round(price + half, 10)
                quote = {"ticker": ticker, "bid": bid, "ask": ask, "last": price}
                book = self.source.book(ticker)
                if book:
                    quote["depth"] = book
                self.notify("broker.quote", quote)
        except OSError:
            pass  # the app's socket is gone; run() notices and reconnects

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
        if method == "broker.getState":
            return s.state()
        if method == "broker.instrument":
            return s.instrument(p["ticker"])
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
            raw = p.get("tickers", [])
            self.tickers = {t for t in raw if self.source.known(t)}
            dropped = len(raw) - len(self.tickers)
            if dropped:
                self.log("data.subscribe: dropped %d stale symbol(s) (e.g. NSE:BANKNIFTY -> use NSE_INDEX:BANKNIFTY)" % dropped, "info", "ws")
            self.log("data.subscribe: %d tickers" % len(self.tickers))
            self.forward_subscription()
        elif method == "broker.watchQuote":
            self.watched = set(p.get("tickers", []))
            self.log("broker.watchQuote: %d tickers" % len(self.watched))
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
                # REST quote poll every 5 s when the WebSocket is not connected (the WebSocket is the
                # primary source; REST is the fallback, mirroring the built-in provider's poller). A
                # connected-but-quiet socket falls back too, throttled: this deployment subscribes
                # all-or-nothing, so one symbol it rejects silences every tick, and after a weekend
                # restart the price cache starts empty — without this the watchlist stays blank.
                # With quotes already cached, the refresh waits for the session (a quiet socket off
                # hours is just the market being shut).
                now = time.time()
                if self.source.ws is None:
                    if now - self.source.last_rest_poll >= 5.0:
                        self.source.last_rest_poll = now
                        self.source.poll_quotes_rest()
                elif self.source.subscribed and now - self.source.last_rest_poll >= 10.0:
                    have = self.source.has_quotes()
                    quiet = now - self.source.last_ws_tick > (90.0 if not have else 300.0)
                    if quiet and (not have or in_market_hours()):
                        self.source.last_rest_poll = now
                        if not self.source.ws_quiet_warned:
                            self.source.ws_quiet_warned = True  # once: the quiet-socket warn says its piece
                            self.log("stream: the WebSocket is connected but quiet — polling REST quotes "
                                     "as a fallback (the subscribe frame may have been rejected; run with "
                                     "OPENALGO_DEBUG=1 to see it)", "warn")
                        self.source.poll_quotes_rest()
                events = self.source.on_tick()
                for q in quotes:
                    if q["ticker"] in self.tickers:
                        self.notify("data.quote", q)
                    if q["ticker"] in self.watched:
                        price = q["price"]
                        bid, ask = q.get("bid"), q.get("ask")
                        if bid is None or ask is None:  # a quote stream without depth: the tick-size spread
                            tick = self.source.tick(q["ticker"])
                            half = tick / 2 if tick else 0.0
                            bid, ask = round(price - half, 10), round(price + half, 10)
                        quote = {"ticker": q["ticker"], "bid": bid, "ask": ask, "last": price}
                        book = self.source.book(q["ticker"])
                        if book:
                            quote["depth"] = book
                        self.notify("broker.quote", quote)
                self.events(events)
                # The broker state reaches the host when its connected flag or its books move,
                # and at least every 30 s (with open positions, every pass: the open P&L moves
                # with the price). The connect-time push carries connected=False — no book has
                # been read yet — and with no open positions nothing else ever re-pushed it, so
                # the ticket kept refusing a working plugin ("Broker not connected").
                rest_ok = self.source.rest_ok
                now = time.time()
                if (self.source.positions or events or rest_ok != self.pushed_connected
                        or now - self.last_state_push >= 30.0):
                    self.push_state()
                    self.last_state_push = now
                    self.pushed_connected = rest_ok
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
        self.pushed_connected = self.source.rest_ok  # what the connect-time push just reported
        self.last_state_push = time.time()
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


def _reset_logs():
    """Truncate the per-category log files once per process start, so each app launch
    begins with a fresh log (the files are capped at ~1 MB anyway)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / "OpenTraderPlugins"
    try:
        d.mkdir(parents=True, exist_ok=True)
        for name in ("openalgo-api.log", "openalgo-ws.log"):
            p = d / name
            try:
                p.write_text("", encoding="utf-8")
            except OSError:
                pass
    except OSError:
        pass


if __name__ == "__main__":
    _reset_logs()
    Plugin(OpenAlgoSource()).run()
