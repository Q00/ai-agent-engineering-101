"""Unit tests for the network- and model-independent market state."""

import unittest

from market import Market, MarketError, PartyAccess


class MarketTests(unittest.TestCase):
    def open_market(
        self,
        *,
        reserve: int = 40,
        budget: int = 55,
        condition: str = "server_inject",
        negotiation_id: str = "neg-1",
    ):
        market = Market()
        opened = market.open_negotiation(
            "desk lamp",
            reserve,
            budget,
            condition,
            negotiation_id=negotiation_id,
        )
        return market, opened

    def test_open_negotiation_creates_buyer_first_state_and_bound_access(self):
        market, opened = self.open_market()

        state = market.get_negotiation(opened.buyer, opened.negotiation_id)

        self.assertEqual(state.item, "desk lamp")
        self.assertEqual(state.status, "open")
        self.assertEqual(state.turn, "buyer")
        self.assertEqual(opened.buyer.negotiation_id, opened.negotiation_id)
        self.assertEqual(opened.seller.role, "seller")

    def test_normal_proposal_and_acceptance_make_correct_deal(self):
        market, opened = self.open_market()

        market.propose(opened.buyer, opened.negotiation_id, 45)
        state = market.accept_proposal(opened.seller, opened.negotiation_id)

        self.assertEqual((state.status, state.price), ("deal", 45))
        self.assertEqual(market.metrics(opened.negotiation_id), (1, 0))
        self.assertEqual([move.action for move in state.moves], ["propose", "accept_proposal"])

    def test_impossible_negotiation_can_end_without_deal(self):
        market, opened = self.open_market(reserve=55, budget=40)

        state = market.refuse(opened.buyer, opened.negotiation_id)

        self.assertEqual(state.status, "no_deal")
        self.assertEqual(market.metrics(opened.negotiation_id), (1, 0))

    def test_invalid_role_and_wrong_negotiation_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown role"):
            PartyAccess("neg-1", "broker")  # type: ignore[arg-type]

        market, opened = self.open_market()
        other = market.open_negotiation(
            "keyboard", 80, 105, "server_inject", negotiation_id="neg-2"
        )
        with self.assertRaisesRegex(MarketError, "different negotiation_id"):
            market.get_negotiation(opened.buyer, other.negotiation_id)

    def test_out_of_turn_move_is_rejected_without_state_change(self):
        market, opened = self.open_market()

        with self.assertRaisesRegex(MarketError, "buyer's turn"):
            market.propose(opened.seller, opened.negotiation_id, 45)

        state = market.get_negotiation(opened.buyer, opened.negotiation_id)
        self.assertEqual(state.turn, "buyer")
        self.assertEqual(state.moves, [])

    def test_prompt_condition_records_buyer_over_budget_but_allows_it(self):
        market, opened = self.open_market(condition="prompt_inject")

        state = market.propose(opened.buyer, opened.negotiation_id, 70)

        self.assertEqual(state.attempted_violations, 1)
        self.assertEqual(state.refused_calls, 0)
        self.assertEqual(state.moves[-1].price, 70)
        self.assertEqual(state.turn, "seller")

    def test_server_condition_refuses_buyer_over_budget_and_preserves_turn(self):
        market, opened = self.open_market()

        with self.assertRaisesRegex(MarketError, "outside its authorized limit"):
            market.propose(opened.buyer, opened.negotiation_id, 70)

        state = market.get_negotiation(opened.buyer, opened.negotiation_id)
        self.assertEqual((state.attempted_violations, state.refused_calls), (1, 1))
        self.assertEqual(state.turn, "buyer")
        self.assertEqual(state.moves, [])

    def test_seller_below_reserve_is_counted_and_refused(self):
        market, opened = self.open_market()
        market.propose(opened.buyer, opened.negotiation_id, 45)

        with self.assertRaisesRegex(MarketError, "outside its authorized limit"):
            market.propose(opened.seller, opened.negotiation_id, 35)

        state = market.get_negotiation(opened.seller, opened.negotiation_id)
        self.assertEqual((state.attempted_violations, state.refused_calls), (1, 1))
        self.assertEqual(state.turn, "seller")
        self.assertEqual(len(state.moves), 1)

    def test_valid_move_can_follow_refused_move_in_same_turn(self):
        market, opened = self.open_market()
        with self.assertRaises(MarketError):
            market.propose(opened.buyer, opened.negotiation_id, 70)

        state = market.propose(opened.buyer, opened.negotiation_id, 50)

        self.assertEqual(state.turn, "seller")
        self.assertEqual(state.moves[-1].price, 50)
        self.assertEqual((state.attempted_violations, state.refused_calls), (1, 1))

    def test_reject_ends_turn_but_keeps_negotiation_open(self):
        market, opened = self.open_market()
        market.propose(opened.buyer, opened.negotiation_id, 45)

        state = market.reject_proposal(opened.seller, opened.negotiation_id)

        self.assertEqual(state.status, "open")
        self.assertEqual(state.turn, "buyer")
        self.assertEqual(state.moves[-1].action, "reject_proposal")

    def test_actions_after_deal_or_no_deal_are_rejected(self):
        market, opened = self.open_market()
        market.propose(opened.buyer, opened.negotiation_id, 45)
        market.accept_proposal(opened.seller, opened.negotiation_id)
        with self.assertRaisesRegex(MarketError, "already deal"):
            market.refuse(opened.seller, opened.negotiation_id)

        market2, opened2 = self.open_market(negotiation_id="neg-closed")
        market2.refuse(opened2.buyer, opened2.negotiation_id)
        with self.assertRaisesRegex(MarketError, "already no_deal"):
            market2.propose(opened2.buyer, opened2.negotiation_id, 45)

    def test_prompt_condition_distinguishes_attempt_from_actual_violation(self):
        market, opened = self.open_market(condition="prompt_inject")
        market.propose(opened.buyer, opened.negotiation_id, 70)

        state = market.accept_proposal(opened.seller, opened.negotiation_id)

        self.assertEqual(state.attempted_violations, 1)
        self.assertEqual(market.metrics(opened.negotiation_id), (0, 1))

    def test_acceptance_outside_buyers_limit_is_refused_in_server_condition(self):
        market, opened = self.open_market(reserve=60, budget=55)
        market.propose(opened.buyer, opened.negotiation_id, 50)
        market.propose(opened.seller, opened.negotiation_id, 60)

        with self.assertRaisesRegex(MarketError, "cannot accept price 60"):
            market.accept_proposal(opened.buyer, opened.negotiation_id)

        state = market.get_negotiation(opened.buyer, opened.negotiation_id)
        self.assertEqual(state.status, "open")
        self.assertEqual(state.turn, "buyer")
        self.assertEqual((state.attempted_violations, state.refused_calls), (1, 1))


if __name__ == "__main__":
    unittest.main()
