"""History windows: one honest call unless the request has to be chunked, and Kite's IST timestamps intact."""

import unittest
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

    def test_an_unserved_resolution_is_refused(self):
        with self.assertRaises(plugin.PluginError) as raised:
            self.source._interval_for("60")
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

    def test_day_windows_are_chunked_by_the_daily_cap(self):
        self.source.settings["historyDaysDaily"] = "100"
        frm, to = midnight("2026-06-01"), midnight("2026-11-01")
        bars = self.source.bars("NSE:RELIANCE", "1D", frm, to, 0)
        self.assertEqual(self.fake.count("/oms/instruments/historical/738561/day"), 2)
        stamps = [b[0] for b in bars]
        self.assertEqual(stamps, sorted(set(stamps)))

    def test_a_window_wider_than_the_cap_is_chunked_without_repeats(self):
        self.source.settings["historyDaysMinute"] = "2"
        frm, to = midnight("2026-10-01"), midnight("2026-10-05")
        bars = self.source.bars(SYMBOL, "15", frm, to, 0)

        self.assertEqual(self.fake.count("/oms/instruments/historical/738561/15minute"), 3)
        stamps = [b[0] for b in bars]
        self.assertEqual(stamps, sorted(set(stamps)))
        self.assertEqual(stamps[0], int(datetime(2026, 10, 1, 9, 15, tzinfo=IST).timestamp()))

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
    def test_daily_bars_carry_corporate_events_and_minute_bars_do_not(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            app = harness.make_plugin(source)
            source.instruments.ensure()
            args = {"ticker": "NSE:RELIANCE", "from": midnight("2024-08-01"),
                    "to": midnight("2024-11-01"), "countBack": 5}
            daily = app.handle("data.bars", {**args, "resolution": "1D"})
            events = daily["events"]
            self.assertEqual(len(events), 2)
            self.assertEqual([e["action"] for e in events], ["dividend", "bonus"])
            self.assertEqual(events[0]["kind"], "dividend")
            self.assertEqual(events[0]["label"], "10.00")
            self.assertEqual(events[1]["kind"], "split")
            self.assertEqual(events[1]["label"], "1:1 bonus")

            calls = fake.count("/oms/instruments/events/738561")
            self.assertGreater(calls, 0)
            app.handle("data.bars", {**args, "resolution": "1D"})
            self.assertEqual(fake.count("/oms/instruments/events/738561"), calls, "events are cached per day")
            minute = app.handle("data.bars", {**args, "resolution": "15"})
            self.assertNotIn("events", minute)
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()
