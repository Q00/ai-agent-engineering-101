"""Model-free tests for Challenge 1 retrieval, selection, and A2A safety."""
import itertools
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import httpx

import challenge1_seller
import harness
from challenge1_catalog import CatalogError, CatalogIndex


QUERY = "something wooden to keep about 40 books, not taller than my desk"
TARGET_ID = "bookshelf-low-oak-40"


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = CatalogIndex()

    def test_catalog_has_thousands_of_valid_unique_products(self):
        self.assertEqual(len(self.catalog.products), 3001)
        self.assertEqual(len(self.catalog.by_id), 3001)
        self.assertTrue(all(p.name and p.description and p.reserve > 0 for p in self.catalog.products))

    def test_natural_language_search_returns_small_relevant_candidate_set(self):
        candidates = self.catalog.search(QUERY)
        self.assertLessEqual(len(candidates), 8)
        self.assertIn(TARGET_ID, {product.id for product in candidates})
        self.assertLess(len(candidates), len(self.catalog.products))

    def test_selection_must_be_from_retrieved_candidates(self):
        candidates = self.catalog.search(QUERY)
        self.assertEqual(self.catalog.require_candidate(TARGET_ID, candidates).reserve, 260)
        with self.assertRaises(CatalogError):
            self.catalog.require_candidate("lamp-0001", candidates)

    def test_model_prompt_contains_only_candidates_not_full_catalog(self):
        candidates = self.catalog.search(QUERY)
        context = harness.CatalogSelectionContext(
            QUERY,
            tuple(harness.CatalogCandidate(p.id, p.name, p.description) for p in candidates),
        )
        result = {"selected_id": TARGET_ID, "reason": "best semantic match"}
        with patch("harness.run_decision", return_value=result) as run:
            self.assertEqual(harness.decide_catalog_item(context), result)
        prompt = run.call_args.args[0]
        self.assertEqual(prompt.count('"id"'), len(candidates))
        self.assertLess(len(prompt), 5000)
        self.assertNotIn("lamp-0375", prompt)

    def test_model_cannot_select_outside_candidates(self):
        product = self.catalog.search(QUERY, 1)[0]
        context = harness.CatalogSelectionContext(
            QUERY, (harness.CatalogCandidate(product.id, product.name, product.description),),
        )
        with patch("harness.run_decision", return_value={
            "selected_id": "invented-product", "reason": "invalid",
        }):
            with self.assertRaisesRegex(harness.HarnessError, "outside"):
                harness.decide_catalog_item(context)

    def test_audit_is_single_json_line_and_flushes(self):
        with patch("builtins.print") as emit:
            challenge1_seller._audit(
                "catalog_selection", candidate_count=8,
                selected_id=TARGET_ID, reserve=260, task_id="task-1",
            )
        emit.assert_called_once()
        self.assertTrue(emit.call_args.kwargs["flush"])
        payload = json.loads(emit.call_args.args[0])
        self.assertEqual(set(payload), {
            "event", "candidate_count", "selected_id", "reserve", "task_id",
        })


class ChallengeSellerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.executor = challenge1_seller.Challenge1Seller(CatalogIndex())
        self.app = challenge1_seller.build_app(18766, self.executor)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.ids = itertools.count(1)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def send(self, text, task_id=None):
        message = {"role": "ROLE_USER", "messageId": f"m-{next(self.ids)}", "parts": [{"text": text}]}
        if task_id:
            message["taskId"] = task_id
        response = await self.client.post("/", headers={"A2A-Version": "1.0"}, json={
            "jsonrpc": "2.0", "id": str(next(self.ids)), "method": "SendMessage",
            "params": {"message": message},
        })
        value = response.json()["result"]
        return value.get("task", value)

    async def test_description_selects_and_freezes_product_then_completes_safely(self):
        output = StringIO()
        with redirect_stdout(output):
            with patch("challenge1_seller.decide_catalog_item", return_value={
                "selected_id": TARGET_ID, "reason": "match",
            }) as select, patch("challenge1_seller.decide_seller", return_value={
                "action": "ask", "price": 300, "reason": "opening",
            }) as negotiate:
                opened = await self.send(QUERY)
            self.assertEqual(opened["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            self.assertIn(TARGET_ID, opened["status"]["message"]["parts"][0]["text"])
            self.assertEqual(len(select.call_args.args[0].candidates), 8)
            self.assertEqual(negotiate.call_args.args[0].reserve, 260)
            task_id = opened["id"]
            with patch("challenge1_seller.decide_seller", return_value={
                "action": "accept", "price": 260, "reason": "deal",
            }):
                completed = await self.send("260", task_id)
        receipt = completed["artifacts"][0]["parts"][0]["data"]
        self.assertEqual(completed["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(receipt["product_id"], TARGET_ID)
        self.assertEqual(receipt["price"], 260)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([event["event"] for event in events],
                         ["catalog_selection", "trade_completed"])
        self.assertEqual({event["task_id"] for event in events}, {task_id})
        self.assertEqual(events[0], {
            "event": "catalog_selection", "candidate_count": 8,
            "selected_id": TARGET_ID, "reserve": 260, "task_id": task_id,
        })
        self.assertEqual(events[1], {
            "event": "trade_completed", "product_id": TARGET_ID, "price": 260,
            "task_id": task_id, "a2a_state": "TASK_STATE_COMPLETED",
        })

    async def test_out_of_candidate_selection_is_rejected_before_negotiation(self):
        output = StringIO()
        with redirect_stdout(output), patch("challenge1_seller.decide_catalog_item", return_value={
            "selected_id": "invented-product", "reason": "invalid",
        }), patch("challenge1_seller.decide_seller") as negotiate:
            task = await self.send(QUERY)
        self.assertEqual(task["status"]["state"], "TASK_STATE_REJECTED")
        negotiate.assert_not_called()
        self.assertEqual(output.getvalue(), "")

    async def test_selected_product_reserve_is_enforced(self):
        with patch("challenge1_seller.decide_catalog_item", return_value={
            "selected_id": TARGET_ID, "reason": "match",
        }), patch("challenge1_seller.decide_seller", return_value={
            "action": "ask", "price": 259, "reason": "invalid low ask",
        }):
            task = await self.send(QUERY)
        self.assertEqual(task["status"]["state"], "TASK_STATE_REJECTED")


if __name__ == "__main__":
    unittest.main()
