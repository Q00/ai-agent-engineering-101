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

    def test_rotation_uses_least_recent_safe_shop_every_fifth_attempt(self):
        records = [observation("known") for _ in range(4)]
        selected, reason, _ = memory.choose_shop([shop("known"), shop("new")], records,
                                                  "lamp", 120, now=NOW)
        self.assertEqual(selected["name"], "new")
        self.assertIn("Selection=rotation", reason)
        records.append(observation("new", "no_deal"))
        self.assertEqual(self.choose([shop("known"), shop("new")], records)["name"], "known")

    def test_rotation_excludes_mismatch_and_avoids_repeated_outages(self):
        records = [observation("blocked", "receipt_mismatch"),
                   observation("unavailable", "transport_error"),
                   observation("unavailable", "transport_error"), observation("known")]
        self.assertEqual(self.choose([shop("blocked"), shop("unavailable"), shop("known")],
                                     records)["name"], "known")

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


class FastQuoteTrades(unittest.TestCase):
    """market.trade() over mock HTTP with the rule path on: wording decides what is actually sent."""

    def setUp(self):
        self.posts = []
        directory = buyer.ROOT / "logs/lab1-step2/unit-runs" / uuid4().hex
        (directory / "prompts").mkdir(parents=True)
        root_patch = patch.object(buyer, "ROOT", directory)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        prompt_patch = patch.object(market, "ROOT", directory)
        prompt_patch.start()
        self.addCleanup(prompt_patch.stop)
        (directory / "prompts/buyer-r3.txt").write_text("fixture prompt", encoding="utf-8")

    def transport(self, quote, *, receipt_price=None, after_offer=None, offer_state="TASK_STATE_COMPLETED",
                  metadata=None, data=None, nested=False):
        def respond(request):
            if request.method == "GET":
                return httpx.Response(200, json={"supportedInterfaces": [
                    {"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "http://fixture.test/rpc"}]})
            body = json.loads(request.content)
            self.posts.append(body)
            message = body.get("params", {}).get("message", {})
            state, text, artifacts = "TASK_STATE_INPUT_REQUIRED", quote, []
            if body["method"] == "CancelTask":
                state = "TASK_STATE_CANCELED"
            elif message.get("taskId"):
                offered = int(message["parts"][0]["text"])
                if after_offer is not None:
                    text = after_offer
                else:
                    state = offer_state
                    if state == "TASK_STATE_COMPLETED":
                        artifacts = [{"parts": [{"data": {"shop": "Fixture", "item": "lamp",
                                     "price": receipt_price or offered, "task": "task-a", "sig_seller": "fixture-only"}}]}]
            parts = [{"text": text}]
            if data is not None:
                parts.append({"data": data, "mediaType": "application/json"})
            reply = {"parts": parts}
            if metadata is not None:
                reply["metadata"] = metadata
            task = {"id": "task-a", "contextId": "context-a", "status": {"state": state, "message": reply},
                    "artifacts": artifacts}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                             "result": {"task": task} if nested else task})
        return httpx.MockTransport(respond)

    def trade(self, transport, gate=lambda: False, budget=300000, accept=None):
        return market.trade(shop("fixture"), "lamp", budget, transport=transport, fast_rules=True,
                            model_gate=gate, max_turns=3, accept_conditions=accept)

    def test_bidet_quote_with_grade_only_in_nested_metadata_sends_no_offer(self):
        quote = "Bidet DDS-S550R: asking 178698. Send your offer as a number.\nReply with a number, or ok to take the ask: 178698"
        gate_calls = []
        with patch("buyer.run_harness") as harness:
            result, record = self.trade(
                self.transport(quote, nested=True, data={"ask": 178698.0, "sku": "G0aebcd94"},
                               metadata={"ask": 178698.0, "warranty_hours": 6.0, "certificate": True, "grade": "C"}),
                gate=lambda: gate_calls.append(1) or "reserved", accept={"cert": True, "grades": ["A", "B"]})
            harness.assert_not_called()
        self.assertEqual(self.offers(), [])
        self.assertEqual(gate_calls, [])
        self.assertIsNone(result["receipt"])
        self.assertEqual(record["failure_reason"], "quote_grade_unacceptable")
        saved = self.decision_file(record)
        self.assertEqual((saved["source"], saved["reason_code"], saved["parser_reason"], saved["action"]),
                         ("quote_filter", "quote_grade_unacceptable", "rule_offer", "walk_away"))
        self.assertEqual((saved["quote_check"]["basis"], saved["quote_check"]["grade"]), ("structured", "C"))
        self.assertEqual(self.posts[-1]["method"], "CancelTask")

    def test_shelf_life_from_text_sends_no_offer_on_a_food_order(self):
        _, record = self.trade(self.transport("Kimchi, 100 h of shelf life left. Asking 110."),
                               accept={"cert": True, "grades": ["A", "B"], "min_hours_left": 128.0})
        self.assertEqual(self.offers(), [])
        self.assertEqual(record["failure_reason"], "quote_shelf_life_short")
        self.assertEqual(record["decision_trail"][0]["quote_check"]["basis"], "text")

    def test_acceptable_or_silent_quotes_still_offer_and_record_the_check(self):
        accept = {"cert": True, "grades": ["A", "B"], "min_hours_left": 128.0}
        for quote, metadata in (("Asking 110.", None), ("grade B, 200 h of shelf life left. Asking 110.", None),
                                ("Asking 110. A fair amount of shelf life left.", {"grade": "A"}),
                                ("at least 38 hours of shelf life left. Asking 110.", None)):
            with self.subTest(quote=quote):
                self.posts.clear()
                _, record = self.trade(self.transport(quote, metadata=metadata), accept=accept)
                self.assertEqual(self.offers(), [110])
                check = record["decision_trail"][0]["quote_check"]
                self.assertIsNone(check["reason_code"])

    def test_a_passing_claim_does_not_skip_later_safeguards_or_mark_payment(self):
        _, record = self.trade(self.transport("Grade A. Asking 110."), accept={"cert": True, "grades": ["A", "B"]})
        self.assertEqual(record["verification"], "local_receipt_consistent")
        self.assertEqual(record["decision_trail"][0]["source"], "deterministic")

    def test_filter_also_precedes_a_model_reservation_and_counteroffers(self):
        gate_calls = []
        with patch("buyer.run_harness") as harness:
            _, record = self.trade(self.transport("grade C. asking 300001."), accept={"grades": ["A", "B"]},
                                   gate=lambda: gate_calls.append(1) or "reserved")
            harness.assert_not_called()
        self.assertEqual(gate_calls, [])
        self.assertEqual(record["failure_reason"], "quote_grade_unacceptable")
        self.assertEqual(record["decision_trail"][0]["parser_reason"], "over_budget")
        self.posts.clear()
        _, record = self.trade(self.transport("asking 110.", after_offer="grade C. Counteroffer: 105."),
                               accept={"grades": ["A", "B"]})
        self.assertEqual(self.offers(), [110])
        self.assertEqual(record["failure_reason"], "quote_grade_unacceptable")

    def test_conflict_is_recorded_even_when_the_offer_goes_out(self):
        _, record = self.trade(self.transport("grade A. Asking 110.", metadata={"grade": "B"}),
                               accept={"grades": ["A", "B"]})
        self.assertEqual(self.offers(), [110])
        self.assertEqual(record["decision_trail"][0]["quote_check"]["conflicts"], ["grade"])

    def test_no_accept_conditions_means_no_check(self):
        _, record = self.trade(self.transport("grade C. Asking 110."))
        self.assertEqual(self.offers(), [110])
        self.assertNotIn("quote_check", record["decision_trail"][0])

    def offers(self):
        return [int(p["params"]["message"]["parts"][0]["text"]) for p in self.posts
                if p["method"] == "SendMessage" and p["params"]["message"].get("taskId")]

    def decision_file(self, record, turn=1):
        return json.loads((Path(record["buyer_log_dir"]) / f"decision-{turn:02d}.json").read_text(encoding="utf-8"))

    def test_reported_quotes_are_offered_by_rule_without_the_model(self):
        gate_calls = []
        for quote, price in (("asking 110.", 110), ("Counteroffer: 234659.", 234659),
                             ("Our asking price is 42630.", 42630)):
            with self.subTest(quote=quote), patch("buyer.run_harness") as harness:
                self.posts.clear()
                result, record = self.trade(self.transport(quote), gate=lambda: gate_calls.append(1) or False)
                harness.assert_not_called()
                self.assertEqual(self.offers(), [price])
                self.assertEqual(record["outcome"], "purchased")
                self.assertEqual(result["receipt"]["price"], price)
                self.assertIsNone(record["failure_reason"])
                saved = self.decision_file(record)
                self.assertEqual((saved["source"], saved["reason_code"], saved["price"]),
                                 ("deterministic", "rule_offer", price))
                self.assertEqual(record["decision_trail"], [
                    {k: saved[k] for k in ("turn", "source", "reason_code", "parser_reason", "action", "price")}])
        self.assertEqual(gate_calls, [])

    def test_receipt_is_recorded_as_unconfirmed_not_paid(self):
        _, record = self.trade(self.transport("asking 110."))
        self.assertEqual(record["verification"], "local_receipt_consistent")
        self.assertNotIn("settlement_ledger", record)

    def test_unclear_quotes_send_no_offer_and_name_the_quota_reason(self):
        for quote, parser_reason in (("asking 100.50", "malformed_price"), ("asking 1,00", "malformed_price"),
                                     ("asking 100. Counteroffer: 90.", "conflicting_prices"),
                                     ("list price 110.", "no_labeled_price"), ("Foo 110. Send a number.", "no_labeled_price"),
                                     ("asking 300001.", "over_budget")):
            with self.subTest(quote=quote), patch("buyer.run_harness") as harness:
                self.posts.clear()
                result, record = self.trade(self.transport(quote), gate=lambda: "model_quota_hour")
                harness.assert_not_called()
                self.assertEqual(self.offers(), [])
                self.assertIsNone(result["receipt"])
                self.assertEqual(record["outcome"], "no_deal")
                self.assertEqual(record["failure_reason"], "model_quota_hour")
                saved = self.decision_file(record)
                self.assertEqual((saved["source"], saved["reason_code"], saved["parser_reason"], saved["action"]),
                                 ("model_gate", "model_quota_hour", parser_reason, "walk_away"))
                self.assertEqual(self.posts[-1]["method"], "CancelTask")

    def test_plain_false_gate_and_missing_gate_are_distinguished(self):
        with patch("buyer.run_harness") as harness:
            _, denied = self.trade(self.transport("Our price is 90."), gate=lambda: False)
            _, missing = self.trade(self.transport("Our price is 90."), gate=None)
            harness.assert_not_called()
        self.assertEqual(denied["failure_reason"], "model_gate_denied")
        self.assertEqual(missing["failure_reason"], "model_gate_missing")

    def test_repeated_counteroffer_walks_away_by_rule(self):
        with patch("buyer.run_harness") as harness:
            _, record = self.trade(self.transport("asking 110.", after_offer="Counteroffer: 110."))
            harness.assert_not_called()
        self.assertEqual(self.offers(), [110])
        self.assertEqual(record["failure_reason"], "rule_repeated_offer")
        self.assertEqual([d["reason_code"] for d in record["decision_trail"]], ["rule_offer", "rule_repeated_offer"])

    def test_seller_rejection_after_a_rule_offer_is_not_a_quota_failure(self):
        _, record = self.trade(self.transport("asking 110.", offer_state="TASK_STATE_REJECTED"))
        self.assertEqual(record["outcome"], "no_deal")
        self.assertEqual(record["negotiation_outcome"], "seller_rejected")
        self.assertEqual(record["failure_reason"], "seller_rejected")
        self.assertEqual(self.offers(), [110])

    def test_model_decisions_and_failures_are_labeled_and_get_the_labeled_ask(self):
        # The last number here is an offer count; it must not reach the model as the price.
        quote = "asking 300001. This negotiation closes after 6 offers."
        with patch("buyer.run_harness", return_value={"action": "walk_away", "price": None, "reason": "too high"}) as harness:
            _, record = self.trade(self.transport(quote), gate=lambda: "reserved")
        harness.assert_called_once()
        self.assertEqual(record["failure_reason"], "model_walk_away")
        saved = self.decision_file(record)
        self.assertEqual((saved["source"], saved["parser_reason"], saved["context"]["current_ask"]),
                         ("model", "over_budget", 300001))
        with patch("buyer.run_harness", return_value={"action": "offer", "price": 300001, "reason": "over"}):
            _, failed = self.trade(self.transport(quote), gate=lambda: "reserved")
        self.assertEqual(failed["outcome"], "agent_error")
        self.assertEqual(failed["failure_reason"], "model_failed")
        self.assertEqual(self.decision_file(failed)["status"], "failed")

    def test_unlabeled_quote_reaches_the_model_without_a_guessed_ask(self):
        with patch("buyer.run_harness", return_value={"action": "walk_away", "price": None, "reason": "unclear"}):
            _, record = self.trade(self.transport("Foo 700g. Our price is 41972. Closes after 6 offers."),
                                   gate=lambda: "reserved")
        self.assertIsNone(self.decision_file(record)["context"]["current_ask"])

    def test_message_without_a_positive_number_is_logged_before_failing(self):
        _, record = self.trade(self.transport("Send your offer."), gate=lambda: "reserved")
        self.assertEqual(record["outcome"], "agent_error")
        self.assertEqual(record["failure_reason"], "no_positive_number")
        self.assertEqual(self.decision_file(record)["source"], "parser")

    def test_gate_is_asked_at_most_once_per_trade(self):
        calls = []
        def gate():
            calls.append(1)
            return "reserved"
        replies = iter([{"action": "offer", "price": 90, "reason": "try"},
                        {"action": "walk_away", "price": None, "reason": "stop"}])
        with patch("buyer.run_harness", side_effect=lambda *a, **k: next(replies)):
            self.trade(self.transport("Our price is 95.", after_offer="Our price is 94."), gate=gate)
        self.assertEqual(calls, [1])

    def test_session_and_outcome_files_carry_the_trail_and_no_secrets(self):
        _, record = self.trade(self.transport("asking 110."))
        directory = Path(record["buyer_log_dir"])
        for name in ("session.json", "outcome.json"):
            saved = json.loads((directory / name).read_text(encoding="utf-8"))
            self.assertEqual(saved["decisions"][0]["reason_code"], "rule_offer")
        for name in ("session.json", "outcome.json", "decision-01.json"):
            text = (directory / name).read_text(encoding="utf-8").lower()
            for marker in ("api_key", "authorization", "claim", "private key", "begin "):
                self.assertNotIn(marker, text)


class ReservationReasons(unittest.TestCase):
    def test_each_denial_has_its_own_code_and_reserve_model_stays_boolean(self):
        import buyer_support
        from tempfile import TemporaryDirectory
        settings = {"max_model_orders_per_hour": 2, "max_model_orders_per_day": 3}
        with TemporaryDirectory() as md, patch.object(buyer_support, "config", return_value=settings):
            now = 1793750400.0
            self.assertEqual(buyer_support.model_reservation(md, "a", now), "reserved")
            self.assertEqual(buyer_support.model_reservation(md, "a", now), "model_order_already_reserved")
            self.assertTrue(buyer_support.reserve_model(md, "b", now))
            self.assertEqual(buyer_support.model_reservation(md, "c", now), "model_quota_hour")
            self.assertFalse(buyer_support.reserve_model(md, "c", now))
            self.assertEqual(buyer_support.model_reservation(md, "c", now + 3601), "reserved")
            self.assertEqual(buyer_support.model_reservation(md, "d", now + 7300), "model_quota_day")


if __name__ == "__main__":
    unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout, verbosity=2))
