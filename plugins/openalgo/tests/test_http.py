"""The HTTP shape the deployments demand, and what a rate limit must never become.

Fix A (0.1.18): a bodyless GET carries no `Content-Type` — a local Flask/Werkzeug answers one
that does with HTTP 400, which is what silenced the instruments pull on 0.1.17.

Fix C (0.1.18): a 429 on `/symbol` is not an answer about the symbol. Cached as a miss it read
as "Unknown symbol" (-32004, final: the app unsubscribes it) for the rest of the day.
"""

import unittest

import fake_openalgo
import harness
import plugin


class HttpShape(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)

    def tearDown(self):
        self.fake.close()

    def test_instruments_get_carries_no_content_type(self):
        rows = self.source.instruments.rows_for("NSE")

        self.assertEqual([r["symbol"] for r in rows], ["RELIANCE", "TCS", "IDEA"])
        calls = self.fake.sent("/api/v1/instruments", "GET")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("content-type", calls[0]["headers"],
                         "a bodyless GET declaring a JSON body type is the HTTP 400 a local "
                         "Flask/Werkzeug answers (0.1.17)")
        self.assertEqual(calls[0]["headers"].get("accept"), "application/json")
        # the key travels where the API takes it: the query string
        self.assertEqual(self.fake.requests[0]["query"].get("exchange"), "NSE")

    def test_instruments_land_once_per_day(self):
        self.source.instruments.rows_for("NSE")
        self.source.instruments.rows_for("NSE")
        self.assertEqual(self.fake.count("/api/v1/instruments"), 1,
                         "the day's master is memory, then today's disk cache")

    def test_post_carries_the_body_type(self):
        self.fake.quotes["NSE:RELIANCE"] = {"ltp": 2501.5, "prev_close": 2490.0}
        data = self.source._post("/api/v1/quotes", {"symbol": "RELIANCE", "exchange": "NSE"})

        self.assertEqual(data["ltp"], 2501.5)
        calls = self.fake.sent("/api/v1/quotes", "POST")
        self.assertEqual(len(calls), 1)
        self.assertIn("content-type", calls[0]["headers"])
        self.assertEqual(calls[0]["body"].get("apikey"), harness.KEY)


class RateLimitIsNotAnAnswer(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)
        self.app = harness.make_plugin(self.source)

    def tearDown(self):
        self.app.alive = False
        self.fake.close()

    def test_busy_symbol_is_not_cached_and_stays_retryable(self):
        self.fake.busy_symbol = True
        with self.assertRaises(plugin.PluginError) as raised:
            self.source._symbol_row("NSE:WIPRO")
        self.assertEqual(raised.exception.code, plugin.BUSY_CODE)
        self.assertNotIn("NSE:WIPRO", self.source._symbol_rows,
                         "a 429 cached as a miss is a day-long 'Unknown symbol'")

        with self.assertRaises(plugin.PluginError) as raised:
            self.source.symbol_info("NSE:WIPRO")
        self.assertEqual(raised.exception.code, plugin.BUSY_CODE,
                         "the app retries -32005 with backoff; -32004 unsubscribes the symbol")

        # The limit lifts: the same symbol answers, because nothing was cached against it.
        self.fake.busy_symbol = False
        self.fake.symbols[("NSE", "WIPRO")] = fake_openalgo._row(
            "WIPRO", "WIPRO LTD", "NSE", "EQ")
        info = self.source.symbol_info("NSE:WIPRO")
        self.assertEqual(info["name"], "WIPRO LTD")
        self.assertIn("NSE:WIPRO", self.source._symbol_rows)

    def test_a_failed_lookup_is_not_cached_either(self):
        self.assertIsNone(self.source._symbol_row("NSE:NEWLY"))  # the live /symbol says not found
        self.assertNotIn("NSE:NEWLY", self.source._symbol_rows)
        with self.assertRaises(plugin.PluginError) as raised:
            self.source.symbol_info("NSE:NEWLY")
        self.assertEqual(raised.exception.code, -32004)

    def test_the_key_reaches_the_request_but_never_the_log(self):
        self.fake.busy_symbol = True
        with self.assertRaises(plugin.PluginError):
            self.source.symbol_info("NSE:WIPRO")
        calls = self.fake.sent("/api/v1/symbol", "POST")
        self.assertEqual(calls[-1]["body"].get("apikey"), harness.KEY)
        logged = " ".join(self.app.sock.log_lines())
        self.assertNotIn(harness.KEY, logged)


if __name__ == "__main__":
    unittest.main()
