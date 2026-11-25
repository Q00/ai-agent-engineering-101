"""Retry reason "error_before_offer" (retry_other_shop.errors_before_offer): an agent or transport error in an attempt whose trail
has no offer or accept action is retried once at another shop, by error type and trail state only, under the same time limits,
the first shop excluded and one model reservation per order. Errors after an offer, a purchase or a receipt are never retried."""

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import buyer_support as support
import market_buyer

T = 1793760000
ON = {"min_order_seconds_left": 180, "max_elapsed_seconds": 150}
ERR = {**ON, "errors_before_offer": True}
ORDER = {"expires": T + 600}


def rec(outcome, actions=(), **extra):
    trail = [{"turn": i + 1, "action": a, "reason_code": "x"} for i, a in enumerate(actions)]
    return {"outcome": outcome, "failure_reason": extra.pop("failure_reason", outcome), "decision_trail": trail, **extra}


class Decision(unittest.TestCase):
    def test_errors_before_any_offer_are_retried(self):
        for record in (rec("agent_error"), rec("transport_error"), rec("agent_error", actions=(None,)), rec("transport_error", actions=(None, "walk_away"))):
            with self.subTest(record=record):
                self.assertEqual(support.should_retry(record, ORDER, 5, T, ERR), ("error_before_offer", "retry"))

    def test_errors_after_an_offer_are_never_retried(self):
        for record in (rec("agent_error", actions=("offer",)), rec("transport_error", actions=(None, "offer")), rec("agent_error", actions=("accept",)),
                       rec("agent_error", actions=("offer", None), failure_reason="turn_limit_exceeded")):
            with self.subTest(record=record):
                self.assertEqual(support.should_retry(record, ORDER, 5, T, ERR), (None, "not_retryable"))

    def test_a_purchase_or_a_receipt_is_never_retried(self):
        for record in (rec("purchased"), rec("agent_error", receipt={"price": 1}), rec("transport_error", receipt={"price": 1})):
            with self.subTest(record=record):
                self.assertEqual(support.should_retry(record, ORDER, 5, T, ERR), (None, "not_retryable"))

    def test_other_outcomes_and_a_missing_trail_are_not_this_reason(self):
        for record in (rec("invalid_receipt"), rec("receipt_mismatch"), rec("no_deal", failure_reason="model_quota_hour"),
                       {"outcome": "agent_error", "failure_reason": "agent_error"}):
            with self.subTest(record=record):
                self.assertEqual(support.should_retry(record, ORDER, 5, T, ERR), (None, "not_retryable"))

    def test_off_keeps_todays_reasons_only(self):
        self.assertEqual(support.should_retry(rec("agent_error"), ORDER, 5, T, ON), (None, "not_retryable"))
        self.assertEqual(support.should_retry(rec("transport_error"), ORDER, 5, T, ON), (None, "not_retryable"))
        self.assertEqual(support.should_retry(rec("no_deal", failure_reason="quote_grade_unacceptable"), ORDER, 5, T, ERR), ("quote_grade_unacceptable", "retry"))

    def test_the_time_limits_apply(self):
        self.assertEqual(support.should_retry(rec("agent_error"), {"expires": T + 179}, 5, T, ERR), (None, "order_closing"))
        self.assertEqual(support.should_retry(rec("agent_error"), ORDER, 151, T, ERR), (None, "elapsed"))


class Settings(unittest.TestCase):
    def test_only_true_turns_it_on(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "b.json"
            for value, expected in ((True, ERR), (False, ON), ("true", ON), (1, ON), (None, ON)):
                path.write_text(json.dumps({"retry_other_shop": {"enabled": True, **ON, "errors_before_offer": value}}), encoding="utf-8")
                self.assertEqual(support.retry_settings(path), expected, value)
            path.write_text(json.dumps({"retry_other_shop": {"enabled": False, **ON, "errors_before_offer": True}}), encoding="utf-8")
            self.assertIsNone(support.retry_settings(path))

    def test_the_shipped_setting(self):
        section = json.loads((support.ROOT / "buyer_support.json").read_text(encoding="utf-8"))["retry_other_shop"]
        # Installed off at 12:00:16 KST 11-09; switched on in its own commit after the full suite.
        self.assertIs(section["errors_before_offer"], True)


class Main(unittest.TestCase):
    """market_buyer.main with the reason on: one retry at another shop, never a third attempt, never after an offer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.md = root / ".market"
        self.md.mkdir()
        (self.md / "catalog.json").write_text(json.dumps({"G1": {"kind": "goods", "list_price": 100000}}), encoding="utf-8")
        (self.md / "order.json").write_text(json.dumps({"order": "o1", "item": "G1", "budget": 90000, "round": 3, "kind": "goods", "acceptable": ["G1"], "expires": T + 600}), encoding="utf-8")
        shops = [{"name": n, "url": "https://shops/" + n, "reviews": [], "offers": [{"sku": "G1", "qty": 2}]} for n in ("a", "b", "c")]
        (self.md / "shops.json").write_text(json.dumps(shops), encoding="utf-8")
        self.root = root

    def run_main(self, attempts):
        attempts = list(attempts); trades = []; prepares = []
        def prepare(md, shops, memory, catalog, budget, policy, portfolio, snapshot, order, retry_of=None):
            prepares.append(retry_of)
            chosen = next(s for s in shops if s["name"] != retry_of)
            return chosen, "G1", 70000, "p", [{"shop": chosen["name"], "sku": "G1", "warranty_required": 0, "cap_without_floor": 80000}], \
                {"status": "reserved", "max_turns": 3, "fast_rules": False, "retry_of": retry_of}
        def trade(shop, item, budget, **options):
            trades.append(shop["name"])
            outcome, actions = attempts.pop(0)
            record = {"timestamp": "2026-11-05T00:00:00+00:00", "shop_name": shop["name"], "url_at_time": shop["url"], "item": item, **rec(outcome, actions),
                      "receipt": {"price": 69000} if outcome == "purchased" else None}
            return {"shop": shop["url"], "receipt": record["receipt"], "stars": 3, "review": "r"}, record
        argv = ["market_buyer.py", "--shops", str(self.md / "shops.json"), "--item", "G1", "--budget", "90000", "--order", str(self.md / "order.json"), "--mode", "codex"]
        with patch.object(sys, "argv", argv), patch.object(market_buyer, "ROOT", self.root), patch.object(market_buyer, "prepare_support", side_effect=prepare), \
                patch.object(market_buyer, "trade", side_effect=trade), patch.object(market_buyer, "fresh", return_value={}), \
                patch.object(market_buyer, "retry_settings", return_value=ERR), patch.object(market_buyer.time, "time", return_value=T), redirect_stdout(io.StringIO()) as out:
            market_buyer.main()
        log = json.loads(sorted((self.root / "logs/market-buyer").glob("*.json"))[-1].read_text(encoding="utf-8"))
        return trades, prepares, log, [l for l in out.getvalue().splitlines() if l.startswith("{")]

    def test_an_error_before_any_offer_is_retried_once_at_another_shop(self):
        trades, prepares, log, lines = self.run_main([("agent_error", ()), ("purchased", ("offer",))])
        self.assertEqual(trades, ["a", "b"])
        self.assertEqual(prepares, [None, "a"])
        self.assertEqual(log["record"]["retry"], {"status": "retry", "reason": "error_before_offer", "of": "a"})
        self.assertEqual(len(lines), 1)

    def test_the_second_attempt_is_never_retried_again(self):
        trades, _, log, lines = self.run_main([("transport_error", ()), ("agent_error", ())])
        self.assertEqual(trades, ["a", "b"])
        self.assertEqual(len(lines), 1)

    def test_an_error_after_an_offer_ends_the_order(self):
        trades, _, log, _ = self.run_main([("agent_error", ("offer",))])
        self.assertEqual(trades, ["a"])
        self.assertEqual(log["record"]["retry"]["status"], "not_retryable")


if __name__ == "__main__":
    unittest.main()
