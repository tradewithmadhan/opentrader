"""NSE reports: one session warms the site and its cookies are sent on every report GET."""

import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import harness  # noqa: F401 - puts the plugin directory on sys.path
import nse

CSV = ("SYMBOL,DATE OF LISTING,ISIN NUMBER,FACE VALUE,SERIES\n"
       "RELIANCE,06-OCT-2008,INE002A01018,10,EQ\n")


class Handler(BaseHTTPRequestHandler):
    homes = 0
    reports = 0
    refuse_first = False

    def do_GET(self):
        if self.path == "/":
            type(self).homes += 1
            self.send_response(200)
            self.send_header("Set-Cookie", f"nse_session=c{type(self).homes}; Path=/")
            self.end_headers()
            self.wfile.write(b"home")
        elif self.path.startswith("/report"):
            type(self).reports += 1
            cookie = self.headers.get("Cookie") or ""
            if not cookie or (type(self).refuse_first and cookie.endswith("c1")):
                self.send_response(403)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.end_headers()
            self.wfile.write(CSV.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


class NseReports(unittest.TestCase):
    def setUp(self):
        Handler.homes = Handler.reports = 0
        Handler.refuse_first = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_port}"
        self.saved = nse.WARM_URLS, nse.REPORTS, nse.REPORT_HEADERS
        nse.WARM_URLS = (base + "/",)
        nse.REPORTS = {"equity": base + "/report?name=equity", "sme": base + "/report?name=sme"}
        nse.REPORT_HEADERS = {k: {"Accept": "text/csv,*/*"} for k in nse.REPORTS}

    def tearDown(self):
        nse.WARM_URLS, nse.REPORTS, nse.REPORT_HEADERS = self.saved
        self.server.shutdown()
        self.server.server_close()

    def test_security_list_url_previous_day_and_parser(self):
        from datetime import date
        self.assertEqual(nse.security_list_url(date(2026, 11, 25)),
                         "https://nsearchives.nseindia.in/content/equities/sec_list_25112026.csv")
        self.assertEqual(nse.previous_trading_day(date(2026, 11, 26)), date(2026, 11, 25))
        self.assertEqual(nse.previous_trading_day(date(2026, 11, 30)), date(2026, 11, 27))
        text = ('Symbol,Series,Security Name,Band,Remarks\n'
                'RELIANCE,EQ,RELIANCE INDUSTRIES,20,-\n'
                'NEWCO,SM,NEW CO,2,"GSM STAGE - 0"\n')
        self.assertEqual(nse.parse_security_list(text), [
            {"symbol": "RELIANCE", "series": "EQ", "band": "20", "remarks": "-"},
            {"symbol": "NEWCO", "series": "SM", "band": "2", "remarks": "GSM STAGE - 0"},
        ])

    def test_volume_gainers_and_announcements(self):
        self.assertIn("index=equities", nse.corporate_announcements_url("equities"))
        self.assertEqual(nse.parse_volume_gainers('{"data":[{"symbol":"RELIANCE"},{"symbol":"TCS"}]}'),
                         ["RELIANCE", "TCS"])
        payload = ('[{"symbol":"RELIANCE","desc":"Dividend","an_dt":"26-Nov-2026"},'
                   '{"bm_symbol":"NEWCO","desc":"Board Meeting","an_dt":"26-Nov-2026"}]')
        self.assertEqual(nse.parse_corporate_announcements(payload), [
            {"symbol": "RELIANCE", "purpose": "Dividend", "date": "26-Nov-2026"},
            {"symbol": "NEWCO", "purpose": "Board Meeting", "date": "26-Nov-2026"},
        ])

    def test_event_calendar_url_and_both_row_shapes(self):
        day = "26-11-2026"
        self.assertEqual(nse.event_calendar_url(), "https://www.nseindia.in/api/event-calendar")
        self.assertIn("index=equities", nse.event_calendar_url("equities", day, day))
        self.assertIn("from_date=26-11-2026", nse.event_calendar_url("sme", day, day))
        payload = '[{"symbol":"RELIANCE","purpose":"Dividend"},{"bm_symbol":"NEWCO","bm_purpose":"Board Meeting"},{"symbol":"RELIANCE","purpose":"Dividend"}]'
        self.assertEqual(nse.parse_event_calendar(payload), ["NEWCO", "RELIANCE"])
        self.assertEqual(nse.event_calendar_rows(payload), [
            {"symbol": "RELIANCE", "purpose": "Dividend"},
            {"symbol": "NEWCO", "purpose": "Board Meeting"},
        ])
        self.assertEqual(nse.parse_event_calendar("bad"), [])

    def test_price_band_hitters_are_parsed_from_both_sides(self):
        payload = ('{"upper":{"AllSec":{"data":[{"symbol":"RELIANCE"},{"symbol":"TCS"}]}},'
                   '"lower":{"AllSec":{"data":[{"symbol":"INFY"},{"symbol":"TCS"}]}},'
                   '"both":{"AllSec":{"data":[]}}}')
        self.assertEqual(nse.parse_price_band_hitters(payload), ["INFY", "RELIANCE", "TCS"])
        self.assertEqual(nse.parse_price_band_hitters("not json"), [])

    def test_session_cookie_is_used_on_the_report(self):
        text = nse.Client().get("equity")
        self.assertIn("RELIANCE", text)
        self.assertEqual(Handler.homes, 1, "the site is warmed once for the shared session")

    def test_one_refusal_rebuilds_the_session_and_retries(self):
        Handler.refuse_first = True
        text = nse.Client().get("equity")
        self.assertIn("RELIANCE", text)
        self.assertEqual(Handler.homes, 2, "a refusal drops the cookies and warms again")
        self.assertEqual(Handler.reports, 2)

    def test_listings_are_parsed_and_cached_for_the_day(self):
        client = nse.Client()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nse-listings-today.json"
            rows = nse.load_listings(client, path)
            self.assertIn("RELIANCE", rows)
            self.assertEqual(rows["RELIANCE"]["isin"], "INE002A01018")
            self.assertEqual(rows["RELIANCE"]["series"], "EQ")
            self.assertTrue(path.exists())
            self.server.shutdown()  # a second read comes from the cache, not the server
            self.assertEqual(nse.load_listings(nse.Client(), path), rows)


if __name__ == "__main__":
    unittest.main()
