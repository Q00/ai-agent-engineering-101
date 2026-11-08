import contextlib
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from buyer_diagnostics import read_tail, summarize
from harness import HarnessError
from market_buyer import Buyer, BuyerError, ReceiptMismatch, main, trade_and_record


def task(state="TASK_STATE_INPUT_REQUIRED", text="Asking price: 80", receipt=None):
    result = {"id": "t1", "status": {"state": state, "message": {"parts": [{"text": text}]}}}
    if receipt is not None:
        result["artifacts"] = [{"parts": [{"data": receipt}]}]
    return result


TERMS = {"price_limit": 90, "max_concession": 10, "max_decisions": 2,
         "allow_accept_above_concession": False}


class NegotiationDiagnosticsTests(unittest.TestCase):
    def run_buyer(self, tasks, decisions=None, *, policy=None, error=None):
        responses = iter(tasks)
        def transport(request):
            if request.method == "GET":
                if error:
                    raise error
                return httpx.Response(200, json={"supportedInterfaces": [{"url": "https://shop.invalid/rpc",
                    "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]})
            body = json.loads(request.content)
            response = task("TASK_STATE_CANCELED", "cancelled") if body["method"] == "CancelTask" else next(responses)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": response})
        client = httpx.Client(transport=httpx.MockTransport(transport))
        buyer = Buyer(client, url="https://shop.invalid", item="G1", budget=100, policy=policy or TERMS)
        with client, patch("market_buyer.buyer_decision", side_effect=decisions or []) as model, contextlib.redirect_stdout(io.StringIO()):
            try:
                result = buyer.run()
            except (BuyerError, HarnessError) as exc:
                result = exc
        return buyer, result, model.call_count

    def test_walk_away_is_local_reason_not_seller_stock_claim(self):
        buyer, result, calls = self.run_buyer([task(text="Out of stock. Asking price: 80")],
            [{"action": "walk_away", "reason": "stock unavailable"}])
        self.assertEqual(result, "no_deal")
        self.assertEqual(buyer.diagnostics()["reason"], "model_walk_away")
        self.assertEqual(buyer.diagnostics()["decisions"], 1)
        self.assertEqual(buyer.last_parsed_ask, 80)
        self.assertEqual(calls, 1)
        self.assertIsNone(buyer.seller_terminal)

    def test_seller_rejection_and_cancellation_are_observed_states(self):
        for state, reason in (("TASK_STATE_REJECTED", "seller_rejected"), ("TASK_STATE_CANCELED", "seller_cancelled")):
            with self.subTest(state=state):
                buyer, result, calls = self.run_buyer([task(state, "price too low; inventory empty")])
                self.assertEqual(result, "no_deal")
                self.assertEqual(buyer.reason_code, reason)
                self.assertEqual(buyer.seller_terminal["reported_tags"], ["stock", "price"])
                self.assertEqual(buyer.seller_terminal["source"], "untrusted_seller")
                self.assertEqual(calls, 0)

    def test_price_and_concession_and_turn_limits_have_distinct_codes(self):
        cases = [([{"action": "offer", "price": 91, "reason": "test"}], "price_limit", 1),
                 ([{"action": "offer", "price": 40, "reason": "test"},
                   {"action": "offer", "price": 60, "reason": "test"}], "concession_limit", 2),
                 ([{"action": "offer", "price": 40, "reason": "test"}] * 2, "decision_limit", 2)]
        for decisions, reason, calls in cases:
            with self.subTest(reason=reason):
                buyer, result, actual = self.run_buyer([task()] * 3, decisions)
                self.assertEqual(result, "no_deal")
                self.assertEqual(buyer.reason_code, reason)
                self.assertEqual(buyer.decisions, calls)
                self.assertEqual(actual, calls)

    def test_receipt_does_not_claim_payment(self):
        receipt = {"item": "G1", "price": 50, "task": "t1", "sig_seller": "unchanged-signature"}
        buyer, result, _ = self.run_buyer([task(), task("TASK_STATE_COMPLETED", "done", receipt)],
            [{"action": "offer", "price": 50, "reason": "test"}])
        self.assertEqual(result, "receipt_issued")
        self.assertEqual(buyer.reason_code, "receipt_issued")
        self.assertEqual(buyer.receipt, receipt)
        self.assertEqual(buyer.diagnostics()["last_authorized_bid"], 50)
        self.assertNotIn("sig_seller", json.dumps(buyer.diagnostics()))
        self.assertNotIn("budget", json.dumps(buyer.diagnostics()))

    def test_receipt_mismatch_is_not_price_policy_refusal(self):
        buyer, result, _ = self.run_buyer([task(), task("TASK_STATE_COMPLETED", "done", {"item": "G1", "price": 51})],
            [{"action": "offer", "price": 50, "reason": "test"}])
        self.assertIsInstance(result, ReceiptMismatch)
        self.assertEqual(buyer.reason_code, "receipt_mismatch")

    def test_model_errors_and_transport_errors_are_distinct(self):
        for exc, reason in ((HarnessError("Harness timed out; log"), "model_timeout"),
                            (HarnessError("invalid JSON"), "model_error")):
            buyer, result, calls = self.run_buyer([task()], [exc])
            self.assertIsInstance(result, HarnessError)
            self.assertEqual(buyer.reason_code, reason)
            self.assertEqual(buyer.decisions, 1)
        buyer, result, calls = self.run_buyer([], error=httpx.ReadTimeout("secret token must not enter diagnostics"))
        self.assertIsInstance(result, BuyerError)
        self.assertEqual(buyer.reason_code, "protocol_or_network_error")
        self.assertEqual(buyer.stage, "discovery")
        self.assertNotIn("secret", json.dumps(buyer.diagnostics()))

    def test_trade_persists_diagnostics_without_changing_result_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.jsonl"
            def run(buyer):
                buyer.reason_code = "seller_rejected"
                return "no_deal"
            with patch.object(Buyer, "run", run):
                result = trade_and_record({"name": "shop", "url": "https://shop.invalid"}, item="G1", budget=100,
                    memory_path=path, reason="test", seconds=10, client=None, order={"round": 3, "order": "o1"},
                    policy={**TERMS, "accept": {}})
            row = json.loads(path.read_text())
            self.assertEqual(row["negotiation_diagnostics"]["reason"], "seller_rejected")
            self.assertEqual(set(result), {"shop", "receipt", "stars", "review", "accept"})

    def test_selection_failure_has_separate_safe_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            order = root / "order.json"
            order.write_text(json.dumps({"round": 3, "order": "o1", "item": "goods", "budget": 123456}))
            memory = root / "memory.jsonl"
            with patch("market_buyer.read_shops", side_effect=BuyerError("secret text")), contextlib.redirect_stdout(io.StringIO()):
                code = main(["--shops", str(root / "shops.json"), "--item", "goods", "--budget", "123456",
                             "--order", str(order), "--memory", str(memory)])
            self.assertEqual(code, 1)
            event = (root / "buyer-events.jsonl").read_text()
            self.assertNotIn("secret", event)
            self.assertNotIn("123456", event)
            self.assertFalse(memory.exists())
            self.assertEqual(json.loads(event)["stage"], "selection_setup")


class LocalDiagnosticsTests(unittest.TestCase):
    NOW = 100000

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = {"memory": self.root / "data/market-memory.jsonl",
                      "payments": self.root / ".market/payments.jsonl",
                      "reviews": self.root / "logs/settled-deals.jsonl"}
        self.record = {"time": datetime.fromtimestamp(self.NOW - 30, timezone.utc).isoformat(),
                       "round": 3, "order": "o1", "url": "https://shop.invalid", "name": "shop", "item": "G1",
                       "price": 50, "result": "receipt_issued", "observed_receipt": {"task": "t1", "item": "G1", "price": 50, "unit": "u1"}}
        self.payment = {"order": "o1", "shop": "https://shop.invalid", "item": "G1", "price": 50, "paid": True, "ledger": 17}
        self.review = {"order": "o1", "receipt": self.record["observed_receipt"],
                       "settled": {"ledger": 17, "shop": self.record["url"], "sku": "G1", "unit": "u1"}}

    def write(self, name, rows):
        path = self.paths[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def report(self, **kwargs):
        return summarize(self.root, now=self.NOW, hours=1, **kwargs)

    def test_duplicate_memory_payments_reviews_count_one_confirmed_sale(self):
        self.write("memory", [self.record, self.record])
        self.write("payments", [self.payment, self.payment])
        self.write("reviews", [self.review, self.review])
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.paths.values()}
        report = self.report()
        self.assertEqual(report["attempts"], 1)
        self.assertEqual(report["confirmed_settlements"], 1)
        self.assertEqual(report["excluded_rows"]["duplicate_attempt_rows"], 1)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.paths.values()})

    def test_receipt_without_matching_ledger_is_unconfirmed(self):
        self.write("memory", [self.record])
        for payment in ({**self.payment, "ledger": None}, {**self.payment, "price": 49}, {**self.payment, "round": 2}):
            with self.subTest(payment=payment):
                self.write("payments", [payment])
                self.assertEqual(self.report()["settlement_status"], {"receipt_unconfirmed": 1})

    def test_review_exact_task_can_confirm_missing_payment_tail(self):
        self.write("memory", [self.record])
        self.write("reviews", [self.review])
        self.assertEqual(self.report()["confirmed_settlements"], 1)
        self.write("reviews", [{**self.review, "receipt": {**self.review["receipt"], "task": "other"}}])
        self.assertEqual(self.report()["confirmed_settlements"], 0)

    def test_old_round_out_of_window_future_and_undated_attempts_are_excluded(self):
        rows = [self.record, {**self.record, "round": 2}, {**self.record, "time": "invalid"},
                {**self.record, "time": datetime.fromtimestamp(self.NOW - 4000, timezone.utc).isoformat()},
                {**self.record, "time": datetime.fromtimestamp(self.NOW + 1, timezone.utc).isoformat()}]
        self.write("memory", rows)
        self.write("payments", [self.payment])
        report = self.report()
        self.assertEqual(report["attempts"], 1)
        self.assertEqual(report["confirmed_settlements"], 1)
        self.assertEqual(sum(report["excluded_rows"].values()), 4)

    def test_legacy_reason_is_unknown_not_inferred_from_text(self):
        self.write("memory", [{**self.record, "result": "no_deal", "detail": "out of stock, price too high"}])
        report = self.report()
        self.assertEqual(report["terminal_reasons"], {"unknown": 1})
        self.assertEqual(report["seller_reported_unconfirmed"], {})

    def test_seller_stock_tag_remains_separate_from_reason(self):
        self.write("memory", [{**self.record, "result": "no_deal", "negotiation_diagnostics": {
            "version": 1, "reason": "seller_rejected", "stage": "seller_terminal", "decisions": 0,
            "seller_terminal": {"source": "untrusted_seller", "reported_tags": ["stock"]}}}])
        report = self.report()
        self.assertEqual(report["terminal_reasons"], {"seller_rejected": 1})
        self.assertEqual(report["seller_reported_unconfirmed"], {"stock": 1})

    def test_refusal_does_not_overwrite_later_confirmed_ledger(self):
        self.write("memory", [self.record])
        self.write("payments", [{**self.payment, "paid": False}, self.payment])
        self.assertEqual(self.report()["confirmed_settlements"], 1)
        self.write("payments", [{**self.payment, "paid": False}])
        self.assertEqual(self.report()["settlement_status"], {"payment_refusal_observed": 1})

    def test_conflicting_ledgers_or_reused_ledger_never_double_count(self):
        self.write("memory", [self.record])
        self.write("payments", [self.payment, {**self.payment, "ledger": 18}])
        self.assertEqual(self.report()["confirmed_settlements"], 0)
        other = {**self.record, "order": "o2", "observed_receipt": {**self.record["observed_receipt"], "task": "t2"}}
        self.write("memory", [self.record, other])
        self.write("payments", [self.payment, {**self.payment, "order": "o2"}])
        self.assertEqual(self.report()["confirmed_settlements"], 0)

    def test_ambiguous_payment_without_task_needs_exact_review(self):
        other = {**self.record, "observed_receipt": {**self.record["observed_receipt"], "task": "t2"}}
        self.write("memory", [self.record, other])
        self.write("payments", [self.payment])
        self.assertEqual(self.report()["confirmed_settlements"], 0)
        self.write("reviews", [self.review])
        self.assertEqual(self.report()["confirmed_settlements"], 1)

    def test_malformed_structured_fields_do_not_crash_or_leak(self):
        self.write("memory", [{**self.record, "item": {}, "negotiation_diagnostics": {
            "version": 1, "reason": ["secret"], "stage": "secret", "seller_terminal": {
                "source": "untrusted_seller", "reported_tags": [["bad"]]}}}])
        report = self.report()
        self.assertEqual(report["terminal_reasons"], {"unknown": 1})
        self.assertNotIn("secret", json.dumps(report))
        self.assertEqual(report["confirmed_settlements"], 0)

    def test_tail_coverage_reports_partial_malformed_and_row_limit(self):
        path = self.root / "tail.jsonl"
        path.write_bytes(b'{"a":1}\nnot-json\n{"a":2}\n{"partial"')
        rows, status = read_tail(path, row_limit=2)
        self.assertEqual(rows, [{"a": 2}])
        self.assertEqual(status["malformed"], 1)
        self.assertTrue(status["row_truncated"])
        self.assertTrue(status["partial_tail"])
        _, status = read_tail(path, byte_limit=20)
        self.assertTrue(status["byte_truncated"])


if __name__ == "__main__":
    unittest.main()
