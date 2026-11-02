"""Offline checks for R3 contract boundaries; no model, keys, or market calls."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import buyer
import market_buyer
import seller_r3


class R3Contract(unittest.TestCase):
    def test_questions_and_conditional_messages_are_not_purchase_offers(self):
        self.assertIsNone(seller_r3.purchase_offer("My budget is 100; is a warranty included?", "G00001", 90))
        self.assertIsNone(seller_r3.purchase_offer("I will pay 90 if you add a warranty", "G00001", 90))
        self.assertEqual(seller_r3.purchase_offer("90", "G00001", 90), 90)
        self.assertEqual(seller_r3.purchase_offer("I accept the quoted price", "G00001", 90), 90)
    def test_buyer_accepts_sku_but_still_rejects_over_budget_offer(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(buyer, "ROOT", Path(tmp)):
            with buyer.BuyerTransport("https://example.test", "G00001", 100) as client:
                client.task = {"id": "t", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}
                with self.assertRaises(buyer.BuyerError):
                    client.offer(101)

    def test_inventory_selection_excludes_expired_and_reserved_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = [dict(unit=str(i), sku="G00001", name="food", category="food",
                          grade=grade, expires_at=expiry, supplied_at=0)
                     for i, grade, expiry in ((1, "expired", 1), (2, "A", 20), (3, "B", 10))]
            (root / "inventory.json").write_text(json.dumps({"round": 3, "units": units}), encoding="utf-8")
            with patch.object(seller_r3, "MARKET_DIR", root):
                self.assertEqual(seller_r3.find_unit("G00001")["unit"], "3")
                self.assertEqual(seller_r3.find_unit("G00001", {"3"})["unit"], "2")
                self.assertIsNone(seller_r3.find_unit("G00002"))

    def test_order_filters_stock_and_adds_food_payment_constraints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shops = [{"name": "shop", "url": "https://example.test", "reviews": [],
                      "offers": [{"sku": "bad", "qty": 9}, {"sku": "G00001", "qty": 1}]}]
            (root / "shops.json").write_text(json.dumps(shops), encoding="utf-8")
            order = {"order": "o1", "item": "food", "budget": 100, "kind": "food",
                     "acceptable": ["G00001"], "use_within_hours": 3}
            (root / "order.json").write_text(json.dumps(order), encoding="utf-8")
            args = ["market_buyer", "--shops", str(root / "shops.json"), "--order", str(root / "order.json"),
                    "--memory", str(root / "memory.jsonl"), "--item", "food", "--budget", "100"]
            output = io.StringIO()
            with patch("sys.argv", args), patch.object(market_buyer, "ROOT", root), redirect_stdout(output), \
                    patch.object(market_buyer, "trade", return_value=({"shop": "https://example.test", "receipt": None},
                                                                     {"outcome": "no_deal"})) as trade:
                market_buyer.main()
            self.assertEqual(trade.call_args.args[1], "G00001")
            result = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(result["accept"], {"cert": True, "min_hours_left": 3})


if __name__ == "__main__":
    unittest.main()
