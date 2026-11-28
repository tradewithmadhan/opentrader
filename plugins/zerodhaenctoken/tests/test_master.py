"""The instrument master: Kite's CSV normalised into app symbols, searchable, cached across restarts."""

import os
import socket
import unittest
from datetime import timedelta

import fake_kite
import harness
import plugin


class Network(unittest.TestCase):
    def test_ipv4_is_preferred_with_ipv6_fallback(self):
        v6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))
        v4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))

        def fake(host, port, family=0, *args, **kwargs):
            return [v4] if family == socket.AF_INET else [v6, v4]

        old = plugin._original_getaddrinfo
        plugin._original_getaddrinfo = fake
        try:
            self.assertEqual(plugin._ipv4_first_getaddrinfo("example.test", 443)[0][0], socket.AF_INET)
        finally:
            plugin._original_getaddrinfo = old


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
        self.assertEqual({r["ticker"] for r in rows}, {"NSE:RELIANCE", "NFO:RELIANCE26DECFUT"})
        self.assertEqual(next(r["type"] for r in rows if r["ticker"] == "NSE:RELIANCE"), "stock")

    def test_multi_word_option_query_matches_the_description(self):
        rows = self.source.search("NIFTY 25000")
        self.assertIn("NFO:NIFTY25NOV25000CE", [r["ticker"] for r in rows])
        self.assertEqual(
            next(r["description"] for r in rows if r["ticker"] == "NFO:NIFTY25NOV25000CE"),
            "NIFTY 25000 CE 2026-11-25")

    def test_exchange_prefix_narrows(self):
        found = {r["ticker"] for r in self.source.search("NSE:REL")}
        self.assertIn("NSE:RELIANCE", found)
        self.assertIn("NFO:RELIANCE26DECFUT", found)
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
        for iv in ("2", "4", "60", "120", "180", "240", "5S", "10S", "15S", "30S", "45S"):
            self.assertIn(iv, info["intervals"])
        self.assertNotIn("1S", info["intervals"], "no one-second history source")
        self.assertEqual(info["history"]["day"], 10950)
        self.assertEqual(info["history"]["minute"], plugin.HISTORY_DAYS_MAX["minute"])
        self.assertEqual(self.source.symbol_info("NCO:GOLD")["history"], {"day": 10950})
        self.assertEqual(self.source.symbol_info("NFO:NIFTY25NOVFUT")["pointValue"], 75)
        self.assertEqual(self.source.symbol_info("NSE_INDEX:NIFTY")["type"], "index")

    def test_listing_columns_are_offered_as_instrument_fields(self):
        equity_url, sme_url = plugin.NSE_EQUITY_LIST_URL, plugin.NSE_SME_LIST_URL
        plugin.NSE_EQUITY_LIST_URL = self.fake.url + "/nse/EQUITY_L.csv"
        plugin.NSE_SME_LIST_URL = self.fake.url + "/nse/SME_EQUITY_L.csv"
        try:
            info = self.source.symbol_info("NSE:RELIANCE")
            fields = {f["name"]: f["value"] for f in info["fields"]}
            self.assertEqual(info["isin"], "INE002A01018")
            self.assertEqual(fields["Face value"], "10")
            self.assertEqual(fields["Series"], "EQ")
            self.assertRegex(fields["Listed"], r"^\d{2}/\d{2}/\d{4}$")
            self.assertNotIn("ISIN", fields)
            self.assertNotIn("Tick size", fields)
            fut = self.source.symbol_info("NFO:NIFTY25NOVFUT")
            self.assertNotIn("ISIN", [f["name"] for f in fut["fields"]])
        finally:
            plugin.NSE_EQUITY_LIST_URL, plugin.NSE_SME_LIST_URL = equity_url, sme_url

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
            self.assertNotIn("sector", info, "the first answer must not wait on the external service")
            self.assertTrue(harness.wait_until(lambda: "RELIANCE" in source._profiles, 3.0))
            info = source.symbol_info("NSE:RELIANCE")
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

    def test_company_profile_is_fetched_once_and_cached_on_disk(self):
        import tempfile
        fake = fake_kite.FakeKite()
        plugin.COMPANY_API = fake.url + "/v1"
        folder = tempfile.mkdtemp(prefix="zerodha-profiles-")
        try:
            source = harness.make_source(fake)
            plugin.local_app_data = lambda: folder  # make_source uses a throwaway cache; pin it for this test
            source._profiles = source._read_profile_cache()
            source._company_profile("RELIANCE")
            source._company_profile("RELIANCE")
            self.assertEqual(fake.count("/v1/companies/RELIANCE"), 1)
            self.assertTrue(source._profile_cache_path().exists())
            other = harness.make_source(fake)
            plugin.local_app_data = lambda: folder
            other._profiles = other._read_profile_cache()
            self.assertEqual(other._profiles["RELIANCE"]["sector"], "Energy")
            self.assertEqual(fake.count("/v1/companies/RELIANCE"), 1, "a restart must not fetch it again")
        finally:
            fake.close()
            plugin.COMPANY_API = "https://api.dharun.dev/v1"

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
            self.assertEqual(old["minute"], old["day"], "both families are listing-bounded by the same cap")

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


class PluginApi7(unittest.TestCase):
    def test_manifest_and_source_speak_api_12(self):
        import json
        root = os.path.dirname(os.path.abspath(plugin.__file__))
        for name in ("opentrader-plugin.json", "opentrader-plugin.dev.json"):
            manifest = json.load(open(os.path.join(root, name), encoding="utf-8"))
            self.assertEqual(manifest["api"], 12)
        self.assertEqual(plugin.API, 12)

    def test_nfo_session_runs_to_1540(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            self.assertEqual(source.symbol_info("NFO:NIFTY25NOVFUT")["session"], "0915-1540")
            self.assertEqual(source.symbol_info("NSE:RELIANCE")["session"], "0915-1530")
        finally:
            fake.close()

    def test_continuous_future_maps_to_the_front_contract(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            source.instruments.ensure()
            info = source.symbol_info("NFO:NIFTY1!")
            self.assertEqual(info["tradeTicker"], "NFO:NIFTY26DECFUT")
            self.assertEqual(info["root"], "NIFTY")
            self.assertEqual(info["expiration"], "2026-12-29")
            self.assertEqual(info["baseCurrency"], "INR")
            self.assertIn("NFO:NIFTY1!", source.instruments.continuous)
            self.assertEqual(source.instruments.row("NFO:NIFTY1!")["token"],
                             source.instruments.row("NFO:NIFTY26DECFUT")["token"])
        finally:
            fake.close()

    def test_root_and_expiration_cover_all_and_derivatives(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            source.instruments.ensure()
            equity = source.symbol_info("NSE:RELIANCE")
            self.assertEqual(equity["type"], "stock")
            self.assertEqual(equity["root"], "RELIANCE")
            self.assertNotIn("expiration", equity)
            future = source.symbol_info("NFO:NIFTY26DECFUT")
            self.assertEqual(future["type"], "futures")
            self.assertEqual(future["root"], "NIFTY")
            self.assertEqual(future["expiration"], "2026-12-29")
            option = source.symbol_info("NFO:NIFTY25NOV25000CE")
            self.assertEqual(option["type"], "option")
            self.assertEqual(option["root"], "NIFTY")
            self.assertEqual(option["expiration"], "2026-11-25")
        finally:
            fake.close()

    def test_continuous_symbol_only_in_search_when_asked(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            source.instruments.ensure()
            plain = {r["ticker"] for r in source.search("NIFTY")}
            asked = {r["ticker"] for r in source.search("NIFTY1!")}
            self.assertNotIn("NFO:NIFTY1!", plain)
            self.assertIn("NFO:NIFTY1!", asked)
        finally:
            fake.close()

    def test_watchlists_are_whole_symbols_and_capped(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            source.instruments.ensure()
            lists = source.watchlists()
            self.assertEqual({w["id"] for w in lists},
                             {"zerodha-blue-chips", "zerodha-indices", "zerodha-nse-indices",
                              "zerodha-bse-indices"})
            for item in lists:
                self.assertLessEqual(len(item["symbols"]) + sum(len(s["symbols"]) for s in item["sections"]), 1000)
                for sym in item["symbols"] + [x for s in item["sections"] for x in s["symbols"]]:
                    self.assertIn(":", sym)
            self.assertTrue(any("NSE:RELIANCE" in item["symbols"] for item in lists))
        finally:
            fake.close()

    def test_symbol_info_declares_series_and_fields(self):
        fake = fake_kite.FakeKite()
        try:
            source = harness.make_source(fake)
            fut = source.symbol_info("NFO:NIFTY25NOVFUT")
            self.assertEqual(fut["series"], ["OI"])
            self.assertEqual([f["name"] for f in fut["fields"]], ["Expiry"])
            self.assertIn("pointValue", fut)
            eq = source.symbol_info("NSE:RELIANCE")
            self.assertEqual(eq["series"], [])
            self.assertNotIn("Tick size", [f["name"] for f in eq["fields"]])
        finally:
            fake.close()


class CachePruning(unittest.TestCase):
    def test_keeps_only_todays_master_listings_and_events(self):
        harness.fresh_cache()
        cache = plugin.plugin_data_dir() / "cache"
        events = cache / "events"
        events.mkdir(parents=True, exist_ok=True)
        today = plugin.datetime.now(plugin.TZ_IST).date()
        keep = {
            f"master-{plugin.PLUGIN_VERSION}-{today}.json",
        }
        drop = {
            f"master-{plugin.PLUGIN_VERSION}-{today - timedelta(days=1)}.json",
            f"master-{plugin.PLUGIN_VERSION}-{today - timedelta(days=5)}.json",
        }
        for name in keep | drop:
            (cache / name).write_text("{}")
        current_event, old_event = events / f"738561-{today}.json", events / f"738561-{today - timedelta(days=3)}.json"
        current_event.write_text("[]")
        old_event.write_text("[]")
        current_list, old_list = (cache / f"nse-listings-{today}.json",
                                  cache / f"nse-listings-{today - timedelta(days=1)}.json")
        current_list.write_text("{}")
        old_list.write_text("{}")

        plugin.ZerodhaSource()  # prunes on startup

        for name in keep:
            self.assertTrue((cache / name).exists(), name)
        for name in drop:
            self.assertFalse((cache / name).exists(), name)
        self.assertTrue(current_event.exists())
        self.assertFalse(old_event.exists())
        self.assertTrue(current_list.exists())
        self.assertFalse(old_list.exists(), "yesterday's listing CSV is removed too")


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
