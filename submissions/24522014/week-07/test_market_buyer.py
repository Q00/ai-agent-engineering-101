"""Check the student's memory rules and hostile receipt handling without a model."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

import buyer
import buyer_memory as memory
import market_buyer as market

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def shop(name, reviews=None, url=None):
    return {"name": name, "url": url or f"http://{name}.test", "reviews": reviews or []}


def observation(name="known", outcome="purchased", **changes):
    return {"version": 1, "timestamp": NOW.isoformat(), "shop_name": name,
            "item": "lamp", "budget": 120, "price": 110, "outcome": outcome, **changes}


class MemoryRules(unittest.TestCase):
    def choose(self, shops, records, **kwargs):
        return memory.choose_shop(shops, records, "lamp", 120, now=NOW, **kwargs)[0]

    def test_success_remembered_by_name_despite_url_change(self):
        chosen = self.choose([shop("aaa-new"), shop("known", url="http://changed.test")], [observation()])
        self.assertEqual(chosen["name"], "known")

    def test_unaffordable_past_price_is_not_current_offer_or_guarantee(self):
        chosen = self.choose([shop("aaa-new"), shop("known")], [observation(price=150)])
        self.assertEqual(chosen["name"], "aaa-new")

    def test_no_deal_does_not_block_reconsideration(self):
        chosen = self.choose([shop("known")], [observation(outcome="no_deal", budget=80)])
        self.assertEqual(chosen["name"], "known")

    def test_repeated_specific_public_complaints_lower_priority(self):
        reviews = [{"stars": 1, "review": text, "round": 1} for text in
                   ("Agreed 110, receipt says 135.", "Receipt price was higher than agreed.")]
        chosen = self.choose([shop("known", reviews), shop("new")], [observation()])
        self.assertEqual(chosen["name"], "new")

    def test_single_duplicate_and_unrelated_bad_reviews_are_not_repeated_fraud(self):
        bad = {"stars": 1, "review": "Receipt price was higher.", "round": 1}
        self.assertFalse(memory.repeated_price_complaints([bad, bad]))
        unrelated = [{"stars": 1, "review": "Receipt matched agreed price but delivery was slow.", "round": 1},
                     {"stars": 1, "review": "Too expensive.", "round": 1}]
        self.assertFalse(memory.repeated_price_complaints(unrelated))

    def test_repeated_outages_expire_and_one_outage_does_not_penalize(self):
        shops = [shop("aaa"), shop("zzz")]
        records = [observation("aaa", "transport_error")]
        self.assertEqual(self.choose(shops, records)["name"], "aaa")
        records.append(observation("aaa", "transport_error"))
        self.assertEqual(self.choose(shops, records)["name"], "zzz")
        expired = [{**r, "timestamp": (NOW - timedelta(hours=2)).isoformat()} for r in records]
        self.assertEqual(self.choose(shops, expired)["name"], "aaa")

    def test_direct_mismatch_is_not_forgotten_by_window(self):
        records = [observation(outcome="receipt_mismatch")]
        records.extend(observation(outcome="no_deal") for _ in range(21))
        self.assertEqual(self.choose([shop("known"), shop("new")], records)["name"], "new")
        self.assertIsNone(self.choose([shop("known")], records))

    def test_append_only_round_trip_and_corruption_is_visible(self):
        path = buyer.ROOT / "logs/lab1-step2/unit-memory" / (uuid4().hex + ".jsonl")
        memory.remember(path, observation())
        before = path.read_bytes()
        memory.remember(path, observation(outcome="transport_error"))
        self.assertTrue(path.read_bytes().startswith(before))
        self.assertEqual(len(memory.load_memory(path)), 2)
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{broken\n')
        with self.assertRaises(ValueError):
            memory.load_memory(path)


class TradeRules(unittest.TestCase):
    def setUp(self):
        self.requests = []
        directory = buyer.ROOT / "logs/lab1-step2/unit-runs" / uuid4().hex
        (directory / "prompts").mkdir(parents=True)
        (directory / "prompts/buyer.txt").write_bytes((buyer.ROOT / "prompts/buyer.txt").read_bytes())
        root_patch = patch.object(buyer, "ROOT", directory)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def transport(self, receipt_price=110, item="lamp"):
        def respond(request):
            self.requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"supportedInterfaces": [
                    {"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "http://fixture.test/rpc"}]})
            body = json.loads(request.content)
            message = body.get("params", {}).get("message", {})
            state, artifacts = "TASK_STATE_INPUT_REQUIRED", []
            if body["method"] == "CancelTask":
                state = "TASK_STATE_CANCELED"
            elif message.get("taskId"):
                state = "TASK_STATE_COMPLETED"
                artifacts = [{"parts": [{"data": {"shop": "Fixture", "item": item,
                             "price": receipt_price, "task": "task-a", "sig_seller": "fixture-only"}}]}]
            task = {"id": "task-a", "contextId": "context-a", "status": {"state": state,
                    "message": {"parts": [{"text": "asking 110"}]}}, "artifacts": artifacts}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": task})
        return httpx.MockTransport(respond)

    @patch("buyer.run_harness", return_value={"action": "offer", "price": 110, "reason": "within budget"})
    def test_success_preserves_receipt_and_protocol(self, harness):
        result, record = market.trade(shop("fixture"), "lamp", 120, transport=self.transport())
        self.assertEqual(record["outcome"], "purchased")
        self.assertEqual(result["stars"], 5)
        self.assertEqual(result["receipt"], record["receipt"])
        self.assertEqual(result["receipt"]["sig_seller"], "fixture-only")
        self.assertTrue(all(r.headers["A2A-Version"] == "1.0" for r in self.requests))

    @patch("buyer.run_harness", return_value={"action": "offer", "price": 110, "reason": "within budget"})
    def test_mismatch_within_budget_is_not_reported_as_success(self, harness):
        result, record = market.trade(shop("fixture"), "lamp", 120, transport=self.transport(receipt_price=115))
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["stars"], 1)
        self.assertEqual(record["outcome"], "receipt_mismatch")
        self.assertEqual(record["receipt"]["price"], 115)
        self.assertEqual(record["agreed_price"], 110)

    @patch("buyer.run_harness", return_value={"action": "offer", "price": 121, "reason": "invalid"})
    def test_model_cannot_send_over_budget_offer(self, harness):
        result, record = market.trade(shop("fixture"), "lamp", 120, transport=self.transport())
        self.assertIsNone(result["receipt"])
        self.assertEqual(len([r for r in self.requests if r.method == "POST"]), 1)
        self.assertEqual(record["outcome"], "agent_error")

    @patch("buyer.run_harness", return_value={"action": "walk_away", "price": None, "reason": "cannot agree"})
    def test_no_deal_is_neutral_not_dishonesty(self, harness):
        result, record = market.trade(shop("fixture"), "lamp", 120, transport=self.transport())
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["stars"], 3)
        self.assertEqual(record["outcome"], "no_deal")

    def test_connection_failure_is_temporary_evidence(self):
        def fail(request):
            raise httpx.ConnectError("fixture outage", request=request)
        result, record = market.trade(shop("fixture"), "lamp", 120, transport=httpx.MockTransport(fail))
        self.assertEqual(record["outcome"], "transport_error")
        self.assertEqual(result["stars"], 3)

    def test_temporary_server_failure_is_not_a_buyer_or_honesty_failure(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(503, text="Temporary outage"))
        result, record = market.trade(shop("fixture"), "lamp", 120, transport=transport)
        self.assertEqual(record["outcome"], "transport_error")
        self.assertEqual(result["stars"], 3)


if __name__ == "__main__":
    unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout, verbosity=2))
