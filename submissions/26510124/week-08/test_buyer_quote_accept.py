import base64
from copy import deepcopy
import json
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from buyer_policy import buying_terms
from buyer_quote_accept import certified_choice
from market_buyer import trade_and_record
from seller_agent import DEFAULT, build
from test_inventory import unit
import test_closing as fixtures
from vendor import market_kit_v4 as kit


class CertifiedQuoteTests(unittest.TestCase):
    setUp = fixtures.ClosingTests.setUp
    seller = fixtures.ClosingTests.seller

    def inputs(self, kind="goods"):
        key = Ed25519PrivateKey.generate()
        body = {"sku": "G00001", "unit": "u1", "shop": "ours", "kind": kind,
                "grade": "B" if kind == "goods" else None, "expires_at": time.time()+36000 if kind == "food" else None}
        cert = {"cert": body, "sig": base64.b64encode(key.sign(kit.canon(body))).decode()}
        order = {"order": "o1", "round": 3, "kind": kind, "acceptable": ["G00001"],
                 "market_pubkey": kit.pub_b64(key), "use_within_hours": 2}
        terms = buying_terms(99000, product={"list_price": 100000}, order=order)
        quote = {"sku": "G00001", "unit": "u1", "ask": 60000, "grade": "B", "cert": cert, "warranty_hours": 6}
        task = {"status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"parts": [
            {"text": "List price: 100000. Asking price: 60000."}, {"data": quote}]}}}
        args = dict(item="G00001", shop_name="ours", order=order, policy=terms, market_dir=self.root, seconds_left=270)
        return task, args, key

    def test_accepts_target_only_and_rejects_conflicts_and_bad_certificates(self):
        task, args, _ = self.inputs()
        self.assertEqual(certified_choice(task, **args)["price"], 60000)
        for change in (lambda t: t["status"]["message"].update(metadata={"ask": 60001}),
                       lambda t: t["status"]["message"]["parts"][0].update(text="Asking price: 60001"),
                       lambda t: t["status"]["message"]["parts"][1]["data"].update(warranty_hours=0),
                       lambda t: t["status"]["message"]["parts"][1]["data"]["cert"]["cert"].update(grade="A"),
                       lambda t: t["status"]["message"]["parts"].append({"data": {"price": 60000}})):
            altered = deepcopy(task); change(altered)
            self.assertIsNone(certified_choice(altered, **args))
        self.assertIsNone(certified_choice(task, **{**args, "shop_name": "other"}))
        self.assertIsNone(certified_choice(task, **{**args, "policy": {**args["policy"], "target_price": 59000}}))
        self.assertIsNone(certified_choice(task, **{**args, "order": {**args["order"], "acceptable": []}}))

    def test_food_requires_fresh_clock_and_use_horizon_with_checkout_headroom(self):
        task, args, key = self.inputs("food")
        with patch("buyer_quote_accept.schedule_now", return_value={"status": "missing"}):
            self.assertIsNone(certified_choice(task, **args))
        with patch("buyer_quote_accept.schedule_now", return_value={"status": "synced", "now": time.time()-780}):
            self.assertIsNotNone(certified_choice(task, **args))
            cert = task["status"]["message"]["parts"][1]["data"]["cert"]
            cert["cert"]["expires_at"] = time.time()+3*3600+100
            cert["sig"] = base64.b64encode(key.sign(kit.canon(cert["cert"]))).decode()
            self.assertIsNone(certified_choice(task, **args))

    def test_actual_trade_skips_model_but_still_waits_for_checkout(self):
        task, args, _ = self.inputs()
        product = unit(grade="B", cost=40000, cert=task["status"]["message"]["parts"][1]["data"]["cert"])
        # A B-grade quote with no warranty must keep the original model path.
        seller, key = self.seller(product)
        seller.policy["goods_warranty_hours"]["B"] = 6
        with patch("seller_r3.inventory_decision", return_value=fixtures.counter(60000)), \
                patch("market_buyer.buyer_decision", side_effect=AssertionError("No buyer model expected")) as model, \
                TestClient(build(DEFAULT, seller)) as client:
            result = trade_and_record({"name": "ours", "url": "http://testserver"}, item="G00001", budget=99000,
                memory_path=self.root / "memory.jsonl", reason="fixture", seconds=10, client=client,
                order=args["order"], policy=args["policy"], market_dir=self.root)
        self.assertIsNotNone(result["receipt"])
        self.assertEqual(result["receipt"]["price"], 60000)
        model.assert_not_called()
        row = json.loads((self.root / "memory.jsonl").read_text())
        self.assertEqual(row["result"], "receipt_issued")
        self.assertEqual(row["negotiation_diagnostics"]["fast_accepts"], 1)
        self.assertEqual(row["negotiation_diagnostics"]["model_decisions"], 0)
