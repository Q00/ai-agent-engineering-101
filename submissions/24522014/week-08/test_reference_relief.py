"""The recent-price cap can be lifted only under every condition, stays inside the order budget and the value margin, and stops itself when it loses."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

import buyer_counter
import market_buyer as market
from market_state import read, write
import reference_relief as rr
from test_market_buyer import TradeFixture, shop

COUNTER = {"enabled": True, "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.10}
GOODS = {"order_budget": 2000, "kind": "goods", "catalog_kind": "goods"}
GOOD = dict(cap=1000, list_price=2000, order_budget=2000, grade="A", kind="goods", catalog_kind="goods", counter_reason="counter_ratio_exceeded")


class Conditions(unittest.TestCase):
    def test_all_conditions_pass(self):
        for reason in rr.ALLOWED_AFTER:
            for grade in ("A", "B"):
                self.assertEqual(rr.eligible(1600, **{**GOOD, "grade": grade, "counter_reason": reason}), (True, None))

    def test_each_condition_refuses_alone(self):
        for changes, why in (({"kind": "food"}, "not_goods"), ({"catalog_kind": "food"}, "not_goods"), ({"grade": None}, "grade_not_a_or_b"), ({"grade": "C"}, "grade_not_a_or_b"),
                             ({"list_price": 1999}, "over_value_limit"), ({"list_price": None}, "over_value_limit"), ({"list_price": True}, "over_value_limit"), ({"order_budget": 1599}, "over_order_budget"),
                             ({"counter_reason": "rule_counter_offer"}, "first_turn_counter_or_other_reason"), ({"counter_reason": "counter_surplus_too_low"}, "first_turn_counter_or_other_reason"),
                             ({"counter_reason": "counter_disabled"}, "first_turn_counter_or_other_reason"), ({"counter_reason": None}, "first_turn_counter_or_other_reason"),
                             ({"cap": 1600}, "not_over_cap")):
            with self.subTest(changes=changes):
                self.assertEqual(rr.eligible(1600, **{**GOOD, **changes}), (False, why))

    def test_value_limit_is_exact_and_ignores_warranty_protection(self):
        self.assertEqual(rr.value_limit(2000), 1600)
        self.assertEqual(rr.value_limit(1999), 1599)
        self.assertEqual(rr.value_limit(1001), 800)
        self.assertEqual(rr.eligible(1601, **GOOD)[1], "over_value_limit")


class Settings(unittest.TestCase):
    def test_off_unless_valid_and_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "s.json"
            self.assertIsNone(rr.settings(path))
            for content in ("{broken", "[]", "{}", '{"reference_relief": 1}', '{"reference_relief": {"enabled": "true", "resume_token": "1"}}', '{"reference_relief": {"enabled": true}}',
                            '{"reference_relief": {"enabled": false, "resume_token": "1"}}'):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content):
                    self.assertIsNone(rr.settings(path))
            path.write_text('{"reference_relief": {"enabled": true, "resume_token": "1"}}', encoding="utf-8")
            self.assertEqual(rr.settings(path), {"resume_token": "1"})

    def test_shipped_setting_is_off(self):
        self.assertIsNone(rr.settings())
        self.assertIs(json.loads(rr.CONFIG.read_text(encoding="utf-8"))["reference_relief"]["enabled"], False)


class AutoStop(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.md = self.root / ".market"
        self.md.mkdir()
        self.config = self.root / "s.json"
        self.config.write_text('{"reference_relief": {"enabled": true, "resume_token": "1"}}', encoding="utf-8")

    def deal(self, order="o1", token="1"):
        rr.note_deal(self.md, token, order, {"ask": 10}, {"shop_name": "s", "item": "G", "category": "c", "outcome": "purchased"}, now=100)

    def active(self, now=1000):
        return rr.active(self.md, self.root, self.config, now)

    def test_runs_without_deals(self):
        self.assertEqual(self.active(), "1")

    def test_stops_below_zero_says_so_and_stays_off(self):
        self.deal()
        with patch.object(rr, "realized", return_value=(-5, 1, 1, 0)), patch("builtins.print") as said:
            self.assertIsNone(self.active())
        self.assertIn("자동 중지", str(said.call_args))
        self.assertEqual(read(self.md / rr.STATE)["stopped"]["surplus"], -5)
        self.assertEqual(json.loads((self.root / rr.ALERTS).read_text(encoding="utf-8").splitlines()[0])["surplus"], -5)
        with patch.object(rr, "realized", return_value=(99, 1, 0, 0)):
            self.assertIsNone(self.active(now=5000))

    def test_zero_and_above_keep_running(self):
        self.deal()
        for total in (0, 50):
            with self.subTest(total=total), patch.object(rr, "realized", return_value=(total, 1, 0, 0)):
                self.assertEqual(self.active(now=1000 + total * 1000), "1")

    def test_a_new_token_starts_a_new_count(self):
        self.deal()
        with patch.object(rr, "realized", return_value=(-5, 1, 1, 0)), patch("builtins.print"):
            self.assertIsNone(self.active())
        self.config.write_text('{"reference_relief": {"enabled": true, "resume_token": "2"}}', encoding="utf-8")
        with patch.object(rr, "realized") as realized:
            self.assertEqual(self.active(now=5000), "2")
            realized.assert_not_called()

    def test_any_failure_means_off_and_never_raises(self):
        self.deal()
        with patch.object(rr, "realized", side_effect=OSError("disk")):
            self.assertIsNone(self.active())

    def test_a_failed_note_does_not_raise(self):
        with patch.object(rr, "write", side_effect=OSError("disk")), patch("builtins.print"):
            rr.note_deal(self.md, "1", "o", {}, {})

    def test_realized_counts_breakage_and_only_our_orders(self):
        deals = [dict(order="o1", estimated_surplus=-100, broken=True), dict(order="o2", estimated_surplus=30, broken=False), dict(order="other", estimated_surplus=9999, broken=False)]
        with patch("buyer_surplus.settled_deals", return_value=(deals, [])):
            self.assertEqual(rr.realized(self.root, {"o1", "o2"}), (-70, 2, 1, 0))


class References(unittest.TestCase):
    """A relief deal never becomes a price reference, and nothing changes when there are none."""

    OLD = dict(seq=1, ts=900, round=3, kind="settle", data=dict(unit="u-old", shop="http://s", item="G", amount=100, grade="A"))
    MINE = dict(seq=2, ts=950, round=3, kind="settle", data=dict(unit="u-mine", shop="http://s", item="G", amount=150, grade="A"))
    SAME_UNIT = dict(seq=3, ts=951, round=3, kind="settle", data=dict(unit="u-mine", shop="http://s", item="G", amount=150, grade="A"))
    BROKEN = dict(seq=2, ts=950, round=3, kind="broken", data=dict(unit="u-mine", shop="http://s", item="G"))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.md = Path(temp.name)

    def deal(self, order="o1", ledger=2, unit="u-mine", paid=True, price=150):
        rr.note_deal(self.md, "1", order, {"ask": price}, {"shop_name": "s", "item": "G"})
        with (self.md / "payments.jsonl").open("a", encoding="utf8") as stream:
            stream.write(json.dumps({"order": order, "paid": paid, "ledger": ledger if paid else None, "shop": "http://s", "item": "G", "price": price}) + "\n")
        if paid:
            write(self.md / "buyer-deals" / f"{ledger}.json", dict(ledger=ledger, order=order, unit=unit, shop="http://s", sku="G", price=price, grade="A", purchased_at=950, warranty_hours=0))

    def test_no_relief_deals_returns_the_same_rows(self):
        rows = [self.OLD, self.MINE]
        self.assertIs(rr.public_rows_without_deals(self.md, rows), rows)

    def test_our_row_goes_by_ledger_or_unit_and_others_stay(self):
        self.deal()
        kept = rr.public_rows_without_deals(self.md, [self.OLD, self.MINE, self.SAME_UNIT, self.BROKEN])
        self.assertEqual(kept, [self.OLD, self.BROKEN])

    def test_an_unpaid_relief_deal_removes_nothing(self):
        self.deal(paid=False)
        rows = [self.OLD, self.MINE]
        self.assertEqual(rr.public_rows_without_deals(self.md, rows), rows)

    def select(self, memory, rows):
        from r3_selection import select
        policy = {"grades": ["A", "B"], "minimum_surplus_ratio": .05, "uninsured_discount_ratio": .2, "preferred_warranty_hours": 6}
        offers = [{"name": "s", "url": "http://s", "offers": [{"sku": "G", "qty": 1}]}]
        return select(offers, memory, {"G": {"list_price": 1000}}, 1000, policy=policy, portfolio=[], kind="goods", public_rows=rows, now=1000)[4][0]

    def row(self, price, relief=None):
        return {"verification": "market_settled", "shop_name": "s", "item": "G", "outcome": "purchased", "price": price, "round": 3, "url_at_time": "http://s", "reference_relief": relief}

    def test_own_reference_skips_a_relief_deal(self):
        chosen = self.select([self.row(100), self.row(150, {"ask": 150})], [])
        self.assertEqual((chosen["past_settled_price"], chosen["reference_source"]), (100, "own_r3_settlement"))

    def test_with_only_a_relief_deal_there_is_no_own_reference(self):
        self.assertEqual(self.select([self.row(150, {"ask": 150})], [])["reference_source"], "unknown")

    def test_ordinary_deals_still_set_the_reference(self):
        for key in (None, {}):
            chosen = self.select([self.row(100), self.row(120, key)], [])
            self.assertEqual(chosen["past_settled_price"], 120)
        record_without_the_field = self.row(130)
        del record_without_the_field["reference_relief"]
        self.assertEqual(self.select([record_without_the_field], [])["past_settled_price"], 130)

    def test_prepare_passes_rows_without_our_deal(self):
        import buyer_support
        self.deal()
        captured = {}

        def choose(shops, memory, catalog, budget, policy, portfolio, rows, *args, **kwargs):
            captured["rows"] = rows
            return None, None, budget, "none", []
        settings = {**buyer_support.config()}
        snapshot = {"round": 3, "observed_at": 1000, "rows": [self.OLD, self.MINE]}
        with patch.object(buyer_support, "choose", choose), patch.object(buyer_support, "context", return_value={}), patch.object(buyer_support, "config", return_value=settings):
            buyer_support.prepare(self.md, [], [], {}, 100, {}, [], snapshot, {"order": "new", "kind": "goods"}, now=1000)
        self.assertEqual(captured["rows"], [self.OLD])

    def attempt(self, order, cap, price, ledger):
        return {"at": 950, "order": order, "shop": "s", "url": "http://s", "sku": "G", "cap": cap, "rival_at_selection": False}

    def records(self, attempts):
        write(self.md / "buyer-support-state.json", {"schema": 1, "round": 3, "attempts": attempts})

    def test_records_keep_relief_deals_marked(self):
        import buyer_support
        self.deal()
        self.records([self.attempt("o1", 100, 150, 2)])
        report = buyer_support.report(self.md)
        self.assertEqual((report["confirmed_paid_deals"], report["reference_relief_paid_deals"], report["buyer_paid_to_other_shops"]), (1, 1, 150))
        rows = buyer_support.research_rows(self.md, 951)
        self.assertEqual([r["seq"] for r in rows], [2])
        self.assertIs(rows[0]["reference_relief"], True)

    def test_ordinary_records_are_unchanged(self):
        import buyer_support
        with (self.md / "payments.jsonl").open("a", encoding="utf8") as stream:
            stream.write(json.dumps({"order": "n1", "paid": True, "ledger": 5, "shop": "http://s", "item": "G", "price": 90}) + "\n")
            stream.write(json.dumps({"order": "n2", "paid": True, "ledger": 6, "shop": "http://s", "item": "G", "price": 150}) + "\n")
        for ledger, order, price in ((5, "n1", 90), (6, "n2", 150)):
            write(self.md / "buyer-deals" / f"{ledger}.json", dict(ledger=ledger, order=order, unit=f"u{ledger}", shop="http://s", sku="G", price=price, grade="A", purchased_at=950, warranty_hours=0))
        self.records([self.attempt("n1", 100, 90, 5), self.attempt("n2", 100, 150, 6)])
        report = buyer_support.report(self.md)
        self.assertEqual((report["confirmed_paid_deals"], report["reference_relief_paid_deals"]), (1, 0))
        rows = buyer_support.research_rows(self.md, 951)
        self.assertEqual([r["seq"] for r in rows], [5])
        self.assertNotIn("reference_relief", rows[0])

    def test_a_noted_deal_at_another_price_is_not_let_through(self):
        import buyer_support
        self.deal(price=150)
        self.assertFalse(buyer_support.within_limit(151, {"order": "o1", "cap": 100}, buyer_support.relief_deals(self.md)))
        self.assertTrue(buyer_support.within_limit(150, {"order": "o1", "cap": 100}, buyer_support.relief_deals(self.md)))


class Trades(TradeFixture):
    def sequence(self, replies, *, receipt_price=None, metadata=None):
        """Replies in order; an offer after the last one completes the deal at the offered price, or at receipt_price."""
        remaining = list(replies)

        def respond(request):
            if request.method == "GET":
                return httpx.Response(200, json={"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "http://fixture.test/rpc"}]})
            body = json.loads(request.content)
            self.posts.append(body)
            message = body.get("params", {}).get("message", {})
            state, artifacts = "TASK_STATE_INPUT_REQUIRED", []
            if body["method"] == "CancelTask":
                state, text = "TASK_STATE_CANCELED", "bye"
            elif remaining:
                text = remaining.pop(0)
            else:
                state, text = "TASK_STATE_COMPLETED", "Deal"
                artifacts = [{"parts": [{"data": {"shop": "Fixture", "item": "lamp", "price": receipt_price or int(message["parts"][0]["text"]), "task": "task-a", "sig_seller": "x"}}]}]
            reply = {"parts": [{"text": text}], "metadata": {"grade": "A", **(metadata or {})}}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"id": "task-a", "contextId": "c", "status": {"state": state, "message": reply}, "artifacts": artifacts}})
        return httpx.MockTransport(respond)

    def trade(self, transport, *, relief=GOODS, cap=1000, list_price=2000, accept=None, gate=None):
        gate = gate or (lambda: False)
        with patch("buyer.run_harness") as harness:
            result, record = market.trade(shop("fixture"), "lamp", cap, transport=transport, fast_rules=True, model_gate=gate, max_turns=3, accept_conditions=accept or {"cert": True, "grades": ["A", "B"]},
                                          over_budget_counter=COUNTER, catalog_list_price=list_price, reference_relief=relief)
            harness.assert_not_called()
        return result, record

    def test_first_turn_counters_then_accepts_the_new_ask(self):
        result, record = self.trade(self.sequence(["Asking 1100.", "Asking 1100."]))
        self.assertEqual(self.offers(), [1000, 1100])
        self.assertEqual(result["receipt"]["price"], 1100)
        self.assertEqual([s["reason_code"] for s in record["decision_trail"]], ["rule_counter_offer", rr.REASON_CODE])
        taken = record["reference_relief"]
        self.assertEqual((taken["ask"], taken["reference_cap"], taken["list_price"], taken["grade"], taken["turn"], taken["counter_reason"]), (1100, 1000, 2000, "A", 2, "counter_already_used"))
        self.assertEqual(record["budget"], 1000)

    def test_a_ratio_too_wide_to_counter_accepts_at_once(self):
        result, record = self.trade(self.sequence(["Asking 1500."]))
        self.assertEqual(self.offers(), [1500])
        self.assertEqual(result["receipt"]["price"], 1500)
        self.assertEqual(record["reference_relief"]["counter_reason"], "counter_ratio_exceeded")

    def test_never_above_the_order_budget_or_the_value_margin(self):
        for replies, relief, list_price in ((["Asking 1500."], {**GOODS, "order_budget": 1499}, 2000), (["Asking 1601."], GOODS, 2000)):
            with self.subTest(replies=replies):
                self.posts.clear()
                result, record = self.trade(self.sequence(replies), relief=relief, list_price=list_price)
                self.assertEqual(self.offers(), [])
                self.assertIsNone(result["receipt"])
                self.assertIsNone(record["reference_relief"])

    def test_a_receipt_other_than_the_accepted_ask_is_refused(self):
        result, record = self.trade(self.sequence(["Asking 1500."], receipt_price=1501))
        self.assertIsNone(result["receipt"])
        self.assertEqual(record["outcome"], "receipt_mismatch")

    def test_food_unknown_grade_and_off_all_stop_at_the_gate(self):
        for relief, metadata in (({**GOODS, "kind": "food"}, None), ({**GOODS, "catalog_kind": "food"}, None), (None, None)):
            with self.subTest(relief=relief):
                self.posts.clear()
                result, record = self.trade(self.sequence(["Asking 1500."], metadata=metadata), relief=relief)
                self.assertEqual(self.offers(), [])
                self.assertEqual(record["decision_trail"][-1]["reason_code"], "model_gate_denied")

    def test_grade_c_is_refused_by_the_condition_even_if_policy_would_allow_it(self):
        result, record = self.trade(self.sequence(["Asking 1500."], metadata={"grade": "C"}), accept={"cert": True, "grades": ["A", "B", "C"]})
        self.assertEqual(self.offers(), [])
        self.assertIsNone(record["reference_relief"])

    def test_no_grade_stops_at_the_gate(self):
        def respond_without_grade(request):
            if request.method == "GET":
                return httpx.Response(200, json={"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "http://fixture.test/rpc"}]})
            body = json.loads(request.content)
            self.posts.append(body)
            state = "TASK_STATE_CANCELED" if body["method"] == "CancelTask" else "TASK_STATE_INPUT_REQUIRED"
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"id": "task-a", "contextId": "c", "status": {"state": state, "message": {"parts": [{"text": "Asking 1500."}]}}, "artifacts": []}})
        result, record = self.trade(httpx.MockTransport(respond_without_grade))
        self.assertEqual(self.offers(), [])
        self.assertEqual(record["decision_trail"][-1]["reason_code"], "model_gate_denied")

    def test_only_once_in_a_negotiation(self):
        result, record = self.trade(self.sequence(["Asking 1500.", "Asking 1590.", "Asking 1590."]))
        self.assertEqual(self.offers()[0], 1500)
        self.assertTrue(all(offer <= 1500 for offer in self.offers()))
        self.assertLessEqual(sum(s["reason_code"] == rr.REASON_CODE for s in record["decision_trail"]), 1)

    def test_the_model_is_not_called_and_the_gate_not_used(self):
        gate = []
        self.trade(self.sequence(["Asking 1500."]), gate=lambda: gate.append(1) or False)
        self.assertEqual(gate, [])


if __name__ == "__main__":
    unittest.main()
