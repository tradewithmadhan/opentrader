"""History windows: one honest call unless the request has to be chunked, and Kite's IST timestamps intact."""

import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone

import fake_kite
import harness
import plugin

IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL = "NSE:RELIANCE"


def midnight(day):
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST).timestamp())


class Intervals(unittest.TestCase):
    def setUp(self):
        self.source = harness.make_source()

    def test_resolutions_map_to_kite_tokens(self):
        self.assertEqual(self.source._interval_for("1"), "minute")
        self.assertEqual(self.source._interval_for("15"), "15minute")
        self.assertEqual(self.source._interval_for("30"), "30minute")
        self.assertEqual(self.source._interval_for("1D"), "day")
        self.assertEqual(self.source._interval_for("5S"), "5second")
        self.assertEqual(self.source._interval_for("2"), "2minute")
        self.assertEqual(self.source._interval_for("120"), "2hour")
        self.assertEqual(self.source._interval_for("180"), "3hour")

    def test_an_unserved_resolution_is_refused(self):
        with self.assertRaises(plugin.PluginError) as raised:
            self.source._interval_for("1S")
        self.assertEqual(raised.exception.code, -32602)


class Windows(unittest.TestCase):
    def setUp(self):
        self.fake = fake_kite.FakeKite()
        self.source = harness.make_source(self.fake)

    def tearDown(self):
        self.fake.close()

    def test_one_call_serves_a_window_inside_the_cap(self):
        frm, to = midnight("2026-10-01"), midnight("2026-10-06")
        bars = self.source.bars(SYMBOL, "15", frm, to, 0)

        self.assertEqual(self.fake.count("/oms/instruments/historical/738561/15minute"), 1)
        expected = self.fake.history_rows("15minute", datetime.fromtimestamp(frm, IST),
                                          datetime.fromtimestamp(to, IST))
        self.assertEqual(len(bars), len(expected))
        self.assertEqual(bars[0][0], int(datetime(2026, 10, 1, 9, 15, tzinfo=IST).timestamp()))
        self.assertEqual(bars[0][4], 105.0)
        self.assertEqual([b[0] for b in bars], sorted({b[0] for b in bars}))

    def test_history_caps_use_settings_with_safe_defaults(self):
        self.assertEqual(self.source._cap("historyDaysMinute", 30), 30)
        self.assertEqual(self.source._cap("historyDaysDaily", 1900), 1900)
        for bad in ("", "0", "-3", "many"):
            self.source.settings["historyDaysMinute"] = bad
            self.assertEqual(self.source._cap("historyDaysMinute", 30), 30)

    def test_only_the_newest_chunk_loads_first_and_scroll_loads_older_pages(self):
        self.source.settings["historyDaysMinute"] = "2"
        frm, to = midnight("2026-10-01"), midnight("2026-10-05")
        first = self.source.bars(SYMBOL, "15", frm, to, 0)
        self.assertEqual(self.fake.count("/oms/instruments/historical/738561/15minute"), 1,
                         "one Kite call on the initial load, not one per chunk")
        self.assertTrue(first)

        first_day = datetime.fromtimestamp(first[0][0], IST).date()
        older_to = int(datetime(first_day.year, first_day.month, first_day.day, tzinfo=IST).timestamp()) - 86400
        older = self.source.bars(SYMBOL, "15", frm, older_to, 0)
        self.assertEqual(self.fake.count("/oms/instruments/historical/738561/15minute"), 2,
                         "the next page is fetched when the chart scrolls back")
        self.assertTrue(older)
        self.assertLess(older[-1][0], first[0][0])
        stamps = [b[0] for b in older + first]
        self.assertEqual(stamps, sorted(set(stamps)), "pages never repeat a bar")

    def test_day_requests_also_return_one_chunk_at_a_time(self):
        self.source.settings["historyDaysDaily"] = "100"
        frm, to = midnight("2026-06-01"), midnight("2026-11-01")
        first = self.source.bars("NSE:RELIANCE", "1D", frm, to, 0)
        self.assertEqual(self.fake.count("/oms/instruments/historical/738561/day"), 1)
        self.assertEqual([b[0] for b in first], sorted({b[0] for b in first}))

    def test_a_day_window_uses_the_day_endpoint_at_ist_midnight(self):
        frm, to = midnight("2026-10-01"), midnight("2026-10-30")
        bars = self.source.bars(SYMBOL, "1D", frm, to, 0)

        self.assertEqual(self.fake.count("/oms/instruments/historical/738561/day"), 1)
        self.assertEqual(bars[0], [midnight("2026-10-01"), 100.0, 110.0, 90.0, 105.0, 10])

    def test_count_back_keeps_the_newest_bars(self):
        frm, to = midnight("2026-10-01"), midnight("2026-10-06")
        bars = self.source.bars(SYMBOL, "15", frm, to, 20)

        self.assertEqual(len(bars), 20)
        self.assertEqual(bars[-1][0], self.source.bars(SYMBOL, "15", frm, to, 0)[-1][0])

    def test_a_rate_limit_is_retryable_not_a_silent_empty_chart(self):
        self.fake.history_status = 429
        with self.assertRaises(plugin.PluginError) as raised:
            self.source.bars(SYMBOL, "15", midnight("2026-10-01"), midnight("2026-10-02"), 0)
        self.assertEqual(raised.exception.code, -32005)

    def test_unknown_symbol_is_final(self):
        with self.assertRaises(plugin.PluginError) as raised:
            self.source.bars("NSE:NOPE", "15", midnight("2026-10-01"), midnight("2026-10-02"), 0)
        self.assertEqual(raised.exception.code, -32004)


class Logging(unittest.TestCase):
    def test_bars_requests_and_failures_are_logged(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            app = harness.make_plugin(source)
            source.instruments.ensure()
            app.sock.lines.clear()
            app.handle("data.bars", {"ticker": "NSE:RELIANCE", "resolution": "15",
                                     "from": midnight("2026-10-01"), "to": midnight("2026-10-06"),
                                     "countBack": 20})
            self.assertTrue(any("data.bars NSE:RELIANCE 15:" in line
                                for line in app.sock.log_lines()))
            app.sock.lines.clear()
            fake.history_status = 429
            with self.assertRaises(plugin.PluginError):
                app.handle("data.bars", {"ticker": "NSE:RELIANCE", "resolution": "15",
                                         "from": midnight("2026-10-01"), "to": midnight("2026-10-06"),
                                         "countBack": 20})
            self.assertTrue(any("history NSE:RELIANCE 15:" in line
                                for line in app.sock.log_lines()))
        finally:
            fake.close()


class Events(unittest.TestCase):
    def test_data_events_answer_the_plugin_api_shape(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            app = harness.make_plugin(source)
            source.instruments.ensure()
            answer = app.handle("data.events", {"ticker": "NSE:RELIANCE"})
            events = answer["events"]
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0], {"type": "dividend", "date": "2024-08-19", "amount": 10.0})
            self.assertEqual(events[1], {"type": "split", "date": "2024-10-28", "from": 1, "to": 2})

            calls = fake.count("/oms/instruments/events/738561")
            self.assertGreater(calls, 0)
            app.handle("data.events", {"ticker": "NSE:RELIANCE"})
            self.assertEqual(fake.count("/oms/instruments/events/738561"), calls, "events are cached per day")
            bars = app.handle("data.bars", {"ticker": "NSE:RELIANCE", "resolution": "1D",
                                            "from": midnight("2024-08-01"), "to": midnight("2024-11-01"),
                                            "countBack": 5})
            self.assertNotIn("events", bars)
        finally:
            fake.close()

    def test_changed_events_notify_the_app_with_the_whole_symbol(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            app = harness.make_plugin(source)
            source.instruments.ensure()
            source.events_for("NSE:RELIANCE")
            self.assertEqual(app.sock.payloads("data.eventsChanged"), [])
            token = source.instruments.row("NSE:RELIANCE")["token"]
            source._events_at[token] -= 2 * plugin.EVENTS_REFRESH_SEC  # due for a re-check
            fake.event_rows_changed = True
            answer = source.events_for("NSE:RELIANCE")
            self.assertEqual(len(answer), 3)
            self.assertEqual(app.sock.payloads("data.eventsChanged"),
                             [{"tickers": ["NSE:RELIANCE"]}])
        finally:
            fake.close()

    def test_open_interest_series_bars_and_quote(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            app = harness.make_plugin(source)
            source.instruments.ensure()
            frm, to = midnight("2026-10-01"), midnight("2026-10-06")
            bars = app.handle("data.bars", {"ticker": "NFO:NIFTY25NOVFUT_OI", "resolution": "1D",
                                            "from": frm, "to": to, "countBack": 5})
            self.assertTrue(bars["bars"])
            self.assertEqual(len(bars["bars"][0]), 5, "an OI bar has one repeated value")
            self.assertGreater(bars["bars"][0][1], 0)

            app.on_notification("data.subscribe", {"tickers": ["NFO:NIFTY25NOVFUT_OI"]})
            self.assertIn("NFO:NIFTY25NOVFUT_OI", source.oi_subscribed)
            packet = fake_kite.FakeWs.packet_full(779521, 250.0, oi=12345)
            tick = plugin.ZerodhaWS._parse_packet(packet, 100)
            self.assertEqual(tick["oi"], 12345)
            source.on_ticks([tick])
            oi_quotes = [q for q in app.sock.payloads("data.quote")
                         if q["ticker"] == "NFO:NIFTY25NOVFUT_OI"]
            self.assertTrue(oi_quotes)
            self.assertEqual(oi_quotes[-1]["price"], 12345)
            self.assertEqual(len(oi_quotes[-1]["minute"]), 6)
        finally:
            fake.close()


class Seconds(unittest.TestCase):
    def test_second_bars_use_kite_history_when_it_serves_them(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            source.instruments.ensure()
            at = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
            frm, to = int(at.timestamp()), int(at.timestamp()) + 300
            bars = source.bars("NSE:RELIANCE", "5S", frm, to, 10)
            self.assertEqual(len(bars), 10)
            self.assertEqual(bars[1][0] - bars[0][0], 5)
            self.assertEqual(fake.count("/oms/instruments/historical/738561/5second"), 1)
        finally:
            fake.close()

    def test_two_four_and_sixty_minute_come_from_kite_directly(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            source.instruments.ensure()
            at = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
            frm, to = int(at.timestamp()), int(at.timestamp()) + 3600
            for resolution, token in (("2", "2minute"), ("4", "4minute"), ("60", "60minute"),
                                      ("120", "2hour"), ("180", "3hour"), ("240", "4hour")):
                bars = source.bars("NSE:RELIANCE", resolution, frm, to, 5)
                self.assertTrue(bars)
                self.assertEqual(fake.count(f"/oms/instruments/historical/738561/{token}"), 1)
        finally:
            fake.close()

    def test_request_windows_are_clamped_to_kite_limits(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            source.instruments.ensure()
            source.settings["historyDaysMinute"] = "500"  # bigger than the hard cap
            to = datetime.now(IST)
            for resolution, days in (("1", 60), ("60", 400), ("120", 400), ("5S", 2)):
                frm = int((to - timedelta(days=200)).timestamp())
                source.bars("NSE:RELIANCE", resolution, frm, int(to.timestamp()), 10)
                req = fake.sent("/oms/instruments/historical/738561/" + plugin.INTERVALS[resolution])[-1]
                asked = datetime.strptime(urllib.parse.unquote(req["query"]["from"]),
                                          "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                self.assertLessEqual((to - asked).days, days + 1)
        finally:
            fake.close()

    def test_second_bars_fall_back_to_live_ticks(self):
        fake = fake_kite.FakeKite()
        fake.history_status = 400  # this deployment refuses seconds
        try:
            source = harness.make_source(fake)
            source.instruments.ensure()
            source.subscribed = {"NSE:RELIANCE"}
            source.on_ticks([{"instrument_token": 738561, "last_price": 2500.0,
                              "volume": 100, "ohlc": {"open": 2499.0, "high": 2501.0, "low": 2498.0}}])
            stamp = source._ticks["NSE:RELIANCE"][0][0]
            frm = int(stamp) // 5 * 5
            bars = source.bars("NSE:RELIANCE", "5S", frm, frm + 10, 10)
            self.assertEqual(bars[0][0], frm)
            self.assertEqual(bars[0][4], 2500.0)
            self.assertEqual(fake.count("/oms/instruments/historical/738561/5second"), 1,
                             "Kite was tried before the tick fallback")
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()
