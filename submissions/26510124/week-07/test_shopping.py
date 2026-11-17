"""Offline Challenge 2 tests: no real harness calls or paid transactions."""
import asyncio
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

import httpx

import shopping_buyer as shopping


def product(number=0):
    return {"id": f"sku-{number}", "name": "Low wooden bookcase", "description": "Holds 45 books below desk height",
            "category": "bookshelf", "material": "wood", "height_cm": 70, "capacity_books": 45}


def entry(number=0):
    return {"id": f"need-{number}", "description": "Wooden storage for 40 books, at most 75cm tall",
            "constraints": {"category": "bookshelf", "material": "wood", "height_cm_max": 75, "capacity_books_min": 40}}


def plan():
    return {"assignments": [{"item_id": f"need-{i}", "shop_id": f"shop-{i}", "budget": 120} for i in range(3)],
            "reason": "Each card offers suitable storage and leaves bargaining room"}


def task(number, status, *, ask=140, selected=None, price=None):
    selected = product(number) if selected is None else selected
    result = {"id": f"task-{number}", "status": {"state": "TASK_STATE_" + status, "message": {"parts": [
        {"text": f"Our selected product asking {ask}"}, {"data": {"product": selected, "ask": ask}}]}}}
    if price is not None:
        result["artifacts"] = [{"parts": [{"data": {"item": selected["id"], "product": selected, "price": price}}]}]
    return result


class ContractTests(unittest.TestCase):
    def test_plan_rejects_duplicate_missing_unknown_and_aggregate_overspend(self):
        ids, shops = {f"need-{i}" for i in range(3)}, {f"shop-{i}" for i in range(20)}
        for mutation in ("duplicate", "missing", "unknown", "overspend", "boolean"):
            with self.subTest(mutation=mutation):
                candidate = plan()
                if mutation == "duplicate":
                    candidate["assignments"][2]["item_id"] = "need-0"
                elif mutation == "missing":
                    candidate["assignments"].pop()
                elif mutation == "unknown":
                    candidate["assignments"][0]["shop_id"] = "not-a-real-shop"
                elif mutation == "overspend":
                    candidate["assignments"][0]["budget"] = 121
                else:
                    candidate["assignments"][0]["budget"] = True
                ledger = shopping.AllocationLedger(360)
                with self.assertRaises(shopping.HarnessError):
                    ledger.reserve(candidate, ids, shops)
                self.assertEqual(ledger.allocations, {})

    def test_ledger_rejects_duplicate_purchases_without_mutating_spend(self):
        ledger = shopping.AllocationLedger(360)
        with redirect_stdout(io.StringIO()):
            ledger.reserve(plan(), {f"need-{i}" for i in range(3)}, {f"shop-{i}" for i in range(3)})
        receipt = {"shopping_item_id": "need-0", "shop_id": "shop-0", "price": 110}
        ledger.purchase("need-0", receipt)
        with self.assertRaisesRegex(shopping.ShoppingError, "Duplicate"):
            ledger.purchase("need-0", receipt)
        with self.assertRaisesRegex(shopping.ShoppingError, "allocation"):
            ledger.purchase("need-1", {"shopping_item_id": "need-1", "shop_id": "shop-1", "price": 121})
        self.assertEqual(sum(row["price"] for row in ledger.receipts.values()), 110)

    def test_large_summary_is_rejected_and_recent_turns_preserve_exact_facts(self):
        state = shopping.NegotiationState("need-0", "shop-0", 120, task_id="exact-task", product=product(), last_offer=100)
        for i in range(100):
            state.remember("seller", f"Historic turn {i}")
        state.summary = "Old conversation says the budget was 9999 and taskId was false."
        context = state.context(entry())
        self.assertEqual(len(context["recent_turns"]), 2)
        self.assertEqual(context["recent_turns"][0]["text"], "Historic turn 98")
        self.assertEqual(context["exact_state"]["allocated_budget"], 120)
        self.assertEqual(context["exact_state"]["taskId"], "exact-task")
        decision = {"action": "offer", "price": 100, "summary": "x" * 801, "reason": "Trying to expand context"}
        with self.assertRaisesRegex(shopping.HarnessError, "800"):
            shopping.validate_decision(json.dumps(decision), 120, 140)

    def test_constraints_and_receipt_validate_identity_not_just_affordability(self):
        good = product()
        shopping.verify_product(good, entry()["constraints"])
        for changed in ({"height_cm": 76}, {"capacity_books": 39}, {"material": "metal"}, {"height_cm": True}):
            with self.subTest(changed=changed), self.assertRaises(shopping.ShoppingError):
                shopping.verify_product(good | changed, entry()["constraints"])
        buyer = shopping.ShoppingBuyer(None, {"shops": [{"id": "shop-0", "url": "http://shop.test"}]}, {"items": [entry()]}, 120)
        with redirect_stdout(io.StringIO()):
            buyer.ledger.reserve({"assignments": [{"item_id": "need-0", "shop_id": "shop-0", "budget": 120}], "reason": "One item"}, {"need-0"}, {"shop-0"})
        state = shopping.NegotiationState("need-0", "shop-0", 120, product=good, last_offer=110)
        wrong = task(0, "COMPLETED", selected=good | {"id": "substitute-sku"}, price=110)
        with self.assertRaisesRegex(shopping.ShoppingError, "product differs"):
            buyer.receipt(state, wrong, entry())
        wrong_price = task(0, "COMPLETED", price=111)
        with self.assertRaisesRegex(shopping.ShoppingError, "authorized offer"):
            buyer.receipt(state, wrong_price, entry())
        self.assertFalse(buyer.ledger.receipts)


class ConcurrentShoppingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.opened = set()
        self.open_gate = asyncio.Event()
        self.posts = {}
        self.wrong_product = False
        self.all_ask_high = False
        self.contexts = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        self.buyer = shopping.ShoppingBuyer(self.client,
            {"shops": [{"id": f"shop-{i}", "url": f"http://shop-{i}.test"} for i in range(20)]},
            {"items": [entry(i) for i in range(3)]}, 360)
        self.capture = io.StringIO()
        self.output_context = redirect_stdout(self.capture)
        self.output_context.__enter__()

    async def asyncTearDown(self):
        self.output_context.__exit__(None, None, None)
        await self.client.aclose()

    async def handle(self, request):
        self.assertEqual(request.headers.get("A2A-Version"), "1.0")
        number = int(request.url.host.split(".")[0].split("-")[1])
        self.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"name": f"Storage Shop {number}", "skills": [{"id": "storage", "description": "Wooden low book storage"}],
                "supportedInterfaces": [{"url": f"http://shop-{number}.test/", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]})
        body = json.loads(request.content)
        if body["method"] == "CancelTask":
            result = task(number, "CANCELED")
        else:
            self.assertEqual(body["method"], "SendMessage")
            message = body["params"]["message"]
            count = self.posts.get(number, 0)
            self.posts[number] = count + 1
            if count == 0:
                self.assertNotIn("taskId", message)
                self.assertEqual(message["parts"][1]["data"]["constraints"], entry()["constraints"])
                # A barrier requires all three tasks to overlap, and would fail
                # a sequential implementation before any offer is sent.
                self.opened.add(number)
                if len(self.opened) == 3:
                    self.open_gate.set()
                await asyncio.wait_for(self.open_gate.wait(), 1)
                selected = product(number) | ({"material": "metal"} if self.wrong_product else {})
                result = task(number, "INPUT_REQUIRED", selected=selected)
            else:
                self.assertEqual(message["taskId"], f"task-{number}")
                amount = int(message["parts"][0]["text"])
                self.assertLessEqual(amount, 120)
                result = task(number, "INPUT_REQUIRED", ask=110) if count == 1 else task(number, "COMPLETED", price=amount)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"task": result}})

    def model(self, prompt, context, validator, **kwargs):
        self.assertGreater(kwargs["timeout"], 0)
        if prompt == "shopping_plan.txt":
            self.assertEqual(len(context["shops"]), 20)
            self.assertTrue(all(shop["skills"] for shop in context["shops"]))
            answer = plan()
        else:
            self.contexts.append(context)
            current_round = context["exact_state"]["round"]
            answer = {"action": "offer" if current_round == 0 else "accept", "price": 95 if current_round == 0 else 110,
                      "summary": "Opened at 140; I offer 95." if current_round == 0 else "Offered 95, seller moved 140 to 110; accept 110.",
                      "reason": "Negotiate then accept a matching affordable product"}
        return validator(json.dumps(answer))

    async def test_twenty_cards_three_concurrent_negotiations_and_summaries(self):
        with patch.object(shopping, "decide", side_effect=self.model):
            result = await self.buyer.run()
        self.assertTrue(result["passed"])
        self.assertEqual(result["cards_read"], 20)
        self.assertEqual(result["total_spent"], 330)
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["duplicates"], [])
        self.assertEqual(result["peak_concurrent_negotiations"], 3)
        self.assertEqual(result["peak_distinct_shops"], 3)
        self.assertEqual(len(result["receipts"]), 3)
        later = [context for context in self.contexts if context["exact_state"]["round"] == 1]
        self.assertEqual(len(later), 3)
        for context in later:
            self.assertIn("offer 95", context["summary"])
            self.assertEqual(len(context["recent_turns"]), 2)
            self.assertEqual(context["exact_state"]["allocated_budget"], 120)
        events = [json.loads(line)["event"] for line in self.capture.getvalue().splitlines()]
        self.assertLess(events.index("allocations_reserved"), events.index("negotiation_started"))

    async def test_wrong_product_cancels_before_any_price_or_negotiation_model_call(self):
        self.wrong_product = True
        with patch.object(shopping, "decide", side_effect=self.model) as model:
            result = await self.buyer.run()
        self.assertFalse(result["passed"])
        self.assertEqual(result["total_spent"], 0)
        self.assertEqual(len(result["missing"]), 3)
        self.assertEqual(model.call_count, 1)  # planning only
        self.assertEqual(self.posts, {0: 1, 1: 1, 2: 1})
        cancellations = [request for request in self.requests if request.method == "POST" and json.loads(request.content)["method"] == "CancelTask"]
        self.assertEqual(len(cancellations), 3)

    async def test_one_walkaway_reports_unmet_item_without_reallocating_its_money(self):
        def walkaway(prompt, context, validator, **kwargs):
            if prompt == "shopping_negotiate.txt" and context["shopping_item"]["id"] == "need-0":
                return validator(json.dumps({"action": "walk_away", "summary": "Cannot agree on a suitable price", "reason": "No deal"}))
            return self.model(prompt, context, validator, **kwargs)
        with patch.object(shopping, "decide", side_effect=walkaway):
            result = await self.buyer.run()
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing"], ["need-0"])
        self.assertEqual(result["total_spent"], 220)
        self.assertEqual(sum(row["budget"] for row in self.buyer.ledger.allocations.values()), 360)


if __name__ == "__main__":
    unittest.main()
