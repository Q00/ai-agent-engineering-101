from pathlib import Path
import tempfile
import unittest

from market_intel import summarize, fresh, competition
from market_state import write
from restock_policy import plan
from r3_policy import selling_terms
from market_intel import collect
from types import SimpleNamespace


class PublicEvidence(unittest.TestCase):
    def test_live_supply_window_closed_does_not_attempt_purchases(self):
        inv = {"round": 3, "balance": 5000000, "units": [], "restock": {"open": False}}
        buys, reason = plan(inv, {"A": {"kind": "goods", "category": "g", "list_price": 100}}, [{"category": "g"}], {})
        self.assertEqual(buys, [])
        self.assertEqual(reason["reason"], "market_restock_window_closed")

    def test_live_r3_amount_field_and_contradictory_price(self):
        row = {"seq": 1, "ts": 100, "round": 3, "kind": "settle",
               "data": {"item": "A", "amount": 90, "buyer": "b"}}
        self.assertEqual(summarize([row], {"A": {}}, 200)["A"]["median_price"], 90)
        row["data"]["price"] = 99
        self.assertEqual(summarize([row], {"A": {}}, 200), {})

    def test_live_board_forum_metric_is_observed(self):
        new = competition({"round": 3, "shops": [{"name": "kcwarni", "deals": 4, "book": 9, "revenue": 3, "stars_rank": 4}], "buyers": [{"name": "kcwarni", "deals": 4, "saved": .24, "surplus": 100, "forum": .4}]})
        self.assertEqual(new["diagnostic_components"]["buyers.saved"]["weight"], .5)
        self.assertEqual(new["diagnostic_components"]["shops.book"]["weight"], .5)
        self.assertNotIn("buyers.surplus", new["diagnostic_components"])
        # Patch 3.5: on a share board the forum rating counts as itself; before patch 3.4 it was a percentile.
        self.assertEqual(new["diagnostic_components"]["buyers.forum"]["scored_as"], "value")
        self.assertEqual(new["diagnostic_components"]["shops.book"]["scored_as"], "percentile")
        old = competition({"round": 3, "buyers": [{"name": "kcwarni", "deals": 4, "saved": 477829557, "surplus": 100, "forum": .4}]})
        self.assertIn("buyers.surplus", old["diagnostic_components"])
        self.assertEqual(old["diagnostic_components"]["buyers.forum"]["scored_as"], "percentile")
        c = competition({"round": 3, "buyers": [{"name": "kcwarni", "deals": 4, "surplus": 100, "forum": .4}]})
        self.assertEqual(c["diagnostic_components"]["buyers.forum"]["value"], .4)

    def test_bounded_collector_skips_backlog_with_explicit_gap_and_fresh_tail(self):
        calls = []
        def get(url, params):
            calls.append((url, params))
            if url.endswith("/api/shops"):
                data = {"round": 3}
            elif url.endswith("/api/board"):
                data = {"round": 3}
            else:
                starts = {-500: 1, 500: 501, 1000: 1001}
                start = 2001 if len(calls) == 6 else starts[params["after"]]
                data = [{"seq": n, "ts": 100, "kind": "order", "round": 3, "data": {}}
                        for n in range(start, start+500)]
                if len(calls) == 6:
                    data[-1].update(kind="settle", data={"item": "A", "price": 90, "buyer": "b"})
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root/".market/catalog.json", {"A": {"list_price": 100}})
            result = collect(root, client=SimpleNamespace(get=get), now=200)
        self.assertEqual(len(calls), 6)
        self.assertEqual(result["cursor"], 2500)
        self.assertEqual(result["skipped_seq_ranges"], [[1501, 2000]])
        self.assertIn("A", result["skus"])

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
