from __future__ import annotations

import socket
import unittest
import base64
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from adapters.operational import SettlementGuard, buyer_preview, negotiation_guard, restock_decision, seller_preview


class OperationalAdapterTests(unittest.TestCase):
    def test_restock_defaults_to_exact_safe_output(self):
        self.assertEqual(restock_decision({}, {}, {}), {"buy": []})

    def test_preview_never_authorizes_purchase(self):
        result = restock_decision({"balance": 5_000_000}, {"X": {"cost": 100_000, "kind": "goods"}},
                                  {"items": [{"sku": "X", "qty": 2}]}, "preview")
        self.assertEqual(result["buy"], [])
        self.assertEqual(result["preview"][0]["estimated_cost"], 200_000)

    def test_seller_uses_real_inventory_unit_and_no_below_cost_floor(self):
        result = seller_preview({"units": [{"sku": "X", "unit": "u1", "cost": 100, "grade": "A"}]},
                                {"X": {"list_price": 150, "kind": "goods"}}, {"acceptable": ["X"]})
        self.assertEqual(result["unit"], "u1")
        self.assertGreaterEqual(result["floor"], 100)

    def test_buyer_requires_verified_offer_and_builds_accept_policy(self):
        order = {"acceptable": ["X"], "budget": 200, "use_within_hours": 3}
        bad = {"shop": "bad", "sku": "X", "ask": 50, "certificate_status": "invalid", "grade": "A"}
        good = {"shop": "good", "sku": "X", "ask": 100, "certificate_status": "unknown", "grade": "A",
                "kind": "food", "list_price": 150, "warranty_hours": 0, "reliability": .9}
        result = buyer_preview(order, [bad, good])
        self.assertEqual(result["shop"], "good")
        self.assertEqual(result["accept"]["min_hours_left"], 3)

    def test_negotiation_failure_and_duplicate_guard(self):
        self.assertEqual(negotiation_guard(90, 100, 200, 1)["action"], "reject")
        self.assertEqual(negotiation_guard(110, 100, 200, 4)["reason"], "negotiation_limit")
        guard = SettlementGuard()
        self.assertTrue(guard.reserve("order-1"))
        self.assertFalse(guard.reserve("order-1"))

    def test_food_unit_must_cover_use_window(self):
        result = seller_preview({"units": [{"sku": "X", "unit": "u1", "cost": 100, "grade": "A",
                                              "expires_at": 10_100}]},
                                {"X": {"list_price": 150, "kind": "food"}},
                                {"acceptable": ["X"], "kind": "food", "at": 10_000, "use_within_hours": 1})
        self.assertEqual(result["action"], "reject")

    def test_adapters_are_offline(self):
        with patch.object(socket, "socket", side_effect=AssertionError("network")):
            self.assertEqual(restock_decision({}, {}, {}), {"buy": []})

    def test_official_kit_rejects_bad_market_certificate(self):
        root = Path(__file__).parents[4]
        sys.path.insert(0, str(root))
        from scripts.market_kit import check_accept
        key = Ed25519PrivateKey.generate()
        pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        order = {"market_pubkey": base64.b64encode(pub).decode(), "kind": "food", "shelf_hours": 24}
        body = {"unit": "u1", "sku": "X", "grade": "A", "expires_at": time.time() + 7200}
        bad = {"cert": body, "sig": base64.b64encode(b"not-a-valid-signature").decode()}
        checkout = {"unit": "u1", "item": "X", "cert": bad}
        self.assertIn("not the market's certificate", check_accept(order, checkout, {"cert": True}))

    def test_preview_respects_total_stock_cap(self):
        inventory = {"balance": 5_000_000, "units": [{"cost": 990_000}]}
        catalog = {"X": {"estimated_cost": 100_000, "kind": "goods"}}
        result = restock_decision(inventory, catalog, [{"sku": "X", "qty": 1}], "preview")
        self.assertEqual(result["buy"], [])
        self.assertEqual(result["preview"], [])


if __name__ == "__main__":
    unittest.main()
