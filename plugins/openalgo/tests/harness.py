"""Shared ground for the plugin's suites: a throwaway cache folder, a source wired to the fake
deployment, and the app's side of the connection captured instead of dialled.

Nothing here touches the real user's cache: `plugin.local_app_data` points at a fresh temp
folder for each source, so the day-stamped files the plugin reads and writes live there.
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import fake_openalgo  # noqa: E402
import plugin  # noqa: E402

KEY = "test-key-8f2a"  # the suites' API key: never printed, only carried in the requests

EXCHANGES = "NSE,NFO"


def fresh_cache():
    """A private cache folder for one source, and the plugin pointed at it."""
    folder = tempfile.mkdtemp(prefix="openalgo-suite-")
    plugin.local_app_data = lambda: folder
    return folder


def settings(fake=None, **extra):
    base = {
        "apiUrl": fake.url if fake is not None else "http://127.0.0.1:1",
        "wsUrl": fake.ws.url if fake is not None else "",
        "apiKey": KEY,
        "exchanges": EXCHANGES,
        "product": "MIS",
        "strategy": "OpenTrader",
    }
    base.update(extra)
    return base


def make_source(fake=None, **extra):
    """A source whose cache is private, whose settings point at the fake, and whose public
    company API lookup is off (the suites make no calls to the internet)."""
    fresh_cache()
    source = plugin.OpenAlgoSource()
    source.settings = settings(fake, **extra)
    source.exchanges = [e for e in EXCHANGES.split(",") if e]
    source._company_profile = lambda ticker: None
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


def make_plugin(source):
    """A plugin on the source, alive, writing to a captured socket."""
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
