"""Market contract checks with a mock A2A server and deterministic model replies."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
import market_buyer as mb


def task(state, text="", receipt=None):
    value = {"id": "trade-1", "status": {
        "state": "TASK_STATE_" + state, "message": {"parts": [{"text": text}]}}}
    if receipt is not None:
        value["artifacts"] = [{"name": "receipt", "parts": [{"data": receipt}]}]
    return value


class MarketBuyerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = Path(self.temp.name) / "memory.jsonl"
        self.shop = {"name": "Stable Name", "url": "https://new-url.test", "reviews": []}
        self.receipt = {"shop": "Stable Name", "item": "lamp", "price": 110.0,
                        "task": "trade-1", "sig_seller": "original-signature", "extra": {"keep": True}}
        self.bodies = []
        self.script = []
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.addCleanup(self.client.close)
        self.output = io.StringIO()
        ctx = redirect_stdout(self.output)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    def handle(self, request):
        self.assertEqual(request.headers["A2A-Version"], "1.0")
        if request.method == "GET":
            return httpx.Response(200, json={"supportedInterfaces": [{
                "protocolBinding": "JSONRPC", "protocolVersion": "1.0",
                "url": self.shop["url"] + "/rpc"}]})
        body = json.loads(request.content)
        self.bodies.append(body)
        method, response = self.script.pop(0)
        self.assertEqual(body["method"], method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": response})

    def trade(self, decisions):
        with patch.object(mb, "buyer_decision", side_effect=decisions):
            return mb.trade_and_record(self.shop, item="lamp", budget=120,
                                       memory_path=self.memory, reason="Trusted history",
                                       seconds=30, client=self.client)

    def test_receipt_preserved_with_signature_extra_fields_and_task_continuation(self):
        self.script = [("SendMessage", task("INPUT_REQUIRED", "asking 110")),
                       ("SendMessage", task("COMPLETED", receipt=self.receipt))]
        result = self.trade([{"action": "accept", "price": 110, "reason": "Affordable"}])
        self.assertEqual(result["receipt"], self.receipt)
        self.assertEqual(result["shop"], self.shop["url"])
        self.assertEqual(self.bodies[-1]["params"]["message"]["taskId"], "trade-1")
        record = mb.read_memory(self.memory)[0]
        self.assertEqual((record["name"], record["item"], record["price"], record["result"]),
                         ("Stable Name", "lamp", 110.0, "purchased"))

    def test_over_budget_offer_never_reaches_wire_and_failure_is_remembered(self):
        self.script = [("SendMessage", task("INPUT_REQUIRED", "asking 150")),
                       ("CancelTask", task("CANCELED"))]
        result = self.trade([{"action": "offer", "price": 121, "reason": "Invalid"}])
        self.assertEqual([r["method"] for r in self.bodies], ["SendMessage", "CancelTask"])
        self.assertIsNone(result["receipt"])
        self.assertTrue(result["review"])
        self.assertEqual(mb.read_memory(self.memory)[0]["result"], "error")

    def test_mismatched_receipt_is_remembered_across_url_changes(self):
        wrong = dict(self.receipt, price=119)
        self.script = [("SendMessage", task("INPUT_REQUIRED", "asking 110")),
                       ("SendMessage", task("COMPLETED", receipt=wrong))]
        result = self.trade([{"action": "accept", "price": 110, "reason": "Affordable"}])
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["stars"], 1)
        records = mb.read_memory(self.memory)
        self.assertEqual(records[0]["observed_receipt"], wrong)
        changed = dict(self.shop, url="https://another-url.test")
        evidence = mb.shop_evidence(changed, records)
        self.assertEqual(evidence["own_memory"]["receipt_mismatches"], 1)

    def test_wrong_item_and_task_are_rejected(self):
        for override in ({"item": "chair"}, {"task": "different-task"}):
            with self.subTest(override=override):
                self.script = [("SendMessage", task("INPUT_REQUIRED", "asking 110")),
                               ("SendMessage", task("COMPLETED", receipt=dict(self.receipt, **override)))]
                result = self.trade([{"action": "accept", "price": 110, "reason": "Affordable"}])
                self.assertIsNone(result["receipt"])
        self.assertEqual(len(mb.read_memory(self.memory)), 2)

    def test_walk_away_has_null_receipt_review_and_memory(self):
        self.script = [("SendMessage", task("INPUT_REQUIRED", "asking 300")),
                       ("CancelTask", task("CANCELED"))]
        result = self.trade([{"action": "walk_away", "reason": "Cannot agree within budget"}])
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["stars"], 3)
        self.assertTrue(result["review"])
        self.assertEqual(mb.read_memory(self.memory)[0]["result"], "no_deal")

    def test_evidence_is_bounded_without_forgetting_old_mismatch(self):
        shop = dict(self.shop, reviews=[{"stars": 1, "review": "x" * 1000, "round": 1}] * 10)
        records = [{"name": shop["name"], "result": "receipt_mismatch"}]
        records += [{"name": shop["name"], "item": "lamp", "price": 110, "result": "purchased"}] * 30
        evidence = mb.shop_evidence(shop, records)
        self.assertEqual(evidence["reviews"]["low_star_count"], 10)
        self.assertEqual(len(evidence["reviews"]["recent_newest_first"]), 3)
        self.assertEqual(len(evidence["reviews"]["recent_newest_first"][0]["review"]), 400)
        self.assertEqual(evidence["own_memory"]["receipt_mismatches"], 1)
        self.assertEqual(len(evidence["own_memory"]["recent_newest_first"]), 3)

    def test_selection_supplies_reviews_and_name_memory_to_harness(self):
        shop = dict(self.shop, reviews=[{"stars": 1, "review": "Changed the price", "round": 1}])
        records = [{"name": shop["name"], "result": "receipt_mismatch"}]

        def model(prompt, context, validate, **kwargs):
            self.assertEqual(prompt, "market_select.txt")
            evidence = context["candidates"][0]
            self.assertEqual(evidence["own_memory"]["receipt_mismatches"], 1)
            self.assertEqual(evidence["reviews"]["recent_newest_first"][0]["review"], "Changed the price")
            with self.assertRaises(mb.HarnessError):
                validate('{"name":"invented shop","reason":"not listed"}')
            return validate(json.dumps({"name": shop["name"], "reason": "Only candidate; risky"}))

        with patch.object(mb, "decide", side_effect=model):
            selected, reason = mb.choose_shop([shop], records, item="lamp", budget=120,
                                              deadline=time.monotonic() + 30)
        self.assertEqual(selected, shop)
        self.assertIn(reason, self.output.getvalue())

    def test_large_market_reduces_batches_without_dropping_winner_evidence(self):
        shops = [dict(self.shop, name=f"shop-{i}") for i in range(25)]
        sizes = []

        def model(prompt, context, validate, **kwargs):
            candidates = context["candidates"]
            sizes.append(len(candidates))
            self.assertIn("own_memory", candidates[0])
            return validate(json.dumps({"name": candidates[0]["name"], "reason": "Evidence compared"}))

        with patch.object(mb, "decide", side_effect=model):
            mb.choose_shop(shops, [], item="lamp", budget=120, deadline=time.monotonic() + 30)
        self.assertEqual(sizes, [12, 12, 1, 3])

    def test_cli_last_line_is_exact_result_even_after_progress_output(self):
        shops_path = Path(self.temp.name) / "shops.json"
        shops_path.write_text(json.dumps([self.shop]))
        expected = {"shop": self.shop["url"], "receipt": self.receipt, "stars": 5, "review": "Matched"}

        def selection(*args, **kwargs):
            print("SHOP SELECTION: evidence")
            return self.shop, "Evidence"

        with patch.object(mb, "choose_shop", side_effect=selection), \
                patch.object(mb, "trade_and_record", return_value=expected):
            code = mb.main(["--shops", str(shops_path), "--item", "lamp", "--budget", "120",
                            "--memory", str(self.memory)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.output.getvalue().splitlines()[-1]), expected)

    def test_empty_shop_list_fails_with_json_without_contacting_a_shop(self):
        shops_path = Path(self.temp.name) / "shops.json"
        shops_path.write_text("[]")
        with patch.object(mb, "choose_shop") as selection:
            code = mb.main(["--shops", str(shops_path), "--item", "lamp", "--budget", "120"])
        self.assertEqual(code, 1)
        selection.assert_not_called()
        self.assertIsNone(json.loads(self.output.getvalue().splitlines()[-1])["receipt"])


if __name__ == "__main__":
    unittest.main()
