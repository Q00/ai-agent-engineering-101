"""Linking what broke to who sold it, the buyer's use of it, the shop-choosing agent, and the seller's briefing.

    python -m unittest test_memory_links -v        (from submissions/26622007/week-08)
"""
import json
import os
import sys
import tempfile
import threading
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


def broken(seq, sku="H1", buyer="b", ts=T + 3600, order=None):
    r = {"k": "broken", "seq": seq, "ts": ts, "buyer": buyer, "sku": sku}
    if order:
        r["order"] = order
    return r


class LinkTest(unittest.TestCase):
    def test_links_to_the_latest_purchase_within_the_window(self):
        rows = [settle(1, ts=T - 3600), settle(2, ts=T), broken(3)]
        self.assertEqual([s["seq"] for _, s in mm.link_broken(rows)], [2])

    def test_a_break_naming_its_order_links_to_that_settle_not_the_latest(self):
        rows = [settle(1, ts=T - 3600, grade="C"), settle(2, ts=T), broken(3, order="o1")]
        self.assertEqual([s["seq"] for _, s in mm.link_broken(rows)], [1])
        rows = [settle(1, ts=T - 3600), settle(2, ts=T), broken(3, order="o1"), broken(4)]   # one by order, one by window
        self.assertEqual(sorted(s["seq"] for _, s in mm.link_broken(rows)), [1, 2])
        self.assertEqual(mm.link_broken([settle(1), broken(2, sku="H2", order="o1")]), [])   # order and SKU must agree

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
        self.assertEqual(s["by_sku_grade"]["H1|C"], {"sold": 1, "broke": 1})

    def test_stats_leave_out_recent_sales_that_have_not_broken_yet(self):
        # GPT Pro 11/06 23:40 (메인 23:40): a sale of the last hours that has not broken may still break; counting it
        # lowers the rate. One that already broke counts (independent review: else a bad recent batch lowers the rate)
        rows = [settle(1), broken(2), settle(3, ts=T + 7 * 3600, buyer="c"), settle(4, ts=T + 8 * 3600, buyer="d"),
                broken(5, ts=T + 8 * 3600 + 60, buyer="d"), settle(6, sku="F1", ts=T + 12 * 3600)]
        s = mm.break_stats(rows, CATALOG, mature_only=True)   # newest T+12h: 1 old, 4 recent but broken, 3 left out
        self.assertEqual((s["sold"], s["broke"], s["rate"]), (2, 2, 1.0))
        self.assertEqual(s["by_sku_grade"]["H1|A"], {"sold": 2, "broke": 2})
        self.assertEqual(mm.break_stats(rows, CATALOG)["sold"], 3)              # the buyer's default: every sale
        old = [settle(i, buyer=f"o{i}") for i in range(60)] + [broken(100 + i, buyer=f"o{i}") for i in range(6)]
        new = [settle(200 + i, ts=T + 6 * 3600, buyer=f"n{i}") for i in range(40)] + \
            [broken(300 + i, ts=T + 6 * 3600 + 60, buyer=f"n{i}") for i in range(20)]
        rows = old + new
        self.assertGreaterEqual(mm.break_stats(rows, CATALOG, mature_only=True)["rate"], mm.break_stats(rows, CATALOG)["rate"])

    def test_no_estimate_until_there_is_data_then_it_leans_on_the_overall_rate(self):
        self.assertIsNone(mm.break_chance({"sold": 10, "broke": 1, "rate": 0.1, "by_sku": {}}, "H1"))
        s = {"sold": 100, "broke": 10, "rate": 0.1, "by_sku": {"H1": {"sold": 20, "broke": 10}}}
        self.assertAlmostEqual(mm.break_chance(s, "H9"), 0.1)
        self.assertAlmostEqual(mm.break_chance(s, "H1"), (10 + 2) / 40)
        g = {"sold": 1000, "broke": 100, "rate": 0.1, "by_sku": {"H1": {"sold": 20, "broke": 10}},
             "by_grade": {"A": {"sold": 800, "broke": 24}, "C": {"sold": 200, "broke": 70}, "B": {"sold": 10, "broke": 5}},
             "by_sku_grade": {"H1|C": {"sold": 20, "broke": 10}}}
        self.assertAlmostEqual(mm.unit_break_chance(g, "H1", "C"), (10 + 20 * 0.35) / 40)   # SKU x grade, leaning on C
        self.assertAlmostEqual(mm.unit_break_chance(g, "H2", "A"), 0.03)                     # no SKU x grade sales: the grade
        self.assertAlmostEqual(mm.unit_break_chance(g, "H1", "B"), mm.break_chance(g, "H1"))  # too few B sales: the SKU
        self.assertAlmostEqual(mm.unit_break_chance(g, "H1", None), mm.break_chance(g, "H1"))
        self.assertIsNone(mm.unit_break_chance({"sold": 10, "broke": 1, "rate": 0.1, "by_sku": {}}, "H1", "A"))

    def test_settle_keeps_the_warranty(self):
        e = {"kind": "settle", "seq": 1, "ts": T, "round": 3, "data": {"item": "H1", "warranty_hours": 6}}
        self.assertEqual(mm.compact(e)["warranty"], 6)

    def test_reviews_go_to_their_own_file_and_the_rows_stay_as_they_were(self):
        # "Stars take one mean per reviewing account" (rules, Scoring): the shop's stars part needs each review's stars
        # by account, which the rows never kept (AX 11/10 지시 15). They go to ledger_reviews.jsonl: the rows file is
        # read by byte-sized tails (buyer_rank_pg 30 MB, restock 15-60 MB), so its rows keep their size (review 425e7dc I1).
        res = {"kind": "result", "seq": 2, "ts": T, "round": 3,
               "data": {"buyer": "b1", "order": "o-1", "shop": "https://s", "receipt": {"shop": "nocha12"},
                        "stars": 4, "verified": True, "review": "free text, not kept"}}
        old = {"kind": "result", "seq": 3, "ts": T, "round": 2, "data": {"buyer": "b0", "shop": "https://s", "stars": 1}}
        bare = {"kind": "result", "seq": 4, "ts": T, "round": 3, "data": {"buyer": "b2", "shop": "https://s"}}
        settle = {"kind": "settle", "seq": 5, "ts": T, "round": 3, "data": {"item": "H1", "amount": 9}}
        self.assertEqual(mm.compact(res), {"k": "result", "seq": 2, "ts": T, "buyer": "b1", "order": "o-1",
                                           "shop": "https://s", "seller": "nocha12"})
        pages = [[res, old, bare, settle], []]
        with tempfile.TemporaryDirectory() as md:
            with open(os.path.join(md, mm.CURSOR), "w") as f:
                json.dump({"after": 1, "fields": {}}, f)
            self.assertEqual(mm.sync(md, get=lambda after: pages.pop(0) if pages else []), 3)
            with open(os.path.join(md, mm.ROWS), encoding="utf-8") as f:
                self.assertNotIn("stars", f.read())
            with open(os.path.join(md, mm.REVIEWS), "a", encoding="utf-8") as f:   # a run repeated after a crash
                f.write(json.dumps({"seq": 2, "stars": 4}) + "\n")
            self.assertEqual(mm.load_reviews(md), [{"seq": 2, "ts": T, "buyer": "b1", "order": "o-1", "shop": "https://s",
                                                    "seller": "nocha12", "stars": 4, "verified": True}])
        with tempfile.TemporaryDirectory() as md:
            self.assertEqual(mm.load_reviews(md), [])                         # before the first sync: none, no error


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

    def test_the_limit_keeps_the_list_price_when_a_break_is_refunded(self):
        offer = {"list_price": 10000}
        self.assertEqual(mb.value_limit(offer, 20000, None), 10000)
        self.assertEqual(mb.value_limit(offer, 20000, 0.2), 10000)      # 6 h warranty: a break refunds the ask
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

        def decide(goal, schema, md, agent, given, max_steps, ask_fn=None):
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

    def test_each_call_has_the_choice_deadline_and_none_starts_past_it(self):
        limits = []

        def ask(*a, timeout=None, **k):
            limits.append(timeout)
            return {"shop": "a", "reason": "fallback"}

        def decide(goal, schema, md, agent, given, max_steps, ask_fn=None):
            ask_fn("p", schema, "s", tag="buyer-agent")
            return None
        with mock.patch.object(mb, "AGENT_DIR", "x"), mock.patch.object(mb.agent_loop, "decide", decide), \
                mock.patch.object(mb.market_model, "load_rows", lambda d: []), mock.patch.object(mb, "log"), \
                mock.patch.object(mb, "ask", ask):
            mb.choose(self.SHOPS, {"trades": []}, "김치", 9000)
            self.assertEqual(len(limits), 2)       # the agent's step and the single question
            self.assertTrue(all(0 < t <= mb.CHOOSE_STEP_SECONDS for t in limits))
            # 17:25-18:10 on 11/06 codex took 17-58 s: once the choice's time is gone, code chooses (main()'s except)
            with mock.patch.object(mb, "CHOOSE_SECONDS", 1), self.assertRaises(mb.HarnessError):
                mb.choose(self.SHOPS, {"trades": []}, "김치", 9000)
            self.assertEqual(len(limits), 2)

    def test_the_second_step_is_skipped_once_the_quotes_are_in(self):
        # 11/06 23:30: quotes were in at once and the 2-step choice took 10 s of a 42 s order; best_quote picks anyway
        calls = []

        def ask(*a, timeout=None, **k):
            calls.append(k.get("tag"))
            return {"shop": "a", "reason": "fallback"}

        def decide(goal, schema, md, agent, given, max_steps, ask_fn=None):
            ask_fn("p", schema, "s", tag="buyer-agent")
            ask_fn("p", schema, "s", tag="buyer-agent")
            return None
        with mock.patch.object(mb, "AGENT_DIR", "x"), mock.patch.object(mb.agent_loop, "decide", decide), \
                mock.patch.object(mb.market_model, "load_rows", lambda d: []), mock.patch.object(mb, "log"), \
                mock.patch.object(mb, "ask", ask):
            with self.assertRaises(mb.HarnessError):
                mb.choose(self.SHOPS, {"trades": []}, "김치", 9000, hurry=lambda: True)
            self.assertEqual(calls, ["buyer-agent"])          # the first step only: the tools still get their turn
            calls.clear()
            mb.choose(self.SHOPS, {"trades": []}, "김치", 9000, hurry=lambda: False)
            self.assertEqual(len(calls), 3)

    def test_a_stopped_choice_asks_no_further_step(self):
        # 11/07 04:27-04:50: 37 of 61 runs had 1-2 agent calls after main had moved on; the abandoned thread kept stepping
        calls = []

        def ask(*a, timeout=None, **k):
            calls.append(k.get("tag"))
            return {"shop": "a", "reason": "fallback"}

        def decide(goal, schema, md, agent, given, max_steps, ask_fn=None):
            ask_fn("p", schema, "s", tag="buyer-agent")
            return None
        stop = threading.Event()
        stop.set()
        with mock.patch.object(mb, "AGENT_DIR", "x"), mock.patch.object(mb.agent_loop, "decide", decide), \
                mock.patch.object(mb.market_model, "load_rows", lambda d: []), mock.patch.object(mb, "log"), \
                mock.patch.object(mb, "ask", ask):
            with self.assertRaises(mb.HarnessError):
                mb.choose(self.SHOPS, {"trades": []}, "김치", 9000, stop=stop)
        self.assertEqual(calls, [])

    def test_a_choice_stopped_after_its_first_step_asks_no_second(self):
        stop, calls = threading.Event(), []

        def ask(*a, timeout=None, **k):
            calls.append(k.get("tag"))
            stop.set()                     # main moves on while this step is out
            return {"shop": "a", "reason": "fallback"}

        def decide(goal, schema, md, agent, given, max_steps, ask_fn=None):
            ask_fn("p", schema, "s", tag="buyer-agent")
            ask_fn("p", schema, "s", tag="buyer-agent")
            return None
        with mock.patch.object(mb, "AGENT_DIR", "x"), mock.patch.object(mb.agent_loop, "decide", decide), \
                mock.patch.object(mb.market_model, "load_rows", lambda d: []), mock.patch.object(mb, "log"), \
                mock.patch.object(mb, "ask", ask):
            with self.assertRaises(mb.HarnessError):
                mb.choose(self.SHOPS, {"trades": []}, "김치", 9000, stop=stop)
        self.assertEqual(calls, ["buyer-agent"])

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

    def test_a_turn_has_its_own_limit_and_a_failed_turn_asks_for_a_number(self):
        deal = {"id": "H1", "name": "컵", "reserve": 5, "ask": 9, "rounds": 1, "history": []}
        limits = []
        with mock.patch.object(seller, "memory_line", lambda n: ""), \
                mock.patch.object(seller, "ask", lambda p, *a, timeout=None, **k: limits.append(timeout)):
            seller.decide_turn(deal, "8")
        self.assertEqual(limits, [seller.TURN_SECONDS])
        self.assertLess(seller.TURN_SECONDS, 60)
        self.assertIn("My price: 9", seller.fallback_reply(deal))       # the talk stays open
        self.assertIsNone(seller.fallback_reply(dict(deal, rounds=seller.MAX_ROUNDS)))

    def test_first_message_calls_have_a_limit_and_fall_back_to_rules(self):
        cat = mock.Mock()
        cat.exact.return_value = None
        cat.categories = ["seating"]
        sofa = {"id": "S1", "name": "leather sofa", "category": "seating", "description": "three seats", "reserve": 50}
        cat.search.return_value = [sofa]
        limits = []

        def failing(p, *a, timeout=None, **k):
            limits.append(timeout)
            raise seller.HarnessError("timed out")
        shop = seller.Seller.__new__(seller.Seller)
        shop.catalog = cat
        with mock.patch.object(seller, "ask", failing):
            item, d = shop.choose("I want to buy a sofa")
            self.assertEqual((item["id"], d["ask"]), ("S1", 50))         # keyword search, rule pick at the reserve
            self.assertEqual(shop.choose("I want to buy a lamp"), (None, None))   # no shared word: no guess
            cat.exact.return_value = sofa
            self.assertEqual(shop.choose("leather sofa")[1]["ask"], 50)         # exact name, rule opening
        self.assertTrue(limits and all(t == seller.FIRST_SECONDS for t in limits))
        self.assertLess(seller.FIRST_SECONDS, 60)

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
