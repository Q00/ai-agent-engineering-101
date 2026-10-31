"""Round 3's own memory, decisions linked to their results, the measurements, and tool answers that stay valid JSON.

    python -m unittest test_context -v        (from submissions/26622007/week-08)
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import agent_tools           # noqa: E402
import decision_memory as dm # noqa: E402
import harness               # noqa: E402
import market_buyer as mb    # noqa: E402
import market_model as mm    # noqa: E402
import restock               # noqa: E402
import scorecard             # noqa: E402
import shop_memory           # noqa: E402

NOW = 1800000000


class Round3MemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.path.join(self.tmp.name, "r2.json")
        self.new = os.path.join(self.tmp.name, "r3.json")
        with open(self.old, "w") as f:
            json.dump({"trades": [{"shop": "cheat", "item": "lamp", "result": "deal", "problems": ["agreed 100, receipt says 120"]},
                                  {"shop": "fair", "item": "lamp", "result": "deal", "problems": []}]}, f)

    def start(self):
        with mock.patch.object(mb, "MEMORY_R3", self.new), mock.patch.object(mb, "MEMORY_R2", self.old), \
                mock.patch.object(mb, "MEMORY", self.old), mock.patch.object(mb.carried_bans, "__defaults__", (self.old,)):
            mb.start_round3_memory()
            return mb.load_memory()

    def test_starts_empty_except_for_shops_caught_cheating(self):
        m = self.start()
        self.assertEqual(m["trades"], [])
        self.assertEqual(m["carried_bans"], {"cheat": "agreed 100, receipt says 120"})

    def test_it_is_not_reset_later(self):
        self.start()
        with open(self.new) as f:
            m = json.load(f)
        m["trades"].append({"shop": "x", "item": "G1", "result": "deal"})
        with open(self.new, "w") as f:
            json.dump(m, f)
        self.assertEqual(len(self.start()["trades"]), 1)

    def test_a_carried_ban_keeps_the_shop_out_and_old_trades_do_not_count_as_tried(self):
        shops = [{"name": "cheat", "url": "https://c", "reviews": []}, {"name": "fair", "url": "https://f", "reviews": []}]
        with mock.patch.object(mb, "log"), mock.patch.object(mb, "AGENT_DIR", None):
            shop, why = mb.choose(shops, {"trades": [], "carried_bans": {"cheat": "p"}}, "김치", 100)
        self.assertEqual(shop["name"], "fair")
        self.assertIn("explore", why)

    def test_the_seller_writes_to_the_round_3_file_only_from_round_3(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(shop_memory, "NAME", "nocha12"), \
                mock.patch.object(shop_memory, "MEMORY", os.path.join(d, "old.json")), \
                mock.patch.object(shop_memory, "MEMORY_R3", os.path.join(d, "new.json")):
            with mock.patch.object(shop_memory, "load_inventory", lambda: {"round": 2}):
                shop_memory.record({"result": "deal", "item": "lamp"})
                self.assertTrue(os.path.exists(os.path.join(d, "old.json")))
                self.assertFalse(os.path.exists(os.path.join(d, "new.json")))
            with mock.patch.object(shop_memory, "load_inventory", lambda: {"round": 3}):
                self.assertEqual(shop_memory.load()["negotiations"], [])
                shop_memory.record({"result": "deal", "item": "김치"})
                self.assertEqual([n["item"] for n in shop_memory.load()["negotiations"]], ["김치"])


class OutcomesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name

    def write(self, name, *rows):
        with open(os.path.join(self.md, name), "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def sale(self, seq, sku, age, shop="me", amount=800):
        return {"k": "settle", "seq": seq, "ts": NOW - age * 3600, "buyer": "b", "order": f"o{seq}", "sku": sku, "amount": amount, "shop": shop}

    def run_it(self, units=(), **kw):
        return dm.outcomes(self.md, {"units": list(units)}, now=NOW, me_shops={"me"}, costs=kw.pop("costs", {}), **kw)

    def test_links_a_decision_to_what_sold_after_it(self):
        self.write("decisions.jsonl", {"ts": NOW - 10 * 3600, "agent": "restock", "final": {"why": "it sells", "buy": [{"sku": "A", "qty": 3}, {"sku": "B", "qty": 2}]}})
        self.write(mm.ROWS, self.sale(1, "A", 12), self.sale(2, "A", 5), self.sale(3, "A", 4), self.sale(4, "A", 3, shop="rival"))
        out = self.run_it(units=[{"sku": "B"}, {"sku": "B"}, {"sku": "A"}], costs={"A": 500})
        self.assertEqual(out["decisions"][0]["why"], "it sells")
        self.assertEqual(out["decisions"][0]["lines"][0], {"sku": "A", "asked": 3, "sold": 2, "revenue": 1600})
        best = out["best_skus"][0]
        self.assertEqual((best["sku"], best["sold"], best["left"], best["margin"]), ("A", 2, 1, 600))
        self.assertEqual(out["never_sold"][0]["sku"], "B")

    def test_what_is_neither_sold_nor_left_is_gone(self):
        self.write("decisions.jsonl", {"ts": NOW - 50 * 3600, "agent": "restock", "final": {"why": "", "buy": [{"sku": "F", "qty": 4}]}})
        self.write(mm.ROWS, self.sale(1, "F", 40))
        out = self.run_it(units=[{"sku": "F"}])
        self.assertEqual(out["totals"]["gone_unsold"], 2)

    def test_a_fresh_decision_shows_no_verdict_yet_and_other_agents_are_ignored(self):
        self.write("decisions.jsonl", {"ts": NOW - 60, "agent": "restock", "final": {"why": "", "buy": [{"sku": "A", "qty": 1}]}},
                   {"ts": NOW - 9000, "agent": "forum", "final": {"post": "x"}})
        out = self.run_it()
        self.assertEqual(out["decisions"], [])
        self.assertEqual(out["never_sold"], [])
        self.assertEqual(out["totals"]["decisions"], 1)

    def test_sales_are_split_between_decisions_on_the_same_sku(self):
        self.write("decisions.jsonl", {"ts": NOW - 20 * 3600, "agent": "restock", "final": {"why": "1", "buy": [{"sku": "A", "qty": 1}]}},
                   {"ts": NOW - 10 * 3600, "agent": "restock", "final": {"why": "2", "buy": [{"sku": "A", "qty": 1}]}})
        self.write(mm.ROWS, self.sale(1, "A", 15), self.sale(2, "A", 5), self.sale(3, "A", 4))
        got = [d["lines"][0]["sold"] for d in self.run_it()["decisions"]]
        self.assertEqual(got, [1, 2])

    def test_empty_state_is_fine_and_the_agent_gets_it_as_citable_context(self):
        self.assertEqual(self.run_it()["totals"]["decisions"], 0)
        seen = {}

        def decide(goal, schema, md, agent, given):
            seen.update(given)
            return {"buy": [], "why": ""}
        cfg = {**restock.DEFAULTS, "round_ends_at": None}
        restock.agent_turn(self.md, {"balance": 5000000, "units": []}, {}, [], cfg, None, decide)
        self.assertIn("c1", seen)
        self.assertIn("totals", json.loads(seen["c1"]))

    def test_the_tool_answers(self):
        out = json.loads(agent_tools.run("my_decisions", {}, self.md, folder=os.path.join(HERE, "tools")))
        self.assertIn("totals", out)


class CatalogSearchTest(unittest.TestCase):
    def test_finds_a_sku_by_its_code(self):      # the first real run dropped a good proposal because this found nothing
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "catalog.json"), "w", encoding="utf-8") as f:
                json.dump({"Gea0b0c3f": {"name": "김치", "category": "식품", "list_price": 1, "kind": "food"}}, f, ensure_ascii=False)
            out = json.loads(agent_tools.run("catalog_search", {"query": "Gea0b0c3f"}, d, folder=os.path.join(HERE, "tools")))
        self.assertEqual(out[0]["sku"], "Gea0b0c3f")


class ShrinkTest(unittest.TestCase):
    def test_small_answers_are_untouched(self):
        self.assertEqual(json.loads(agent_tools.shrink({"a": 1})), {"a": 1})

    def test_long_answers_stay_valid_json_within_the_limit_and_say_so(self):
        for big in ([{"sku": f"S{i}", "name": "긴 이름" * 5} for i in range(2000)],
                    {"skus": [{"x": "y" * 50} for _ in range(1000)], "n": 3},
                    {"by": {f"k{i}": {"sold": i} for i in range(3000)}}):
            out = agent_tools.shrink(big, 1000)
            self.assertLessEqual(len(out), 1000)
            self.assertGreater(json.loads(out)["_cut"], 0)

    def test_one_huge_string_still_gives_valid_json(self):
        out = agent_tools.shrink({"text": "z" * 5000}, 500)
        self.assertLessEqual(len(out), 500)
        self.assertEqual(json.loads(out)["_cut"], "all")


class MeasureTest(unittest.TestCase):
    def test_each_request_is_recorded_with_its_size_and_usage(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["a"], "properties": {"a": {"type": "integer"}}}
        answers = [httpx.Response(500, text="no"), httpx.Response(200, json={"choices": [{"message": {"content": '{"a": 1}'}}],
                                                                             "usage": {"prompt_tokens": 7, "completion_tokens": 2, "cost": 0.01}})]
        real = httpx.Client
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(harness.httpx, "Client", lambda **k: real(transport=httpx.MockTransport(lambda r: answers.pop(0)), **k)), \
                mock.patch.object(harness.time, "sleep", lambda s: None), \
                mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "x", "MARKET_DIR": d}):
            self.assertEqual(harness._ask_openrouter("hello", schema, "sys", "t"), {"a": 1})
            table = scorecard.llm_table(d)
        self.assertEqual((table["t"]["calls"], table["t"]["failed"], table["t"]["prompt_tokens"], table["t"]["mean_chars"]), (2, 1, 7, 8))
        self.assertAlmostEqual(table["t"]["cost"], 0.01)

    def test_no_market_folder_means_no_record_and_no_error(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("MARKET_DIR", None)
            harness.record_call("t", "p", "s", 1, True)
            mb.measure("x", time.time(), "nope")

    def test_memory_timing_reaches_the_scorecard(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"MARKET_DIR": d}), \
                mock.patch.object(mb, "MEMORY", os.path.join(d, "m.json")):
            mb.save_memory({"trades": []})
            mb.load_memory()
            t = scorecard.memory_timing(d)
        self.assertEqual(set(t), {"buyer_memory_save", "buyer_memory_load"})
        self.assertGreater(t["buyer_memory_load"]["bytes"], 0)


if __name__ == "__main__":
    unittest.main()
