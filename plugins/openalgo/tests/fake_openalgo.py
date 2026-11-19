"""An honest fake OpenAlgo deployment for the plugin's regression suites.

Honest means it behaves like the real server, not like a mock that says yes to anything:

- `/api/v1/history` serves the whole requested range in one call (the real API does), so a
  suite that counts requests fails when the plugin mis-sizes a window. `history_max_days`
  makes it truncate the way a capped deployment does, on purpose, and `history_floor` ends
  its history early.
- A bodyless GET that carries `Content-Type: application/json` is refused with HTTP 400
  and the message a local Flask/Werkzeug instance answers — the 0.1.17 instruments bug.
- `/api/v1/symbol` answers HTTP 429 when `busy_symbol` is set (the rate limit that turned
  into "Unknown symbol"), and "Symbol not found" for a symbol it does not list.
- The WebSocket speaks the real handshake and takes masked client frames, so the plugin's
  own stream code runs unchanged; `push_tick` sends a market_data message on demand.

Every call is recorded (the API key is redacted in what is kept) with its method, query,
headers and body.
"""

import base64
import hashlib
import json
import socket
import struct
import threading
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IST = timezone(timedelta(hours=5, minutes=30))
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _row(symbol, name, exchange, kind, lotsize="1", tick="0.05", **extra):
    row = {
        "symbol": symbol,
        "brsymbol": f"{symbol}-EQ" if kind == "EQ" else symbol,
        "name": name,
        "exchange": exchange,
        "instrumenttype": kind,
        "lotsize": lotsize,
        "tick_size": tick,
    }
    row.update(extra)
    return row


MASTERS = {
    "NSE": [
        _row("RELIANCE", "RELIANCE INDUSTRIES", "NSE", "EQ"),
        _row("TCS", "TATA CONSULTANCY SERVICES", "NSE", "EQ"),
        _row("IDEA", "VODAFONE IDEA", "NSE", "EQ"),
    ],
    "NFO": [
        _row("NIFTY25NOVFUT", "NIFTY", "NFO", "FUT", lotsize="75", expiry="25-Nov-26"),
        _row("NIFTY25NOV25000CE", "NIFTY", "NFO", "CE", lotsize="75", expiry="25-Nov-26"),
    ],
}


def ist_day(day):
    """A "YYYY-MM-DD" date (the API's date fields are the market's days) as IST midnight."""
    return datetime.strptime(str(day), "%Y-%m-%d").replace(tzinfo=IST)


def _bar(at, i):
    """One deterministic bar: the day-synthesis assertions read these numbers."""
    return {
        "timestamp": int(at.timestamp() * 1000),
        "open": 100.0 + i,
        "high": 110.0 + i,
        "low": 90.0 + i,
        "close": 105.0 + i,
        "volume": 10.0,
    }


def _frame(payload, opcode=0x1):
    """A server→client WebSocket frame (never masked)."""
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += struct.pack(">H", n)
    else:
        header.append(127)
        header += struct.pack(">Q", n)
    return bytes(header) + payload


class FakeWs:
    """The stream: the real handshake, the plugin's masked frames recorded, ticks pushed."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}/ws"
        self.frames = []  # (opcode, payload bytes) the plugin sent
        self.connected = threading.Event()
        self.lock = threading.Lock()
        self.conn = None
        self._alive = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while self._alive:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        while b"\r\n\r\n" not in buf:
            try:
                part = conn.recv(4096)
            except OSError:
                part = b""
            if not part:
                conn.close()
                return
            buf += part
        head = buf.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
        key = ""
        for line in head.split("\r\n")[1:]:
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        conn.sendall((
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode())
        with self.lock:
            self.conn = conn
        self.connected.set()
        rest = buf.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in buf else b""
        buf = rest
        while True:
            while True:
                frame, buf = self._frame(buf)
                if frame is None:
                    break
                with self.lock:
                    self.frames.append(frame)
            try:
                part = conn.recv(65536)
            except OSError:
                return
            if not part:
                return
            buf += part

    @staticmethod
    def _frame(buf):
        """One complete frame from the buffer: ((opcode, payload), rest) or (None, buf)."""
        if len(buf) < 2:
            return None, buf
        opcode = buf[0] & 0x0F
        masked = buf[1] & 0x80
        n = buf[1] & 0x7F
        at = 2
        if n == 126:
            if len(buf) < 4:
                return None, buf
            n = struct.unpack(">H", buf[2:4])[0]
            at = 4
        elif n == 127:
            if len(buf) < 10:
                return None, buf
            n = struct.unpack(">Q", buf[2:10])[0]
            at = 10
        mask = b""
        if masked:
            if len(buf) < at + 4:
                return None, buf
            mask = buf[at:at + 4]
            at += 4
        if len(buf) < at + n:
            return None, buf
        payload = buf[at:at + n]
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return (opcode, payload), buf[at + n:]

    def send_json(self, obj):
        with self.lock:
            conn = self.conn
        if conn is None:
            raise RuntimeError("the fake stream has no client yet")
        conn.sendall(_frame(json.dumps(obj).encode("utf-8")))

    def push_tick(self, exchange, symbol, ltp, **fields):
        """One market_data message, the real stream's shape."""
        data = {"open": ltp, "high": ltp, "low": ltp, "ltp": ltp, "volume": 1000, "close": ltp,
                "prev_close": ltp}
        data.update(fields)
        self.send_json({"type": "market_data", "symbol": symbol, "exchange": exchange, "data": data})

    def actions(self):
        """The JSON actions the plugin sent (`authenticate`, `subscribe`, …)."""
        out = []
        with self.lock:
            frames = list(self.frames)
        for opcode, payload in frames:
            if opcode != 0x1:
                continue
            try:
                out.append(json.loads(payload.decode("utf-8")))
            except ValueError:
                continue
        return out

    def close(self):
        self._alive = False
        with self.lock:
            conn, self.conn = self.conn, None
        for sock in (conn, self.sock):
            try:
                if sock is not None:
                    sock.close()
            except OSError:
                pass


class FakeOpenAlgo:
    def __init__(self, masters=None):
        self.masters = masters or MASTERS
        self.symbols = {}  # (exchange, symbol) -> row: what the live /symbol answers
        self.busy_symbol = False  # /symbol answers HTTP 429 (the rate limit)
        self.history_max_days = None  # a deployment whose one call is capped to the tail
        self.history_floor = None  # "YYYY-MM-DD": nothing older is served
        self.bar_cap = None  # a hard row cap per call (a truncating deployment)
        self.quotes = {}  # ticker -> the REST /quotes fields
        self.intervals = {"minutes": ["1m", "3m", "5m", "15m"], "hours": ["1h"],
                          "seconds": ["5s"], "days": ["D"], "weeks": ["W"], "months": ["M"]}
        self.funds = {"availablecash": "250000.00", "m2munrealized": "1500.00",
                      "m2mrealized": "250.00"}
        self.orderbook = []
        self.positionbook = []
        self.tradebook = []
        self.orders_placed = []
        self.order_seq = 0
        self.requests = []  # every call, the key redacted
        self.counts = Counter()
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.fake = self
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.ws = FakeWs()

    # ── recorded traffic ──
    def record(self, method, path, query, headers, body):
        safe = {k: v for k, v in query.items() if k != "apikey"}
        with self.lock:
            self.requests.append({
                "method": method, "path": path, "query": safe,
                "headers": {k.lower(): v for k, v in headers.items()}, "body": body,
            })
            self.counts[(method, path)] += 1

    def count(self, path, method=None):
        with self.lock:
            return sum(n for (m, p), n in self.counts.items()
                       if p == path and (method is None or m == method))

    def sent(self, path, method=None):
        with self.lock:
            return [r for r in self.requests
                    if r["path"] == path and (method is None or r["method"] == method)]

    # ── history ──
    def history_rows(self, interval, start_date, end_date):
        start, end = ist_day(start_date), ist_day(end_date)
        if self.history_floor:
            start = max(start, ist_day(self.history_floor))
        if self.history_max_days:  # the tail only: one call cannot reach past the cap
            earliest = end - timedelta(days=self.history_max_days - 1)
            start = max(start, earliest)
        rows, i, day = [], 0, start
        while day <= end:
            if day.weekday() < 5:
                if interval.upper() in ("D", "W", "M"):
                    rows.append(_bar(day, i))
                    i += 1
                else:
                    step = _step_seconds(interval)
                    at, close = day.replace(hour=9, minute=15), day.replace(hour=15, minute=30)
                    while at < close:
                        rows.append(_bar(at, i))
                        i += 1
                        at += timedelta(seconds=step)
            day += timedelta(days=1)
        if self.bar_cap:
            rows = rows[:self.bar_cap]
        return rows

    def expected_bars(self, interval, start_date, end_date):
        """How many bars the honest server serves for that window."""
        return len(self.history_rows(interval, start_date, end_date))

    def close(self):
        try:
            self.httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass
        self.httpd.server_close()
        self.ws.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _step_seconds(interval):
    iv = interval.lower()
    if iv.endswith("s"):
        return int(iv[:-1])
    if iv.endswith("h"):
        return int(iv[:-1]) * 3600
    if iv.endswith("m"):
        return int(iv[:-1]) * 60
    raise ValueError(f"the fake server has no grid for {interval}")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # a suite's output is the test's, not the server's

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def _handle(self, method):
        fake = self.server.fake
        parts = urllib.parse.urlsplit(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            body = {}
        fake.record(method, parts.path, query, self.headers, body)
        # What a local Flask/Werkzeug answers a bodyless GET that declares a JSON body type.
        if method == "GET" and "json" in (self.headers.get("Content-Type") or "").lower():
            return self._send(400, {"message": "The browser (or proxy) sent a request that "
                                               "this server could not understand."})
        route = {
            "/api/v1/instruments": self._instruments,
            "/api/v1/symbol": self._symbol,
            "/api/v1/search": self._search,
            "/api/v1/quotes": self._quotes,
            "/api/v1/history": self._history,
            "/api/v1/intervals": self._intervals,
            "/api/v1/market/holidays": self._holidays,
            "/api/v1/funds": self._funds,
            "/api/v1/orderbook": self._orderbook,
            "/api/v1/positionbook": self._positionbook,
            "/api/v1/tradebook": self._tradebook,
            "/api/v1/placeorder": self._place,
            "/api/v1/modifyorder": self._modify,
            "/api/v1/cancelorder": self._cancel,
        }.get(parts.path)
        if route is None:
            return self._send(404, {"status": "error", "message": f"no route {parts.path}"})
        return route(fake, query, body)

    def _send(self, status, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _ok(data):
        return {"status": "success", "data": data}

    def _instruments(self, fake, query, body):
        if not query.get("apikey"):
            return self._send(200, {"status": "error", "message": "API key missing"})
        exchange = str(query.get("exchange") or "").upper()
        return self._send(200, self._ok(fake.masters.get(exchange, [])))

    def _symbol(self, fake, query, body):
        if fake.busy_symbol:
            return self._send(429, {"status": "error",
                                    "message": "Rate limit exceeded. Try again later."})
        key = (str(body.get("exchange") or "").upper(), str(body.get("symbol") or ""))
        row = fake.symbols.get(key)
        if row is None:
            return self._send(200, {"status": "error", "message": "Symbol not found"})
        return self._send(200, self._ok(row))

    def _search(self, fake, query, body):
        q = str(body.get("query") or "").upper()
        rows = [row for exchange, segment in fake.masters.items() for row in segment
                if q in str(row["symbol"]).upper() or q in str(row["name"]).upper()]
        return self._send(200, self._ok(rows[:500]))

    def _quotes(self, fake, query, body):
        exchange = str(body.get("exchange") or "").upper()
        symbol = str(body.get("symbol") or "")
        q = fake.quotes.get(f"{exchange}:{symbol}")
        if q is None:
            return self._send(200, {"status": "error", "message": "No quotes"})
        return self._send(200, self._ok(q))

    def _history(self, fake, query, body):
        try:
            rows = fake.history_rows(str(body.get("interval") or "1m"),
                                     str(body.get("start_date") or ""),
                                     str(body.get("end_date") or ""))
        except (TypeError, ValueError) as e:
            return self._send(200, {"status": "error", "message": str(e)})
        return self._send(200, self._ok(rows))

    def _intervals(self, fake, query, body):
        return self._send(200, self._ok(fake.intervals))

    def _holidays(self, fake, query, body):
        return self._send(200, self._ok([]))

    def _funds(self, fake, query, body):
        return self._send(200, self._ok(fake.funds))

    def _orderbook(self, fake, query, body):
        return self._send(200, self._ok(fake.orderbook))

    def _positionbook(self, fake, query, body):
        return self._send(200, self._ok(fake.positionbook))

    def _tradebook(self, fake, query, body):
        return self._send(200, self._ok(fake.tradebook))

    def _place(self, fake, query, body):
        with fake.lock:
            fake.order_seq += 1
            order_id = f"OID{fake.order_seq}"
            fake.orders_placed.append(dict(body))
            fake.orderbook.append({
                "orderid": order_id,
                "symbol": body.get("symbol"),
                "exchange": body.get("exchange"),
                "action": body.get("action"),
                "pricetype": body.get("pricetype"),
                "quantity": body.get("quantity"),
                "price": body.get("price"),
                "order_status": "open",
                "average_price": 0,
                "updatetime": int(time.time() * 1000),
            })
        return self._send(200, {"status": "success", "orderid": order_id,
                                "data": {"orderid": order_id}})

    def _modify(self, fake, query, body):
        with fake.lock:
            for row in fake.orderbook:
                if row["orderid"] == body.get("orderid"):
                    row.update(quantity=body.get("quantity"), price=body.get("price"),
                               updatetime=int(time.time() * 1000))
        return self._send(200, {"status": "success", "data": {}})

    def _cancel(self, fake, query, body):
        with fake.lock:
            for row in fake.orderbook:
                if row["orderid"] == body.get("orderid"):
                    row.update(order_status="cancelled", updatetime=int(time.time() * 1000))
        return self._send(200, {"status": "success", "data": {}})
