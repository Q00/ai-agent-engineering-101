"""hard_limits.py: the lines the meta-harness cannot move. Protected like the module itself: the meta-harness runs
these tests before any change stands, so a change that unhooks a guard elsewhere fails here."""
import inspect
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import agent_tools
import hard_limits as hl
import meta_harness
import restock

HERE = os.path.dirname(os.path.abspath(__file__))


class HardLimitsTest(unittest.TestCase):
    def pricing(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_the_shipped_settings_are_inside_the_bounds(self):
        self.assertEqual(hl.check_policy_change({}, self.pricing()), [])

    def test_never_spends_into_the_bankruptcy_zone(self):
        self.assertEqual(hl.spendable(900000), 0)
        self.assertEqual(hl.spendable(1500000), 500000)
        self.assertGreater(hl.STOP_BUYING_BELOW, hl.BANKRUPT_LINE)

    def test_one_bounded_step_per_cycle(self):
        old = self.pricing()
        new = json.loads(json.dumps(old))
        new["restock"]["max_stock_cost"] = old["restock"]["max_stock_cost"] * 1.4
        self.assertEqual(hl.check_policy_change(old, new), [])
        new["restock"]["max_stock_cost"] = old["restock"]["max_stock_cost"] * 2
        self.assertIn("more than 50%", hl.check_policy_change(old, new)[0])
        new = json.loads(json.dumps(old))
        new["restock"]["keep_cash"] = 900000
        self.assertTrue(any("outside" in p for p in hl.check_policy_change(old, new)))
        new = json.loads(json.dumps(old))
        new["restock"]["target_per_category"] = 2           # small integers may move by one
        self.assertEqual(hl.check_policy_change(old, new), [])
        del new["restock"]["max_break"]
        self.assertTrue(any("removed" in p for p in hl.check_policy_change(old, new)))
        new = json.loads(json.dumps(old))
        new["markdown"]["below_cost"] = ["food_expiry", "everything"]
        self.assertTrue(hl.check_policy_change(old, new))

    def test_protected_files_and_guard_lines(self):
        self.assertTrue(hl.guard_problems(["hard_limits.py"], ""))
        self.assertTrue(hl.guard_problems(["restock.py"], "-    spare = min(x, hard_limits.spendable(balance))\n"))
        self.assertEqual(hl.guard_problems(["restock.py"], "+    x = 1\n-    y = 2\n"), [])
        self.assertTrue(hl.guard_problems(["restock.py"], "+hard_limits.STOP_BUYING_BELOW = 0\n"))
        self.assertTrue(hl.guard_problems(["restock.py"], "+    setattr(hard_limits, 'BOUNDS', {})\n"))
        self.assertEqual(hl.guard_problems(["restock.py"], "+    if hard_limits.spendable(b) == 0:\n"), [])

    def test_the_sandbox_shuts_out_network_keys_and_writes(self):
        self.assertIn("(deny network*)", hl.SANDBOX)
        self.assertIn(".env", hl.SANDBOX)
        self.assertIn("(deny file-write*)", hl.SANDBOX)


class GuardsStayHookedTest(unittest.TestCase):
    CATALOG = {"H1": {"kind": "goods", "category": "가전>컵", "list_price": 20000}}

    def test_whatever_restock_decides_it_prints_nothing_past_the_line(self):
        with tempfile.TemporaryDirectory() as d:
            for name, data in (("inventory.json", {"round": 3, "balance": 1100000, "units": []}), ("catalog.json", self.CATALOG)):
                with open(os.path.join(d, name), "w") as f:
                    json.dump(data, f)
            printed = []
            env = {k: v for k, v in os.environ.items() if k != "HARNESS"}
            with mock.patch.object(restock, "read_model", lambda *a: None), \
                    mock.patch.object(restock, "plan", lambda *a, **k: {"buy": [{"sku": "H1", "qty": 20}]}), \
                    mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["x", "--state", d]), \
                    mock.patch("builtins.print", lambda *a, **k: printed.append(a[0] if a else "")):
                restock.main()
        out = json.loads(printed[-1])
        self.assertEqual(out["buy"], [{"sku": "H1", "qty": 6}])          # (1,100,000 - 1,000,000) // 15,000

    def test_settings_that_are_not_numbers_fall_back_to_the_cautious_start(self):
        cfg = hl.clamp_cfg("restock", {"food_max_qty": None, "keep_cash": "0", "max_stock_cost": 10 ** 9})
        self.assertEqual((cfg["food_max_qty"], cfg["keep_cash"], cfg["max_stock_cost"]), (2, 3000000, 4000000))
        self.assertEqual(hl.clamp_cfg("markdown", {})["below_cost"], ["food_expiry"])
        self.assertTrue(hl.check_policy_change({"restock": {"max_break": 0.1}}, {"restock": {"max_break": None}}))
        self.assertTrue(hl.check_policy_change({"markdown": {"below_cost": ["food_expiry"]}}, {"markdown": {}}))

    def test_the_meta_harness_still_checks_and_tests(self):
        src = inspect.getsource(meta_harness.cycle)
        self.assertIn("problems += limit_problems(code)", src)
        self.assertIn("run_tests()", src)
        lim = inspect.getsource(meta_harness.limit_problems)
        self.assertIn("hard_limits.check_policy_change", lim)
        self.assertIn("hard_limits.guard_problems", lim)
        self.assertIn('"discover"', inspect.getsource(meta_harness.run_tests))

    def test_no_tool_runs_without_the_sandbox(self):
        with mock.patch.object(shutil, "which", lambda name: None):
            self.assertIn("error", agent_tools.execute("my_memory", {"query": "x"}, tempfile.gettempdir()))

    @unittest.skipUnless(shutil.which("sandbox-exec"), "macOS sandbox")
    def test_a_tool_cannot_reach_the_keys_the_outbox_or_other_programs(self):
        tmp = tempfile.mkdtemp()
        try:
            tools, md = os.path.join(tmp, "tools"), os.path.join(tmp, "md")
            os.makedirs(md)
            probes = {"outbox": "import os,json;open(os.path.join(os.environ['MARKET_DIR'],'outbox.jsonl'),'a').write('x');print(1)",
                      "ps": "import subprocess,os;print(subprocess.run(['ps','eww',str(os.getppid())],capture_output=True).stdout)",
                      "history": "import os;print(len(open(os.path.expanduser('~/.zsh_history'),'rb').read()))",
                      "net": "import socket;socket.create_connection(('1.1.1.1',443),3);print(1)"}
            for name, code in probes.items():
                os.makedirs(os.path.join(tools, name))
                with open(os.path.join(tools, name, "tool.py"), "w") as f:
                    f.write(code)
            for name in probes:
                out = agent_tools.execute(name, {}, md, tools)
                self.assertIn("error", out if isinstance(out, dict) else {}, name)
            self.assertFalse(os.path.exists(os.path.join(md, "outbox.jsonl")))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
