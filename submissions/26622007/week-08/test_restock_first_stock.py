"""Behavior checks for the finite first-stock supply allocation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import restock
import restock_first_stock as trial


class FirstStockTest(unittest.TestCase):
    def setUp(self):
        self.inv = {"restock": {"opens_at": 120000, "closes_at": 120180}}
        self.catalog = {trial.SKU: {"kind": "food", "shelf_hours": 48}}
        self.model = {"active": True, "skus": {trial.SKU: {"mine_rate": 0, "deals": 20}}}
        self.switch = {"enabled": True, "study": "first-stock-test", "start": 119950,
                       "stop": 123550, "b_parity": 0}

    def config(self, switch=None, **kwargs):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / trial.SWITCH).write_text(json.dumps(self.switch if switch is None else switch))
            return trial.config(td, self.inv, kwargs.get("model", self.model), self.catalog,
                                kwargs.get("listed", []), now=kwargs.get("now", 120001))

    def test_finite_eligibility_and_arm_boundaries(self):
        self.assertEqual(self.config()["arm"], "B")
        self.assertEqual(self.config({**self.switch, "b_parity": 1})["arm"], "A")
        for now in (119949, 120180, 123550):
            self.assertIsNone(self.config(now=now))
        self.assertIsNone(self.config(listed=[trial.SKU]))
        self.assertIsNone(self.config(model={"active": True, "skus": {trial.SKU: {"mine_rate": 1, "deals": 20}}}))
        self.assertIsNone(self.config(model={"active": True, "skus": {trial.SKU: {"deals": 20}}}))
        self.assertIsNone(self.config({**self.switch, "stop": 999999}))

    def test_existing_guard_counts_pending_and_duplicate_lines_before_cash(self):
        catalog = {trial.SKU: {"kind": "food", "list_price": 100, "shelf_hours": 48}}
        inv = {"balance": 10**9, "units": []}
        cfg = {**restock.DEFAULTS, "first_stock": {"arm": "B"}, "keep_cash": 0,
               "cash_buffer": 0, "food_max_stock_cost": 10**8, "max_stock_cost": 10**8}
        rows = [{"sku": trial.SKU, "qty": 3}, {"sku": trial.SKU, "qty": 5}]
        self.assertEqual(restock.guard(rows, inv, catalog, cfg), [{"sku": trial.SKU, "qty": 1}])
        waiting = [{"sku": trial.SKU, "qty": 1, "worst": 75}]
        self.assertEqual(restock.guard(rows, inv, catalog, cfg, waiting=waiting), [])
        self.assertEqual(restock.guard(rows, {**inv, "balance": 0}, catalog, cfg), [])
        # Off/A keep the original guard behavior and all original caps.
        self.assertEqual(restock.guard(rows[:1], inv, catalog, {**cfg, "first_stock": {"arm": "A"}}), rows[:1])
        self.assertEqual(restock.guard(rows, inv, catalog, {**cfg, "skip_skus": [trial.SKU]}), [])

    def test_absent_or_malformed_switch_does_not_change_supply(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(trial.config(td, self.inv, self.model, self.catalog, [], now=120001))
            (Path(td) / trial.SWITCH).write_text("broken")
            self.assertIsNone(trial.config(td, self.inv, self.model, self.catalog, [], now=120001))
        self.assertEqual(trial.quantity(None, trial.SKU, 3, 0), 3)
        self.assertEqual(trial.quantity({"arm": "B"}, "other", 3, 0), 3)


class FirstStockMainRecordTest(unittest.TestCase):
    def run_main(self, root, arm="B", held=0, fail_write=False, injected=False, fast=False):
        import contextlib, io, os, sys, time
        state = root / "market"
        state.mkdir(exist_ok=True)
        sku = trial.SKU
        now = 120001
        inv = {"round": 3, "balance": 10**9, "units": [],
               "restock": {"open": True, "opens_at": 120000, "closes_at": 120180}}
        catalog = {sku: {"kind": "food", "list_price": 100, "shelf_hours": 48}}
        (state / "inventory.json").write_text(json.dumps(inv))
        (state / "catalog.json").write_text(json.dumps(catalog))
        (state / "demand.json").write_text("[]")
        pricing = {"restock": {"first_stock": {"arm": "B"}}} if injected else {}
        (root / "pricing.json").write_text(json.dumps(pricing))
        switch = state / trial.SWITCH
        switch.write_text(json.dumps({"enabled": True, "study": "record-test", "start": 119950,
                                      "stop": 123550, "b_parity": 0 if arm == "B" else 1}))
        model = {"active": True, "skus": {sku: {"mine_rate": 0, "deals": 20}}}
        waiting = [{"sku": sku, "qty": held, "worst": 75}] if held else []
        original_guard = restock.guard
        def plan(inv, catalog, demand, cfg, *args, **kwargs):
            # Repeated evaluation must not sum the same reduction twice.
            original_guard([{"sku": sku, "qty": 3}], inv, catalog, cfg, waiting=waiting)
            return {"buy": original_guard([{"sku": sku, "qty": 3}], inv, catalog, cfg, waiting=waiting)}
        def fast_path(state, inv, pricing, cfg, catalog, note=None):
            self.assertIsNone(cfg.get("first_stock"), "pricing cannot inject a trial into the fast guard")
            return {"buy": []}
        oldcwd = os.getcwd()
        output = io.StringIO()
        try:
            os.chdir(root)
            with contextlib.ExitStack() as st:
                st.enter_context(patch.dict(os.environ, HARNESS=""))
                st.enter_context(patch.object(time, "time", return_value=now))
                st.enter_context(patch.object(sys, "argv", ["restock.py", "--state", str(state)]))
                st.enter_context(patch.object(restock, "read_model", return_value=model))
                st.enter_context(patch.object(restock, "read_breaks", return_value=None))
                st.enter_context(patch.object(restock, "pending", return_value=waiting))
                st.enter_context(patch.object(restock, "warranted_sales", return_value=[]))
                st.enter_context(patch.object(restock, "plan", side_effect=plan))
                st.enter_context(patch.object(restock, "log_prices", return_value=None))
                st.enter_context(patch.object(restock.loss_watch, "check", return_value=None))
                st.enter_context(patch.object(restock.agent_memory, "maybe_consolidate", return_value=None))
                st.enter_context(patch.object(restock.shop_forum, "maybe_run", return_value=None))
                st.enter_context(patch.object(restock.measure, "tick", return_value=None))
                if fast: st.enter_context(patch.object(restock, "window_fast", side_effect=fast_path))
                if fail_write: st.enter_context(patch.object(trial.os, "fchmod", side_effect=OSError("denied")))
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(restock.main(), 0)
            return json.loads(output.getvalue().splitlines()[-1]), state
        finally:
            os.chdir(oldcwd)

    def test_main_records_A_B_and_zero_once_without_accumulating_evaluations(self):
        import stat
        for arm, held, proposed, removed in [("A", 0, 3, 0), ("B", 0, 1, 2), ("B", 1, 0, 3)]:
            with self.subTest(arm=arm, held=held), tempfile.TemporaryDirectory() as td:
                answer, state = self.run_main(Path(td), arm=arm, held=held)
                path = state / "measure/restock_first_stock.jsonl"
                self.assertTrue(path.exists(), "eligible main execution must record even zero proposal")
                rows = [json.loads(x) for x in path.read_text().splitlines()]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["proposed_qty"], proposed)
                self.assertEqual(rows[0]["candidate_before_qty"], 3)
                self.assertEqual(rows[0]["candidate_removed_qty"], removed)
                self.assertEqual(rows[0]["held_waiting_qty"], held)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(sum(x["qty"] for x in answer["buy"]), proposed)

    def test_main_record_permission_failure_keeps_purchase(self):
        with tempfile.TemporaryDirectory() as td:
            answer, state = self.run_main(Path(td), fail_write=True)
            self.assertEqual(answer["buy"], [{"sku": trial.SKU, "qty": 1}])
            path = state / "measure/restock_first_stock.jsonl"
            self.assertFalse(path.exists() and path.read_text())

    def test_main_clears_config_injection_before_fast_path(self):
        with tempfile.TemporaryDirectory() as td:
            answer, state = self.run_main(Path(td), injected=True, fast=True)
            self.assertEqual(answer["buy"], [])
            self.assertFalse((state / "measure/restock_first_stock.jsonl").exists())


class FirstStockRecordBoundaryTest(unittest.TestCase):
    def on(self):
        return {"study": "record-boundary", "opens_at": 120000, "arm": "B",
                "model_active": True, "mine_rate": 0.0, "deals": 3, "listed": False}

    def test_zero_without_candidate_and_append_permission_and_allowlist(self):
        import stat
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "measure/restock_first_stock.jsonl"
            path.parent.mkdir()
            path.write_text('{"prior":true}\n')
            path.chmod(0o644)
            on = self.on()
            on["forbidden_payload"] = "private-marker-never-copy"
            trial.record(td, on, [], {"units": []}, [])
            rows = path.read_text().splitlines()
            self.assertEqual(rows[0], '{"prior":true}')
            row = json.loads(rows[1])
            self.assertEqual(row["proposed_qty"], 0)
            self.assertEqual(row["candidate_before_qty"], 0)
            self.assertNotIn("private-marker", rows[1])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_missing_config_nonfinite_and_append_failure_never_raise(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            trial.record(td, None, [], {"units": []}, [])
            on = self.on()
            on["mine_rate"] = float("nan")
            trial.record(td, on, [], {"units": []}, [])
            self.assertFalse((root / "measure/restock_first_stock.jsonl").exists())
            with patch.object(trial.os, "open", side_effect=OSError("denied")):
                trial.record(td, self.on(), [], {"units": []}, [])
            self.assertFalse((root / "measure/restock_first_stock.jsonl").exists())
