"""History windows: one honest call unless the deployment caps the window, and then the cap is
a setting (`historyDaysMinute`, `historyDaysSeconds`, `historyDaysDaily`), never a constant.
"""

import unittest
from unittest.mock import patch

import fake_openalgo
import harness
import plugin

SYMBOL = "NSE:RELIANCE"


def midnight(day):
    return int(fake_openalgo.ist_day(day).timestamp())


class Bars(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)

    def tearDown(self):
        self.fake.close()

    def test_a_zero_row_is_not_a_bar(self):
        rows = [
            {"timestamp": 1000000, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10},
            {"timestamp": 2000000, "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0},
            {"timestamp": 3000000, "open": 100, "high": 0, "low": 0, "close": 100, "volume": 10},
        ]
        with patch.object(self.source, "_post", return_value=rows):
            bars = self.source._window_fetch("NSE", "RELIANCE", "D", True, 0, 4000000, 10, 0)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0][1:5], [100, 101, 99, 100])


class Intervals(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)

    def tearDown(self):
        self.fake.close()

    def test_resolutions_map_to_the_source_tokens(self):
        self.assertEqual(self.source._interval_for("15"), "15m")
        self.assertEqual(self.source._interval_for("60"), "1h")
        self.assertEqual(self.source._interval_for("1D"), "D")
        self.assertEqual(self.source._interval_for("1W"), "W")
        self.assertEqual(self.source._interval_for("5S"), "5s")
        self.assertEqual(self.source._interval_for("1"), "1m")

    def test_an_unserved_resolution_is_refused(self):
        with self.assertRaises(plugin.PluginError) as raised:
            self.source._interval_for("30")
        self.assertEqual(raised.exception.code, -32602)

    def test_served_intervals_are_declared(self):
        intervals = self.source._served_intervals()
        for declared in ("1", "3", "5", "15", "60", "5S", "1D"):
            self.assertIn(declared, intervals)


class Windows(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)

    def tearDown(self):
        self.fake.close()

    def test_one_call_serves_a_window_inside_the_cap(self):
        frm, to = midnight("2026-10-01"), midnight("2026-10-06")
        bars = self.source.bars(SYMBOL, "15", frm, to, 0)

        self.assertEqual(self.fake.count("/api/v1/history"), 1,
                         "the API serves the whole range at once; the window fits the 30-day cap")
        self.assertEqual(len(bars), self.fake.expected_bars("15m", "2026-10-01", "2026-10-06"))
        self.assertEqual(bars[0][0],
                         int(fake_openalgo.ist_day("2026-10-01").replace(hour=9, minute=15).timestamp()))
        self.assertEqual(bars[-1][4], 105.0 + len(bars) - 1)  # closes ascend: sorted, intact

    def test_a_month_fits_one_call_under_the_default_cap(self):
        frm, to = midnight("2026-10-01"), midnight("2026-10-30")
        bars = self.source.bars(SYMBOL, "1", frm, to, 0)

        self.assertEqual(self.fake.count("/api/v1/history"), 1,
                         "30 days of 1m is one call (verified live on the real deployment)")
        self.assertEqual(len(bars), self.fake.expected_bars("1m", "2026-10-01", "2026-10-30"))

    def test_the_minute_cap_is_the_setting(self):
        frm, to = midnight("2026-10-01"), midnight("2026-10-06")
        self.source.settings["historyDaysMinute"] = "2"
        bars = self.source.bars(SYMBOL, "15", frm, to, 0)

        self.assertEqual(self.fake.count("/api/v1/history"), 3, "5 days at 2 days a call")
        self.assertEqual(len(bars), self.fake.expected_bars("15m", "2026-10-01", "2026-10-06"))
        stamps = [b[0] for b in bars]
        self.assertEqual(stamps, sorted(set(stamps)), "chunk boundaries never repeat a bar")

    def test_a_blank_or_bad_cap_keeps_the_default(self):
        for value in ("", "0", "-3", "many"):
            self.source.settings["historyDaysMinute"] = value
            self.assertEqual(self.source._cap("historyDaysMinute", 30), 30)

    def test_the_seconds_cap_is_the_setting(self):
        frm, to = midnight("2026-10-02"), midnight("2026-10-03")  # Friday, then Saturday
        self.source.settings["historyDaysSeconds"] = "1"
        bars = self.source.bars(SYMBOL, "5S", frm, to, 0)

        self.assertEqual(self.fake.count("/api/v1/history"), 2, "2 days at 1 day a call")
        self.assertEqual(len(bars), self.fake.expected_bars("5s", "2026-10-02", "2026-10-03"))

    def test_a_truncating_source_is_paged_until_the_window_spans(self):
        frm, to = midnight("2026-10-01"), midnight("2026-10-26")
        expected = self.fake.expected_bars("1m", "2026-10-01", "2026-10-26")
        self.fake.history_max_days = 10  # this deployment answers only a call's last 10 days
        bars = self.source.bars(SYMBOL, "1", frm, to, 0)

        self.assertGreater(self.fake.count("/api/v1/history"), 1, "one truncated answer is not the window")
        self.assertEqual(len(bars), expected)
        self.assertEqual(bars[0][0],
                         int(fake_openalgo.ist_day("2026-10-01").replace(hour=9, minute=15).timestamp()))

    def test_count_back_keeps_the_newest_bars(self):
        frm, to = midnight("2026-10-01"), midnight("2026-10-06")
        bars = self.source.bars(SYMBOL, "15", frm, to, 20)

        self.assertEqual(len(bars), 20)
        self.assertEqual(bars[-1][0], self.source.bars(SYMBOL, "15", frm, to, 0)[-1][0])


class Daily(unittest.TestCase):
    """The source's D endpoint serves a whole range like the intraday ones: one call, no synthesis
    from the hour bars (0.1.19 — the old fallback cost a slow 1h request per day chart and timed
    out on MCX)."""

    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)

    def tearDown(self):
        self.fake.close()

    def test_a_day_window_is_one_d_call(self):
        frm, to = midnight("2026-10-01"), midnight("2026-10-30")
        bars = self.source.bars(SYMBOL, "1D", frm, to, 0)

        self.assertEqual(self.fake.count("/api/v1/history"), 1)
        self.assertEqual(self.fake.sent("/api/v1/history")[0]["body"]["interval"], "D")
        self.assertEqual(len(bars), self.fake.expected_bars("D", "2026-10-01", "2026-10-30"))
        self.assertEqual(bars[0], [midnight("2026-10-01"), 100.0, 110.0, 90.0, 105.0, 10.0],
                         "the source's own day bar, at IST midnight")

    def test_history_gets_the_long_timeout(self):
        seen = {}
        original = self.source._rest

        def spy(method, path, body, timeout=None):
            seen[(method, path)] = timeout
            return original(method, path, body, timeout)

        self.source._rest = spy
        self.source.bars(SYMBOL, "1D", midnight("2026-10-01"), midnight("2026-10-05"), 0)

        self.assertEqual(seen[("POST", "/api/v1/history")], plugin.OpenAlgoSource.HISTORY_TIMEOUT,
                         "a cold history call takes ~16 s; the 10 s default would drop the chart")
        self.assertGreater(plugin.OpenAlgoSource.HISTORY_TIMEOUT, plugin.OpenAlgoSource.REST_TIMEOUT)

    def test_a_window_wider_than_a_year_walks_d_by_year_and_caches(self):
        frm, to = midnight("2024-01-01"), midnight("2026-01-01")
        bars = self.source.bars(SYMBOL, "1D", frm, to, 0)

        calls = self.fake.sent("/api/v1/history")
        self.assertGreaterEqual(len(calls), 2, "one call per year walked")
        self.assertTrue(all(c["body"]["interval"] == "D" for c in calls),
                        "the D endpoint answers every chunk: no hour or minute synthesis")
        self.assertGreater(len(bars), 400)
        self.assertEqual(bars[0][0], midnight("2024-01-01"))
        self.assertEqual(bars[-1][0], midnight("2026-01-01"))

        again = self.source.bars(SYMBOL, "1D", frm, to, 0)
        self.assertEqual(len(self.fake.sent("/api/v1/history")), len(calls),
                         "a year already walked is cached")
        self.assertEqual(again, bars)


if __name__ == "__main__":
    unittest.main()
