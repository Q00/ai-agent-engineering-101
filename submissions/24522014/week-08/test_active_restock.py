from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_state import write
from restock_capacity import profitable_counts
from restock_policy import plan
from strategy_policy import BASE
import strategy_controller as controller

T = 1793774000


class ActiveCapacity(unittest.TestCase):
    def setUp(self):
        self.inv = {"round": 3, "balance": 50000000, "units": [], "restock": {"open": True}}
        self.cat = {k: {"kind": "goods", "category": "goods", "list_price": 100000} for k in ("A", "B", "C")}
        self.demand = [{"category": "goods"}]

    def run_plan(self, **kwargs):
        return plan(self.inv, self.cat, self.demand, kwargs.pop("sales", {}), now=T, **kwargs)

    def test_six_units_across_two_proven_skus(self):
        buys, info = self.run_plan(own_profitable={"A": 3, "B": 3})
        self.assertEqual(buys, [{"sku": "A", "qty": 3}, {"sku": "B", "qty": 3}])
        self.assertEqual(info["planned_units"], 6)
        self.assertEqual(info["capacity"]["max_total_units"], 16)

    def test_owned_sku_is_topped_up_to_three_not_three_more(self):
        self.cat = {"A": self.cat["A"]}
        self.inv["units"] = [{"sku": "A", "kind": "goods", "cost": 60000, "supplied_at": T} for _ in range(2)]
        self.assertEqual(self.run_plan(own_profitable={"A": 3})[0], [{"sku": "A", "qty": 1}])

    def test_total_stock_sixteen_and_run_six_are_both_enforced(self):
        self.inv["units"] = [{"sku": "held"+str(i), "kind": "goods", "cost": 100, "supplied_at": T} for i in range(15)]
        self.assertEqual(sum(x["qty"] for x in self.run_plan(own_profitable={"A": 3, "B": 3})[0]), 1)
        self.inv["units"].append({"sku": "last", "kind": "goods", "cost": 100, "supplied_at": T})
        self.assertEqual(self.run_plan(own_profitable={"A": 3})[0], [])

    def test_unproven_remains_one_per_sku_and_two_total(self):
        buys, _ = self.run_plan(leader_hints={"A": 2, "B": 2, "C": 2}, hints={"A": 2})
        self.assertEqual(sum(x["qty"] for x in buys), 2)
        self.assertTrue(all(x["qty"] == 1 for x in buys))

    def test_bulk_requires_six_public_sales_and_three_buyers(self):
        signal = {"A": {"settles_6h": 6, "distinct_buyers": 3, "distinct_buyers_6h": 3, "median_price": 100000}}
        self.assertEqual(self.run_plan(intelligence=signal)[0][0], {"sku": "A", "qty": 3})
        signal["A"]["settles_6h"] = 5
        self.assertEqual(self.run_plan(intelligence=signal)[0][0]["qty"], 1)
        signal["A"].update(settles_6h=6, distinct_buyers_6h=2)
        self.assertEqual(self.run_plan(intelligence=signal)[0][0]["qty"], 1)

    def test_old_buyers_do_not_qualify_recent_bulk_demand(self):
        from market_intel import summarize
        rows = [{"round": 3, "kind": "settle", "seq": i, "ts": T-10*i,
                 "data": {"item": "A", "amount": 100000, "buyer": "one"}} for i in range(1, 7)]
        rows += [{"round": 3, "kind": "settle", "seq": i, "ts": T-21601,
                  "data": {"item": "A", "amount": 100000, "buyer": str(i)}} for i in (7, 8)]
        signal = summarize(rows, self.cat, T)
        self.assertEqual(signal["A"]["distinct_buyers"], 3)
        self.assertEqual(signal["A"]["distinct_buyers_6h"], 1)
        self.assertEqual(self.run_plan(intelligence=signal)[0][0]["qty"], 1)

    def test_budget_and_new_seven_day_storage_are_reserved(self):
        self.inv["balance"] = 5000000
        buys, info = self.run_plan(own_profitable={"A": 3, "B": 3})
        self.assertEqual(sum(x["qty"] for x in buys), 3)
        self.assertLessEqual(info["estimated_max_cost"]+info["new_stock_seven_day_storage"], info["budget"])
        self.assertAlmostEqual(info["new_stock_seven_day_storage"], info["estimated_max_cost"]*.07)
        self.inv["balance"] = 1000000
        self.assertEqual(self.run_plan(own_profitable={"A": 3})[0], [])

    def test_lost_demand_stops_bulk_replenishment(self):
        self.cat = {"A": self.cat["A"]}
        self.inv["units"] = [{"sku": "A", "kind": "goods", "cost": 100, "supplied_at": T} for _ in range(2)]
        self.assertEqual(self.run_plan(sales={"A": 10}, own_profitable={})[0], [])

    def test_closed_window_pause_and_end_still_block(self):
        self.inv["restock"]["open"] = False
        self.assertEqual(self.run_plan(own_profitable={"A": 3})[0], [])
        self.inv["restock"]["open"] = True
        self.assertEqual(self.run_plan(runtime={"controls": {**BASE, "restock_paused": True}, "version": 1})[0], [])


class LocalCapacityEvidence(unittest.TestCase):
    def test_only_recent_distinct_local_profitable_units_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)
            records = {str(i): {"round": 3, "source": "local_acp_settled", "unit": f"u{i}", "ts": T-10,
                                   "amount": 120} for i in (1, 2, 3)}
            records["4"] = {**records["1"], "unit": "u4", "amount": 90}
            records["5"] = {**records["1"], "ts": T-21601}
            records["6"] = {**records["1"], "source": "public"}
            write(md/"local-settlements.json", {"schema": 1, "active_round": 3, "r3_anchor_found": True,
                  "conflicting_ledgers": [], "collected_at": T, "records": records})
            for i in (1, 2, 3, 4):
                write(md/f"signed-offers/{i}.json", {"unit": f"u{i}", "sku": "A", "cost": 100})
            self.assertEqual(profitable_counts(md, T), {"A": 3})
            write(md/"signed-offers/conflict.json", {"unit": "u1", "sku": "B", "cost": 100})
            self.assertEqual(profitable_counts(md, T), {"A": 2})
            self.assertEqual(profitable_counts(md, T+3601), {})


class ImmediateReassessment(unittest.TestCase):
    def test_recent_cooldown_is_reassessed_without_two_hour_delay(self):
        from test_strategy_controller import observation, trade
        state = controller.initial()
        first, _ = controller.transition(state, observation(100000), controller.config())
        first.update(phase="cooldown", checked_at=107199, last_change_at=107199)
        obs = observation(107200, 20, 4000, 2000, own_sales=[trade(i, 100000+i) for i in (1, 2, 3)])
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp);write(md/"strategy-state.json", first)
            with patch.object(controller, "observation", return_value=obs):
                result = controller.run(md, now=107200)
            self.assertEqual(result["status"], "testing")
            self.assertIn("start", result["events"])
        self.assertEqual(controller.config()["min_change_seconds"], 0)
        self.assertEqual(controller.config()["trial_seconds"], 7200)
        self.assertEqual(controller.config()["maturity_seconds"], 21600)


if __name__ == "__main__":
    unittest.main()
