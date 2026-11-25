"""Exercise the real buyer main/protocol/memory flow with offline transports."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

import market_buyer
from harness import HarnessError


def shop(name, *, url=None, stars=None):
    return {"name": name, "url": url or f"https://{name}.invalid",
            "reviews": [{"stars": stars, "review": "fixture", "round": 3}] if stars else [],
            "offers": [{"sku": "G00001", "name": "fixture chair", "qty": 1}]}


def step(state, *, method="SendMessage", receipt=None, advance=0):
    return {"state": state, "method": method, "receipt": receipt, "advance": advance}


def quote():
    return step("TASK_STATE_INPUT_REQUIRED")


def receipt(host, *, price=60000):
    return {"price": price, "item": "G00001", "task": f"task-{host}",
            "shop": host, "sig_seller": "fixture-original-signature", "unit": f"unit-{host}"}


def complete(host="second", *, price=60000):
    return step("TASK_STATE_COMPLETED", receipt=receipt(host, price=price))


OFFER = {"action": "offer", "price": 60000, "reason": "local test"}
WALK = {"action": "walk_away", "reason": "offered terms not suitable"}


class BuyerRetryIntegrationTests(unittest.TestCase):
    def run_market(self, plans, *, decisions=None, shops=None, decision_advances=None):
        real_client = httpx.Client
        plans = deepcopy(plans)
        decisions = list(decisions or [OFFER])
        decision_advances = list(decision_advances or [])
        clock, requests, model_inputs = [100.0], [], []

        def transport(request):
            host = request.url.host.split(".")[0]
            requests.append((host, request.method, request.url.path, deepcopy(request.extensions.get("timeout"))))
            if request.method == "GET":
                self.assertEqual(request.url.path, "/.well-known/agent-card.json")
                return httpx.Response(200, json={"supportedInterfaces": [{"protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0", "url": f"https://{host}.invalid/a2a"}]})
            body = json.loads(request.content)
            self.assertEqual(request.url.path, "/a2a")
            self.assertIn(host, plans, "No third/unknown shop may be contacted")
            self.assertTrue(plans[host], f"Unexpected extra RPC to {host}: {body['method']}")
            reply = plans[host].pop(0)
            self.assertEqual(body["method"], reply.get("method", "SendMessage"))
            clock[0] += reply.get("advance", 0)
            if reply.get("error") == "timeout":
                raise httpx.ReadTimeout("offline timeout", request=request)
            if reply.get("error") == "http":
                return httpx.Response(503, json={"error": "offline unavailable"})
            task = {"id": reply.get("task", f"task-{host}"), "status": {
                "state": reply["state"], "message": {"parts": [{"text": "Grade A; warranty 6 hours. Asking price: 65000"}]}}}
            if reply.get("receipt") is not None:
                task["artifacts"] = [{"parts": [{"data": reply["receipt"]}]}]
            if "history" in reply:
                task["history"] = reply["history"]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": task})

        def selection(_, context, validate, **kwargs):
            self.assertEqual(kwargs["max_attempts"], 1)
            self.assertIn("first", {candidate["name"] for candidate in context["candidates"]})
            return validate(json.dumps({"name": "first", "sku": "G00001", "reason": "fixture initial selection"}))

        def decision(**kwargs):
            model_inputs.append(deepcopy(kwargs))
            if decision_advances:
                clock[0] += decision_advances.pop(0)
            self.assertTrue(decisions, "More model decisions than expected")
            output = decisions.pop(0)
            if isinstance(output, Exception):
                raise output
            return deepcopy(output)

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            md = root / ".market"
            md.mkdir()
            order = {"order": "fixture-order", "round": 3, "kind": "goods", "item": "chair",
                     "budget": 100000, "acceptable": ["G00001"]}
            (md / "order.json").write_text(json.dumps(order))
            (md / "shops.json").write_text(json.dumps(shops or [shop("first"), shop("second", stars=5)]))
            (md / "catalog.json").write_text(json.dumps({"G00001": {"list_price": 100000, "kind": "goods"}}))
            memory = root / "data" / "market-memory.jsonl"
            stack.enter_context(patch.object(market_buyer, "ROOT", root))
            stack.enter_context(patch.object(market_buyer.time, "monotonic", side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(market_buyer.httpx, "Client",
                                             side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(transport), **kwargs)))
            chosen = stack.enter_context(patch.object(market_buyer, "choose_shop", wraps=market_buyer.choose_shop))
            select = stack.enter_context(patch.object(market_buyer, "decide", side_effect=selection))
            model = stack.enter_context(patch.object(market_buyer, "buyer_decision", side_effect=decision))
            stack.enter_context(patch("harness.call_model", side_effect=AssertionError("No live model call")))
            stdout = stack.enter_context(redirect_stdout(io.StringIO()))
            code = market_buyer.main(["--shops", str(md / "shops.json"), "--order", str(md / "order.json"),
                                      "--item", "chair", "--budget", "100000", "--memory", str(memory)])
            lines = stdout.getvalue().splitlines()
            final_lines = [line for line in lines if line.startswith("{")]
            self.assertEqual(len(final_lines), 1, "Only one result JSON may reach the kit")
            result = json.loads(final_lines[0])
            records = [json.loads(line) for line in memory.read_text().splitlines()] if memory.exists() else []
            self.assertEqual(select.call_count, 1)
            self.assertEqual(chosen.call_count, 1)
            selection_state = chosen.call_args.kwargs["selection_state"]
            self.assertEqual(set(selection_state), {"shops", "candidates", "values", "seed"})
            return {"code": code, "result": result, "records": records, "requests": requests,
                    "model_inputs": model_inputs, "model_calls": model.call_count, "clock": clock[0], "log": lines}

    def test_confirmed_reject_tries_one_alternative_and_preserves_both_attempts(self):
        run = self.run_market({"first": [step("TASK_STATE_REJECTED")], "second": [quote(), complete()]})
        self.assertEqual(run["code"], 0)
        self.assertEqual(run["result"]["shop"], "https://second.invalid")
        self.assertEqual(run["result"]["receipt"], receipt("second"))
        self.assertEqual(set(run["result"]), {"shop", "receipt", "stars", "review", "accept"})
        self.assertEqual([(r["name"], r["result"]) for r in run["records"]],
                         [("first", "no_deal"), ("second", "receipt_issued")])
        self.assertEqual({r["order"] for r in run["records"]}, {"fixture-order"})
        self.assertEqual(run["records"][0]["negotiation_diagnostics"]["terminal_state"], "TASK_STATE_REJECTED")
        self.assertEqual(run["model_calls"], 1)

    def test_confirmed_walkaway_cancel_shares_budget_with_second_shop(self):
        run = self.run_market({"first": [quote(), step("TASK_STATE_CANCELED", method="CancelTask")],
                               "second": [quote(), complete()]}, decisions=[WALK, OFFER])
        self.assertIsNotNone(run["result"]["receipt"])
        self.assertTrue(run["records"][0]["negotiation_diagnostics"]["cancel_confirmed"])
        self.assertEqual([row["decisions_left"] for row in run["model_inputs"]], [4, 3])
        self.assertEqual(run["records"][1]["purchase_policy"]["max_decisions"], 3)

    def test_uncertain_cancel_never_opens_alternative_even_after_cleanup(self):
        for failure in ("timeout", "wrong_task", "still_working"):
            with self.subTest(failure=failure):
                bad = {"method": "CancelTask", "error": "timeout"} if failure == "timeout" else (
                    {**step("TASK_STATE_CANCELED", method="CancelTask"), "task": "wrong"}
                    if failure == "wrong_task" else step("TASK_STATE_WORKING", method="CancelTask"))
                run = self.run_market({"first": [quote(), bad, step("TASK_STATE_CANCELED", method="CancelTask")]},
                                      decisions=[WALK])
                self.assertIsNone(run["result"]["receipt"])
                self.assertEqual({row[0] for row in run["requests"]}, {"first"})
                self.assertEqual(run["records"][0]["result"], "error")

    def test_network_and_model_errors_never_try_alternative(self):
        for failure in ("network", "model"):
            with self.subTest(failure=failure):
                plans = {"first": [{"method": "SendMessage", "error": "http"}]} if failure == "network" else {
                    "first": [quote(), step("TASK_STATE_CANCELED", method="CancelTask")]}
                decisions = [HarnessError("Harness failed; offline fixture")] if failure == "model" else []
                run = self.run_market(plans, decisions=decisions)
                self.assertEqual({row[0] for row in run["requests"]}, {"first"})
                self.assertEqual(run["records"][0]["result"], "error")

    def test_any_receipt_or_artifact_blocks_alternative(self):
        for case in ("valid", "wrong_price", "completed_empty", "rejected_receipt"):
            with self.subTest(case=case):
                if case in ("valid", "wrong_price"):
                    plans = {"first": [quote(), complete("first", price=60000 if case == "valid" else 60001)]}
                elif case == "completed_empty":
                    plans = {"first": [step("TASK_STATE_COMPLETED")]}
                else:
                    plans = {"first": [step("TASK_STATE_REJECTED", receipt=receipt("first"))]}
                run = self.run_market(plans)
                self.assertEqual({row[0] for row in run["requests"]}, {"first"})
                self.assertTrue(run["records"][0]["negotiation_diagnostics"]["receipt_seen"])
                self.assertEqual(len(run["records"]), 1)
                if case == "valid":
                    self.assertEqual(run["result"]["receipt"], receipt("first"))

    def test_receipt_or_uncertainty_in_history_blocks_rejected_and_cancelled_retries(self):
        for history in ([{"parts": [{"data": receipt("first")}]}],
                        {"parts": []}, [{"parts": []}] * 33,
                        [{"parts": [{"text": "old"}] * 33}]):
            with self.subTest(history_type=type(history).__name__, length=len(history)):
                run = self.run_market({"first": [{**step("TASK_STATE_REJECTED"), "history": history}]})
                self.assertEqual({row[0] for row in run["requests"]}, {"first"})
                self.assertTrue(run["records"][0]["negotiation_diagnostics"]["receipt_seen"])
        run = self.run_market({"first": [quote(), {**step("TASK_STATE_CANCELED", method="CancelTask"),
                              "history": [{"parts": [{"data": receipt("first")}]}]}]}, decisions=[WALK])
        self.assertEqual({row[0] for row in run["requests"]}, {"first"})
        self.assertTrue(run["records"][0]["negotiation_diagnostics"]["receipt_seen"])

    def test_four_decisions_are_shared_and_second_limit_never_opens_third(self):
        run = self.run_market({"first": [quote(), quote(), step("TASK_STATE_REJECTED")],
                              "second": [quote(), quote(), quote(), step("TASK_STATE_CANCELED", method="CancelTask")]},
                             decisions=[OFFER] * 4,
                             shops=[shop("first"), shop("second", stars=5), shop("third", stars=1)])
        self.assertEqual(run["model_calls"], 4)
        self.assertEqual([row["decisions_left"] for row in run["model_inputs"]], [4, 3, 2, 1])
        self.assertEqual([r["negotiation_diagnostics"]["decisions"] for r in run["records"]], [2, 2])
        self.assertEqual(run["records"][1]["negotiation_diagnostics"]["reason"], "decision_limit")
        self.assertEqual({row[0] for row in run["requests"]}, {"first", "second"})

    def test_original_270_second_deadline_is_not_reset_for_alternative(self):
        run = self.run_market({"first": [step("TASK_STATE_REJECTED", advance=251)]})
        self.assertEqual(run["clock"], 351)
        self.assertEqual({row[0] for row in run["requests"]}, {"first"})
        run = self.run_market({"first": [step("TASK_STATE_REJECTED", advance=240)],
                              "second": [quote(), step("TASK_STATE_CANCELED", method="CancelTask")]},
                             decisions=[OFFER], decision_advances=[31])
        self.assertEqual(run["model_inputs"][0]["timeout"], 30)
        self.assertEqual(run["records"][1]["result"], "error")
        self.assertIsNone(run["result"]["receipt"])
        self.assertEqual(run["clock"], 371)
        self.assertTrue(all(row[3]["read"] <= 30 for row in run["requests"] if row[0] == "second"))

    def test_same_url_alias_is_excluded_and_second_rejection_ends_order(self):
        run = self.run_market({"first": [step("TASK_STATE_REJECTED")], "second": [step("TASK_STATE_REJECTED")]},
                             shops=[shop("first"), shop("alias", url="https://first.invalid/", stars=5),
                                    shop("second", stars=5), shop("third", stars=1)])
        self.assertEqual([row["name"] for row in run["records"]], ["first", "second"])
        self.assertEqual(run["result"]["shop"], "https://second.invalid")
        self.assertIsNone(run["result"]["receipt"])
        self.assertEqual({row[0] for row in run["requests"]}, {"first", "second"})


if __name__ == "__main__":
    unittest.main()
