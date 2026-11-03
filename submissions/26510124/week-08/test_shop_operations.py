"""Integrated stock/price controls using synthetic records and real A2A shapes."""
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from feedback import atomic_json, load_json
from opening_strategy import bounds
import restock
import sales_performance
from seller_agent import DEFAULT, build
from seller_r3 import InventorySeller
from shop_policy import policy_at, selling_terms
from test_adaptive_seller import send
from test_inventory import unit
from vendor import market_kit_v4 as kit

ROOT = Path(__file__).resolve().parent


class ShopOperationsTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.md = self.root / ".market"
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.policy = policy_at()
        self.now = datetime.fromisoformat("2026-11-03T12:00:00+09:00").timestamp()
        self.end = datetime.fromisoformat(self.policy["horizon"]["ends_at"]).timestamp()
        self.inv = {"round": 3, "balance": 5000000, "start": 5000000, "injected": 0,
                    "bankrupt_line": 500000, "bankruptcies_left": 2, "units": []}
        self.catalog = {"F00001": {"name": "food", "kind": "food", "category": "food", "list_price": 10000},
                        "G00001": {"name": "goods", "kind": "goods", "category": "goods", "list_price": 100000}}
        self.demand = [{"category": "food"}, {"category": "goods"}]
        atomic_json(self.root / "shop-policy.json", self.policy)
        atomic_json(self.md / "catalog.json", self.catalog)

    def run_cycle(self, inv, now):
        atomic_json(self.md / "demand.json", self.demand)
        os.utime(self.md / "demand.json", (now, now))
        with patch("restock.Inventory") as inventory, patch("restock.refresh_market", return_value=None) as network, \
                patch("harness.decide", side_effect=AssertionError("No model for inventory control")):
            inventory.return_value.snapshot.return_value = inv
            decision = restock.run_once(self.root, now)
        self.assertEqual(network.call_count, 1)  # Existing bounded poll; no extra fetch introduced.
        evidence = [json.loads(line) for line in (self.logs / "restock-decisions.jsonl").read_text().splitlines()][-1]
        return decision, evidence

    def plan(self, inv=None, sales=(), now=None):
        return restock.plan(inv or self.inv, self.catalog, self.demand, {"sales": list(sales)}, [],
                            self.policy["restock"], self.now if now is None else now,
                            horizon=self.policy["horizon"])

    def sale(self, index, now):
        return {"unit": str(index), "sku": "G00001", "category": "goods", "kind": "goods",
                "cost": 70000, "price": 85000, "supplied_at": now - 10 * 3600,
                "settled_at": now - 9 * 3600, "warranty_hours": 6, "refunds": []}

    def test_missing_food_pauses_rebuy_then_late_settlement_restores_it_without_model(self):
        food = unit(unit="food1", sku="F00001", category="food", kind="food", cost=6000,
                    list_price=10000, supplied_at=self.now - 100, expires_at=self.now + 100)
        self.run_cycle({**self.inv, "units": [food]}, self.now)
        missing, detail = self.run_cycle(self.inv, self.now + 901)
        self.assertNotIn("F00001", {row["sku"] for row in missing["buy"]})
        self.assertEqual(detail["stock_observations"]["expired_unconfirmed_cost"], 6000)
        self.assertEqual(detail["wallet_profit"], 0)  # Estimates do not mutate authoritative wallet profit.
        receipt = {"round": 3, "time": self.now + 10, "outcome": "receipt_issued", "unit": "food1",
                   "sku": "F00001", "category": "food", "kind": "food", "cost": 6000,
                   "price": 8000, "supplied_at": food["supplied_at"], "warranty_hours": 0}
        (self.logs / "inventory-seller-events.jsonl").write_text(json.dumps(receipt) + "\n")
        (self.logs / "market-kit.log").write_text("12:00:00 [acp] settled Food at 8000, ledger #1, unit food1 grade A\n")
        restored, detail = self.run_cycle(self.inv, self.now + 1802)
        self.assertEqual(detail["stock_observations"]["unresolved_cost"], 0)
        self.assertIn("F00001", {row["sku"] for row in restored["buy"]})
        self.assertEqual(load_json(self.md / "stock-lifecycle.json")["units"]["food1"]["state"], "settled")

    def test_large_single_purchase_cannot_spend_entire_free_cash(self):
        self.catalog["G00001"]["list_price"] = 1000000
        decision, detail = self.plan()
        self.assertNotIn("G00001", {row["sku"] for row in decision["buy"]})
        self.assertLessEqual(detail["budgeted_cost"], 500000)
        self.assertGreaterEqual(detail["projected_min_balance"], 2000000)

    def test_cash_starvation_is_visible_and_never_spends_reserve_or_files_bankruptcy(self):
        for balance, eligible in ((1500000, False), (499999, True)):
            decision, detail = self.plan(inv={**self.inv, "balance": balance})
            self.assertEqual(decision, {"buy": []})
            self.assertEqual(detail["operation_status"], "cash_floor_no_stock")
            self.assertTrue(detail["needs_attention"])
            self.assertEqual(detail["bankruptcy_eligible"], eligible)
            self.assertFalse(detail["automatic_bankruptcy"])

    def test_live_hook_stops_buying_near_deadline_and_for_unknown_round(self):
        for rnd, now in ((3, self.end - 6 * 3600), (4, self.end - 5 * 3600)):
            with self.subTest(round=rnd):
                # Independent temp control avoids cross-round fixture timestamps.
                control = self.md / "restock-control.json"
                if control.exists():
                    control.unlink()
                decision, detail = self.run_cycle({**self.inv, "round": rnd}, now)
                self.assertEqual(decision, {"buy": []})
                self.assertFalse(detail["horizon"]["allow_purchases"])

    def test_final_day_only_buys_proven_fast_sku_and_still_obeys_caps(self):
        now = self.end - 12 * 3600
        decision, detail = self.plan(sales=[self.sale(i, now) for i in range(3)], now=now)
        self.assertEqual(decision["buy"], [{"sku": "G00001", "qty": 3}])
        self.assertFalse(detail["horizon"]["allow_exploration"])
        self.assertIn("unproven_near_round_end", detail["horizon_exclusions"])
        # Existing holding cost also consumes the money cap even with a low unit count.
        inv = {**self.inv, "units": [unit(unit="held", sku="G00001", category="goods", cost=450000,
                                          supplied_at=now - 100)]}
        decision, _ = self.plan(inv=inv, sales=[self.sale(i, now) for i in range(3)], now=now)
        self.assertEqual(decision["buy"], [])

    def test_clearance_is_round_specific_and_applies_without_quote_reference(self):
        product = unit(grade="A")
        terms = selling_terms(product, self.policy)
        before = deepcopy(terms)
        band = bounds(product, terms, None, self.policy, self.end - 12 * 3600, rnd=3)
        self.assertEqual((band["minimum"], band["maximum"]), (77000, 77770))
        self.assertEqual(terms, before)
        # Fresh supplied time avoids the independent old-stock markdown.
        product["supplied_at"] = self.end - 12 * 3600
        self.assertIsNone(bounds(product, terms, None, self.policy, self.end - 12 * 3600, rnd=4))

    def test_real_a2a_clearance_acceptance_keeps_warranty_and_uses_one_decision(self):
        product = unit(grade="A")
        atomic_json(self.md / "inventory.json", {**self.inv, "units": [product]})
        key = Ed25519PrivateKey.generate()
        with patch("seller_agent.load_shop_key", return_value=key):
            seller = InventorySeller({**DEFAULT, "name": "ours", "market_dir": self.md})
        seller.events = self.logs / "seller.jsonl"
        clock = SimpleNamespace(time=lambda: self.end - 6 * 3600 - 30)
        def decide(prompt, context, validate, **kwargs):
            self.assertEqual(context["negotiation"]["opening_bounds"]["source"], "round_end_stock_clearance")
            self.assertEqual(context["sales_terms"]["warranty_hours"], 6)
            return validate('{"action":"counter","price":77770,"reason":"bounded clearance"}')
        with patch("seller_r3.time", clock), patch("seller_r3.decide", side_effect=decide) as model, \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            # Within the same 5-minute negotiation, the next phase begins.
            clock.time = lambda: self.end - 6 * 3600 + 30
            done = send(client, "77770", first["id"])
        self.assertEqual(model.call_count, 1)
        receipt = done["artifacts"][0]["parts"][0]["data"]
        receipt["price"] = int(receipt["price"])
        self.assertEqual(receipt["price"], 77770)
        self.assertEqual(receipt["warranty_hours"], 6)
        self.assertTrue(kit.verify_seller(kit.pub_b64(key), receipt))

    def test_sales_epoch_boundary_survives_public_cache_expiry(self):
        public = {"round": 3, "reset_at": self.now - 60, "events": []}
        first = sales_performance.update(self.root, 3, self.now, public=public)
        with patch("sales_performance.read_market", return_value=None):
            second = sales_performance.update(self.root, 3, self.now + 900)
        self.assertEqual(first["reset_at"], second["reset_at"])
