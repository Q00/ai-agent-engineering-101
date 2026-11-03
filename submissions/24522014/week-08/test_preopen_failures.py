"""Pre-opening failure checks with temporary files and mocked model/market calls."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import market_buyer
import monitor_shop
import seller_r3
from settlement_memory import payment_outcomes


class FilesAndOrders(unittest.TestCase):
    def test_missing_or_corrupt_inventory_does_not_invent_r2_stock(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(seller_r3, "MARKET_DIR", Path(tmp)):
            self.assertIsNone(seller_r3.find_unit("lamp"))
            (Path(tmp) / "inventory.json").write_text("{broken", encoding="utf8")
            self.assertIsNone(seller_r3.find_unit("lamp"))

    def test_payment_partial_write_vs_completed_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payments.jsonl"
            path.write_text('{"order":"o1"}\n{partial', encoding="utf8")
            self.assertIn("o1", payment_outcomes(path))
            path.write_text('{broken}\n', encoding="utf8")
            with self.assertRaises(json.JSONDecodeError):
                payment_outcomes(path)

    def test_empty_r3_acceptable_is_no_stock_and_not_legacy_trade(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "shops.json").write_text(json.dumps([{"name": "shop", "url": "https://example.test", "reviews": []}]), encoding="utf8")
            order = {"round": 3, "item": "goods", "budget": 100, "acceptable": [], "kind": "goods"}
            (root / "order.json").write_text(json.dumps(order), encoding="utf8")
            args = ["buyer", "--shops", str(root / "shops.json"), "--order", str(root / "order.json"),
                    "--memory", str(root / "memory.jsonl"), "--item", "goods", "--budget", "100"]
            with patch("sys.argv", args), patch.object(market_buyer, "ROOT", root), \
                    patch.object(market_buyer, "trade") as trade, redirect_stdout(io.StringIO()) as output:
                market_buyer.main()
            trade.assert_not_called()
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["shop"], "")

    def test_invalid_food_order_is_rejected_before_trade(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "shops.json").write_text("[]", encoding="utf8")
            (root / "order.json").write_text(json.dumps({"round": 3, "item": "food", "kind": "food", "budget": 100}), encoding="utf8")
            args = ["buyer", "--shops", str(root / "shops.json"), "--order", str(root / "order.json"),
                    "--memory", str(root / "memory.jsonl"), "--item", "food", "--budget", "100"]
            with patch("sys.argv", args), patch.object(market_buyer, "trade") as trade:
                with self.assertRaises(SystemExit):
                    market_buyer.main()
            trade.assert_not_called()

    def test_comparison_failure_is_not_logged_as_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "old.json").write_text("{broken", encoding="utf8")
            registry = {"round": 3, "shops": [{"name": "kcwarni", "reviews": []}]}
            board = {"shops": [{"name": "kcwarni", "alive": True}]}
            responses = [MagicMock(), MagicMock()]
            responses[0].json.return_value = registry
            responses[1].json.return_value = board
            client = MagicMock()
            client.__enter__.return_value.get.side_effect = responses
            with patch.object(monitor_shop, "LOGS", root), patch.object(monitor_shop.httpx, "Client", return_value=client), \
                    patch.object(monitor_shop, "prepare", return_value=None), redirect_stdout(io.StringIO()) as output:
                code = monitor_shop.main()
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "error")


class SellerDelay(unittest.IsolatedAsyncioTestCase):
    async def check_change(self, change, expected):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = {"unit": "u1", "sku": "G1", "name": "fixture", "kind": "goods", "grade": "A",
                    "cost": 100, "list_price": 120, "supplied_at": time.time(), "expires_at": None}
            path = root / "inventory.json"
            path.write_text(json.dumps({"round": 3, "units": [unit]}), encoding="utf8")
            (root / "catalog.json").write_text("{}", encoding="utf8")
            seller = seller_r3.Seller()
            seller.open["t1"] = {"unit": unit, "ask": 120, "offer": None, "lines": []}
            updater = MagicMock()
            updater.reject = AsyncMock()
            updater.requires_input = AsyncMock()
            def model(_):
                path.write_text(json.dumps({"round": 3, "units": [{**unit, **change}]}), encoding="utf8")
                return {"action": "accept"}, "fixture"
            context = SimpleNamespace(current_task=SimpleNamespace(id="t1", context_id="c1"), message=None)
            with patch.object(seller_r3, "MARKET_DIR", root), patch.object(seller_r3, "__file__", str(root / "seller_r3.py")), \
                    patch.object(seller_r3, "get_message_text", return_value="120"), \
                    patch.object(seller_r3, "TaskUpdater", return_value=updater), \
                    patch.object(seller_r3, "ask_json", side_effect=model), patch.object(seller_r3, "sign") as sign:
                await seller.execute(context, MagicMock())
            sign.assert_not_called()
            getattr(updater, expected).assert_awaited_once()

    async def test_expiry_during_model_call_never_signs(self):
        await self.check_change({"expires_at": 1}, "reject")

    async def test_grade_change_requires_new_disclosure_and_confirmation(self):
        await self.check_change({"grade": "B"}, "requires_input")


if __name__ == "__main__":
    unittest.main()
