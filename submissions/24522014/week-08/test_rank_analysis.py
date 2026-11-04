"""Scores in the rank analysis follow the official rule, and what-ifs hold rivals still."""

import glob
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import rank_analysis as ra


def buyer(name, surplus, forum, deals, posts, all_score=0.5):
    return {"name": name, "surplus": surplus, "forum": forum, "deals": deals, "posts": posts, "all": all_score}


class Formula(unittest.TestCase):
    def test_percentile_shares_ties_and_scales_by_n_minus_one(self):
        self.assertEqual(ra.percentile(3, [1, 2, 3, 4, 5]), .5)
        self.assertEqual(ra.percentile(1, [1, 1, 3]), .25)
        self.assertEqual(ra.percentile(5, [1, 2, 3, 4, 5]), 1)
        self.assertEqual(ra.percentile(1, [1, 2, 3, 4, 5]), 0)

    def test_missing_values_count_as_zero(self):
        rows = [buyer("a", 1, None, 1, 1), buyer("b", 2, .5, 2, 2)]
        self.assertEqual(ra.computed_all(rows[1], rows, ra.BUYER_WEIGHTS), 1)
        self.assertEqual(ra.computed_all(rows[0], rows, ra.BUYER_WEIGHTS), 0)

    def test_saved_boards_reproduce_the_official_scores(self):
        boards = sorted(glob.glob(str(ra.ROOT / "logs/rank-analysis/board-*.json")))
        if not boards:
            self.skipTest("no saved board")
        result = ra.formula_check(json.loads(Path(boards[-1]).read_text(encoding="utf-8")))
        self.assertLess(result["buyers"]["max_error"], .001)
        self.assertLess(result["shops"]["max_error"], .001)


class WhatIf(unittest.TestCase):
    def setUp(self):
        self.rows = [buyer("me", 10, .1, 1, 1), buyer("a", 20, .2, 2, 2), buyer("b", 30, .3, 3, 3), buyer("c", 40, .4, 4, 4), buyer("d", 50, .5, 5, 5)]
        self.me = self.rows[0]

    def test_passing_rivals_pays_the_weight_per_rank(self):
        self.assertAlmostEqual(ra.what_if(self.me, self.rows, ra.BUYER_WEIGHTS, "surplus", 25), .5 * 1 / 4)
        self.assertAlmostEqual(ra.what_if(self.me, self.rows, ra.BUYER_WEIGHTS, "surplus", 60), .5)

    def test_matching_a_rival_is_a_tie(self):
        self.assertAlmostEqual(ra.what_if(self.me, self.rows, ra.BUYER_WEIGHTS, "surplus", 20), .5 * .5 / 4)

    def test_rivals_are_not_changed(self):
        before = json.dumps(self.rows)
        ra.what_if(self.me, self.rows, ra.BUYER_WEIGHTS, "surplus", 60)
        self.assertEqual(json.dumps(self.rows), before)

    def test_value_for_gain_finds_the_smallest_rival_value(self):
        self.assertEqual(ra.value_for_gain(self.me, self.rows, ra.BUYER_WEIGHTS, "deals", .01), 2)
        # Matching 30 only ties, worth .1875; passing it takes the next rival's value.
        self.assertEqual(ra.value_for_gain(self.me, self.rows, ra.BUYER_WEIGHTS, "surplus", .2), 40)
        self.assertIsNone(ra.value_for_gain(self.rows[-1], self.rows, ra.BUYER_WEIGHTS, "surplus", .1))

    def test_conversion_separates_volume_from_value(self):
        board = {"buyers": [{"name": "me", "orders": 100, "deals": 10, "surplus": 100, "all": .1},
                            {"name": "a", "orders": 100, "deals": 50, "surplus": 1000, "all": .9},
                            {"name": "skip", "orders": 5, "deals": 0, "surplus": 0, "all": None}],
                 "shops": [{"name": "me", "visits": 10, "deals": 5, "revenue": 500, "profit": 50, "all": .1, "broken": 1, "refunds": 0},
                           {"name": "a", "visits": 100, "deals": 20, "revenue": 4000, "profit": 200, "all": .9, "broken": 0, "refunds": 0}]}
        result = ra.conversion(board, {"shop": {"name": "me"}, "buyer": {"name": "me"}},
                               [{"player": "a", "shop": {"name": "a"}, "buyer": {"name": "a"}}])
        own = result["own"]
        self.assertEqual((own["buyer"]["deals_per_order"], own["buyer"]["surplus_per_deal"]), (.1, 10))
        self.assertEqual((own["shop"]["deals_per_visit"], own["shop"]["profit_per_deal"]), (.5, 10))
        self.assertEqual(result["market_median"]["buyer"]["deals_per_order"], .3)
        self.assertEqual(result["rivals"]["a"]["buyer"]["surplus_per_deal"], 20)

    def test_spearman_ranks(self):
        rows = [{"a": 1, "b": 10}, {"a": 2, "b": 20}, {"a": 3, "b": 30}]
        self.assertEqual(ra.spearman(rows, lambda r: r["a"], lambda r: r["b"]), 1)
        self.assertEqual(ra.spearman(rows, lambda r: r["a"], lambda r: -r["b"]), -1)
        self.assertIsNone(ra.spearman(rows[:2], lambda r: r["a"], lambda r: r["b"]))
        self.assertIsNone(ra.spearman(rows, lambda r: 1, lambda r: r["b"]))

    def test_attempts_are_counted_for_the_current_utc_day(self):
        with TemporaryDirectory() as root:
            (Path(root) / ".market").mkdir()
            (Path(root) / ".market/buyer-support-state.json").write_text(json.dumps({"attempts": [
                {"at": 1793750400.0 - 86400}, {"at": 1793750400.0}, {"at": 1793750400.0 + 7200}]}), encoding="utf-8")
            result = ra.attempts_today(root, 1793750400.0 + 7300)
        self.assertEqual((result["attempts"], result["last_hour"]), (2, 1))
        self.assertEqual(result["hours_to_480_at_last_hour_rate"], 478.0)

    def test_distribution_reports_ties(self):
        result = ra.distribution([0, 0, 0, 5, 9])
        self.assertEqual((result["largest_tie"], result["zero_or_missing"], result["distinct"]), (3, 3, 3))


class History(unittest.TestCase):
    def snapshot(self, root, name, at, total, surplus, ahead, n=10):
        folder = Path(root) / "logs/market-intel"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.json").write_text(json.dumps({"round": 3, "observed_at": at, "competition": {
            "rank": 5, "player_count": 20, "official_player": {"total": total, "shop": {"all": .5}, "buyer": {"all": total - .5}},
            "own": {"shops": {}, "buyers": {"surplus": surplus}},
            "diagnostic_components": {
                "shops.profit": {"weight": .5, "value": 1, "observed_participants": n, "ahead_of_percent": 50, "tied": 1},
                "buyers.surplus": {"weight": .5, "value": surplus, "observed_participants": n, "ahead_of_percent": ahead, "tied": 1},
                "buyers.forum": {"weight": .3, "value": .5, "observed_participants": n - 2, "ahead_of_percent": 50, "tied": 1}}}}), encoding="utf-8")

    def test_rising_value_with_falling_percentile_is_attributed_to_rivals(self):
        with TemporaryDirectory() as root:
            self.snapshot(root, "a", 100, 1.0, 10, 50)
            self.snapshot(root, "b", 200, .9, 20, 30)
            first, last = ra.history(root)
            result = ra.decompose(first, last)
        row = result["components"]["buyers.surplus"]
        self.assertEqual(row["driver"], "rivals_moved")
        self.assertEqual(row["value_change"], 10)
        self.assertAlmostEqual(row["points"], .5 * (3 - 5) / 9, places=3)
        self.assertEqual(result["total_change"], -.1)

    def test_falling_value_is_named(self):
        with TemporaryDirectory() as root:
            self.snapshot(root, "a", 100, 1.0, 20, 50)
            self.snapshot(root, "b", 200, .9, 10, 30)
            first, last = ra.history(root)
        self.assertEqual(ra.decompose(first, last)["components"]["buyers.surplus"]["driver"], "own_value_fell")

    def test_missing_forum_participants_count_below_us(self):
        with TemporaryDirectory() as root:
            self.snapshot(root, "a", 100, 1.0, 20, 50)
            point = ra.history(root)[0]
        self.assertAlmostEqual(point["components"]["buyers.forum"]["below"], 6.0)


if __name__ == "__main__":
    unittest.main()
