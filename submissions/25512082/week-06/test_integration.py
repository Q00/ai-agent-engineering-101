"""Model-free Buyer/Seller integration tests over localhost A2A HTTP."""
import socket
import threading
import time
import unittest
from unittest.mock import patch

import httpx
import uvicorn

import buyer
import seller


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class RunningSeller:
    def __init__(self):
        self.port = free_port()
        self.executor = seller.Seller()
        config = uvicorn.Config(seller.build_app(self.port, self.executor), host="127.0.0.1",
                                port=self.port, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 5
        url = f"http://127.0.0.1:{self.port}/.well-known/agent-card.json"
        while time.monotonic() < deadline:
            try:
                if httpx.get(url, timeout=0.2).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.02)
        raise RuntimeError("mock seller did not start")

    def __exit__(self, exc_type, exc, traceback):
        self.server.should_exit = True
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("mock seller did not stop")

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


class IntegrationTests(unittest.TestCase):
    def trade(self, item, budget, seller_decisions, buyer_decisions):
        with patch("seller.decide_seller", side_effect=seller_decisions) as seller_model, \
             patch("buyer.decide_buyer", side_effect=buyer_decisions) as buyer_model, \
             RunningSeller() as running, buyer.BuyerClient(running.url) as client:
            outcome = client.buy(item, budget)
            states = [deal.state for deal in running.executor.negotiations.values()]
        return outcome, states, seller_model.call_count, buyer_model.call_count

    def test_lamp_purchase(self):
        outcome, states, seller_calls, buyer_calls = self.trade(
            "lamp", 120,
            [{"action": "ask", "price": 110, "reason": "open"},
             {"action": "accept", "price": 110, "reason": "deal"}],
            [{"action": "accept", "price": 110, "reason": "within budget"}],
        )
        self.assertEqual((outcome.outcome, outcome.price), ("deal", 110))
        self.assertEqual(states, ["completed"])
        self.assertEqual((seller_calls, buyer_calls), (2, 1))

    def test_chair_counteroffer_then_purchase(self):
        outcome, states, seller_calls, buyer_calls = self.trade(
            "chair", 200,
            [{"action": "ask", "price": 240, "reason": "open"},
             {"action": "counter", "price": 200, "reason": "counter"},
             {"action": "accept", "price": 200, "reason": "deal"}],
            [{"action": "offer", "price": 180, "reason": "start"},
             {"action": "accept", "price": 200, "reason": "within budget"}],
        )
        self.assertEqual((outcome.outcome, outcome.price), ("deal", 200))
        self.assertEqual(states, ["completed"])
        self.assertEqual((seller_calls, buyer_calls), (3, 2))

    def test_bookshelf_budget_shortfall_walks_away(self):
        outcome, states, seller_calls, buyer_calls = self.trade(
            "bookshelf", 230,
            [{"action": "ask", "price": 338, "reason": "open"}],
            [{"action": "walk_away", "price": None, "reason": "over budget"}],
        )
        self.assertEqual(outcome.outcome, "walked_away")
        self.assertEqual(states, ["canceled"])
        self.assertEqual((seller_calls, buyer_calls), (1, 1))


if __name__ == "__main__":
    unittest.main()
