"""An honest fake Kite deployment for the Zerodha plugin's regression suites.

Honest means it behaves like the real service, not like a mock that says yes to anything:

- The public instrument CSV has the real column layout and an index row with `segment=INDICES`, so the
  master normalisation is exercised, not bypassed.
- /oms/instruments/historical serves the whole requested window in one call and caps it the way Kite does
  (60 days intraday, 2000 days daily); `history_max_days` truncates on purpose, so a mis-sized window fails.
- The WebSocket speaks the real handshake, takes masked client frames, and emits the real binary packet
  layouts (LTP, quote, index, full with depth, one-byte heartbeat) plus text error frames.
- A 403 handshake or a token error text is available to test the refresh prompt.

Every HTTP call is recorded (the enctoken is redacted) with its path and query.
"""
import base64
import csv
import gzip
import hashlib
import io
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

COLUMNS = [
    "instrument_token", "exchange_token", "tradingsymbol", "name", "last_price", "expiry",
    "strike", "tick_size", "lot_size", "instrument_type", "segment", "exchange",
]


def row(token, exchange, tradingsymbol, name, itype, segment="", tick="0.05", lot="1", expiry="", strike=""):
    return {
        "instrument_token": str(token), "exchange_token": str(token), "tradingsymbol": tradingsymbol,
        "name": name, "last_price": "100", "expiry": expiry, "strike": strike, "tick_size": tick,
        "lot_size": lot, "instrument_type": itype, "segment": segment or exchange, "exchange": exchange,
    }


MASTER_ROWS = [
    row(738561, "NSE", "RELIANCE", "RELIANCE INDUSTRIES", "EQ"),
    row(3045, "NSE", "TCS", "TATA CONSULTANCY SERVICES", "EQ"),
    row(256265, "NSE", "NIFTY 50", "NIFTY 50", "IDX", "INDICES"),       # → NSE_INDEX:NIFTY
    row(260105, "BSE", "SENSEX", "SENSEX", "IDX", "INDICES"),            # → BSE_INDEX:SENSEX
    row(779521, "NFO", "NIFTY25NOVFUT", "NIFTY", "FUT", expiry="2026-11-25", lot="75"),
    row(779522, "NFO", "NIFTY25NOV25000CE", "NIFTY", "CE", expiry="2026-11-25", strike="25000", lot="75"),
    row(4127297, "CDS", "USDINR25NOVFUT", "USDINR", "FUT", tick="0.0025"),
    row(5349633, "MCX", "CRUDEOIL25NOVFUT", "CRUDEOIL", "FUT", expiry="2026-11-20"),
    row(268, "NCO", "GOLD", "GOLD", "EQ"),
    row(999001, "NSE", "NEWCO", "NEW CO", "EQ"),
]


def master_csv(rows=None):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    for r in (rows if rows is not None else MASTER_ROWS):
        writer.writerow(r)
    return out.getvalue()


def step_seconds(interval):
    if interval.endswith("minute"):
        return int(interval[:-6]) * 60
    return 60


def _server_frame(opcode, payload):
    n = len(payload)
    head = bytearray([0x80 | opcode])
    if n < 126:
        head.append(n)
    elif n < 65536:
        head.append(126)
        head += struct.pack(">H", n)
    else:
        head.append(127)
        head += struct.pack(">Q", n)
    return bytes(head) + payload


def _read_frame(buf):
    """One masked client frame from the buffer: ((opcode, payload), rest) or (None, buf)."""
    if len(buf) < 2:
        return None, buf
    opcode = buf[0] & 0x0F
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
    if len(buf) < at + 4:
        return None, buf
    mask = buf[at:at + 4]
    at += 4
    if len(buf) < at + n:
        return None, buf
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(buf[at:at + n]))
    return (opcode, payload), buf[at + n:]


class FakeWs:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}/?api_key=kitefront&access_token=tok"
        self.frames = []
        self.lock = threading.Lock()
        self.conn = None
        self.connected = threading.Event()
        self.reject = False  # answer the handshake with 403
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
            part = conn.recv(4096)
            if not part:
                conn.close()
                return
            buf += part
        head, _, rest = buf.partition(b"\r\n\r\n")
        if self.reject:
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            conn.close()
            return
        key = ""
        for line in head.decode(errors="replace").split("\r\n")[1:]:
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        conn.sendall((f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        with self.lock:
            self.conn = conn
        self.connected.set()
        buf = rest
        while True:
            frame, buf = _read_frame(buf)
            while frame is not None:
                opcode, payload = frame
                with self.lock:
                    self.frames.append((opcode, payload))
                frame, buf = _read_frame(buf)
            try:
                part = conn.recv(65536)
            except OSError:
                return
            if not part:
                return
            buf += part

    def actions(self):
        """The JSON actions the plugin sent (subscribe, mode, unsubscribe)."""
        out = []
        with self.lock:
            frames = list(self.frames)
        for opcode, payload in frames:
            if opcode != 0x1:
                continue
            try:
                out.append(json.loads(payload.decode("utf-8")))
            except ValueError:
                pass
        return out

    def send_json(self, obj):
        with self.lock:
            conn = self.conn
        if conn is None:
            raise RuntimeError("the fake stream has no client yet")
        conn.sendall(_server_frame(0x1, json.dumps(obj).encode("utf-8")))

    @staticmethod
    def packet_ltp(token, price):
        return struct.pack(">Ii", token, int(round(price * 100)))

    @staticmethod
    def packet_quote(token, price, open_=None, high=None, low=None, close=None, volume=1000, divisor=100):
        p = lambda v: int(round((v if v is not None else price) * divisor))
        return struct.pack(">11i", token, p(price), 10, p(price), p(volume / 100.0), 0, 0,
                           p(open_), p(high), p(low), p(close))

    @staticmethod
    def packet_index(token, price, open_=None, high=None, low=None, close=None):
        p = lambda v: int(round((v if v is not None else price) * 100))
        # The index layout is token, ltp, high, low, open, close, change — not the quote order.
        return struct.pack(">7i", token, p(price), p(high), p(low), p(open_), p(close), 0)

    @staticmethod
    def packet_full(token, price, open_=None, high=None, low=None, close=None, volume=1000, oi=0):
        packet = bytearray(FakeWs.packet_quote(token, price, open_, high, low, close, volume))
        packet += struct.pack(">i", 0)                    # 44:48 last traded timestamp
        packet += struct.pack(">iii", int(oi), int(oi), int(oi))  # 48:60 OI, day high, day low
        packet += struct.pack(">I", int(time.time()))      # 60:64 exchange timestamp
        for i in range(5):                                 # 64:124 buy depth (qty, price, orders, pad)
            packet += struct.pack(">IIHH", 100 * (i + 1), int(round((price - i * 0.05) * 100)), i + 1, 0)
        for i in range(5):                                 # 124:184 sell depth
            packet += struct.pack(">IIHH", 200 * (i + 1), int(round((price + i * 0.05) * 100)), i + 1, 0)
        return bytes(packet)

    def push_ticks(self, packets):
        """Send one binary frame holding the given packets (a few ticks of one moment)."""
        with self.lock:
            conn = self.conn
        if conn is None:
            raise RuntimeError("the fake stream has no client yet")
        payload = struct.pack(">H", len(packets))
        for packet in packets:
            payload += struct.pack(">H", len(packet)) + packet
        conn.sendall(_server_frame(0x2, payload))

    def push_error(self, text):
        self.send_json({"type": "error", "data": text})

    def push_order(self, data):
        """A Kite order postback, which arrives as a text frame without any subscription."""
        self.send_json({"type": "order", "data": data})

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


class FakeKite:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else MASTER_ROWS
        self.history_max_days = None
        self.history_floor = None
        self.bar_cap = None
        self.history_status = 200
        self.profile_user_id = "FAKE123"
        self.profile_403 = False
        self.gzip_master = False
        self.margins = {
            "equity": {"net": 250000.0, "available": {"cash": 200000.0, "collateral": 50000.0},
                       "utilised": {"debits": 0.0}},
            "commodity": {"net": 50000.0, "available": {"cash": 50000.0, "collateral": 0.0},
                          "utilised": {"debits": 0.0}},
        }
        self.position_rows = []
        self.holding_rows = []
        self.order_rows = []
        self.trade_rows = []
        self.order_seq = 0
        self.requests = []
        self.counts = Counter()
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.fake = self
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.master_url = f"{self.url}/instruments"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.ws = FakeWs()

    @property
    def ws_url(self):
        return self.ws.url

    def record(self, method, path, query, headers, body=None):
        safe = {k: v for k, v in query.items() if k != "apikey"}
        with self.lock:
            self.requests.append({"method": method, "path": path, "query": safe,
                                  "headers": {k.lower(): v for k, v in headers.items()}, "body": body or {}})
            self.counts[(method, path)] += 1

    def count(self, path, method=None):
        with self.lock:
            return sum(n for (m, p), n in self.counts.items()
                       if p == path and (method is None or m == method))

    def sent(self, path, method=None):
        with self.lock:
            return [r for r in self.requests
                    if r["path"] == path and (method is None or r["method"] == method)]

    def history_rows(self, interval, frm, to):
        start, end = frm.date(), to.date()
        if self.history_floor:
            start = max(start, datetime.strptime(self.history_floor, "%Y-%m-%d").date())
        if self.history_max_days:
            earliest = end - timedelta(days=self.history_max_days - 1)
            start = max(start, earliest)
        out, i, day = [], 0, start
        step = step_seconds(interval) if interval != "day" else 0
        while day <= end:
            if day.weekday() < 5:
                if interval == "day":
                    out.append([f"{day.isoformat()}T00:00:00+0530", 100.0 + i, 110.0 + i, 90.0 + i,
                                105.0 + i, 10.0, 1000.0 + i])
                    i += 1
                else:
                    at = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
                    close = at.replace(hour=15, minute=30)
                    while at < close:
                        out.append([at.strftime("%Y-%m-%dT%H:%M:%S+0530"), 100.0 + i, 110.0 + i,
                                    90.0 + i, 105.0 + i, 10.0, 1000.0 + i])
                        i += 1
                        at += timedelta(seconds=step)
            day += timedelta(days=1)
        if self.bar_cap:
            out = out[:self.bar_cap]
        return out

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


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self):  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self):  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method):
        fake = self.server.fake
        parts = urllib.parse.urlsplit(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", errors="replace")).items()}
        fake.record(method, parts.path, query, self.headers, body)
        path = parts.path

        if method == "GET" and path == "/instruments":
            data = master_csv(fake.rows).encode("utf-8")
            headers = {"Content-Type": "text/csv"}
            if fake.gzip_master:
                data = gzip.compress(data)
                headers["Content-Encoding"] = "gzip"
            return self._send(200, data, headers)
        if method == "GET" and path == PROFILE_PATH:
            if fake.profile_403:
                return self._send(403, {"status": "error", "error_type": "TokenException",
                                        "message": "Invalid enctoken"})
            return self._send(200, {"status": "success", "data": {"user_id": fake.profile_user_id}})
        if method == "GET" and path.startswith("/oms/instruments/historical/"):
            if fake.history_status != 200:
                return self._send(fake.history_status, {"status": "error", "message": "rate limited"})
            interval = path.split("/")[-1]
            try:
                frm = datetime.strptime(query["from"].replace("+", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                to = datetime.strptime(query["to"].replace("+", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
            except (KeyError, ValueError) as e:
                return self._send(200, {"status": "error", "message": str(e)})
            return self._send(200, {"status": "success", "data": {"candles": fake.history_rows(interval, frm, to)}})

        if method == "GET" and path == "/nse/EQUITY_L.csv":
            recent = (datetime.now(IST) - timedelta(days=30)).strftime("%d-%b-%Y")
            text = ("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAST CLOSED PRICE, FACE VALUE, ISIN NUMBER\n"
                    f"RELIANCE,Reliance Industries,EQ,06-OCT-2008,2500.00,10,INE002A01018\n"
                    f"NEWCO,New Co,EQ,{recent},100.00,1,INE0NEWCO123\n")
            return self._send(200, text.encode("utf-8"), {"Content-Type": "text/csv"})
        if method == "GET" and path == "/nse/SME_EQUITY_L.csv":
            text = ("SYMBOL,NAME_OF_COMPANY,SERIES,DATE_OF_LISTING,PAST_CLOSED_PRICE,FACE_VALUE,ISIN_NUMBER\n"
                    "SMECO,SME Co,ST,26-Nov-24,50.00,10,INE0SMECO123\n")
            return self._send(200, text.encode("utf-8"), {"Content-Type": "text/csv"})
        if method == "GET" and path.startswith("/v1/companies/"):
            return self._send(200, {"status": "success", "data": {
                "company": "Reliance Industries Limited", "sector": "Energy",
                "industry": "Refining", "oneLine": "An Indian conglomerate.",
            }})
        if method == "GET" and path.startswith("/oms/instruments/events/"):
            return self._send(200, {"status": "success", "data": [
                {"action_type": "dividend", "ex_date": "2024-08-19", "meta": {"ratio_1": 100, "ratio_2": 10}},
                {"action_type": "bonus", "ex_date": "2024-10-28", "meta": {"ratio_1": 1, "ratio_2": 1}},
            ]})
        if method == "GET" and path == "/oms/user/margins":
            return self._send(200, {"status": "success", "data": fake.margins})
        if method == "GET" and path == "/oms/portfolio/positions":
            return self._send(200, {"status": "success",
                                    "data": {"net": fake.position_rows, "day": []}})
        if method == "GET" and path == "/oms/portfolio/holdings":
            # The personal OMS returns a bare list; Kite Connect wraps it. Both must read.
            rows = fake.holding_rows if getattr(fake, "holdings_as_list", True) else {"holdings": fake.holding_rows}
            return self._send(200, {"status": "success", "data": rows})
        if method == "GET" and path == "/oms/orders":
            return self._send(200, {"status": "success", "data": fake.order_rows})
        if method == "GET" and path == "/oms/trades":
            return self._send(200, {"status": "success", "data": fake.trade_rows})
        if method == "POST" and path == "/oms/orders/regular":
            fake.order_seq += 1
            order_id = f"OID{fake.order_seq}"
            row = {
                "order_id": order_id, "tradingsymbol": body.get("tradingsymbol"),
                "exchange": body.get("exchange"), "transaction_type": body.get("transaction_type"),
                "order_type": body.get("order_type"), "quantity": body.get("quantity"),
                "filled_quantity": 0, "price": body.get("price"), "trigger_price": body.get("trigger_price"),
                "product": body.get("product"), "status": "OPEN", "validity": body.get("validity"),
                "order_timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
            }
            fake.order_rows.append(row)
            return self._send(200, {"status": "success", "data": {"order_id": order_id}})
        if path.startswith("/oms/orders/") and len(path.split("/")) >= 5:
            order_id = path.rsplit("/", 1)[-1]
            order = next((o for o in fake.order_rows if o["order_id"] == order_id), None)
            if order is None:
                return self._send(404, {"status": "error", "message": "no such order"})
            if method == "PUT":
                order.update(quantity=body.get("quantity"), price=body.get("price"),
                             order_type=body.get("order_type"), validity=body.get("validity"))
            elif method == "DELETE":
                order["status"] = "CANCELLED"
            return self._send(200, {"status": "success", "data": {"order_id": order_id}})
        return self._send(404, {"status": "error", "message": f"no route {method} {path}"})

    def _send(self, status, obj, headers=None):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
        self.send_response(status)
        if not headers or "Content-Type" not in headers:
            self.send_header("Content-Type", "application/json" if not isinstance(obj, bytes) else "text/csv")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


PROFILE_PATH = "/oms/user/profile"
