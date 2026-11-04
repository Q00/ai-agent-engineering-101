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
