"""The account side: the books mapped to the app's shape, transitions becoming events, and a
ticket's watched symbols answered per tick.
"""

import json
import unittest

import fake_openalgo
import harness

FUT = "NFO:NIFTY25NOVFUT"
EQ = "NSE:RELIANCE"


def order_row(**extra):
    row = {
        "orderid": "O1", "symbol": "NIFTY25NOVFUT", "exchange": "NFO",
        "action": "BUY", "pricetype": "LIMIT", "quantity": 150, "filled_quantity": 0,
        "price": 250.0, "order_status": "open", "average_price": 0,
        "updatetime": 1760000000000,
    }
    row.update(extra)
    return row


class Books(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)
        self.app = harness.make_plugin(self.source)
        self.source.instruments.rows_for("NSE")
        self.source.instruments.rows_for("NFO")

    def tearDown(self):
        self.app.alive = False
        self.fake.close()

    def test_state_before_any_read_says_not_connected(self):
        state = self.source.state()
        self.assertFalse(state["connected"])
        self.assertEqual(state["positions"], [])
        self.assertEqual(state["account"]["balance"], 0.0)
        self.assertEqual(state["capabilities"]["orderTypes"],
                         ["market", "limit", "stop", "stopLimit"])

    def test_the_first_tick_reads_the_books(self):
        self.fake.orderbook = [order_row()]
        self.fake.positionbook = [{
            "symbol": "NIFTY25NOVFUT", "exchange": "NFO", "netqty": 150,
            "average_price": 250.0, "pnl": 0,
        }]
        events = self.source.on_tick()

        self.assertEqual(events, [], "an order already working is not a transition")
        state = self.source.state()
        self.assertTrue(state["connected"])
        self.assertEqual(state["account"]["balance"], 250000.0)
        self.assertEqual(state["account"]["equity"], 251500.0)
        self.assertEqual(state["positions"][0]["qty"], 2, "150 units of a 75-lot are 2 contracts")
        self.assertEqual(state["orders"][0]["qty"], 2)
        self.assertEqual(state["orders"][0]["status"], "working")
        self.assertEqual(self.fake.count("/api/v1/funds"), 1)

    def test_a_fill_on_the_books_updates_the_order(self):
        self.fake.orderbook = [order_row()]
        self.source.force_poll()
        self.source.on_tick()

        self.fake.orderbook = [order_row(order_status="complete", filled_quantity=150,
                                         average_price=251.5)]
        self.source.force_poll()
        self.source.on_tick()

        order = self.source.state()["orders"][0]
        self.assertEqual(order["status"], "filled")
        self.assertEqual(order["filledQty"], 2)
        self.assertEqual(order["avgFillPrice"], 251.5)

    def test_a_cancel_on_the_books_becomes_an_event(self):
        self.fake.orderbook = [order_row()]
        self.source.force_poll()
        self.source.on_tick()

        self.fake.orderbook = [order_row(order_status="cancelled")]
        self.source.force_poll()
        events = self.source.on_tick()

        self.assertEqual([e["kind"] for e in events], ["cancelled"])
        self.assertEqual(events[0]["qty"], 2)

    def test_a_pushed_order_update_moves_the_order_and_the_state(self):
        self.source.place({"ticker": FUT, "side": "buy", "type": "limit", "qty": 2,
                           "limitPrice": 250.0})  # a fresh order: "placing"
        order_id = self.source.orders[0]["id"]
        self.fake.orderbook[0].update(order_status="complete", filled_quantity=150,
                                      average_price=250.0)
        self.app.sock.lines.clear()

        self.source._ws_message(json.dumps({
            "type": "order_update", "orderid": order_id, "order_status": "complete",
            "filled_quantity": 150, "average_price": 250.0,
        }).encode())

        self.assertEqual(self.source.orders[0]["status"], "filled")
        events = self.app.sock.payloads("broker.event")
        self.assertEqual([e["kind"] for e in events], ["executed"])
        self.assertEqual(events[0]["qty"], 2)
        self.assertGreaterEqual(len(self.app.sock.payloads("broker.state")), 1,
                                "the ticket follows a fill without waiting for the next poll")

    def test_the_open_pnl_follows_the_price(self):
        self.fake.positionbook = [{
            "symbol": "NIFTY25NOVFUT", "exchange": "NFO", "netqty": 150,
            "average_price": 250.0,
        }]
        self.source.force_poll()
        self.source.on_tick()
        self.source.quotes[FUT] = {"price": 260.0, "prevClose": 250.0}

        position = self.source.state()["positions"][0]
        self.assertEqual(position["last"], 260.0)
        self.assertEqual(position["unrealizedPnl"], 1500.0, "(260 - 250) x 150 units")

    def test_a_short_position_flips_the_pnl_sign(self):
        self.fake.positionbook = [{
            "symbol": "NIFTY25NOVFUT", "exchange": "NFO", "netqty": -150,
            "average_price": 250.0,
        }]
        self.source.force_poll()
        self.source.on_tick()
        self.source.quotes[FUT] = {"price": 260.0}

        position = self.source.state()["positions"][0]
        self.assertEqual(position["side"], "sell")
        self.assertEqual(position["qty"], 2)
        self.assertEqual(position["unrealizedPnl"], -1500.0)


class TicketQuotes(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)
        self.app = harness.make_plugin(self.source)
        self.source.instruments.rows_for("NFO")

    def tearDown(self):
        self.app.alive = False
        self.fake.close()

    def test_a_watched_ticker_is_quoted_with_its_tick_spread(self):
        self.app.on_notification("broker.watchQuote", {"tickers": [FUT]})
        self.source._ws_message(json.dumps({
            "type": "market_data", "symbol": "NIFTY25NOVFUT", "exchange": "NFO",
            "data": {"ltp": 250.0, "volume": 1200},
        }).encode())

        quotes = self.app.sock.payloads("broker.quote")
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0]["ticker"], FUT)
        self.assertEqual(quotes[0]["bid"], 249.975)
        self.assertEqual(quotes[0]["ask"], 250.025)
        self.assertEqual(quotes[0]["last"], 250.0)
        self.assertEqual(self.source.subscribed, {FUT})

    def test_a_watched_ticker_uses_the_stream_depth(self):
        self.app.on_notification("broker.watchQuote", {"tickers": [FUT]})
        self.source._ws_message(json.dumps({
            "type": "market_data", "symbol": "NIFTY25NOVFUT", "exchange": "NFO",
            "data": {"ltp": 250.0, "volume": 1200,
                     "depth": {"buy": [{"price": 249.9, "quantity": 3, "orders": 1},
                                       {"price": 249.8, "quantity": 2, "orders": 1}],
                               "sell": [{"price": 250.1, "quantity": 2, "orders": 1},
                                        {"price": 250.2, "quantity": 1, "orders": 1}]}},
        }).encode())

        quotes = self.app.sock.payloads("broker.quote")
        self.assertEqual(quotes[0]["bid"], 249.9, "the best buy level, not the tick-size spread")
        self.assertEqual(quotes[0]["ask"], 250.1, "the best sell level, not the tick-size spread")
        self.assertEqual(quotes[0]["last"], 250.0)

    def test_the_rest_fallback_keeps_the_brokers_bid_and_ask(self):
        self.app.on_notification("broker.watchQuote", {"tickers": [FUT]})
        self.fake.quotes[FUT] = {"ltp": 250.0, "bid": 249.9, "ask": 250.1}
        self.source.poll_quotes_rest()
        self.app.push_quote(FUT)

        quotes = self.app.sock.payloads("broker.quote")
        self.assertEqual(quotes[-1]["bid"], 249.9, "the REST answer's own bid, not the tick-size spread")
        self.assertEqual(quotes[-1]["ask"], 250.1, "the REST answer's own ask, not the tick-size spread")

    def test_a_subscription_drops_stale_symbols(self):
        self.app.on_notification("data.subscribe", {
            "tickers": [EQ, "MCX:GOLD1!", "NSE:NIFTY"],
        })
        self.assertEqual(self.app.tickers, {EQ},
                         "an unserved exchange and a stale index name are dropped")
        self.assertEqual(self.source.subscribed, {EQ})

    def test_data_and_ticket_subscriptions_are_forwarded_together(self):
        self.app.on_notification("data.subscribe", {"tickers": [EQ]})
        self.app.on_notification("broker.watchQuote", {"tickers": [FUT]})
        self.assertEqual(self.source.subscribed, {EQ, FUT})


if __name__ == "__main__":
    unittest.main()
