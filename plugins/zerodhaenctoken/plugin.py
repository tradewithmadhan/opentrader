#!/usr/bin/env python3
"""OpenTrader plugin: Zerodha Kite personal API with enctoken — market data over REST + WebSocket.

Phase 1 (this file): datafeed only — the instrument master, search, history and live ticks. Trading is added
later. The enctoken is the cookie from a logged-in kite.zerodha.com session; it goes in a masked setting and is
never logged. Kite's REST quote endpoints need an OAuth key, so every live price here comes from the Kite ticker
over wss://ws.kite.trade, exactly as broker/zerodhaenctoken does in OpenAlgo.

The connection layer at the bottom is OpenTrader's plugin template, unchanged, except that requests run on their
own threads and the source locks only the state each one touches (a slow history call must not hold up ticks).

Run it:
  - during development:  python plugin.py           (reads the connection file the app writes, see README)
  - when installed:      the app starts the built executable and passes the connection in the environment.
"""
import base64
import csv
import gzip
import hashlib
import http.client
import io
import json
import os
import queue
import re
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import nse

PLUGIN_ID = "zerodhaenctoken"
PLUGIN_VERSION = "0.7.1"
# Plugin API 10: 3 = password settings / data folder / prompts / provides, 4 = data.watchlists,
# 5 = data.events, 6 = extra series (OI) and instrument values (fields), 7 = broker pages (Holdings),
# 8 = data.symbolInfoChanged, plugin.toast and data.scripts, 9 = tradeTicker (continuous futures),
# 10 = script syminfo fields (root, expiration, isin, baseCurrency, country, volumeType).
API = 10
DEV_CONNECTION_FILE = "OpenTraderPlugins/zerodhaenctoken-dev.json"

TZ_IST = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")

# Kite's own web app talks to these endpoints from a browser session. Send the ordinary browser headers
# alongside the enctoken so the requests read as that same logged-in session, not as an unnamed script.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36")
# The ordinary CORS/client-hint headers the Kite web app sends on its own /oms calls. No Cookie header and no
# Cloudflare clearance token: the enctoken in Authorization is the session, and those cookies cannot be copied
# reliably (and must never be logged).
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://kite.zerodha.com",
    "Referer": "https://kite.zerodha.com/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Priority": "u=1, i",
    "Sec-CH-UA": '"Not.A/Brand";v="24", "Chromium";v="158"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
KITE_VERSION = "3.0.0"

# Kite's instruments master and personal API carry no logo, sector or industry. The company-info service used by
# the app's OpenAlgo plugin fills those in for cash equities; a failed lookup is cached briefly and never fails
# symbol_info. Tests point this at their fake server.
COMPANY_API = "https://api.dharun.dev/v1"
# NSE's official listing lists carry a DATE OF LISTING per symbol, used to cap how far back the app asks for a
# symbol that has not existed that long. The SME file has slightly different headers and 2-digit years.
NSE_EQUITY_LIST_URL = nse.REPORTS["equity"]
NSE_SME_LIST_URL = nse.REPORTS["sme"]
# How far back the app may scroll, per bar family. This is the `history` value returned from data.symbolInfo;
# it is NOT the per-request Kite chunk size (that is historyDaysMinute/historyDaysDaily, set in the manifest).
#
# OpenTrader caches this per symbol and clamps EVERY data.bars request, including older scroll pages, in
# reachable() (src-tauri/src/plugins/data.rs): from = max(from, now - depth); a page entirely older than that
# returns no bars at all. So these numbers are the plugin's answer to "how much history does this source have?".
#
# _history_depth() takes min(here, days since the symbol's NSE listing date), so a recently listed stock gets a
# small depth and cannot make the app ask for years before it existed. Symbols without a listing row use these
# maxima directly.
#
# Kite's personal API serves day bars back to at least 2000 (verified), so 10950 days (~30 years) covers it.
# Both families use the same 30-year ceiling, so `history` is simply "days since listing, capped at 30 years"
# for every timeframe. Minute bars were verified back to at least Jan 2024; Kite documents no exact floor and
# may have no minute data in the early years. Declaring the same depth as days means the app will allow the
# scroll-back attempt all the way to the listing date; where Kite has no minute candles the page comes back
# empty and the chart shows its history-end marker. That is preferred here over an artificial 10-year stop.
# Week/month charts use the day family; 3m..240m charts use the minute family.
HISTORY_DAYS_MAX = {"minute": 10950, "day": 10950}

# Plugin API 4/6 extension points. A sample blue-chip list plus the master's NSE/BSE indices are offered as
# watchlists; replace or extend these with your own lists, and add extra fields or series you can source here.
SAMPLE_BLUE_CHIPS = ("RELIANCE", "TCS", "INFOSYS", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC",
                     "KOTAKBANK", "LICI", "LT", "HCLTECH", "ASIANPAINT", "MARUTI", "TITAN", "ULTRACEMCO",
                     "SUNPHARMA", "BAJFINANCE", "NESTLEIND", "WIPRO", "ONGC", "TATAMOTORS", "ADANIENT",
                     "M&M", "TATASTEEL", "JSWSTEEL", "POWERGRID", "NTPC", "COALINDIA", "DRREDDY")
EXTRA_FIELDS = []   # more {"name":…, "value":…} instrument values, added to every symbol's fields
EXTRA_SERIES = []   # more extra series names, added to every symbol's series (serve <ticker>_<name>)
# A sample extra series: five values in one bar's OHLCV slots, read by CustomOne_5Values with one request.security.
# Declared only for these symbols so a stray <symbol>_CUSTOMONE on another symbol gets no made-up numbers.
CUSTOMONE_SYMBOLS = {"NSE_INDEX:NIFTY"}
CUSTOMONE_NAME = "CUSTOMONE"
# NSE's live price-band-hitter list becomes a watchlist, refreshed on this cadence.
PRICE_BAND_POLL_SEC = 300

# A brief empty broker.watchQuote/watchDepth is ignored: the app re-sends the watch on each chart switch, and
# dropping full mode for that instant floods Kite with mode frames and blanks the ticket's bid/ask.
WATCH_GRACE_SEC = 1.5
# The app re-asks data.events after an hour. Re-check the source on that cadence so a new or cancelled corporate
# action can be pushed with data.eventsChanged instead of waiting for the next day's cache rollover.
EVENTS_REFRESH_SEC = 3600
# Kite's corporate-actions endpoint refuses ranges over 1095 days, so walk it in 3-year chunks from this year.
EVENTS_FROM = date(1990, 1, 1)
EVENTS_CHUNK_DAYS = 1095

# Bump only when parse_master_csv's output shape changes; not on every plugin release (the master is 31 MB).
# Plugin-provided scripts (data.scripts, API 8).
SCRIPT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_SCRIPTS = 50
MAX_SCRIPT_SOURCE = 512 * 1024
TOAST_POSITIONS = ("top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right")

MASTER_CACHE_VERSION = 1

# A Kite ticker connection carries at most 3000 instruments. The app can subscribe more (a large watchlist plus
# charts and alerts), so keep the set within the broker's limit instead of losing the whole stream.
MAX_TOKENS = 3000

DEFAULT_API_URL = "https://kite.zerodha.com"
DEFAULT_WS_URL = "wss://ws.kite.trade"
DEFAULT_MASTER_URL = "https://api.kite.trade/instruments"
DEFAULT_EXCHANGES = "NSE,BSE,NFO,BFO,CDS,BCD,MCX,NCO,NSE_INDEX,BSE_INDEX,MCX_INDEX,GLOBAL_INDEX"

ALL_EXCHANGES = set(DEFAULT_EXCHANGES.split(","))
INDEX_EXCHANGES = {"NSE": "NSE_INDEX", "BSE": "BSE_INDEX", "MCX": "MCX_INDEX", "CDS": "CDS_INDEX"}

# Kite segment → the app's search-row type. The strings must match the app's vocabulary exactly.
KINDS = {
    "EQ": "stock", "BE": "stock", "SM": "stock", "MF": "fund", "MFP": "fund",
    "IDX": "index", "IND": "index",
    "FUT": "futures", "FUTCUR": "futures",
    "CE": "option", "PE": "option", "OPT": "option", "CUR": "forex",
}

# Regular session in the market's clock. Indices follow their cash segment.
SESSIONS = {
    "NSE": "0915-1530", "BSE": "0915-1530", "NFO": "0915-1540", "BFO": "0915-1530",
    "NCO": "0915-1530", "CDS": "0900-1700", "BCD": "0900-1700",
    "MCX": "0900-2330", "GLOBAL_INDEX": "0000-2359:23456",
}

# Kite's index tradingsymbol → the app symbol users type. Ported from OpenAlgo's master loader.
NSE_INDEX_NAMES = {
    "NIFTY 50": "NIFTY", "NIFTY NEXT 50": "NIFTYNXT50", "NIFTY FIN SERVICE": "FINNIFTY",
    "NIFTY BANK": "BANKNIFTY", "NIFTY MID SELECT": "MIDCPNIFTY", "INDIA VIX": "INDIAVIX",
    "NIFTY 100": "NIFTY100", "NIFTY 200": "NIFTY200", "NIFTY 500": "NIFTY500",
    "NIFTY ALPHA 50": "NIFTYALPHA50", "NIFTY AUTO": "NIFTYAUTO", "NIFTY COMMODITIES": "NIFTYCOMMODITIES",
    "NIFTY CONSUMPTION": "NIFTYCONSUMPTION", "NIFTY CPSE": "NIFTYCPSE", "NIFTY DIV OPPS 50": "NIFTYDIVOPPS50",
    "NIFTY ENERGY": "NIFTYENERGY", "NIFTY FMCG": "NIFTYFMCG", "NIFTY GROWSECT 15": "NIFTYGROWSECT15",
    "NIFTY INFRA": "NIFTYINFRA", "NIFTY IT": "NIFTYIT", "NIFTY MEDIA": "NIFTYMEDIA",
    "NIFTY METAL": "NIFTYMETAL", "NIFTY MNC": "NIFTYMNC", "NIFTY PHARMA": "NIFTYPHARMA",
    "NIFTY PSE": "NIFTYPSE", "NIFTY PSU BANK": "NIFTYPSUBANK", "NIFTY PVT BANK": "NIFTYPVTBANK",
    "NIFTY REALTY": "NIFTYREALTY", "NIFTY SERV SECTOR": "NIFTYSERVSECTOR",
    "NIFTY MID LIQ 15": "NIFTYMIDLIQ15", "NIFTY MIDCAP 50": "NIFTYMIDCAP50",
    "NIFTY MIDCAP 100": "NIFTYMIDCAP100", "NIFTY MIDCAP 150": "NIFTYMIDCAP150",
    "NIFTY MIDSML 400": "NIFTYMIDSML400", "NIFTY SMLCAP 50": "NIFTYSMLCAP50",
    "NIFTY SMLCAP 100": "NIFTYSMLCAP100", "NIFTY SMLCAP 250": "NIFTYSMLCAP250",
    "NIFTY100 EQL WGT": "NIFTY100EQLWGT", "NIFTY100 LIQ 15": "NIFTY100LIQ15",
    "NIFTY100 LOWVOL30": "NIFTY100LOWVOL30", "NIFTY100 QUALTY30": "NIFTY100QUALTY30",
    "NIFTY200 QUALTY30": "NIFTY200QUALTY30", "NIFTY50 DIV POINT": "NIFTY50DIVPOINT",
    "NIFTY50 EQL WGT": "NIFTY50EQLWGT", "NIFTY50 PR 1X INV": "NIFTY50PR1XINV",
    "NIFTY50 PR 2X LEV": "NIFTY50PR2XLEV", "NIFTY50 TR 1X INV": "NIFTY50TR1XINV",
    "NIFTY50 TR 2X LEV": "NIFTY50TR2XLEV", "NIFTY50 VALUE 20": "NIFTY50VALUE20",
    "NIFTY GS 10YR": "NIFTYGS10YR", "NIFTY GS 10YR CLN": "NIFTYGS10YRCLN",
    "NIFTY GS 11 15YR": "NIFTYGS1115YR", "NIFTY GS 15YRPLUS": "NIFTYGS15YRPLUS",
    "NIFTY GS 4 8YR": "NIFTYGS48YR", "NIFTY GS 8 13YR": "NIFTYGS813YR",
    "NIFTY GS COMPSITE": "NIFTYGSCOMPSITE",
}
BSE_INDEX_NAMES = {
    "SNSX50": "SENSEX50", "SNXT50": "BSESENSEXNEXT50", "MID150": "BSE150MIDCAPINDEX",
    "LMI250": "BSE250LARGEMIDCAPINDEX", "MSL400": "BSE400MIDSMALLCAPINDEX",
    "AUTO": "BSEAUTO", "BSE CG": "BSECAPITALGOODS", "CARBON": "BSECARBONEX",
    "BSE CD": "BSECONSUMERDURABLES", "CPSE": "BSECPSE", "DOL100": "BSEDOLLEX100",
    "DOL200": "BSEDOLLEX200", "DOL30": "BSEDOLLEX30", "ENERGY": "BSEENERGY",
    "BSEFMC": "BSEFASTMOVINGCONSUMERGOODS", "FINSER": "BSEFINANCIALSERVICES",
    "GREENX": "BSEGREENX", "BSE HC": "BSEHEALTHCARE", "INFRA": "BSEINDIAINFRASTRUCTUREINDEX",
    "INDSTR": "BSEINDUSTRIALS", "BSE IT": "BSEINFORMATIONTECHNOLOGY", "BSEIPO": "BSEIPO",
    "LRGCAP": "BSELARGECAP", "METAL": "BSEMETAL", "MIDCAP": "BSEMIDCAP",
    "MIDSEL": "BSEMIDCAPSELECTINDEX", "OILGAS": "BSEOIL&GAS", "POWER": "BSEPOWER",
    "BSEPSU": "BSEPSU", "REALTY": "BSEREALTY", "SMLCAP": "BSESMALLCAP",
    "SMLSEL": "BSESMALLCAPSELECTINDEX", "SMEIPO": "BSESMEIPO", "TECK": "BSETECK",
    "TELCOM": "BSETELECOM",
}
GLOBAL_INDEX_NAMES = {"GIFT NIFTY": "GIFTNIFTY"}

# Kite reports lot_size = 1 for every MCX row (quantity is in contracts). The real market lot is needed for
# pointValue and, later, for turning units into Kite contracts. Ported from OpenAlgo's mcx_contract_size.py.
MCX_CONTRACT_SIZES = {
    "ALUMINI": 1, "ALUMINIUM": 5, "CARDAMOM": 100, "COPPER": 2500, "COTTON": 25,
    "COTTONOIL": 5, "CRUDEOIL": 100, "CRUDEOILM": 10, "ELECDMBL": 50, "GOLD": 1,
    "GOLDGUINEA": 8, "GOLDM": 100, "GOLDPETAL": 1, "GOLDTEN": 10, "KAPAS": 4,
    "LEAD": 5, "LEADMINI": 1, "MCXBULLDEX": 30, "MCXMETLDEX": 40, "MENTHAOIL": 360,
    "NATGASMINI": 250, "NATURALGAS": 1250, "NICKEL": 250, "SILVER": 30,
    "SILVER100": 100, "SILVERM": 5, "SILVERMIC": 1, "STEELREBAR": 5, "ZINC": 5, "ZINCMINI": 1,
}
MCX_SIZE_REVISIONS = {"MCXBULLDEX": ((date(2026, 11, 1), 15),)}

# App resolution → Kite interval token. Verified live on the personal OMS: 2/4/60-minute, 2/3/4-hour and
# 5/10/15/30/45-second history are all served directly. Kite names hours "2hour"/"3hour"/"4hour", not
# "120minute"/"180minute"/"240minute" (those are refused).
INTERVALS = {
    "1": "minute", "2": "2minute", "3": "3minute", "4": "4minute", "5": "5minute",
    "10": "10minute", "15": "15minute", "30": "30minute", "60": "60minute",
    "120": "2hour", "180": "3hour", "240": "4hour",
    "5S": "5second", "10S": "10second", "15S": "15second", "30S": "30second", "45S": "45second",
    "1D": "day",
}
SECOND_INTERVALS = {"5S": 5, "10S": 10, "15S": 15, "30S": 30, "45S": 45}  # tick-built fallback only
SERVED_INTERVALS = list(INTERVALS)
HOUR_INTERVALS = {"60minute", "2hour", "3hour", "4hour"}
# Hard per-request limits measured live (app settings are clamped to these).
CHUNK_DAYS = {"second": 2, "minute": 60, "hour": 400, "day": 1900}
# Aggregated daily timeframes (3M/12M) do not scroll the app, so a single 1900-day chunk would leave them starting
# only ~5 years back. Fill the whole asked daily window in one answer, capped so Kite is never stormed.
MAX_DAILY_PAGES = 8
TICK_BUFFER_SEC = 7200  # fallback second bars, used only if Kite history refuses

PROFILE_PATH = "/oms/user/profile"
HISTORY_PATH = "/oms/instruments/historical/{token}/{interval}"


class PluginError(Exception):
    """An error answered to the app; `message` is shown to the user."""

    def __init__(self, message, code=-32000):
        super().__init__(message)
        self.code = code


class AuthError(Exception):
    """The enctoken was refused (403/401 or a Kite token error text)."""


def num(v, default=None):
    """A broker number that arrives as a string, blank, or not at all."""
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)) and v == v:
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return default
    return default


def ist_midnight(ts):
    """Epoch seconds of `ts`'s trading day's midnight in IST (a daily bar's start)."""
    day = datetime.fromtimestamp(ts, TZ_IST).date()
    return int(datetime(day.year, day.month, day.day, tzinfo=TZ_IST).timestamp())


def parse_kite_date(text):
    """Kite's expiry ("2026-11-25" or "25-NOV-26") as a date, or None."""
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    return None


def mcx_size(name, expiry_text):
    """Real MCX contract size of an underlying, honouring revisions by expiry."""
    if not isinstance(name, str):
        return None
    root = name.strip().upper()
    size = MCX_CONTRACT_SIZES.get(root)
    if size is None:
        return None
    expiry = parse_kite_date(expiry_text)
    if expiry is not None:
        for effective_from, revised in MCX_SIZE_REVISIONS.get(root, ()):
            if expiry >= effective_from:
                size = revised
    return size


def divisor_for_exchange(exchange):
    """The Kite price divisor: CDS is quoted in 10^7 paise, BCD in 10^4, the rest in paise."""
    return 10_000_000 if exchange == "CDS" else (10_000 if exchange == "BCD" else 100)


def local_app_data():
    if sys.platform == "win32":
        return os.environ["LOCALAPPDATA"]
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support")
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def plugin_data_dir():
    env = os.environ.get("OPENTRADER_PLUGIN_DATA")
    return Path(env) if env else Path(local_app_data()) / "OpenTraderPlugins" / PLUGIN_ID


def clean_symbol(ticker, exchange):
    """The symbol users type and other reports use, from Kite's tradingsymbol.

    Kite's NSE/BSE cash rows carry a series suffix (`STLTECH-BE`, `ABH-SM`, `IEML-RE1-ST`) that no other
    report has. Keep `ticker` for Kite's own calls and expose the bare name as `symbol` to the app.
    """
    if exchange in ("NSE", "BSE") and "-" in ticker:
        base, _, suffix = ticker.rpartition("-")
        if base and 1 <= len(suffix) <= 3 and suffix.isalnum():
            return base.upper()
    return ticker.upper()


def parse_master_csv(text):
    """Kite's public instrument CSV → rows keyed by (app exchange, ticker).

    The app exchange is Kite's exchange; index segments get their own suffix and GLOBAL/NSEIX fold into
    GLOBAL_INDEX. The ticker is Kite's tradingsymbol, with the major index names shortened the way users type
    them. `token` is the instrument_token used by both history and the ticker.
    """
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        kite_exchange = str(r.get("exchange") or "").strip().upper()
        segment = str(r.get("segment") or "").strip().upper()
        if segment == "INDICES":
            app_exchange = INDEX_EXCHANGES.get(kite_exchange, kite_exchange)
        elif kite_exchange in ("GLOBAL", "NSEIX"):
            app_exchange = "GLOBAL_INDEX"
        else:
            app_exchange = kite_exchange
        if app_exchange not in ALL_EXCHANGES:
            continue
        raw_ticker = str(r.get("tradingsymbol") or "").strip()
        if not raw_ticker:
            continue
        if app_exchange == "NSE_INDEX":
            ticker = NSE_INDEX_NAMES.get(raw_ticker, raw_ticker)
        elif app_exchange == "BSE_INDEX":
            ticker = BSE_INDEX_NAMES.get(raw_ticker, raw_ticker)
        elif app_exchange == "GLOBAL_INDEX":
            ticker = GLOBAL_INDEX_NAMES.get(raw_ticker, raw_ticker)
        else:
            ticker = raw_ticker

        name = str(r.get("name") or "").strip() or raw_ticker
        kind_raw = str(r.get("instrument_type") or "").strip().upper()
        # Kite leaves instrument_type blank on index rows; the segment is the reliable signal.
        kind = "index" if app_exchange.endswith("_INDEX") else KINDS.get(kind_raw, "stock")
        tick = num(r.get("tick_size")) or 0.05
        lot = num(r.get("lot_size")) or 1.0
        if app_exchange == "MCX":
            lot = mcx_size(name, str(r.get("expiry") or "")) or lot
        expiry = str(r.get("expiry") or "").strip()
        strike = num(r.get("strike"))
        parts = [name]
        if kind in ("futures", "option"):
            # Kite's CSV often carries strike = 0 on futures (and on some options); it is "no strike",
            # not a real value — showing it reads as "BANKEX 0 FUT ...".
            if strike is not None and strike > 0:
                parts.append(f"{strike:g}")
            if kind_raw:
                parts.append(kind_raw)
            if expiry:
                parts.append(expiry)
        try:
            token = int(str(r.get("instrument_token") or "").strip())
        except ValueError:
            continue
        out.append({
            "exchange": app_exchange, "ticker": ticker, "token": token, "symbol": clean_symbol(ticker, app_exchange),
            "kite_exchange": kite_exchange, "name": name, "description": " ".join(parts),
            "type": kind, "tick": tick, "lot": lot, "expiry": expiry,
        })
    return out


class Instruments:
    """The instrument master: one public CSV per day, cached in the plugin's data folder."""

    def __init__(self, source):
        self.source = source
        self.lock = threading.RLock()
        self.rows = []
        self.by_key = {}
        self.by_token = {}
        self.by_kite = {}
        self.token_meta = {}
        # Continuous futures: synthetic `<ROOT>1!` → the front contract, plus token → app symbols for quotes.
        self.continuous = {}
        self.token_aliases = {}
        self.catalog = None
        self.day = None
        self.loading = None

    @staticmethod
    def _path(day):
        # Version only when the parsed shape changes — a plugin update must not re-download 31 MB every time.
        return plugin_data_dir() / "cache" / f"master-v{MASTER_CACHE_VERSION}-{day}.json"

    @staticmethod
    def _existing_path(day):
        """Read today's master from the new name, or migrate an older version-named file instead of refetching."""
        new = Instruments._path(day)
        if new.exists():
            return new
        try:
            old = sorted((plugin_data_dir() / "cache").glob(f"master-*-{day}.json"),
                         key=lambda p: p.stat().st_mtime)
            if old:
                return old[-1]
        except OSError:
            pass
        return new

    def ensure(self):
        """Load today's master (memory → disk → Kite). Several callers share one fetch."""
        today = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        with self.lock:
            if self.day == today:
                return self.rows
            if self.loading is None:
                self.loading = threading.Event()
                mine = True
            else:
                mine = False
        if not mine:
            self.loading.wait(60)
            with self.lock:
                return self.rows
        rows = None
        try:
            path = self._existing_path(today)
            try:
                with open(path, encoding="utf-8") as f:
                    rows = json.load(f)
            except (OSError, ValueError):
                pass
            if rows is None:
                text = self.source.fetch_master()
                rows = parse_master_csv(text)
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(rows, f)
                except OSError:
                    pass
            with self.lock:
                self.rows = rows
                # A cache written before `symbol` existed is filled in memory; no 30 MB re-download for this.
                for r in rows:
                    if not r.get("symbol"):
                        r["symbol"] = clean_symbol(r["ticker"], r["exchange"])
                self.by_key = {}
                for r in rows:  # Kite's ticker wins a collision with a bare symbol of another row
                    self.by_key.setdefault((r["exchange"], r["ticker"]), r)
                for r in rows:
                    self.by_key.setdefault((r["exchange"], r["symbol"]), r)
                self.by_token = {r["token"]: r for r in rows}
                # Hot path: the tick thread reads this on every packet without taking the instruments lock.
                self.token_meta = {r["token"]: (f"{r['exchange']}:{r['ticker']}", r["tick"]) for r in rows}
                self.by_kite = {(r["kite_exchange"], r["ticker"]): r for r in rows}
                self._build_continuous(rows)
                self.catalog = None
                self.day = today
                self.source.log(f"instrument master: {len(rows)} rows ready")
        except Exception as e:
            self.source.log(f"instrument master: {e}", "warn")
            with self.lock:
                if not self.rows:
                    self.rows = []
                    self.by_key = {}
                    self.by_token = {}
                    self.by_kite = {}
                    self.token_meta = {}
                    self.continuous = {}
                    self.token_aliases = {}
        finally:
            with self.lock:
                event, self.loading = self.loading, None
            event.set()
        return self.rows

    def _build_continuous(self, rows):
        """One synthetic `<ROOT>1!` symbol per NFO root with more than one expiry (TradingView style)."""
        self.continuous = {}
        self.token_aliases = {}
        today = datetime.now(TZ_IST).date()
        by_root = {}
        for row in rows:
            if row["exchange"] != "NFO" or row.get("type") != "futures" or not row.get("expiry"):
                continue
            try:
                expiry = date.fromisoformat(row["expiry"])
            except (ValueError, TypeError):
                continue
            root = str(row.get("name") or "").strip().upper()
            if root.isalnum():
                by_root.setdefault(root, []).append((expiry, row))
        for root, entries in by_root.items():
            if len({e[0] for e in entries}) < 2:
                continue
            entries.sort(key=lambda e: (e[0], e[1]["ticker"]))
            upcoming = [e for e in entries if e[0] >= today] or entries
            expiry, front = upcoming[0]
            symbol = f"{root}1!"
            synthetic = dict(front)
            synthetic.update({"symbol": symbol, "name": root,
                              "description": f"{root} Continuous Future", "continuous": True,
                              "root": root, "expiration": expiry.isoformat()})
            with self.lock:
                self.by_key[("NFO", symbol)] = synthetic
            whole = f"NFO:{symbol}"
            self.continuous[whole] = front["ticker"]
            aliases = self.token_aliases.setdefault(front["token"], [])
            for name in (whole, f"NFO:{front['symbol']}"):
                if name not in aliases:
                    aliases.append(name)

    def row(self, ticker):
        """The row of a whole symbol ("NSE:RELIANCE"), or None."""
        self.ensure()
        exchange, sep, symbol = ticker.partition(":")
        with self.lock:
            if not sep:
                return None
            return self.by_key.get((exchange.upper(), symbol.upper()))

    def clean_of(self, whole):
        """The bare app symbol of a whole Kite ticker, when it differs (STLTECH-BE → NSE:STLTECH)."""
        exchange, sep, ticker = whole.partition(":")
        if not sep:
            return None
        with self.lock:
            row = self.by_key.get((exchange.upper(), ticker.upper()))
        if not row or row["symbol"].upper() == ticker.upper():
            return None
        return f"{exchange}:{row['symbol']}"

    def app_symbols_for(self, whole):
        """Other app names of the same instrument: the clean symbol and any continuous future over it."""
        exchange, sep, ticker = whole.partition(":")
        if not sep:
            return []
        with self.lock:
            row = self.by_key.get((exchange.upper(), ticker.upper()))
            aliases = list(self.token_aliases.get(row["token"], [])) if row else []
        names = [f"{exchange}:{row['symbol']}"] if row else []
        names.extend(aliases)
        return [name for name in dict.fromkeys(names) if name.upper() != whole.upper()]

    def token_row(self, token):
        with self.lock:
            return self.by_token.get(int(token))

    def kite_row(self, kite_exchange, tradingsymbol):
        """A book/postback row's Kite exchange + tradingsymbol → the master row (and app symbol)."""
        self.ensure()
        key = (str(kite_exchange or "").strip().upper(), str(tradingsymbol or "").strip())
        with self.lock:
            return self.by_kite.get(key)

    def catalog_get(self):
        """Precomputed search rows: (ticker, name, whole symbol, type, description, searchable text).

        The searchable text joins ticker, name and the built description (e.g. "NIFTY 25000 CE 25-NOV-26"), so a
        multi-word option query needs no space in the exchange tradingsymbol.
        """
        with self.lock:
            if self.catalog is not None:
                return self.catalog
            catalog = []
            for r in self.rows:
                symbol_up, name_up = r["symbol"].upper(), r["name"].upper()
                description_up = r["description"].upper()
                catalog.append((symbol_up, name_up, f"{r['exchange']}:{r['symbol']}", r["type"],
                                r["description"], f"{symbol_up} {r['ticker'].upper()} {name_up} {description_up}",
                                False))
            for whole in self.continuous:
                _, _, symbol = whole.partition(":")
                catalog.append((symbol.upper(), symbol.upper(), whole, "futures",
                                f"{symbol} Continuous Future", f"{symbol.upper()} CONTINUOUS FUTURE", True))
            self.catalog = catalog
            return catalog

    def search(self, query, exchanges):
        """Up to 50 rows, ranked symbol-prefix → symbol → name/description, spread over the segments."""
        self.ensure()
        q = " ".join(query.strip().upper().split())  # normalise whitespace: "nifty  25000"
        prefix, sep, rest = q.partition(":")
        if sep and prefix in exchanges:
            q = rest.strip()
        words = q.split()
        tiers = ({}, {}, {})
        for ticker_up, name_up, whole, kind, description, searchable, continuous in self.catalog_get():
            exchange = whole.split(":", 1)[0]
            if exchange not in exchanges or (continuous and "1!" not in q):
                continue
            hit = (ticker_up, whole, kind, description)
            if q and ticker_up.startswith(q):
                tiers[0].setdefault(exchange, []).append(hit)
            elif q and (q in ticker_up or all(w in ticker_up for w in words)):
                tiers[1].setdefault(exchange, []).append(hit)
            elif q and all(w in searchable for w in words):
                # Name/description hits: "NIFTY 25000" matches "NIFTY 25000 CE 25-NOV-26".
                tiers[2].setdefault(exchange, []).append(hit)
            elif not q:
                tiers[0].setdefault(exchange, []).append(hit)
        out, seen = [], set()
        for bucket in tiers:
            for hits in bucket.values():
                hits.sort()
            segs = [bucket[e] for e in exchanges if e in bucket]
            while segs and len(out) < 50:
                for hits in list(segs):
                    while hits:
                        _, whole, kind, description = hits.pop(0)
                        if whole in seen:
                            continue
                        seen.add(whole)
                        out.append({"ticker": whole, "description": description, "type": kind})
                        break
                    if not hits:
                        segs.remove(hits)
        return out


class ZerodhaWS:
    """Minimal Kite ticker over stdlib socket: handshake, masked client frames, binary packet parsing.

    `websocket-client` would be simpler but the template is stdlib-only (PyInstaller builds one file with no
    extra wheels). The stream runs on its own thread, reconnects with backoff and resubscribes on reconnect.
    """

    def __init__(self, source):
        self.source = source
        self.lock = threading.Lock()
        self.url = ""
        self.sock = None
        self.buf = bytearray()
        self.tokens = {}  # instrument_token → mode; the whole set the app wants
        self.connected = False
        self.last_message = 0.0
        self.retries = 0
        self.stop = threading.Event()

    def log(self, message, level="info"):
        self.source.log(message, level, "ws")

    # ── frames ──
    def _send_frame(self, opcode, payload, masked=True):
        n = len(payload)
        header = bytearray([0x80 | opcode])
        flag = 0x80 if masked else 0
        if n < 126:
            header.append(flag | n)
        elif n < 65536:
            header.append(flag | 126)
            header += struct.pack(">H", n)
        else:
            header.append(flag | 127)
            header += struct.pack(">Q", n)
        if masked:
            mask = os.urandom(4)
            header += mask
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.lock:
            if self.sock is not None:
                self.sock.sendall(bytes(header) + payload)

    def _send_json(self, obj):
        self._send_frame(0x1, json.dumps(obj, separators=(",", ":")).encode("utf-8"))

    def _frames(self):
        """Yield complete (opcode, payload) frames, leaving partial ones in the buffer."""
        while True:
            b = self.buf
            if len(b) < 2:
                return
            opcode = b[0] & 0x0F
            masked = bool(b[1] & 0x80)
            n = b[1] & 0x7F
            at = 2
            if n == 126:
                if len(b) < 4:
                    return
                n = struct.unpack(">H", b[2:4])[0]
                at = 4
            elif n == 127:
                if len(b) < 10:
                    return
                n = struct.unpack(">Q", b[2:10])[0]
                at = 10
            mask = b""
            if masked:
                if len(b) < at + 4:
                    return
                mask = b[at:at + 4]
                at += 4
            if len(b) < at + n:
                return
            payload = bytes(b[at:at + n])
            if masked:
                payload = bytes(x ^ mask[i % 4] for i, x in enumerate(payload))
            del self.buf[:at + n]
            yield opcode, payload

    # ── subscriptions ──
    def update(self, modes):
        """Replace the subscribed tokens with a token → mode map ('quote' or 'full').

        The order ticket needs a real bid/ask and the DOM needs the book, so those tokens stream in Kite's
        full mode; chart-only tokens stay in quote mode. The stream carries one mode per token.
        """
        new = {int(t): m for t, m in modes.items()}
        if len(new) > MAX_TOKENS:
            self.source.log(f"watchlist/charts want {len(new)} tokens; Kite allows {MAX_TOKENS} — "
                            f"subscribing to the first {MAX_TOKENS}", "warn")
            new = dict(list(new.items())[:MAX_TOKENS])
        with self.lock:
            removed = set(self.tokens) - set(new)
            added, changed = [], []
            for token, mode in new.items():
                if token not in self.tokens:
                    added.append(token)
                elif self.tokens[token] != mode:
                    changed.append(token)
            self.tokens = new
        if removed and self.connected:
            self._send_json({"a": "unsubscribe", "v": sorted(removed)})
        for group, is_added in ((added, True), (changed, False)):
            by_mode = {}
            for token in group:
                by_mode.setdefault(new[token], []).append(token)
            for mode, tokens in by_mode.items():
                if is_added:
                    self._subscribe(sorted(tokens), mode)
                elif self.connected:
                    self._send_json({"a": "mode", "v": [mode, sorted(tokens)]})

    def _subscribe(self, tokens, mode):
        """Kite takes up to 3000 tokens per connection; batches of 200 keep each frame small."""
        if not self.connected:
            return
        for i in range(0, len(tokens), 200):
            batch = tokens[i:i + 200]
            self._send_json({"a": "subscribe", "v": batch})
            self._send_json({"a": "mode", "v": [mode, batch]})
            time.sleep(0.05)

    def _resubscribe(self):
        with self.lock:
            by_mode = {}
            for token, mode in self.tokens.items():
                by_mode.setdefault(mode, []).append(token)
        for mode, tokens in by_mode.items():
            self._subscribe(sorted(tokens), mode)

    # ── connection ──
    def run(self, generation):
        while self.source.ws_generation == generation and not self.stop.is_set():
            try:
                self._once(generation)
            except AuthError as e:
                self.log(f"enctoken refused: {e}", "warn")
                self.source.auth_failed()
                return
            except Exception as e:  # noqa: BLE001 - the stream is best effort; it retries
                self.source._perf["reconnects"] += 1
                self.log(f"stream reconnecting ({self.source.clean(str(e))})", "warn")
            self.connected = False
            if self.source.ws_generation != generation or self.stop.is_set():
                return
            delay = min(2 * (1.5 ** min(self.retries, 8)), 30)
            self.retries += 1
            if self.stop.wait(delay):
                return

    def _once(self, generation):
        url = self.source.ws_url()
        self.url = url
        parts = urllib.parse.urlsplit(url)
        secure = parts.scheme == "wss"
        host = parts.hostname or "127.0.0.1"
        port = parts.port or (443 if secure else 80)
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        raw = socket.create_connection((host, port), timeout=10)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if secure else raw
        try:
            challenge = base64.b64encode(os.urandom(16)).decode()
            sock.sendall((f"GET {target} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                          f"User-Agent: {BROWSER_UA}\r\n"
                          f"Accept-Language: {BROWSER_HEADERS['Accept-Language']}\r\n"
                          "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Key: {challenge}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = sock.recv(4096)
                if not chunk:
                    raise OSError("the stream closed during the handshake")
                head += chunk
            header, _, rest = head.partition(b"\r\n\r\n")
            status = header.split(b"\r\n", 1)[0]
            if b" 101" not in status:
                text = status.decode(errors="replace")
                if b" 403" in status or b" 401" in status:
                    raise AuthError(text)
                raise OSError(text)
            accept = base64.b64encode(hashlib.sha1(
                (challenge + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            if accept.encode() not in header:
                raise OSError("the stream's WebSocket accept does not match")
            with self.lock:
                self.sock = sock
                self.buf = bytearray(rest)
                self.connected = True
                self.last_message = time.time()
            self.retries = 0
            self.log("connected")
            self._resubscribe()
            sock.settimeout(60)
            while self.source.ws_generation == generation and not self.stop.is_set():
                chunk = sock.recv(65536)
                if not chunk:
                    raise OSError("the stream closed")
                self.buf += chunk
                for opcode, payload in self._frames():
                    self._frame(opcode, payload)
        finally:
            with self.lock:
                self.sock = None
                self.connected = False
            try:
                sock.close()
            except OSError:
                pass

    def _frame(self, opcode, payload):
        self.last_message = time.time()
        if opcode == 0x8:  # close
            raise OSError("the stream sent a close frame")
        if opcode == 0x9:  # ping → pong
            self._send_frame(0xA, payload, masked=True)
            return
        if opcode == 0xA:
            return
        if opcode == 0x1:
            try:
                data = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return
            if data.get("type") == "error":
                text = str(data.get("data") or "")
                lower = text.lower()
                if any(x in lower for x in ("403", "401", "token", "unauthor", "forbidden")):
                    raise AuthError(text)
                self.log(self.source.clean(text), "warn")
            elif data.get("type") == "order" and data.get("data"):
                # Kite pushes order postbacks on the ticker without a subscription (phase 2).
                self.source.on_order_update(data["data"])
            return
        if opcode != 0x2 or len(payload) == 0:
            return
        if len(payload) == 1:  # Kite's one-byte heartbeat
            return
        ticks = self._parse_binary(payload)
        if ticks:
            self.source.on_ticks(ticks)

    def _parse_binary(self, data):
        if len(data) < 2:
            return []
        count = struct.unpack(">H", data[0:2])[0]
        out, at = [], 2
        for _ in range(count):
            if at + 2 > len(data):
                break
            n = struct.unpack(">H", data[at:at + 2])[0]
            at += 2
            if at + n > len(data):
                break
            packet, at = data[at:at + n], at + n
            token = struct.unpack(">I", packet[0:4])[0] if len(packet) >= 4 else 0
            divisor = self.source.divisor_for(token)
            tick = self._parse_packet(packet, divisor)
            if tick:
                out.append(tick)
        return out

    @staticmethod
    def _parse_packet(packet, divisor):
        """Kite's binary layouts: 8 = LTP, 28/32 = index, 44 = quote, 184 = full (depth)."""
        if len(packet) < 8:
            return None
        token = struct.unpack(">I", packet[0:4])[0]
        try:
            ltp = struct.unpack(">i", packet[4:8])[0] / divisor
        except struct.error:
            return None
        mode = "ltp" if len(packet) == 8 else ("full" if len(packet) >= 184 else "quote")
        tick = {"instrument_token": token, "last_price": ltp, "mode": mode, "ohlc": {}}
        if len(packet) in (28, 32):
            # Index layout: token, ltp, high, low, open, close (previous close), change.
            f = struct.unpack(">7i", packet[0:28])
            tick["ohlc"] = {"open": f[4] / 100.0, "high": f[2] / 100.0,
                            "low": f[3] / 100.0, "close": f[5] / 100.0}
        if len(packet) >= 44:
            f = struct.unpack(">11i", packet[0:44])
            tick.update(volume=f[4], last_traded_quantity=f[2],
                        average_traded_price=f[3] / divisor,
                        total_buy_quantity=f[5], total_sell_quantity=f[6])
            tick["ohlc"] = {"open": f[7] / divisor, "high": f[8] / divisor,
                            "low": f[9] / divisor, "close": f[10] / divisor}
        if len(packet) >= 60:
            # Full packet: open interest at 48-52, its day high/low at 52-60, exchange time at 60-64.
            tick["oi"] = float(struct.unpack(">i", packet[48:52])[0])
            tick["oi_day_high"] = float(struct.unpack(">i", packet[52:56])[0])
            tick["oi_day_low"] = float(struct.unpack(">i", packet[56:60])[0])
        if len(packet) >= 184:
            depth = {"buy": [], "sell": []}
            for side, start in (("buy", 64), ("sell", 124)):
                for i in range(5):
                    base = start + i * 12
                    if base + 12 <= len(packet):
                        qty = struct.unpack(">I", packet[base:base + 4])[0]
                        price = struct.unpack(">I", packet[base + 4:base + 8])[0] / divisor
                        orders = struct.unpack(">H", packet[base + 8:base + 10])[0]
                        depth[side].append({"quantity": qty, "price": price, "orders": orders})
            tick["depth"] = depth
        return tick


class ZerodhaSource:
    # ── the source you replace: every method is called from an app request; keep the return shapes ──
    def __init__(self):
        self.plugin = None
        self.lock = threading.RLock()
        self.settings = {}
        self.exchanges = []
        self.enctoken = ""
        self.user_id = ""
        self.instruments = Instruments(self)
        self.quotes = {}  # whole symbol → live price cache and forming bars
        self._ticks = {}  # whole symbol → [(epoch sec, price, volume delta)] for the app's second charts
        self.subscribed = set()  # whole symbols from data.subscribe
        self.oi_subscribed = set()  # extra series tickers subscribed for their OI
        self.ws = None
        self.ws_generation = 0
        self.awaiting_token = False
        self.prompt_id = "enctoken-refresh"
        # Per-request history caps come from settings (like the OpenAlgo plugin); see _cap().
        # ── broker (phase 2) ──
        self.broker_enabled = False
        self.watched = set()  # broker.watchQuote — ticket bid/ask
        self.watched_depth = set()  # broker.watchDepth — DOM
        self.margins = None
        self.positions = []
        self.holdings = []  # API 7 broker page: Kite's delivery holdings
        self.orders = {}  # order id → the app's order dict
        self.executions = []
        self.connected_broker = False
        self.last_poll = 0.0
        self.broker_poll = None
        self.order_seq = 0
        # Company details never change in real time: cache them on disk, once per ticker, across restarts and days.
        self._profiles = self._read_profile_cache()  # ticker → {name, sector, industry, oneLine}
        self._profile_failed_until = 0.0
        self._profile_queue = queue.Queue()  # one background worker, so a watchlist never starts 30 HTTP calls
        self._profile_queued = set()
        threading.Thread(target=self._profile_worker, daemon=True, name="CompanyProfiles").start()
        self._listings = {}  # bare symbol → listing row
        self._listings_day = None
        self._listings_loading = None  # single-flight event for the two public CSVs
        self._nse = nse.Client(timeout=5)  # one session for every NSE report; never logs a token (NSE needs none)
        self.price_band_symbols = []  # whole NSE symbols from the latest price-band-hitter report
        self.nse_upcoming_sections = []  # all forthcoming calendar rows, grouped by purpose
        self.nse_today_sections = []  # today's calendar rows, grouped by purpose
        self.volume_gainers_symbols = []  # NSE volume gainers
        self.corporate_announcements_sections = []  # latest announcements, grouped by purpose
        self.newly_listed_sections = []  # 90 / 180 day listings from the cached listing CSVs
        self.holdings_event_sections = []  # our holdings that have an upcoming calendar event
        self._holdings_events_at = 0.0  # local join, refreshed hourly rather than every five minutes
        self._newly_listed_at = 0.0  # the listing CSVs are day-stamped, so once a day is enough
        self._continuous_map = {}  # continuous symbol → front contract, to spot a monthly roll
        self._security_list_day = None  # IST day of the price-band CSV already read
        self.band_2_symbols = []
        self.band_5_symbols = []
        self.band_10_symbols = []
        self.band_20_eq1 = []
        self.band_20_eq2 = []
        self.band_20_sm = []
        self.gsm_sections = []
        self._price_band_stop = threading.Event()
        self._price_band_thread = None
        self.company_details = True
        self.toast_position = "top-right"  # where this plugin's toasts ask to appear
        self.sample_toasts = False  # one sample of each level on start, for testing plugin.toast
        self._sample_toasts_sent = False
        self.script_manifest_url = ""  # legacy: full scripts.json URL; kept as an alias for plugin_data_url
        self.plugin_data_url = ""  # raw folder URL holding scripts.json and/or watchlists.json
        self.script_poll_sec = 300
        self._remote_scripts = {}  # id → validated script from the remote manifest / disk cache
        self._sent_scripts = {}  # id → script last sent with data.scripts
        self._remote_watchlists = {}  # id → validated list from the remote manifest / disk cache
        self._sent_watchlists = {}  # id → list last sent with data.watchlists
        self._script_stop = threading.Event()
        self._script_poll_thread = None
        self.quote_interval = 0  # ms between data.quote updates per symbol; 0 = every tick
        self.max_quotes_per_second = 200  # total data.quote budget; 0 = every tick (old behaviour)
        self._quote_tokens = float(self.max_quotes_per_second)
        self._quote_refill_at = time.monotonic()
        self._watch_timers = {}  # method → Timer for a pending empty-watch clear
        # Live performance counters, logged every 30 s by _perf_loop (started in configure()).
        self._perf = {"ticks": 0, "quotes": 0, "bquotes": 0, "depth": 0,
                      "batches": 0, "skipped": 0, "budget_skipped": 0,
                      "cached_bars": 0, "reconnects": 0}
        self._perf_thread = None
        self._events = {}  # token → (day, corporate actions)
        self._events_at = {}  # token → when the actions were last fetched, for a periodic re-check

        self._prune_caches()

    # ── cache housekeeping ──
    def _prune_caches(self):
        """Drop stale day-stamped cache files so a long-running install doesn't accumulate ~30 MB a day."""
        root = plugin_data_dir() / "cache"
        today = datetime.now(TZ_IST).date()
        removed = 0
        try:
            for path in root.glob("master-*.json"):
                parts = path.stem.split("-")
                if len(parts) < 5:
                    continue  # not a master-<version>-<day> file
                version, day_text = "-".join(parts[1:-3]), "-".join(parts[-3:])
                try:
                    day = date.fromisoformat(day_text)
                except ValueError:
                    continue
                # Same-day data only: yesterday's master is removed too, so every start fetches the current one.
                if day == today:
                    continue
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
            for path in root.glob("nse-*.json"):
                day_text = "-".join(path.stem.split("-")[-3:])
                try:
                    day = date.fromisoformat(day_text)
                except ValueError:
                    continue
                if day == today:
                    continue
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
            for path in (root / "events").glob("*-*.json"):
                parts = path.stem.split("-")
                if len(parts) < 4:
                    continue
                day_text = "-".join(parts[-3:])
                try:
                    day = date.fromisoformat(day_text)
                except ValueError:
                    continue
                if day == today:
                    continue
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        except OSError:
            pass
        if removed:
            self.log(f"cache prune: removed {removed} stale file(s)")

    def _perf_loop(self):
        """One [perf] line every 30 s: enough to judge a large watchlist without logging individual ticks."""
        while True:
            time.sleep(30)
            with self.lock:
                counts = dict(self._perf)
                for key in self._perf:
                    self._perf[key] = 0
                cached = len(self.quotes)
                ws = self.ws
                tokens = len(ws.tokens) if ws is not None else 0
                full = sum(1 for mode in ws.tokens.values() if mode == "full") if ws is not None else 0
            self.log(f"[perf] ticks={counts['ticks']} data.quote={counts['quotes']} "
                     f"broker.quote={counts['bquotes']} depth={counts['depth']} "
                     f"batches={counts['batches']} throttle_skipped={counts['skipped']} "
                     f"budget_skipped={counts['budget_skipped']} cached_bars={counts['cached_bars']} "
                     f"quotes_cached={cached} tokens={tokens}(full {full}) "
                     f"reconnects={counts['reconnects']}")

    # ── settings and logging ──
    @staticmethod
    def clean_token(value):
        """A pasted enctoken may arrive with surrounding quotes; strip whitespace and one matching pair."""
        text = str(value or "").strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
            text = text[1:-1].strip()
        return text

    def clean(self, text):
        """A message with the enctoken removed (nothing secret reaches the log)."""
        s = str(text)
        return s.replace(self.enctoken, "…") if self.enctoken and self.enctoken in s else s

    def _log_enabled(self, category):
        key = {"api": "logApi", "ws": "logWs", "debug": "logDebug", "nse": "logNse"}.get(category, "logApi")
        default = "false" if category == "debug" else "true"
        return str(self.settings.get(key, default)).strip().lower() == "true"

    def _log_path(self, category):
        return plugin_data_dir() / {"ws": "zerodha-ws.log", "nse": "zerodha-nse.log"}.get(
            category, "zerodha-api.log")

    def _write_log(self, category, line):
        """Append to a per-category file with a rolling ~1 MB cap. Logging must never break the plugin."""
        try:
            path = self._log_path(category)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if path.exists() and path.stat().st_size > 1024 * 1024:
                    data = path.read_bytes()
                    path.write_bytes(data[-512 * 1024:])
            except OSError:
                pass
            with path.open("a", encoding="utf-8") as f:
                f.write(line.rstrip("\n") + "\n")
        except OSError:
            pass

    def log(self, message, level="info", category="api"):
        """One line to the app's plugin log, stdout when run by hand, and to a file for live debugging."""
        text = self.clean(message)
        p = self.plugin
        if p and p.alive:
            p.notify("log", {"level": level, "message": f"[{category}] {text}"})
        else:
            print(text, flush=True)
        if self._log_enabled(category):
            stamp = datetime.now(TZ_IST).strftime("%Y-%m-%d %H:%M:%S")
            self._write_log(category, f"{stamp} [{level}] [{category}] {text}")

    def notice(self, level, message):
        """A line in the account manager's Notifications log (broker plugins only)."""
        p = self.plugin
        if p and p.alive:
            p.notify("broker.notice", {"level": level, "message": self.clean(message)})

    def toast(self, level, message, sound=False):
        """A short, non-blocking message on the user's charts (Plugin API 8), top-right by default."""
        p = self.plugin
        if p and p.alive:
            p.notify("plugin.toast", {"level": level, "message": self.clean(message),
                                      "sound": bool(sound), "position": self.toast_position})

    @staticmethod
    def _scripts_dir():
        base = getattr(sys, "_MEIPASS", None)  # PyInstaller one-file extraction dir
        return Path(base) / "scripts" if base else Path(__file__).resolve().parent / "scripts"

    @classmethod
    def plugin_scripts(cls):
        """The OakScripts shipped with this plugin, sent whole with data.scripts."""
        names = {
            "Monthly_Levels": "Monthly Levels (Expiry H/L)",
            "MA_Cloud": "MA Cloud",
            "EMA_Cross_Strategy": "EMA Cross Strategy",
            "CustomOne_5Values": "CustomOne 5 values",
        }
        out = []
        for path in sorted(cls._scripts_dir().glob("*.js")):
            try:
                source = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not source or len(source) > 512 * 1024:
                continue
            kind = "strategy" if re.search(r"\bstrategy\s*\(", source) else "indicator"
            out.append({"id": path.stem, "name": names.get(path.stem, path.stem),
                        "kind": kind, "source": source})
        return out[:50]

    @staticmethod
    def _valid_script(raw):
        """One protocol-valid script, or None. Remote data is untrusted, so validate every field."""
        if not isinstance(raw, dict):
            return None
        sid, source = raw.get("id"), raw.get("source")
        if not isinstance(sid, str) or not SCRIPT_ID_RE.match(sid):
            return None
        if not isinstance(source, str) or not source.strip():
            return None
        if len(source.encode("utf-8")) > MAX_SCRIPT_SOURCE:
            return None
        name = raw.get("name") if isinstance(raw.get("name"), str) and raw["name"].strip() else sid
        kind = raw.get("kind") if raw.get("kind") in ("indicator", "strategy") else "indicator"
        return {"id": sid, "name": name, "kind": kind, "source": source}

    @staticmethod
    def _remote_cache_path():
        return plugin_data_dir() / "cache" / "remote-scripts.json"

    @staticmethod
    def _read_remote_cache():
        try:
            with open(ZerodhaSource._remote_cache_path(), encoding="utf-8") as f:
                data = json.load(f)
            rows = data.get("scripts") if isinstance(data, dict) else data
            if not isinstance(rows, list):
                return {}
        except (OSError, ValueError):
            return {}
        out = {}
        for raw in rows:
            script = ZerodhaSource._valid_script(raw)
            if script:
                out[script["id"]] = script
        return out

    def _write_remote_cache(self):
        path = self._remote_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"schema": 1, "scripts": list(self._remote_scripts.values())}, f)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as e:
            self.log(f"remote script cache not saved: {e}", "warn")

    @staticmethod
    def _valid_watchlist(raw):
        """One protocol-valid watchlist, or None. Remote data is untrusted."""
        if not isinstance(raw, dict):
            return None
        sid, name = raw.get("id"), raw.get("name")
        if not isinstance(sid, str) or not 0 < len(sid) <= 64:
            return None
        if not isinstance(name, str) or not name.strip():
            name = sid
        symbols = [s for s in (raw.get("symbols") or []) if isinstance(s, str) and s.strip()]
        sections = []
        for sec in (raw.get("sections") or []):
            if not isinstance(sec, dict) or not isinstance(sec.get("name"), str):
                continue
            rows = [s for s in (sec.get("symbols") or []) if isinstance(s, str) and s.strip()]
            sections.append({"name": sec["name"], "symbols": rows})
        if len(symbols) + sum(len(s["symbols"]) for s in sections) > 1000:
            return None
        return {"id": sid, "name": name, "symbols": symbols, "sections": sections}

    @staticmethod
    def _watchlist_cache_path():
        return plugin_data_dir() / "cache" / "remote-watchlists.json"

    @staticmethod
    def _read_watchlist_cache():
        try:
            with open(ZerodhaSource._watchlist_cache_path(), encoding="utf-8") as f:
                data = json.load(f)
            rows = data.get("watchlists") if isinstance(data, dict) else data
            if not isinstance(rows, list):
                return {}
        except (OSError, ValueError):
            return {}
        out = {}
        for raw in rows:
            item = ZerodhaSource._valid_watchlist(raw)
            if item:
                out[item["id"]] = item
        return out

    def _write_watchlist_cache(self):
        path = self._watchlist_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"schema": 1, "watchlists": list(self._remote_watchlists.values())}, f)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as e:
            self.log(f"remote watchlist cache not saved: {e}", "warn")

    def _fetch_remote_watchlists(self):
        """Validated id → list map. An absent watchlists.json means no remote lists."""
        rows = self._fetch_manifest("watchlists.json", "watchlists")
        if rows is None:
            return {}
        out = {}
        for raw in rows:
            item = self._valid_watchlist(raw)
            if item:
                out[item["id"]] = item
            else:
                self.log("a remote watchlist was dropped: invalid id or contents", "warn")
        return out

    def _data_url(self):
        """The raw folder URL holding scripts.json / watchlists.json, from either setting."""
        base = (self.plugin_data_url or self.script_manifest_url or "").strip()
        if not base:
            return ""
        parts = urllib.parse.urlsplit(base)
        path = parts.path
        if path.endswith(("/scripts.json", "/watchlists.json", "scripts.json", "watchlists.json")):
            path = path.rsplit("/", 1)[0] + "/"
        elif not path.endswith("/"):
            path += "/"
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))

    def _file_url(self, name):
        base = self._data_url()
        if not base:
            return ""
        parts = urllib.parse.urlsplit(base)
        path = parts.path.rstrip("/") + "/" + name
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))

    def _fetch_manifest(self, name, key):
        """Fetch one optional file from the base URL. None means the file isn't published."""
        url = self._file_url(name)
        if not url:
            return None
        data = self._external_json(url, timeout=10)
        if data is None:
            return None
        rows = data.get(key) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ValueError(f"the remote {name} has no {key} list")
        return rows

    def _fetch_remote_scripts(self):
        """Validated id → script map. An absent scripts.json means no remote scripts."""
        rows = self._fetch_manifest("scripts.json", "scripts")
        if rows is None:
            return {}
        out = {}
        for raw in rows:
            script = self._valid_script(raw)
            if script:
                out[script["id"]] = script
            else:
                self.log("a remote script was dropped: invalid id or source", "warn")
        return out

    def merged_scripts(self):
        """Local scripts first; a remote script with the same id wins."""
        merged = {s["id"]: s for s in self.plugin_scripts()}
        merged.update(self._remote_scripts)
        return list(merged.values())[:MAX_SCRIPTS]

    def send_scripts(self):
        """Send the whole set only when it changed; toast on a remote change."""
        scripts = self.merged_scripts()
        by_id = {s["id"]: s for s in scripts}
        with self.lock:
            if by_id == self._sent_scripts:
                return False
            previous = self._sent_scripts
            new = [s["name"] for sid, s in by_id.items() if sid not in previous]
            self._sent_scripts = by_id
        p = self.plugin
        if p and p.alive:
            p.notify("data.scripts", {"scripts": scripts})
            if new:
                self.toast("info", "New plugin indicators: " + ", ".join(new[:4]))
            elif previous and previous != by_id:
                self.toast("info", "Plugin indicators updated from the remote manifest")
        return True

    def merged_watchlists(self):
        """Local sample lists first; a remote list with the same id wins."""
        merged = {w["id"]: w for w in self.watchlists()}
        merged.update(self._remote_watchlists)
        return list(merged.values())[:100]

    def send_watchlists(self):
        """Send the whole set only when it changed; toast on a remote change."""
        lists = self.merged_watchlists()
        by_id = {w["id"]: w for w in lists}
        with self.lock:
            if by_id == self._sent_watchlists:
                return False
            previous = self._sent_watchlists
            new = [w["name"] for wid, w in by_id.items() if wid not in previous]
            self._sent_watchlists = by_id
        p = self.plugin
        if p and p.alive:
            p.notify("data.watchlists", {"watchlists": lists})
            if new:
                self.toast("info", "New plugin watchlists: " + ", ".join(new[:4]))
            elif previous and previous != by_id:
                self.toast("info", "Plugin watchlists updated from the remote manifest")
        return True

    @staticmethod
    def _resolve_nse_map(names, rows):
        """Bare NSE tickers → clean app symbols from our master.

        Kite stores many cash symbols as NAME-SM / NAME-BE / NAME-ST while NSE's reports use bare names;
        match exact first, then the ticker with its last series suffix removed.
        """
        exact = {r["ticker"].upper(): r for r in rows if r["exchange"] == "NSE"}
        prefixed = {}
        for r in rows:
            if r["exchange"] == "NSE" and "-" in r["ticker"]:
                prefixed.setdefault(r["ticker"].upper().rsplit("-", 1)[0], r)
        out = {}
        for name in names:
            row = exact.get(name) or prefixed.get(name)
            if row:
                out[name] = f"NSE:{row['symbol']}"
        return out

    def _resolve_nse_symbols(self, names, rows):
        """Bare tickers → clean symbols, with the number that could not be resolved."""
        found = self._resolve_nse_map(names, rows)
        return [found[n] for n in names if n in found][:1000], len(names) - len(found)

    def _refresh_price_band(self):
        """Read NSE's live price-band-hitter report and keep its tickers for the watchlist."""
        started = time.monotonic()
        url = nse.REPORTS.get("price_band_hitters", "")
        text = self._nse.get("price_band_hitters", timeout=8)
        status = self._nse.session.last_status
        elapsed = int((time.monotonic() - started) * 1000)
        if not text:
            self.log(f"Price Band Hitters: check FAILED — no body from {url} "
                     f"(HTTP {status or 'no response'}, {elapsed}ms)", "warn", "nse")
            return False
        hitters = nse.parse_price_band_hitters(text)
        if not hitters:
            self.log(f"Price Band Hitters: check FAILED — no tickers parsed from {url} "
                     f"(HTTP {status}, {elapsed}ms)", "warn", "nse")
            return False
        rows = self.instruments.rows
        if not rows:
            self.log(f"Price Band Hitters: {len(hitters)} tickers from NSE, instrument master not loaded yet "
                     f"— validation deferred (HTTP {status}, {elapsed}ms)", category="nse")
            return False
        symbols, skipped = self._resolve_nse_symbols(hitters, rows)
        if not symbols:
            self.log(f"Price Band Hitters: check FAILED — none of the {len(hitters)} tickers is an NSE symbol "
                     f"in our instrument master (HTTP {status}, {elapsed}ms)", "warn", "nse")
            return False
        with self.lock:
            changed = symbols != self.price_band_symbols
            if changed:
                self.price_band_symbols = symbols
        state = "CHANGED" if changed else "unchanged"
        example = ", ".join(symbols[:5])
        self.log(f"Price Band Hitters: {len(symbols)} validated symbols, {state} "
                 f"({skipped} skipped — not in our NSE master; "
                 f"HTTP {status}, {elapsed}ms; e.g. {example})", category="nse")
        return changed

    def _refresh_nse_calendars(self):
        """Two watchlists from NSE's event calendar: all forthcoming, and today's, equities + SME."""
        rows = self.instruments.rows
        if not rows:
            self.log("NSE event calendar: instrument master not loaded yet — validation deferred", category="nse")
            return False
        day = datetime.now(TZ_IST).strftime("%d-%m-%Y")
        groups = (
            ("NSE all Forthcomming", "nse_upcoming_sections",
             (nse.event_calendar_url(), nse.event_calendar_url(index="sme"))),
            ("NSE Today Events", "nse_today_sections",
             (nse.event_calendar_url(index="equities", from_date=day, to_date=day),
              nse.event_calendar_url(index="sme", from_date=day, to_date=day))),
        )
        changed_any = False
        for label, field, urls in groups:
            events = []
            seen = set()
            for url in urls:
                started = time.monotonic()
                text = self._nse.get("event_calendar", url=url, timeout=8)
                status = self._nse.session.last_status
                elapsed = int((time.monotonic() - started) * 1000)
                if not text:
                    self.log(f"{label}: check FAILED — no body from {url} "
                             f"(HTTP {status or 'no response'}, {elapsed}ms)", "warn", "nse")
                    continue
                for event in nse.event_calendar_rows(text):
                    key = (event["symbol"], event["purpose"])
                    if key not in seen:
                        seen.add(key)
                        events.append(event)
            if not events:
                continue
            found = self._resolve_nse_map({e["symbol"] for e in events}, rows)
            grouped = {}
            for event in events:
                symbol = found.get(event["symbol"])
                if not symbol:
                    continue
                names = grouped.setdefault(event["purpose"], [])
                if symbol not in names:
                    names.append(symbol)
            sections = [{"name": purpose, "symbols": sorted(names)[:1000]}
                        for purpose, names in sorted(grouped.items())]
            sections = sections[:1000]
            total = sum(len(s["symbols"]) for s in sections)
            skipped = sum(1 for e in events if e["symbol"] not in found)
            with self.lock:
                changed = sections != getattr(self, field)
                if changed:
                    setattr(self, field, sections)
            changed_any = changed or changed_any
            self.log(f"{label}: {total} validated symbols in {len(sections)} purposes, "
                     f"{'CHANGED' if changed else 'unchanged'} "
                     f"({skipped} skipped — not in our NSE master; "
                     f"e.g. {', '.join(sorted(found.values())[:5])})", category="nse")
        return changed_any

    def _refresh_volume_gainers(self):
        """NSE's volume-gainers list, validated against our master."""
        started = time.monotonic()
        text = self._nse.get("volume_gainers", timeout=8)
        status, elapsed = self._nse.session.last_status, int((time.monotonic() - started) * 1000)
        if not text:
            self.log(f"NSE Volume Gainers: check FAILED — no body "
                     f"(HTTP {status or 'no response'}, {elapsed}ms)", "warn", "nse")
            return False
        rows = self.instruments.rows
        if not rows:
            self.log("NSE Volume Gainers: instrument master not loaded yet — validation deferred", category="nse")
            return False
        symbols, skipped = self._resolve_nse_symbols(nse.parse_volume_gainers(text), rows)
        with self.lock:
            changed = symbols != self.volume_gainers_symbols
            if changed:
                self.volume_gainers_symbols = symbols
        self.log(f"NSE Volume Gainers: {len(symbols)} validated symbols, "
                 f"{'CHANGED' if changed else 'unchanged'} ({skipped} skipped; HTTP {status}, {elapsed}ms)"
                 f"{'; e.g. ' + ', '.join(symbols[:5]) if symbols else ''}", category="nse")
        return changed

    def _refresh_corporate_announcements(self):
        """Latest NSE announcements (equities + SME), grouped by the announcement's purpose."""
        events, seen = [], set()
        for index in ("equities", "sme"):
            url = nse.corporate_announcements_url(index)
            started = time.monotonic()
            text = self._nse.get("corporate_announcements", url=url, timeout=8)
            status, elapsed = self._nse.session.last_status, int((time.monotonic() - started) * 1000)
            if not text:
                self.log(f"NSE Corporate Announcements: check FAILED for {index} — no body "
                         f"(HTTP {status or 'no response'}, {elapsed}ms)", "warn", "nse")
                continue
            for event in nse.parse_corporate_announcements(text):
                key = (event["symbol"], event["purpose"], event["date"])
                if key not in seen:
                    seen.add(key)
                    events.append(event)
        rows = self.instruments.rows
        if not rows or not events:
            return False
        found = self._resolve_nse_map({e["symbol"] for e in events}, rows)
        grouped = {}
        for event in events:
            symbol = found.get(event["symbol"])
            if not symbol:
                continue
            names = grouped.setdefault(event["purpose"], [])
            if symbol not in names:
                names.append(symbol)
        sections = [{"name": purpose, "symbols": sorted(names)[:1000]}
                    for purpose, names in sorted(grouped.items())][:1000]
        with self.lock:
            changed = sections != self.corporate_announcements_sections
            if changed:
                self.corporate_announcements_sections = sections
        total = sum(len(s["symbols"]) for s in sections)
        skipped = sum(1 for e in events if e["symbol"] not in found)
        self.log(f"NSE Corporate Announcements: {total} validated symbols in {len(sections)} purposes, "
                 f"{'CHANGED' if changed else 'unchanged'} ({skipped} skipped)", category="nse")
        return changed

    def _refresh_newly_listed(self):
        """Symbols first listed in the last 90 / 180 days, from today's cached listing CSVs."""
        now_mono = time.monotonic()
        if now_mono - self._newly_listed_at < 86400:
            return False
        day = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        path = plugin_data_dir() / "cache" / f"nse-listings-{day}.json"
        try:
            with open(path, encoding="utf-8") as handle:
                listings = json.load(handle)
        except (OSError, ValueError):
            return False
        rows = self.instruments.rows
        if not rows or not listings:
            return False
        self._newly_listed_at = now_mono
        now = time.time()
        sections = []
        for label, days in (("Last 90 days", 90), ("Last 180 days", 180)):
            names = sorted(k for k, v in listings.items()
                           if v.get("date") and now - v["date"] <= days * 86400)
            symbols, _ = self._resolve_nse_symbols(names, rows)
            if symbols:
                sections.append({"name": label, "symbols": symbols})
        with self.lock:
            changed = sections != self.newly_listed_sections
            if changed:
                self.newly_listed_sections = sections
        self.log(f"NSE Newly Listed: {sum(len(s['symbols']) for s in sections)} symbols in "
                 f"{len(sections)} windows, {'CHANGED' if changed else 'unchanged'}", category="nse")
        return changed

    def _refresh_holdings_events(self):
        """Our holdings that appear in the upcoming event calendar, grouped by purpose (local join)."""
        now_mono = time.monotonic()
        if now_mono - self._holdings_events_at < 3600:
            return False
        self._holdings_events_at = now_mono
        rows = self.instruments.rows
        if not rows:
            return False
        held = set()
        with self.lock:
            holdings = list(self.holdings)
        for holding in holdings:
            symbol = holding.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                continue
            row = self.instruments.row(symbol)
            exchange, _, ticker = symbol.partition(":")
            held.add(f"{exchange}:{row['symbol'] if row else ticker}".upper())
        sections = []
        for section in self.nse_upcoming_sections:
            symbols = [s for s in section["symbols"] if s.upper() in held]
            if symbols:
                sections.append({"name": section["name"], "symbols": symbols})
        with self.lock:
            changed = sections != self.holdings_event_sections
            if changed:
                self.holdings_event_sections = sections
        self.log(f"My Holdings upcoming events: {sum(len(s['symbols']) for s in sections)} in "
                 f"{len(sections)} purposes, {'CHANGED' if changed else 'unchanged'}", category="nse")
        return changed

    def _refresh_security_lists(self):
        """NSE's dated security list describes the NEXT day, so read the previous trading day's file."""
        today = datetime.now(TZ_IST).date()
        stamp = today.strftime("%Y-%m-%d")
        if self._security_list_day == stamp:
            return False
        day = nse.previous_trading_day(today)
        text = ""
        for _ in range(7):
            url = nse.security_list_url(day)
            started = time.monotonic()
            text = self._nse.get("security_list", url=url, timeout=8)
            status, elapsed = self._nse.session.last_status, int((time.monotonic() - started) * 1000)
            if text:
                self.log(f"NSE price bands: read {day:%d-%m-%Y} list (HTTP {status}, {elapsed}ms)",
                         category="nse")
                break
            self.log(f"NSE price bands: no list for {day:%d-%m-%Y} (HTTP {status or 'no response'}) "
                     f"— trying an earlier trading day", "warn", "nse")
            day = nse.previous_trading_day(day)
        if not text:
            return False
        master = self.instruments.rows
        if not master:
            self.log("NSE price bands: instrument master not loaded yet — validation deferred", category="nse")
            return False
        parsed = nse.parse_security_list(text)
        bands, gsm = {"2": [], "5": [], "10": [], "20": []}, []
        for row in parsed:
            if "GSM" in row["remarks"].upper():
                gsm.append(row)
            if row["band"] in bands:
                bands[row["band"]].append(row)

        plain = lambda band: self._resolve_nse_symbols([r["symbol"] for r in bands[band]], master)[0][:1000]
        band2, band5, band10 = plain("2"), plain("5"), plain("10")
        # Ordinary EQ is the only class above 1000; split it alphabetically into two halves.
        eq_names = sorted({r["symbol"] for r in bands["20"] if r["series"] == "EQ"})
        eq_map = self._resolve_nse_map(eq_names, master)
        eq_all = [eq_map[n] for n in eq_names if n in eq_map]
        middle = (len(eq_all) + 1) // 2
        eq1, eq2 = eq_all[:middle], eq_all[middle:]
        sm_names = sorted({r["symbol"] for r in bands["20"] if r["series"] == "SM"})
        eq_sm = self._resolve_nse_symbols(sm_names, master)[0][:1000]

        gsm_map = self._resolve_nse_map({r["symbol"] for r in gsm}, master)
        stages = {}
        for row in gsm:
            symbol = gsm_map.get(row["symbol"])
            if not symbol:
                continue
            names = stages.setdefault(row["remarks"] or "GSM", [])
            if symbol not in names:
                names.append(symbol)
        gsm_sections = [{"name": stage, "symbols": sorted(names)[:1000]}
                        for stage, names in sorted(stages.items())]

        with self.lock:
            changed = (band2 != self.band_2_symbols or band5 != self.band_5_symbols
                       or band10 != self.band_10_symbols or eq1 != self.band_20_eq1
                       or eq2 != self.band_20_eq2 or eq_sm != self.band_20_sm
                       or gsm_sections != self.gsm_sections)
            if changed:
                self.band_2_symbols, self.band_5_symbols, self.band_10_symbols = band2, band5, band10
                self.band_20_eq1, self.band_20_eq2 = eq1, eq2
                self.band_20_sm, self.gsm_sections = eq_sm, gsm_sections
            self._security_list_day = stamp
        self.log(f"NSE price bands {day:%d-%m-%Y}: 2%={len(band2)} 5%={len(band5)} 10%={len(band10)} "
                 f"20% EQ={len(eq_all)} in 2 lists, SM={len(eq_sm)}; "
                 f"GSM={sum(len(s['symbols']) for s in gsm_sections)} in {len(gsm_sections)} stages, "
                 f"{'CHANGED' if changed else 'unchanged'}", category="nse")
        return changed

    def _price_band_loop(self):
        """Refresh the NSE lists on start and every five minutes; send watchlists only when one changed."""
        while not self._price_band_stop.is_set():
            try:
                changed = self._refresh_price_band()
                changed = self._refresh_nse_calendars() or changed
                changed = self._refresh_volume_gainers() or changed
                changed = self._refresh_corporate_announcements() or changed
                changed = self._refresh_newly_listed() or changed
                changed = self._refresh_security_lists() or changed
                changed = self._refresh_holdings_events() or changed
                if changed:
                    self.push_watchlists()
            except Exception as e:  # noqa: BLE001 - an NSE failure must never break the plugin
                self.log(f"NSE reports not read: {self.clean(str(e))}", "warn", "nse")
            if self._price_band_stop.wait(PRICE_BAND_POLL_SEC):
                break

    def _script_poll_loop(self):
        """Check both optional manifest files at one interval; no changes means no notification."""
        while not self._script_stop.is_set():
            base = self._data_url()
            if base:
                try:
                    fetched = self._fetch_remote_scripts()
                    with self.lock:
                        changed = fetched != self._remote_scripts
                        self._remote_scripts = fetched
                    if changed:
                        self._write_remote_cache()
                        self.log(f"remote indicators: {len(fetched)} from {self._file_url('scripts.json')}")
                        self.send_scripts()
                except Exception as e:  # noqa: BLE001 - a bad poll must never break the plugin
                    self.log(f"remote indicators not read: {self.clean(str(e))}", "warn")
                try:
                    fetched = self._fetch_remote_watchlists()
                    with self.lock:
                        changed = fetched != self._remote_watchlists
                        self._remote_watchlists = fetched
                    if changed:
                        self._write_watchlist_cache()
                        self.log(f"remote watchlists: {len(fetched)} from {self._file_url('watchlists.json')}")
                        self.send_watchlists()
                except Exception as e:  # noqa: BLE001
                    self.log(f"remote watchlists not read: {self.clean(str(e))}", "warn")
            self._script_stop.wait(self.script_poll_sec)

    def configure(self, settings, provides=None):
        with self.lock:
            changed = (settings.get("enctoken") != self.enctoken
                       or settings.get("userId") != self.user_id
                       or settings.get("apiUrl") != self.settings.get("apiUrl")
                       or settings.get("wsUrl") != self.settings.get("wsUrl"))
            self.settings = dict(settings)
            self.enctoken = self.clean_token(settings.get("enctoken"))
            self.user_id = str(settings.get("userId") or "").strip()
            self.exchanges = [e.strip().upper() for e in
                              (settings.get("exchanges") or DEFAULT_EXCHANGES).split(",") if e.strip()]
            self.broker_enabled = "broker" in provides if provides is not None else self.broker_enabled
            self.company_details = str(settings.get("companyDetails", "true")).strip().lower() == "true"
            position = str(settings.get("toastPosition", "top-right") or "").strip().lower()
            self.toast_position = position if position in TOAST_POSITIONS else "top-right"
            self.sample_toasts = str(settings.get("sampleToasts", "false")).strip().lower() == "true"
            self.script_manifest_url = str(settings.get("scriptManifestUrl") or "").strip()
            self.plugin_data_url = str(settings.get("pluginDataUrl") or "").strip()
            try:
                self.script_poll_sec = max(60, int(float(settings.get("scriptPollSec", 300) or 300)))
            except (TypeError, ValueError):
                self.script_poll_sec = 300
            try:
                self.quote_interval = max(0, int(float(settings.get("quoteIntervalMs", 0) or 0)))
            except (TypeError, ValueError):
                self.quote_interval = 0
            try:
                self.max_quotes_per_second = max(0, int(float(settings.get("maxQuotesPerSecond", 200) or 0)))
            except (TypeError, ValueError):
                self.max_quotes_per_second = 200
            self._quote_tokens = float(self.max_quotes_per_second)
            self._quote_refill_at = time.monotonic()
            self.awaiting_token = False
            gen = self.ws_generation + 1
        self.log(f"configure: enctoken={'set' if self.enctoken else 'empty'}, "
                 f"user={self.user_id or 'auto'}, exchanges={','.join(self.exchanges)}, "
                 f"capabilities={','.join(provides) if provides is not None else 'unchanged'}, "
                 f"broker={'on' if self.broker_enabled else 'off'}")
        with self.lock:
            self._remote_scripts = self._read_remote_cache() if self._data_url() else {}
            self._remote_watchlists = self._read_watchlist_cache() if self._data_url() else {}
        self.send_scripts()
        self.send_watchlists()
        if self._data_url() and (self._script_poll_thread is None or not self._script_poll_thread.is_alive()):
            self._script_stop.set()
            self._script_stop = threading.Event()
            self._script_poll_thread = threading.Thread(target=self._script_poll_loop, daemon=True,
                                                        name="RemoteScripts")
            self._script_poll_thread.start()
        if self._price_band_thread is None or not self._price_band_thread.is_alive():
            self._price_band_stop = threading.Event()
            self._price_band_thread = threading.Thread(target=self._price_band_loop, daemon=True,
                                                      name="PriceBandHitters")
            self._price_band_thread.start()
        if self.sample_toasts and not self._sample_toasts_sent:
            self._sample_toasts_sent = True
            self.toast("info", "Sample info toast from the Zerodha plugin")
            self.toast("warn", "Sample warning toast from the Zerodha plugin")
            self.toast("error", "Sample error toast from the Zerodha plugin")

        def load_master():
            day = datetime.now(TZ_IST).strftime("%Y-%m-%d")
            cached = self.instruments.day == day or self.instruments._existing_path(day).exists()
            if not cached:
                self.toast("info", "Downloading instrument master — first start can take a moment")
            previous = dict(self.instruments.continuous)
            self.instruments.ensure()
            current = dict(self.instruments.continuous)
            rolled = [symbol for symbol, contract in current.items()
                      if symbol in previous and previous[symbol] != contract]
            if rolled and self.plugin is not None and self.plugin.alive:
                self.plugin.notify("data.symbolInfoChanged", {"tickers": rolled})
                self.log(f"Continuous futures rolled: {', '.join(rolled[:8])}"
                         + (f" (+{len(rolled) - 8} more)" if len(rolled) > 8 else ""))
            self._continuous_map = current
            self._load_listings()  # warm both caches before the app's symbolInfo burst arrives
            self._refresh_price_band()  # the watchlists are ready with the app's first symbolInfo burst
            self._refresh_nse_calendars()
            self._refresh_volume_gainers()
            self._refresh_corporate_announcements()
            self._refresh_newly_listed()
            self._refresh_security_lists()
            self._refresh_holdings_events()
            if self.plugin is not None and self.plugin.alive:
                self.toast("info", f"Pre-data ready — {len(self.instruments.rows)} instruments and NSE listings cached. "
                                   f"You can use the app.")
                self.plugin.notify("data.symbolInfoChanged", {})
                self.plugin.push_watchlists()

        threading.Thread(target=load_master, daemon=True).start()
        if changed or self.ws is None:
            self._start_ws(gen)
        if self.broker_enabled and self.broker_poll is None:
            self.broker_poll = threading.Thread(target=self._poll_loop, daemon=True, name="ZerodhaPoll")
            self.broker_poll.start()
        if self._perf_thread is None:
            self._perf_thread = threading.Thread(target=self._perf_loop, daemon=True, name="ZerodhaPerf")
            self._perf_thread.start()

    def auth_failed(self):
        """A 403 on the stream: ask for a fresh enctoken instead of leaving the feed dead until a restart."""
        with self.lock:
            if self.awaiting_token:
                return
            self.awaiting_token = True
        self.log("The enctoken was refused — ask for a fresh one from kite.zerodha.com", "warn")
        self.notice("error", "Session expired — enter a fresh enctoken to restore data and trading")
        p = self.plugin
        if p and p.alive:
            p.notify("plugin.prompt", {
                "id": self.prompt_id, "title": "Zerodha enctoken expired",
                "message": "Log in at kite.zerodha.com, copy the enctoken cookie, and paste it here.",
                "input": "password", "image": None,
            })

    def on_prompt_answer(self, prompt_id, value):
        if prompt_id != self.prompt_id:
            return
        with self.lock:
            if not self.awaiting_token:
                return
            if not value:
                self.awaiting_token = False
                self.log("Enctoken refresh cancelled; the stream stays stopped", "warn")
                return
            self.enctoken = self.clean_token(value)
            self.settings["enctoken"] = self.enctoken
            self.awaiting_token = False
            gen = self.ws_generation + 1
        self.log("Enctoken refreshed; reconnecting")
        self._start_ws(gen)
        threading.Thread(target=self._after_token_refresh, daemon=True, name="TokenRecovery").start()

    def _after_token_refresh(self):
        """A fresh token can answer symbols refused earlier; make the app ask them again."""
        try:
            self.instruments.ensure()
            self._load_listings()
        except Exception as e:  # noqa: BLE001 - a slow source must not block the notification
            self.log(f"Reference data after token refresh not ready: {e}", "warn")
        p = self.plugin
        if p and p.alive:
            p.notify("data.symbolInfoChanged", {})
            p.push_watchlists()
            self.log("Enctoken refreshed: symbol information reissued to the app")

    # ── REST ──
    def _base(self):
        return str(self.settings.get("apiUrl", DEFAULT_API_URL)).rstrip("/")

    def _master_url(self):
        return str(self.settings.get("masterUrl", DEFAULT_MASTER_URL))

    def fetch_master(self):
        """Kite's public instrument CSV — one GET, no auth, optionally gzip-encoded."""
        url = urllib.parse.urlsplit(self._master_url())
        secure = url.scheme == "https"
        host = url.hostname or "api.kite.trade"
        port = url.port or (443 if secure else 80)
        target = url.path or "/instruments"
        if url.query:
            target += "?" + url.query
        conn = (http.client.HTTPSConnection(host, port, timeout=30) if secure
                else http.client.HTTPConnection(host, port, timeout=30))
        try:
            headers = dict(BROWSER_HEADERS)
            headers.update({"Accept": "text/csv,*/*", "Accept-Encoding": "gzip"})
            conn.request("GET", target, headers=headers)
            r = conn.getresponse()
            raw = r.read()
            if r.status != 200:
                raise PluginError(f"Instrument master answered HTTP {r.status}")
            if (r.getheader("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException, PluginError) as e:
            raise PluginError(f"Cannot download the instrument master ({self.clean(str(e))})") from None
        finally:
            conn.close()

    def _rest(self, path, method="GET", body=None, timeout=10):
        """One call on Kite's personal /oms API with the enctoken header; (status, body bytes) or PluginError."""
        if not self.enctoken:
            raise PluginError("The Zerodha plugin has no enctoken yet — fill it in Settings > Plugins")
        base = urllib.parse.urlsplit(self._base())
        secure = base.scheme == "https"
        host = base.hostname or "kite.zerodha.com"
        port = base.port or (443 if secure else 80)
        conn = (http.client.HTTPSConnection(host, port, timeout=timeout) if secure
                else http.client.HTTPConnection(host, port, timeout=timeout))
        try:
            headers = dict(BROWSER_HEADERS)
            headers.update({
                "Authorization": f"enctoken {self.enctoken}",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip",  # handled below; no brotli/zstd so the body stays readable
                "X-Kite-Version": KITE_VERSION,
            })
            if self.user_id:
                headers["X-Kite-Userid"] = self.user_id
            payload = None
            if body is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                payload = urllib.parse.urlencode({k: str(v) for k, v in body.items()})
            conn.request(method, path, body=payload, headers=headers)
            r = conn.getresponse()
            raw = r.read()
            if (r.getheader("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return r.status, raw
        except (OSError, http.client.HTTPException) as e:
            raise PluginError(f"Zerodha is not reachable ({self.clean(str(e))})") from None
        finally:
            conn.close()

    def _json(self, path, method="GET", body=None, timeout=10):
        status, raw = self._rest(path, method, body, timeout)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PluginError(f"Zerodha answered HTTP {status} with no JSON") from None
        if status in (401, 403) or data.get("error_type") == "TokenException":
            raise AuthError(str(data.get("message") or f"HTTP {status}"))
        if status == 429:
            raise PluginError("Zerodha is rate limiting history — the app will ask again later", -32005)
        if status >= 500:
            raise PluginError(f"Zerodha answered HTTP {status} — the app will ask again later", -32005)
        if str(data.get("status", "")).lower() not in ("success", "ok"):
            raise PluginError(f"Zerodha: {data.get('message') or 'the request failed'}")
        return data.get("data", data)

    def profile_user_id(self):
        """The Zerodha user id the ticker URL needs; a blank setting means look it up once."""
        if self.user_id:
            return self.user_id
        try:
            data = self._json(PROFILE_PATH)
            uid = str(data.get("user_id") or "").strip()
        except AuthError:
            raise  # an expired enctoken must reach the refresh prompt, not become a generic error
        except PluginError as e:
            raise PluginError(f"Cannot read the Zerodha user id — set userId in Settings ({self.clean(str(e))})") from None
        if not uid:
            raise PluginError("Zerodha did not return a user id — set userId in Settings")
        self.user_id = uid
        return uid

    def ws_url(self):
        """Kite's ticker URL. A proxy may set wsUrl with its own query; otherwise build it from the enctoken.

        The token is percent-encoded: it contains '+' and '/', which a query parser would otherwise read as a
        space. Nothing here is logged.
        """
        base = str(self.settings.get("wsUrl", DEFAULT_WS_URL)).rstrip("/")
        if "access_token=" in base:
            return base
        uid = self.profile_user_id()
        return (f"{base}?api_key=kitefront"
                f"&access_token={urllib.parse.quote(self.enctoken, safe='')}"
                f"&user_id={urllib.parse.quote(uid, safe='')}")

    # ── company details and logo (Kite has neither; the external service is best effort) ──
    @staticmethod
    def _external_json(url, timeout=5):
        """One public GET, no credentials, no log. None on any problem."""
        parts = urllib.parse.urlsplit(url)
        secure = parts.scheme == "https"
        host = parts.hostname
        if not host:
            return None
        port = parts.port or (443 if secure else 80)
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        conn = (http.client.HTTPSConnection(host, port, timeout=timeout) if secure
                else http.client.HTTPConnection(host, port, timeout=timeout))
        try:
            conn.request("GET", target, headers={
                "User-Agent": BROWSER_UA, "Accept": "application/json", "Accept-Encoding": "gzip",
            })
            r = conn.getresponse()
            raw = r.read()
            if (r.getheader("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            if r.status != 200:
                return None
            return json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001 - an external enrichment must never break symbol info
            return None
        finally:
            conn.close()

    @staticmethod
    def _profile_cache_path():
        return plugin_data_dir() / "cache" / "company-profiles.json"

    @staticmethod
    def _read_profile_cache():
        """A bad or missing cache file must never stop the plugin starting."""
        try:
            with open(ZerodhaSource._profile_cache_path(), encoding="utf-8") as f:
                data = json.load(f)
            return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _profile_worker(self):
        """Fetch profiles one at a time. A large watchlist queues its symbols instead of spawning a thread each."""
        while True:
            ticker = self._profile_queue.get()
            try:
                self._company_profile(ticker)
            except Exception:  # noqa: BLE001 - enrichment is optional
                pass
            finally:
                self._profile_queue.task_done()

    _PROFILE_WRITE_LOCK = threading.Lock()

    def _write_profile_cache(self):
        """Write to a private temp file first: two threads can never fight over the same name."""
        path = self._profile_cache_path()
        tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._profiles, f)
            with self._PROFILE_WRITE_LOCK:
                os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as e:
            self.log(f"company profile cache not saved: {e}", "warn")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _company_profile(self, ticker):
        """sector/industry/name for a cash equity. Fetched at most once per ticker, then kept on disk for good."""
        with self.lock:
            hit = self._profiles.get(ticker)
            if hit is not None:
                return hit
            if time.monotonic() < self._profile_failed_until:
                return None
        try:
            answer = self._external_json(f"{COMPANY_API}/companies/{urllib.parse.quote(ticker, safe='')}")
            row = (answer or {}).get("data") if isinstance(answer, dict) else None
            profile = None
            if isinstance(row, dict):
                profile = {k: str(row[k]) for k in ("name", "company", "sector", "industry", "oneLine") if row.get(k)}
                if profile.get("company") and not profile.get("name"):
                    profile["name"] = profile["company"]
        except Exception:  # noqa: BLE001 - enrichment is optional; never let it break a symbol
            profile = None
        with self.lock:
            if profile is None:
                self._profile_failed_until = time.monotonic() + 60.0
            else:
                self._profiles[ticker] = profile
        if profile is not None:
            self._write_profile_cache()
        return profile

    # ── per-symbol history depth from NSE's listing lists (fetched by nse.py over one session) ──
    def _load_listings(self):
        """Single-flight: a watchlist must not make 30 symbolInfo threads fetch the same two CSVs at once."""
        today = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        with self.lock:
            if self._listings_day == today:
                return
            if self._listings_loading is None:
                self._listings_loading = threading.Event()
                mine = True
            else:
                mine = False
        if not mine:
            # This is optional enrichment: never block a symbolInfo request for it. The single fetch fills the cache
            # in a moment, and the app asks again shortly; an empty listing row just means fewer extra fields.
            return
        try:
            path = plugin_data_dir() / "cache" / f"nse-listings-{today}.json"
            data = nse.load_listings(self._nse, path, urls={
                "equity": NSE_EQUITY_LIST_URL, "sme": NSE_SME_LIST_URL,
            }, timeout=5)
            with self.lock:
                self._listings, self._listings_day = data, today
        except Exception as e:  # noqa: BLE001 - the listing data is optional
            self.log(f"NSE listing lists not read: {e}", "warn", "nse")
        finally:
            with self.lock:
                event, self._listings_loading = self._listings_loading, None
                if event is not None:
                    event.set()

    def _listing_of(self, symbol, listings=None):
        """The cached NSE listing row: {date, isin, face, series}. `listings` is a snapshot taken once per request."""
        if not symbol:
            return None
        if listings is None:
            self._load_listings()
            with self.lock:
                listings = self._listings
        entry = listings.get(symbol.upper())
        if entry is None:
            return None
        return entry if isinstance(entry, dict) else {"date": entry}

    def _listed_text(self, symbol, listings=None):
        """dd/mm/yyyy listing date of an NSE symbol, or None (used in longDescription and the Listed field)."""
        entry = self._listing_of(symbol, listings)
        when = entry.get("date") if entry else None
        return datetime.fromtimestamp(when, TZ_IST).strftime("%d/%m/%Y") if when else None

    def _listing_fields(self, symbol, listings=None):
        """The other NSE listing CSV columns we keep: ISIN, face value and series."""
        entry = self._listing_of(symbol, listings) or {}
        return {key: entry[key] for key in ("isin", "face", "series") if entry.get(key)}

    def _history_depth(self, row, listings=None):
        """Per-symbol `history` for data.symbolInfo: the app's hard clamp on history requests and scroll-back.

        NOT the same as the manifest's historyDays* settings: those decide how many days we fetch from Kite in
        one bars() call; this decides how far back the app is allowed to ask at all. The two work together:
        this caps the range, bars() serves it one chunk at a time as the app scrolls.

        NSE equities get min(HISTORY_DAYS_MAX, days since their DATE OF LISTING) from the listing lists loaded by
        _load_listings(), so an IPO listed last month declares 30 days instead of 30 years. Other exchanges and
        symbols with no listing row get the maxima. NCO has no minute history at Kite, so only `day` is declared.
        """
        listed = None
        if row["exchange"] == "NSE":
            entry = self._listing_of(row["ticker"], listings) or {}
            when = entry.get("date")
            if when:
                listed = max(1, int((time.time() - when) / 86400))
        day = min(HISTORY_DAYS_MAX["day"], listed) if listed else HISTORY_DAYS_MAX["day"]
        minute = min(HISTORY_DAYS_MAX["minute"], listed) if listed else HISTORY_DAYS_MAX["minute"]
        if row["exchange"] == "NCO":
            return {"day": day}
        return {"minute": minute, "day": day}

    # ── WebSocket lifecycle ──
    def _start_ws(self, generation):
        if self.ws is not None:
            self.ws.stop.set()
        self.ws_generation = generation
        if not self.enctoken:
            self.log("No enctoken yet — live prices will start once it is set", "warn")
            return
        ws = ZerodhaWS(self)
        self.ws = ws
        threading.Thread(target=ws.run, args=(generation,), daemon=True, name="ZerodhaWS").start()

    def divisor_for(self, token):
        row = self.instruments.token_row(token)
        return divisor_for_exchange(row["kite_exchange"]) if row else 100

    def set_subscriptions(self, tickers):
        """data.subscribe replaces the whole set; keep tickers we cannot serve yet (master still loading)."""
        with self.lock:
            self.subscribed = set(tickers)
            self.oi_subscribed = {t for t in tickers if self._series_row(t) is not None}
        self.forward_subscription()

    def _ws_modes(self):
        """Chart symbols stream in quote mode; the ticket and DOM need the full packet with depth."""
        with self.lock:
            tickers = set(self.subscribed) | set(self.watched) | set(self.watched_depth)
            full = set(self.watched) | set(self.watched_depth)
        modes = {}
        for ticker in tickers:
            series = self._series_row(ticker)
            row = series[0] if series else self.instruments.row(ticker)
            if row:
                with self.lock:
                    self.quotes.setdefault(ticker, {})
                # Open interest comes in the full packet, so stream the underlying in full mode.
                modes[row["token"]] = "full" if ticker in full or series else "quote"
        return modes

    def forward_subscription(self):
        modes = self._ws_modes()
        if modes:
            self.log(f"stream subscription: {len(modes)} tokens "
                     f"({sum(1 for m in modes.values() if m == 'full')} full)", "debug")
        if self.ws is not None:
            self.ws.update(modes)

    def _cancel_watch_clear(self, method):
        timer = self._watch_timers.pop(method, None)
        if timer is not None:
            timer.cancel()

    def _apply_watch_clear(self, method):
        with self.lock:
            if self._watch_timers.pop(method, None) is None:
                return  # a new watch arrived and cancelled this timer
            if method == "broker.watchQuote":
                self.watched = set()
            else:
                self.watched_depth = set()
        self.forward_subscription()

    def set_watch(self, method, tickers):
        """A whole watch set from the ticket or the DOM. An empty one is delayed briefly: switching charts sends an
        empty watch followed at once by the new symbol, and dropping full mode in between churns Kite's mode frames
        and blanks the ticket's quote."""
        tickers = set(tickers)
        with self.lock:
            current = self.watched if method == "broker.watchQuote" else self.watched_depth
            if not tickers and current:
                self._cancel_watch_clear(method)
                timer = threading.Timer(WATCH_GRACE_SEC, self._apply_watch_clear, args=(method,))
                timer.daemon = True
                self._watch_timers[method] = timer
                timer.start()
                return
            self._cancel_watch_clear(method)
            if method == "broker.watchQuote":
                self.watched = tickers
            else:
                self.watched_depth = tickers
        self.forward_subscription()

    @staticmethod
    def _bid_ask(q, tick_size):
        depth = q.get("depth") or {}
        buy, sell = depth.get("buy") or [], depth.get("sell") or []
        if buy and sell:
            return num(buy[0].get("price")), num(sell[0].get("price"))
        price = q.get("price") or 0
        return round(price - tick_size, 10), round(price + tick_size, 10)

    @staticmethod
    def _quote_of(ticker, q):
        out = {
            "ticker": ticker, "time": int(time.time()), "price": q["price"],
            "prevClose": q.get("prevClose"),
            "minute": list(q.get("minute") or []), "day": list(q.get("day") or []),
        }
        if q.get("fields"):
            out["fields"] = q["fields"]
        return out

    def on_ticks(self, ticks):
        """Update the cache once per frame, then write all notifications in a single socket call.

        Kite already groups the ticks of a moment in one binary frame. Coalescing here avoids one JSON-RPC
        message and one sendall per tick when thousands of watchlist symbols are streaming. An optional
        per-symbol interval (quoteIntervalMs) caps chart updates without touching the ticket's quote or DOM.
        """
        p = self.plugin
        self._perf["ticks"] += len(ticks)
        now = int(time.time())
        now_ms = time.time() * 1000
        messages = []
        quote_candidates = []  # (ticker, stamp, message) — the app's total quote budget is shared by all symbols
        with self.lock:
            for t in ticks:
                token = t.get("instrument_token")
                price = t.get("last_price") or 0
                if price <= 0:
                    continue
                meta = self.instruments.token_meta.get(token)  # lock-free, replaced atomically on master reload
                if meta is None:
                    continue
                ticker, tick_size = meta
                wanted_series = {s for s in self.oi_subscribed
                                 if (sr := self._series_row(s)) is not None and sr[0]["token"] == token}
                # A clean symbol, a continuous future and the Kite contract can all be on screen at once:
                # they share one token, so every subscribed name gets the same quote.
                targets = [ticker] + [a for a in self.instruments.app_symbols_for(ticker)
                                      if a in self.subscribed or a in self.watched or a in self.watched_depth]
                if not targets and not wanted_series:
                    continue
                q = self.quotes.setdefault(ticker, {})
                ohlc = t.get("ohlc") or {}
                prev = ohlc.get("close") or 0
                if prev > 0:
                    q["prevClose"] = prev
                volume = t.get("volume") or 0
                day_start = ist_midnight(now)
                d = q.get("day")
                if not d or d[0] != day_start:
                    d = [day_start, ohlc.get("open") or price, ohlc.get("high") or price,
                         ohlc.get("low") or price, price, volume]
                    q["_last_vol"] = 0
                else:
                    d = list(d)
                    d[2] = max(d[2], ohlc.get("high") or price)
                    d[3] = min(d[3], ohlc.get("low") or price)
                    d[4], d[5] = price, volume
                q["day"] = d
                minute_start = now // 60 * 60
                delta = max(0, volume - (q.get("_last_vol") or 0))
                q["_last_vol"] = volume
                m = q.get("minute")
                if not m or m[0] != minute_start:
                    m = [minute_start, price, price, price, price, delta]
                else:
                    m = list(m)
                    m[2], m[3], m[4] = max(m[2], price), min(m[3], price), price
                    m[5] = (m[5] or 0) + delta
                q["minute"] = m
                q["price"] = price
                # Second charts have no Kite history; keep recent ticks so _second_bars can build them.
                if ticker in self.subscribed:
                    ticks = self._ticks.setdefault(ticker, [])
                    ticks.append((now_ms / 1000.0, price, delta))
                    cutoff = now - TICK_BUFFER_SEC
                    if len(ticks) > 20_000 or (ticks and ticks[0][0] < cutoff):
                        self._ticks[ticker] = [x for x in ticks if x[0] >= cutoff][-20_000:]
                if t.get("depth"):
                    q["depth"] = t["depth"]

                for name in targets:
                    if name not in self.subscribed:
                        continue
                    last = self.quotes.get(name, {}).get("_last_sent") or 0
                    if not self.quote_interval or now_ms - last >= self.quote_interval:
                        # Queued, not sent: the per-second budget below decides which of this frame's
                        # candidates go out. Every name sharing the token gets the same price.
                        quote_candidates.append((name, now_ms, {"jsonrpc": "2.0", "method": "data.quote",
                                                                "params": self._quote_of(name, q)}))
                    else:
                        self._perf["skipped"] += 1
                if ticker in self.watched or ticker in self.watched_depth:
                    self._perf["bquotes"] += 1
                    bid, ask = self._bid_ask(q, tick_size)
                    messages.append({"jsonrpc": "2.0", "method": "broker.quote",
                                     "params": {"ticker": ticker, "bid": bid, "ask": ask, "last": price}})
                oi = t.get("oi")
                if wanted_series:
                    stamp = now // 60 * 60
                    for series_ticker in wanted_series:
                        series = self._series_row(series_ticker)
                        if not series:
                            continue
                        row, name = series
                        if name == "OI":
                            if oi is None:
                                continue
                            values = [oi, oi, oi, oi, 0]
                            fields = [{"name": "Open Interest", "value": oi},
                                      {"name": "OI day high", "value": t.get("oi_day_high")},
                                      {"name": "OI day low", "value": t.get("oi_day_low")}]
                        else:
                            values = self.customone_values(now)
                            fields = [{"name": f"Custom {i + 1}", "value": v} for i, v in enumerate(values)]
                        oq = self.quotes.setdefault(series_ticker, {})
                        oq["minute"] = [stamp] + values
                        oq["day"] = [ist_midnight(now)] + values
                        oq["price"] = values[3]
                        oq["fields"] = fields
                        self._perf["quotes"] += 1
                        messages.append({"jsonrpc": "2.0", "method": "data.quote",
                                         "params": self._quote_of(series_ticker, oq)})
                if ticker in self.watched_depth:
                    self._perf["depth"] += 1
                    depth = q.get("depth") or {}
                    messages.append({"jsonrpc": "2.0", "method": "broker.depth",
                                     "params": {
                                         "ticker": ticker, "snapshot": True,
                                         "bids": [{"price": l["price"], "qty": l["quantity"]}
                                                  for l in (depth.get("buy") or [])[:5]],
                                         "asks": [{"price": l["price"], "qty": l["quantity"]}
                                                  for l in (depth.get("sell") or [])[:5]],
                                     }})
        if quote_candidates:
            if self.max_quotes_per_second > 0:
                mono = time.monotonic()
                self._quote_tokens = min(float(self.max_quotes_per_second),
                                         self._quote_tokens + max(0.0, mono - self._quote_refill_at)
                                         * self.max_quotes_per_second)
                self._quote_refill_at = mono
                # Oldest-sent first: a symbol the budget dropped gets priority in the next frame. A full watchlist
                # then rotates instead of the same liquid symbols taking every slot.
                quote_candidates.sort(key=lambda c: self.quotes.get(c[0], {}).get("_last_sent", 0))
                allow = min(len(quote_candidates), int(self._quote_tokens))
                for ticker, stamp, msg in quote_candidates[:allow]:
                    self._quote_tokens -= 1
                    self.quotes[ticker]["_last_sent"] = stamp
                    self._perf["quotes"] += 1
                    messages.append(msg)
                self._perf["budget_skipped"] += len(quote_candidates) - allow
            else:
                for ticker, stamp, msg in quote_candidates:
                    self.quotes[ticker]["_last_sent"] = stamp
                    self._perf["quotes"] += 1
                    messages.append(msg)
        if p and p.alive and messages:
            self._perf["batches"] += 1
            p.send_many(messages)

    # ── market data ──
    def search(self, query):
        return self.instruments.search(query, self.exchanges)

    def symbol_info(self, ticker):
        started = time.monotonic()
        row = self.instruments.row(ticker)
        if row is None:
            raise PluginError(f"Unknown symbol {ticker}", -32004)
        exchange = row["exchange"]
        scale = max(2, int(round(1 / row["tick"]))) if row["tick"] < 1 else 100
        info = {
            "description": row["description"],
            "type": row["type"],
            "currency": "INR",
            "timezone": "Asia/Kolkata",
            "session": SESSIONS.get(exchange, SESSIONS.get(exchange.split("_")[0], "0915-1530")),
            "pricescale": scale,
            "minmov": 1,
            "pointValue": row["lot"],
            # Kite's personal API serves no minute bars for NCO: offer only days (weeks/months are built by the
            # app) so the chart doesn't keep asking for an interval the source cannot answer.
            "intervals": ["1D"] if exchange == "NCO" else list(SERVED_INTERVALS),
        }
        # API 9/10: a continuous future trades as its front contract. Scripts read syminfo.root for every
        # symbol (the underlying for derivatives, the symbol itself otherwise) and the expiry of derivatives.
        if row.get("continuous"):
            info["tradeTicker"] = f"{exchange}:{row['ticker']}"
        underlying = row.get("root") or (str(row["name"]).upper()
                                         if row["type"] in ("futures", "option") and row.get("name")
                                         else row["symbol"])
        info["root"] = underlying
        if row["type"] in ("futures", "option"):
            expiry = row.get("expiration") or row.get("expiry")
            if expiry:
                info["expiration"] = expiry
        info["baseCurrency"] = "INR"
        info["country"] = "IN"
        info["volumeType"] = "base"
        # Ensure a snapshot exists. The first call of the day may fetch the two small CSVs; every other caller
        # returns at once (single-flight) and reads the local cache, so a watchlist is served in parallel.
        self._load_listings()
        # One short lock hold for all cached snapshots; everything after this is lock-free, so a watchlist of 30
        # symbols is answered in parallel from local data rather than queuing on the same lock three times each.
        with self.lock:
            listings = self._listings
            profiles = self._profiles
            may_fetch = time.monotonic() >= self._profile_failed_until
        # Everything below is extra information. A bad cache, a slow service or a bug in one of these must never
        # fail the symbol itself — the chart needs its basic info even when an optional field is broken.
        listed = None
        try:
            info["history"] = self._history_depth(row, listings)
        except Exception:  # noqa: BLE001
            info["history"] = dict(HISTORY_DAYS_MAX) if exchange != "NCO" else {"day": HISTORY_DAYS_MAX["day"]}
        try:
            info["series"] = self.series_for(row)
            listed = self._listed_text(row["ticker"], listings) if exchange == "NSE" else None
            if exchange == "NSE":
                entry = self._listing_of(row["ticker"], listings)
                if entry and entry.get("isin"):
                    info["isin"] = entry["isin"]
            info["fields"] = self.fields_for(
                row, listed, self._listing_fields(row["ticker"], listings) if exchange == "NSE" else None)
        except Exception:  # noqa: BLE001
            info["series"], info["fields"], listed = [], [], None
        try:
            if self.company_details and row["type"] == "stock" and exchange in ("NSE", "BSE"):
                symbol = urllib.parse.quote(row["ticker"], safe="")
                info["icon"] = f"{COMPANY_API}/logos/{symbol}"
                # Best effort: a cached profile is used at once, otherwise it is fetched in the background.
                profile = profiles.get(row["ticker"])
                if profile:
                    for key in ("name", "sector", "industry"):
                        if profile.get(key):
                            info[key] = profile[key]
                    if profile.get("oneLine"):
                        info["longDescription"] = profile["oneLine"]
                elif may_fetch:
                    with self.lock:
                        if row["ticker"] not in self._profile_queued:
                            self._profile_queued.add(row["ticker"])
                            self._profile_queue.put(row["ticker"])
        except Exception:  # noqa: BLE001
            pass
        # OpenTrader has no listingDate field in symbolInfo; the dialog shows longDescription, so put it there.
        try:
            if listed:
                info["longDescription"] = ("Listed " + listed + " · " + info["longDescription"]
                                           if info.get("longDescription") else "Listed " + listed)
        except Exception:  # noqa: BLE001
            pass
        elapsed = time.monotonic() - started
        if elapsed > 1.0:
            self.log(f"symbolInfo {ticker} took {elapsed:.1f}s", "warn")
        return info

    def quote(self, ticker):
        with self.lock:
            q = self.quotes.get(ticker)
            if not q or not q.get("price"):
                return None
            return self._quote_of(ticker, q)

    def _interval_for(self, resolution):
        interval = INTERVALS.get(resolution)
        if interval is None:
            raise PluginError(f"Resolution {resolution} is not served by Zerodha", -32602)
        return interval

    @staticmethod
    def _date_text(ts):
        return urllib.parse.quote(datetime.fromtimestamp(ts, TZ_IST).strftime("%Y-%m-%d %H:%M:%S"), safe="")

    def _cap(self, key, default):
        """A per-request history cap in days; blank, bad or non-positive values keep the default."""
        try:
            value = int(float(self.settings.get(key) or default))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    def _history_one(self, token, interval, frm, to, oi=False, timeout=30):
        """Price bars, or an OI series ([time, value, value, value, value]) when `oi` is set."""
        path = (HISTORY_PATH.format(token=token, interval=interval)
                + f"?from={self._date_text(frm)}&to={self._date_text(to)}&oi=1")
        data = self._json(path, timeout=timeout)
        out = []
        for candle in (data.get("candles") or []):
            if not isinstance(candle, list) or len(candle) < 5:
                continue
            try:
                raw_time = str(candle[0])
                try:
                    when = datetime.fromisoformat(raw_time)  # Kite: "2026-10-22T00:00:00+0530"
                except ValueError:
                    when = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S")  # older adapters
                if when.tzinfo is None:
                    when = when.replace(tzinfo=TZ_IST)
                t = int(when.timestamp())
                if interval == "day":
                    t = ist_midnight(when.timestamp())
                o, h, low, c = (num(candle[i]) for i in range(1, 5))
                v = num(candle[5], 0) or 0
            except (ValueError, TypeError):
                continue
            if None in (o, h, low, c):
                continue
            if oi:
                value = num(candle[6], 0) or 0
                if out and out[-1][0] == t:
                    out[-1] = [t, value, value, value, value]
                else:
                    out.append([t, value, value, value, value])
            elif out and out[-1][0] == t:
                out[-1] = [t, out[-1][1], max(out[-1][2], h), min(out[-1][3], low), c,
                           (out[-1][5] or 0) + v]
            else:
                out.append([t, o, h, low, c, int(v)])
        out.sort(key=lambda b: b[0])
        return out

    @staticmethod
    def _map_event(raw):
        """Kite corporate action → the app's chart-event shape (dividend / split markers)."""
        action = str(raw.get("action_type") or "").lower()
        meta = raw.get("meta") or {}
        first, second = num(meta.get("ratio_1")), num(meta.get("ratio_2"))
        if action == "dividend":
            # Kite's meta: ratio_1 is the face value, ratio_2 is the dividend per share. ratio_1/ratio_2 is the
            # percentage denominator (often 10), which made every dividend read "10.00".
            amount = second if second is not None else num(meta.get("amount"))
            label = f"{amount:.2f}" if amount is not None else "0.00"
            kind = "dividend"
        elif action in ("bonus", "split"):
            label = f"{int(first or 1)}:{int(second or 1)} {action}"
            kind = "split"
        else:
            label, kind = action or "event", "dividend"
        when = 0
        try:
            when = int(datetime.strptime(str(raw.get("ex_date")), "%Y-%m-%d")
                       .replace(tzinfo=TZ_IST).timestamp())
        except (ValueError, TypeError):
            pass
        return {"time": when, "kind": kind, "label": label, "action": action,
                "date": str(raw.get("ex_date") or ""), "meta": meta}

    def corporate_events(self, ticker):
        """Corporate actions for `ticker`, fetched on a daily-bars request and cached for the day.

        The app's chart-event fields aren't in the plugin protocol yet, but the result carries them so an
        upstream app change only has to read `events` from `data.bars`. Kite already serves adjusted prices;
        these are markers only, never applied to OHLC.
        """
        row = self.instruments.row(ticker)
        if row is None or not self.enctoken:
            return []
        token = row["token"]
        today = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        with self.lock:
            hit = self._events.get(token)
            fresh = hit is not None and hit[0] == today
            if fresh and time.time() - self._events_at.get(token, 0) < EVENTS_REFRESH_SEC:
                return hit[1]
            previous = hit[1] if fresh else None
        path = plugin_data_dir() / "cache" / "events" / f"{token}-{today}.json"
        events = None
        if not fresh:  # a periodic re-check must go to the source, or it can never see a new event
            try:
                with open(path, encoding="utf-8") as f:
                    events = json.load(f)
            except (OSError, ValueError):
                pass
        if events is None:
            merged, start, end = [], EVENTS_FROM, datetime.now(TZ_IST).date()
            while start <= end:
                stop = min(start + timedelta(days=EVENTS_CHUNK_DAYS - 1), end)
                path_q = (f"/oms/instruments/events/{token}"
                          f"?from={start.isoformat()}&to={stop.isoformat()}")
                try:
                    data = self._json(path_q, timeout=15)
                except PluginError as e:
                    self.log(f"corporate events {ticker}: {e}", "warn")
                    break
                rows = data if isinstance(data, list) else (data.get("data") or [])
                for r in rows:  # chunks overlap at boundaries; one action per ex-date
                    if isinstance(r, dict) and not any(
                            x.get("action_type") == r.get("action_type") and x.get("ex_date") == r.get("ex_date")
                            for x in merged):
                        merged.append(r)
                start = stop + timedelta(days=1)
            events = [self._map_event(r) for r in merged if isinstance(r, dict)]
            events.sort(key=lambda e: e["time"])
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(events, f)
            except OSError:
                pass
        with self.lock:
            self._events[token] = (today, events)
            self._events_at[token] = time.time()
        if previous is not None and previous != events:
            p = self.plugin
            if p and p.alive:
                p.notify("data.eventsChanged", {"tickers": [ticker]})
            self.log(f"corporate events {ticker}: changed, told the app to re-read", "info")
        if events:
            self.log(f"corporate events {ticker}: {len(events)} actions — "
                     + ", ".join(f"{e['action']} {e['date']} {e['label']}" for e in events[-4:]))
        return events

    def watchlists(self):
        """Sample lists from the master plus today's delivery holdings (Plugin API 4), as whole symbols."""
        lists = []
        held = []
        seen = set()
        for h in self.holdings:
            sym = h.get("symbol")
            if isinstance(sym, str) and sym and sym not in seen:
                seen.add(sym)
                held.append(sym)
        if held:
            lists.append({"id": "zerodha-holdings", "name": "Holdings (zerodha)",
                          "symbols": held[:1000], "sections": []})
        if self.price_band_symbols:
            lists.append({"id": "zerodha-price-band-hitters", "name": "NSE Price Band Hitters",
                          "symbols": list(self.price_band_symbols), "sections": []})
        if self.nse_upcoming_sections:
            lists.append({"id": "zerodha-nse-upcoming", "name": "NSE all Forthcomming",
                          "symbols": [], "sections": self.nse_upcoming_sections})
        if self.nse_today_sections:
            lists.append({"id": "zerodha-nse-today-events", "name": "NSE Today Events",
                          "symbols": [], "sections": self.nse_today_sections})
        if self.volume_gainers_symbols:
            lists.append({"id": "zerodha-volume-gainers", "name": "NSE Volume Gainers",
                          "symbols": list(self.volume_gainers_symbols)[:1000], "sections": []})
        if self.corporate_announcements_sections:
            lists.append({"id": "zerodha-corporate-announcements", "name": "NSE Corporate Announcements",
                          "symbols": [], "sections": self.corporate_announcements_sections})
        if self.newly_listed_sections:
            lists.append({"id": "zerodha-newly-listed", "name": "NSE Newly Listed",
                          "symbols": [], "sections": self.newly_listed_sections})
        if self.holdings_event_sections:
            lists.append({"id": "zerodha-holdings-events", "name": "My Holdings — Upcoming Events",
                          "symbols": [], "sections": self.holdings_event_sections})
        bands = (
            ("zerodha-band-2", "NSE 2% Price band", self.band_2_symbols),
            ("zerodha-band-5", "NSE 5% Price band", self.band_5_symbols),
            ("zerodha-band-10", "NSE 10% Price band", self.band_10_symbols),
            ("zerodha-band-20-eq-1", "NSE 20% Price band EQ (1/2)", self.band_20_eq1),
            ("zerodha-band-20-eq-2", "NSE 20% Price band EQ (2/2)", self.band_20_eq2),
            ("zerodha-band-20-sm", "NSE 20% Price band SM", self.band_20_sm),
        )
        for wid, name, symbols in bands:
            if symbols:
                lists.append({"id": wid, "name": name, "symbols": list(symbols)[:1000], "sections": []})
        if self.gsm_sections:
            lists.append({"id": "zerodha-gsm-stage", "name": "NSE GSM STAGE",
                          "symbols": [], "sections": self.gsm_sections})
        rows = self.instruments.rows
        if not rows:
            return lists
        by_ticker = {r["ticker"].upper(): r for r in rows if r["exchange"] == "NSE"}

        def whole(names):
            out = []
            for name in names:
                row = by_ticker.get(name.upper())
                if row is not None:
                    out.append(f"{row['exchange']}:{row['ticker']}")
            return out[:1000]

        blue = whole(SAMPLE_BLUE_CHIPS)
        nse_idx = [f"NSE_INDEX:{r['symbol']}" for r in rows
                   if r["type"] == "index" and r["exchange"] == "NSE_INDEX"][:1000]
        bse_idx = [f"BSE_INDEX:{r['symbol']}" for r in rows
                   if r["type"] == "index" and r["exchange"] == "BSE_INDEX"][:1000]
        if blue:
            lists.append({"id": "zerodha-blue-chips", "name": "NSE blue chips (sample)",
                          "symbols": blue, "sections": []})
        if nse_idx or bse_idx:
            sections = []
            if nse_idx:
                sections.append({"name": "NSE Index", "symbols": nse_idx})
            if bse_idx:
                sections.append({"name": "BSE Index", "symbols": bse_idx})
            lists.append({"id": "zerodha-indices", "name": "Indices",
                          "symbols": [], "sections": sections})
        if nse_idx:
            lists.append({"id": "zerodha-nse-indices", "name": "NSE Indices", "symbols": [],
                          "sections": [{"name": "NSE Index", "symbols": nse_idx}]})
        if bse_idx:
            lists.append({"id": "zerodha-bse-indices", "name": "BSE Indices", "symbols": [],
                          "sections": [{"name": "BSE Index", "symbols": bse_idx}]})
        return lists

    def events_for(self, ticker):
        """Corporate actions in the Plugin API 5 shape the app draws on the chart."""
        row = self.instruments.row(ticker)
        if row is None or row["type"] != "stock":
            return []  # indices and derivatives have no corporate actions on Kite
        events = self.corporate_events(ticker)
        out = []
        for e in events:
            action, date = e.get("action"), e.get("date")
            if not date:
                continue
            meta = e.get("meta") or {}
            first, second = num(meta.get("ratio_1")), num(meta.get("ratio_2"))
            if action == "dividend":
                # Kite's ratio_2 is the rupees-per-share paid; ratio_1 is the face value. Using ratio_1/ratio_2 made
                # every dividend read 10.00 regardless of the actual payout.
                amount = second if second is not None else num(meta.get("amount"))
                if amount is None:
                    continue
                out.append({"type": "dividend", "date": date, "amount": amount})
            elif action == "bonus":
                # A 1:1 bonus is a 1 → 2 split: the holder ends with both lots.
                out.append({"type": "split", "date": date, "from": 1,
                            "to": int((first or 1) + (second or 1))})
            elif action == "split":
                out.append({"type": "split", "date": date, "from": int(first or 1),
                            "to": int(second or 1)})
        return out[:5000]

    @staticmethod
    def fields_for(row, listed=None, listing=None):
        """Instrument values the app's own Symbol Info does not show.

        Tick size and ISIN are built in (ISIN via API 10), and the lot is Point value, so those are not
        repeated here: scripts read them from syminfo and the dialog shows the built-in fields.
        """
        fields = []
        if row.get("expiry"):
            fields.append({"name": "Expiry", "value": row["expiry"]})
        if listed:
            fields.append({"name": "Listed", "value": listed})
        for key, label in (("face", "Face value"), ("series", "Series")):
            if listing and listing.get(key):
                fields.append({"name": label, "value": listing[key]})
        fields.extend(EXTRA_FIELDS)
        return fields[:50]

    @staticmethod
    def series_for(row):
        """Extra series (Plugin API 6): Kite serves open interest with oi=1 on F&O history.

        CUSTOMONE is a sample of five values packed in one bar's OHLCV slots; only the symbols in
        CUSTOMONE_SYMBOLS declare it, so scripts on other symbols cannot read made-up numbers from it.
        """
        series = ["OI"] if row["type"] in ("futures", "option") else []
        if f'{row["exchange"]}:{row["ticker"]}' in CUSTOMONE_SYMBOLS:
            series.append({"name": CUSTOMONE_NAME, "description": "Custom One (five sample values)",
                           "pricescale": 1})
        series.extend(EXTRA_SERIES)
        return series

    def _series_row(self, ticker):
        """An extra series' ticker (`<symbol>_OI`, `<symbol>_CUSTOMONE`) → its (base row, series name), or None."""
        for name in ["OI", CUSTOMONE_NAME] + list(EXTRA_SERIES):
            suffix = "_" + name
            if ticker.upper().endswith(suffix.upper()):
                base = ticker[:-len(suffix)]
                row = self.instruments.row(base)
                if row is None and ":" not in base:
                    row = next((r for r in self.instruments.rows if r["ticker"].upper() == base.upper()), None)
                if row is None:
                    return None
                if name == CUSTOMONE_NAME and f'{row["exchange"]}:{row["ticker"]}' not in CUSTOMONE_SYMBOLS:
                    return None
                return row, name
        return None

    def _second_bars(self, ticker, resolution, frm, to, count_back):
        """Build N-second OHLCV bars from the live ticks when Kite has no seconds history for this bucket."""
        step = SECOND_INTERVALS[resolution]
        bars = {}
        for ts, price, vol in self._ticks.get(ticker) or []:
            if ts < frm or ts > to:
                continue
            t = int(ts) // step * step
            bar = bars.get(t)
            if bar is None:
                bars[t] = [t, price, price, price, price, vol]
            else:
                bar[2] = max(bar[2], price)
                bar[3] = min(bar[3], price)
                bar[4] = price
                bar[5] += vol
        out = [bars[t] for t in sorted(bars)]
        return out[-count_back:] if count_back and len(out) > count_back else out

    def _live_bar(self, ticker, resolution, frm, to, count_back):
        """The current forming bar, already built from the tick stream — answer a refresh without calling Kite.

        Kite's quote/full packet carries the day's (or minute's) open/high/low, the last price and cumulative volume.
        `on_ticks` turns those into the same OHLCV bar the history endpoint would return, so a "newest one bar"
        refresh — including the OI series' — can be served from the cache.
        """
        if not 1 <= (count_back or 0) <= 2:
            return None
        q = self.quotes.get(ticker)
        if not q:
            return None
        now = time.time()
        if resolution == "1D":
            bar, stamp = q.get("day"), ist_midnight(now)
        elif resolution == "1":
            bar, stamp = q.get("minute"), now // 60 * 60
        else:
            return None
        if not bar or bar[0] != stamp or frm > stamp or to < stamp:
            return None
        return [list(bar)]

    @staticmethod
    def customone_values(when):
        """Five sample values of one bar, stable for its time: 10..50 plus a slow step. A script reads them as
        [open, high, low, close, volume] of <symbol>_CUSTOMONE, so one request.security gives all five."""
        step = float((int(when) // 60) % 1000)
        return [step + 10, step + 20, step + 30, step + 40, step + 50]

    def _customone_bars(self, ticker, resolution, frm, to, count_back):
        """Sample series: take the base symbol's real bar times, put five sample values in each bar's slots."""
        suffix = "_" + CUSTOMONE_NAME
        base = ticker[:-len(suffix)] if ticker.upper().endswith(suffix.upper()) else ticker
        source = self.bars(base, resolution, frm, to, count_back)
        return [[bar[0]] + self.customone_values(bar[0]) for bar in source]

    def bars(self, ticker, resolution, frm, to, count_back):
        series = self._series_row(ticker)
        row = series[0] if series else self.instruments.row(ticker)
        if row is None:
            raise PluginError(f"Unknown symbol {ticker}", -32004)
        interval = self._interval_for(resolution)
        cached = self._live_bar(ticker, resolution, frm, to, count_back)
        if cached is not None:
            self._perf["cached_bars"] += 1
            return cached
        if series and series[1] == CUSTOMONE_NAME:
            return self._customone_bars(ticker, resolution, frm, to, count_back)
        # Kite's personal OMS caps a call at 2 days of seconds, 60 days of minutes, 400 of hours and 2000 of days.
        family = ("second" if interval.endswith("second") else "hour" if interval in HOUR_INTERVALS
                  else "day" if interval == "day" else "minute")
        hard_days = CHUNK_DAYS[family]
        configured = self._cap("historyDaysDaily", 1900) if interval == "day" else self._cap("historyDaysMinute", 30)
        step = min(configured, hard_days) * 86400
        # Intraday: only the newest chunk — the app pages backwards on scroll. Daily: fill the whole asked window,
        # because 3M/12M charts aggregate the single answer and never scroll to fetch older pages themselves.
        start = max(frm, to - step + 1)
        merged = {}
        page_start, page_to, pages = start, to, 0
        while True:
            pages += 1
            try:
                page = self._history_one(row["token"], interval, page_start, page_to, oi=bool(series))
            except PluginError as e:
                if resolution in SECOND_INTERVALS:
                    # Kite normally serves seconds; if this deployment refuses, fill forward from live ticks.
                    self.log(f"history {ticker} {resolution}: {e} — using live ticks", "warn")
                    return self._second_bars(ticker, resolution, frm, to, count_back)
                self.log(f"history {ticker} {resolution}: {e}", "warn")
                raise
            for bar in page:
                merged[bar[0]] = bar
            if interval != "day" or not page or page_start <= frm or pages >= MAX_DAILY_PAGES:
                break
            older_to = page[0][0] - 86400  # one day before this page's oldest bar
            if older_to < frm:
                break
            page_to = older_to
            page_start = max(frm, page_to - step + 1)
        out = [merged[t] for t in sorted(merged)]
        if count_back and len(out) > count_back:
            out = out[-count_back:]
        return out

    # ── broker (phase 2): Kite's personal /oms routes, authenticated with the same enctoken ──
    STATUS = {
        "PUT ORDER REQ RECEIVED": "placing", "VALIDATION PENDING": "placing",
        "OPEN PENDING": "placing", "AMO REQ RECEIVED": "placing",
        "OPEN": "working", "UPDATE": "working", "MODIFY PENDING": "working",
        "MODIFY VALIDATION PENDING": "working", "CANCEL PENDING": "working",
        "TRIGGER PENDING": "working",
        "COMPLETE": "filled", "CANCELLED": "cancelled", "CANCELLED AMO": "cancelled",
        "AMO CANCELLED": "cancelled", "REJECTED": "rejected",
    }

    @classmethod
    def map_status(cls, raw):
        """Kite has many status spellings; terminal ones must never be shown as working."""
        key = str(raw or "").strip().upper()
        if key in cls.STATUS:
            return cls.STATUS[key]
        if "CANCEL" in key:
            return "cancelled"
        if "REJECT" in key:
            return "rejected"
        if "COMPLETE" in key or "FILLED" in key:
            return "filled"
        return "working"
    PRODUCTS = {"MIS": "Intraday", "CNC": "Delivery", "NRML": "Carry forward"}
    KITE_TYPES = {"market": "MARKET", "limit": "LIMIT", "stop": "SL", "stopLimit": "SL-M"}
    APP_TYPES = {v: k for k, v in KITE_TYPES.items()}

    def _app_symbol(self, kite_exchange, tradingsymbol):
        """Kite's book/postback symbol → the app's whole symbol and its master row."""
        row = self.instruments.kite_row(kite_exchange, tradingsymbol)
        if row:
            return f"{row['exchange']}:{row['ticker']}", row
        return f"{kite_exchange or 'NSE'}:{tradingsymbol or ''}", None

    def _to_units(self, row, contracts):
        """Kite reports MCX quantities in contracts; the app works in units (one lot = row['lot'])."""
        return (contracts or 0) * row["lot"] if row and row["exchange"] == "MCX" else (contracts or 0)

    def _to_contracts(self, row, units, field="Quantity"):
        if row and row["exchange"] == "MCX":
            lot = row["lot"] or 1
            count = (units or 0) / lot
            if abs(count - round(count)) > 1e-9:
                raise PluginError(f"{field} must be a whole number of lots ({lot:g} units = 1 lot)")
            return int(round(count))
        if units != int(units):
            raise PluginError("The quantity must be a whole number")
        return int(units)

    @staticmethod
    def _ms(value):
        """Kite's timestamp string as UNIX milliseconds (0 when unreadable)."""
        if not value:
            return 0
        try:
            when = datetime.fromisoformat(str(value))
            if when.tzinfo is None:
                when = when.replace(tzinfo=TZ_IST)
            return int(when.timestamp() * 1000)
        except (ValueError, TypeError):
            return 0

    def resolve_symbol(self, symbol, ticker):
        candidate = symbol if ":" in str(symbol) else ticker
        exchange = str(candidate or "").split(":", 1)[0].upper()
        if exchange in self.exchanges:
            return candidate if self.instruments.row(candidate) else None
        row = self.instruments.kite_row(exchange, ticker)
        return f"{row['exchange']}:{row['ticker']}" if row else None

    def instrument(self, ticker):
        row = self.instruments.row(ticker)
        if row is None:
            raise PluginError(f"Unknown symbol {ticker}", -32004)
        exchange = row["exchange"]
        products = ["MIS", "CNC"] if exchange in ("NSE", "BSE") else ["MIS", "NRML"]
        default = (self.settings.get("product") or "MIS").upper()
        if default not in products:
            default = products[0]
        step = row["lot"] if row["lot"] > 1 else 1  # one lot is the ticket's minimum step for derivatives
        return {
            "qtyMin": step, "qtyStep": step, "pointValue": 1,
            "ticketFields": [{"id": "product", "label": "Product", "default": default,
                              "options": [{"value": p, "label": self.PRODUCTS[p]} for p in products]}],
        }

    def place(self, p):
        ticker = p["ticker"]
        row = self.instruments.row(ticker)
        if row is None:
            raise PluginError(f"Unknown symbol {ticker}", -32004)
        qty = num(p.get("qty"))
        if not qty or qty <= 0:
            raise PluginError("The quantity must be above 0")
        contracts = self._to_contracts(row, qty)
        side = "SELL" if str(p.get("side") or "").lower() == "sell" else "BUY"
        order_type = self.KITE_TYPES.get(p.get("type"), "MARKET" if p.get("type") == "market" else "LIMIT")
        product = str((p.get("fields") or {}).get("product") or self.settings.get("product") or "MIS").upper()
        if product not in self.PRODUCTS:
            product = "MIS"
        validity = "IOC" if str(p.get("duration") or "").lower() == "ioc" else "DAY"
        # The Kite web app posts these fields alongside the ones in the path; keep the payload in that shape.
        body = {
            "variety": "regular",
            "tradingsymbol": row["ticker"], "exchange": row["kite_exchange"],
            "transaction_type": side, "order_type": order_type, "quantity": contracts,
            "product": product, "price": num(p.get("limitPrice")) or 0,
            "trigger_price": num(p.get("stopPrice")) or 0, "disclosed_quantity": 0,
            "validity": validity, "tag": "web",
            "squareoff": 0, "stoploss": 0, "trailing_stoploss": 0,
        }
        if self.user_id:
            body["user_id"] = self.user_id
        data = self._json("/oms/orders/regular", method="POST", body=body, timeout=15)
        order_id = str(data.get("order_id") or "")
        if not order_id:
            raise PluginError("Zerodha accepted the order but gave no order id")
        with self.lock:
            self.orders[order_id] = {
                "id": order_id, "ticker": ticker, "side": side.lower(), "type": p.get("type"),
                "qty": qty, "filledQty": 0, "limitPrice": num(p.get("limitPrice")),
                "stopPrice": num(p.get("stopPrice")), "status": "placing", "duration": validity.lower(),
                "variety": "regular", "fields": {"product": product}, "updatedMs": int(time.time() * 1000),
            }
        self.last_poll = 0
        self.log(f"order placed {order_id}: {ticker} {side} {order_type} qty={qty:g} "
                 f"price={body['price']} trigger={body['trigger_price']} product={product} validity={validity}")
        return order_id, [{"kind": "placed", "ticker": ticker, "side": side.lower(),
                           "type": p.get("type"), "qty": qty}]

    def modify(self, p):
        order_id = p["orderId"]
        with self.lock:
            order = self.orders.get(order_id)
            if not order or order["status"] not in ("placing", "working"):
                raise PluginError("The order is no longer working")
            order = dict(order)
        row = self.instruments.row(order["ticker"])
        qty = num(p.get("qty")) or order["qty"]
        body = {
            "order_type": self.KITE_TYPES.get(p.get("type") or order.get("type"), "LIMIT"),
            "quantity": self._to_contracts(row, qty),
            "price": num(p.get("limitPrice")) or order.get("limitPrice") or 0,
            "disclosed_quantity": 0,
            "validity": "IOC" if str(p.get("duration") or order.get("duration") or "").lower() == "ioc" else "DAY",
        }
        trigger = num(p.get("stopPrice")) or order.get("stopPrice")
        if trigger:
            body["trigger_price"] = trigger
        self._json(f"/oms/orders/{order.get('variety', 'regular')}/{order_id}",
                   method="PUT", body=body, timeout=15)
        with self.lock:
            if order_id in self.orders:
                self.orders[order_id].update(qty=qty, limitPrice=body["price"],
                                             updatedMs=int(time.time() * 1000))
        self.last_poll = 0
        self.log(f"order modified {order_id}: qty={qty:g} price={body['price']} validity={body['validity']}")
        return [{"kind": "modified", "ticker": order["ticker"], "side": order["side"],
                 "type": order["type"], "qty": qty}]

    def cancel(self, order_id):
        with self.lock:
            order = self.orders.get(order_id)
            if not order or order["status"] not in ("placing", "working"):
                raise PluginError("The order is no longer working")
            order = dict(order)
        self._json(f"/oms/orders/{order.get('variety', 'regular')}/{order_id}",
                   method="DELETE", timeout=15)
        with self.lock:
            if order_id in self.orders:
                self.orders[order_id].update(status="cancelled", updatedMs=int(time.time() * 1000))
        self.last_poll = 0
        self.log(f"order cancelled {order_id}: {order['ticker']}")
        return [{"kind": "cancelled", "ticker": order["ticker"], "side": order["side"],
                 "type": order["type"], "qty": order["qty"]}]

    def close_position(self, position_id, qty):
        with self.lock:
            pos = next((x for x in self.positions if x["id"] == position_id), None)
        if pos is None:
            raise PluginError("No such position")
        close = pos["qty"] if qty is None else qty
        side = "sell" if pos["side"] == "buy" else "buy"
        _, events = self.place({"ticker": pos["ticker"], "side": side, "type": "market", "qty": close})
        self.last_poll = 0
        return events

    def set_position_brackets(self, position_id, take_profit, stop_loss):
        raise PluginError("Zerodha does not support brackets on an existing position", -32601)

    def _wire_order(self, r):
        ticker, row = self._app_symbol(r.get("exchange"), r.get("tradingsymbol"))
        if not r.get("tradingsymbol"):
            return None
        kind = str(r.get("order_type") or "").upper()
        side = str(r.get("transaction_type") or "BUY").lower()
        return {
            "id": str(r.get("order_id") or ""), "ticker": ticker,
            "side": "sell" if side.startswith("s") else "buy",
            "type": self.APP_TYPES.get(kind, "market"),
            "qty": self._to_units(row, num(r.get("quantity"), 0)),
            "filledQty": self._to_units(row, num(r.get("filled_quantity", r.get("filled")), 0)),
            "limitPrice": num(r.get("price")), "stopPrice": num(r.get("trigger_price")),
            "avgFillPrice": num(r.get("average_price")),
            "status": self.map_status(r.get("status")),
            "variety": str(r.get("variety") or "regular"),
            "message": r.get("status_message") or r.get("reason"),
            "duration": "ioc" if str(r.get("validity") or "").upper() == "IOC" else "day",
            "fields": {"product": str(r.get("product") or "MIS")},
            "updatedMs": self._ms(r.get("exchange_timestamp") or r.get("order_timestamp")),
        }

    def _wire_position(self, r):
        ticker, row = self._app_symbol(r.get("exchange"), r.get("tradingsymbol"))
        if not r.get("tradingsymbol"):
            return None
        qty = self._to_units(row, num(r.get("quantity"), 0))
        if not qty:
            return None
        return {
            "id": ticker, "ticker": ticker, "side": "sell" if qty < 0 else "buy",
            "qty": abs(qty), "avgPrice": num(r.get("average_price"), 0) or 0,
            "last": num(r.get("last_price")), "unrealizedPnl": num(r.get("pnl")),
            "realizedPnl": None, "takeProfit": None, "stopLoss": None,
        }

    def _wire_holding(self, r):
        """A Kite delivery holding → one row of the API 7 Holdings page."""
        if not getattr(self, "_holding_row_logged", False):
            self.log(f"holding row: keys={sorted(r.keys())} qty={r.get('quantity')} "
                     f"t1={r.get('t1_quantity')} symbol={r.get('tradingsymbol')} product={r.get('product')}")
            self._holding_row_logged = True
        if not r.get("tradingsymbol"):
            return None
        ticker, _row = self._app_symbol(r.get("exchange"), r.get("tradingsymbol"))
        # Kite reports a T1 (bought today, not yet settled) holding with quantity 0 and the stock in t1_quantity.
        qty = (num(r.get("quantity")) or num(r.get("t1_quantity"))
               or num(r.get("opening_quantity")) or num(r.get("holding_quantity")) or 0)
        if not qty:
            return None
        avg = num(r.get("average_price")) or num(r.get("price")) or 0
        live = self.quotes.get(ticker) or {}
        last = num(live.get("price")) or num(r.get("last_price")) or avg
        invested, value = qty * avg, qty * last
        pnl = num(r.get("pnl"))
        if pnl is None:
            pnl = value - invested
        pct = (pnl / invested * 100) if invested else None
        return {
            "id": ticker, "symbol": ticker, "qty": qty,
            "product": str(r.get("product") or "").upper(),
            "avgPrice": round(avg, 2), "last": round(last, 2),
            "invested": round(invested, 2), "value": round(value, 2),
            "pnl": round(pnl, 2), "pnlPct": round(pct, 2) if pct is not None else None,
            "t1Qty": num(r.get("t1_quantity"), 0) or 0,
            "isin": r.get("isin"),
        }

    @staticmethod
    def holdings_page(rows):
        """The broker's own Holdings tab (Plugin API 7), in `broker.state.pages`."""
        return {
            "id": "holdings", "title": "Holdings", "counter": True,
            "tables": [{
                "id": "main",
                "columns": [
                    {"id": "symbol", "label": "Symbol", "format": "symbol"},
                    {"id": "qty", "label": "Quantity", "format": "qty"},
                    {"id": "product", "label": "Product"},
                    {"id": "avgPrice", "label": "Avg price", "format": "price"},
                    {"id": "last", "label": "Last price", "format": "price"},
                    {"id": "invested", "label": "Invested", "format": "money"},
                    {"id": "value", "label": "Current value", "format": "money"},
                    {"id": "pnl", "label": "Unrealized P&L", "format": "pnl"},
                    {"id": "pnlPct", "label": "Unrealized P&L %", "format": "pnlPercent"},
                    {"id": "t1Qty", "label": "T1 qty", "format": "qty", "hidden": True},
                    {"id": "isin", "label": "ISIN", "hidden": True},
                ],
                "sort": {"column": "value", "asc": False},
                "rows": rows,
            }],
        }

    def _wire_execution(self, r):
        ticker, row = self._app_symbol(r.get("exchange"), r.get("tradingsymbol"))
        if not r.get("tradingsymbol"):
            return None
        side = str(r.get("transaction_type") or "BUY").lower()
        return {
            "id": str(r.get("trade_id") or ""), "orderId": str(r.get("order_id") or ""),
            "ticker": ticker, "side": "sell" if side.startswith("s") else "buy",
            "qty": self._to_units(row, num(r.get("quantity"), 0)),
            "price": num(r.get("average_price"), 0) or 0,
            "timeMs": self._ms(r.get("fill_timestamp") or r.get("order_timestamp")),
            "commission": None, "realizedPnl": num(r.get("pnl")),
        }

    def _refresh_books(self):
        """Funds, positions, orders and trades from /oms; status transitions become order events."""
        margins = self._json("/oms/user/margins")
        books = self._json("/oms/portfolio/positions")
        orders = self._json("/oms/orders")
        trades = self._json("/oms/trades")
        try:
            holdings_data = self._json("/oms/portfolio/holdings")
            if not getattr(self, "_holdings_shape_logged", False):
                shape = (list(holdings_data)[:8] if isinstance(holdings_data, dict)
                         else f"{len(holdings_data)} rows")
                self.log(f"holdings response: {type(holdings_data).__name__} [{shape}]")
                self._holdings_shape_logged = True
            # Kite's personal OMS returns `data` as a bare list here (Kite Connect wraps it in {"holdings": [...]}).
            if isinstance(holdings_data, dict):
                rows = holdings_data.get("holdings") or holdings_data.get("data") or []
            else:
                rows = holdings_data or []
            holdings = [h for h in (self._wire_holding(r) for r in rows) if h]
        except AuthError:
            raise  # an expired enctoken must still reach the refresh prompt
        except (PluginError, AttributeError, TypeError, ValueError) as e:
            self.log(f"holdings not read: {e}", "warn")
            holdings = None  # keep the last good page; the other books are still fine
        events = []
        with self.lock:
            was_connected = self.connected_broker
            previous = {oid: o.get("status") for oid, o in self.orders.items()}
            held_before = [h.get("symbol") for h in self.holdings]
            self.margins = margins
            self.positions = [p for p in (self._wire_position(r) for r in (books.get("net") or [])) if p]
            if holdings is not None:
                self.holdings = holdings
            self.orders = {o["id"]: o for o in (self._wire_order(r) for r in orders) if o}
            self.executions = [e for e in (self._wire_execution(r) for r in trades) if e]
            self.connected_broker = True
            for order_id, order in self.orders.items():
                was = previous.get(order_id)
                if was in (None, "placing", "working") and order["status"] == "filled":
                    kind = "executed"
                elif was in (None, "placing", "working") and order["status"] == "cancelled":
                    kind = "cancelled"
                elif was in (None, "placing", "working") and order["status"] == "rejected":
                    kind = "rejected"
                else:
                    continue
                events.append({"kind": kind, "ticker": order["ticker"], "side": order["side"],
                               "type": order["type"], "qty": order["qty"],
                               "price": order.get("avgFillPrice"), "message": order.get("message")})
        if holdings is not None and [h.get("symbol") for h in self.holdings] != held_before:
            self.send_watchlists()  # the Holdings list follows today's delivery holdings
        if not was_connected:
            self.notice("info", "Broker connected — account books are live")
            self.toast("info", "Broker connected — account books are live")
        self.last_poll = time.time()
        self.log(f"books read: {len(self.positions)} positions, {len(self.holdings)} holdings, "
                 f"{len(self.orders)} orders, {len(self.executions)} trades"
                 f"{'; events: ' + ','.join(e['kind'] for e in events) if events else ''}")
        p = self.plugin
        if p and p.alive:
            for event in events:
                p.notify("broker.event", event)
        return events

    def _poll_loop(self):
        while True:
            try:
                self.on_tick()
            except Exception as e:  # noqa: BLE001 - a poll failure must not kill the thread
                self.log(self.clean(str(e)), "warn")
            time.sleep(2)

    def on_tick(self):
        """Every second while positions/orders are open, otherwise every ten seconds."""
        if not self.broker_enabled or not self.enctoken:
            return []
        now = time.time()
        with self.lock:
            work = bool(self.positions) or any(o["status"] in ("placing", "working")
                                               for o in self.orders.values())
            due = now - self.last_poll >= (2 if work else 10)
            fresh = self.last_poll == 0.0
        if not (fresh or due):
            return []
        try:
            events = self._refresh_books()
        except PluginError as e:
            self.log(str(e), "warn")
            if e.code == -32005:
                self.notice("warn", str(e))  # rate limited: the app waits longer between retries
            self.last_poll = now
            return []
        # Always repush: the app otherwise keeps the initial "not connected / zero balance" state on an idle
        # account with no orders or positions, which is exactly when the user opens the account manager.
        if self.plugin and self.plugin.alive:
            self.plugin.push_state()
        return events

    def on_order_update(self, data):
        """A Kite order postback on the ticker: update the order and notify without waiting for the poll."""
        order_id = str(data.get("order_id") or "")
        if not order_id:
            return
        ticker, row = self._app_symbol(data.get("exchange"), data.get("tradingsymbol"))
        status = self.map_status(data.get("status"))
        with self.lock:
            previous_order = self.orders.get(order_id)
            was = previous_order.get("status") if previous_order else None
            order = dict(previous_order or {
                "id": order_id, "ticker": ticker, "side": "buy", "type": "market",
                "qty": self._to_units(row, num(data.get("quantity"), 0)), "filledQty": 0,
                "limitPrice": num(data.get("price")), "stopPrice": num(data.get("trigger_price")),
                "fields": {"product": str(data.get("product") or "MIS")},
            })
            order.update(status=status,
                         filledQty=self._to_units(row, num(data.get("filled_quantity"), 0)),
                         avgFillPrice=num(data.get("average_price")),
                         message=data.get("status_message"), updatedMs=int(time.time() * 1000))
            self.orders[order_id] = order
        self.last_poll = 0
        self.log(f"order postback {order_id}: {status} filled={order['filledQty']:g} avg={order.get('avgFillPrice')}")
        p = self.plugin
        if p and p.alive:
            kind = {"filled": "executed", "cancelled": "cancelled", "rejected": "rejected"}.get(status)
            if kind and was != status:
                p.notify("broker.event", {"kind": kind, "ticker": order["ticker"], "side": order["side"],
                                          "type": order["type"], "qty": order["qty"],
                                          "price": order.get("avgFillPrice"), "message": order.get("message")})
            p.push_state()

    def state(self):
        """The whole account, as broker.state sends it."""
        with self.lock:
            margins = self.margins or {}
            positions = [dict(x) for x in self.positions]
            holdings = [dict(x) for x in self.holdings]
            orders = sorted(self.orders.values(), key=lambda o: o.get("updatedMs") or 0)[-200:]
            executions = list(self.executions[-200:])
            connected = self.connected_broker
        equity = margins.get("equity") or {}
        commodity = margins.get("commodity") or {}
        cash = sum(num((s.get("available") or {}).get("cash"), 0) for s in (equity, commodity))
        collateral = sum(num((s.get("available") or {}).get("collateral"), 0) for s in (equity, commodity))
        debits = sum(num((s.get("utilised") or {}).get("debits"), 0) for s in (equity, commodity))
        net = sum(num(s.get("net"), 0) for s in (equity, commodity))
        # Kite's available.cash intermittently reports 0 on a funded account while net stays right.
        balance = (net + debits - collateral) if net else cash
        unrealized = sum(num(p.get("unrealizedPnl"), 0) or 0 for p in positions)
        account = {
            "id": self.user_id or "ZERODHA", "name": "Zerodha", "currency": "INR", "isDemo": False,
            "balance": round(balance, 2), "equity": round(balance + unrealized, 2),
            "unrealizedPnl": round(unrealized, 2), "realizedPnl": 0,
            "marginUsed": round(debits, 2), "marginFree": round(balance, 2),
            "values": [{"label": "Net", "value": f"{net:.2f}"}, {"label": "Collateral", "value": f"{collateral:.2f}"}],
        }
        default = (self.settings.get("product") or "MIS").upper()
        if default not in self.PRODUCTS:
            default = "MIS"
        return {
            "connected": connected,
            "accounts": [{"id": account["id"], "name": account["name"]}],
            "account": account,
            "positionMode": "netting",
            "capabilities": {
                "orderTypes": ["market", "limit", "stop", "stopLimit"],
                "brackets": False, "positionBrackets": False, "partialClose": True, "modifyOrders": True,
                "ticketFields": [{"id": "product", "label": "Product", "default": default,
                                  "options": [{"value": p, "label": label} for p, label in self.PRODUCTS.items()]}],
                "durations": [{"id": "day", "label": "Day", "default": True},
                              {"id": "ioc", "label": "Immediate or Cancel", "orderTypes": ["market", "limit"]}],
            },
            "positions": positions, "orders": orders, "executions": executions,
            "pages": [self.holdings_page(holdings)],  # Plugin API 7: the broker's own account tabs
        }


# ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
# 2. The connection to OpenTrader (the template's, unchanged except for the finer-grained source locking).
# ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def connection():
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
        source.plugin = self
        self.sock = None
        self.send_lock = threading.Lock()
        self.alive = False

    def send(self, message):
        line = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self.send_lock:
            self.sock.sendall(line)

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def send_many(self, messages):
        """One socket write for a whole frame's notifications — far fewer syscalls on a large watchlist."""
        if not messages:
            return
        payload = "".join(json.dumps(m, separators=(",", ":")) + "\n" for m in messages).encode("utf-8")
        with self.send_lock:
            self.sock.sendall(payload)

    def log(self, message, level="info", category="api"):
        self.source.log(message, level, category)

    def push_quote(self, ticker):
        q = self.source.quote(ticker)
        if q:
            self.notify("data.quote", q)

    def push_watchlists(self):
        if self.source.send_watchlists():
            self.log(f"data.watchlists: {len(self.source.merged_watchlists())} lists")

    def push_state(self):
        state = self.source.state()
        self.source.log(f"broker.state: connected={state['connected']} "
                        f"balance={state['account']['balance']} equity={state['account']['equity']} "
                        f"positions={len(state['positions'])} orders={len(state['orders'])}", "debug")
        self.notify("broker.state", state)

    def events(self, events):
        for event in events:
            self.notify("broker.event", event)
        if events:
            self.push_state()

    def handle(self, method, p):
        s = self.source
        if method == "ping":
            return {}
        if method == "data.search":
            return {"symbols": s.search(p.get("query", ""))[:50]}
        if method == "data.symbolInfo":
            return s.symbol_info(p["ticker"])
        if method == "data.bars":
            resolution = p["resolution"]
            frm, to, count_back = int(p["from"]), int(p["to"]), int(p.get("countBack", 2000))
            bars = s.bars(p["ticker"], resolution, frm, to, count_back)
            window = (f"{datetime.fromtimestamp(frm, TZ_IST):%d/%m/%Y}"
                      f"–{datetime.fromtimestamp(to, TZ_IST):%d/%m/%Y}")
            self.log(f"data.bars {p['ticker']} {resolution}: {len(bars)} bars "
                     f"({window}, countBack={count_back})")
            return {"bars": bars}
        if method == "data.events":
            # The app asks for events on any chart symbol. Only stocks can have corporate actions on Kite — answer
            # derivatives and indices at once, without a master lookup, a REST call or a log line.
            ticker = p["ticker"]
            row = s.instruments.row(ticker)
            if row is None or row["type"] != "stock":
                return {"events": []}
            events = s.events_for(ticker)
            self.log(f"data.events {ticker}: {len(events)} events")
            return {"events": events}
        if method == "broker.getState":
            return s.state()
        if method == "broker.resolveSymbol":
            return {"ticker": s.resolve_symbol(p.get("symbol"), p.get("ticker"))}
        if method == "broker.instrument":
            return s.instrument(p["ticker"])
        if method == "broker.selectAccount":
            return {}
        if method == "broker.place":
            order_id, events = s.place(p)
            self.events(events)
            return {"orderId": order_id}
        if method == "broker.modify":
            self.events(s.modify(p))
            return {}
        if method == "broker.cancel":
            self.events(s.cancel(p["orderId"]))
            return {}
        if method == "broker.closePosition":
            self.events(s.close_position(p["positionId"], p.get("qty")))
            return {}
        if method == "broker.setPositionBrackets":
            s.set_position_brackets(p["positionId"], p.get("takeProfit"), p.get("stopLoss"))
            return {}
        raise PluginError(f"Unknown method {method}", -32601)

    def on_request(self, message):
        try:
            result = self.handle(message["method"], message.get("params") or {})
            self.send({"jsonrpc": "2.0", "id": message["id"], "result": result})
        except AuthError as e:
            self.send({"jsonrpc": "2.0", "id": message["id"],
                       "error": {"code": -32000, "message": self.source.clean(str(e))}})
            self.source.auth_failed()
        except PluginError as e:
            if message.get("method", "").startswith("broker."):
                self.log(f"{message['method']} failed: {e}", "warn")
            self.send({"jsonrpc": "2.0", "id": message["id"],
                       "error": {"code": e.code, "message": self.source.clean(str(e))}})
        except (KeyError, TypeError, ValueError) as e:
            self.send({"jsonrpc": "2.0", "id": message["id"],
                       "error": {"code": -32602, "message": f"Bad parameters: {self.source.clean(str(e))}"}})
        except Exception as e:  # noqa: BLE001 - any failure is answered, never left hanging
            self.send({"jsonrpc": "2.0", "id": message["id"],
                       "error": {"code": -32000, "message": self.source.clean(str(e))}})

    def on_notification(self, method, p):
        if method == "plugin.configure":
            self.source.configure(p.get("settings", {}), p.get("provides"))
            if self.source.broker_enabled:
                self.push_state()
        elif method == "data.subscribe":
            self.source.set_subscriptions(p.get("tickers", []))
        elif method in ("broker.watchQuote", "broker.watchDepth"):
            self.source.set_watch(method, p.get("tickers", []))
        elif method == "plugin.promptAnswer":
            self.source.on_prompt_answer(p.get("id"), p.get("value"))
        # data.watchTicks: no tick charts in this plugin.

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
        app_name = welcome.get("result", {}).get("app", "OpenTrader")
        app_api = welcome.get("result", {}).get("api")
        self.log(f"Connected to {app_name} (Plugin API {app_api})")
        self.source.toast("info", f"Connected to {app_name} — Plugin API {app_api}, preparing data")
        self.push_watchlists()
        for line in reader:
            if not line.strip():
                continue
            message = json.loads(line)
            if "method" in message and "id" in message:
                threading.Thread(target=self.on_request, args=(message,), daemon=True).start()
            elif "method" in message:
                self.on_notification(message["method"], message.get("params") or {})
        self.alive = False
        if self.source.ws is not None:
            self.source.ws.stop.set()

    def run(self):
        """Started by the app: end when the connection ends (the app starts us again). Run by hand: retry."""
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
    Plugin(ZerodhaSource()).run()
