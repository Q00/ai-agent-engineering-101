"""The funnel must count payment only from the kit's payment record and keep old runs labeled as unknown."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import buyer_funnel as bf

SHOP = "https://shop.test"


def trade_record(order, *, outcome="no_deal", agreed=None, receipt=None, trail=(), reason=None, log_dir=None, **extra):
    record = {"order": order, "outcome": outcome, "url_at_time": SHOP, "item": "sku",
              "price": receipt["price"] if receipt else None, "agreed_price": agreed, "receipt": receipt,
              "buyer_log_dir": log_dir or "", **extra}
    if trail is not None:
        record.update(decision_trail=list(trail), failure_reason=reason)
    return record


def step(source, code, parser="no_labeled_price", action="walk_away"):
    return {"turn": 1, "source": source, "reason_code": code, "parser_reason": parser, "action": action, "price": None}


def receipt(price=100):
    return {"item": "sku", "price": price, "task": "t"}


class Funnel(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "logs/market-buyer").mkdir(parents=True)
        (self.root / ".market").mkdir()
        self.count = 0

    def add(self, record, name=None):
        self.count += 1
        name = name or f"20261104T1000{self.count:02d}000000Z"
        (self.root / "logs/market-buyer" / f"{name}-abcd.json").write_text(
            json.dumps({"record": record}), encoding="utf-8")

    def pay(self, *rows):
        with (self.root / ".market/payments.jsonl").open("a", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")

    def report(self, since="20261104T000000000000Z", until=None):
        return bf.funnel(self.root, since, until)

    def stages(self, report):
        return {row["stage"]: row["count"] for row in report["funnel"]}

    def test_only_a_settled_payment_row_counts_as_paid(self):
        rule = [step("deterministic", "rule_offer", "rule_offer", "offer")]
        self.add(trade_record("paid", outcome="purchased", agreed=100, receipt=receipt(), trail=rule))
        self.add(trade_record("grade", outcome="purchased", agreed=100, receipt=receipt(), trail=rule))
        self.add(trade_record("life", outcome="purchased", agreed=100, receipt=receipt(), trail=rule))
        self.add(trade_record("norow", outcome="purchased", agreed=100, receipt=receipt(), trail=rule))
        self.add(trade_record("other", outcome="purchased", agreed=100, receipt=receipt(), trail=rule))
        self.add(trade_record("noledger", outcome="purchased", agreed=100, receipt=receipt(), trail=rule))
        self.pay({"order": "paid", "shop": SHOP, "item": "sku", "price": 100, "paid": True, "ledger": 7},
                 {"order": "grade", "shop": SHOP, "item": "sku", "price": 100, "paid": False,
                  "reason": "not paying: grade C, the policy accepts ['A', 'B']"},
                 {"order": "life", "shop": SHOP, "item": "sku", "price": 100, "paid": False,
                  "reason": "not paying: 114.52 h of shelf life left, the policy asks for 128.0"},
                 {"order": "other", "shop": SHOP, "item": "sku", "price": 90, "paid": True, "ledger": 8},
                 {"order": "noledger", "shop": SHOP, "item": "sku", "price": 100, "paid": True})
        report = self.report()
        self.assertEqual(self.stages(report), {"orders": 6, "selected": 6, "quoted": 6, "offered": 6,
                                               "receipt": 6, "paid": 1})
        self.assertEqual(report["causes"], {"paid": 1, "payment_refused:grade": 1, "payment_refused:shelf_life": 1,
                                            "receipt_no_payment_row": 1, "payment_record_mismatch": 1,
                                            "payment_refused:unknown": 1})

    def test_each_failure_stage_gets_its_own_cause(self):
        self.add({"outcome": "research_skipped", "order": "s", "buyer_support": {"status": "attempt_cooldown"}})
        self.add(trade_record("q", trail=[step("model_gate", "model_quota_hour", "over_budget")],
                              reason="model_quota_hour", negotiation_outcome="walked_away"))
        self.add(trade_record("o", agreed=100, trail=[step("deterministic", "rule_offer", "rule_offer", "offer")],
                              reason="seller_rejected"))
        self.add(trade_record("r", trail=[step("model", "model_offer", action="offer")], reason="seller_rejected"))
        self.add(trade_record("a", trail=[], reason="seller_rejected"))
        self.add(trade_record("t", outcome="transport_error", trail=[], reason="transport_error"))
        report = self.report()
        self.assertEqual(self.stages(report), {"orders": 6, "selected": 5, "quoted": 3, "offered": 1,
                                               "receipt": 0, "paid": 0})
        self.assertEqual(report["causes"], {"selection:attempt_cooldown": 1, "model_quota_hour": 1,
                                            "seller_rejected_after_offer": 1, "seller_rejected_at_quote": 1,
                                            "seller_rejected_at_open": 1, "transport_error": 1})
        self.assertEqual(report["non_rule_paths_by_parser_reason"]["model_quota_hour|parser=over_budget"], 1)
        self.assertEqual(report["decision_paths"]["deterministic:rule_offer"], 1)
        self.assertEqual(report["sample"], {"hook_runs": 6, "distinct_orders": 6, "runs_with_decision_trail": 5})

    def test_quote_filter_is_counted_by_reason_and_basis(self):
        def filtered(code, basis, conflicts=()):
            entry = step("quote_filter", code)
            entry["quote_check"] = {"reason_code": code, "basis": basis, "conflicts": list(conflicts),
                                    "text_hours_note": "absent"}
            return entry
        self.add(trade_record("g", trail=[filtered("quote_grade_unacceptable", "structured")],
                              reason="quote_grade_unacceptable"))
        self.add(trade_record("s", trail=[filtered("quote_shelf_life_short", "text", ["hours_left"])],
                              reason="quote_shelf_life_short"))
        report = self.report()
        self.assertEqual(self.stages(report), {"orders": 2, "selected": 2, "quoted": 2, "offered": 0, "receipt": 0, "paid": 0})
        self.assertEqual(report["causes"], {"quote_grade_unacceptable": 1, "quote_shelf_life_short": 1})
        self.assertEqual(report["decision_paths"]["quote_filter:quote_shelf_life_short"], 1)
        self.assertEqual(report["quote_filter_by_basis"], {"quote_grade_unacceptable|basis=structured": 1,
                                                           "quote_shelf_life_short|basis=text": 1})
        self.assertEqual(report["quote_conflicts"], {"hours_left": 1})
        self.assertEqual(report["non_rule_paths_by_parser_reason"], {})

    def test_counteroffers_are_separate_from_rule_offers(self):
        counter = step("deterministic", "rule_counter_offer", "over_budget", "offer")
        counter["counter_check"] = {"reason_code": "rule_counter_offer"}
        refused = step("model_gate", "model_quota_hour", "over_budget")
        refused["counter_check"] = {"reason_code": "counter_ratio_exceeded"}
        disabled = step("model_gate", "model_quota_hour", "over_budget")
        disabled["counter_check"] = {"reason_code": "counter_disabled"}
        self.add(trade_record("c1", outcome="purchased", agreed=100, receipt=receipt(), trail=[counter]))
        self.add(trade_record("c2", agreed=100, trail=[counter], reason="seller_rejected"))
        self.add(trade_record("r", outcome="purchased", agreed=100, receipt=receipt(),
                              trail=[step("deterministic", "rule_offer", "rule_offer", "offer")]))
        self.add(trade_record("x", trail=[refused], reason="model_quota_hour"))
        self.add(trade_record("d", trail=[disabled], reason="model_quota_hour"))
        self.pay({"order": "c1", "shop": SHOP, "item": "sku", "price": 100, "paid": True, "ledger": 9},
                 {"order": "r", "shop": SHOP, "item": "sku", "price": 100, "paid": True, "ledger": 10})
        report = self.report()
        self.assertEqual(report["counter_offer"], {"offered": 2, "receipt": 1, "paid": 1, "exclusions": {
            "counter_ratio_exceeded": 1, "counter_disabled": 1}})
        self.assertEqual(report["decision_paths"]["deterministic:rule_counter_offer"], 2)
        self.assertEqual(report["decision_paths"]["deterministic:rule_offer"], 1)

    def test_turn_limit_endings_and_cancel_results_are_counted(self):
        def ended(result):
            entry = step("turn_limit", "turn_limit_exceeded", None, None)
            entry["action"] = None
            entry["cancel"] = {"attempted": True, "result": result, "state": "x", "error": None}
            return entry
        self.add(trade_record("a", outcome="agent_error", agreed=90, trail=[ended("canceled")], reason="turn_limit_exceeded"))
        self.add(trade_record("b", outcome="agent_error", agreed=90, trail=[ended("failed")], reason="turn_limit_exceeded"))
        report = self.report()
        self.assertEqual(report["causes"], {"turn_limit_exceeded": 2})
        self.assertEqual(report["turn_limit"], {"endings": 2, "cancel_results": {"canceled": 1, "failed": 1}})
        self.assertEqual(report["decision_paths"]["turn_limit:turn_limit_exceeded"], 2)
        self.assertEqual(report["non_rule_paths_by_parser_reason"], {})

    def test_model_routes_are_counted_by_provider(self):
        first = dict(step("model", "model_offer", "over_budget", "offer"), provider="agy", model="gemini-x", route_reason="agy_first")
        second = dict(step("model", "model_walk_away"), provider="codex", model="gpt-x", route_reason="agy_limit_failure")
        self.add(trade_record("a", agreed=90, trail=[first], reason="seller_rejected"))
        self.add(trade_record("b", trail=[second], reason="model_walk_away"))
        self.assertEqual(self.report()["model_routes"], {"agy:gemini-x|agy_first": 1, "codex:gpt-x|agy_limit_failure": 1})

    def test_refusals_by_wording_listed_quantity_and_skips(self):
        self.add(trade_record("a", trail=[], reason="seller_rejected", request_format="sku_only", refused_at_open=True, selected_offer_qty=1))
        self.add(trade_record("b", trail=[], reason="seller_rejected", request_format="name_and_sku", refused_at_open=True, selected_offer_qty=2))
        self.add(trade_record("c", trail=[], reason="x", request_format="name_and_sku", refused_at_open=False, selected_offer_qty=9,
                              buyer_support={"refusal_skipped_shops": ["s1", "s2"]}))
        report = self.report()
        self.assertEqual(report["open_refusals_by_request_format"], {"sku_only": {"attempts": 1, "refused_at_open": 1},
                                                                     "name_and_sku": {"attempts": 2, "refused_at_open": 1}})
        self.assertEqual(report["open_refusals_by_listed_qty"]["4+"], {"attempts": 1, "refused_at_open": 0})
        self.assertEqual(report["runs_skipping_refusing_shops"], {"s1": 1, "s2": 1})

    def test_receipt_mismatches_are_counted_by_format_and_kind(self):
        other = {"item": "other", "price": 100, "task": "t"}
        self.add(trade_record("a", outcome="receipt_mismatch", agreed=100, receipt=other, task="t", shop_name="s1", request_format="name_and_sku"))
        self.add(trade_record("b", outcome="receipt_mismatch", agreed=100, receipt=other, task="t", shop_name="s2", request_format="sku_only"))
        self.add(trade_record("c", outcome="receipt_mismatch", agreed=90, receipt=receipt(), task="t", shop_name="s2"))
        self.add(trade_record("d", reason="seller_rejected", shop_name="s3"))
        found = self.report()["receipt_mismatches"]
        self.assertEqual(found["total"], 3)
        self.assertEqual(found["by_request_format"], {"name_and_sku": 1, "sku_only": 1, "None": 1})
        self.assertEqual(found["by_kind"], {"item": 2, "price": 1})
        self.assertEqual(found["by_shop"], {"s2": 2, "s1": 1})

    def test_category_receipts_are_counted_by_shop_and_format(self):
        other = {"item": "김치", "price": 100, "task": "t"}
        self.add(trade_record("a", outcome="receipt_category_only", agreed=100, receipt=other, task="t", shop_name="s1", receipt_format="order_item", category_receipt_release={"result": "failed"}))
        self.add(trade_record("b", outcome="receipt_category_only", agreed=100, receipt=other, task="t", shop_name="s1", receipt_format="order_item", category_receipt_release={"result": "canceled"}))
        self.add(trade_record("c", reason="seller_rejected"))
        report = self.report()
        self.assertEqual(report["receipt_formats"], {"order_item": {"receipts": 2, "paid": 0}})
        self.assertEqual(report["receipt_category_only"], {"total": 2, "by_shop": {"s1": 2}, "release_results": {"failed": 1, "canceled": 1}})
        self.assertEqual(report["receipt_mismatches"]["total"], 0)
        self.assertEqual(report["causes"]["receipt_category_only"], 2)

    def test_no_mismatch_reports_zero(self):
        self.add(trade_record("a", reason="seller_rejected"))
        self.assertEqual(self.report()["receipt_mismatches"], {"total": 0, "by_request_format": {}, "by_kind": {}, "by_shop": {}})

    def legacy_dir(self, name, *, outcome, turns=1, decision=None):
        directory = self.root / "logs/buyer" / name
        directory.mkdir(parents=True)
        (directory / "02.response.txt").write_text(json.dumps({"result": {"task": {
            "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}}}), encoding="utf-8")
        (directory / "outcome.json").write_text(json.dumps({"outcome": outcome, "turns": turns}), encoding="utf-8")
        if decision:
            (directory / f"decision-{turns:02d}.json").write_text(json.dumps(decision), encoding="utf-8")
        return "C:\\elsewhere\\logs\\buyer\\" + name

    def test_old_runs_are_labeled_unlogged_not_assumed_to_be_quota(self):
        unlogged = self.legacy_dir("a", outcome="walked_away")
        model = self.legacy_dir("b", outcome="walked_away", decision={"status": "ok"})
        rule = self.legacy_dir("c", outcome="walked_away", decision={"source": "deterministic"})
        rejected = self.legacy_dir("d", outcome="seller_rejected")
        for order, path in (("a", unlogged), ("b", model), ("c", rule), ("d", rejected)):
            self.add(trade_record(order, trail=None, log_dir=path))
        report = self.report()
        self.assertEqual(self.stages(report)["quoted"], 4)
        self.assertEqual(report["causes"], {"walk_away_unlogged_pre_fix": 1, "model_walk_away": 1,
                                            "rule_repeated_offer": 1, "seller_rejected_at_quote": 1})
        self.assertEqual(report["sample"]["runs_with_decision_trail"], 0)

    def test_time_window_uses_the_run_stamp(self):
        self.add(trade_record("x", trail=[]), name="20261104T090000000000Z")
        self.add(trade_record("y", trail=[]), name="20261104T100000000000Z")
        self.add(trade_record("z", trail=[]), name="20261104T110000000000Z")
        report = self.report(bf.stamp("2026-11-04T09:30:00Z"), bf.stamp("2026-11-04T19:30:00+09:00"))
        self.assertEqual(report["sample"]["hook_runs"], 1)
        self.assertEqual(report["window"]["first_run"], "20261104T100000000000Z")
        with self.assertRaises(ValueError):
            bf.stamp("2026-11-04T09:30:00")

    def test_empty_window_has_no_rates(self):
        report = self.report()
        self.assertEqual(report["sample"]["hook_runs"], 0)
        self.assertTrue(all(row["of_previous"] is None for row in report["funnel"]))

    def test_payment_reason_urls_and_numbers_are_masked(self):
        cause = bf.payment_cause({"payment_reason": "the checkout at https://x.test/a total 1,000 failed"})
        self.assertNotIn("x.test", cause)
        self.assertNotIn("1,000", cause)


if __name__ == "__main__":
    unittest.main()
