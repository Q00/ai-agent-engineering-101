"""tools.evaluate on a small synthetic day of events written with the real EventWriter.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, paths  # noqa: E402
from core.events import EventWriter  # noqa: E402
from tools import evaluate  # noqa: E402,F401  (registers the tools)
from test_calc_check import Base, STRATEGY  # noqa: E402


class EvalTest(Base):
    def setUp(self):
        super().setUp()
        s, b, r, m = (EventWriter(n, n) for n in ("seller", "buyer", "restock", "models"))
        for w in (s, b, r, m):
            w.context(strategy_version=1, epoch="e1", card_version=1)
        for t, outcome, price, cost in (("t1", "completed", 3300, 3000), ("t2", "completed", 2700, 3000),
                                        ("t3", "canceled", None, 3000)):
            s.emit("seller.request", {"excerpt": "..."}, task=t)
            s.emit("seller.decision", {"action": "counter", "price": 3500, "ms": 3100, "guard": None}, task=t)
            s.emit("seller.closed", {"outcome": outcome, "price": price, "cost": cost, "claim": "B" if t == "t1" else None,
                                     "claim_true": True}, task=t)
            if outcome == "completed":
                s.emit("seller.settled", {"price": price, "cost": cost, "ledger": 10,
                                          **({"claim": "B", "claim_true": True} if t == "t1" else {})}, task=t)
        b.emit("buyer.order", {"category": "김치", "budget": 30000}, order="o1")
        b.emit("buyer.pick", {"shop": "elm", "explored": True}, order="o1")
        b.emit("buyer.closed", {"outcome": "completed", "agreed": 20000, "receipt_price": 20000, "verdict": "fair"}, order="o1")
        b.emit("buyer.payment", {"paid": True, "price": 20000}, order="o1")
        b.emit("buyer.received", {"value": 36900, "price": 20000, "surplus": 16900}, order="o1")
        b.emit("buyer.order", {"category": "김치", "budget": 30000}, order="o2")
        b.emit("buyer.closed", {"outcome": "completed", "agreed": 20000, "receipt_price": 23000,
                                "verdict": "cheated: agreed 20000, receipt 23000"}, order="o2")
        b.emit("buyer.payment", {"paid": False, "reason": "no certificate, and the policy asks for one"}, order="o2")
        r.emit("restock.bought", {"qty": 4, "cost_total": 12000, "kind": "goods"})
        r.emit("restock.plan", {"lines": 1, "refused": None, "used_model": True})
        m.emit("model.call", {"seat": "seller", "model": "haiku", "ms": 2900, "ok": True})
        m.emit("model.call", {"seat": "seller", "model": "haiku", "ms": 3100, "ok": False, "rate_limited": True})
        for w in (s, b, r, m):
            w.close()

    def test_funnels(self):
        f = self.ok("eval.funnel", scope="seller")
        self.assertEqual((f["negotiations"], f["completed"], f["settled"], f["lost_by"]), (3, 2, 2, {"canceled": 1}))
        g = self.ok("eval.funnel", scope="buyer")
        self.assertEqual((g["orders"], g["agreed"], g["paid"], g["useful"]), (2, 2, 1, 1))
        self.assertEqual(list(g["unpaid_by"]), ["no certificate, and the policy asks for one"])

    def test_economics_and_stock(self):
        e = self.ok("eval.unit_economics", scope="seller")
        self.assertEqual((e["revenue"], e["profit_before_losses"], e["below_cost_deals"], e["exposure_cost"]),
                         (6000, 0, 1, 300))
        self.assertEqual(self.ok("eval.unit_economics", scope="buyer")["surplus"], 16900)
        st = self.ok("eval.stock_health")
        self.assertEqual((st["bought"], st["sold"], st["sell_through"]), (4, 2, 0.5))

    def test_quality_and_integrity(self):
        q = self.ok("eval.decision_quality", scope="seller")
        self.assertEqual((q["model_calls"], q["failed"], q["rate_limited"]), (2, 1, 1))
        i = self.ok("eval.integrity")
        self.assertEqual((i["cheats_caught"], i["our_claims"], i["our_claims_true"]), (1, 1, 1))

    def test_epoch_slice(self):
        self.assertEqual(self.ok("eval.funnel", scope="seller", epoch="e2")["closed"], 0)

    def test_experiment_judgement(self):
        exp = {"min_sample": {"metric": "funnel.seller.closed", "value": 3},
               "success_if": [{"metric": "funnel.seller.completion_rate", "op": ">=", "value": 0.6}],
               "rollback_if": [{"metric": "unit_economics.seller.below_cost_deals", "op": ">", "value": 2}]}
        r = self.ok("eval.experiment", experiment=exp)
        self.assertEqual(r["verdict"], "success")
        self.assertEqual(self.ok("eval.experiment", experiment=dict(exp, min_sample={"metric": "funnel.seller.closed",
                                                                                      "value": 10}))["verdict"],
                         "not enough data")
        self.assertIn("unknown metric", self.refused("eval.experiment", experiment=dict(
            exp, min_sample={"metric": "nope.x", "value": 1}))["reason"])

    def test_score_needs_collection(self):
        self.assertIn("no scoreboard", self.refused("eval.score")["reason"])
        atomic.write_json(paths.memory() / "facts" / "scoreboard.json", {"players_percentile": 0.42})
        self.assertEqual(self.ok("eval.score")["players_percentile"], 0.42)


if __name__ == "__main__":
    unittest.main()
