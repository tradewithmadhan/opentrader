"""NSE public reports over one browser-like session.

NSE refuses a bare GET to its report hosts: the site sets cookies on its own pages that the report
endpoints expect. This module owns that session so every report shares the same cookies, and adding
another NSE API is one line in REPORTS. Standard library only, so the plugin's build stays unchanged.
"""
import csv
import gzip
import http.client
import io
import json
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ_IST = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36")
# One home for the whole session: NSE's .in site sets the cookies the reports accept, including the
# nsearchives.nseindia.com CSVs (verified live).
HOME = "https://www.nseindia.in/"

# Every NSE report the plugin reads. A new endpoint is added here, with its headers when they differ.
REPORTS = {
    "equity": "https://nsearchives.nseindia.in/content/equities/EQUITY_L.csv",
    "sme": "https://nsearchives.nseindia.in/emerge/corporates/content/SME_EQUITY_L.csv",
    "price_band_hitters": "https://www.nseindia.in/api/live-analysis-price-band-hitter",
    "event_calendar": "https://www.nseindia.in/api/event-calendar",
    "volume_gainers": "https://www.nseindia.in/api/live-analysis-volume-gainers",
    "corporate_announcements": "https://www.nseindia.in/api/corporate-announcements",
    "security_list": "https://nsearchives.nseindia.in/content/equities/sec_list_{}.csv",
}
EVENT_CALENDAR_URL = REPORTS["event_calendar"]
CORPORATE_URL = REPORTS["corporate_announcements"]
SECURITY_LIST_URL = REPORTS["security_list"]
# The .com archive mirrors still answer on their own, but after a .in API call in the same session they can
# return nothing. Keep .in as the common host and use these only if it fails.
FALLBACKS = {
    "https://nsearchives.nseindia.in/content/equities/EQUITY_L.csv":
        "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://nsearchives.nseindia.in/emerge/corporates/content/SME_EQUITY_L.csv":
        "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
}
REPORT_HEADERS = {
    "equity": {"Accept": "text/csv,*/*", "Referer": HOME},
    "sme": {"Accept": "text/csv,*/*", "Referer": HOME},
    "price_band_hitters": {"Accept": "application/json,text/plain,*/*",
                           "Referer": "https://www.nseindia.in/"},
    "event_calendar": {"Accept": "application/json,text/plain,*/*",
                       "Referer": "https://www.nseindia.in/"},
    "volume_gainers": {"Accept": "application/json,text/plain,*/*",
                       "Referer": "https://www.nseindia.in/"},
    "corporate_announcements": {"Accept": "application/json,text/plain,*/*",
                                "Referer": "https://www.nseindia.in/"},
    "security_list": {"Accept": "text/csv,*/*", "Referer": HOME},
}
# Pages whose cookies the report hosts look for, tried in order until one sets them.
WARM_URLS = (HOME, "https://www.nseindia.com/")
RETRY_STATUS = {401, 403, 429}


class Session:
    """A tiny cookie jar: warm the site once, then send its cookies on every report GET."""

    def __init__(self, timeout=15):
        self.timeout = timeout
        self.cookies = {}
        self.warmed = False
        self.last_status = 0  # status of the latest report GET, for the plugin's log

    @staticmethod
    def _cookie_pair(value):
        name, _, data = value.partition("=")
        return name.strip(), data.split(";", 1)[0].strip()

    def _once(self, url, headers=None, redirects=3):
        parts = urllib.parse.urlsplit(url)
        if not parts.hostname or parts.scheme not in ("http", "https"):
            return 0, ""
        secure = parts.scheme == "https"
        conn = (http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=self.timeout) if secure
                else http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=self.timeout))
        request_headers = {
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }
        if self.cookies:
            request_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in sorted(self.cookies.items()))
        request_headers.update(headers or {})
        try:
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            conn.request("GET", path, headers=request_headers)
            response = conn.getresponse()
            self.last_status = response.status
            raw = response.read()
            for key, value in response.getheaders():
                if key.lower() != "set-cookie":
                    continue
                name, data = self._cookie_pair(value)
                if not name:
                    continue
                if "; max-age=0" in value.lower() or "; expires=thu, 01 jan 1970" in value.lower():
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = data
            if response.status in (301, 302, 303, 307, 308) and redirects:
                location = response.getheader("Location")
                if location:
                    return self._once(urllib.parse.urljoin(url, location), headers, redirects - 1)
            if (response.getheader("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return response.status, raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - optional enrichment must never break symbol info
            self.last_status = 0
            return 0, ""
        finally:
            conn.close()

    def warm(self):
        if self.warmed and self.cookies:
            return
        self.warmed = True
        for url in WARM_URLS:
            self._once(url, {"Accept": "text/html,application/xhtml+xml,*/*"})
            if self.cookies:
                return

    def get(self, url, headers=None, retries=1):
        """Body of a report, or '' when it cannot be read. A refusal re-warms the session once."""
        for attempt in range(retries + 1):
            self.warm()
            status, body = self._once(url, headers)
            if status == 200:
                return body
            if status in RETRY_STATUS and attempt < retries:
                self.cookies.clear()
                self.warmed = False
                time.sleep(0.5)
                continue
            return ""
        return ""


class Client:
    """Report registry + session, with a per-report single-flight for callers on several threads."""

    def __init__(self, timeout=15):
        self.session = Session(timeout)
        self._locks = {}
        self._guard = threading.Lock()

    def get(self, name, url=None, headers=None, timeout=None):
        """Fetch one report by registry name (or an explicit URL). A new NSE API needs only a REPORTS entry."""
        url = url or REPORTS.get(name)
        if not url:
            return ""
        registered = REPORTS.get(name)
        fallback = FALLBACKS.get(registered) if url == registered else None
        request_headers = headers or REPORT_HEADERS.get(name)

        def fetch(target):
            text = self.session.get(target, request_headers)
            return text or (self.session.get(fallback, request_headers) if fallback else "")

        with self._guard:
            lock = self._locks.setdefault(name, threading.Lock())
        with lock:
            if timeout is not None and timeout != self.session.timeout:
                old, self.session.timeout = self.session.timeout, timeout
                try:
                    return fetch(url)
                finally:
                    self.session.timeout = old
            return fetch(url)


def listing_date(text):
    """A listing date as UNIX seconds at IST midnight, or None."""
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d"):
        try:
            day = datetime.strptime(str(text).strip(), fmt).date()
            return int(datetime(day.year, day.month, day.day, tzinfo=TZ_IST).timestamp())
        except (ValueError, TypeError):
            pass
    return None


def parse_listings(text):
    """CSV rows → {SYMBOL: {date, isin, face, series}}."""
    out = {}
    for raw in csv.DictReader(io.StringIO(text)):
        row = {str(k).strip(): str(v or "").strip() for k, v in raw.items() if k}
        symbol = row.get("SYMBOL") or ""
        date_text = row.get("DATE OF LISTING") or row.get("DATE_OF_LISTING") or ""
        when = listing_date(date_text) if symbol else None
        if when:
            out[symbol.upper()] = {
                "date": when,
                "isin": row.get("ISIN NUMBER") or row.get("ISIN_NUMBER") or row.get("ISIN") or "",
                "face": row.get("FACE VALUE") or row.get("FACE_VALUE") or "",
                "series": row.get("SERIES") or "",
            }
    return out


def security_list_url(day):
    """The dated price-band CSV URL; `day` is a date, written ddmmyyyy as NSE names the file."""
    return SECURITY_LIST_URL.format(day.strftime("%d%m%Y"))


def previous_trading_day(day):
    """The day before `day`, skipping Saturday and Sunday (Monday gives Friday)."""
    previous = day - timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= timedelta(days=1)
    return previous


def parse_security_list(text):
    """The price-band CSV → {symbol, series, band, remarks} rows; band may be 'No Band'."""
    out = []
    for raw in csv.DictReader(io.StringIO(text)):
        symbol = str(raw.get("Symbol") or "").strip().upper()
        if not symbol:
            continue
        out.append({
            "symbol": symbol,
            "series": str(raw.get("Series") or "").strip().upper(),
            "band": str(raw.get("Band") or "").strip(),
            "remarks": str(raw.get("Remarks") or "").strip(),
        })
    return out


def event_calendar_url(index=None, from_date=None, to_date=None):
    """The calendar URL with optional segment and date window (dd-mm-yyyy, NSE's format)."""
    params = {}
    if index:
        params["index"] = index
    if from_date:
        params["from_date"] = from_date
    if to_date:
        params["to_date"] = to_date
    return EVENT_CALENDAR_URL + ("?" + urllib.parse.urlencode(params) if params else "")


def event_calendar_rows(text):
    """Unique (symbol, purpose) pairs from a calendar answer. Equities use `symbol`, SME uses `bm_symbol`."""
    try:
        rows = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    out, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol") or row.get("bm_symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        symbol = symbol.strip().upper()
        purpose = row.get("purpose") or row.get("bm_purpose") or "Event"
        purpose = str(purpose).strip() or "Event"
        if (symbol, purpose) in seen:
            continue
        seen.add((symbol, purpose))
        out.append({"symbol": symbol, "purpose": purpose})
    return out


def corporate_announcements_url(index="equities"):
    """The announcements URL for an NSE segment (`equities` or `sme`)."""
    return CORPORATE_URL + "?" + urllib.parse.urlencode({"index": index})


def parse_volume_gainers(text):
    """Tickers from the volume-gainers report, unique and sorted."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    rows = data.get("data") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    return sorted({r["symbol"].strip().upper() for r in rows
                   if isinstance(r, dict) and isinstance(r.get("symbol"), str) and r["symbol"].strip()})


def parse_corporate_announcements(text):
    """Unique (symbol, purpose, date) rows from NSE's announcements feed."""
    try:
        rows = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    out, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol") or row.get("bm_symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        symbol = symbol.strip().upper()
        purpose = str(row.get("desc") or "Announcement").strip() or "Announcement"
        when = str(row.get("an_dt") or "").strip()
        key = (symbol, purpose, when)
        if key not in seen:
            seen.add(key)
            out.append({"symbol": symbol, "purpose": purpose, "date": when})
    return out


def parse_event_calendar(text):
    """Tickers from a calendar answer, unique and sorted."""
    return sorted({row["symbol"] for row in event_calendar_rows(text)})


def parse_price_band_hitters(text):
    """Tickers in the price-band-hitter report, unique and sorted.

    The API groups rows under `upper`, `lower` and `both`, each with an `AllSec.data` list and two
    subsets of it. Only AllSec is read to avoid duplicates.
    """
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out = set()
    for side in ("upper", "lower", "both"):
        group = data.get(side)
        if not isinstance(group, dict):
            continue
        all_sec = group.get("AllSec")
        rows = all_sec.get("data") if isinstance(all_sec, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            symbol = row.get("symbol") if isinstance(row, dict) else None
            if isinstance(symbol, str) and symbol.strip():
                out.add(symbol.strip().upper())
    return sorted(out)


def load_listings(client, path, urls=None, timeout=5):
    """Today's parsed listing rows, read from cache or fetched over the shared session.

    `urls` maps report names to URLs; the caller may override the registry (the test harness points
    these at its local server).
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = None
    if data is None:
        data = {}
        for name, url in (urls or REPORTS).items():
            text = client.get(name, url=url, timeout=timeout)
            if text:
                data.update(parse_listings(text))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
        except OSError:
            pass
    return data
