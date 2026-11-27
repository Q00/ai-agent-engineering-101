"""Rule tests for the market. No model, no network: python -m unittest test_market"""

import unittest

from market_server import INJECTION, Market, MoveRefused


def opened(condition, reserve=70, budget=100):
    market = Market()
    o = market.open("camera", reserve, budget, condition)
    grants = {role: market.tokens[tok] for role, tok in o["tokens"].items()}
    return market, o["negotiation_id"], grants


class MarketRules(unittest.TestCase):
    def test_role_and_binding_come_from_token(self):
        market, nid, g = opened("prompt")
        self.assertEqual(market.view(g["seller"], nid)["your_role"], "seller")
        _, other_nid, _ = opened("prompt")
        with self.assertRaisesRegex(MoveRefused, "different negotiation"):
            market.view(g["buyer"], "neg-other")
        with self.assertRaisesRegex(MoveRefused, "different negotiation"):
            market.move(g["buyer"], other_nid, "propose", 80)

    def test_turn_order(self):
        market, nid, g = opened("prompt")
        with self.assertRaisesRegex(MoveRefused, "buyer's turn"):
            market.move(g["seller"], nid, "propose", 90)
        market.move(g["buyer"], nid, "propose", 60)
        with self.assertRaisesRegex(MoveRefused, "seller's turn"):
            market.move(g["buyer"], nid, "propose", 65)

    def test_accept_needs_other_party_offer(self):
        market, nid, g = opened("prompt")
        with self.assertRaisesRegex(MoveRefused, "no proposal"):
            market.move(g["buyer"], nid, "accept_proposal")
        market.move(g["buyer"], nid, "propose", 80)
        market.move(g["seller"], nid, "accept_proposal")
        s = market.state(nid)
        self.assertEqual((s["status"], s["price"]), ("deal", 80))
        with self.assertRaisesRegex(MoveRefused, "closed"):
            market.move(g["buyer"], nid, "propose", 81)

    def test_prompt_condition_executes_out_of_limit_but_counts_attempt(self):
        market, nid, g = opened("prompt_inject")
        market.move(g["buyer"], nid, "propose", 60)
        market.move(g["seller"], nid, "propose", 120)
        market.move(g["buyer"], nid, "accept_proposal")          # 120 > budget 100
        s = market.state(nid)
        self.assertEqual((s["status"], s["price"]), ("deal", 120))
        self.assertEqual([e["attempted_violation"] for e in s["events"]], [False, False, True])

    def test_server_condition_refuses_out_of_limit(self):
        market, nid, g = opened("server_inject")
        with self.assertRaisesRegex(MoveRefused, "up to 100"):
            market.move(g["buyer"], nid, "propose", 130)
        market.move(g["buyer"], nid, "propose", 60)
        with self.assertRaisesRegex(MoveRefused, "70 or more"):
            market.move(g["seller"], nid, "accept_proposal")     # 60 < reserve 70
        market.move(g["seller"], nid, "propose", 120)
        with self.assertRaisesRegex(MoveRefused, "up to 100"):
            market.move(g["buyer"], nid, "accept_proposal")
        s = market.state(nid)
        self.assertEqual(s["status"], "open")
        self.assertEqual(sum(e["attempted_violation"] for e in s["events"]), 3)
        self.assertEqual(sum(bool(e["refused"]) for e in s["events"]), 3)

    def test_injection_only_in_buyer_view_of_seller_proposals(self):
        market, nid, g = opened("server_inject", reserve=120, budget=100)
        market.move(g["buyer"], nid, "propose", 90)
        market.move(g["seller"], nid, "propose", 125)
        buyer_moves = market.view(g["buyer"], nid)["moves"]
        self.assertNotIn("notice", buyer_moves[0])
        self.assertEqual(buyer_moves[1]["notice"], INJECTION.format(raised=150))
        self.assertTrue(all("notice" not in m for m in market.view(g["seller"], nid)["moves"]))
        market2, nid2, g2 = opened("server")
        market2.move(g2["buyer"], nid2, "propose", 60)
        market2.move(g2["seller"], nid2, "propose", 95)
        self.assertTrue(all("notice" not in m for m in market2.view(g2["buyer"], nid2)["moves"]))

    def test_move_limit(self):
        market, nid, g = opened("prompt")
        for i in range(4):
            market.move(g["buyer"], nid, "propose", 50 + i)
            market.move(g["seller"], nid, "propose", 99 - i)
        with self.assertRaisesRegex(MoveRefused, "move limit"):
            market.move(g["buyer"], nid, "accept_proposal")
        self.assertEqual(market.state(nid)["status"], "open")


if __name__ == "__main__":
    unittest.main()
