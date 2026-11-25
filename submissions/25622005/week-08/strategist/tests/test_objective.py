"""strategist/objective.py (score_twin): the buyer.min_kept target from the board's saved, the conversion gates,
the pause when the Saved rule sentence is gone, and the guard's handling of source "objective"."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-objective-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import policy  # noqa: E402
from strategist import guard, objective  # noqa: E402

FACTS = {objective.RULE_FACT: True}
ME = "psyoung82"


def buyers(*saved):
    rows = [{"name": f"b{i}", "deals": 100, "saved": s} for i, s in enumerate(saved)]
    return rows + [{"name": "house", "house": True, "deals": 9999, "saved": 0.9}, {"name": "idle", "deals": 0, "saved": 0.8}]


def brief(saved=0.2417, food=(95, 64), goods=(63, 31), board_conv=0.517):
    return {"me": {"buyer": {"saved": saved, "conversion": board_conv}},
            "buyer_log": {"food": {"orders": food[0], "deals": food[1]}, "goods": {"orders": goods[0], "deals": goods[1]}}}


def pol(min_kept=0.28):
    p = copy.deepcopy(policy.DEFAULTS)
    p["buyer"]["min_kept"] = min_kept
    return p


class AspirationTest(unittest.TestCase):
    def test_rank_among_ranked_buyers_house_and_idle_left_out(self):
        rows = buyers(*[round(0.40 - 0.01 * i, 2) for i in range(12)])   # 0.40, 0.39, ... 0.29
        self.assertEqual(objective.aspiration(rows, ME), 0.38)         # rank 3
        self.assertIsNone(objective.aspiration(buyers(0.3, 0.2), ME))
        self.assertIsNone(objective.aspiration(None, ME))

    def test_conversion_prefers_the_window_over_the_board(self):
        self.assertEqual(objective.conversion(brief()), (round(95 / 158, 4), "window"))
        self.assertEqual(objective.conversion(brief(food=(5, 2), goods=(4, 1))), (0.517, "board"))
        self.assertEqual(objective.conversion({}), (None, None))


class RowsTest(unittest.TestCase):
    def test_raises_one_step_toward_the_aspiration_plus_margin(self):
        rows, alerts, st = objective.rows(brief(), buyers(*[round(0.40 - 0.01 * i, 2) for i in range(12)]), pol(0.28), FACTS, ME)
        self.assertEqual(st["target"], 0.40)                           # rank-3 saved 0.38 + 0.02
        self.assertEqual((st["action"], alerts), ("raise", []))
        self.assertEqual([(r["key"], r["value"], r["source"]) for r in rows], [("buyer.min_kept", 0.31, "objective")])
        self.assertIn("score_twin raise", rows[0]["reason"])

    def test_target_follows_our_own_saved_when_it_is_the_higher(self):
        rows, _, st = objective.rows(brief(saved=0.35), buyers(0.30, 0.29), pol(0.28), FACTS, ME)
        self.assertEqual((st["aspire"], st["target"], rows[0]["value"]), (None, 0.37, 0.31))

    def test_the_last_step_lands_on_the_target_and_then_holds(self):
        rows, _, st = objective.rows(brief(), buyers(0.30, 0.29), pol(0.31), FACTS, ME)   # target 0.2417+0.02 -> 0.27
        self.assertEqual((st["target"], st["action"], rows), (0.27, "hold", []))          # above target: no lowering while conversion is fine
        rows, _, st = objective.rows(brief(saved=0.30), buyers(), pol(0.31), FACTS, ME)   # target 0.32
        self.assertEqual((rows[0]["value"], st["action"]), (0.32, "raise"))

    def test_low_conversion_blocks_a_raise_and_lower_still_lower_lowers_a_step(self):
        rows, _, st = objective.rows(brief(food=(50, 10), goods=(50, 15)), buyers(0.4, 0.39, 0.38), pol(0.28), FACTS, ME)
        self.assertEqual((st["conv"], st["action"], rows), (0.25, "hold", []))
        rows, _, st = objective.rows(brief(food=(50, 15), goods=(50, 25)), buyers(0.4, 0.39, 0.38), pol(0.28), FACTS, ME)
        self.assertEqual((st["conv"], st["action"], rows[0]["value"]), (0.4, "raise", 0.31))   # 0.30 is enough
        rows, _, st = objective.rows(brief(food=(50, 5), goods=(50, 12)), buyers(), pol(0.34), FACTS, ME)   # target 0.27
        self.assertEqual((st["conv"], st["action"], rows[0]["value"]), (0.17, "lower", 0.31))
        rows, _, st = objective.rows(brief(food=(50, 5), goods=(50, 12)), buyers(), pol(0.29), FACTS, ME)   # within a step of the target
        self.assertEqual((st["action"], rows), ("hold", []))

    def test_the_volume_peers_kept_share_lifts_the_target(self):
        rows = buyers(0.3882, 0.3575, 0.3569, 0.3223) + [
            {"name": "p1", "deals": 1068, "paid": 0.477, "saved": 0.3882}, {"name": "p2", "deals": 6817, "paid": 0.532, "saved": 0.3575},
            {"name": "p3", "deals": 5970, "paid": 0.561, "saved": 0.3217}, {"name": "p4", "deals": 2255, "paid": 0.583, "saved": 0.3569},
            {"name": "thin", "deals": 500, "paid": 0.30, "saved": 0.6},
            {"name": ME, "deals": 7220, "paid": 0.671, "saved": 0.2553}]
        self.assertEqual(objective.peer_kept(rows, ME), 0.439)                   # 0.523, 0.468, 0.439: not thin, not ours
        self.assertIsNone(objective.peer_kept(buyers(0.4, 0.3), ME))             # no paid, 100 deals
        out, _, st = objective.rows(brief(saved=0.2553), rows, pol(0.31), FACTS, ME)
        self.assertEqual((st["aspire"], st["peer_kept"], st["target"]), (0.3882, 0.439, 0.44))   # over 0.3882 + 0.02
        self.assertEqual((st["action"], out[0]["value"]), ("raise", 0.34))
        self.assertIn("peer kept 0.439", out[0]["reason"])

    def test_target_stays_inside_the_guard_bounds(self):
        rows, _, st = objective.rows(brief(saved=0.60), buyers(), pol(0.42), FACTS, ME)
        self.assertEqual((st["target"], rows[0]["value"]), (0.5, 0.45))

    def test_paused_without_the_saved_rule_sentence(self):
        for facts in ({}, {objective.RULE_FACT: None}):
            rows, alerts, st = objective.rows(brief(), buyers(0.4), pol(0.28), facts, ME)
            self.assertEqual((rows, st["action"]), ([], "paused"))
            self.assertEqual(len(alerts), 1)
            self.assertIn("score_twin paused", alerts[0])
            self.assertIn("buyer.min_kept is left to the model", alerts[0])

    def test_no_saved_on_the_board_is_a_hold(self):
        rows, alerts, st = objective.rows({"me": {}}, buyers(0.4), pol(0.28), FACTS, ME)
        self.assertEqual((rows, alerts, st["action"]), ([], [], "hold"))


class GuardTest(unittest.TestCase):
    def test_objective_rows_pass_without_cooldown_or_max_changes_and_only_for_min_kept(self):
        p = pol(0.28)
        now = 1_794_000_000.0
        rows = [{"key": "buyer.min_kept", "value": 0.31, "source": "objective", "reason": "score_twin"},
                {"key": "buyer.cap_food", "value": 0.9, "source": "objective"},
                {"key": "seller.first_ask_list", "value": 0.9, "source": "model"}]
        acc, rej = guard.guard(rows, p, {"buyer.min_kept": now - 60}, now, max_changes=0, cooldown_h=3)
        self.assertEqual([(a["key"], a["old"], a["new"], a["source"]) for a in acc], [("buyer.min_kept", 0.28, 0.31, "objective")])
        self.assertEqual([(r["key"], r["why"]) for r in rej], [("buyer.cap_food", "not an objective key"),
                                                               ("seller.first_ask_list", "over max_changes")])

    def test_a_model_row_for_the_same_key_is_a_duplicate(self):
        p = pol(0.28)
        rows = [{"key": "buyer.min_kept", "value": 0.31, "source": "objective"},
                {"key": "buyer.min_kept", "value": 0.25, "source": "model"}]
        acc, rej = guard.guard(rows, p, {}, 1_794_000_000.0)
        self.assertEqual([a["new"] for a in acc], [0.31])
        self.assertEqual([r["why"] for r in rej], ["duplicate"])

    def test_objective_value_is_clamped_to_the_bounds(self):
        acc, _ = guard.guard([{"key": "buyer.min_kept", "value": 0.9, "source": "objective"}], pol(0.28), {}, 1.0)
        self.assertEqual(acc[0]["new"], 0.5)


if __name__ == "__main__":
    unittest.main()
