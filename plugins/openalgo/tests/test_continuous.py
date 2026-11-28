"""Continuous futures (`NFO:NIFTY1!`) and startup toasts.

The synthetic contract follows the front expiry of its root, trades as that contract and tells the
app when it rolls — the same shape the built-in studies expect of a `1!` symbol.
"""

import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import fake_openalgo
import harness
import plugin

CONT = "NFO:NIFTY1!"


def fut(symbol, expiry):
    return {"symbol": symbol, "name": "NIFTY", "instrumenttype": "FUT", "expiry": expiry,
            "lotsize": "75", "tick_size": "0.05"}


class Continuous(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)
        self.app = harness.make_plugin(self.source)
        self.source.exchanges = ["NFO"]  # one landed segment: search reads the catalog, not the live fallback
        self.source.instruments.rows_for("NFO")

    def tearDown(self):
        self.app.alive = False
        self.fake.close()

    def test_master_builds_continuous_and_search_finds_it(self):
        self.assertIn(CONT, self.source.instruments.continuous)
        self.assertTrue(self.source.known(CONT))
        hits = [r["ticker"] for r in self.source.search("NIFTY1", "FUT")]
        self.assertIn(CONT, hits)

    def test_front_is_the_nearest_upcoming_expiry(self):
        today = datetime.now(plugin.TZ_IST).date()
        near = (today + timedelta(days=30)).strftime("%d-%b-%y")
        far = (today + timedelta(days=60)).strftime("%d-%b-%y")
        self.source.instruments.rows["NFO"] = [fut("NIFTYNEARFUT", near), fut("NIFTYFARFUT", far)]
        self.source.instruments._rebuild_continuous()
        self.assertEqual(self.source.instruments.continuous[CONT], "NFO:NIFTYNEARFUT")

    def test_syminfo_points_at_the_front_contract(self):
        with patch.object(self.source, "_calendar_session", return_value=None):
            info = self.source.symbol_info(CONT)
        self.assertEqual(info["tradeTicker"], self.source.instruments.continuous[CONT])
        self.assertEqual(info["root"], "NIFTY")
        self.assertEqual(info["type"], "futures")
        self.assertIn("expiration", info)

    def test_quote_serves_the_front_contracts_ticks(self):
        front = self.source.instruments.continuous[CONT]
        self.source.quotes[front] = {"price": 25000.0, "time": int(time.time())}
        quote = self.source.quote(CONT)
        self.assertEqual(quote["ticker"], CONT)
        self.assertEqual(quote["price"], 25000.0)

    def test_history_reads_the_front_contract(self):
        with patch.object(self.source, "_interval_for", return_value="1m"), \
                patch.object(self.source, "_window", return_value=[]) as window:
            self.source.bars(CONT, "1", 0, 100, 10)
        self.assertEqual(window.call_args[0][1], self.source.instruments.continuous[CONT].partition(":")[2])

    def test_a_roll_notifies_the_app(self):
        self.app.sock.lines.clear()
        today = datetime.now(plugin.TZ_IST).date()
        new = (today + timedelta(days=90)).strftime("%d-%b-%y")
        self.source.instruments.rows["NFO"] = [fut("NIFTYNEWFUT", new), fut("NIFTY25NOVFUT", "25-Nov-26")]
        self.source.instruments._rebuild_continuous()
        self.assertEqual(self.source.instruments.continuous[CONT], "NFO:NIFTYNEWFUT")
        told = [t for p in self.app.sock.payloads("data.symbolInfoChanged") for t in (p.get("tickers") or [])]
        self.assertIn(CONT, told)


class Toasts(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)
        self.app = harness.make_plugin(self.source)

    def tearDown(self):
        self.app.alive = False
        self.fake.close()

    def test_toast_is_a_plugin_notification(self):
        self.app.toast("info", "hello")
        self.assertIn({"level": "info", "message": "hello"}, self.app.sock.payloads("plugin.toast"))

    def test_broker_toast_waits_for_the_master_and_fires_once(self):
        self.source.rest_ok = True
        self.app.maybe_broker_toast()
        self.assertEqual([p for p in self.app.sock.payloads("plugin.toast")
                          if "Broker connected" in (p.get("message") or "")], [])
        self.source.instruments.prefetch_done.set()
        self.app.maybe_broker_toast()
        self.app.maybe_broker_toast()
        told = [p for p in self.app.sock.payloads("plugin.toast")
                if "Broker connected" in (p.get("message") or "")]
        self.assertEqual(len(told), 1)

    def test_a_cold_master_announces_download_and_ready(self):
        self.source.instruments._prefetch_all(["NFO"])
        messages = [p.get("message") or "" for p in self.app.sock.payloads("plugin.toast")]
        self.assertTrue(any("Downloading instrument master" in m for m in messages))
        self.assertTrue(any("Instrument master ready" in m for m in messages))

    def test_a_ready_master_asks_the_app_to_reload(self):
        self.source.instruments._prefetch_all(["NFO"])
        self.assertTrue(self.app.sock.payloads("data.symbolInfoChanged"))
        self.assertIn({}, self.app.sock.payloads("data.symbolInfoChanged"))


class Cache(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)
        self.app = harness.make_plugin(self.source)

    def tearDown(self):
        self.app.alive = False
        self.fake.close()

    def test_only_todays_master_files_are_kept(self):
        cache = self.source.instruments.cache_dir
        cache.mkdir(parents=True, exist_ok=True)
        today = datetime.now(plugin.TZ_IST).strftime("%Y-%m-%d")
        keep = cache / f"NFO-{today}.json"
        old = cache / "NFO-2026-01-01.json"
        keep.write_text("[]", encoding="utf-8")
        old.write_text("[]", encoding="utf-8")
        self.source.instruments._prune_caches()
        self.assertTrue(keep.exists())
        self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
