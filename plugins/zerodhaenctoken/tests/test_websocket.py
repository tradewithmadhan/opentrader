"""Realtime: a tick on Kite's binary stream reaches the app at once, with the right divisor and field order."""

import time
import unittest

import fake_kite
import harness
import plugin


class Stream(unittest.TestCase):
    def setUp(self):
        self.fake = fake_kite.FakeKite()
        self.source = harness.make_source(self.fake)
        self.app = harness.make_plugin(self.source)
        self.source.instruments.ensure()  # token map in memory before the stream starts
        self.source.configure(harness.settings(self.fake))
        self.assertTrue(harness.wait_until(lambda: self.source.ws and self.source.ws.connected, 5.0))

    def tearDown(self):
        self.source.ws_generation += 1
        if self.source.ws is not None:
            self.source.ws.stop.set()
        self.app.alive = False
        self.fake.close()

    def subscribed(self, ticker):
        self.source.set_subscriptions([ticker])
        token = self.source.instruments.row(ticker)["token"]
        self.assertTrue(harness.wait_until(
            lambda: any(a.get("a") == "subscribe" and token in (a.get("v") or [])
                        for a in self.fake.ws.actions()), 5.0))

    def test_subscribes_the_tokens_of_the_app_symbols(self):
        self.app.on_notification("data.subscribe", {"tickers": ["NSE:RELIANCE", "NSE_INDEX:NIFTY"]})
        self.assertTrue(harness.wait_until(
            lambda: any(a.get("a") == "subscribe" and set(a.get("v") or []) == {738561, 256265}
                        for a in self.fake.ws.actions()), 5.0))
        self.assertTrue(any(a.get("a") == "mode" and a.get("v", [None])[0] == "quote"
                            for a in self.fake.ws.actions()))

    def test_one_ltp_tick_is_one_quote(self):
        self.subscribed("NSE:RELIANCE")
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_ltp(738561, 2500.0)])
        self.assertTrue(harness.wait_until(lambda: self.app.sock.payloads("data.quote"), 3.0))
        quotes = self.app.sock.payloads("data.quote")
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0]["price"], 2500.0)
        self.assertEqual(quotes[0]["ticker"], "NSE:RELIANCE")

    def test_quote_interval_throttles_chart_updates(self):
        self.source.quote_interval = 1000
        self.subscribed("NSE:RELIANCE")
        self.app.sock.lines.clear()
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_ltp(738561, 2500.0)])
        self.assertTrue(harness.wait_until(lambda: self.app.sock.payloads("data.quote"), 3.0))
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_ltp(738561, 2501.0)])
        time.sleep(0.3)
        self.assertEqual(len(self.app.sock.payloads("data.quote")), 1,
                         "the second tick inside the interval updates the cache, not the chart")

    def test_configure_parses_the_new_settings(self):
        self.source.configure(harness.settings(self.fake, companyDetails="false", quoteIntervalMs="150"),
                              ["datafeed"])
        self.assertFalse(self.source.company_details)
        self.assertEqual(self.source.quote_interval, 150)

    def test_quote_packet_builds_the_forming_bars(self):
        self.subscribed("NSE:RELIANCE")
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_quote(
            738561, 2500.0, open_=2490.0, high=2510.0, low=2480.0, close=2495.0, volume=5000)])
        self.assertTrue(harness.wait_until(lambda: self.app.sock.payloads("data.quote"), 3.0))
        q = self.app.sock.payloads("data.quote")[-1]
        self.assertEqual(q["price"], 2500.0)
        self.assertEqual(q["prevClose"], 2495.0)
        self.assertEqual(q["minute"][0], int(time.time()) // 60 * 60)
        self.assertEqual(q["minute"][4], 2500.0)
        self.assertEqual(q["day"][4], 2500.0)
        self.assertEqual(q["day"][5], 5000)

    def test_index_packet_uses_its_own_field_order(self):
        self.subscribed("NSE_INDEX:NIFTY")
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_index(
            256265, 25000.0, open_=24900.0, high=25100.0, low=24800.0, close=24950.0)])
        self.assertTrue(harness.wait_until(lambda: self.app.sock.payloads("data.quote"), 3.0))
        q = self.app.sock.payloads("data.quote")[-1]
        self.assertEqual(q["price"], 25000.0)
        self.assertEqual(q["prevClose"], 24950.0)
        self.assertEqual(q["day"][2], 25100.0)
        self.assertEqual(q["day"][3], 24800.0)

    def test_cds_prices_use_the_ten_million_divisor(self):
        self.subscribed("CDS:USDINR25NOVFUT")
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_quote(
            4127297, 83.125, open_=83.0, high=83.5, low=82.9, close=83.0, divisor=10_000_000)])
        self.assertTrue(harness.wait_until(lambda: self.app.sock.payloads("data.quote"), 3.0))
        self.assertAlmostEqual(self.app.sock.payloads("data.quote")[-1]["price"], 83.125, places=3)

    def test_an_empty_subscription_unsubscribes(self):
        self.subscribed("NSE:RELIANCE")
        self.source.set_subscriptions([])
        self.assertTrue(harness.wait_until(
            lambda: any(a.get("a") == "unsubscribe" and 738561 in (a.get("v") or [])
                        for a in self.fake.ws.actions()), 3.0))

    def test_the_secret_never_reaches_the_log(self):
        self.source.log("using " + harness.KEY)
        joined = " ".join(self.app.sock.log_lines())
        self.assertNotIn(harness.KEY, joined)
        self.assertIn("…", joined)


class Logs(unittest.TestCase):
    def test_api_and_ws_log_files_are_written_with_the_token_redacted(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            harness.make_plugin(source)
            source.log("using " + harness.KEY)
            source.log("stream up", "info", "ws")
            api = (plugin.plugin_data_dir() / "zerodha-api.log").read_text(encoding="utf-8")
            ws = (plugin.plugin_data_dir() / "zerodha-ws.log").read_text(encoding="utf-8")
            self.assertIn("using …", api)
            self.assertNotIn(harness.KEY, api)
            self.assertIn("stream up", ws)
        finally:
            fake.close()


class Url(unittest.TestCase):
    def test_the_app_supplied_ws_url_gets_the_token_and_user_id(self):
        fake = fake_kite.FakeKite()
        try:
            # The manifest gives the plain Kite URL; the plugin must add auth itself.
            source = harness.make_source(fake, wsUrl="wss://ws.kite.trade")
            source.user_id = ""
            source.enctoken = "tok+en/token=="
            url = source.ws_url()
            self.assertTrue(url.startswith("wss://ws.kite.trade?api_key=kitefront"))
            self.assertIn("access_token=tok%2Ben%2Ftoken%3D%3D", url)  # '+' and '/' must be encoded
            self.assertIn("user_id=FAKE123", url)
            self.assertEqual(source.user_id, "FAKE123")
        finally:
            fake.close()


class Auth(unittest.TestCase):
    def test_a_refused_enctoken_asks_the_user_and_recovers(self):
        fake = fake_kite.FakeKite()
        fake.ws.reject = True
        source = harness.make_source(fake)
        app = harness.make_plugin(source)
        source.instruments.ensure()
        source.configure(harness.settings(fake))
        try:
            self.assertTrue(harness.wait_until(
                lambda: any(p.get("method") == "plugin.prompt" for p in app.sock.notifications()), 5.0))
            prompt = [p for p in app.sock.notifications("plugin.prompt")][0]["params"]
            self.assertEqual(prompt["input"], "password")
            fake.ws.reject = False
            app.on_notification("plugin.promptAnswer", {"id": prompt["id"], "value": "fresh-token"})
            self.assertEqual(source.enctoken, "fresh-token")
            self.assertTrue(harness.wait_until(lambda: source.ws and source.ws.connected, 5.0))
        finally:
            source.ws_generation += 1
            if source.ws is not None:
                source.ws.stop.set()
            app.alive = False
            fake.close()


if __name__ == "__main__":
    unittest.main()
