"""Offline buyer policy regressions; only unique temporary fixtures are written.

Run: python -B submissions/25512085/week-08/test_buyer_price_offline.py
No model, network, real market directory, keys, or persistent hub is used.
"""
from contextlib import ExitStack, contextmanager, redirect_stdout
import copy
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import market_buyer as buyer
import store


class BuyerPriceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        preferred = Path.home() / "AppData/Local/Temp/opencode"
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
            prefix="buyer-price-offline-", dir=preferred if preferred.exists() else None)))
        self.now = 1_800_000_000
        self.sku = "f00000001"
        self.catalog = {self.sku: {"list_price": 16700, "name": "누룽지", "category": "food"},
                        "f00000002": {"list_price": 10000, "name": "other", "category": "food"}}
        self.hub = store.default_hub()
        self.order = {"order": "order-1", "kind": "food", "category": "food",
                      "acceptable": [self.sku, "f00000002"], "use_within_hours": 12}
        self.stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")))
        self.stack.enter_context(patch.object(subprocess, "Popen", side_effect=AssertionError("process forbidden")))
        self.stack.enter_context(patch.object(buyer.httpx, "post", side_effect=AssertionError("HTTP forbidden")))
        self.stack.enter_context(patch.object(store.urllib.request, "urlopen", side_effect=AssertionError("HTTP forbidden")))
        self.stack.enter_context(patch.object(buyer.time, "time", return_value=self.now))

    def receipt(self, price, **extra):
        # Presence-only fixture: cryptographic verification belongs to the kit.
        return dict(item=self.sku, price=price, task="task-1", shop="shop",
                    **({"sig_seller": "offline-signature-placeholder"} | extra))

    def run_buyer(self, ask=20040, receipt=None, complete_after=0, budget=25000, legacy=False):
        shops_path = self.tmp / "shops.json"
        shops_path.write_text(json.dumps([{"name": "shop", "url": "https://offline.invalid",
                                            "offers": [{"sku": self.sku}]}]), encoding="utf-8")
        order_path = self.tmp / "order.json"
        order_path.write_text(json.dumps({} if legacy else self.order), encoding="utf-8")
        offers, methods = [], []

        @contextmanager
        def local_hub():
            yield self.hub

        def rpc(url, method, params):
            methods.append(method)
            if method == "CancelTask":
                return {}
            text = params["message"]["parts"][0]["text"]
            if text.isascii() and text.isdigit():
                offers.append(int(text))
            elif text.startswith("I offer "):
                offers.append(int(text.split()[2]))
            done = receipt is not None and len(offers) >= complete_after
            task = {"id": "task-1", "status": {
                "state": "TASK_STATE_COMPLETED" if done else "TASK_STATE_INPUT_REQUIRED",
                "message": {"parts": [{"text": f"certified item ask {ask}"}]}}}
            if done:
                task["artifacts"] = [{"parts": [{"data": receipt}]}]
            return task

        out = io.StringIO()
        with ExitStack() as local:
            replacements = {
                "MEMORY_FILE": self.tmp / "memory.json", "MARKET_DIR": self.tmp / "absent-market",
                "load_hub": lambda: self.hub, "load_catalog": lambda *a: self.catalog,
                "sync_broken_and_payments": lambda *a: [], "record_buyer_order": lambda *a: None,
                "mutate_hub": local_hub, "fetch_board_stats": lambda: {},
                "check_acp_support": lambda *a: True, "rpc_call": rpc,
                "call_harness": lambda *a: "OFFER 100",  # never invoke a model, including Round 2
            }
            for name, value in replacements.items():
                local.enter_context(patch.object(buyer, name, value))
            local.enter_context(patch.object(buyer.httpx, "get", return_value=SimpleNamespace(json=lambda: {})))
            local.enter_context(patch.object(sys, "argv", ["buyer", "--shops", str(shops_path),
                "--order", str(order_path), "--item", "lamp" if legacy else "food", "--budget", str(budget)]))
            with redirect_stdout(out):
                buyer.main()
        result = json.loads(out.getvalue().splitlines()[-1])
        memory = buyer.load_json_safe(self.tmp / "memory.json", {})
        return result, offers, methods, memory

    def test_16700_food_never_offers_over_15030_and_cancels(self):
        result, offers, methods, memory = self.run_buyer()
        self.assertIsNone(result["receipt"])
        self.assertEqual(len(offers), 3)  # Round 3 reserves time for alternative shops.
        self.assertEqual(offers[-1], 15030)
        self.assertTrue(all(0 < p <= 15030 for p in offers))
        self.assertNotIn(20040, offers)  # ask is within budget, but no turn-3 auto-accept
        self.assertEqual(methods.count("CancelTask"), 1)
        self.assertFalse(memory["shop"]["dishonest"])

    def test_first_completed_overpriced_is_policy_decline_without_pending_or_stigma(self):
        result, offers, _, memory = self.run_buyer(receipt=self.receipt(20040))
        self.assertEqual(offers, [])
        self.assertIsNone(result["receipt"])
        self.assertFalse(self.hub.get("pending_deals"))
        self.assertFalse(memory["shop"]["dishonest"])
        self.assertEqual(memory["shop"]["recent"][-1]["outcome"], "declined-price-policy")

    def test_every_offer_branch_clamped_even_when_budget_step_exceeds_cap(self):
        for ask, budget in [(15030, 25000), (14000, 25000), (9000, 25000),
                            (20040, 1000000), (None, 25000), (20040, 8000)]:
            with self.subTest(ask=ask, budget=budget):
                result, offers, _, _ = self.run_buyer(ask=ask, budget=budget)
                if ask is None:
                    self.assertEqual(offers, [])  # Unparseable quote now retries immediately.
                else:
                    self.assertTrue(offers)
                self.assertTrue(all(0 < p <= min(15030, budget) for p in offers))
                self.assertIsNone(result["receipt"])

    def test_overpriced_after_negotiation_also_declines_without_stigma(self):
        result, offers, _, memory = self.run_buyer(receipt=self.receipt(20040), complete_after=3)
        self.assertLessEqual(max(offers), 15030)
        self.assertIsNone(result["receipt"])
        self.assertFalse(memory["shop"]["dishonest"])
        self.assertFalse(self.hub.get("pending_deals"))

    def test_cheap_food_passes_with_existing_expiry_accept(self):
        result, _, _, _ = self.run_buyer(receipt=self.receipt(9000))
        self.assertEqual(result["receipt"]["price"], 9000)
        self.assertEqual(result["accept"], {"cert": True, "grades": ["A", "B", "C"], "min_hours_left": 12.25})
        self.assertEqual(self.hub["pending_deals"]["order-1"]["price"], 9000)

    def test_real_shipped_sku_not_target_controls_cap(self):
        receipt = self.receipt(10000)
        receipt["item"] = "f00000002"  # actual list=10000 => cap=9000
        result, _, _, memory = self.run_buyer(receipt=receipt)
        self.assertIsNone(result["receipt"])
        self.assertFalse(memory["shop"]["dishonest"])

    def test_missing_catalog_price_defers_without_rpc(self):
        self.catalog = {}
        result, offers, methods, _ = self.run_buyer()
        self.assertIsNone(result["receipt"])
        self.assertEqual((offers, methods), ([], []))

    def test_unknown_shipped_price_defers(self):
        del self.catalog["f00000002"]
        receipt = self.receipt(5000)
        receipt["item"] = "f00000002"
        result, _, _, memory = self.run_buyer(receipt=receipt)
        self.assertIsNone(result["receipt"])
        self.assertEqual(memory["shop"]["recent"][-1]["outcome"], "declined-price-policy")

    def samples(self):
        return [{"ts": self.now - 600, "amount": amount, "grade": "A",
                 "expires_at": self.now - 600 + 48 * 3600, "seq": i}
                for i, amount in enumerate([9000, 10000, 11000], 1)]

    def policy(self, rows=None, receipt=True):
        self.hub["demand_radar"]["sku_comparables"] = {self.sku: self.samples() if rows is None else rows}
        hint = self.receipt(10000, claim="A", expires_at=self.now + 48 * 3600) if receipt else None
        return buyer.food_price_policy(self.catalog, self.hub, self.sku, 25000, self.order, hint, self.now)

    def test_recent_same_grade_similar_life_median_plus_ten_percent(self):
        cap, accept = self.policy()
        self.assertEqual(cap, 11000)
        self.assertEqual(accept["grades"], ["A"])
        self.assertAlmostEqual(accept["min_hours_left"], 38.4)
        self.assertGreater(48 - 10 / 3600, accept["min_hours_left"])
        result, _, _, memory = self.run_buyer(receipt=self.receipt(12000, claim="A", expires_at=self.now + 48 * 3600))
        self.assertIsNone(result["receipt"])
        self.assertFalse(memory["shop"]["dishonest"])

    def test_comparable_accept_pins_untrusted_claim_for_kit(self):
        self.policy()
        result, _, _, _ = self.run_buyer(receipt=self.receipt(10000, claim="A", expires_at=self.now + 48 * 3600))
        self.assertTrue(result["accept"]["cert"])
        self.assertEqual(result["accept"]["grades"], ["A"])
        self.assertAlmostEqual(result["accept"]["min_hours_left"], 38.4)

    def test_unknown_negotiation_grade_and_expiry_do_not_mix_prices(self):
        self.assertEqual(self.policy(receipt=False), (15030, {}))
        self.hub["demand_radar"]["sku_settles"] = {self.sku: [1, 2, 3]}
        self.assertEqual(buyer.food_price_policy(self.catalog, self.hub, self.sku, 25000,
                                              self.order, self.receipt(10000), self.now), (15030, {}))

    def test_incomparable_observations_fall_back(self):
        mutations = [
            {"ts": self.now - 10801}, {"ts": self.now + 1}, {"grade": "B"}, {"grade": None},
            {"expires_at": self.now - 600 + 10 * 3600},
            {"expires_at": self.now - 600 + 200 * 3600}, {"expires_at": None},
            {"amount": -1}, {"ts": None},
        ]
        for change in mutations:
            with self.subTest(change=change):
                rows = self.samples()
                rows[0].update(change)  # only two qualifying samples remain
                self.assertEqual(self.policy(rows), (15030, {}))
        self.assertEqual(self.policy(self.samples()[:2]), (15030, {}))
        self.policy()
        self.hub["demand_radar"]["sku_comparables"] = {"f00000002": self.samples()}
        self.assertEqual(buyer.food_price_policy(self.catalog, self.hub, self.sku, 25000,
                         self.order, self.receipt(10000, claim="A", expires_at=self.now + 48 * 3600), self.now), (15030, {}))

    def test_comparables_never_raise_list_or_budget_cap(self):
        rows = self.samples()
        for row in rows:
            row["amount"] = 100000
        self.assertEqual(self.policy(rows)[0], 15030)
        self.assertEqual(buyer.food_price_policy(self.catalog, self.hub, self.sku, 8000, self.order)[0], 8000)

    def test_shelf_similarity_uses_both_bucket_and_range(self):
        self.assertTrue(buyer.similar_food_remaining(48, 50, 12))
        self.assertFalse(buyer.similar_food_remaining(31, 33, 12))  # close, different bucket
        self.assertFalse(buyer.similar_food_remaining(33, 60, 12))  # same bucket, too different
        self.assertFalse(buyer.similar_food_remaining(12, 12, 12))  # checkout margin

    def test_budget_and_receipt_tampering_still_rejected(self):
        for receipt in [self.receipt(26000), dict(self.receipt(9000), task="wrong"),
                        dict(self.receipt(9000), item="unacceptable")]:
            with self.subTest(receipt=receipt):
                result, _, _, memory = self.run_buyer(receipt=receipt)
                self.assertIsNone(result["receipt"])
                self.assertEqual(memory["shop"]["recent"][-1]["outcome"], "rip-off")
        result, offers, _, memory = self.run_buyer(ask=8000, receipt=self.receipt(9000), complete_after=1)
        self.assertLess(offers[-1], 9000)
        self.assertIsNone(result["receipt"])
        self.assertEqual(memory["shop"]["recent"][-1]["outcome"], "rip-off")

    def test_goods_expected_surplus_warranty_and_unknown_grade_unchanged(self):
        self.order["kind"] = "goods"
        # No claim: goods still conservatively assume C. 0.65 * 16700 < 12000.
        result, _, _, _ = self.run_buyer(receipt=self.receipt(12000))
        self.assertIsNone(result["receipt"])
        # Stay inside the new goods 80% safety cap while isolating quality rules.
        result, _, _, _ = self.run_buyer(receipt=self.receipt(13000, warranty_hours=6))
        self.assertEqual(result["receipt"]["price"], 13000)
        self.assertEqual(result["accept"]["warranty_hours"], 6)
        result, _, _, _ = self.run_buyer(receipt=self.receipt(13000, claim="A"))
        self.assertIsNotNone(result["receipt"])

    def test_goods_negotiation_caps_offers_and_rejects_zero_surplus(self):
        self.order["kind"] = "goods"
        result, offers, _, memory = self.run_buyer(ask=16700)
        self.assertIsNone(result["receipt"])
        self.assertEqual(len(offers), 3)
        self.assertEqual(offers[-1], 13360)  # 16700 * .80, now stricter than .92
        self.assertTrue(all(0 < p <= 13360 for p in offers))
        result, _, _, memory = self.run_buyer(receipt=self.receipt(16700, warranty_hours=6))
        self.assertIsNone(result["receipt"])
        self.assertEqual(memory["shop"]["recent"][-1]["outcome"], "declined-price-policy")

    def test_round2_negotiation_and_receipt_unchanged(self):
        result, offers, _, _ = self.run_buyer(ask=200, receipt=dict(self.receipt(200), item="lamp"),
                                             complete_after=3, budget=250, legacy=True)
        self.assertEqual(offers, [100, 155, 200])
        self.assertEqual(result["receipt"]["price"], 200)
        self.assertNotIn("accept", result)

    def test_store_preserves_aggregates_and_deduplicates_seq(self):
        hub = store.default_hub()
        del hub["demand_radar"]["sku_comparables"]  # pre-change state
        row = {"seq": 1, "kind": "settle", "ts": self.now, "data": {
            "buyer": "other", "item": self.sku.upper(), "amount": 10000, "grade": "A",
            "expires_at": self.now + 48 * 3600, "shop": "shop", "order": "order-1"}}
        store.apply_ledger_rows(hub, [row, row], self.catalog)
        first = copy.deepcopy(hub)
        store.apply_ledger_rows(hub, [row], self.catalog)
        self.assertEqual(hub, first)
        radar = hub["demand_radar"]
        self.assertEqual(radar["market_settles"], {"food": 1})
        self.assertEqual(radar["sku_settles"][self.sku], [10000])
        self.assertEqual(radar["sku_sold_ts"][self.sku], [self.now])
        self.assertEqual(radar["cat_sold_ts"]["food"], [self.now])
        observation = radar["sku_comparables"][self.sku][0]
        self.assertEqual(observation["grade"], "A")
        self.assertNotIn("order", observation)  # prediction metadata is not a second ledger
        self.assertNotIn("seq", observation)
        self.assertEqual(hub["ledger_seq"], 1)  # replay dedup remains cursor-based
        for seq in range(2, 65):
            entry = copy.deepcopy(row)
            entry["seq"] = seq
            store.apply_ledger_rows(hub, [entry], self.catalog)
        self.assertEqual(len(radar["sku_comparables"][self.sku]), 50)
        self.assertEqual(len(radar["sku_settles"][self.sku]), 30)
        own = copy.deepcopy(row)
        own["seq"] = 65
        own["data"]["buyer"] = store.OWN_NAME
        result = copy.deepcopy(row)
        result.update(seq=66, kind="result")
        store.apply_ledger_rows(hub, [own, result], self.catalog)
        self.assertEqual(radar["market_settles"]["food"], 64)
        self.assertEqual(hub["ledger_seq"], 66)

    def test_missing_public_grade_not_replaced_by_claim(self):
        row = {"seq": 1, "kind": "settle", "data": {
            "buyer": "other", "item": self.sku, "amount": 100, "claim": "A"}}
        store.apply_ledger_rows(self.hub, [row], self.catalog)
        observation = self.hub["demand_radar"]["sku_comparables"][self.sku][0]
        self.assertIsNone(observation["grade"])
        self.assertIsNone(observation["ts"])
        self.assertIsNone(observation["expires_at"])

    def test_compile_without_bytecode(self):
        for name in ("market_buyer.py", "store.py", Path(__file__).name):
            path = Path(__file__).with_name(name)
            compile(path.read_text(encoding="utf-8"), str(path), "exec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
