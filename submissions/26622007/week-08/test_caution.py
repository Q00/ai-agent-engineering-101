"""The cautious start and what lets it grow: restock guard, markdown, memory, the tool sandbox, the tool forge, the
meta-harness's limit check."""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import agent_memory
import agent_tools
import hard_limits
import meta_harness
import restock
import tool_forge
from shop_stock import markdown

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = {"F1": {"kind": "food", "category": "식품>김치", "list_price": 20000, "shelf_hours": 720},
           "H1": {"kind": "goods", "category": "가전>컵", "list_price": 20000},
           "H2": {"kind": "goods", "category": "가전>컵", "list_price": 20000}}
DEMAND = [{"category": "식품>김치", "kind": "food", "median_price": 20000},
          {"category": "가전>컵", "kind": "goods", "median_price": 20000}]


def shipped():
    with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
        p = json.load(f)
    return {**restock.DEFAULTS, **p["restock"], "round_ends_at": None}


class RestockCautionTest(unittest.TestCase):
    def test_first_stock_is_one_goods_unit_and_no_food(self):
        out = restock.plan({"balance": 5000000, "units": []}, CATALOG, DEMAND, shipped())
        self.assertEqual(len(out["buy"]), 1)
        self.assertEqual(CATALOG[out["buy"][0]["sku"]]["kind"], "goods")
        self.assertEqual(out["buy"][0]["qty"], 1)

    def test_guard_caps_food_stock_cost_and_the_bankruptcy_line(self):
        cfg = shipped()
        inv = {"balance": 5000000, "units": []}
        self.assertEqual(restock.guard([{"sku": "F1", "qty": 20}], inv, CATALOG, cfg), [{"sku": "F1", "qty": cfg["food_max_qty"]}])
        held = [{"unit": str(i), "sku": "H1", "cost": 15000} for i in range(66)]      # 990,000 of stock already
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 5}], {"balance": 5000000, "units": held}, CATALOG, cfg), [])
        loose = {**cfg, "keep_cash": 0, "max_stock_cost": float("inf")}
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 20}], {"balance": 1010000, "units": []}, CATALOG, loose), [])
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 20}], {"balance": 1030000, "units": []}, CATALOG, loose),
                         [{"sku": "H1", "qty": 2}])                                      # 15,000 worst cost each

    def test_goods_that_break_often_are_left_out(self):
        breaks = {"sold": 100, "broke": 10, "rate": 0.1, "by_sku": {"H1": {"sold": 50, "broke": 25}}}
        out = restock.guard([{"sku": "H1", "qty": 1}, {"sku": "H2", "qty": 1}], {"balance": 5000000, "units": []},
                            CATALOG, shipped(), breaks)
        self.assertEqual(out, [{"sku": "H2", "qty": 1}])


class MarkdownCautionTest(unittest.TestCase):
    NOW = 1800000000.0

    def test_only_food_near_its_date_goes_under_cost(self):
        cfg = {"below_cost": ["food_expiry"]}
        food = {"cost": 1000, "list_price": 2000, "supplied_at": self.NOW - 500 * 3600, "expires_at": self.NOW + 6 * 3600}
        goods = {"cost": 1000, "list_price": 2000, "supplied_at": self.NOW - 20 * 86400, "expires_at": None}
        self.assertLess(markdown(food, cfg, None, self.NOW)[0], 1000)
        self.assertEqual(markdown(goods, cfg, None, self.NOW)[0], 1000)
        self.assertEqual(markdown(goods, cfg, self.NOW + 3600, self.NOW)[0], 1000)       # not at the end of the round either


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.md = os.path.join(self.tmp, "md")
        os.makedirs(self.md)
        self.env = mock.patch.dict(os.environ, {"AGENT_MEMORY_DIR": os.path.join(self.tmp, "mem")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp)

    def test_a_mistake_becomes_a_lesson_that_comes_back_when_it_matters(self):
        results = {"never_sold": [{"sku": "F1", "asked": 4, "sold": 0, "left": 0, "gone_unsold": 4, "hours_since_first": 30}],
                   "best_skus": []}
        agent_memory.learn_from_outcomes(self.md, results, CATALOG)
        self.assertIn("thrown away", agent_memory.select(self.md, ["F1"]))
        self.assertIn("thrown away", agent_memory.select(self.md, ["식품>김치"]))
        self.assertNotIn("thrown away", agent_memory.select(self.md, ["H1", "가전>컵", "goods"]))
        self.assertIn("thrown away", agent_memory.lessons_for(["F1"]))

    def test_selection_stays_within_its_budget_and_daily_lines_show(self):
        agent_memory.daily(self.md, "sale", "H1 sold at 18000")
        text = agent_memory.select(self.md, ["H1"], limit=600)
        self.assertLessEqual(len(text), 600)
        self.assertIn("H1 sold", text)

    def test_old_days_fold_into_memory_under_the_cap(self):
        old = time.time() - 10 * 86400
        for i in range(3):
            agent_memory.daily(self.md, "restock", f"bought {i}", now=old)
        with mock.patch.object(agent_memory, "cfg", lambda: {**agent_memory.DEFAULTS, "long_term_chars": 700}):
            self.assertTrue(agent_memory.maybe_consolidate(self.md))
            for d in range(40):
                agent_memory.add_line("## Past days", f"day {d} " + "x" * 30)
            text = agent_memory.long_term()
        self.assertLessEqual(len(text), 701)
        self.assertIn("day 39", text)
        self.assertNotIn("day 0 ", text)
        self.assertTrue(os.listdir(os.path.join(self.tmp, "mem", "daily", "archive")))
        self.assertFalse(agent_memory.maybe_consolidate(self.md))           # not due again yet

    def test_the_memory_tool_searches_the_notebook(self):
        agent_memory.daily(self.md, "sale", "H2 sold at 9000")
        out = json.loads(agent_tools.run("my_memory", {"query": "H2"}, self.md, folder=os.path.join(HERE, "tools")))
        self.assertEqual(len(out["hits"]), 1)


class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tools, self.md = os.path.join(self.tmp, "tools"), os.path.join(self.tmp, "md")
        os.makedirs(self.md)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def tool(self, name, code, selftest=None):
        os.makedirs(os.path.join(self.tools, name))
        with open(os.path.join(self.tools, name, "tool.py"), "w") as f:
            f.write(code)
        m = {"purpose": name, **({"selftest": {"args": selftest}} if selftest is not None else {})}
        with open(os.path.join(self.tools, name, "manifest.json"), "w") as f:
            json.dump(m, f)

    @unittest.skipUnless(shutil.which("sandbox-exec"), "macOS sandbox")
    def test_no_network_no_secrets_no_writes_outside(self):
        secret = os.path.join(self.tmp, ".env")
        with open(secret, "w") as f:
            f.write("X=1")
        self.tool("net", "import socket,json;socket.create_connection(('1.1.1.1',443),3);print(json.dumps({}))")
        self.tool("env", f"import json;print(json.dumps(open({secret!r}).read()))")
        self.tool("write", f"import json;open({os.path.join(HERE, 'x.txt')!r},'w');print(json.dumps({{}}))")
        self.tool("keys", "import os,json;print(json.dumps(sorted(k for k in os.environ if 'KEY' in k)))")
        self.tool("ok", "import os,json;open(os.path.join(os.environ['MARKET_DIR'],'f'),'w').write('1');print(json.dumps({'ok':1}))")
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-test-value-123456"}):
            for name in ("net", "env", "write"):
                self.assertIn("error", json.loads(agent_tools.run(name, {}, self.md, folder=self.tools)), name)
            self.assertEqual(json.loads(agent_tools.run("keys", {}, self.md, folder=self.tools)), [])
        self.assertEqual(json.loads(agent_tools.run("ok", {}, self.md, folder=self.tools)), {"ok": 1})
        self.assertFalse(os.path.exists(os.path.join(HERE, "x.txt")))

    def test_failing_tools_are_left_out(self):
        self.tool("bad", "raise SystemExit(3)", selftest={})
        self.tool("good", "import json;print(json.dumps(1))", selftest={})
        self.tool("flaky", "import json;print(json.dumps(1))")
        with open(os.path.join(self.md, "tool_calls.jsonl"), "w") as f:
            for _ in range(5):
                f.write(json.dumps({"tool": "flaky", "ok": False}) + "\n")
        self.assertEqual(list(agent_tools.discover(self.tools, md=self.md)), ["good"])
        with open(os.path.join(self.md, "tools_status.json")) as f:
            self.assertEqual(sorted(json.load(f)["left_out"]), ["bad", "flaky"])


class ForgeTest(unittest.TestCase):
    GOOD = "import json, os, sys\nargs = json.load(sys.stdin)\nmd = os.environ['MARKET_DIR']\nprint(json.dumps({'n': len(os.listdir(md))}))\n"

    def test_the_code_check(self):
        self.assertEqual(tool_forge.check_code(self.GOOD), [])
        for bad in ("import socket", "import subprocess", "from urllib import request", "eval('1')",
                    "import os\nos.environ['OPENROUTER_API_KEY']", "import os\nos.environ.get('HOME')", "import os\nos.remove('x')",
                    "open('x', 'w')", "import os\ndict(os.environ)"):
            self.assertTrue(tool_forge.check_code(bad), bad)

    def test_repeated_work_becomes_a_tool_or_is_rejected(self):
        tmp = tempfile.mkdtemp()
        try:
            md, tools = os.path.join(tmp, "md"), os.path.join(tmp, "tools")
            os.makedirs(md)
            os.makedirs(tools)
            with open(os.path.join(md, "tool_calls.jsonl"), "w") as f:
                for _ in range(8):
                    f.write(json.dumps({"tool": "ledger_search", "args": {"sku": "A"}, "ok": True}) + "\n")
            self.assertTrue(tool_forge.repeats(md))
            answer = {"make": True, "name": "count files", "purpose": "p", "args_schema_json": "{}", "code": self.GOOD,
                      "selftest_args_json": "{}", "why": "w"}
            with mock.patch.object(agent_tools, "TOOLS", tools), mock.patch.dict(os.environ, {"AGENT_MEMORY_DIR": os.path.join(tmp, "m")}), \
                    mock.patch("builtins.print"):
                self.assertEqual(tool_forge.maybe_run(md, lambda *a, **k: answer), "auto_count_files")
                self.assertIn("auto_count_files", agent_tools.discover(tools, md=md))
                self.assertIsNone(tool_forge.maybe_run(md, lambda *a, **k: answer))            # not due again
                broken = dict(answer, name="broken one", code="import json\nraise SystemExit(1)\n")
                self.assertEqual(tool_forge.install(md, broken, tools)[1][:8], "selftest")
                self.assertFalse(os.path.exists(os.path.join(tools, "auto_broken_one")))
                self.assertTrue(os.listdir(os.path.join(md, "forge_rejected")))
        finally:
            shutil.rmtree(tmp)


class MetaLimitTest(unittest.TestCase):
    def test_a_change_past_the_limits_is_a_problem(self):
        old = {"restock": {"keep_cash": 3000000}}
        with mock.patch.object(meta_harness, "pricing_at_head", lambda: old), \
                mock.patch.object(meta_harness, "pricing_now", lambda: {"restock": {"keep_cash": 1000000}}), \
                mock.patch.object(meta_harness, "git", lambda *a, **k: ""):
            self.assertTrue(meta_harness.limit_problems(["pricing.json"]))
        with mock.patch.object(meta_harness, "pricing_at_head", lambda: old), \
                mock.patch.object(meta_harness, "pricing_now", lambda: {"restock": {"keep_cash": 2500000}}), \
                mock.patch.object(meta_harness, "git", lambda *a, **k: ""):
            self.assertEqual(meta_harness.limit_problems(["pricing.json"]), [])
        self.assertTrue(hard_limits.guard_problems(["hard_limits.py"], ""))


if __name__ == "__main__":
    unittest.main()
