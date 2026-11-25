"""Behavioral checks for the first shop-side Round 3 strategy slice."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
from collections import Counter

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import market_memory
import newsvendor_tool
import restock
import seller_r3
import buyer_memory
import buyer_policy
import buyer_r3
import market_buyer_r3
import review_r3


class NewsvendorTests(unittest.TestCase):
    def test_price_guidance_compares_profit_with_paid_same_grade_and_coverage(self):
        from newsvendor_tool import price_guidance
        now = 500_000
        unit = {'sku': 'G1', 'grade': 'A', 'cost': 180_000, 'kind': 'goods',
                'supplied_at': now - 86400}
        evidence = {'outcome': 'SETTLED', 'sku': 'G1', 'grade': 'A', 'settled_grade': 'A',
                    'opening_ask': 230_000,
                    'settled_at': now - 60, 'warranty_hours_at_receipt': 6}
        rows = [{**evidence, 'ledger': n, 'settled_price': price}
                for n, price in enumerate((198_000, 210_000, 224_000), 1)]
        rows += [rows[1], {**evidence, 'ledger': 4, 'settled_price': 280_000, 'warranty_hours_at_receipt': 72},
                 {**evidence, 'ledger': 5, 'settled_price': 280_000, 'settled_grade': 'B'},
                 {**evidence, 'ledger': 6, 'settled_price': 300_000, 'settled_at': now-73*3600},
                 {**evidence, 'ledger': 7, 'settled_price': 300_000, 'outcome': 'RECEIPT_PENDING'}]
        got = price_guidance(unit, now=now, warranty_hours=6, attempts=rows,
                             offered_price=200_000, target_profit=5_000)
        self.assertEqual(got['break_even_price'], 202_000)
        self.assertEqual(got['minimum_profitable_price'], 202_001)
        self.assertEqual(got['minimum_price_for_target'], 207_556)
        self.assertEqual(got['comparable_paid_72h']['count'], 3)
        self.assertEqual(got['comparable_paid_72h']['median'], 210_000)
        self.assertEqual(got['suggested_opening_price'], 230_000)
        self.assertEqual(got['estimated_profit_at_offer'], -1_800)
        self.assertEqual(got['estimated_profit_at_suggestion'], 25_200)
        self.assertIsNone(price_guidance(unit, now=now, warranty_hours=6, attempts=rows,
                                        target_profit=15_000)['suggested_opening_price'])

    def test_paid_prices_without_original_quotes_do_not_become_new_opening_quotes(self):
        unit = {'sku': 'G1', 'grade': 'A', 'cost': 100, 'kind': 'goods'}
        rows = [{'sku': 'G1', 'settled_grade': 'A', 'outcome': 'SETTLED',
                 'settled_at': 990, 'settled_price': 150, 'ledger': n,
                 'warranty_hours_at_receipt': 0} for n in range(3)]
        result = newsvendor_tool.price_guidance(unit, now=1000, attempts=rows)
        self.assertEqual(result['comparable_paid_72h']['median'], 150)
        self.assertIsNone(result['suggested_opening_price'])
        self.assertEqual(result['comparable_openings_72h']['count'], 0)

    def test_price_guidance_does_not_mix_food_shelf_life_or_count_signed_as_paid(self):
        from newsvendor_tool import price_guidance
        now = 500_000
        unit = {'sku': 'F1', 'grade': 'C', 'cost': 5_000, 'kind': 'food',
                'expires_at': now + 3600}
        evidence = {'outcome': 'SETTLED', 'sku': 'F1', 'settled_grade': 'C',
                    'settled_at': now - 600, 'settled_price': 6_000}
        rows = [{**evidence, 'ledger': 1, 'hours_left_at_receipt': 12},
                {**evidence, 'ledger': 2, 'hours_left_at_receipt': 2}]
        got = price_guidance(unit, now=now, attempts=rows, offered_price=0)
        self.assertEqual(got['minimum_profitable_price'], 5_001)
        self.assertEqual(got['comparable_paid_72h']['count'], 1)
        self.assertEqual(got['estimated_profit_at_offer'], -5_000)
        self.assertIsNone(got['suggested_opening_price'])

    def test_lecture_discrete_example_selects_thirteen(self) -> None:
        probabilities = [0.0] * 4 + [
            0.01, 0.02, 0.04, 0.08, 0.09, 0.11, 0.16,
            0.20, 0.11, 0.10, 0.04, 0.02, 0.01, 0.01,
        ]
        result = newsvendor_tool.recommend_quantity(probabilities, 100, 45, salvage=40)
        self.assertEqual(result["quantity"], 13)
        self.assertAlmostEqual(result["critical_ratio"], 55 / 60, places=5)

    def test_quote_shows_grade_and_expiry_without_cost_floor(self) -> None:
        unit = {"sku": "food-1", "unit": "u1", "kind": "food", "grade": "C",
                "cost": 450, "list_price": 1000, "expires_at": 3600}
        quote = newsvendor_tool.quote_candidates(unit, now=0)
        self.assertEqual([x["price"] for x in quote["candidates"]], [540, 600, 660])
        self.assertEqual(quote["hours_left"], 1.0)


class MemoryTests(unittest.TestCase):
    def test_receipt_does_not_count_until_kit_settles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_log, kit_log, cursor = root / "events.jsonl", root / "kit.log", root / "cursor.json"
            market_memory.record("opening", path=event_log, task_id="t1", sku="food-1",
                                 unit="u1", grade="B", ask=100, list_price=120)
            market_memory.record("offer", path=event_log, task_id="t1", price=90)
            market_memory.record("counter", path=event_log, task_id="t1", ask=95)
            market_memory.record("receipt_signed", path=event_log, task_id="t1", sku="food-1",
                                 unit="u1", grade="B", price=95)
            pending = market_memory.compact_context("food-1", "B", path=event_log)
            self.assertEqual(pending["outcomes"], {"RECEIPT_PENDING": 1})
            self.assertEqual(pending["recent_same_grade"][0]["counters"], [95])
            kit_log.write_text("12:00 [acp] settled Food at 95, ledger #123, unit u1 grade B\n")
            self.assertEqual(market_memory.sync_settlements(kit_log, event_log=event_log,
                                                            cursor_path=cursor), 1)
            self.assertEqual(market_memory.sync_settlements(kit_log, event_log=event_log,
                                                            cursor_path=cursor), 0)
            settled = market_memory.compact_context("food-1", "B", path=event_log)
            self.assertEqual(settled["outcomes"], {"SETTLED": 1})
            self.assertEqual(settled["recent_same_grade"][0]["settled_price"], 95)

    def test_buyer_memory_waits_for_kit_payment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_log, payments = root / "buyer-events.jsonl", root / "payments.jsonl"
            buyer_memory.record("negotiation", path=event_log, order="o1", shop="https://shop",
                                sku="F1", result="receipt_pending")
            self.assertEqual(buyer_memory.context("https://shop", "F1", path=event_log)
                             ["same_sku_payment_outcomes"], {})
            payments.write_text(json.dumps({"order": "o1", "shop": "https://shop", "item": "F1",
                                            "price": 700, "paid": True, "ledger": 10}) + "\n")
            self.assertEqual(buyer_memory.import_payments(payments, event_path=event_log), 1)
            self.assertEqual(buyer_memory.import_payments(payments, event_path=event_log), 0)
            self.assertEqual(buyer_memory.context("https://shop", "F1", path=event_log)
                             ["same_sku_payment_outcomes"], {"paid": 1})

    def test_broken_goods_news_is_product_evidence_not_shop_blame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_log, broken = root / "buyer-events.jsonl", root / "broken.jsonl"
            broken.write_text(json.dumps({"name": "Lamp", "broke_at": 12345}) + "\n")
            self.assertEqual(buyer_memory.import_broken(broken, event_path=event_log), 1)
            self.assertEqual(buyer_memory.import_broken(broken, event_path=event_log), 0)
            first = buyer_memory.context("https://first", "G1", product_name="Lamp", path=event_log)
            second = buyer_memory.context("https://second", "G1", product_name="Lamp", path=event_log)
            self.assertEqual(first["same_product_broken_reports"], 1)
            self.assertEqual(second["same_product_broken_reports"], 1)
            self.assertEqual(first["shop_payment_count"], 0)


class BuyerPolicyTests(unittest.TestCase):
    def test_food_requires_use_window_and_caps_private_budget(self) -> None:
        order = {"kind": "food", "budget": 1200, "use_within_hours": 4,
                 "acceptable": ["F1"]}
        product = {"kind": "food", "list_price": 1000, "shelf_hours": 24}
        self.assertEqual(buyer_policy.price_cap(order, product), 850)
        self.assertEqual(buyer_policy.acceptance(order), {"cert": True, "min_hours_left": 4.0})
        self.assertEqual(buyer_policy.price_cap(order, dict(product, shelf_hours=3.99)), 0)
        self.assertEqual(buyer_policy.price_cap(order, dict(product, shelf_hours=4)), 850)
        self.assertEqual(buyer_policy.price_cap(dict(order, budget=700), product), 700)

    def test_goods_grade_policy_and_receipt_guard(self) -> None:
        order = {"kind": "goods", "budget": 1000, "acceptable": ["G1"]}
        product = {"kind": "goods", "list_price": 1000}
        self.assertEqual(buyer_policy.price_cap(order, product), 750)
        self.assertEqual(buyer_policy.acceptance(order), {"cert": True, "grades": ["A", "B"]})
        receipt = {"item": "G1", "price": 700, "task": "t1", "sig_seller": "signature"}
        self.assertIsNone(buyer_policy.receipt_problem(receipt, selected_sku="G1",
                                                        order=order, final_offer=700,
                                                        task_id="t1", cap=750))
        self.assertIsNotNone(buyer_policy.receipt_problem(dict(receipt, price=800), selected_sku="G1",
                                                           order=order, final_offer=700,
                                                           task_id="t1", cap=750))

    def test_buyer_selects_stocked_external_shop_and_guards_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            os.chdir(directory)
            try:
                order = {"order": "o1", "item": "food", "kind": "food", "budget": 1200,
                         "acceptable": ["F1"], "use_within_hours": 4,
                         "expires": time.time() + 500}
                catalog = {"F1": {"kind": "food", "name": "Food", "list_price": 1000,
                                  "shelf_hours": 24}}
                shops = [{"name": "outseop-26510122", "url": "https://own", "offers": [{"sku": "F1", "qty": 1}]},
                         {"name": "external", "url": "https://external", "offers": [{"sku": "F1", "qty": 1}]}]
                receipt = {"item": "F1", "price": 700, "task": "t1", "sig_seller": "sig"}
                outcome = {"receipt": receipt, "reason": "receipt received", "offers": [700], "task_id": "t1"}
                choice = {"candidate_id": "0", "opening_offer": 700, "reason": "in stock"}
                with patch.object(market_buyer_r3, "decide_json", return_value=choice), \
                     patch.object(market_buyer_r3, "negotiate", return_value=outcome) as mock_negotiate:
                    result = market_buyer_r3.run(order, shops, catalog, "outseop-26510122",epsilon=0)
                self.assertEqual(result["shop"], "https://external")
                self.assertEqual(result["receipt"], receipt)
                self.assertEqual(result["accept"], {"cert": True, "min_hours_left": 4.0})
                self.assertEqual(mock_negotiate.call_args.kwargs["cap"], 850)
            finally:
                os.chdir(previous)

    def test_model_cannot_open_at_the_full_payment_cap(self) -> None:
        candidate = {"id": "0", "shop": "other", "url": "https://other", "sku": "F1",
                     "name": "Food", "list_price": 1000, "cap": 850, "stock": 1,
                     "reviews": {"count": 0, "average": None, "low_rating_count": 0},
                     "first_hand": {}}
        with patch.object(market_buyer_r3, "decide_json", return_value={
                "candidate_id": "0", "opening_offer": 850, "reason": "test"}):
            _, opening = market_buyer_r3.choose([candidate], {"item": "food", "kind": "food"})
        self.assertEqual(opening, 722)

    def test_a2a_request_names_sku_without_revealing_budget(self) -> None:
        first = {"id": "t1", "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                         "message": {"parts": [{"text": "asking 700"}]}}}
        receipt = {"item": "F1", "price": 600, "task": "t1", "sig_seller": "sig"}
        completed = {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED"},
                     "artifacts": [{"parts": [{"data": receipt}]}]}
        order = {"kind": "food", "budget": 1200, "acceptable": ["F1"]}
        with patch.object(buyer_r3, "card_endpoint", return_value="https://shop/a2a"), \
             patch.object(buyer_r3, "send", side_effect=[first, completed]) as mock_send:
            outcome = buyer_r3.negotiate("https://shop", "F1", "Food", order,
                                         opening_offer=600, cap=850, list_price=1000,
                                         memory={}, deadline=time.monotonic() + 240)
        self.assertEqual(outcome["receipt"], receipt)
        self.assertEqual(outcome["offers"], [600])
        self.assertIn("F1", mock_send.call_args_list[0].args[2])
        self.assertNotIn("1200", mock_send.call_args_list[0].args[2])

    def test_buyer_retries_other_shop_after_mismatched_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            os.chdir(directory)
            try:
                order = {"order": "o1", "item": "food", "kind": "food", "budget": 1200,
                         "acceptable": ["F1"], "use_within_hours": 1,
                         "expires": time.time() + 500}
                catalog = {"F1": {"kind": "food", "list_price": 1000, "shelf_hours": 24}}
                shops = [{"name": "first", "url": "https://first", "offers": [{"sku": "F1", "qty": 1}]},
                         {"name": "second", "url": "https://second", "offers": [{"sku": "F1", "qty": 1}]}]
                wrong = {"receipt": {"item": "F1", "price": 900, "task": "t1", "sig_seller": "sig"},
                         "offers": [700], "task_id": "t1", "reason": "receipt received"}
                good = {"receipt": {"item": "F1", "price": 700, "task": "t2", "sig_seller": "sig"},
                        "offers": [700], "task_id": "t2", "reason": "receipt received"}
                with patch.object(market_buyer_r3, "decide_json", return_value={
                        "candidate_id": "0", "opening_offer": 700, "reason": "test"}), \
                     patch.object(market_buyer_r3, "negotiate", side_effect=[wrong, good]):
                    result = market_buyer_r3.run(order, shops, catalog, "own",epsilon=0)
                self.assertEqual(result["shop"], "https://second")
                self.assertEqual(result["receipt"]["price"], 700)
                first_memory = buyer_memory.context("https://first", "F1")
                self.assertEqual(first_memory["observed_receipt_problems"], 1)
            finally:
                os.chdir(previous)

    def test_review_records_settlement_and_bounded_forum_post(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            os.chdir(directory)
            try:
                deal = {"order": {"order": "o1", "kind": "food"},
                        "receipt": {"item": "F1", "price": 700},
                        "settled": {"shop": "https://shop", "sku": "F1", "name": "Food",
                                    "grade": "A", "value": 1000, "ledger": 12}}
                catalog = {"F1": {"list_price": 1000}}
                result = review_r3.run(deal, catalog,allow_forum_publish=True)
                self.assertEqual(result["stars"], 5)
                self.assertEqual(len(Path(".market/outbox.jsonl").read_text().splitlines()), 1)
                deal["settled"]["ledger"] = 13
                review_r3.run(deal, catalog,allow_forum_publish=True)
                self.assertEqual(len(Path(".market/outbox.jsonl").read_text().splitlines()), 1)
            finally:
                os.chdir(previous)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        # This class tests active-market behavior, independently of today's date.
        # Production keeps its real deadline; only this fixture uses an active period.
        synthetic_end = time.time() + 48 * 3600
        for target in ('capital_policy.ROUND_END', 'newsvendor_tool.ROUND_END'):
            deadline = patch(target, synthetic_end)
            deadline.start()
            self.addCleanup(deadline.stop)

    def test_a2a_model_choice_is_emitted_and_unit_is_held_after_receipt(self) -> None:
        async def exchange() -> None:
            app = seller_r3.build(9999)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://testserver") as client:
                async def send(message: str, task_id: str | None = None) -> dict:
                    body = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()),
                            "parts": [{"text": message}]}
                    if task_id:
                        body["taskId"] = task_id
                    response = await client.post("/", json={"jsonrpc": "2.0", "id": "1",
                                                     "method": "SendMessage", "params": {"message": body}},
                                                 headers={"A2A-Version": "1.0"})
                    response.raise_for_status()
                    result = response.json()
                    self.assertNotIn("error", result)
                    return result["result"].get("task", result["result"])

                first = await send("I want to buy A1")
                self.assertIn("asking 120", first["status"]["message"]["parts"][0]["text"])
                second = await send("110", first["id"])
                self.assertEqual(second["status"]["state"], "TASK_STATE_COMPLETED")
                third = await send("I want to buy A1")
                self.assertEqual(third["status"]["state"], "TASK_STATE_REJECTED")

        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            os.chdir(directory)
            try:
                key = Ed25519PrivateKey.generate()
                Path("shop_key.pem").write_bytes(key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption()))
                market = Path(".market")
                market.mkdir()
                (market / "inventory.json").write_text(json.dumps({"round": 3, "units": [{
                    "unit": "u1", "sku": "A1", "name": "Example Food", "category": "food>example",
                    "kind": "food", "grade": "A", "cost": 70, "list_price": 100,
                    "supplied_at": 1, "expires_at": time.time() + 36000,
                }]}))
                answers = [
                    {"action": "ask", "price": 120, "unit_id": "u1", "reason": "test quote"},
                    {"action": "accept", "price": None, "unit_id": "u1", "reason": "test acceptance"},
                ]
                with patch.object(seller_r3, "decide_json", side_effect=answers):
                    import asyncio
                    asyncio.run(exchange())
                summary = market_memory.compact_context("A1", "A")
                self.assertEqual(summary["outcomes"], {"RECEIPT_PENDING": 1})
                self.assertEqual(summary["recent_same_grade"][0]["opening_ask"], 120)
            finally:
                os.chdir(previous)

    def test_restock_hook_uses_model_scenario_and_quantity_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            os.chdir(directory)
            try:
                market = Path(".market")
                market.mkdir()
                (market / "inventory.json").write_text(json.dumps(
                    {"round": 3, "balance": 5_000_000, "units": []}))
                (market / "catalog.json").write_text(json.dumps({
                    "food-1": {"kind": "food", "list_price": 1000,
                               "shelf_hours": 72, "name": "Example Food"}}))
                choice = {"action": "buy", "sku": "food-1", "expected_sale_price": 800,
                          "demand_mean": 1.2, "reason": "small exploratory lot"}
                approval = {'action':'approve','candidate_id':'base-600-repeat','valid_for_seconds':600,
                            'max_total_qty':2,'reason':'approve calculated bounded lot'}
                with patch.object(restock, "decide_json", side_effect=[choice,approval]) as model, \
                     patch.object(restock, "fetch_market_sales", return_value=Counter({"food-1": 3})):
                    result = restock.run(market)
                    self.assertEqual(model.call_count,2)
                    approval_prompt=model.call_args_list[1].args[1]
                    self.assertEqual(approval_prompt['market_evidence']['recent_public_settlements'],3)
                    self.assertEqual(approval_prompt['market_evidence']['currently_stocked'],0)
                    self.assertEqual(restock.run(market),{'buy':[]})
                    self.assertEqual(model.call_count,2)  # no call or duplicate intent before refresh
                self.assertEqual(result, {"buy": [{"sku": "food-1", "qty": 1}]})
                decision = next(e for e in reversed(market_memory.events()) if e['event']=='restock_decision')
                self.assertEqual(decision["calculation"]["quantity"], 1)
            finally:
                os.chdir(previous)

    def test_seller_rejects_grade_change_while_model_decides(self) -> None:
        async def exchange() -> None:
            app = seller_r3.build(9999)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://testserver") as client:
                async def send(message: str, task_id: str | None = None) -> dict:
                    body = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()),
                            "parts": [{"text": message}]}
                    if task_id:
                        body["taskId"] = task_id
                    response = await client.post("/", json={"jsonrpc": "2.0", "id": "1",
                                                     "method": "SendMessage", "params": {"message": body}},
                                                 headers={"A2A-Version": "1.0"})
                    response.raise_for_status()
                    result = response.json()
                    self.assertNotIn("error", result)
                    return result["result"].get("task", result["result"])

                first = await send("I want A1")
                second = await send("90", first["id"])
                self.assertEqual(second["status"]["state"], "TASK_STATE_REJECTED")

        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            os.chdir(directory)
            try:
                market = Path(".market")
                market.mkdir()
                inventory = market / "inventory.json"
                unit = {"unit": "u1", "sku": "A1", "name": "Food", "category": "food",
                        "kind": "food", "grade": "A", "cost": 70, "list_price": 100,
                        "supplied_at": 1, "expires_at": time.time() + 36000}
                inventory.write_text(json.dumps({"round": 3, "units": [unit]}))

                calls = 0

                def decide(*_args):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return {"action": "ask", "price": 100, "unit_id": "u1", "reason": "opening"}
                    unit["grade"] = "B"
                    inventory.write_text(json.dumps({"round": 3, "units": [unit]}))
                    return {"action": "accept", "price": None, "unit_id": "u1", "reason": "accept"}

                with patch.object(seller_r3, "decide_json", side_effect=decide):
                    import asyncio
                    asyncio.run(exchange())
                self.assertFalse(any(e["event"] == "receipt_signed" for e in market_memory.events()))
            finally:
                os.chdir(previous)

    def test_seller_does_not_offer_taken_or_expired_units(self) -> None:
        inventory = {"round": 3, "units": [
            {"unit": "u1", "sku": "A1", "name": "Kimchi", "category": "food>kimchi", "grade": "A"},
            {"unit": "u2", "sku": "A1", "name": "Kimchi", "category": "food>kimchi", "grade": "expired"},
            {"unit": "u3", "sku": "A1", "name": "Kimchi", "category": "food>kimchi", "grade": "B"},
        ]}
        result = seller_r3.find_units("I want A1", {"u1"}, inventory)
        self.assertEqual([u["unit"] for u in result], ["u3"])

    def test_public_settlements_enter_candidates_as_labeled_sales(self) -> None:
        catalog = {"a": {"kind": "food", "list_price": 100, "shelf_hours": 72},
                   "b": {"kind": "food", "list_price": 100, "shelf_hours": 72}}
        candidates = restock.choose_candidates(catalog, {"units": []}, [], Counter({"b": 4}))
        self.assertEqual(candidates[0]["sku"], "b")
        self.assertEqual(candidates[0]["recent_public_settlements"], 4)

    def test_public_ledger_reader_ignores_orders_and_old_rounds(self) -> None:
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return [
                    {"round": 3, "kind": "settle", "data": {"item": "a"}},
                    {"round": 3, "kind": "order", "data": {"item": "b"}},
                    {"round": 2, "kind": "result", "data": {"item": "c"}},
                ]

        with patch.object(restock.httpx, "get", return_value=Response()):
            self.assertEqual(restock.fetch_market_sales(), Counter({"a": 1}))


if __name__ == "__main__":
    unittest.main()
