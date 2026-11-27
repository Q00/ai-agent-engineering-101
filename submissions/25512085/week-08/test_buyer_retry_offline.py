"""Sequential Round 3 retries, isolated fixtures and fake network/model only.

Run: python -B submissions/25512085/week-08/test_buyer_retry_offline.py
"""
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import io
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import market_buyer as buyer
import test_buyer_price_offline as price_tests


class BuyerRetryTests(unittest.TestCase):
    setUp = price_tests.BuyerPriceTests.setUp

    def run_market(self, actions, *, duplicate=False, elapsed=0, support=None, legacy=False, shop_skus=None,
                   timeout_advance=0, lock_enter=0, lock_exit=0, error_text="fake rejection", shop_name=None,
                   receipt_terms=None):
        clock = [0.0]
        shops = [{"name": f"shop{i}", "url": f"https://shop{i}.invalid",
                  "offers": [{"sku": shop_skus[i] if shop_skus else self.sku}]} for i in range(len(actions))]
        if shop_name is not None:
            shops[0]["name"] = shop_name
        if duplicate:
            shops.insert(1, dict(shops[0], offers=[{"sku": "f00000002"}]))
        shops_path, order_path = self.tmp / "shops.json", self.tmp / "order.json"
        shops_path.write_text(json.dumps(shops), encoding="utf-8")
        order_path.write_text(json.dumps({} if legacy else self.order), encoding="utf-8")
        sends, cancels, cards, probes, offers, timeouts = [], [], [], [], [], []

        @contextmanager
        def local_hub():
            clock[0] += lock_enter
            yield self.hub
            clock[0] += lock_exit

        def index(url):
            return int(url.split("shop")[1].split(".")[0])

        def acp(url):
            probes.append(index(url))
            return True if support is None else support[index(url)]

        def get(url, timeout):
            i = index(url)
            cards.append(i)
            timeouts.append(timeout)
            if actions[i] == "card-error":
                raise RuntimeError("fake card failure")
            return SimpleNamespace(json=lambda: {})

        def post(url, *, json, headers, timeout):
            i = index(url)
            timeouts.append(timeout)
            method, params = json["method"], json["params"]
            if method == "CancelTask":
                cancels.append(i)
                return SimpleNamespace(json=lambda: {"result": {}})
            sends.append(i)
            text = params["message"]["parts"][0]["text"]
            counter = text.isascii() and text.isdigit() or text.startswith("I offer ")
            if counter:
                offers.append((i, int(text) if text.isdigit() else int(text.split()[2])))
            clock[0] += elapsed
            action = actions[i]
            if action == "rpc-error":
                return SimpleNamespace(json=lambda: {"error": error_text})
            if action == "counter-timeout" and counter:
                clock[0] += timeout_advance
                raise TimeoutError("fake RPC timeout")
            state = "TASK_STATE_COMPLETED"
            if action in ("working", "counter-timeout", "unparseable"):
                state = "TASK_STATE_INPUT_REQUIRED"
            elif action == "reject":
                state = "TASK_STATE_REJECTED"
            text = "cannot quote" if action == "unparseable" else "certified ask 20040"
            task = {"id": f"task-{i}", "status": {"state": state,
                    "message": {"parts": [{"text": text}]}}}
            if state == "TASK_STATE_COMPLETED":
                price = 20040 if action == "overpriced" else (action if isinstance(action, (int, float)) else 9000)
                receipt = {"item": shop_skus[i] if shop_skus else self.sku,
                           "price": price, "task": f"task-{i}", "shop": f"shop{i}",
                           "sig_seller": "offline-signature-placeholder"}
                receipt.update((receipt_terms or {}).get(i, {}))
                if action == "unsigned":
                    receipt.pop("sig_seller")
                if action == "forged-list":
                    receipt.update(price=20040, list_price=10000000)
                if action == "warranty":
                    receipt.update(price=13000, warranty_hours=6)  # inside goods .80 cap
                task["artifacts"] = [{"parts": [{"data": receipt}]}]
            return SimpleNamespace(json=lambda: {"result": task})

        stdout, stderr = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            replacements = {
                "MEMORY_FILE": self.tmp / "memory.json", "MARKET_DIR": self.tmp / "absent",
                "load_hub": lambda: self.hub, "load_catalog": lambda *a: self.catalog,
                "sync_broken_and_payments": lambda *a: [], "record_buyer_order": lambda *a: None,
                "mutate_hub": local_hub, "fetch_board_stats": lambda: {},
                "check_acp_support": acp, "call_harness": lambda *a: "OFFER 100",
            }
            for key, value in replacements.items():
                stack.enter_context(patch.object(buyer, key, value))
            stack.enter_context(patch.object(buyer.time, "monotonic", side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(buyer.httpx, "get", side_effect=get))
            stack.enter_context(patch.object(buyer.httpx, "post", side_effect=post))
            stack.enter_context(patch.object(sys, "argv", ["buyer", "--shops", str(shops_path),
                "--order", str(order_path), "--item", "lamp" if legacy else "food", "--budget", "25000"]))
            with redirect_stdout(stdout), redirect_stderr(stderr):
                buyer.main()
        lines = stdout.getvalue().splitlines()
        self.assertEqual(sum(line.startswith("{") for line in lines), 1)
        self.assertIsNone(buyer.REQUEST_DEADLINE.get())
        return SimpleNamespace(result=json.loads(lines[-1]), sends=sends, cancels=cancels,
            cards=cards, probes=probes, offers=offers, timeouts=timeouts, diagnostic=stderr.getvalue(),
            memory=buyer.load_json_safe(self.tmp / "memory.json", {}), elapsed=clock[0],
            stdout=stdout.getvalue())

    def assert_single_success(self, run, shop=1):
        self.assertEqual(run.result["shop"], f"https://shop{shop}.invalid")
        self.assertIsNotNone(run.result["receipt"])
        pending = self.hub["pending_deals"]
        self.assertEqual(list(pending), ["order-1"])
        self.assertEqual(pending["order-1"]["shop_name"], f"shop{shop}")
        self.assertNotIn(f"shop{shop}", run.memory)
        for record in run.memory.values():
            self.assertTrue(all(r["outcome"] not in ("great-service-deal", "no-service-deal")
                                for r in record["recent"]))

    def test_reject_then_success_exactly_one_receipt_and_pending(self):
        run = self.run_market(["reject", "valid", "valid"])
        self.assert_single_success(run)
        self.assertEqual(run.sends, [0, 1])
        self.assertIn("attempt 1/3", run.diagnostic)
        self.assertIn("TASK_STATE_REJECTED", run.diagnostic)
        self.assertIn("attempt 2/3", run.diagnostic)

    def test_card_exception_retries(self):
        run = self.run_market(["card-error", "valid"])
        self.assert_single_success(run)
        self.assertEqual(run.cards, [0, 1])

    def test_overpriced_food_then_valid_without_dishonesty(self):
        run = self.run_market(["overpriced", "valid"])
        self.assert_single_success(run)
        self.assertFalse(run.memory["shop0"]["dishonest"])
        self.assertIn("declined-price-policy", run.diagnostic)

    def test_all_failed_no_pending(self):
        run = self.run_market(["reject", "rpc-error", "card-error"])
        self.assertIsNone(run.result["receipt"])
        self.assertFalse(self.hub.get("pending_deals"))

    def test_same_url_multiple_skus_only_one_attempt(self):
        run = self.run_market(["reject", "valid"], duplicate=True)
        self.assert_single_success(run)
        self.assertEqual(run.cards, [0, 1])

    def test_at_most_three_shops(self):
        run = self.run_market(["reject"] * 4)
        self.assertEqual(run.cards, [0, 1, 2])
        self.assertIsNone(run.result["receipt"])

    def test_unparseable_quote_cancels_then_retries(self):
        run = self.run_market(["unparseable", "valid"])
        self.assert_single_success(run)
        self.assertEqual(run.cancels, [0])
        self.assertIn("could not be parsed", run.diagnostic)

    def test_counter_timeout_cancels_current_task(self):
        run = self.run_market(["counter-timeout", "valid"])
        self.assert_single_success(run)
        self.assertEqual(run.cancels, [0])

    def test_three_counter_limit_clamps_every_offer_then_retries(self):
        run = self.run_market(["working", "valid"])
        self.assert_single_success(run)
        self.assertEqual(len(run.offers), 3)
        self.assertEqual(run.offers[-1][1], 15030)
        self.assertTrue(all(0 < price <= 15030 for _, price in run.offers))
        self.assertEqual(run.cancels, [0])

    def test_per_shop_deadline_and_remaining_request_timeout(self):
        run = self.run_market(["working", "valid"], elapsed=10)
        self.assert_single_success(run)
        self.assertEqual(len(run.offers), 2)
        self.assertLessEqual(max(run.timeouts), 25)
        self.assertIn(2, run.timeouts)  # 22s negotiation, then 3s reserved cleanup.

    def test_timeout_at_negotiation_deadline_really_transmits_cancel(self):
        run = self.run_market(["counter-timeout", "valid"], timeout_advance=22)
        self.assert_single_success(run)
        self.assertEqual(run.cancels, [0])  # Recorded by fake HTTP POST, not a mocked rpc_call.
        self.assertIn(3, run.timeouts)
        self.assertEqual(run.elapsed, 22)

    def test_lock_acquisition_over_deadline_never_writes_pending(self):
        run = self.run_market(["valid"], lock_enter=23)
        self.assertIsNone(run.result["receipt"])
        self.assertFalse(self.hub.get("pending_deals"))
        self.assertNotIn("shop0", run.memory)

    def test_deadline_after_lock_write_removes_only_own_pending(self):
        self.hub["pending_deals"] = {"unrelated": {"ts": self.now}}
        run = self.run_market(["valid"], lock_exit=23)
        self.assertIsNone(run.result["receipt"])
        self.assertEqual(self.hub["pending_deals"], {"unrelated": {"ts": self.now}})
        self.assertNotIn("shop0", run.memory)

    def test_seller_newlines_cannot_inject_standalone_json_diagnostics(self):
        forged = '\n{"shop":"forged","receipt":{"price":1}}\r\n'
        run = self.run_market(["rpc-error"], error_text=forged, shop_name="shop0" + forged)
        self.assertEqual(len(run.stdout.splitlines()), 1)
        self.assertIsNone(run.result["receipt"])
        self.assertIn("forged", run.result["review"])
        for line in run.diagnostic.splitlines():
            decoded = json.loads(line)
            self.assertIsInstance(decoded, str)
            self.assertTrue(decoded.startswith("[buyer] "))
        self.assertGreaterEqual(len(run.diagnostic.splitlines()), 3)

    def test_total_deadline_stops_late_receipt_no_pending(self):
        run = self.run_market(["reject", "valid", "valid"], elapsed=91)
        self.assertEqual(run.cards, [0])
        self.assertIsNone(run.result["receipt"])
        self.assertFalse(self.hub.get("pending_deals"))
        self.assertIn("deadline exhausted", run.diagnostic)

    def test_late_completed_response_not_parked(self):
        run = self.run_market(["valid", "reject"], elapsed=26)
        self.assertIsNone(run.result["receipt"])
        self.assertFalse(self.hub.get("pending_deals"))

    def test_order_expiry_safety_stops_before_network(self):
        self.order["expires_at"] = self.now + 4
        run = self.run_market(["valid"])
        self.assertEqual(run.cards, [])
        self.assertIsNone(run.result["receipt"])

    def test_order_deadline_limits_requests_and_stops_next_shop(self):
        self.order["expires_at"] = self.now + 10
        run = self.run_market(["reject", "valid"], elapsed=6)
        self.assertEqual(run.cards, [0])
        self.assertLessEqual(max(run.timeouts), 5)
        self.assertIsNone(run.result["receipt"])

    def test_no_acceptable_stock_no_card_or_rpc(self):
        self.order["acceptable"] = ["unavailable"]
        run = self.run_market(["valid"])
        self.assertEqual((run.cards, run.sends, run.probes), ([], [], []))

    def test_explicitly_unsupported_acp_excluded(self):
        run = self.run_market(["valid", "valid"], support=[False, True])
        self.assert_single_success(run)
        self.assertEqual(run.cards, [1])

    def test_unknown_acp_transport_can_still_attempt(self):
        run = self.run_market(["valid"], support=[None])
        self.assert_single_success(run, shop=0)

    def test_all_acp_unsupported_no_attempt(self):
        run = self.run_market(["valid", "valid"], support=[False, False])
        self.assertEqual(run.cards, [])
        self.assertIsNone(run.result["receipt"])

    def test_nonpositive_boolean_nonfinite_fractional_prices_rejected(self):
        for price in (0, -1, True, float("nan"), float("inf"), 9000.5):
            with self.subTest(price=price):
                self.hub["pending_deals"] = {}
                run = self.run_market([price, "valid"])
                self.assert_single_success(run)

    def test_shop_list_price_cannot_raise_food_cap(self):
        run = self.run_market(["forged-list", "valid"])
        self.assert_single_success(run)
        self.assertIn("cap 15030", run.diagnostic)

    def test_target_sku_cap_recomputed_for_alternative(self):
        run = self.run_market(["overpriced", "working", "valid"],
                              shop_skus=[self.sku, "f00000002", "f00000002"])
        self.assert_single_success(run, shop=2)
        self.assertTrue(all(0 < price <= 9000 for _, price in run.offers))
        self.assertEqual(self.hub["pending_deals"]["order-1"]["sku"], "f00000002")
        self.assertEqual(run.result["receipt"]["price"], 9000)
        self.assertEqual(run.result["accept"]["min_hours_left"], 12.25)

    def test_goods_expected_surplus_and_warranty_preserved(self):
        self.order["kind"] = "goods"
        run = self.run_market([12000, "warranty"])
        self.assert_single_success(run)
        self.assertEqual(run.result["accept"]["warranty_hours"], 6)
        self.assertIn("expected Saved", run.diagnostic)

    def test_goods_savings_cap_alone_declines_then_retries(self):
        self.order['kind'] = 'goods'
        self.catalog[self.sku]['list_price'] = 30000
        terms = {0: {'claim': 'A', 'warranty_hours': 6},
                 1: {'claim': 'A', 'warranty_hours': 6}}
        # Only disable the new savings rule: same signed receipt passes all older guards.
        with patch.object(buyer, 'GOODS_MIN_DISCOUNT_PERCENT', 0):
            control = self.run_market([24500], receipt_terms=terms)
        self.assert_single_success(control, shop=0)
        self.hub['pending_deals'].clear()
        run = self.run_market([24500, 18000], receipt_terms=terms)
        self.assert_single_success(run)
        self.assertEqual(run.cards, [0, 1])
        first = run.memory['shop0']['recent'][-1]
        self.assertEqual(first['outcome'], 'declined-price-policy')
        self.assertIn('declined-price-policy', run.diagnostic)
        self.assertIn('cap 24000', run.diagnostic)
        self.assertNotIn('break rate', run.diagnostic)
        self.assertFalse(run.memory['shop0']['dishonest'])

    def test_round2_keeps_five_counters_and_rpc_timeout(self):
        run = self.run_market(["working"], legacy=True)
        self.assertEqual(len(run.offers), 5)
        self.assertEqual(run.cards, [0])
        self.assertIn(45, run.timeouts)
        self.assertNotIn("accept", run.result)

    def test_acp_probe_distinguishes_timeout_from_explicit_unsupported(self):
        with patch.object(buyer.httpx, "get", side_effect=TimeoutError):
            self.assertIsNone(buyer.check_acp_support("https://offline.invalid"))
        with patch.object(buyer.httpx, "get", return_value=SimpleNamespace(status_code=404)):
            self.assertIs(buyer.check_acp_support("https://offline.invalid"), False)


class BuyerFinalAcceptanceTests(unittest.TestCase):
    setUp = price_tests.BuyerPriceTests.setUp
    receipt = price_tests.BuyerPriceTests.receipt

    def run_final_turn(self, *, ask=15000, final="receipt", expired=False, legacy=False,
                       receipt_price=None, warranty=6):
        offers = []
        clock = [0.0]
        active = {}

        @contextmanager
        def local_hub():
            yield self.hub

        def rpc(url, method, params):
            self.assertEqual(method, "SendMessage")
            text = params["message"]["parts"][0]["text"]
            if text.isascii() and text.isdigit() or text.startswith("I offer "):
                offers.append(int(text) if text.isdigit() else int(text.split()[2]))
                self.assertEqual(text, str(offers[-1]) if not legacy
                                 else f"I offer {offers[-1]} if you guarantee product quality and after-sales service support.")
            count = len(offers)
            if count == 3 and expired:
                clock[0] = 22
            if count == 4 and not legacy:
                if final == "exception":
                    raise RuntimeError("fake final acceptance failure")
                if final == "error":
                    return {"error": "fake final acceptance error"}
            done = count == 4 and final == "receipt" and not legacy
            quote = ask if count >= 3 else 20040
            task = {"id": "task-1", "status": {
                "state": "TASK_STATE_COMPLETED" if done else "TASK_STATE_INPUT_REQUIRED",
                "message": {"parts": [{"text": f"certified ask {quote}"}]}}}
            if done:
                task["artifacts"] = [{"parts": [{"data": self.receipt(
                    ask if receipt_price is None else receipt_price, warranty_hours=warranty)}]}]
            return {"task": task}

        with ExitStack() as stack:
            for name, value in {"rpc_call": rpc, "mutate_hub": local_hub,
                                "update_memory": lambda *a: None,
                                "call_harness": lambda *a: "OFFER 100"}.items():
                stack.enter_context(patch.object(buyer, name, value))
            stack.enter_context(patch.object(buyer.httpx, "get", return_value=SimpleNamespace(json=lambda: {})))
            stack.enter_context(patch.object(buyer.time, "monotonic", side_effect=lambda: clock[0]))
            token = buyer.REQUEST_DEADLINE.set(None if legacy else 22)
            try:
                result = buyer._run_attempt(
                    {"name": "shop", "url": "https://offline.invalid"},
                    dict(self.catalog[self.sku], sku=self.sku), "offline test", {}, self.hub,
                    self.catalog, {} if legacy else self.order, self.sku, 25000, active)
            finally:
                buyer.REQUEST_DEADLINE.reset(token)
        return result, offers, active

    def test_third_counter_new_affordable_ask_gets_final_receipt(self):
        result, offers, active = self.run_final_turn()
        self.assertEqual(offers, [9686, 13310, 15030, 15000])
        self.assertEqual(result["receipt"]["price"], 15000)
        self.assertEqual(active["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(self.hub["pending_deals"]["order-1"]["price"], 15000)

    def test_above_offer_limit_never_gets_final_turn_and_reports_both_limits(self):
        result, offers, _ = self.run_final_turn(ask=15031)
        self.assertEqual(len(offers), 3)
        self.assertIsNone(result["receipt"])
        self.assertIn("counter limit reached (ask 15031, offer limit 15030, budget limit 25000)", result["review"])

    def test_expired_deadline_does_not_send_final_turn(self):
        result, offers, _ = self.run_final_turn(expired=True)
        self.assertEqual(len(offers), 3)
        self.assertIsNone(result["receipt"])
        self.assertIn("final acceptance RPC failed (TimeoutError)", result["review"])

    def test_final_turn_counter_is_not_followed_by_another_offer(self):
        result, offers, _ = self.run_final_turn(final="counter")
        self.assertEqual(len(offers), 4)
        self.assertIsNone(result["receipt"])
        self.assertIn("counter limit reached", result["review"])

    def test_final_turn_rpc_failures_are_recorded(self):
        for final, expected in [("exception", "final acceptance RPC failed (RuntimeError)"),
                                ("error", "final acceptance RPC error: fake final acceptance error")]:
            with self.subTest(final=final):
                result, offers, _ = self.run_final_turn(final=final)
                self.assertEqual(len(offers), 4)
                self.assertIsNone(result["receipt"])
                self.assertIn(expected, result["review"])

    def test_final_receipt_cannot_exceed_last_accepted_offer(self):
        result, offers, _ = self.run_final_turn(receipt_price=15001)
        self.assertEqual(offers[-1], 15000)
        self.assertIsNone(result["receipt"])
        self.assertIn("Invalid receipt", result["review"])

    def test_goods_comparables_cap_still_blocks_final_turn(self):
        self.order["kind"] = "goods"
        self.hub["demand_radar"]["sku_settles"] = {self.sku: [10000] * 3}
        result, offers, _ = self.run_final_turn(ask=14800)
        self.assertEqual(len(offers), 3)
        self.assertLessEqual(max(offers), 13360)  # new .80 cap dominates comparable .88
        self.assertIsNone(result["receipt"])
        self.assertIn("offer limit 13360", result["review"])

    def test_goods_final_receipt_still_requires_positive_surplus_or_warranty(self):
        self.order["kind"] = "goods"
        result, offers, _ = self.run_final_turn(ask=13000, warranty=0)
        self.assertEqual(len(offers), 4)
        self.assertIsNone(result["receipt"])
        self.assertIn("expected Saved", result["review"])

    def test_round2_keeps_five_counters_without_final_acceptance(self):
        result, offers, _ = self.run_final_turn(legacy=True)
        self.assertEqual(len(offers), 5)
        self.assertIsNone(result["receipt"])
        self.assertNotIn("Reason:", result["review"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
