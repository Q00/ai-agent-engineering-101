"""outer.health: the seller's negotiations sorted into outcomes, from the seller's events.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.events import EventWriter  # noqa: E402
from outer import health  # noqa: E402
from tests.test_calc_check import Base  # noqa: E402


class HealthTest(Base):
    def test_seller_outcomes(self):
        w = EventWriter("t", source="seller")
        w.emit("seller.request", {"excerpt": "Quote only, no purchase authorization: X", "price": None}, task="q")
        w.emit("seller.closed", {"outcome": "canceled"}, task="q")
        w.emit("seller.request", {"excerpt": "I want to buy X", "price": None}, task="l")
        w.emit("seller.closed", {"outcome": "canceled"}, task="l")
        w.emit("seller.request", {"excerpt": "I want to buy Y", "price": None}, task="m")
        w.emit("seller.missed", {"skus": ["G1"]}, task="m")
        w.emit("seller.request", {"excerpt": "I want to buy Z", "price": None}, task="c")
        w.emit("seller.closed", {"outcome": "completed", "price": 900, "cost": 600}, task="c")
        w.close()
        s = health.seller(0)
        self.assertEqual(s["outcomes"], {"quote_only": 1, "left_at_opening": 1, "no_stock": 1, "completed": 1})
        self.assertEqual((s["revenue"], s["cost_of_sales"], s["missing_skus"]), (900, 600, [("G1", 1)]))
        self.assertEqual(s["left_at_opening_share"], 0.33)


if __name__ == "__main__":
    unittest.main()
