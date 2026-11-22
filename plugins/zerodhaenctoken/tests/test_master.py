"""The instrument master: Kite's CSV normalised into app symbols, searchable, cached across restarts."""

import unittest
from datetime import timedelta

import fake_kite
import harness
import plugin


class Parse(unittest.TestCase):
    def test_index_segments_and_rows(self):
        rows = plugin.parse_master_csv(fake_kite.master_csv())
        by_key = {(r["exchange"], r["ticker"]): r for r in rows}
        self.assertEqual(by_key[("NSE", "RELIANCE")]["token"], 738561)
        self.assertEqual(by_key[("NSE_INDEX", "NIFTY")]["token"], 256265)
        self.assertEqual(by_key[("BSE_INDEX", "SENSEX")]["type"], "index")
        self.assertEqual(by_key[("NFO", "NIFTY25NOVFUT")]["lot"], 75)
        self.assertEqual(by_key[("NFO", "NIFTY25NOVFUT")]["kite_exchange"], "NFO")

    def test_mcx_uses_the_real_lot_not_kites_one(self):
        rows = plugin.parse_master_csv(fake_kite.master_csv())
        crude = next(r for r in rows if r["ticker"] == "CRUDEOIL25NOVFUT")
        self.assertEqual(crude["lot"], 100)
        self.assertEqual(crude["kite_exchange"], "MCX")

    def test_futures_description_has_no_zero_strike(self):
        rows = [fake_kite.row(1, "BFO", "BANKEX26NOVFUT", "BANKEX", "FUT", strike="0",
                              expiry="2026-11-26")]
        parsed = plugin.parse_master_csv(fake_kite.master_csv(rows))
        self.assertEqual(len(parsed), 1)
        description = parsed[0]["description"]
        self.assertNotIn(" 0 ", f" {description} ")
        self.assertIn("FUT", description)
        self.assertIn("2026-11-26", description)

    def test_gzip_master_is_readable(self):
        fake = fake_kite.FakeKite()
        fake.gzip_master = True
        try:
            source = harness.make_source(fake)
            self.assertEqual(len(source.instruments.ensure()), len(fake_kite.MASTER_ROWS))
        finally:
            fake.close()


class SearchAndInfo(unittest.TestCase):
    def setUp(self):
        self.fake = fake_kite.FakeKite()
        self.source = harness.make_source(self.fake)
        self.app = harness.make_plugin(self.source)

    def tearDown(self):
        self.fake.close()

    def test_search_returns_whole_symbols(self):
        rows = self.source.search("REL")
        self.assertEqual([r["ticker"] for r in rows], ["NSE:RELIANCE"])
        self.assertEqual(rows[0]["type"], "stock")

    def test_exchange_prefix_narrows(self):
        self.assertEqual([r["ticker"] for r in self.source.search("NSE:REL")], ["NSE:RELIANCE"])
        # The app filters rows by the exchange the user typed; the plugin may return matches on other
        # segments (NFO's NIFTY future also matches "NIFTY"), so only the wanted row is asserted here.
        self.assertIn("NSE_INDEX:NIFTY", [r["ticker"] for r in self.source.search("NSE_INDEX:NIFTY")])

    def test_empty_query_and_cap(self):
        rows = self.source.search("")
        self.assertLessEqual(len(rows), 50)
        self.assertIn("NSE:RELIANCE", [r["ticker"] for r in rows])

    def test_symbol_info_shapes(self):
        info = self.source.symbol_info("NSE:RELIANCE")
        self.assertEqual(info["currency"], "INR")
        self.assertEqual(info["timezone"], "Asia/Kolkata")
        self.assertEqual(info["session"], "0915-1530")
        self.assertEqual(info["pricescale"], 20)  # one tick = 0.05
        self.assertEqual(info["pointValue"], 1)
        self.assertIn("1", info["intervals"])
        self.assertIn("1D", info["intervals"])
        self.assertEqual(info["history"]["day"], 10950)
        self.assertEqual(info["history"]["minute"], 1825)
        self.assertEqual(self.source.symbol_info("NCO:GOLD")["history"], {"day": 10950})
        self.assertEqual(self.source.symbol_info("NFO:NIFTY25NOVFUT")["pointValue"], 75)
        self.assertEqual(self.source.symbol_info("NSE_INDEX:NIFTY")["type"], "index")

    def test_unknown_symbol_is_final(self):
        with self.assertRaises(plugin.PluginError) as raised:
            self.source.symbol_info("NSE:NOPE")
        self.assertEqual(raised.exception.code, -32004)


class Headers(unittest.TestCase):
    def test_requests_carry_ordinary_browser_headers(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            source.instruments.ensure()
            master = fake.sent("/instruments", "GET")[0]
            self.assertEqual(master["headers"]["user-agent"], plugin.BROWSER_UA)
            source._json("/oms/user/margins")
            oms = fake.sent("/oms/user/margins", "GET")[0]
            self.assertEqual(oms["headers"]["user-agent"], plugin.BROWSER_UA)
            self.assertEqual(oms["headers"]["x-kite-version"], plugin.KITE_VERSION)
            self.assertEqual(oms["headers"]["x-kite-userid"], "FAKE123")
            self.assertNotIn("opentrader", oms["headers"]["user-agent"].lower())
            self.assertEqual(oms["headers"]["sec-fetch-site"], "same-origin")
            self.assertEqual(oms["headers"]["origin"], "https://kite.zerodha.com")
            self.assertNotIn("cookie", oms["headers"])
        finally:
            fake.close()


class CompanyInfo(unittest.TestCase):
    def tearDown(self):
        plugin.COMPANY_API = "https://api.dharun.dev/v1"

    def test_equity_gets_logo_and_company_details_from_the_external_service(self):
        fake = fake_kite.FakeKite()
        plugin.COMPANY_API = fake.url + "/v1"
        try:
            source = harness.make_source(fake)
            info = source.symbol_info("NSE:RELIANCE")
            self.assertEqual(info["icon"], fake.url + "/v1/logos/RELIANCE")
            self.assertEqual(info["name"], "Reliance Industries Limited")
            self.assertEqual(info["sector"], "Energy")
            self.assertEqual(info["industry"], "Refining")
            self.assertEqual(info["longDescription"], "An Indian conglomerate.")
            source.symbol_info("NSE:RELIANCE")
            self.assertEqual(fake.count("/v1/companies/RELIANCE"), 1, "the profile is cached")
            self.assertNotIn("icon", source.symbol_info("NFO:NIFTY25NOVFUT"))
            self.assertNotIn("icon", source.symbol_info("NSE_INDEX:NIFTY"))
        finally:
            fake.close()

    def test_company_details_can_be_turned_off(self):
        fake = fake_kite.FakeKite()
        plugin.COMPANY_API = fake.url + "/v1"
        try:
            source = harness.make_source(fake)
            source.company_details = False
            info = source.symbol_info("NSE:RELIANCE")
            self.assertNotIn("icon", info)
            self.assertNotIn("sector", info)
            self.assertEqual(fake.count("/v1/companies/RELIANCE"), 0)
        finally:
            fake.close()

    def test_a_dead_external_service_never_breaks_symbol_info(self):
        fake = fake_kite.FakeKite()
        plugin.COMPANY_API = "http://127.0.0.1:1/v1"
        try:
            source = harness.make_source(fake)
            info = source.symbol_info("NSE:RELIANCE")
            self.assertEqual(info["type"], "stock")
            self.assertNotIn("sector", info)
        finally:
            fake.close()


class Nco(unittest.TestCase):
    def test_nco_offers_only_daily_intervals(self):
        rows = fake_kite.MASTER_ROWS + [fake_kite.row(268, "NCO", "GOLD", "GOLD", "EQ")]
        fake = fake_kite.FakeKite(rows=rows)
        try:
            source = harness.make_source(fake)
            info = source.symbol_info("NCO:GOLD")
            self.assertEqual(info["intervals"], ["1D"])
            self.assertEqual(info["history"], {"day": 10950})
            self.assertEqual(source.symbol_info("NSE:RELIANCE")["intervals"],
                             plugin.SERVED_INTERVALS)
        finally:
            fake.close()


class ListingDepth(unittest.TestCase):
    def test_history_depth_is_bounded_by_listing_date(self):
        fake = fake_kite.FakeKite()
        equity_url, sme_url = plugin.NSE_EQUITY_LIST_URL, plugin.NSE_SME_LIST_URL
        plugin.NSE_EQUITY_LIST_URL = fake.url + "/nse/EQUITY_L.csv"
        plugin.NSE_SME_LIST_URL = fake.url + "/nse/SME_EQUITY_L.csv"
        try:
            source = harness.make_source(fake)
            old = source.symbol_info("NSE:RELIANCE")["history"]
            self.assertLess(old["day"], plugin.HISTORY_DAYS_MAX["day"])
            self.assertEqual(old["minute"], plugin.HISTORY_DAYS_MAX["minute"])

            new_info = source.symbol_info("NSE:NEWCO")
            new = new_info["history"]
            self.assertLessEqual(new["day"], 31)
            self.assertLessEqual(new["minute"], 31)
            self.assertIn("Listed ", new_info["longDescription"])
            self.assertRegex(new_info["longDescription"], r"Listed \d{2}/\d{2}/\d{4}")

            source.symbol_info("NSE:TCS")  # TCS isn't in the fake list: falls back to the max
            self.assertEqual(source.symbol_info("NSE:TCS")["history"]["day"],
                             plugin.HISTORY_DAYS_MAX["day"])
            self.assertEqual(fake.count("/nse/EQUITY_L.csv"), 1, "listing lists are cached per day")
            self.assertEqual(fake.count("/nse/SME_EQUITY_L.csv"), 1)
        finally:
            plugin.NSE_EQUITY_LIST_URL, plugin.NSE_SME_LIST_URL = equity_url, sme_url
            fake.close()


class CachePruning(unittest.TestCase):
    def test_keeps_recent_master_and_todays_events(self):
        harness.fresh_cache()
        cache = plugin.plugin_data_dir() / "cache"
        events = cache / "events"
        events.mkdir(parents=True, exist_ok=True)
        today = plugin.datetime.now(plugin.TZ_IST).date()
        keep = {
            f"master-{plugin.PLUGIN_VERSION}-{today}.json",
            f"master-{plugin.PLUGIN_VERSION}-{today - timedelta(days=1)}.json",
        }
        drop = {
            f"master-{plugin.PLUGIN_VERSION}-{today - timedelta(days=5)}.json",
            f"master-0.0.1-{today}.json",
        }
        for name in keep | drop:
            (cache / name).write_text("{}")
        current_event, old_event = events / f"738561-{today}.json", events / f"738561-{today - timedelta(days=3)}.json"
        current_event.write_text("[]")
        old_event.write_text("[]")

        plugin.ZerodhaSource()  # prunes on startup

        for name in keep:
            self.assertTrue((cache / name).exists(), name)
        for name in drop:
            self.assertFalse((cache / name).exists(), name)
        self.assertTrue(current_event.exists())
        self.assertFalse(old_event.exists())


class Cache(unittest.TestCase):
    def test_master_is_fetched_once_per_day(self):
        fake = fake_kite.FakeKite()
        folder = harness.fresh_cache()
        try:
            first = plugin.ZerodhaSource()
            first.settings = harness.settings(fake)
            first.enctoken = harness.KEY
            first.exchanges = first.settings["exchanges"].split(",")
            self.assertEqual(len(first.instruments.ensure()), len(fake_kite.MASTER_ROWS))
            self.assertEqual(fake.count("/instruments"), 1)

            second = plugin.ZerodhaSource()
            second.settings = harness.settings(fake)
            second.enctoken = harness.KEY
            second.exchanges = second.settings["exchanges"].split(",")
            self.assertEqual(len(second.instruments.ensure()), len(fake_kite.MASTER_ROWS))
            self.assertEqual(fake.count("/instruments"), 1, "the second start re-reads the cached master")
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()
