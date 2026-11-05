"""strategist/outcome.py on hand-built buyer and forum rows and ledger buckets: the narrowed auto-revert
(food conversion, buyer.cap_food and buyer.min_surplus_list only, both halves of the hour after),
report-only metrics, superseded, expired and waiting."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-outcome-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import policy  # noqa: E402
from strategist import outcome  # noqa: E402

T = 1_794_000_000.0         # the apply time


def orders(kind, deals, n, t0, t1):
    """n orders spread over [t0, t1), the first `deals` of them deals."""
    return [{"ts": t0 + (t1 - t0) * i / n, "kind": "order", "inputs": {"kind": kind},
             "choice": {"shop": "s"} if i < deals else "no_deal"} for i in range(n)]


def hist(base, halves, kind="food"):
    """base: (deals, n) over the 2 h before T; halves: [(deals, n), (deals, n)] for the hour after."""
    rows = orders(kind, *base, T - 7200, T)
    rows += orders(kind, *halves[0], T, T + 1800) + orders(kind, *halves[1], T + 1800, T + 3600)
    return {"buyer": rows, "forum": []}


def pol(cap_food=0.95, min_surplus=0.07):
    p = copy.deepcopy(policy.DEFAULTS)
    p["buyer"]["cap_food"], p["buyer"]["min_surplus_list"] = cap_food, min_surplus
    return p


class OutcomeTest(unittest.TestCase):
    def registered(self, h, rows, buckets=None):
        state = {}
        outcome.register(state, "r1", rows, T, h, buckets or {})
        return state

    MS = [{"key": "buyer.min_surplus_list", "old": 0.05, "new": 0.07, "source": "model"}]

    def test_v38_replay_reverts_min_surplus(self):
        h = hist((140, 200), [(12, 50), (13, 50)])
        state = self.registered(h, self.MS)
        self.assertEqual(outcome.waiting(state, T + 1800), "r1")
        self.assertEqual(outcome.check(state, h, {}, pol(), T + 1800), ([], []))    # not due yet
        reverts, outs = outcome.check(state, h, {}, pol(), T + 3700)
        self.assertEqual([(r["key"], r["value"], r["source"]) for r in reverts],
                         [("buyer.min_surplus_list", 0.05, "revert")])
        self.assertIn("buyer_food_conv 0.7->0.25 after r1", reverts[0]["reason"])
        self.assertEqual((outs[0]["result"], outs[0]["revert"]), ("revert", ["buyer.min_surplus_list"]))
        self.assertEqual(outs[0]["metrics"]["buyer_food_conv"], [0.7, 0.25])
        b = state["blocked"]["buyer.min_surplus_list"]
        self.assertEqual((b["dir"], b["until"]), (1, T + 3700 + 12 * 3600))
        self.assertEqual((state["checks"], outcome.waiting(state, T + 3700)), ([], None))

    def test_noise_kept(self):
        h = hist((144, 200), [(31, 50), (31, 50)])                      # .72 -> .62: under the 30 % drop
        state = self.registered(h, self.MS)
        reverts, outs = outcome.check(state, h, {}, pol(), T + 3700)
        self.assertEqual((reverts, outs[0]["result"]), ([], "kept"))

    def test_one_bad_half_is_not_enough(self):
        h = hist((140, 200), [(5, 50), (35, 50)])
        reverts, outs = outcome.check(self.registered(h, self.MS), h, {}, pol(), T + 3700)
        self.assertEqual((reverts, outs[0]["result"]), ([], "kept"))

    def test_too_few_orders_is_inconclusive(self):
        h = hist((140, 200), [(1, 10), (1, 10)])
        reverts, outs = outcome.check(self.registered(h, self.MS), h, {}, pol(), T + 3700)
        self.assertEqual((reverts, outs[0]["result"]), ([], "inconclusive"))

    def test_superseded_when_the_value_changed_since(self):
        h = hist((140, 200), [(5, 50), (5, 50)])
        state = self.registered(h, self.MS)
        reverts, outs = outcome.check(state, h, {}, pol(min_surplus=0.03), T + 3700)
        self.assertEqual((reverts, outs[0]["result"], outs[0]["superseded"]),
                         ([], "superseded", ["buyer.min_surplus_list"]))
        self.assertEqual(state["blocked"], {})

    def test_goods_swing_does_not_revert_an_unwatched_key(self):
        h = hist((110, 130), [(16, 32), (17, 33)], kind="goods")       # .85 -> .5: report only
        rows = [{"key": "buyer.open_list", "old": 0.6, "new": 0.65, "source": "model"}]
        reverts, outs = outcome.check(self.registered(h, rows), h, {}, pol(), T + 3700)
        self.assertEqual((reverts, outs[0]["result"]), ([], "report only"))
        self.assertEqual(outs[0]["metrics"]["buyer_goods_conv"], [0.8462, 0.5077])

    def test_report_only_metrics_shop_share_and_posts(self):
        buckets = {str(int(T - 3600)): {"settle": 10, "ours": 5}, str(int(T + 600)): {"settle": 10, "ours": 1}}
        h = {"buyer": [], "forum": [{"ts": T - 3000 + i * 300, "choice": {"posted": True}} for i in range(10)]
                                   + [{"ts": T + 100, "choice": {"posted": True}}, {"ts": T + 200, "choice": {"posted": False}}]}
        rows = [{"key": "stock.max_qty", "old": 10, "new": 15, "source": "model"}]
        state = self.registered(h, rows, buckets)
        _, outs = outcome.check(state, h, buckets, pol(), T + 3700)
        self.assertEqual(outs[0]["metrics"]["shop_share"], [0.5, 0.1])
        self.assertEqual(outs[0]["metrics"]["posts_h"], [5.0, 1.0])

    def test_only_model_rows_register(self):
        state = {}
        outcome.register(state, "r1", [{"key": "forum.min_gap_min", "old": 10, "new": 5, "source": "market-rule"},
                                       {"key": "buyer.cap_food", "old": 0.98, "new": 0.95, "source": "revert"}],
                         T, {}, {})
        self.assertEqual(state, {})

    def test_late_check_expires(self):
        h = hist((140, 200), [(5, 50), (5, 50)])
        reverts, outs = outcome.check(self.registered(h, self.MS), h, {}, pol(), T + 4 * 3600)
        self.assertEqual((reverts, outs[0]["result"]), ([], "expired"))

    def test_due_a_little_before_the_hour_measured_up_to_now(self):
        h = hist((140, 200), [(12, 50), (13, 50)])
        state = self.registered(h, self.MS)
        self.assertEqual(outcome.check(state, h, {}, pol(), T + 3600 - outcome.CHECK_SLACK_S - 1), ([], []))
        reverts, outs = outcome.check(state, h, {}, pol(), T + 3300)    # the next run, the apply 5 min after its start
        self.assertEqual([r["key"] for r in reverts], ["buyer.min_surplus_list"])
        self.assertEqual(outs[0]["n"]["buyer_food_conv"], [200, 92])   # rows of [T, T + 3300) only

    def test_broken_state_parts_are_ignored(self):
        state = {"blocked": ["x"], "checks": [None, {"ts": "x"}, {"ts": T, "keys": None}]}
        self.assertEqual(outcome.check(state, None, None, pol(), T + 3700), ([], []))
        self.assertEqual((state["blocked"], state["checks"]), ({}, []))

    def test_expired_blocks_are_dropped(self):
        state = {"blocked": {"buyer.cap_food": {"dir": 1, "until": T - 1, "why": "x"},
                             "buyer.min_surplus_list": {"dir": 1, "until": T + 99, "why": "y"}}}
        outcome.check(state, {}, {}, pol(), T)
        self.assertEqual(list(state["blocked"]), ["buyer.min_surplus_list"])


if __name__ == "__main__":
    unittest.main()
