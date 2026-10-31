"""Linking what broke to who sold it, the buyer's use of it, the shop-choosing agent, and the seller's briefing.

    python -m unittest test_memory_links -v        (from submissions/26622007/week-08)
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import agent_tools           # noqa: E402
import market_buyer as mb    # noqa: E402
import market_model as mm    # noqa: E402
import restock               # noqa: E402
import seller                # noqa: E402

T = 1800000000
CATALOG = {"H1": {"kind": "goods", "category": "가전>컵", "list_price": 10000, "name": "컵"},
           "H2": {"kind": "goods", "category": "가전>컵", "list_price": 20000, "name": "큰 컵"},
           "F1": {"kind": "food", "category": "식품>김치", "list_price": 20000, "shelf_hours": 720, "name": "김치"}}


def settle(seq, sku="H1", buyer="b", ts=T, shop="https://a", grade="A"):
    return {"k": "settle", "seq": seq, "ts": ts, "buyer": buyer, "order": f"o{seq}", "sku": sku, "amount": 8000, "grade": grade, "shop": shop}


def broken(seq, sku="H1", buyer="b", ts=T + 3600):
    return {"k": "broken", "seq": seq, "ts": ts, "buyer": buyer, "sku": sku}


class LinkTest(unittest.TestCase):
    def test_links_to_the_latest_purchase_within_the_window(self):
        rows = [settle(1, ts=T - 3600), settle(2, ts=T), broken(3)]
        self.assertEqual([s["seq"] for _, s in mm.link_broken(rows)], [2])

    def test_each_purchase_breaks_once_and_two_breaks_take_two(self):
        rows = [settle(1, ts=T - 3600), settle(2, ts=T), broken(3), broken(4, ts=T + 3700)]
        self.assertEqual(sorted(s["seq"] for _, s in mm.link_broken(rows)), [1, 2])

    def test_a_break_outside_the_window_or_from_another_buyer_links_to_nothing(self):
        self.assertEqual(mm.link_broken([settle(1), broken(2, ts=T + 8 * 3600)]), [])
        self.assertEqual(mm.link_broken([settle(1), broken(2, buyer="other")]), [])
        self.assertEqual(mm.link_broken([settle(1, ts=T + 7200), broken(2)]), [])

    def test_stats_count_goods_only_by_sku_grade_and_shop(self):
        rows = [settle(1), settle(2, ts=T + 1, buyer="c", grade="C", shop="https://b"), broken(3, buyer="c"), settle(4, sku="F1")]
        s = mm.break_stats(rows, CATALOG)
        self.assertEqual((s["sold"], s["broke"], s["rate"]), (2, 1, 0.5))
        self.assertEqual(s["by_grade"]["C"], {"sold": 1, "broke": 1})
        self.assertEqual(s["by_shop"]["https://a"], {"sold": 1, "broke": 0})

    def test_no_estimate_until_there_is_data_then_it_leans_on_the_overall_rate(self):
        self.assertIsNone(mm.break_chance({"sold": 10, "broke": 1, "rate": 0.1, "by_sku": {}}, "H1"))
        s = {"sold": 100, "broke": 10, "rate": 0.1, "by_sku": {"H1": {"sold": 20, "broke": 10}}}
        self.assertAlmostEqual(mm.break_chance(s, "H9"), 0.1)
        self.assertAlmostEqual(mm.break_chance(s, "H1"), (10 + 2) / 40)

    def test_settle_keeps_the_warranty(self):
        e = {"kind": "settle", "seq": 1, "ts": T, "round": 3, "data": {"item": "H1", "warranty_hours": 6}}
        self.assertEqual(mm.compact(e)["warranty"], 6)


class BuyerMemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name
        self.settled = os.path.join(self.md, "settled.jsonl")

    def write(self, name, *rows):
        with open(os.path.join(self.md, name), "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def memory(self):
        return {"trades": [{"order": "o1", "shop": "a", "item": "H1", "result": "deal", "price": 8000, "budget": 9000},
                           {"order": "o2", "shop": "b", "item": "H1", "result": "deal", "price": 8000, "budget": 9000}]}

    def test_marks_the_right_shop_with_warranty_cover(self):
        self.write("settled.jsonl", {"order": "o1", "time": T, "name": "컵", "warranty_hours": 6},
                   {"order": "o2", "time": T + 100, "name": "컵", "warranty_hours": 0})
        self.write("broken.jsonl", {"name": "컵", "broke_at": T + 3600})
        m = self.memory()
        with mock.patch.object(mb, "log"):
            mb.sync_broken(m, self.md, self.settled)
        self.assertEqual((m["trades"][1].get("broke"), m["trades"][1].get("covered")), (True, False))
        self.assertNotIn("broke", m["trades"][0])

    def test_a_second_run_does_not_count_it_again_and_a_later_break_takes_the_other(self):
        self.write("settled.jsonl", {"order": "o1", "time": T, "name": "컵", "warranty_hours": 6}, {"order": "o2", "time": T + 100, "name": "컵"})
        self.write("broken.jsonl", {"name": "컵", "broke_at": T + 3600})
        m = self.memory()
        with mock.patch.object(mb, "log"):
            mb.sync_broken(m, self.md, self.settled)
            mb.sync_broken(m, self.md, self.settled)
            self.assertNotIn("broke", m["trades"][0])
            self.write("broken.jsonl", {"name": "컵", "broke_at": T + 3700})
            mb.sync_broken(m, self.md, self.settled)
        self.assertEqual(m["trades"][0]["covered"], True)

    def test_what_broke_shows_in_the_shop_history(self):
        line = mb.history_line([{"shop": "a", "item": "H1", "result": "deal", "price": 8000, "budget": 9000, "broke": True, "covered": False},
                                {"shop": "a", "item": "H1", "result": "unpaid", "unpaid_reason": "no certificate", "budget": 9000}])
        self.assertIn("1 of your 1 purchases broke", line)
        self.assertIn("no certificate", line)

    def test_the_limit_counts_the_chance_of_breaking(self):
        offer = {"list_price": 10000}
        self.assertEqual(mb.value_limit(offer, 20000, None), 10000)
        self.assertEqual(mb.value_limit(offer, 20000, 0.2), 8000)
        self.assertEqual(mb.value_limit(offer, 5000, 0.2), 5000)

    def test_tools_answer(self):
        self.write(mm.ROWS, settle(1), broken(2))
        with open(os.path.join(self.md, "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(CATALOG, f, ensure_ascii=False)
        out = json.loads(agent_tools.run("break_rates", {"sku": "H1"}, self.md, folder=os.path.join(HERE, "tools")))
        self.assertEqual(out["goods_broke"], 1)
        mem = os.path.join(self.md, "mem.json")
        with open(mem, "w") as f:
            json.dump(self.memory(), f)
        with mock.patch.dict(os.environ, {"BUYER_MEMORY": mem}):
            out = json.loads(agent_tools.run("my_purchases", {}, self.md, folder=os.path.join(HERE, "tools")))
        self.assertEqual(out["a"]["deals"], 1)


class BreakLineTest(unittest.TestCase):
    def test_says_the_record_or_nothing(self):
        self.assertIn("3 of 10", mb.break_line({"sold": 10, "broke": 3}))
        self.assertEqual(mb.break_line(None), "")
        self.assertEqual(mb.break_line({"sold": 0, "broke": 1}), "")


class ChooseAgentTest(unittest.TestCase):
    SHOPS = [{"name": "a", "url": "https://a", "reviews": []}, {"name": "b", "url": "https://b", "reviews": []}]

    def run_choose(self, decide, md="x"):
        with mock.patch.object(mb, "AGENT_DIR", md), mock.patch.object(mb.agent_loop, "decide", decide), \
                mock.patch.object(mb.market_model, "load_rows", lambda d: []), \
                mock.patch.object(mb, "log"), mock.patch.object(mb, "ask", lambda *a, **k: {"shop": "a", "reason": "fallback"}):
            return mb.choose(self.SHOPS, {"trades": []}, "김치", 9000)

    def test_the_agent_chooses_with_the_candidates_as_citable_evidence(self):
        seen = {}

        def decide(goal, schema, md, agent, given, max_steps):
            seen.update(given=given, steps=max_steps, enum=schema["properties"]["shop"]["enum"])
            return {"shop": "b", "reason": "ledger says so"}
        shop, why = self.run_choose(decide)
        self.assertEqual(shop["name"], "b")
        self.assertIn("agent", why)
        self.assertIn("shop 'a'", seen["given"]["c0"])
        self.assertIn("ledger name: https://a", seen["given"]["c0"])      # first real run searched the ledger by name and found nothing
        self.assertEqual((seen["steps"], sorted(seen["enum"])), (mb.CHOOSE_STEPS, ["a", "b"]))

    def test_no_evidenced_decision_or_a_harness_failure_falls_back(self):
        self.assertEqual(self.run_choose(lambda *a, **k: None)[1].split(": ")[1], "fallback")

        def boom(*a, **k):
            raise mb.HarnessError("429")
        self.assertEqual(self.run_choose(boom)[0]["name"], "a")

    def test_round_2_never_uses_it(self):
        self.assertEqual(self.run_choose(lambda *a, **k: self.fail("agent in round 2"), md=None)[1].split(": ")[1], "fallback")


class BriefingTest(unittest.TestCase):
    def test_the_seller_reads_what_the_restock_tick_wrote(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"MARKET_DIR": d}):
            self.assertEqual(seller.briefing_line("H1"), "")
            with open(os.path.join(d, "briefing.json"), "w") as f:
                json.dump({"skus": {"H1": {"price_ratio": 0.82, "rate": 1.25, "sellers": 3}}}, f)
            line = seller.briefing_line("H1")
            self.assertIn("82%", line)
            self.assertIn("3 other shops", line)
            self.assertEqual(seller.briefing_line("H2"), "")

    def test_it_reaches_the_turn_prompt_only_from_round_3(self):
        deal = {"id": "H1", "name": "컵", "reserve": 5, "ask": 9, "rounds": 1, "history": []}
        seen = []
        with mock.patch.object(seller, "briefing_line", lambda s: "BRIEFING\n"), mock.patch.object(seller, "memory_line", lambda n: ""), \
                mock.patch.object(seller, "ask", lambda p, *a, **k: seen.append(p)):
            seller.decide_turn(deal, "8")
            seller.decide_turn(dict(deal, unit="u"), "8")
        self.assertNotIn("BRIEFING", seen[0])
        self.assertIn("BRIEFING", seen[1])

    def test_restock_writes_the_whole_briefing(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, mm.ROWS), "w") as f:
                f.write(json.dumps(settle(1)) + "\n")
            with mock.patch.object(mm, "sync", lambda *a, **k: 0), mock.patch("builtins.print"):
                restock.read_model(d, CATALOG, {**mm.DEFAULTS, **restock.DEFAULTS})
            with open(os.path.join(d, "briefing.json")) as f:
                self.assertIn("H1", json.load(f)["skus"])


if __name__ == "__main__":
    unittest.main()
