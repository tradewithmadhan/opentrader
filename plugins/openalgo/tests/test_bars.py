"""History windows: one honest call unless the deployment caps the window, and then the cap is
a setting (`historyDaysMinute`, `historyDaysSeconds`, `historyDaysDaily`), never a constant.
"""

import unittest

import fake_openalgo
import harness
import plugin

SYMBOL = "NSE:RELIANCE"


def midnight(day):
    return int(fake_openalgo.ist_day(day).timestamp())


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
    """The deployment's D endpoint answers one row per window: the days are synthesized from
    the hour bars it serves fully."""

    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)

    def tearDown(self):
        self.fake.close()

    def test_days_are_folded_from_the_hours_the_source_serves(self):
        frm, to = midnight("2026-10-01"), midnight("2026-10-05")
        bars = self.source.bars(SYMBOL, "1D", frm, to, 0)

        days = [b[0] for b in bars]
        self.assertEqual(days, [midnight("2026-10-01"), midnight("2026-10-02"), midnight("2026-10-05")],
                         "weekdays only, one bar per trading day")
        first = bars[0]
        self.assertEqual(first[1], 100.0, "open: the first hour's open")
        self.assertEqual(first[4], 111.0, "close: the last hour's close")
        self.assertEqual(first[2], 116.0, "high: the hours' highest")
        self.assertEqual(first[3], 90.0, "low: the hours' lowest")
        self.assertEqual(first[5], 70.0, "volume: the hours summed")
        self.assertTrue(self.source._d_synth.get("NSE"), "the probe is remembered per exchange")

    def test_short_day_windows_keep_the_source_row(self):
        frm, to = midnight("2026-10-01"), midnight("2026-10-03")
        bars = self.source.bars(SYMBOL, "1D", frm, to, 0)
        self.assertEqual(len(bars), 1, "a 2-day window takes the D endpoint's own answer")
        self.assertFalse(self.source._d_synth.get("NSE"))


if __name__ == "__main__":
    unittest.main()
