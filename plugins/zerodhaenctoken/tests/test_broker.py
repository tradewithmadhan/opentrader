"""Trading over Kite's personal /oms API: books, orders and postbacks mapped to the app's broker protocol."""

import unittest

import fake_kite
import harness
import plugin

FUT = "NFO:NIFTY25NOVFUT"  # master lot = 75
EQ = "NSE:RELIANCE"


def order_row(**extra):
    row = {
        "order_id": "O1", "tradingsymbol": "NIFTY25NOVFUT", "exchange": "NFO",
        "transaction_type": "BUY", "order_type": "LIMIT", "quantity": 150,
        "filled_quantity": 0, "price": 250.0, "trigger_price": 0, "product": "NRML",
        "status": "OPEN", "validity": "DAY", "variety": "regular",
        "order_timestamp": "2026-11-21 10:00:00",
    }
    row.update(extra)
    return row


class Books(unittest.TestCase):
    def setUp(self):
        self.fake = fake_kite.FakeKite()
        self.source = harness.make_broker_source(self.fake)
        self.app = harness.make_plugin(self.source)
        self.source.instruments.ensure()

    def tearDown(self):
        self.app.alive = False
        self.fake.close()

    def test_state_before_any_read_says_not_connected(self):
        state = self.source.state()
        self.assertFalse(state["connected"])
        self.assertEqual(state["positions"], [])
        self.assertEqual(state["capabilities"]["orderTypes"],
                         ["market", "limit", "stop", "stopLimit"])
        self.assertIs(state["account"]["isDemo"], False)

    def test_first_poll_reads_the_books_in_units(self):
        self.fake.position_rows = [{
            "tradingsymbol": "NIFTY25NOVFUT", "exchange": "NFO", "quantity": 150,
            "average_price": 250.0, "last_price": 260.0, "pnl": 1500.0,
        }]
        self.fake.order_rows = [order_row()]
        events = self.source.on_tick()
        state = self.source.state()

        self.assertEqual(events, [], "an already-working order is not a transition")
        self.assertTrue(state["connected"])
        self.assertEqual(state["account"]["balance"], 250000.0)
        self.assertEqual(state["positions"][0]["qty"], 150, "NFO quantities are already units (75 = one lot)")
        self.assertEqual(state["orders"][0]["qty"], 150)
        self.assertEqual(state["orders"][0]["status"], "working")
        self.assertEqual(self.fake.count("/oms/user/margins"), 1)

    def test_holdings_are_offered_as_an_api_7_broker_page(self):
        self.fake.holding_rows = [{
            "tradingsymbol": "RELIANCE", "exchange": "NSE", "isin": "INE002A01018",
            "product": "CNC", "quantity": 10, "t1_quantity": 10,
            "average_price": 2450.5, "last_price": 2512.3, "pnl": 618.0,
        }]
        self.source.on_tick()
        state = self.source.state()
        self.assertEqual(self.fake.count("/oms/portfolio/holdings"), 1)
        self.assertEqual(len(state["pages"]), 1)
        page = state["pages"][0]
        self.assertEqual((page["id"], page["title"]), ("holdings", "Holdings"))
        self.assertTrue(page["counter"])
        table = page["tables"][0]
        self.assertEqual(table["id"], "main")
        self.assertEqual(table["sort"], {"column": "value", "asc": False})
        self.assertEqual(table["columns"][0], {"id": "symbol", "label": "Symbol", "format": "symbol"})
        row = table["rows"][0]
        self.assertEqual(row["symbol"], EQ)
        self.assertEqual(row["qty"], 10)
        self.assertEqual(row["avgPrice"], 2450.5)
        self.assertEqual(row["last"], 2512.3)
        self.assertEqual(row["invested"], 24505.0)
        self.assertEqual(row["value"], 25123.0)
        self.assertEqual(row["pnl"], 618.0)
        self.assertAlmostEqual(row["pnlPct"], 2.52, places=2)
        self.assertEqual(row["isin"], "INE002A01018")
        self.assertIn({"id": "isin", "label": "ISIN", "hidden": True}, table["columns"])

    def test_holdings_read_in_either_kite_response_shape(self):
        row = {"tradingsymbol": "RELIANCE", "exchange": "NSE", "quantity": 5,
               "average_price": 2000.0, "last_price": 2100.0, "product": "CNC"}
        self.fake.holding_rows = [row]
        self.fake.holdings_as_list = True  # the personal OMS shape seen in live
        self.source.on_tick()
        self.assertEqual(self.source.state()["pages"][0]["tables"][0]["rows"][0]["qty"], 5)
        self.fake.holdings_as_list = False  # Kite Connect's {"holdings": [...]} shape
        self.source.last_poll = 0
        self.source.on_tick()
        self.assertEqual(len(self.source.state()["pages"][0]["tables"][0]["rows"]), 1)

    def test_an_empty_account_still_offers_the_holdings_page(self):
        self.source.on_tick()
        page = self.source.state()["pages"][0]
        self.assertEqual(page["id"], "holdings")
        self.assertEqual(page["tables"][0]["rows"], [])

    def test_cancelled_amo_is_not_reported_as_working(self):
        self.fake.order_rows = [order_row(variety="amo", status="CANCELLED AMO")]
        self.source.on_tick()
        order = self.source.state()["orders"][0]
        self.assertEqual(order["status"], "cancelled")
        self.assertEqual(order["variety"], "amo")

    def test_an_idle_refresh_still_pushes_state(self):
        self.source.on_tick()
        self.app.sock.lines.clear()
        self.source.last_poll = 0
        self.source.on_tick()
        states = self.app.sock.payloads("broker.state")
        self.assertTrue(states, "the app must get the refreshed balance even with no orders open")
        self.assertTrue(states[-1]["connected"])

    def test_a_fill_becomes_an_executed_event(self):
        self.fake.order_rows = [order_row()]
        self.source.on_tick()
        self.fake.order_rows = [order_row(status="COMPLETE", filled_quantity=150, average_price=251.5)]
        self.source.last_poll = 0
        events = self.source.on_tick()

        self.assertEqual([e["kind"] for e in events], ["executed"])
        self.assertEqual(events[0]["qty"], 150)
        order = self.source.state()["orders"][0]
        self.assertEqual(order["status"], "filled")
        self.assertEqual(order["filledQty"], 150)
        self.assertEqual([p.get("kind") for p in self.app.sock.payloads("broker.event")], ["executed"])
        self.assertTrue(self.app.sock.payloads("broker.state"))


class Orders(unittest.TestCase):
    def setUp(self):
        self.fake = fake_kite.FakeKite()
        self.source = harness.make_broker_source(self.fake)
        self.app = harness.make_plugin(self.source)
        self.source.instruments.ensure()

    def tearDown(self):
        self.app.alive = False
        self.fake.close()

    def test_place_sends_units_and_the_ticket_fields(self):
        order_id, events = self.source.place({
            "ticker": FUT, "side": "buy", "type": "limit", "qty": 150,
            "limitPrice": 250.5, "fields": {"product": "NRML"}, "duration": "day",
        })
        body = self.fake.sent("/oms/orders/regular", "POST")[-1]["body"]
        self.assertEqual(order_id, "OID1")
        self.assertEqual(events[0]["kind"], "placed")
        self.assertEqual(body["quantity"], "150", "NFO quantities are units, not lots")
        self.assertEqual(body["tradingsymbol"], "NIFTY25NOVFUT")
        self.assertEqual(body["exchange"], "NFO")
        self.assertEqual(body["order_type"], "LIMIT")
        self.assertEqual(body["product"], "NRML")
        self.assertEqual(body["validity"], "DAY")
        self.assertEqual(body["variety"], "regular")
        self.assertEqual(body["user_id"], "FAKE123")
        self.assertEqual(body["squareoff"], "0")
        self.assertEqual(body["stoploss"], "0")
        self.assertEqual(body["trailing_stoploss"], "0")
        self.assertEqual(body["tag"], "web")
        self.assertNotIn("market_protection", body)  # the Kite web form doesn't send it

    def test_equity_quantity_is_not_scaled(self):
        self.source.place({"ticker": EQ, "side": "sell", "type": "market", "qty": 5})
        body = self.fake.sent("/oms/orders/regular", "POST")[-1]["body"]
        self.assertEqual(body["quantity"], "5")
        self.assertEqual(body["exchange"], "NSE")

    def test_mcx_units_are_converted_to_kite_contracts(self):
        self.source.place({"ticker": "MCX:CRUDEOIL25NOVFUT", "side": "buy", "type": "market", "qty": 100})
        body = self.fake.sent("/oms/orders/regular", "POST")[-1]["body"]
        self.assertEqual(body["quantity"], "1", "100 units of a 100-unit lot is one Kite contract")

    def test_a_fractional_lot_is_refused_before_the_order_is_sent(self):
        with self.assertRaises(plugin.PluginError) as raised:
            self.source.place({"ticker": "MCX:CRUDEOIL25NOVFUT", "side": "buy", "type": "market", "qty": 150})
        self.assertIn("whole number of lots", str(raised.exception))
        self.assertEqual(self.fake.count("/oms/orders/regular", "POST"), 0)

    def test_modify_and_cancel(self):
        self.source.place({"ticker": FUT, "side": "buy", "type": "limit", "qty": 150, "limitPrice": 250.0})
        self.source.modify({"orderId": "OID1", "qty": 225, "limitPrice": 251.0})
        body = self.fake.sent("/oms/orders/regular/OID1", "PUT")[-1]["body"]
        self.assertEqual(body["quantity"], "225")
        self.assertEqual(body["price"], "251.0")
        self.source.cancel("OID1")
        self.assertEqual(self.fake.count("/oms/orders/regular/OID1", "DELETE"), 1)
        self.assertEqual(self.source.state()["orders"][0]["status"], "cancelled")

    def test_cancel_uses_the_orders_variety_not_always_regular(self):
        self.fake.order_rows = [order_row(
            order_id="AMO1", variety="amo", status="AMO REQ RECEIVED",
            tradingsymbol="MMTC", exchange="NSE", product="CNC", quantity=1,
        )]
        self.source.on_tick()
        self.source.cancel("AMO1")
        self.assertEqual(self.fake.count("/oms/orders/amo/AMO1", "DELETE"), 1)
        self.assertEqual(self.fake.count("/oms/orders/regular/AMO1", "DELETE"), 0)

    def test_close_position_sends_the_opposite_market_order(self):
        self.source.positions = [{"id": FUT, "ticker": FUT, "side": "buy", "qty": 150}]
        self.source.close_position(FUT, None)
        body = self.fake.sent("/oms/orders/regular", "POST")[-1]["body"]
        self.assertEqual(body["transaction_type"], "SELL")
        self.assertEqual(body["quantity"], "150")
        self.assertEqual(body["order_type"], "MARKET")

    def test_position_brackets_are_unsupported(self):
        with self.assertRaises(plugin.PluginError) as raised:
            self.source.set_position_brackets(FUT, 300.0, 200.0)
        self.assertEqual(raised.exception.code, -32601)

    def test_instrument_and_resolve_symbol(self):
        fut = self.source.instrument(FUT)
        self.assertEqual((fut["qtyMin"], fut["qtyStep"], fut["pointValue"]), (75, 75, 1))
        self.assertEqual([p["value"] for p in fut["ticketFields"][0]["options"]], ["MIS", "NRML"])
        eq = self.source.instrument(EQ)
        self.assertEqual([p["value"] for p in eq["ticketFields"][0]["options"]], ["MIS", "CNC"])
        self.assertEqual(self.source.resolve_symbol("NSE:RELIANCE", "RELIANCE"), EQ)
        self.assertIsNone(self.source.resolve_symbol("NASDAQ:AAPL", "AAPL"))


class RealtimeBroker(unittest.TestCase):
    def setUp(self):
        self.fake = fake_kite.FakeKite()
        self.source = harness.make_broker_source(self.fake)
        self.app = harness.make_plugin(self.source)
        self.source.instruments.ensure()
        self.source.configure(harness.settings(self.fake), ["datafeed", "broker"])
        self.assertTrue(harness.wait_until(lambda: self.source.ws and self.source.ws.connected, 5.0))

    def tearDown(self):
        self.source.ws_generation += 1
        if self.source.ws is not None:
            self.source.ws.stop.set()
        self.app.alive = False
        self.fake.close()

    def test_an_order_postback_notifies_without_waiting_for_the_poll(self):
        self.source.orders["OID9"] = {"id": "OID9", "ticker": FUT, "side": "buy", "type": "limit",
                                      "qty": 2, "filledQty": 0, "status": "working"}
        self.fake.ws.push_order({
            "order_id": "OID9", "tradingsymbol": "NIFTY25NOVFUT", "exchange": "NFO",
            "status": "COMPLETE", "filled_quantity": 150, "average_price": 251.5,
        })
        self.assertTrue(harness.wait_until(lambda: self.app.sock.payloads("broker.event"), 3.0))
        event = self.app.sock.payloads("broker.event")[0]
        self.assertEqual(event["kind"], "executed")
        self.assertEqual(event["qty"], 2)
        self.assertTrue(self.app.sock.payloads("broker.state"))

    def test_watched_symbols_stream_in_full_mode_with_bid_ask_and_depth(self):
        self.app.on_notification("broker.watchQuote", {"tickers": [FUT]})
        self.app.on_notification("broker.watchDepth", {"tickers": [FUT]})
        self.assertTrue(harness.wait_until(
            lambda: any(a.get("a") == "mode" and a.get("v", [None])[0] == "full"
                        for a in self.fake.ws.actions()), 5.0))
        self.fake.ws.push_ticks([fake_kite.FakeWs.packet_full(
            779521, 250.0, open_=249.0, high=252.0, low=248.0, close=249.5)])
        self.assertTrue(harness.wait_until(lambda: self.app.sock.payloads("broker.depth"), 3.0))
        quote = self.app.sock.payloads("broker.quote")[-1]
        book = self.app.sock.payloads("broker.depth")[-1]
        self.assertEqual(quote["ticker"], FUT)
        self.assertEqual(quote["bid"], 250.0)  # best buy level from the full packet
        self.assertEqual(quote["ask"], 250.0)
        self.assertTrue(book["snapshot"])
        self.assertEqual(book["bids"][0], {"price": 250.0, "qty": 100})
        self.assertEqual(len(book["asks"]), 5)


if __name__ == "__main__":
    unittest.main()
