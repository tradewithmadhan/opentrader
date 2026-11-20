"""Realtime: a tick on the socket reaches the app at once.

0.1.17 pushed quotes only from the stream loop, which sleeps a second between passes — the
chart repainted once a second however many ticks the socket carried. A tick now notifies the
moment it lands (fix B), and the loop keeps its REST fallback.
"""

import json
import threading
import time
import unittest

import fake_openalgo
import harness

EQ = "NSE:RELIANCE"


def market_data(price, volume=1000):
    return json.dumps({
        "type": "market_data", "symbol": "RELIANCE", "exchange": "NSE",
        "data": {"ltp": price, "open": price, "high": price, "low": price,
                 "volume": volume, "close": price},
    }).encode()


class TicksReachTheAppAtOnce(unittest.TestCase):
    def setUp(self):
        self.fake = fake_openalgo.FakeOpenAlgo()
        self.source = harness.make_source(self.fake)
        self.app = harness.make_plugin(self.source)
        self.app.tickers = {EQ}
        self.app.forward_subscription()

    def tearDown(self):
        # Let the stream loop leave on its own (a new generation), so closing the fake is not
        # read as a dropped connection and logged as one.
        self.source.ws_generation += 1
        harness.wait_until(lambda: not self.source.ws_thread_alive(), timeout=3.0)
        self.app.alive = False
        self.fake.close()

    def test_every_tick_notifies(self):
        started = time.time()
        for i in range(5):
            self.source._ws_message(market_data(2500.0 + i))
        elapsed = time.time() - started

        quotes = self.app.sock.payloads("data.quote")
        self.assertEqual([q["price"] for q in quotes], [2500.0, 2501.0, 2502.0, 2503.0, 2504.0],
                         "one notification per tick, not one per second")
        self.assertLess(elapsed, 1.0, "the push does not wait for the stream loop's sleep(1)")

    def test_an_unsubscribed_ticker_is_not_pushed(self):
        self.app.tickers = set()
        self.app.forward_subscription()
        self.source._ws_message(market_data(2500.0))
        self.assertEqual(self.app.sock.payloads("data.quote"), [])
        self.assertEqual(self.app.sock.payloads("broker.quote"), [])

    def test_the_quote_carries_the_forming_bars(self):
        self.source._ws_message(market_data(2500.0, volume=5000))
        quote = self.app.sock.payloads("data.quote")[0]
        self.assertEqual(quote["ticker"], EQ)
        self.assertEqual(quote["minute"][4], 2500.0)
        self.assertEqual(quote["day"][4], 2500.0)
        self.assertEqual(quote["day"][5], 5000.0)

    def test_a_live_stream_push_needs_no_loop(self):
        self.source.configure(harness.settings(self.fake))
        self.assertTrue(self.fake.ws.connected.wait(5.0), "the fake stream accepted the connection")
        self.assertTrue(harness.wait_until(
            lambda: any(a.get("action") == "subscribe" for a in self.fake.ws.actions()), 5.0),
            "the plugin subscribed the chart's symbol")

        started = time.time()
        for i in range(10):
            self.fake.ws.push_tick("NSE", "RELIANCE", 2600.0 + i)
        arrived = harness.wait_until(
            lambda: len(self.app.sock.payloads("data.quote")) >= 10, timeout=2.0)
        elapsed = time.time() - started

        self.assertTrue(arrived, "10 ticks on the socket = 10 pushes, without the 1 s loop")
        self.assertLess(elapsed, 1.0, "the socket's tick rate is the chart's rate")

    def test_the_stream_subscribes_five_depth_levels(self):
        self.source.configure(harness.settings(self.fake))
        self.assertTrue(self.fake.ws.connected.wait(5.0), "the fake stream accepted the connection")
        self.assertTrue(harness.wait_until(
            lambda: any(a.get("action") == "subscribe" for a in self.fake.ws.actions()), 5.0),
            "the plugin subscribed the chart's symbol")
        subscribe = [a for a in self.fake.ws.actions() if a.get("action") == "subscribe"][-1]
        self.assertEqual(subscribe.get("mode"), 3, "depth mode, so the ladder gets a real bid / ask")
        self.assertEqual(subscribe.get("depth"), 5)

    def test_the_rest_fallback_still_pushes(self):
        self.fake.quotes[EQ] = {"ltp": 2499.0, "prev_close": 2490.0}
        self.source.ws = None  # no socket: the loop's REST poll is the source
        self.source.last_ws_tick = 0.0
        thread = threading.Thread(target=self.app.stream, daemon=True)
        thread.start()
        try:
            pushed = harness.wait_until(
                lambda: any(q["price"] == 2499.0 for q in self.app.sock.payloads("data.quote")),
                timeout=4.0)
            self.assertTrue(pushed, "the fallback poll still reaches the app")
        finally:
            self.app.alive = False
            thread.join(timeout=3.0)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
