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
        for timer in list(self.source._watch_timers.values()):
            timer.cancel()
        self.source._watch_timers.clear()
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

    def test_perf_counters_track_ticks_quotes_and_batches(self):
        self.subscribed("NSE:RELIANCE")
        before = dict(self.source._perf)
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_ltp(738561, 2500.0)])
        self.assertTrue(harness.wait_until(lambda: self.app.sock.payloads("data.quote"), 3.0))
        self.assertGreater(self.source._perf["ticks"], before["ticks"])
        self.assertGreater(self.source._perf["quotes"], before["quotes"])
        self.assertGreaterEqual(self.source._perf["batches"], before["batches"] + 1)

    def test_quote_interval_throttles_chart_updates(self):
        self.source.quote_interval = 1000
        self.subscribed("NSE:RELIANCE")
        self.app.sock.lines.clear()
        skipped_before = self.source._perf["skipped"]
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_ltp(738561, 2500.0)])
        self.assertTrue(harness.wait_until(lambda: self.app.sock.payloads("data.quote"), 3.0))
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_ltp(738561, 2501.0)])
        time.sleep(0.3)
        self.assertEqual(len(self.app.sock.payloads("data.quote")), 1,
                         "the second tick inside the interval updates the cache, not the chart")
        self.assertGreater(self.source._perf["skipped"], skipped_before)

    def test_configure_parses_the_new_settings(self):
        self.source.configure(harness.settings(self.fake, companyDetails="false", quoteIntervalMs="150",
                                              maxQuotesPerSecond="75"), ["datafeed"])
        self.assertFalse(self.source.company_details)
        self.assertEqual(self.source.quote_interval, 150)
        self.assertEqual(self.source.max_quotes_per_second, 75)

    def test_global_quote_budget_caps_a_frame_and_counts_the_drops(self):
        self.source.max_quotes_per_second = 2
        self.source._quote_tokens = 2.0
        self.source._quote_refill_at = time.monotonic()
        self.source.set_subscriptions(["NSE:RELIANCE", "NSE:TCS", "NSE_INDEX:NIFTY"])
        self.app.sock.lines.clear()
        skipped = self.source._perf["budget_skipped"]
        self.fake.ws.push_ticks([
            fake_kite.FakeWs.packet_ltp(738561, 2500.0),
            fake_kite.FakeWs.packet_ltp(3045, 3000.0),
            fake_kite.FakeWs.packet_index(256265, 25000.0),
        ])
        self.assertTrue(harness.wait_until(lambda: len(self.app.sock.payloads("data.quote")) == 2, 3.0))
        self.assertEqual(len(self.app.sock.payloads("data.quote")), 2, "three ticks, two budget slots")
        self.assertGreater(self.source._perf["budget_skipped"], skipped)

    def test_quote_budget_prefers_symbols_not_sent_recently(self):
        self.source.max_quotes_per_second = 1
        self.source._quote_tokens = 1.0
        self.source._quote_refill_at = time.monotonic()
        self.source.set_subscriptions(["NSE:RELIANCE", "NSE:TCS"])
        self.source.quotes["NSE:RELIANCE"]["_last_sent"] = time.time() * 1000
        self.app.sock.lines.clear()
        self.fake.ws.push_ticks([
            fake_kite.FakeWs.packet_ltp(738561, 2500.0),
            fake_kite.FakeWs.packet_ltp(3045, 3000.0),
        ])
        self.assertTrue(harness.wait_until(lambda: self.app.sock.payloads("data.quote"), 3.0))
        self.assertEqual([q["ticker"] for q in self.app.sock.payloads("data.quote")], ["NSE:TCS"])

    def test_brief_empty_watch_keeps_full_mode_and_does_not_churn(self):
        self.app.on_notification("broker.watchQuote", {"tickers": ["NSE:RELIANCE"]})
        self.assertTrue(harness.wait_until(
            lambda: any(a.get("a") == "mode" and (a.get("v") or [None])[0] == "full"
                        for a in self.fake.ws.actions()), 3.0))
        modes = len([a for a in self.fake.ws.actions() if a.get("a") == "mode"])
        self.app.on_notification("broker.watchQuote", {"tickers": []})
        time.sleep(0.2)
        self.assertEqual(self.source.watched, {"NSE:RELIANCE"}, "an empty watch is held briefly")
        self.app.on_notification("broker.watchQuote", {"tickers": ["NSE:RELIANCE"]})
        time.sleep(plugin.WATCH_GRACE_SEC + 0.3)
        self.assertEqual(self.source.watched, {"NSE:RELIANCE"})
        self.assertEqual(len([a for a in self.fake.ws.actions() if a.get("a") == "mode"]), modes,
                         "the chart switch must not send mode frames")

    def test_current_daily_bar_is_served_from_the_tick_cache(self):
        self.subscribed("NSE:RELIANCE")
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_quote(
            738561, 2500.0, open_=2490.0, high=2510.0, low=2480.0, close=2495.0, volume=5000)])
        self.assertTrue(harness.wait_until(lambda: self.source.quotes.get("NSE:RELIANCE", {}).get("day"), 3.0))
        path = "/oms/instruments/historical/738561/day"
        before = self.fake.count(path)
        now = int(time.time())
        bars = self.source.bars("NSE:RELIANCE", "1D", plugin.ist_midnight(now), now, 1)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0][1:5], [2490.0, 2510.0, 2480.0, 2500.0])
        self.assertEqual(bars[0][5], 5000)
        self.assertEqual(self.fake.count(path), before, "the newest-bar refresh makes no Kite history call")
        self.assertGreater(self.source._perf["cached_bars"], 0)
        older = now - 10 * 86400
        self.assertTrue(self.source.bars("NSE:RELIANCE", "1D", older, now, 10))
        self.assertGreater(self.fake.count(path), before, "a real history window still goes to Kite")

    def test_oi_series_current_bar_uses_the_live_oi_cache(self):
        ticker = "NFO:NIFTY25NOVFUT_OI"
        self.source.set_subscriptions([ticker])
        token = self.source.instruments.row("NFO:NIFTY25NOVFUT")["token"]
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_full(token, 250.0, oi=12345)])
        self.assertTrue(harness.wait_until(lambda: self.source.quotes.get(ticker, {}).get("day"), 3.0))
        path = f"/oms/instruments/historical/{token}/day"
        before = self.fake.count(path)
        now = int(time.time())
        bars = self.source.bars(ticker, "1D", plugin.ist_midnight(now), now, 1)
        self.assertEqual(bars[0][4], 12345)
        self.assertEqual(self.fake.count(path), before, "the OI refresh needs no second history call")

    def test_an_empty_watch_does_clear_after_the_grace_period(self):
        self.app.on_notification("broker.watchDepth", {"tickers": ["NSE:RELIANCE"]})
        self.assertTrue(harness.wait_until(
            lambda: any(a.get("a") == "mode" and (a.get("v") or [None])[0] == "full"
                        for a in self.fake.ws.actions()), 3.0))
        self.app.on_notification("broker.watchDepth", {"tickers": []})
        self.assertTrue(harness.wait_until(lambda: self.source.watched_depth == set(),
                                           plugin.WATCH_GRACE_SEC + 2.0))

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
            notices = [n["params"] for n in app.sock.notifications("broker.notice")]
            self.assertTrue(any(n["level"] == "error" and "enctoken" in n["message"] for n in notices))
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


class Api8(unittest.TestCase):
    def test_the_plugin_ships_indicators_and_a_strategy(self):
        scripts = {s["id"]: s for s in plugin.ZerodhaSource.plugin_scripts()}
        names = {s["name"] for s in scripts.values()}
        self.assertIn("Monthly Levels (Expiry H/L)", names)
        self.assertIn("MA Cloud", names)
        self.assertIn("EMA Cross Strategy", names)
        self.assertEqual(scripts["EMA_Cross_Strategy"]["kind"], "strategy")
        self.assertTrue(all(s["kind"] == "indicator" for sid, s in scripts.items()
                            if sid != "EMA_Cross_Strategy"))

    def test_remote_manifest_overrides_local_and_only_notifies_on_change(self):
        fake = fake_kite.FakeKite()
        source = harness.make_source(fake)
        app = harness.make_plugin(source)
        try:
            source.script_manifest_url = "http://example.test/scripts.json"
            manifest = {"schema": 1, "scripts": [
                {"id": "MA_Cloud", "name": "Remote MA Cloud", "kind": "indicator", "source": 'indicator("Remote")'},
                {"id": "new_study", "name": "New Study", "kind": "indicator", "source": 'indicator("New")'},
                {"id": "bad id", "name": "bad", "source": "x"},
            ]}
            source._external_json = lambda url, timeout=5: manifest
            source._remote_scripts = source._fetch_remote_scripts()
            self.assertNotIn("bad id", source._remote_scripts)
            merged = {s["id"]: s for s in source.merged_scripts()}
            self.assertEqual(merged["MA_Cloud"]["name"], "Remote MA Cloud")

            self.assertTrue(source.send_scripts())
            self.assertEqual(len(app.sock.payloads("data.scripts")), 1)
            self.assertTrue(any("New Study" in t["message"] for t in app.sock.payloads("plugin.toast")))
            self.assertFalse(source.send_scripts(), "an unchanged set is not sent again")
            self.assertEqual(len(app.sock.payloads("data.scripts")), 1)
        finally:
            app.alive = False
            fake.close()

    def test_one_folder_url_finds_both_manifests(self):
        fake = fake_kite.FakeKite()
        source = harness.make_source(fake)
        app = harness.make_plugin(source)
        try:
            source.plugin_data_url = "https://example.test/plugin-indicators?ref=main"
            self.assertEqual(source._data_url(), "https://example.test/plugin-indicators/?ref=main")
            seen = []

            def fake_manifest(url, timeout=5):
                seen.append(url)
                if url.endswith("/watchlists.json?ref=main"):
                    return {"watchlists": [
                        {"id": "remote-list", "name": "Remote list", "symbols": ["NSE:TCS"], "sections": []},
                        {"id": "", "name": "bad", "symbols": []}]}
                return {"scripts": [{"id": "remote_script", "name": "Remote", "kind": "indicator", "source": "x"}]}

            source._external_json = fake_manifest
            source._remote_scripts = source._fetch_remote_scripts()
            source._remote_watchlists = source._fetch_remote_watchlists()
            self.assertEqual(len(seen), 2)
            self.assertIn("remote_script", source._remote_scripts)
            self.assertIn("remote-list", source._remote_watchlists)
            self.assertNotIn("", source._remote_watchlists)

            self.assertTrue(source.send_watchlists())
            lists = app.sock.payloads("data.watchlists")[-1]["watchlists"]
            self.assertIn("remote-list", [w["id"] for w in lists])
            self.assertTrue(any("Remote list" in t["message"] for t in app.sock.payloads("plugin.toast")))
            self.assertFalse(source.send_watchlists(), "unchanged watchlists send nothing")
        finally:
            app.alive = False
            fake.close()

    def test_toast_and_symbol_info_changed_are_sent_to_the_app(self):
        fake = fake_kite.FakeKite()
        source = harness.make_source(fake)
        app = harness.make_plugin(source)
        try:
            source.toast("warn", "sample warning", True)
            source.plugin.notify("data.symbolInfoChanged", {})
            toasts = self_payloads(app, "plugin.toast")
            self.assertEqual(toasts, [{"level": "warn", "message": "sample warning", "sound": True,
                                       "position": "top-right"}])
            self.assertEqual(self_payloads(app, "data.symbolInfoChanged"), [{}])
        finally:
            app.alive = False
            fake.close()

    def test_toast_position_setting(self):
        fake = fake_kite.FakeKite()
        settings = harness.settings(fake, toastPosition="bottom-center")
        source = harness.make_source(fake, toastPosition="bottom-center")
        app = harness.make_plugin(source)
        try:
            source.configure(settings)
            source.toast("info", "where")
            payload = self_payloads(app, "plugin.toast")[-1]
            self.assertEqual(payload["position"], "bottom-center")
        finally:
            app.alive = False
            fake.close()


def self_payloads(app, method):
    return [m.get("params") for m in app.sock.notifications(method)]


if __name__ == "__main__":
    unittest.main()
