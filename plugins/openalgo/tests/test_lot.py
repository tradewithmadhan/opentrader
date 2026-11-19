"""Lots: the master's lot size is the unit the app and the broker meet at.

The app works in contracts; the broker in units. Every crossing goes through the master's
`lotsize` (× on the order, ÷ on the books), and an equity's lot of 1 is the identity.
"""

import unittest

import fake_openalgo
import harness
import plugin

FUT = "NFO:NIFTY25NOVFUT"
EQ = "NSE:RELIANCE"


class Lots(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)
        self.app = harness.make_plugin(self.source)
        self.source.instruments.rows_for("NFO")
        self.source.instruments.rows_for("NSE")

    def tearDown(self):
        self.app.alive = False
        self.fake.close()

    def test_lot_of_a_derivative_and_an_equity(self):
        self.assertEqual(self.source._lot_of(FUT), 75)
        self.assertEqual(self.source._lot_of(EQ), 1)

    def test_contracts_and_units_are_inverses(self):
        self.assertEqual(self.source._units(FUT, 2), 150)
        self.assertEqual(self.source._contracts(FUT, 150), 2)
        self.assertEqual(self.source._units(EQ, 7), 7)
        self.assertEqual(self.source._contracts(EQ, 7), 7)
        self.assertEqual(self.source._contracts(FUT, 100), 100 / 75)  # never a wrong 1-lot answer

    def test_instrument_says_what_the_ticket_takes(self):
        fut = self.source.instrument(FUT)
        self.assertEqual((fut["qtyMin"], fut["qtyStep"]), (1, 1))
        products = fut["ticketFields"][0]["options"]
        self.assertEqual([p["value"] for p in products], ["MIS", "NRML"],
                         "a derivative is intraday or carry-forward")

        eq = self.source.instrument(EQ)
        self.assertEqual([p["value"] for p in eq["ticketFields"][0]["options"]], ["MIS", "CNC"])

    def test_place_sends_units_not_contracts(self):
        order_id, events = self.source.place({
            "ticker": FUT, "side": "buy", "type": "limit", "qty": 2,
            "limitPrice": 250.5, "fields": {"product": "NRML"},
        })
        self.assertTrue(order_id)
        self.assertEqual(events[0]["kind"], "placed")
        body = self.fake.orders_placed[-1]
        self.assertEqual(body["quantity"], 150, "2 contracts of a 75-lot are 150 units")
        self.assertEqual(body["action"], "BUY")
        self.assertEqual(body["pricetype"], "LIMIT")
        self.assertEqual(body["validity"], "DAY")
        self.assertEqual(body["product"], "NRML")
        self.assertEqual(body["symbol"], "NIFTY25NOVFUT")
        self.assertEqual(body["exchange"], "NFO")

    def test_a_fractional_contract_is_refused(self):
        with self.assertRaises(plugin.PluginError) as raised:
            self.source.place({"ticker": FUT, "side": "buy", "type": "market", "qty": 1.5})
        self.assertIn("one lot", str(raised.exception))
        self.assertEqual(self.fake.count("/api/v1/placeorder"), 0)

    def test_the_books_come_back_in_contracts(self):
        self.fake.orderbook = [{
            "orderid": "O9", "symbol": "NIFTY25NOVFUT", "exchange": "NFO",
            "action": "SELL", "pricetype": "market", "quantity": 150, "filled_quantity": 75,
            "order_status": "complete", "average_price": 250.0,
        }]
        self.source.force_poll()
        self.source.on_tick()
        order = self.source.orders[0]
        self.assertEqual(order["qty"], 2)
        self.assertEqual(order["filledQty"], 1)
        self.assertEqual(order["side"], "sell")
        self.assertEqual(order["status"], "filled")

    def test_modify_sends_units_too(self):
        self.source.place({"ticker": FUT, "side": "buy", "type": "limit", "qty": 2,
                           "limitPrice": 250.0})
        order_id = self.source.orders[0]["id"]
        self.source.modify({"orderId": order_id, "qty": 3, "limitPrice": 251.0})
        body = self.fake.sent("/api/v1/modifyorder", "POST")[-1]["body"]
        self.assertEqual(body["quantity"], 225)
        self.assertEqual(body["price"], 251.0)


if __name__ == "__main__":
    unittest.main()
