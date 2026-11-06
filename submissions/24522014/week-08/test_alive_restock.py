"""A market reading of alive False alone leaves restock to its cash and stock limits, and stays out of every experiment."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from market_state import read, write
import strategy_controller as controller
from strategy_policy import BASE, effective
from restock_policy import plan

KST = timezone(timedelta(hours=9))


class AliveRestock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.md = self.root / ".market"
        self.now = time.time()
        self.log = self.root / "logs/market-kit.log"
        self.log.parent.mkdir(parents=True)
        clock = datetime.fromtimestamp(self.now, KST).strftime("%H:%M:%S")
        self.log.write_text(f"{clock} [market] shop registered as kcwarni, round 3\n{clock} [acp] settled fixture at 100, ledger #1, unit u grade A\n", encoding="utf8")
        os.utime(self.log, (self.now, self.now))
        write(self.md / "signed-offers/0.json", {"unit": "u", "sku": "G1", "cost": 80, "price": 100, "issued_at": self.now-10})
        self.market()
        self.inventory()

    def market(self, alive=False, observed_at=None, **shop):
        fields = {"alive": alive, "deals": 10, "visits": 20, "revenue": 2000, "stars": 5, "reviews": 10, **shop}
        if alive is None:
            fields.pop("alive")
        write(self.md / "market-intel.json", {"round": 3, "observed_at": self.now if observed_at is None else observed_at, "competition": {"own": {"shops": fields}},
                                              "rows": [{"round": 3, "kind": "settle", "seq": 1, "ts": self.now, "data": {"unit": "u", "item": "G1", "grade": "A", "amount": 100}}]})

    def inventory(self, balance=5000000):
        write(self.md / "inventory.json", {"round": 3, "balance": balance, "units": [], "restock": {"open": True}})

    def run_it(self):
        result = controller.run(self.md, now=self.now, force=True)
        return result, read(self.md / "strategy-policy.json")["controls"], read(self.md / "strategy-state.json")

    def test_alive_false_alone_keeps_restock_open_and_starts_nothing(self):
        result, controls, state = self.run_it()
        self.assertFalse(controls["restock_paused"])
        self.assertEqual(controls, BASE)
        self.assertEqual(result["status"], "waiting_for_fresh_r3_data")
        self.assertIsNone(state["active"])
        self.assertEqual(state["history"], [])
        self.assertEqual(state["experiments_today"], 0)
        self.assertIsNone(read(self.md / "strategy-policy.json")["experiment"])
        observation = controller.observation(self.md, self.now)
        self.assertFalse(observation["valid"])
        self.assertTrue(observation["shop_not_alive"])

    def test_the_real_plan_buys_inside_its_limits(self):
        self.run_it()
        runtime = effective(self.md, self.now+1)
        self.assertEqual(runtime["controls"]["restock_budget_rate"], BASE["restock_budget_rate"])
        inventory = {"round": 3, "balance": 5000000, "units": []}
        catalog = {sku: {"kind": "goods", "category": "g", "list_price": 100000} for sku in ("A", "B")}
        buys, info = plan(inventory, catalog, [{"category": "g"}], {}, now=self.now+1, runtime=runtime)
        self.assertTrue(buys)
        self.assertNotEqual(info.get("reason"), "strategy_cash_or_data_guard")
        full = {"round": 3, "balance": 5000000, "units": [{"sku": "A", "kind": "goods", "grade": "A", "cost": 1} for _ in range(80)]}
        self.assertEqual(plan(full, catalog, [{"category": "g"}], {}, now=self.now+1, runtime=runtime)[1]["reason"], "stock_or_cash_limit")

    def test_alive_false_with_old_market_data_pauses(self):
        self.market(observed_at=self.now-3700)
        self.assertTrue(self.run_it()[1]["restock_paused"])

    def test_alive_false_with_old_inventory_pauses(self):
        os.utime(self.md / "inventory.json", (self.now-300, self.now-300))
        self.assertTrue(self.run_it()[1]["restock_paused"])

    def test_alive_false_with_broken_local_evidence_pauses(self):
        os.utime(self.log, (self.now-1000, self.now-1000))
        result, controls, _ = self.run_it()
        self.assertTrue(controls["restock_paused"])
        self.assertEqual(result["status"], "waiting_for_fresh_r3_data")

    def test_alive_false_with_a_missing_board_number_pauses(self):
        self.market(deals=None)
        self.assertTrue(self.run_it()[1]["restock_paused"])

    def test_a_missing_or_odd_alive_pauses(self):
        for alive in (None, "false", 0):
            with self.subTest(alive=alive):
                self.market(alive=alive)
                self.assertTrue(self.run_it()[1]["restock_paused"])

    def test_alive_true_is_valid_and_open_as_before(self):
        self.market(alive=True)
        observation = controller.observation(self.md, self.now)
        self.assertTrue(observation["valid"])
        self.assertNotIn("shop_not_alive", observation)
        _, controls, state = self.run_it()
        self.assertFalse(controls["restock_paused"])
        self.assertEqual(len(state["history"]), 1)

    def test_low_cash_pauses(self):
        for balance in (500000, 400000):
            with self.subTest(balance=balance):
                self.inventory(balance)
                self.assertTrue(self.run_it()[1]["restock_paused"])

    def test_low_cash_with_alive_true_still_pauses(self):
        self.market(alive=True)
        self.inventory(500000)
        self.assertTrue(self.run_it()[1]["restock_paused"])

    def test_a_held_cash_guard_is_not_lifted_by_this_path(self):
        state = controller.initial()
        state["controls"]["restock_paused"] = True
        write(self.md / "strategy-state.json", state)
        self.assertTrue(self.run_it()[1]["restock_paused"])

    def test_a_running_experiment_ends_without_a_verdict(self):
        state = controller.initial()
        state["active"] = {"id": "trial", "phase": "testing", "kind": "budget", "candidate": {**BASE, "restock_budget_rate": .02}, "before": json.loads(json.dumps(BASE))}
        state["controls"] = {**BASE, "restock_budget_rate": .02}
        write(self.md / "strategy-state.json", state)
        _, controls, after = self.run_it()
        self.assertIsNone(after["active"])
        self.assertEqual(after["controls"], BASE)
        self.assertEqual(controls, BASE)
        self.assertEqual(after["history"], [])
        self.assertNotIn("last_rejected", after)


if __name__ == "__main__":
    unittest.main()
