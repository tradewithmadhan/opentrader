"""Shared ground for the plugin's suites: a throwaway cache, a source wired to the fake Kite, and the app's
side of the connection captured instead of dialled. Nothing touches the user's real settings or cache.
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import fake_kite  # noqa: E402
import plugin  # noqa: E402

KEY = "test-enctoken-8f2a"  # never printed, only carried in the requests

# No test should reach the real NSE listing lists; suites that need them patch these constants to the fake.
plugin.NSE_EQUITY_LIST_URL = "http://127.0.0.1:1/equity.csv"
plugin.NSE_SME_LIST_URL = "http://127.0.0.1:1/sme.csv"


def fresh_cache():
    folder = tempfile.mkdtemp(prefix="zerodha-suite-")
    plugin.local_app_data = lambda: folder
    return folder


def settings(fake=None, **extra):
    base = {
        "apiUrl": fake.url if fake is not None else "http://127.0.0.1:1",
        "wsUrl": fake.ws_url if fake is not None else "",
        "masterUrl": fake.master_url if fake is not None else "http://127.0.0.1:1/instruments",
        "enctoken": KEY,
        "userId": "FAKE123",
        "product": "MIS",
        "exchanges": ",".join(sorted(plugin.ALL_EXCHANGES)),
    }
    base.update(extra)
    return base


def make_source(fake=None, **extra):
    """A source whose cache is private and whose URLs point at the fake. It does not start the stream until
    `configure()` is called, so the master/history suites make no WebSocket connections."""
    fresh_cache()
    source = plugin.ZerodhaSource()
    source.settings = settings(fake, **extra)
    source.enctoken = source.settings["enctoken"]
    source.user_id = source.settings.get("userId", "")
    source.exchanges = [e.strip().upper() for e in source.settings["exchanges"].split(",") if e.strip()]
    return source


class CaptureSocket:
    """The app's socket, kept: every JSON-RPC line the plugin sent."""

    def __init__(self):
        self.lock = threading.Lock()
        self.lines = []

    def sendall(self, data):
        with self.lock:
            self.lines.extend(line for line in data.decode("utf-8").split("\n") if line.strip())

    def messages(self):
        with self.lock:
            lines = list(self.lines)
        return [json.loads(line) for line in lines]

    def notifications(self, method=None):
        out = [m for m in self.messages() if "method" in m and "id" not in m]
        return [m for m in out if method is None or m["method"] == method]

    def payloads(self, method):
        return [m.get("params") for m in self.notifications(method)]

    def log_lines(self):
        return [str(p.get("message") or "") for p in self.payloads("log")]


def make_broker_source(fake=None, **extra):
    """A source with the broker capability enabled (the app sends `provides` in plugin.configure)."""
    source = make_source(fake, **extra)
    source.broker_enabled = True
    return source


def make_plugin(source):
    plugin_ = plugin.Plugin(source)
    plugin_.alive = True
    plugin_.sock = CaptureSocket()
    return plugin_


def wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
