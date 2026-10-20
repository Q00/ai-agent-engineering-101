"""Model-free tests for the bounded live-trade diagnostic."""
import math
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import buyer
import diagnostic_trade as diagnostic
import seller
from harness import BuyerDecisionContext, SellerDecisionContext


class FakeClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class DiagnosticGuardTests(unittest.TestCase):
    def test_five_call_limit_is_shared_across_roles(self):
        guard = diagnostic.CallGuard(max_calls=5)
        for role in ("seller", "buyer", "seller", "buyer", "seller"):
            guard.claim(role)
        with self.assertRaisesRegex(diagnostic.DiagnosticLimitError, "MODEL_CALL_LIMIT"):
            guard.claim("seller")
        self.assertEqual((guard.total_calls, guard.seller_calls, guard.buyer_calls), (5, 3, 2))

    def test_two_buyer_round_limit(self):
        guard = diagnostic.CallGuard(max_calls=5, max_buyer_rounds=2)
        guard.claim("buyer")
        guard.claim("buyer")
        with self.assertRaisesRegex(diagnostic.DiagnosticLimitError, "BUYER_ROUND_LIMIT"):
            guard.claim("buyer")
        self.assertEqual(guard.total_calls, 2)

    def test_monotonic_deadline_blocks_call_before_counting(self):
        clock = FakeClock()
        guard = diagnostic.CallGuard(timeout_seconds=180, clock=clock)
        clock.value = 270.0
        with self.assertRaisesRegex(diagnostic.DiagnosticLimitError, "DIAGNOSTIC_TIMEOUT"):
            guard.claim("seller")
        self.assertEqual(guard.total_calls, 0)

    def test_remaining_time_is_passed_to_seller_harness(self):
        clock = FakeClock()
        guard = diagnostic.CallGuard(timeout_seconds=180, clock=clock)
        clock.value += 71.2
        context = SellerDecisionContext("lamp", 100, "opening", None)
        decision = {"action": "ask", "price": 150, "reason": "opening"}
        with patch("diagnostic_trade.harness.decide_seller", return_value=decision) as call:
            guard.decide_seller(context)
        self.assertEqual(call.call_args.args[1].timeout_seconds, 98)

    def test_remaining_time_is_passed_to_buyer_harness(self):
        clock = FakeClock()
        guard = diagnostic.CallGuard(timeout_seconds=180, clock=clock)
        clock.value += 100.1
        context = BuyerDecisionContext("lamp", 120, 150, "task", 120)
        decision = {"action": "offer", "price": 110, "reason": "offer"}
        with patch("diagnostic_trade.harness.decide_buyer", return_value=decision) as call:
            guard.decide_buyer(context)
        self.assertEqual(call.call_args.args[1].timeout_seconds, 69)

    def test_default_buyer_rounds_are_not_changed(self):
        self.assertEqual(buyer.MAX_ROUNDS, 8)

    def test_model_error_category_is_recorded_without_raw_stderr(self):
        guard = diagnostic.CallGuard()
        context = SellerDecisionContext("lamp", 100, "opening", None)
        error = diagnostic.harness.HarnessError(
            "secret raw stderr", error_type=diagnostic.harness.ErrorType.NETWORK_ERROR
        )
        with patch("diagnostic_trade.harness.decide_seller", side_effect=error):
            with self.assertRaises(diagnostic.harness.HarnessError):
                guard.decide_seller(context)
        self.assertEqual(guard.model_error_type, "NETWORK_ERROR")
        self.assertEqual(len(guard.turns), 1)
        self.assertEqual(guard.turns[0].termination_reason, "model_error")
        self.assertIsNone(guard.turns[0].action)
        self.assertIsNone(guard.turns[0].a2a_state)
        self.assertNotIn("secret", json.dumps(diagnostic.asdict(guard.turns[0])))

    def test_round_limit_records_allow_listed_five_turn_trace(self):
        guard = diagnostic.CallGuard()
        seller_context = SellerDecisionContext("lamp", 100, "opening", None)
        buyer_context = BuyerDecisionContext("lamp", 120, 150, "task", 120)
        seller_decisions = [
            {"action": "ask", "price": 150, "reason": "not recorded opening detail"},
            {"action": "counter", "price": 130, "reason": "not recorded counter detail"},
            {"action": "counter", "price": 125, "reason": "not recorded final detail"},
        ]
        buyer_decisions = [
            {"action": "offer", "price": 110, "reason": "not recorded offer detail"},
            {"action": "offer", "price": 120, "reason": "not recorded final offer detail"},
        ]
        with patch("diagnostic_trade.harness.decide_seller", side_effect=seller_decisions) as seller_call, \
             patch("diagnostic_trade.harness.decide_buyer", side_effect=buyer_decisions) as buyer_call:
            guard.decide_seller(seller_context)
            guard.decide_buyer(buyer_context)
            guard.decide_seller(seller_context)
            guard.decide_buyer(buyer_context)
            guard.decide_seller(seller_context)

        trace = [diagnostic.asdict(turn) for turn in guard.turns]
        self.assertEqual([turn["turn"] for turn in trace], [1, 2, 3, 4, 5])
        self.assertEqual([turn["role"] for turn in trace],
                         ["Seller", "Buyer", "Seller", "Buyer", "Seller"])
        self.assertEqual([turn["model_calls"] for turn in trace], [1, 2, 3, 4, 5])
        self.assertEqual(trace[-1]["a2a_state"], buyer.INPUT_REQUIRED)
        self.assertEqual(trace[-1]["termination_reason"], "round_limit")
        self.assertTrue(all("reason" not in turn for turn in trace))
        seller_contexts = [call.args[0] for call in seller_call.call_args_list]
        buyer_contexts = [call.args[0] for call in buyer_call.call_args_list]
        self.assertEqual([item.remaining_buyer_decisions for item in seller_contexts], [2, 1, 0])
        self.assertEqual([item.is_final_decision for item in seller_contexts], [False, False, True])
        self.assertEqual([item.remaining_buyer_decisions for item in buyer_contexts], [1, 0])
        self.assertEqual([item.is_final_decision for item in buyer_contexts], [False, True])


class ReceiptTests(unittest.TestCase):
    def test_integer_valued_float_is_accepted(self):
        self.assertEqual(diagnostic.integral_price(110.0), 110)

    def test_bool_is_rejected(self):
        with self.assertRaises(ValueError):
            diagnostic.integral_price(True)

    def test_fractional_and_non_finite_values_are_rejected(self):
        for value in (110.5, math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                diagnostic.integral_price(value)


class OutcomeTests(unittest.TestCase):
    def test_success_is_distinct_from_normal_no_deal(self):
        guard = diagnostic.CallGuard()
        success = diagnostic.classify_outcome(buyer.BuyerOutcome("deal", "t", 110), guard)
        no_deal = diagnostic.classify_outcome(buyer.BuyerOutcome("walked_away", "t"), guard)
        self.assertTrue(success.purchase_succeeded)
        self.assertEqual(success.outcome, "purchase_success")
        self.assertFalse(no_deal.purchase_succeeded)
        self.assertEqual(no_deal.outcome, "walked_away")

    def test_normal_terminal_causes_remain_distinct(self):
        for cause in ("rejected", "canceled", "walked_away", "round_limit"):
            with self.subTest(cause=cause):
                result = diagnostic.classify_outcome(buyer.BuyerOutcome(cause, "t"), diagnostic.CallGuard())
                self.assertEqual(result.outcome, cause)
                self.assertFalse(result.purchase_succeeded)

    def test_unknown_outcome_is_program_error(self):
        result = diagnostic.classify_outcome(buyer.BuyerOutcome("unexpected"), diagnostic.CallGuard())
        self.assertEqual(result.outcome, "program_error")


class DiagnosticPathTests(unittest.TestCase):
    def test_runner_shares_guard_and_restores_submission_defaults(self):
        calls = []

        class FakeSeller:
            url = "http://127.0.0.1:1"

            def __enter__(self):
                calls.append("server_start")
                return self

            def __exit__(self, *args):
                calls.append("server_stop")

        class FakeBuyer:
            def __init__(self, url):
                self.url = url

            def __enter__(self):
                return self

            def __exit__(self, *args):
                calls.append("buyer_close")

            def buy(self, item, budget):
                self.assert_limits()
                seller.decide_seller(SellerDecisionContext("lamp", 100, "opening", None))
                buyer.decide_buyer(BuyerDecisionContext("lamp", 120, 110, "task", 120))
                seller.decide_seller(SellerDecisionContext("lamp", 100, "negotiating", 110))
                return buyer.BuyerOutcome("deal", "task", 110.0)

            @staticmethod
            def assert_limits():
                if buyer.MAX_ROUNDS != 2 or buyer.RUN_TIMEOUT_SECONDS != 180:
                    raise AssertionError("diagnostic limits were not installed")

        with (
            patch("diagnostic_trade._RunningSeller", FakeSeller),
            patch("diagnostic_trade.buyer.BuyerClient", FakeBuyer),
            patch("diagnostic_trade.harness.decide_seller", side_effect=[
                {"action": "ask", "price": 150, "reason": "open"},
                {"action": "accept", "price": 110, "reason": "deal"},
            ]) as seller_call,
            patch("diagnostic_trade.harness.decide_buyer", return_value={
                "action": "accept", "price": 110, "reason": "within budget",
            }) as buyer_call,
            patch("diagnostic_trade.harness.subprocess.Popen", side_effect=AssertionError("real subprocess")),
        ):
            result = diagnostic.run_diagnostic()

        self.assertTrue(result.purchase_succeeded)
        self.assertEqual((result.model_calls, result.seller_calls, result.buyer_calls), (3, 2, 1))
        self.assertEqual(len(result.turns), 3)
        self.assertEqual(result.turns[-1].a2a_state, buyer.COMPLETED)
        self.assertEqual(result.turns[-1].termination_reason, "deal_completed")
        self.assertEqual((seller_call.call_count, buyer_call.call_count), (2, 1))
        self.assertEqual(calls, ["server_start", "buyer_close", "server_stop"])
        self.assertEqual((buyer.MAX_ROUNDS, buyer.RUN_TIMEOUT_SECONDS), (8, 240))

    def test_seller_safe_rejection_after_model_failure_is_not_normal_no_deal(self):
        guard = diagnostic.CallGuard()
        guard.model_error_type = "NETWORK_ERROR"

        class FakeSeller:
            url = "http://127.0.0.1:1"
            def __enter__(self): return self
            def __exit__(self, *args): pass

        class FakeBuyer:
            def __init__(self, url): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def buy(self, item, budget): return buyer.BuyerOutcome("no_deal", "task")

        with patch("diagnostic_trade._RunningSeller", FakeSeller), \
             patch("diagnostic_trade.buyer.BuyerClient", FakeBuyer):
            with self.assertRaises(diagnostic.RecordedModelError) as caught:
                diagnostic.run_diagnostic(guard)
        self.assertEqual(caught.exception.error_type, "NETWORK_ERROR")


class DiagnosticCliTests(unittest.TestCase):
    def run_main(self, result=None, error=None):
        stream = StringIO()
        side_effect = error if error is not None else None
        with (
            patch("diagnostic_trade.run_diagnostic", return_value=result, side_effect=side_effect),
            patch("diagnostic_trade.signal.signal", return_value=signal_handler) as install,
            patch("diagnostic_trade.signal.setitimer") as timer,
            redirect_stdout(stream),
        ):
            code = diagnostic.main()
        payload = json.loads(stream.getvalue())
        return code, payload, install, timer

    def test_success_is_json_and_arms_watchdog(self):
        turn = diagnostic.DiagnosticTurn(
            1, "Seller", "accept", 110, buyer.COMPLETED, "deal_completed", 3
        )
        result = diagnostic.DiagnosticResult("purchase_success", True, 110, 3, 2, 1, (turn,))
        code, payload, install, timer = self.run_main(result=result)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["model_calls"], 3)
        self.assertEqual(payload["price"], 110)
        self.assertEqual(payload["turns"], [diagnostic.asdict(turn)])
        self.assertEqual(timer.call_args_list[0].args, (diagnostic.signal.ITIMER_REAL, 170))
        self.assertEqual(timer.call_args_list[-1].args, (diagnostic.signal.ITIMER_REAL, 0))
        self.assertEqual(install.call_count, 2)

    def test_failure_still_records_shared_counts_without_raw_message(self):
        error = RuntimeError("secret raw failure")

        def fail(guard):
            guard.claim("seller")
            guard.claim("buyer")
            raise error

        stream = StringIO()
        with (
            patch("diagnostic_trade.run_diagnostic", side_effect=fail),
            patch("diagnostic_trade.signal.signal", return_value=signal_handler),
            patch("diagnostic_trade.signal.setitimer"),
            redirect_stdout(stream),
        ):
            code = diagnostic.main()
        output = stream.getvalue()
        payload = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "program_error")
        self.assertEqual((payload["model_calls"], payload["seller_calls"], payload["buyer_calls"]), (2, 1, 1))
        self.assertEqual(payload["turns"], [])
        self.assertNotIn("secret raw failure", output)

    def test_program_error_and_timeout_payloads_preserve_completed_turns(self):
        guard = diagnostic.CallGuard()
        decision = {"action": "ask", "price": 150, "reason": "must not be recorded"}
        context = SellerDecisionContext("lamp", 100, "opening", None)
        with patch("diagnostic_trade.harness.decide_seller", return_value=decision):
            guard.decide_seller(context)

        for error in (RuntimeError("private detail"),
                      diagnostic.DiagnosticLimitError("DIAGNOSTIC_TIMEOUT")):
            with self.subTest(error=type(error).__name__):
                payload = diagnostic._failure_payload(error, guard, 1.0)
                self.assertEqual(len(payload["turns"]), 1)
                self.assertEqual(payload["turns"][0]["action"], "ask")
                self.assertNotIn("must not be recorded", json.dumps(payload))
        self.assertEqual(
            diagnostic._failure_payload(
                diagnostic.DiagnosticLimitError("DIAGNOSTIC_TIMEOUT"), guard, 1.0
            )["status"],
            "timeout",
        )

    def test_watchdog_timeout_has_exit_124_and_counts(self):
        code, payload, _, _ = self.run_main(
            error=diagnostic.DiagnosticLimitError("DIAGNOSTIC_TIMEOUT")
        )
        self.assertEqual(code, 124)
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["error_type"], "DIAGNOSTIC_TIMEOUT")
        self.assertIn("turns", payload)


signal_handler = object()


if __name__ == "__main__":
    unittest.main()
