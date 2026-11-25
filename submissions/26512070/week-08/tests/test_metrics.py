"""harness.metrics.compute: the fixed metrics (design 17-2) from trajectories and events."""
import unittest

from harness.metrics import compute


def t(role, start, result=None, after=None, end=None):
    return {"role": role, "start": start, "end": end or start + 10, "result": result or {}, "after": after or {}}


class MetricsTest(unittest.TestCase):
    def test_compute(self):
        trs = [t("buyer", 1, {"agreed": 100, "paid": True}, {"surplus": 50}),
               t("buyer", 2, {"agreed": 100, "paid": False}),
               t("buyer", 3, {"outcome": "rejected", "cause": "negotiation"}),
               t("seller", 4, {"settled": True, "settled_price": 200, "cost": 150}, {"stars": 5}),
               t("seller", 5, {"outcome": "rejected"}),
               t("buyer", 99, {"agreed": 1, "paid": True}, {"surplus": 999})]          # outside the window
        evs = [{"ts": 6, "kind": "forum.votes", "data": {"as": "buyer", "post_id": 1, "likes": 1}},
               {"ts": 7, "kind": "forum.votes", "data": {"as": "buyer", "post_id": 1, "likes": 3}},
               {"ts": 7, "kind": "forum.votes", "data": {"as": "buyer", "post_id": 2, "likes": 1, "dislikes": 1}},
               {"ts": 8, "kind": "seller.missed", "data": {}},
               {"ts": 9, "kind": "tool.result", "data": {"ok": False, "reason": "tool error: KeyError"}}]
        m = compute(trs, evs, 0, 50, book=7)
        b, s, p = m["buyer"], m["shop"], m["process"]
        self.assertEqual((b["surplus"], b["orders"], b["settled"]), (50, 3, 1))
        self.assertEqual(b["agreed_unpaid_rate"], 0.5)
        self.assertEqual(b["votes_per_post"], 1.5)            # last count per post: 3 and 0
        self.assertEqual((s["revenue"], s["margin_rate"], s["new_stars_mean"]), (200, 0.25, 5.0))
        self.assertEqual((s["no_stock_rate"], s["book_change"]), (0.5, 7))
        self.assertEqual((p["tool_failures"], p["tool_failures_ours"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
