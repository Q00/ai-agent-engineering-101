import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from local_support import ROOT
from strategy.runtime import buyer_terms, seller_terms, inventory_analysis, policy, read_evidence
from strategy.round3_math import buying_terms, selling_terms


class StrategyTests(unittest.TestCase):
    def test_extracted_function_bodies_match_recorded_round3_source(self):
        manifest = json.loads((ROOT / "strategy/SOURCE.json").read_text())
        port = ast.parse((ROOT / "strategy/round3_math.py").read_text())
        copied = {n.name: ast.dump(n) for n in port.body if isinstance(n, ast.FunctionDef)}
        for file, names in manifest["functions"].items():
            raw = (ROOT.parent / "week-08" / file).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest["files"][file])
            for n in ast.parse(raw).body:
                if isinstance(n, ast.FunctionDef) and n.name in names:
                    self.assertEqual(copied[n.name], ast.dump(n))

    def test_same_policy_before_fees_and_exact_fee_boundaries(self):
        cfg = policy()
        product = {"kind": "goods", "grade": "A", "cost": 101, "list_price": 2000}
        zero = {"seller_bps": 0, "seller_fixed": 0, "buyer_bps": 0, "buyer_fixed": 0}
        self.assertEqual(seller_terms(product, zero)["price_floor"], selling_terms(product, cfg["shop"])["price_floor"])
        self.assertEqual(buyer_terms(1000, product, {"kind": "goods"}, zero)["price_limit"],
                         buying_terms(1000, product=product, order={"kind": "goods", "round": 4}, config=cfg["buyer"])["price_limit"])
        for bps in (1, 500, 9999):
            fees = dict(zero, seller_bps=bps, buyer_bps=bps, seller_fixed=7, buyer_fixed=13)
            floor = seller_terms(product, fees)["price_floor"]
            net = lambda p: p - (p * bps + 9999) // 10000 - 7
            self.assertGreaterEqual(net(floor), 112)
            self.assertLess(net(floor - 1), 112)
            cap = buyer_terms(1000, product, {"kind": "goods"}, fees)["price_limit"]
            total = lambda p: p + (p * bps + 9999) // 10000 + 13
            self.assertLessEqual(total(cap), 900)
            self.assertGreater(total(cap + 1), 900)

    def test_unknown_fees_and_insufficient_funds_fail(self):
        with self.assertRaises(KeyError):
            buyer_terms(1000, {"list_price": 2000}, {"kind": "goods"}, {})
        with self.assertRaises(ValueError):
            buyer_terms(10, {"list_price": 100}, {"kind": "goods"}, {"buyer_bps": 500, "buyer_fixed": 100})

    def test_analysis_deduplicates_sales_and_does_not_guess_missing_state(self):
        cfg = policy()["shop"]
        sale = {"receipt_id": "r1", "sku": "milk", "status": "settled", "at": 9900}
        data = {"cash": 1000, "units": [{"sku": "milk", "kind": "food", "available": True}],
                "sales": [sale, sale, dict(sale, receipt_id="r2", status="pending")], "next_supply_at": 10100}
        result = inventory_analysis(data, cfg, 10000)
        self.assertEqual(result["targets"][0]["confirmed_sales_last_hour"], 1)
        self.assertEqual(result["targets"][0]["target"], 1)
        self.assertEqual(result["cash_reserve"], 400)
        self.assertEqual(inventory_analysis(None, cfg, 10000)["status"], "unavailable")
        self.assertEqual(inventory_analysis({}, cfg, 10000)["status"], "unavailable")

    def test_old_round_and_stale_observations_never_enter_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "state/shop/evidence.json"
            p.parent.mkdir(parents=True)
            for rnd, at in ((3, 1000), (4, 0), (4, 1001)):
                p.write_text(json.dumps({"round": rnd, "observed_at": at, "source": "https://fixture.invalid"}))
                self.assertIsNone(read_evidence(tmp, "shop", 1000))
            p.write_text(json.dumps({"round": 4, "observed_at": 1000, "source": "https://fixture.invalid"}))
            self.assertIsNotNone(read_evidence(tmp, "shop", 1000))
        self.assertIsNone(policy()["shop"]["horizon"]["ends_at"])
        self.assertFalse(policy()["shop"]["restock"]["focused_goods"]["enabled"])
        self.assertNotIn("review_preferences", policy()["buyer"])
