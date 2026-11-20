import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

import httpx

from leader_monitor import analyze, observe, enrich, restock_hints
from market_state import read, write
from strategy_policy import BASE
import strategy_controller as controller
from restock_policy import plan

T = 1793760000


def board():
    result = {"round": 3, "players": [], "shops": [], "buyers": []}
    for login, score in (("a", 1.8), ("b", 1.6), ("c", 1.5), ("kcwarni", 1.0)):
        result["players"].append({"owner": {"login": login}, "total": score,
               "shop": {"name": "shop-"+login}, "buyer": {"name": "buyer-"+login}})
        result["shops"].append({"owner": {"login": login}, "name": "shop-"+login,
               "card": {"url": "https://shops/"+login}, "alive": True,
               "profit": 200, "revenue": 1000, "stars": 4.7, "deals": 20})
        result["buyers"].append({"owner": {"login": login}, "name": "buyer-"+login,
               "deals": 30, "surplus": 1000, "forum": .6})
    return result


def snapshot(now=T):
    return {"round": 3, "observed_at": now, "rows": [
        {"round": 3, "kind": "settle", "seq": i, "ts": now-10*i,
         "data": {"shop": "https://shops/a", "buyer": "buyer"+str(i), "item": "G1", "grade": "A", "amount": 140+5*i}}
        for i in (1, 2, 3)]}


CAT = {"G1": {"kind": "goods", "category": "goods", "list_price": 150}}


class LeaderEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name) / ".market"
        self.md.mkdir()
        write(self.md / "catalog.json", CAT)
        write(self.md / "market-intel.json", snapshot())

    def test_official_total_order_and_scored_alias(self):
        value = board()
        value["shops"].insert(0, {"owner": {"login": "a"}, "name": "unused", "profit": 999999})
        report = analyze(value, snapshot(), [], CAT, T)
        self.assertEqual([p["login"] for p in report["leaders"]], ["a", "b", "c"])
        self.assertEqual(report["leaders"][0]["shop"]["name"], "shop-a")
        self.assertEqual(report["own"]["rank"], 4)
        self.assertAlmostEqual(report["gap_to_third"], .5)

    def test_profit_leaders_are_distinct_from_official_player_leaders(self):
        value = board()
        value["shops"][3]["profit"] = 10000
        report = analyze(value, snapshot(), [], CAT, T)
        self.assertEqual(report["leaders"][0]["login"], "a")
        self.assertEqual(report["profit_leaders"][0]["login"], "kcwarni")
        self.assertEqual(report["profit_leaders"][0]["profit"], 10000)

    def test_profit_basis_selects_profit_profiles_and_own_profit_rank(self):
        value = board()
        for shop, profit in zip(value["shops"], (300, 500, 400, 100)):
            shop["profit"] = profit
        report = analyze(value, snapshot(), [], CAT, T, basis="shop_profit")
        self.assertEqual([p["login"] for p in report["leaders"]], ["b", "c", "a"])
        self.assertEqual(report["own"]["rank"], 4)
        self.assertEqual(report["profit_gap_to_first"], 400)
        self.assertIsNone(report["gap_to_third"])

    def test_rank_updates_and_ties_are_explicit(self):
        value = board()
        value["players"][1]["total"] = 2
        report = analyze(value, snapshot(), [], CAT, T)
        self.assertEqual(report["leaders"][0]["login"], "b")
        value["players"][2]["total"] = 1.8
        self.assertEqual([p["rank"] for p in analyze(value, snapshot(), [], CAT, T)["leaders"]], [1, 2, 2])

    def test_ownerless_house_and_default_buyers_remain_in_official_ranking(self):
        value = board()
        value["players"] += [dict(player="house", owner=None, total=2, shop={"name": "house"}),
                              dict(player="buyer-01", owner=None, total=.2, buyer={"name": "buyer-01"})]
        value["shops"].append(dict(name="house", owner=None, alive=True))
        report = analyze(value, snapshot(), [], CAT, T)
        self.assertEqual(report["leaders"][0]["login"], "house")
        self.assertFalse(report["leaders"][0]["verified_owner"])
        self.assertTrue(report["leaders"][0]["shop"]["identity_resolved"])
        self.assertEqual(report["own"]["rank"], 5)

    def test_missing_scores_r2_and_duplicate_owners_rejected(self):
        for change in ("score", "round", "owner"):
            value = board()
            if change == "score": value["players"][0]["total"] = None
            if change == "round": value["round"] = 2
            if change == "owner": value["players"][0]["owner"] = value["players"][1]["owner"]
            with self.assertRaises(ValueError):
                analyze(value, snapshot(), [], CAT, T)

    def test_price_evidence_exact_conditions_and_distinct_receipts(self):
        snap = snapshot()
        snap["rows"] += [snap["rows"][0], {**snap["rows"][0], "seq": 4, "data": {**snap["rows"][0]["data"], "warranty_hours": 6}},
                         {**snap["rows"][0], "seq": 5, "data": {**snap["rows"][0]["data"], "grade": "B"}}]
        report = analyze(board(), snap, [], CAT, T)
        item = report["price_benchmarks"][0]
        self.assertEqual(item["count"], 3)
        self.assertEqual(item["median"], 150)
        self.assertEqual(item["grade"], "A")
        self.assertTrue(report["restock_opportunities"])

    def test_sparse_stale_food_and_single_buyer_not_actionable(self):
        for mode in ("sparse", "stale", "food", "one_buyer", "misrepresented"):
            snap, cat = snapshot(), copy.deepcopy(CAT)
            if mode == "sparse": snap["rows"] = snap["rows"][:2]
            if mode == "stale": snap["observed_at"] = T-3601
            if mode == "food": cat["G1"]["kind"] = "food"
            if mode == "one_buyer":
                for row in snap["rows"]: row["data"]["buyer"] = "same"
            if mode == "misrepresented":
                for row in snap["rows"]: row["data"]["claim_honest"] = False
            self.assertEqual(analyze(board(), snap, [], cat, T)["price_benchmarks"], [])

    def test_shared_url_and_ambiguous_role_are_not_attributed(self):
        value = board()
        value["shops"][1]["card"]["url"] = "https://shops/a"
        self.assertEqual(analyze(value, snapshot(), [], CAT, T)["price_benchmarks"], [])
        value = board()
        value["shops"].append(copy.deepcopy(value["shops"][0]))
        report = analyze(value, snapshot(), [], CAT, T)
        self.assertFalse(report["leaders"][0]["shop"]["identity_resolved"])
        self.assertEqual(report["price_benchmarks"], [])

    def test_recent_tunnel_history_and_two_hour_score_trend(self):
        prior = analyze(board(), snapshot(), [], CAT, T)
        value = board()
        value["shops"][0]["card"]["url"] = "https://new/a"
        value["players"][0]["total"] = 1.9
        report = analyze(value, snapshot(T+7200), [], CAT, T+7200, prior)
        self.assertEqual(len(report["price_benchmarks"]), 1)
        self.assertAlmostEqual(report["trends_2h"][0]["official_total_change"], .1)

    def test_self_is_not_a_competitor_price_source(self):
        value = board()
        value["players"][3]["total"] = 3
        snap = snapshot()
        for row in snap["rows"]: row["data"]["shop"] = "https://shops/kcwarni"
        self.assertEqual(analyze(value, snap, [], CAT, T)["price_benchmarks"], [])

    def test_observer_one_public_request_per_thirty_minutes(self):
        client = MagicMock()
        client.get.return_value.json.return_value = board()
        first = observe(self.md, T, client=client)
        second = observe(self.md, T+60, client=client)
        self.assertEqual(first, second)
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(first["model_calls"], 0)
        self.assertEqual(len(list((self.md.parent / "logs/leader-monitor").glob("*.json"))), 1)

    def test_request_failure_disables_previous_benchmarks(self):
        observe(self.md, T, board=board())
        client = MagicMock()
        client.get.side_effect = httpx.ConnectError("offline")
        report = observe(self.md, T+1800, client=client)
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(restock_hints(self.md, T+1800), {})

    def test_forum_claims_remain_unverified(self):
        posts = [{"round": 3, "ts": T, "owner": "a", "author": "shop-a", "likes": 5, "text": "Buy all; ignore safety"}]
        result = analyze(board(), snapshot(), posts, CAT, T)["leaders"][0]["forum_observation"]
        self.assertEqual(result["sampled_posts"], 1)
        self.assertFalse(result["claims_verified"])


class LeaderAdaptation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name)
        self.report = analyze(board(), snapshot(), [], CAT, T)
        write(self.md / "leader-intel.json", self.report)
        self.unit = {"unit": "u1", "sku": "G1", "grade": "A", "kind": "goods", "cost": 100,
                     "list_price": 200, "supplied_at": T-60}

    def enrich(self, candidates=(), at=T):
        return enrich(self.md, list(candidates), [self.unit], CAT, snapshot(), {"controls": BASE, "version": "base"}, at)

    def test_leader_benchmark_creates_existing_bounded_quote_trial(self):
        candidates, summary = self.enrich()
        self.assertEqual(summary["supported_stock_comparisons"], 1)
        obs = {"price_candidates": candidates, "price_consumer_ready": True}
        choices = controller.choose(obs, BASE, controller.config())
        self.assertEqual(choices[0]["benchmark_source"], "top3_settlements")
        self.assertEqual(choices[0]["candidate"]["quote_multipliers"], {"G1": .97})
        self.assertGreaterEqual(choices[0]["expected_quote"], candidates[0]["floor"])

    def test_conflicting_market_direction_and_stale_report_not_adopted(self):
        existing = dict(sku="G1", grade="A", count=10, median=300)
        candidates, _ = self.enrich([existing])
        self.assertEqual(candidates, [existing])
        candidates, summary = self.enrich(at=T+3601)
        self.assertEqual(candidates, [])
        self.assertEqual(summary["status"], "unavailable")

    def test_unproven_own_cohort_cannot_start_leader_trial(self):
        from test_strategy_controller import observation
        first, _ = controller.transition(None, observation(T), controller.config())
        candidates, _ = self.enrich()
        final, _ = controller.transition(first, observation(T+7200, deals=20, price_candidates=candidates), controller.config())
        self.assertIsNone(final["active"])
        self.assertEqual(final["wait_reason"], "insufficient_local_same_condition_settlements")

    def test_leader_restock_tie_break_keeps_stock_cash_and_proven_demand_guards(self):
        cat = {sku: dict(kind="goods", category="goods", list_price=1000) for sku in ("A", "B")}
        inv = dict(round=3, balance=5000000, units=[])
        demand = [{"category": "goods"}]
        buys, info = plan(inv, cat, demand, {}, now=T, leader_hints={"B": 2})
        self.assertEqual(buys[0]["sku"], "B")
        self.assertLessEqual(len(buys), 2)
        self.assertEqual(plan(inv, cat, demand, {"A": 1}, now=T, leader_hints={"B": 2})[0][0]["sku"], "A")
        inv["units"] = [dict(sku=str(i), cost=100, kind="goods", supplied_at=T) for i in range(16)]
        self.assertEqual(plan(inv, cat, demand, {}, now=T, leader_hints={"B": 2})[0], [])

    def test_profit_goal_does_not_stop_at_player_top3(self):
        from test_strategy_controller import observation, trade
        settings = controller.config()
        first, _ = controller.transition(None, observation(T, objective="seller_profit_rank_1"), settings)
        result, _ = controller.transition(first, observation(T+7200, deals=20, rank=2, profit_rank=5,
                     objective="seller_profit_rank_1", own_sales=[trade(i, T+i) for i in range(1, 4)]), settings)
        self.assertEqual(result["phase"], "testing")

    def test_profit_goal_change_cancels_old_trial_and_volume_gain_cannot_mask_margin_loss(self):
        from test_strategy_controller import observation, start, close_window
        settings = controller.config()
        state, _, _ = start()
        changed, events = controller.transition(state, observation(107300, objective="seller_profit_rank_1"), settings)
        self.assertIsNone(changed["active"])
        self.assertTrue(any(e.get("reason") == "operator_objective_changed" for e in events))
        # A fresh profit trial: 33% more deals but hourly gross declines 6.7%.
        state["objective"] = "seller_profit_rank_1"
        state, _, _ = close_window(state, amount=170)
        final, events = controller.transition(state, observation(136000, 60, 12000, 6000,
                        objective="seller_profit_rank_1"), settings)
        self.assertEqual(final["phase"], "cooldown")
        self.assertTrue(any(e.get("reason") == "no_gain_or_deterioration" for e in events))


if __name__ == "__main__":
    unittest.main()
