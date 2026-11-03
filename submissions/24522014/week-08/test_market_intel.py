from pathlib import Path
import tempfile
import unittest

from market_intel import summarize, fresh, competition
from market_state import write
from restock_policy import plan
from r3_policy import selling_terms


class PublicEvidence(unittest.TestCase):
    def test_only_recent_r3_settlements_are_demand(self):
        row = {"seq": 1, "ts": 1000, "round": 3, "kind": "settle",
               "data": {"item": "A", "price": 90, "buyer": "b"}}
        rows = [row, row, {**row, "seq": 2, "round": 2}, {**row, "seq": 3, "kind": "result"},
                {**row, "seq": 4, "ts": -100000}, {**row, "seq": 5, "data": {"item": "A", "price": -1}}]
        s = summarize(rows, {"A": {}}, 1100)
        self.assertEqual(s["A"]["settles_24h"], 1)
        self.assertEqual(s["A"]["ledger_refs"], [1])

    def test_stale_snapshot_is_not_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp)/"market-intel.json", {"round": 3, "observed_at": 100})
            self.assertTrue(fresh(tmp, 200))
            self.assertEqual(fresh(tmp, 4000), {})

    def test_competition_uses_official_scores_and_ties(self):
        board = {"round": 3, "players": [{"owner": {"login": n}, "all": s}
                  for n, s in (("first", 1.9), ("second", 1.8), ("third", 1.7), ("kcwarni", 1.6))]}
        c = competition(board)
        self.assertEqual(c["rank"], 4)
        self.assertAlmostEqual(c["gap_to_third"], .1)
        self.assertIsNone(competition({"players": [{"owner": "kcwarni"}]})["rank"])

    def test_public_proof_can_replenish_while_experiments_are_full(self):
        inv = {"round": 3, "balance": 5000000, "units": [{"sku": "X"}, {"sku": "Y"}]}
        cat = {"A": {"kind": "goods", "category": "g", "list_price": 100000}}
        evidence = {"A": {"distinct_buyers": 3, "settles_6h": 3, "median_price": 90000,
                          "observed_gap_hours": 1}}
        self.assertEqual(plan(inv, cat, [{"category": "g"}], {}, intelligence=evidence)[0], [{"sku": "A", "qty": 1}])
        evidence["A"]["median_price"] = 70000
        self.assertEqual(plan(inv, cat, [{"category": "g"}], {}, intelligence=evidence)[0], [])

    def test_cash_guard_reserves_existing_storage_and_refunds(self):
        inv = {"round": 3, "balance": 1100000, "local_refund_reserve": 100000,
               "units": [{"sku": "X", "kind": "goods", "cost": 100000}]}
        self.assertEqual(plan(inv, {}, [], {})[0], [])

    def test_hot_stock_keeps_margin_and_late_stock_can_clear(self):
        u = {"unit": "u", "sku": "A", "kind": "goods", "cost": 100, "list_price": 200, "supplied_at": 0}
        cold = selling_terms(u, now=24*3600)
        hot = selling_terms(u, now=24*3600, signals={"distinct_buyers": 3, "settles_6h": 3, "median_price": 200})
        self.assertGreater(hot["quote"], cold["quote"])
        self.assertGreater(cold["floor"], 100)
        self.assertTrue(selling_terms(u, now=14*86400)["below_cost_allowed"])


if __name__ == "__main__":
    unittest.main()
