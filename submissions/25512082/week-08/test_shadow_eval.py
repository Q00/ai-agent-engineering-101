import unittest
from shadow_eval import evaluate, top_overlap

CAT = {"G1": {"list_price": 10_000}, "G2": {"list_price": 20_000}}
SETTLES = [{"ts": 110, "data": {"sku": "G1", "amount": 7_000}}, {"ts": 9_999, "data": {"sku": "G2", "amount": 15_000}}]


class ShadowEvalTest(unittest.TestCase):
    def test_model_vs_rules_scored_inside_horizon_only(self):
        rows = [{"ts": 100, "model_called": True, "source": "model", "error": None, "latency_s": 2.0,
                 "prompt_sha256": "h", "model_picks_dropped": 1,
                 "enforced": [{"sku": "G1"}], "rule_proposal": [{"sku": "G2"}]}]
        out = evaluate(rows, SETTLES, CAT, horizon=1_000)
        self.assertEqual(out["pairs"][0]["model"]["hypothetical_sold"], 1)
        self.assertEqual(out["pairs"][0]["rules"]["hypothetical_sold"], 0)  # G2 settled after the horizon
        self.assertEqual(out["picks_dropped_by_rules"], 1)

    def test_failures_and_consistency(self):
        base = {"model_called": True, "latency_s": 1.0, "prompt_sha256": "h", "rule_proposal": [], "ts": 0}
        rows = [dict(base, source="model", error=None, enforced=[{"sku": "G1"}, {"sku": "G2"}]),
                dict(base, source="model", error=None, enforced=[{"sku": "G1"}]),
                dict(base, source="rules", error="TimeoutError", enforced=[])]
        out = evaluate(rows, [], CAT)
        self.assertEqual(out["model_failures"], 1)
        self.assertAlmostEqual(out["consistency_same_context"][0], 0.5)
        self.assertEqual(top_overlap([], []), 1.0)


if __name__ == "__main__":
    unittest.main()
