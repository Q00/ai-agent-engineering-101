from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import market_buyer
import seller_r3
from r3_selection import select


class RoundIsolation(unittest.TestCase):
    def test_r2_does_not_call_buyer_or_load_old_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"shops.json").write_text("[]", encoding="utf8")
            (root/"order.json").write_text(json.dumps({"round": 2}), encoding="utf8")
            args = ["buyer", "--shops", str(root/"shops.json"), "--order", str(root/"order.json"),
                    "--item", "lamp", "--budget", "100"]
            with patch("sys.argv", args), patch.object(market_buyer, "load_memory") as memory, \
                    patch.object(market_buyer, "trade") as trade, redirect_stdout(io.StringIO()) as output:
                market_buyer.main()
            trade.assert_not_called(); memory.assert_not_called()
            self.assertIn("R3-only", output.getvalue())

    def test_default_r3_memory_never_opens_legacy_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); md = root/".market"; md.mkdir()
            for name, data in {"shops.json": [], "catalog.json": {}, "order.json": {
                    "round": 3, "item": "goods", "kind": "goods", "budget": 100, "acceptable": []}}.items():
                (md/name).write_text(json.dumps(data), encoding="utf8")
            (md/"buyer-memory.jsonl").write_text("corrupt legacy data", encoding="utf8")
            args = ["buyer", "--shops", str(md/"shops.json"), "--order", str(md/"order.json"),
                    "--item", "goods", "--budget", "100"]
            with patch.object(market_buyer, "ROOT", root), patch("sys.argv", args), \
                    patch.object(market_buyer, "load_memory", return_value=[]) as load, redirect_stdout(io.StringIO()):
                market_buyer.main()
            load.assert_called_once_with(md/"r3-buyer-memory.jsonl")

    def test_r2_inventory_cannot_sell_furniture(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(seller_r3, "MARKET_DIR", Path(tmp)):
            (Path(tmp)/"inventory.json").write_text('{"round":2,"units":[]}', encoding="utf8")
            self.assertIsNone(seller_r3.find_unit("lamp"))

    def test_no_forced_fifth_trade_rotation_or_r2_public_review_ranking(self):
        shops = [{"name": n, "url": n, "reviews": [{"round": 2, "stars": 1, "review": "bad"}],
                  "offers": [{"sku": "A", "qty": 1}]} for n in ("cheap", "other")]
        records = [{"shop_name": "cheap", "item": "A", "price": 50, "outcome": "purchased",
                    "verification": "market_settled"}] * 4
        self.assertEqual(select(shops, records, {"A": {"list_price": 100}}, 100)[0]["name"], "cheap")

    def test_public_price_reference_only_uses_recent_r3_settlement(self):
        shop = {"name": "s", "url": "u", "reviews": [], "offers": [{"sku": "A", "qty": 1}]}
        row = {"round": 2, "kind": "settle", "ts": 100, "data": {"shop": "u", "item": "A", "price": 1}}
        evidence = select([shop], [], {"A": {"list_price": 100}}, 100, public_rows=[row], now=200)[4]
        self.assertIsNone(evidence[0]["past_settled_price"])
        row["round"] = 3
        evidence = select([shop], [], {"A": {"list_price": 100}}, 100, public_rows=[row], now=200)[4]
        self.assertEqual(evidence[0]["reference_source"], "public_r3_settlement")


if __name__ == "__main__":
    unittest.main()
