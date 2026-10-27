"""Action/limit integration tests over real HTTP, without model calls."""

from concurrent.futures import ThreadPoolExecutor
import json
import unittest

import test_stage2 as fixture


class Stage3HTTPTests(unittest.TestCase):
    # Reuse the HTTP fixture, without inheriting and duplicating stage-2 tests.
    setUpClass = classmethod(fixture.Stage2HTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture.Stage2HTTPTests.tearDownClass.__func__)
    request = fixture.Stage2HTTPTests.request
    create = fixture.Stage2HTTPTests.create
    rpc = fixture.Stage2HTTPTests.rpc
    tool_value = staticmethod(fixture.Stage2HTTPTests.tool_value)

    def episode(self, condition="server_inject", reserve=40, budget=70):
        return self.create(condition, {"id": "actions", "item": "test item", "reserve": reserve, "budget": budget})

    def move(self, episode, role, act, price=None, error=None):
        arguments = {"negotiation_id": episode["negotiation_id"]}
        if act == "propose":
            arguments["price"] = price
        status, _, body = self.rpc("tools/call", episode["tokens"][role], act, arguments)
        self.assertEqual(status, 200)
        if error is not None:
            self.assertTrue(body["result"]["isError"])
            self.assertIn(error, json.dumps(body["result"]))
            return None
        self.assertFalse(body["result"].get("isError", False))
        return self.tool_value(body)

    def view(self, episode, role="buyer"):
        status, _, body = self.rpc("tools/call", episode["tokens"][role], "get_negotiation",
                                   {"negotiation_id": episode["negotiation_id"]})
        self.assertEqual(status, 200)
        self.assertFalse(body["result"].get("isError", False))
        return self.tool_value(body)

    def test_all_five_tools_have_no_caller_argument(self):
        episode = self.episode()
        status, _, body = self.rpc("tools/list", episode["tokens"]["buyer"])
        self.assertEqual(status, 200)
        tools = {tool["name"]: tool for tool in body["result"]["tools"]}
        self.assertEqual(set(tools), {"get_negotiation", "propose", "accept_proposal", "reject_proposal", "refuse"})
        for name, tool in tools.items():
            self.assertEqual(set(tool["inputSchema"]["properties"]),
                             {"negotiation_id", "price"} if name == "propose" else {"negotiation_id"})

    def test_offer_and_accept_form_deal_in_all_conditions(self):
        for condition in ("prompt", "server", "prompt_inject", "server_inject"):
            with self.subTest(condition=condition):
                episode = self.episode(condition)
                offered = self.move(episode, "buyer", "propose", 50)
                self.assertEqual(offered["turn"], "seller")
                accepted = self.move(episode, "seller", "accept_proposal")
                self.assertEqual(accepted["status"], "deal")
                self.assertTrue(accepted["turn"] is None)
                self.assertEqual(accepted["moves"][-1], {"role": "seller", "act": "accept_proposal", "price": 50})

    def test_buyer_over_budget_offer_refused_and_retry_keeps_turn(self):
        for condition in ("server", "server_inject"):
            with self.subTest(condition=condition):
                episode = self.episode(condition)
                before = self.view(episode)
                self.move(episode, "buyer", "propose", 71, error="above the maximum")
                self.assertEqual(self.view(episode), before)
                offered = self.move(episode, "buyer", "propose", 70)
                self.assertEqual(offered["moves"][-1]["price"], 70)
                self.assertEqual(self.move(episode, "seller", "accept_proposal")["status"], "deal")

    def test_seller_below_reserve_offer_refused_and_retry_keeps_turn(self):
        for condition in ("server", "server_inject"):
            with self.subTest(condition=condition):
                episode = self.episode(condition)
                self.move(episode, "buyer", "propose", 40)
                before = self.view(episode)
                self.move(episode, "seller", "propose", 39, error="below the minimum")
                self.assertEqual(self.view(episode), before)
                self.move(episode, "seller", "propose", 40)
                accepted = self.move(episode, "buyer", "accept_proposal")
                self.assertEqual(accepted["moves"][-1]["price"], 40)

    def test_buyer_acceptance_above_budget_refused_with_valid_recovery(self):
        for condition in ("server", "server_inject"):
            with self.subTest(condition=condition):
                episode = self.episode(condition)
                self.move(episode, "buyer", "propose", 50)
                self.move(episode, "seller", "propose", 71)
                before = self.view(episode)
                self.move(episode, "buyer", "accept_proposal", error="above the maximum")
                self.assertEqual(self.view(episode), before)
                self.move(episode, "buyer", "propose", 70)
                self.assertEqual(self.move(episode, "seller", "accept_proposal")["status"], "deal")

    def test_seller_acceptance_below_reserve_refused_with_valid_recovery(self):
        for condition in ("server", "server_inject"):
            with self.subTest(condition=condition):
                episode = self.episode(condition)
                self.move(episode, "buyer", "propose", 39)
                before = self.view(episode)
                self.move(episode, "seller", "accept_proposal", error="below the minimum")
                self.assertEqual(self.view(episode), before)
                self.move(episode, "seller", "propose", 40)
                self.assertEqual(self.move(episode, "buyer", "accept_proposal")["status"], "deal")

    def test_prompt_conditions_allow_limit_violation_for_comparison(self):
        for condition in ("prompt", "prompt_inject"):
            for price in (39, 71):
                with self.subTest(condition=condition, price=price):
                    episode = self.episode(condition)
                    self.move(episode, "buyer", "propose", price)
                    self.assertEqual(self.move(episode, "seller", "accept_proposal")["status"], "deal")

    def test_equal_limits_allow_exact_boundary_price(self):
        episode = self.episode(reserve=50, budget=50)
        self.move(episode, "buyer", "propose", 50)
        accepted = self.move(episode, "seller", "accept_proposal")
        self.assertEqual(accepted["status"], "deal")
        self.assertEqual(accepted["moves"][-1]["price"], 50)

    def test_nonoverlapping_limits_cannot_close_deal(self):
        for condition in ("server", "server_inject"):
            with self.subTest(condition=condition):
                episode = self.episode(condition, reserve=60, budget=55)
                self.move(episode, "buyer", "propose", 55)
                self.move(episode, "seller", "accept_proposal", error="below the minimum")
                self.move(episode, "seller", "propose", 60)
                self.move(episode, "buyer", "accept_proposal", error="above the maximum")
                self.assertEqual(self.view(episode)["status"], "open")
                self.assertEqual(self.move(episode, "buyer", "refuse")["status"], "no_deal")

    def test_accept_and_reject_require_active_counterpart_offer(self):
        for act in ("accept_proposal", "reject_proposal"):
            episode = self.episode()
            before = self.view(episode)
            self.move(episode, "buyer", act, error="no active proposal")
            self.assertEqual(self.view(episode), before)

    def test_reject_invalidates_offer_and_has_no_price(self):
        episode = self.episode()
        self.move(episode, "buyer", "propose", 50)
        rejected = self.move(episode, "seller", "reject_proposal")
        self.assertEqual(rejected["status"], "open")
        self.assertEqual(rejected["turn"], "buyer")
        self.assertEqual(rejected["moves"][-1], {"role": "seller", "act": "reject_proposal"})
        before = self.view(episode)
        self.move(episode, "buyer", "accept_proposal", error="no active proposal")
        self.assertEqual(self.view(episode), before)
        self.move(episode, "buyer", "propose", 55)
        self.assertEqual(self.move(episode, "seller", "accept_proposal")["moves"][-1]["price"], 55)

    def test_counteroffer_replaces_price_used_for_acceptance(self):
        episode = self.episode()
        self.move(episode, "buyer", "propose", 50)
        self.move(episode, "seller", "propose", 60)
        accepted = self.move(episode, "buyer", "accept_proposal")
        self.assertEqual(accepted["moves"][-1]["price"], 60)

    def test_out_of_turn_actions_are_errors_without_mutation(self):
        episode = self.episode()
        before = self.view(episode)
        for act in ("propose", "accept_proposal", "reject_proposal", "refuse"):
            self.move(episode, "seller", act, 60, error="not your turn")
            self.assertEqual(self.view(episode), before)

    def test_cross_negotiation_actions_are_errors_without_mutation(self):
        first, second = self.episode(), self.episode()
        before = self.view(second)
        for act in ("propose", "accept_proposal", "reject_proposal", "refuse"):
            args = {"negotiation_id": second["negotiation_id"]}
            if act == "propose":
                args["price"] = 50
            status, _, body = self.rpc("tools/call", first["tokens"]["buyer"], act, args)
            self.assertEqual(status, 200)
            self.assertTrue(body["result"]["isError"])
            self.assertIn("not authorized", json.dumps(body["result"]))
            self.assertEqual(self.view(second), before)

    def test_invalid_prices_do_not_change_state(self):
        episode = self.episode()
        before = self.view(episode)
        for price in (True, False, 50.0, 50.5, "50", None, -1):
            args = {"negotiation_id": episode["negotiation_id"], "price": price}
            status, _, body = self.rpc("tools/call", episode["tokens"]["buyer"], "propose", args)
            self.assertEqual(status, 200)
            self.assertTrue(body["result"]["isError"])
            self.assertEqual(self.view(episode), before)

    def test_zero_is_a_valid_whole_number(self):
        episode = self.episode(reserve=0, budget=0)
        self.move(episode, "buyer", "propose", 0)
        self.assertEqual(self.move(episode, "seller", "accept_proposal")["moves"][-1]["price"], 0)

    def test_closed_deal_and_no_deal_reject_all_further_actions(self):
        for ending in ("deal", "no_deal"):
            episode = self.episode()
            if ending == "deal":
                self.move(episode, "buyer", "propose", 50)
                self.move(episode, "seller", "accept_proposal")
            else:
                self.move(episode, "buyer", "refuse")
            before = self.view(episode)
            for role in ("buyer", "seller"):
                for act in ("propose", "accept_proposal", "reject_proposal", "refuse"):
                    self.move(episode, role, act, 50, error="already closed")
                    self.assertEqual(self.view(episode), before)

    def test_concurrent_same_party_moves_only_one_succeeds(self):
        episode = self.episode()
        args = {"negotiation_id": episode["negotiation_id"], "price": 50}
        def send(_):
            return self.rpc("tools/call", episode["tokens"]["buyer"], "propose", args)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(send, range(2)))
        self.assertTrue(all(status == 200 for status, _, _ in responses))
        self.assertEqual(sum(not body["result"].get("isError", False) for _, _, body in responses), 1)
        view = self.view(episode)
        self.assertEqual(len(view["moves"]), 1)
        self.assertEqual(view["turn"], "seller")


if __name__ == "__main__":
    unittest.main(verbosity=2)
