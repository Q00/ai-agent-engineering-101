"""Budget, actual-settlement evidence, restart and official hook contract tests."""
from contextlib import redirect_stdout
import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from feedback import atomic_json, load_json
from inventory import StockError
import restock
import sales_performance
from shop_policy import policy_at
from test_inventory import unit
from vendor import market_kit_v4 as kit

ROOT = Path(__file__).resolve().parent


class RestockTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.md = self.root / ".market"
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.now = time.time()
        self.cfg = policy_at()
        self.inv = {"round": 3, "balance": 5000000, "start": 5000000, "units": [],
                    "restock": {"open": True, "opens_at": self.now - 30, "closes_at": self.now + 150}}
        self.catalog = {f"G{i:05}": {"name": f"Product {i}", "kind": "goods", "list_price": 100000,
                                     "category": "furniture" if i < 4 else "appliances"} for i in range(1, 9)}
        self.demand = [{"category": "furniture"}, {"category": "appliances"}]
        atomic_json(self.root / "shop-policy.json", self.cfg)
        atomic_json(self.md / "inventory.json", self.inv)
        atomic_json(self.md / "catalog.json", self.catalog)
        atomic_json(self.md / "demand.json", self.demand)
        self.memory = {"schema": 1, "round": 3, "updated_at": self.now, "sales": []}
        network = patch("restock.refresh_market", return_value=None)
        network.start()
        self.addCleanup(network.stop)

    def receipt(self, **updates):
        return {"round": 3, "time": self.now - 60, "outcome": "receipt_issued", "unit": "u1",
                "sku": "G00001", "category": "furniture", "price": 85000, **updates}

    def write_receipts(self, rows):
        (self.logs / "inventory-seller-events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def settled(self, seq=10, unit_id="u1", price=85000):
        return f"12:00:01 [acp] settled Product at {price}, ledger #{seq}, unit {unit_id} grade A\n"

    def plan(self):
        return restock.plan(self.inv, self.catalog, self.demand, self.memory, [], self.cfg["restock"], self.now)

    def test_receipts_refusals_wrong_prices_and_other_rounds_are_not_sales(self):
        self.write_receipts([self.receipt(), self.receipt(unit="old", round=2),
                             self.receipt(unit="wrong-price"), self.receipt(unit="refused", outcome="error")])
        (self.logs / "market-kit.log").write_text(self.settled(11, "old") + self.settled(12, "wrong-price", 1)
                         + self.settled(13, "refused") + "[buyer] " + self.settled(14))
        memory = sales_performance.update(self.root, 3, self.now)
        self.assertEqual(memory["sales"], [])

    def test_confirmed_sale_survives_tail_rotation_and_is_counted_once(self):
        self.write_receipts([self.receipt()])
        path = self.logs / "market-kit.log"
        path.write_text(self.settled() * 2 + self.settled(10, "missing"))
        first = sales_performance.update(self.root, 3, self.now)
        self.assertEqual(len(first["sales"]), 1)
        path.write_text("12:00:10 [buyer] unrelated\n")
        second = sales_performance.update(self.root, 3, self.now + 100)
        self.assertEqual(second["sales"], first["sales"])
        summary = sales_performance.summary(second, 3, self.now + 100)
        self.assertEqual(summary["top_skus"][0]["sku"], "G00001")
        self.assertEqual(summary["top_skus"][0]["settled"], 1)
        self.assertEqual(summary["top_skus"][0]["unknown"], 1)
        self.assertIn("refunds", summary["window"])
        self.assertEqual(sales_performance.update(self.root, 4, self.now)["sales"], [])

    def test_old_and_partial_confirmations_do_not_create_sales(self):
        self.write_receipts([self.receipt(time=self.now - 8 * 86400)])
        path = self.logs / "market-kit.log"
        path.write_text(self.settled())
        self.assertFalse(sales_performance.update(self.root, 3, self.now)["sales"])
        self.write_receipts([self.receipt()])
        path.write_text(self.settled().rstrip("\n"))
        self.assertFalse(sales_performance.update(self.root, 3, self.now)["sales"])

    def test_cold_start_explores_at_most_five_different_skus_without_model(self):
        with patch("harness.decide", side_effect=AssertionError("No model in restock")):
            decision = restock.run_once(self.root, self.now)
        self.assertEqual(sum(b["qty"] for b in decision["buy"]), 5)
        self.assertTrue(all(b["qty"] == 1 for b in decision["buy"]))
        self.assertEqual(set(decision), {"buy"})
        rows = [json.loads(x) for x in (self.logs / "restock-decisions.jsonl").read_text().splitlines()]
        self.assertGreaterEqual(rows[-1]["projected_min_balance"], 2000000)

    def test_sales_with_unknown_costs_are_restocked_only_one_at_a_time(self):
        self.memory["sales"] = [{"sku": "G00001", "category": "furniture"}] * 4
        decision, detail = self.plan()
        counts = {b["sku"]: b["qty"] for b in decision["buy"]}
        self.assertEqual(counts["G00001"], 1)
        self.assertLessEqual(sum(counts.values()), 5)
        self.assertEqual(sum(c["reason"] == "small_exploration" for c in detail["choices"]), 1)

    def test_cash_floor_is_starting_capital_and_worst_cost_rounds_up(self):
        self.inv["balance"] = 2000077
        self.catalog = {"G00001": {"name": "cheap", "kind": "goods", "category": "furniture", "list_price": 101}}
        decision, detail = self.plan()
        self.assertEqual(decision["buy"], [{"sku": "G00001", "qty": 1}])
        self.assertEqual(detail["budgeted_cost"], 77)
        self.assertEqual(detail["projected_min_balance"], 2000000)
        self.inv["balance"] = 2000001
        self.assertEqual(self.plan()[0], {"buy": []})
        self.inv["balance"] = 499999
        self.assertEqual(self.plan()[0], {"buy": []})

    def test_total_and_food_stock_caps_include_already_held_units(self):
        self.inv["units"] = [unit(unit=str(i), sku="G00008", category="appliances") for i in range(19)]
        decision, _ = self.plan()
        self.assertLessEqual(sum(b["qty"] for b in decision["buy"]), 1)
        self.inv["units"] = [unit(sku="F00001", category="furniture", kind="food")]
        self.catalog = {"F00001": {"name": "food", "kind": "food", "list_price": 1000, "category": "furniture"}}
        self.memory["sales"] = [{"sku": "F00001", "category": "furniture"}]
        self.assertEqual(self.plan()[0], {"buy": []})

    def test_restarts_and_concurrent_hooks_cannot_repeat_plan(self):
        first = restock.run_once(self.root, self.now)
        self.assertTrue(first["buy"])
        self.assertEqual(restock.run_once(self.root, self.now + 60), {"buy": []})
        with (self.md / "restock.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(restock.run_once(self.root, self.now + 901), {"buy": []})

    def test_corrupt_controls_stale_inventory_and_stale_categories_refuse_buys(self):
        control = self.md / "restock-control.json"
        control.write_text("{broken")
        with self.assertRaises(ValueError):
            restock.run_once(self.root, self.now)
        control.unlink()
        os.utime(self.md / "inventory.json", (self.now - 100, self.now - 100))
        with self.assertRaises(StockError):
            restock.run_once(self.root, self.now)
        atomic_json(self.md / "inventory.json", self.inv)
        os.utime(self.md / "demand.json", (self.now - 500, self.now - 500))
        with self.assertRaisesRegex(ValueError, "stale"):
            restock.run_once(self.root, self.now)
        self.assertFalse(control.exists())  # No plan was emitted or supply window consumed.

    def test_round2_waits_and_kit_consumes_round3_plan_without_bankruptcy(self):
        atomic_json(self.md / "inventory.json", {**self.inv, "round": 2})
        self.assertEqual(restock.run_once(self.root, self.now), {"buy": []})
        atomic_json(self.md / "inventory.json", self.inv)
        decision = restock.run_once(self.root, self.now)
        agent = json.loads((ROOT / "agent.json").read_text())
        self.assertIn("restock.py", agent["restock"]["cmd"])
        market = Mock()
        market.call.return_value.status_code = 200
        market.call.return_value.json.return_value = {"total": 100, "balance": 4900000}
        with patch.object(kit, "run_buyer", return_value=[json.dumps(decision)]), \
                patch.object(kit, "refresh_inventory"), patch.object(kit, "log"), \
                patch.object(kit, "bankrupt", side_effect=AssertionError("No bankruptcy")):
            kit.restock(agent, self.root, market, self.md, self.inv, self.demand)
        self.assertEqual(market.call.call_count, len(decision["buy"]))
        for call, planned in zip(market.call.call_args_list, decision["buy"]):
            self.assertEqual(call.args[:2], ("POST", "/api/supply"))
            self.assertEqual(call.kwargs["json"], planned)

    def test_hook_failure_prints_empty_plan_not_random_default(self):
        with patch("sys.argv", ["restock.py", "--state", str(ROOT / ".market")]), \
                patch("restock.run_once", side_effect=StockError("stale")), \
                patch("restock.append_event") as log, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(restock.main(), 0)
        self.assertEqual(json.loads(out.getvalue()), {"buy": []})
        self.assertEqual(log.call_args.args[1]["status"], "refused")
