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


class TradeFixture(unittest.TestCase):
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

    def offers(self):
        return [int(p["params"]["message"]["parts"][0]["text"]) for p in self.posts
                if p["method"] == "SendMessage" and p["params"]["message"].get("taskId")]

    def decision_file(self, record, turn=1):
        return json.loads((Path(record["buyer_log_dir"]) / f"decision-{turn:02d}.json").read_text(encoding="utf-8"))


class FastQuoteTrades(TradeFixture):
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


class CounterOfferTrades(TradeFixture):
    """Over-budget counteroffer over mock HTTP. Cap 100, list price 200 unless a test says otherwise."""
    ON = {"enabled": True, "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.10}
    OFF = {"enabled": False, "max_ask_per_cap": None, "min_surplus_per_list": None}

    def setUp(self):
        super().setUp()
        self.gate_calls = []

    def gate(self):
        self.gate_calls.append(1)
        return "model_quota_hour"

    def counter_trade(self, transport, *, settings=None, list_price=200, accept=None, cap=100, gate=None):
        return market.trade(shop("fixture"), "lamp", cap, transport=transport, fast_rules=True, max_turns=3,
                            model_gate=gate or self.gate, accept_conditions=accept,
                            over_budget_counter=self.ON if settings is None else settings,
                            catalog_list_price=list_price)

    def test_exact_ratio_boundary_counters_at_the_cap_without_any_model(self):
        with patch("buyer.run_harness") as harness:
            result, record = self.counter_trade(self.transport("asking 118."))
            harness.assert_not_called()
        self.assertEqual(self.gate_calls, [])
        self.assertEqual(self.offers(), [100])
        entry = record["decision_trail"][0]
        self.assertEqual((entry["source"], entry["reason_code"], entry["parser_reason"], entry["price"]),
                         ("deterministic", "rule_counter_offer", "over_budget", 100))
        check = entry["counter_check"]
        self.assertEqual((check["ask"], check["cap"], check["list_price"], check["offer"], check["ask_per_cap"]),
                         (118, 100, 200, 100, 1.18))
        self.assertEqual(self.decision_file(record)["counter_check"]["reason_code"], "rule_counter_offer")

    def test_accepted_counter_reaches_a_receipt_and_the_settled_path_is_counted_apart(self):
        from settlement_memory import reconcile
        import buyer_surplus
        result, record = self.counter_trade(self.transport("asking 118."))
        self.assertEqual(record["outcome"], "purchased")
        self.assertEqual((result["receipt"]["price"], record["agreed_price"]), (100, 100))
        record.update(order="o-1", url_at_time="http://fixture.test", price=100)
        self.assertEqual(record["verification"], "local_receipt_consistent")
        self.assertEqual(reconcile([record], {})[0]["outcome"], "awaiting_payment")
        settled = reconcile([record], {"o-1": {"shop": "http://fixture.test", "item": "lamp", "price": 100,
                                               "paid": True, "ledger": 5}})[0]
        self.assertEqual((settled["outcome"], settled["verification"]), ("purchased", "market_settled"))
        self.assertEqual(buyer_surplus.offer_path(buyer.ROOT, record), "deterministic:rule_counter_offer")

    def test_exclusions_fall_through_to_the_gate_with_their_reason(self):
        for quote, list_price, cap, code in (("asking 119.", 200, 100, "counter_ratio_exceeded"),
                                             ("asking 100.", 100, 90, "rule_counter_offer"),
                                             ("asking 100.", 100, 91, "counter_surplus_too_low"),
                                             ("asking 110.", None, 100, "counter_no_list_price")):
            with self.subTest(quote=quote, cap=cap), patch("buyer.run_harness") as harness:
                self.posts.clear()
                self.gate_calls.clear()
                _, record = self.counter_trade(self.transport(quote), list_price=list_price, cap=cap)
                harness.assert_not_called()
                check = record["decision_trail"][0]["counter_check"]
                self.assertEqual(check["reason_code"], code)
                if code == "rule_counter_offer":
                    self.assertEqual(self.offers(), [cap])
                    self.assertEqual(self.gate_calls, [])
                else:
                    self.assertEqual(self.offers(), [])
                    self.assertEqual(self.gate_calls, [1])
                    self.assertEqual(record["failure_reason"], "model_quota_hour")

    def test_quote_filter_comes_first(self):
        accept = {"cert": True, "grades": ["A", "B"]}
        _, record = self.counter_trade(self.transport("asking 110.", metadata={"grade": "C"}), accept=accept)
        self.assertEqual(self.offers(), [])
        self.assertEqual(self.gate_calls, [])
        entry = record["decision_trail"][0]
        self.assertEqual((entry["source"], entry["reason_code"]), ("quote_filter", "quote_grade_unacceptable"))
        self.assertNotIn("counter_check", entry)

    def test_ambiguous_or_within_cap_quotes_are_never_countered(self):
        for quote, offers, reason in (("asking 110. Counteroffer: 105.", [], "model_quota_hour"),
                                      ("asking 110. Now 105.", [], "model_quota_hour"), ("Our price is 110.", [], "model_quota_hour"),
                                      ("asking 110.50", [], "model_quota_hour")):
            with self.subTest(quote=quote):
                self.posts.clear()
                _, record = self.counter_trade(self.transport(quote))
                self.assertEqual(self.offers(), offers)
                self.assertEqual(record["failure_reason"], reason)
                self.assertNotIn("counter_check", record["decision_trail"][0])
        self.posts.clear()
        _, record = self.counter_trade(self.transport("asking 90."))
        self.assertEqual(self.offers(), [90])
        self.assertEqual(record["decision_trail"][0]["reason_code"], "rule_offer")

    def test_second_over_budget_reply_cannot_counter_again(self):
        with patch("buyer.run_harness") as harness:
            _, record = self.counter_trade(self.transport("asking 110.", after_offer="Counteroffer: 105."))
            harness.assert_not_called()
        self.assertEqual(self.offers(), [100])
        self.assertEqual(self.gate_calls, [1])
        reasons = [(e["source"], e["reason_code"], e["counter_check"]["reason_code"]) for e in record["decision_trail"]]
        self.assertEqual(reasons, [("deterministic", "rule_counter_offer", "rule_counter_offer"),
                                   ("model_gate", "model_quota_hour", "counter_already_used")])
        self.assertEqual(record["failure_reason"], "model_quota_hour")

    def test_seller_coming_down_after_the_counter_takes_the_existing_rule(self):
        transport = self.transport("asking 110.", after_offer="Counteroffer: 98.")
        with patch("buyer.run_harness") as harness:
            _, record = self.counter_trade(transport)
            harness.assert_not_called()
        self.assertEqual(self.offers()[:2], [100, 98])
        self.assertEqual([e["reason_code"] for e in record["decision_trail"]][:2], ["rule_counter_offer", "rule_offer"])
        self.assertNotIn("counter_check", record["decision_trail"][1])

    def test_a_rule_offer_at_the_cap_is_not_repeated_as_a_counter(self):
        _, record = self.counter_trade(self.transport("asking 100.", after_offer="Counteroffer: 105."))
        self.assertEqual(self.offers(), [100])
        second = record["decision_trail"][1]
        self.assertEqual((second["source"], second["counter_check"]["reason_code"]),
                         ("model_gate", "counter_repeated_price"))

    def test_seller_rejecting_the_counter_is_a_plain_no_deal(self):
        _, record = self.counter_trade(self.transport("asking 110.", offer_state="TASK_STATE_REJECTED"))
        self.assertEqual(record["outcome"], "no_deal")
        self.assertEqual(record["failure_reason"], "seller_rejected")
        self.assertEqual(self.gate_calls, [])

    def test_disabled_behaves_exactly_like_today(self):
        scenarios = (("asking 110.", {}), ("asking 119.", {}), ("asking 110.50", {}), ("asking 90.", {}),
                     ("asking 110.", {"after_offer": "Counteroffer: 105."}), ("Our price is 90.", {}))
        for gate_answer in ("model_quota_hour", "reserved"):
            for quote, options in scenarios:
                with self.subTest(quote=quote, gate=gate_answer), \
                        patch("buyer.run_harness", return_value={"action": "walk_away", "price": None, "reason": "stop"}) as harness:
                    self.posts.clear()
                    today, record_today = self.trade(self.transport(quote, **options), gate=lambda: gate_answer, budget=100)
                    posts_today, calls_today = list(self.offers()), harness.call_count
                    self.posts.clear()
                    harness.reset_mock()
                    off, record_off = self.counter_trade(self.transport(quote, **options), settings=self.OFF,
                                                         gate=lambda: gate_answer)
                    self.assertEqual(self.offers(), posts_today)
                    self.assertEqual(harness.call_count, calls_today)
                    self.assertEqual(off["receipt"], today["receipt"])
                    self.assertEqual(off["stars"], today["stars"])
                    self.assertEqual(record_off["outcome"], record_today["outcome"])
                    self.assertEqual(record_off["failure_reason"], record_today["failure_reason"])
                    plain = lambda trail: [{k: v for k, v in e.items() if k != "counter_check"} for e in trail]
                    self.assertEqual(plain(record_off["decision_trail"]), plain(record_today["decision_trail"]))
                    for entry in record_off["decision_trail"]:
                        if "counter_check" in entry:
                            self.assertEqual(entry["counter_check"]["reason_code"], "counter_disabled")

    def test_no_settings_means_no_counter_and_no_new_field(self):
        _, record = self.trade(self.transport("asking 300001."), gate=lambda: "model_quota_hour")
        self.assertEqual(self.offers(), [])
        self.assertNotIn("counter_check", record["decision_trail"][0])

    def test_counter_never_runs_outside_the_rule_path(self):
        with patch("buyer.run_harness", return_value={"action": "walk_away", "price": None, "reason": "stop"}):
            _, record = market.trade(shop("fixture"), "lamp", 100, transport=self.transport("asking 110."),
                                     over_budget_counter=self.ON, catalog_list_price=200)
        self.assertEqual(self.offers(), [])
        self.assertEqual(record["decision_trail"][0]["source"], "model")

    def test_a_real_reservation_is_not_consumed_by_a_counter(self):
        import buyer_support
        from tempfile import TemporaryDirectory
        settings = {"max_model_orders_per_hour": 2, "max_model_orders_per_day": 24}
        with TemporaryDirectory() as md, patch.object(buyer_support, "config", return_value=settings):
            self.counter_trade(self.transport("asking 110."),
                               gate=lambda: buyer_support.model_reservation(md, "o-1", 1793750400.0))
            self.assertFalse((Path(md) / "buyer-support-state.json").exists())
            self.assertEqual(buyer_support.model_reservation(md, "o-1", 1793750400.0), "reserved")


class MainWiring(unittest.TestCase):
    """main() hands the configured settings and our catalog's list price to trade, never the seller's."""

    def run_main(self, *, fast_rules=True, catalog=None):
        from tempfile import TemporaryDirectory
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        order = {"order": "o-1", "item": "cat", "budget": 300, "round": 3, "kind": "goods", "acceptable": ["k"]}
        (root / "order.json").write_text(json.dumps(order), encoding="utf-8")
        (root / "catalog.json").write_text(json.dumps(catalog if catalog is not None else {"k": {"list_price": 200}}), encoding="utf-8")
        (root / "shops.json").write_text("{}", encoding="utf-8")
        shop_row = {"name": "s", "url": "http://s.test", "offers": [{"sku": "k", "qty": 1}], "reviews": []}
        decision = {"max_turns": 3, "fast_rules": fast_rules, "status": "reserved"}
        argv = ["market_buyer.py", "--shops", str(root / "shops.json"), "--item", "cat", "--budget", "300",
                "--order", str(root / "order.json"), "--memory", str(root / "memory.jsonl")]
        with patch.object(sys, "argv", argv), patch.object(market, "ROOT", root), \
                patch.object(market, "load_shops", return_value=[shop_row]), patch.object(market, "fresh", return_value={}), \
                patch.object(market, "prepare_support", return_value=(shop_row, "k", 100, "why", [], decision)), \
                patch.object(market, "trade", return_value=({"shop": "", "receipt": None}, {"outcome": "no_deal"})) as trade, \
                patch("sys.stdout"):
            market.main()
        return trade.call_args.kwargs

    def test_settings_and_catalog_price_are_passed(self):
        marker = {"enabled": False, "max_ask_per_cap": None, "min_surplus_per_list": None}
        with patch("buyer_counter.load_settings", return_value=marker) as load:
            kwargs = self.run_main()
        load.assert_called_once()
        self.assertEqual(kwargs["over_budget_counter"], marker)
        self.assertEqual(kwargs["catalog_list_price"], 200)
        self.assertTrue(kwargs["fast_rules"])

    def test_sku_missing_from_our_catalog_gives_no_list_price(self):
        self.assertIsNone(self.run_main(catalog={})["catalog_list_price"])

    def test_without_the_rule_path_no_counter_settings_are_passed(self):
        self.assertNotIn("over_budget_counter", self.run_main(fast_rules=False))


class ModelGateSwitch(TradeFixture):
    """model_gate_enabled=false opens model_reservation only; order of paths and every other check stay."""
    LIMITS = {"max_model_orders_per_hour": 2, "max_model_orders_per_day": 3}

    def support_dir(self, flag="absent"):
        import buyer_support
        from tempfile import TemporaryDirectory
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        content = {"mode": "balanced"}
        if flag != "absent":
            content["model_gate_enabled"] = flag
        (root / "buyer_support.json").write_text(json.dumps(content), encoding="utf-8")
        for patcher in (patch.object(buyer_support, "ROOT", root),
                        patch.object(buyer_support, "config", return_value=self.LIMITS)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return root, buyer_support

    def test_only_an_explicit_false_turns_the_gate_off(self):
        for flag, enabled in (("absent", True), (True, True), (False, False), ("false", True), (0, True), (None, True)):
            with self.subTest(flag=flag):
                _, support = self.support_dir(flag)
                self.assertEqual(support.model_gate_enabled(), enabled)

    def test_unreadable_settings_keep_the_gate(self):
        root, support = self.support_dir(False)
        (root / "buyer_support.json").write_text("{broken", encoding="utf-8")
        self.assertTrue(support.model_gate_enabled())
        (root / "buyer_support.json").write_text("[]", encoding="utf-8")
        self.assertTrue(support.model_gate_enabled())
        (root / "buyer_support.json").unlink()
        self.assertTrue(support.model_gate_enabled())

    def test_gate_on_still_refuses_with_each_reason(self):
        root, support = self.support_dir(True)
        now = 1793750400.0
        self.assertEqual(support.model_reservation(root, "a", now), "reserved")
        self.assertEqual(support.model_reservation(root, "a", now), "model_order_already_reserved")
        self.assertEqual(support.model_reservation(root, "b", now), "reserved")
        self.assertEqual(support.model_reservation(root, "c", now), "model_quota_hour")
        self.assertEqual(support.model_reservation(root, "c", now + 3601), "reserved")
        self.assertEqual(support.model_reservation(root, "d", now + 7300), "model_quota_day")
        with patch("strategy_controller.acquire", return_value=None):
            self.assertEqual(support.model_reservation(root, "e", now), "model_gate_lock_busy")

    def test_gate_off_opens_every_refusal_and_keeps_a_usage_record(self):
        root, support = self.support_dir(False)
        now = 1793750400.0
        answers = [support.model_reservation(root, name, now) for name in ("a", "a", "b", "c", "d", "e", "f")]
        self.assertEqual(set(answers), {"gate_disabled"})
        with patch("strategy_controller.acquire", return_value=None):
            self.assertEqual(support.model_reservation(root, "busy", now), "gate_disabled")
        state = json.loads((root / "buyer-support-state.json").read_text(encoding="utf-8"))
        self.assertEqual(len(state["ungated_model_orders"]), 7)
        self.assertEqual(state.get("model_orders", []), [])

    def test_bookkeeping_failures_never_stop_the_model(self):
        import strategy_controller
        root, support = self.support_dir(False)
        real_release = strategy_controller.release

        def release_then_fail(handle):
            real_release(handle)
            raise OSError("unlock")
        for failing in (patch.object(support, "write", side_effect=OSError("disk")),
                        patch.object(support, "read", side_effect=ValueError("corrupt")),
                        patch("strategy_controller.acquire", side_effect=OSError("lock")),
                        patch("strategy_controller.release", side_effect=release_then_fail)):
            with self.subTest(failing=str(failing)), failing:
                self.assertEqual(support.model_reservation(root, "a", 1793750400.0), "gate_disabled")

    def test_gate_off_does_not_read_the_quota_settings(self):
        root, support = self.support_dir(False)
        with patch.object(support, "config", side_effect=ValueError("bounds")):
            self.assertEqual(support.model_reservation(root, "a", 1793750400.0), "gate_disabled")

    def test_turning_the_gate_back_on_restores_limits_without_counting_the_open_period(self):
        root, support = self.support_dir(False)
        now = 1793750400.0
        for name in "abcd":
            support.model_reservation(root, name, now)
        (root / "buyer_support.json").write_text(json.dumps({"model_gate_enabled": True}), encoding="utf-8")
        self.assertEqual(support.model_reservation(root, "e", now), "reserved")
        self.assertEqual(support.model_reservation(root, "f", now), "reserved")
        self.assertEqual(support.model_reservation(root, "g", now), "model_quota_hour")

    def test_upper_bounds_stay_enforced_for_the_settings_file(self):
        import buyer_support
        valid = json.loads((Path(buyer_support.__file__).parent / "buyer_support.json").read_text(encoding="utf-8"))
        for key, value in (("max_model_orders_per_hour", 3), ("max_model_orders_per_day", 25)):
            for flag in (True, False):
                with self.subTest(key=key, flag=flag), patch.object(buyer_support, "read", return_value={**valid, key: value, "model_gate_enabled": flag}):
                    with self.assertRaises(ValueError):
                        buyer_support.config()

    def test_gate_off_answer_reaches_the_model_and_only_after_filter_and_rule(self):
        reply = {"action": "walk_away", "price": None, "reason": "stop"}
        with patch("buyer.run_harness", return_value=reply) as harness:
            _, unclear = self.trade(self.transport("Our price is 90."), gate=lambda: "gate_disabled")
            self.assertEqual(harness.call_count, 1)
            self.assertEqual(unclear["decision_trail"][0]["source"], "model")
            harness.reset_mock()
            _, clear = self.trade(self.transport("asking 110."), gate=lambda: "gate_disabled")
            harness.assert_not_called()
            self.assertEqual(clear["decision_trail"][0]["reason_code"], "rule_offer")
            _, filtered = self.trade(self.transport("grade C. Our price is 90."), gate=lambda: "gate_disabled",
                                     accept={"grades": ["A", "B"]})
            harness.assert_not_called()
            self.assertEqual(filtered["decision_trail"][0]["source"], "quote_filter")

    def test_gate_off_keeps_the_budget_check_on_model_offers(self):
        with patch("buyer.run_harness", return_value={"action": "offer", "price": 300001, "reason": "over"}) as harness:
            _, record = self.trade(self.transport("asking 300001."), gate=lambda: "gate_disabled")
        self.assertEqual(self.offers(), [])
        self.assertEqual(record["outcome"], "agent_error")
        self.assertEqual(harness.call_count, 2)

    def test_gate_off_keeps_the_turn_limit(self):
        replies = [{"action": "offer", "price": 80 + i, "reason": "try"} for i in range(9)]
        with patch("buyer.run_harness", side_effect=replies) as harness:
            _, record = self.trade(self.transport("Our price is 95.", after_offer="Our price is 94."), gate=lambda: "gate_disabled")
        self.assertEqual(harness.call_count, 3)
        self.assertEqual(record["outcome"], "agent_error")

    def test_gate_off_leaves_the_counter_flag_alone(self):
        off = {"enabled": False, "max_ask_per_cap": None, "min_surplus_per_list": None}
        with patch("buyer.run_harness", return_value={"action": "walk_away", "price": None, "reason": "stop"}) as harness:
            _, record = market.trade(shop("fixture"), "lamp", 100, transport=self.transport("asking 110."), fast_rules=True,
                                     max_turns=3, model_gate=lambda: "gate_disabled", over_budget_counter=off, catalog_list_price=200)
        self.assertEqual(harness.call_count, 1)
        self.assertEqual(self.offers(), [])
        self.assertEqual(record["decision_trail"][0]["counter_check"]["reason_code"], "counter_disabled")

    def test_denied_answers_still_block_when_the_gate_is_on(self):
        with patch("buyer.run_harness") as harness:
            for answer in ("model_quota_hour", "model_quota_day", "model_gate_lock_busy", "model_order_already_reserved"):
                with self.subTest(answer=answer):
                    _, record = self.trade(self.transport("Our price is 90."), gate=lambda: answer)
                    self.assertEqual(record["failure_reason"], answer)
            harness.assert_not_called()


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
