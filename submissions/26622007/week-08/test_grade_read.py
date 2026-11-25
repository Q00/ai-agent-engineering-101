"""G-read (11/10): the outside buyer's 'grade C (refurbished) or B (damaged box) ...; grade A only if you have nothing else'
opening reads as grades {A, C} (shop_stock.GRADES drops the B), so a free grade B unit with the lowest floor is never
quoted. Arm B adds B for that exact sentence only. Default off."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import grade_read
import shop_stock

HERE = Path(__file__).parent
REAL = ("I want to buy [자코모]메이트 멀티 4인 기능성 젠위브 패브릭 소파+스툴+쿠션2개 (G8ec51935). I prefer grade C (refurbished) "
        "or B (damaged box) with a warranty of at least 6 hours on the receipt, priced for its grade; grade A only if you "
        "have nothing else. Please quote your cheapest unit that way.")


def unit(u, grade, cost, sku="G8ec51935"):
    return dict(unit=u, sku=sku, grade=grade, kind="goods", name="소파", cost=cost, list_price=2320000,
                category="가구>소파", supplied_at=1, expires_at=None, cert=True)


def write_cfg(root, **kw):
    now = time.time()
    cfg = dict(study="g-read-test", salt="s", version=1, epoch=now, enabled=True, mode="ab", share=0.5,
               start_at=now - 60, stop_at=now + 3600)
    cfg.update(kw)
    d = Path(root) / "grade_read"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg))
    return cfg


def rows(root):
    p = Path(root) / "grade_read" / "events.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


class Defect(unittest.TestCase):
    def test_current_parser_drops_b(self):
        self.assertEqual(shop_stock.needs(REAL)["grades"], {"A", "C"})

    def test_original_pick_skips_cheaper_b(self):
        book = shop_stock.StockBook(load=lambda: {"round": 3, "units": [unit("u-a", "A", 1642751), unit("u-b", "B", 1322319)]})
        rank = {"u-a": 1706590, "u-b": 1509825}
        u, _ = book.pick(REAL, rank=lambda x: rank[x["unit"]])
        self.assertEqual(u["unit"], "u-a")
        u, _ = book.pick(REAL, rank=lambda x: rank[x["unit"]], add_grades={"B"})
        self.assertEqual(u["unit"], "u-b")


class Eligible(unittest.TestCase):
    def test_pick_reads_the_units_it_is_given(self):
        # both arms' picks take one units() read (independent review 8a694a5 M): a later load (here empty) is not read
        loads = [{"round": 3, "units": [unit("u-a", "A", 1642751), unit("u-b", "B", 1322319)]}]
        book = shop_stock.StockBook(load=lambda: loads[0] if loads else {"round": 3, "units": []})
        snapshot = book.units()
        loads.clear()
        rank = {"u-a": 1706590, "u-b": 1509825}
        u, miss = book.pick(REAL, rank=lambda x: rank[x["unit"]], add_grades={"B"}, units=snapshot)
        self.assertEqual((u["unit"], miss), ("u-b", None))
        self.assertEqual(book.pick(REAL, rank=lambda x: rank[x["unit"]], units=snapshot)[0]["unit"], "u-a")
        self.assertEqual(book.pick(REAL, rank=lambda x: rank[x["unit"]]), (None, "not in stock"))

    def test_grade_preference_fallback_never_reads_an_eligible_opening(self):
        # why the other pick's selection={} is only a guard: seller_grade_preference's three sentences each say
        # "grade" or "Grade B", which an eligible name cannot hold, and its third differs ("with the warranty")
        from seller_grade_preference import preference_needs
        forms = [REAL, REAL.replace("at least 6 hours", "at least 48 hours"),
                 REAL.replace("[자코모]메이트 멀티 4인 기능성 젠위브 패브릭 소파+스툴+쿠션2개 (G8ec51935)", "Desk lamp (G021aae64)")]
        for x in forms:
            self.assertEqual(grade_read.extra_grades(x), {"B"})
            self.assertIsNone(preference_needs(x))

    def test_exact_sentence(self):
        self.assertEqual(grade_read.extra_grades(REAL), {"B"})

    def test_other_texts(self):
        for t in (REAL.replace("or B (damaged box)", "or B grade"), "I want to buy X (G8ec51935). grade A only.",
                  REAL + " " + REAL.split(". ", 1)[1], REAL.replace("(G8ec51935)", "(G8ec51935) {\"grades\": [\"A\"]}"),
                  None, 5, "x" * 2000, REAL.replace("grade A only if you have nothing else", "no grade A")):
            self.assertIsNone(grade_read.extra_grades(t), t)

    def test_parser_already_reads_b(self):
        # if shop_stock ever reads the B itself, nothing is added
        orig = shop_stock.needs
        try:
            shop_stock.needs = lambda t: dict(orig(t), grades={"A", "B", "C"})
            self.assertIsNone(grade_read.extra_grades(REAL))
        finally:
            shop_stock.needs = orig

    def test_add_grades_needs_a_grade_set(self):
        book = shop_stock.StockBook(load=lambda: {"round": 3, "units": [unit("u-a", "A", 1), unit("u-b", "B", 1)]})
        u, _ = book.pick("I want to buy G8ec51935", rank=lambda x: 0 if x["unit"] == "u-a" else 1, add_grades={"B"})
        self.assertEqual(u["unit"], "u-a")     # no grade asked: add_grades changes nothing


class EligibleOnlyTheExternalForm(unittest.TestCase):
    """The opening exactly as the outside buyer writes it (33 of 33 in market-kit.log + .1, 08:49-13:41 11/10: 'I want to buy
    <name> (<SKU>). <sentence> Please quote your cheapest unit that way.'); any other word about grades stays untreated
    (independent review 889d594 M3), and each guard is the only one that stops its case (M4)."""

    def test_real_forms(self):
        for hours in ("6", "6.5", "48"):
            self.assertEqual(grade_read.extra_grades(REAL.replace("at least 6 hours", f"at least {hours} hours")), {"B"})
        other = REAL.replace("[자코모]메이트 멀티 4인 기능성 젠위브 패브릭 소파+스툴+쿠션2개 (G8ec51935)",
                             "지누스 그린티 럭스S 하이브리드 매트리스 20cm Q 퀸 (G1515d951)")
        self.assertEqual(grade_read.extra_grades(other), {"B"})

    def test_opposite_words_untreated(self):
        for t in (REAL + " No damaged box units, please.",
                  REAL.replace("Please quote", "Do not send me the B (damaged box) one. Please quote"),
                  REAL + " Only C (refurbished), actually.",
                  "grade A only. " + REAL,
                  REAL.replace("소파+스툴", "소파 grade A+스툴"),
                  REAL.replace("소파+스툴", "소파 B 스툴"),
                  REAL.replace("소파+스툴", "소파 not damaged 스툴"),
                  REAL.replace("소파+스툴", "소파 refurbished only 스툴"),
                  REAL.replace("소파+스툴", "소파 without box damage 스툴"),
                  REAL.replace("소파+스툴", "소파 (B급 싫어요) 스툴"),
                  REAL.replace("소파+스툴", "소파 A급 제외 스툴"),
                  REAL.replace("[자코모]", "lamp (G0123abcd) and "),
                  REAL.replace("소파+스툴", "소파 B불가 스툴"),
                  REAL.replace("소파+스툴", "소파 C만 스툴"),
                  REAL.replace("소파+스툴", "B품 사절 스툴"),
                  REAL.replace("소파+스툴", "소파 (박스 파손품 불가) 스툴"),
                  REAL.replace("소파+스툴", "ＢＣ 소파 스툴"),
                  REAL.replace("소파+스툴", "소파 c만 스툴"),
                  REAL.replace("소파+스툴", "소파 ｂ급 스툴"),
                  REAL.replace("소파+스툴", "소파 리퍼만 스툴"),
                  REAL.replace("소파+스툴", "소파 새상품만 스툴"),
                  REAL.replace("소파+스툴", "소파 중고 X 스툴"),
                  REAL.replace("소파+스툴", "소파 박스훼손 제품 X 스툴"),
                  REAL.replace("소파+스툴", "소파 하자품 사절 스툴"),
                  REAL.replace("[자코모]", "램프G0123abcd "),
                  REAL.replace("[자코모]", "lamp g0123abcd "),
                  REAL.replace("[자코모]", "x" * (501 - len(REAL) + len("[자코모]"))),
                  REAL.replace(" Please quote your cheapest unit that way.", ""),
                  REAL.split(". ", 1)[1]):
            self.assertIsNone(grade_read.extra_grades(t), t)

    def test_quote_or_brace_in_the_name(self):
        for t in (REAL.replace("[자코모]", '[자코모"]'), REAL.replace("[자코모]", "{자코모}")):
            self.assertIsNone(grade_read.extra_grades(t), t)

    def test_too_long(self):
        t = REAL.replace("[자코모]", "가" * 1200)
        self.assertGreater(len(t), grade_read.MAX_TEXT)
        self.assertIsNone(grade_read.extra_grades(t))


class Assign(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.t = grade_read.Trial(root=self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def task_for(self, arm):
        cfg = self.t.config()
        return next(f"t{i}" for i in range(500) if self.t._arm(cfg, f"t{i}") == arm)

    def test_off_without_config(self):
        a, g = self.t.assign("t1", REAL)
        self.assertIsNone(a)
        self.assertIsNone(g)
        self.assertEqual(rows(self.root), [])

    def test_arms(self):
        write_cfg(self.root)
        ta, tb = self.task_for("A"), self.task_for("B")
        a, g = self.t.assign(ta, REAL)
        self.assertEqual((a["arm"], g), ("A", None))
        b, g = self.t.assign(tb, REAL)
        self.assertEqual((b["arm"], g), ("B", {"B"}))
        r = rows(self.root)
        self.assertEqual([x["event"] for x in r], ["assigned", "assigned"])
        self.assertEqual({x["arm"] for x in r}, {"A", "B"})
        self.assertTrue(all(x["sku"] == "G8ec51935" and len(x["input_sha256"]) == 64 for x in r))
        self.assertNotIn("소파", json.dumps(r, ensure_ascii=False))

    def test_adopted(self):
        write_cfg(self.root, mode="adopted")
        a, g = self.t.assign("t1", REAL)
        self.assertEqual((a["arm"], g), ("adopted", {"B"}))

    def test_ineligible_text_no_row(self):
        write_cfg(self.root)
        self.assertEqual(self.t.assign("t1", "I want to buy G8ec51935"), (None, None))
        self.assertEqual(rows(self.root), [])

    def test_window(self):
        write_cfg(self.root, start_at=time.time() - 30 * 3600, stop_at=time.time() + 3600)
        self.assertEqual(self.t.assign("t1", REAL), (None, None))
        write_cfg(self.root, start_at=time.time() - 7200, stop_at=time.time() - 60)
        self.assertEqual(self.t.assign("t1", REAL), (None, None))
        write_cfg(self.root, enabled=False)
        self.assertEqual(self.t.assign("t1", REAL), (None, None))

    def test_failed_write_keeps_original(self):
        write_cfg(self.root)
        tb = self.task_for("B")
        self.t._write = lambda row: (_ for _ in ()).throw(OSError("disk"))
        self.assertEqual(self.t.assign(tb, REAL), (None, None))

    def test_selected_and_event(self):
        write_cfg(self.root)
        tb = self.task_for("B")
        a, _ = self.t.assign(tb, REAL)
        self.t.selected(tb, a, unit("u-b", "B", 1), None)
        deal = {"grade_read": a, "id": "G8ec51935", "unit": "u-b", "kind": "goods", "rounds": 2, "floor_margin": {"arm": "A"}}
        self.t.event(tb, deal, "deal", 1546976)
        self.t.event(tb, {}, "deal", 1)          # no assignment: no row
        r = rows(self.root)
        self.assertEqual([(x["event"], x.get("result")) for x in r], [("assigned", None), ("selected", None), ("outcome", "deal")])
        self.assertEqual(r[1]["grade"], "B")
        self.assertEqual(r[2]["price"], 1546976)
        self.assertEqual(r[2]["others"]["floor_margin"]["arm"], "A")

    def test_off_while_a_grade_break_trial_is_open(self):
        write_cfg(self.root)
        tb = self.task_for("B")
        now = time.time()
        gbt = dict(start_at=now - 60, stop_at=now + 3600, salt="x", share=0.5, grade_break={"B": 0.19},
                   categories=["가구/침구>"])
        self.assertEqual(self.t.assign(tb, REAL, now=now, pricing={"price": {"grade_break_trial": gbt}}), (None, None))
        self.assertEqual(self.t.assign(tb, REAL, now=now, pricing={"price": {"grade_break_trial": "odd"}}), (None, None))
        self.assertEqual(rows(self.root), [])
        closed = dict(gbt, start_at=now - 7200, stop_at=now - 60)       # 13:00 stop passed: on
        a, g = self.t.assign(tb, REAL, now=now, pricing={"price": {"grade_break_trial": closed}})
        self.assertEqual((a["arm"], g), ("B", {"B"}))
        a, g = self.t.assign(tb, REAL, now=now, pricing={"price": {}})
        self.assertEqual((a["arm"], g), ("B", {"B"}))

    def test_window_cap_12h(self):
        now = time.time()
        write_cfg(self.root, start_at=now - 60, stop_at=now - 60 + 12 * 3600)
        self.assertIsNotNone(self.t.config(now))
        write_cfg(self.root, start_at=now - 60, stop_at=now - 60 + 12 * 3600 + 1)
        self.assertIsNone(self.t.config(now))

    def test_window_edges(self):
        now = time.time()
        write_cfg(self.root, start_at=now, stop_at=now + 9 * 3600)
        self.assertIsNotNone(self.t.config(now))                 # [start, stop): the start is in
        self.assertIsNone(self.t.config(now - 0.001))           # not started
        self.assertIsNone(self.t.config(now + 9 * 3600))         # the stop is out
        self.assertIsNotNone(self.t.config(now + 9 * 3600 - 0.001))

    def test_bad_values_off(self):
        now = time.time()
        d = Path(self.root) / "grade_read"
        base = dict(study="g", salt="s", version=1, epoch=now, enabled=True, mode="ab", share=0.5,
                    start_at=now - 60, stop_at=now + 3600)
        raw = json.dumps(base)
        cases = [raw.replace(f'"stop_at": {json.dumps(base["stop_at"])}', '"stop_at": NaN'),
                 raw.replace(f'"stop_at": {json.dumps(base["stop_at"])}', '"stop_at": Infinity'),
                 raw.replace(f'"start_at": {json.dumps(base["start_at"])}', '"start_at": -Infinity'),
                 raw.replace(f'"start_at": {json.dumps(base["start_at"])}', '"start_at": true'),
                 raw.replace(f'"stop_at": {json.dumps(base["stop_at"])}', '"stop_at": 1' + "0" * 400),
                 raw.replace('"enabled": true', '"enabled": 1'),
                 raw.replace('"share": 0.5', '"share": 1.5'),
                 raw.replace('"mode": "ab"', '"mode": "B"'),
                 json.dumps({k: v for k, v in base.items() if k != "stop_at"}),
                 json.dumps({k: v for k, v in base.items() if k != "start_at"}),
                 json.dumps(dict(base, start_at=now + 60, stop_at=now + 3600)),
                 "[]", "", "{"]
        for c in cases:
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text(c)
            self.t._cache = None
            self.assertIsNone(self.t.config(now), c[:120])
            self.assertEqual(self.t.assign("t1", REAL, now=now), (None, None), c[:120])
        self.assertEqual(rows(self.root), [])

    def test_outcome_rows_say_whether_a_quote_went_out(self):
        write_cfg(self.root)
        tb = self.task_for("B")
        a, _ = self.t.assign(tb, REAL)
        deal = {"grade_read": a, "id": "G8ec51935", "unit": "u-b", "kind": "goods", "rounds": 0}
        self.t.event(tb, deal, "canceled")                         # an error between self.open and the quote
        deal["grade_read_quoted"] = True
        self.t.event(tb, deal, "deal", 1546976)
        self.assertEqual([(x["result"], x["quoted"]) for x in rows(self.root)[1:]], [("canceled", False), ("deal", True)])

    def test_no_quote_rows_apart_from_outcomes(self):
        write_cfg(self.root)
        tb = self.task_for("B")
        a, _ = self.t.assign(tb, REAL)
        self.t.no_quote(tb, a, "warranty", unit("u-b", "B", 1))
        self.t.no_quote(tb, a, "unit_unavailable", None)
        self.t.no_quote(tb, None, "selection", None)              # no assignment: no row
        self.t.no_quote(tb, {"bad": 1}, "selection", None)
        deal = {"grade_read": a, "id": "G8ec51935", "unit": "u-b", "kind": "goods", "rounds": 3}
        self.t.event(tb, deal, "rejected", 1500000)                # seller.remember after a quote: an outcome row
        r = rows(self.root)
        self.assertEqual([(x["event"], x.get("reason")) for x in r],
                         [("assigned", None), ("no_quote", "warranty"), ("no_quote", "unit_unavailable"),
                          ("outcome", None)])
        self.assertEqual((r[3]["result"], r[3]["price"]), ("rejected", 1500000))
        self.assertEqual((r[1]["unit"], r[1]["grade"], r[1]["arm"]), ("u-b", "B", "B"))
        self.assertIsNone(r[2]["unit"])

    def test_selected_records_both_picks(self):
        write_cfg(self.root)
        ta, tb = self.task_for("A"), self.task_for("B")
        ua, ub = unit("u-a", "A", 1642751), unit("u-b", "B", 1322319)
        a, _ = self.t.assign(ta, REAL)
        self.t.selected(ta, a, ua, None, alt=ub)                  # arm A quotes u-a; arm B would have quoted u-b
        b, _ = self.t.assign(tb, REAL)
        self.t.selected(tb, b, ub, None, alt=ua)
        sel = [x for x in rows(self.root) if x["event"] == "selected"]
        for x in sel:
            self.assertEqual((x["unit_a"], x["grade_a"], x["cost_a"]), ("u-a", "A", 1642751))
            self.assertEqual((x["unit_b"], x["grade_b"], x["cost_b"]), ("u-b", "B", 1322319))
            self.assertTrue(x["switch"])
        self.assertEqual([x["unit"] for x in sel], ["u-a", "u-b"])
        self.t.selected(ta, a, ua, None, alt=ua)
        self.assertFalse(rows(self.root)[-1]["switch"])

    def test_goods_margin_in_others(self):
        write_cfg(self.root)
        tb = self.task_for("B")
        a, _ = self.t.assign(tb, REAL)
        deal = {"grade_read": a, "id": "G8ec51935", "unit": "u-b", "goods_margin": {"study": "goods-margin-1110", "arm": "B"}}
        self.t.event(tb, deal, "deal", 1)
        self.assertEqual(rows(self.root)[-1]["others"]["goods_margin"], {"study": "goods-margin-1110", "arm": "B"})

    def test_never_raises(self):
        write_cfg(self.root)
        self.t.selected("t", None, None, None)
        self.t.selected("t", {"bad": 1}, {"unit": "x"}, "why")
        self.t.event("t", {"grade_read": {"bad": 1}}, "deal")
        self.assertEqual(self.t.assign("t", object()), (None, None))


class SwitchConfig(unittest.TestCase):
    def test_finite_9_to_12h(self):
        c = grade_read.make_config(1794290000.0, 12, "g-read-1110")
        self.assertEqual((c["start_at"], c["stop_at"], c["epoch"]), (1794290000.0, 1794290000.0 + 43200, 1794290000.0))
        self.assertEqual((c["mode"], c["share"], c["enabled"], c["version"], c["salt"]), ("ab", 0.5, True, 1, "g-read-1110-salt"))
        with tempfile.TemporaryDirectory() as td:
            t = grade_read.Trial(root=td)
            (Path(td) / "grade_read").mkdir()
            (Path(td) / "grade_read" / "config.json").write_text(json.dumps(c))
            self.assertIsNotNone(t.config(1794290000.0 + 60))
            self.assertIsNone(t.config(1794290000.0 + 43200))
        self.assertEqual(grade_read.make_config(1794290000, 9, "g")["stop_at"], 1794290000 + 32400)

    def test_rejects(self):
        for args in ((1794290000.0, 8.99, "g"), (1794290000.0, 12.01, "g"), (1794290000.0, float("nan"), "g"),
                     (1794290000.0, True, "g"), (float("inf"), 12, "g"), (True, 12, "g"), (0, 12, "g"),
                     (1794290000.0, 12, ""), (1794290000.0, 12, None), (10 ** 400, 12, "g")):
            with self.assertRaises(ValueError, msg=repr(args)[:80]):
                grade_read.make_config(*args)


class SellerWiring(unittest.TestCase):
    SRC = (HERE / "seller.py").read_text(encoding="utf-8")

    def test_wired(self):
        s = self.SRC
        self.assertIn("import grade_read", s)
        self.assertRegex(s, r"GRADE_READ = grade_read\.Trial\(\)")
        i = s.index("asyncio.to_thread(GRADE_READ.assign, task.id, ")
        j = s.index("self.stock.pick(text, rank=lambda x: floor_rank(x, ranked)")
        self.assertLess(i, j)
        i = j
        self.assertIn("add_grades=grade_add", s[i:i + 900])
        self.assertIn("asyncio.to_thread(GRADE_READ.selected, task.id, grade_read_assignment, u, miss, alt=grade_read_alt)", s)
        self.assertRegex(s, r'deal\["grade_read"\] = grade_read_assignment')
        self.assertIn("GRADE_READ.event(task_id, deal, result, price, observed_at)", s)

    def test_rejections_journaled(self):
        # every way the opening ends before a quote leaves a row (independent review 889d594 M1)
        s = self.SRC
        for reason, before in (("selection", 'await up.reject(say("Sorry, we have none of that in stock."'),
                               ("warranty", 'await up.reject(say("Sorry, we cannot sell this item with a fair warranty'),
                               ("unit_unavailable", 'await up.reject(say("That unit is no longer available')):
            call = f'GRADE_READ.no_quote, task.id, grade_read_assignment, "{reason}", u)'
            self.assertIn(call, s)
            self.assertLess(s.index(call), s.index(before))
            self.assertLess(s.index(before) - s.index(call), 600)
            self.assertIn("if grade_read_assignment is not None:", s[s.index(call) - 140:s.index(call)])

    def test_other_pick_before_any_await(self):
        s = self.SRC
        i = s.index("snapshot = self.stock.units()")
        j = s.index("await asyncio.to_thread(GRADE_READ.selected, task.id, grade_read_assignment, u, miss, alt=")
        between = s[i:j]
        self.assertEqual(between.count("units=snapshot"), 2)       # both arms' picks on one stock read
        self.assertIn('add_grades=None if grade_add else {"B"}', between)
        self.assertNotIn("await", between)
        # OFF and ineligible: the original call, exactly
        self.assertIn("u, miss = self.stock.pick(text, rank=lambda x: floor_rank(x, ranked), **grade_kwargs)", s)

    def test_eligible_text_is_what_pick_reads(self):
        self.assertEqual(grade_read.MAX_TEXT, int(re.search(r"^MAX_TEXT = (\d+)", self.SRC, re.M)[1]))

    def test_quoted_flag_right_after_the_quote(self):
        s = self.SRC
        i = s.index("            await up.requires_input(say(quote_text, ask_price))\n")
        self.assertEqual(s[i:].split("\n")[1:3], ["            if grade_read_assignment is not None:",
                                                   '                deal["grade_read_quoted"] = True'])

    def test_assign_reads_the_whole_message_and_pricing(self):
        self.assertIn("GRADE_READ.assign, task.id, text if warranty_text is None else warranty_text,\n"
                      "                                                                  pricing=pricing)", self.SRC)

    def test_switch_listed(self):
        import meta_harness
        self.assertIn(".market/grade_read/config.json", meta_harness.SWITCHES)

    def test_sol_source(self):
        src = (HERE / "sol_seller_margin.py").read_text(encoding="utf-8")
        self.assertIn("'grade_read.py'", src)


SEAM = r"""
import asyncio,json,sys,time,uuid
from types import SimpleNamespace
from unittest.mock import patch
import seller
from shop_stock import StockBook
mode=sys.argv[1]
REAL=sys.argv[2]
root=seller.GRADE_READ.root
if mode!='off':
 root.mkdir(parents=True,exist_ok=True)
 (root/'config.json').write_text(json.dumps(dict(study='g-read',salt='s',version=1,epoch='t',enabled=True,mode='ab',
  share=1.0 if mode.startswith('B') else 0.0,start_at=time.time()-5,stop_at=time.time()+3600)))
def unit(u,g,cost):
 return dict(unit=u,sku='G8ec51935',grade=g,kind='goods',name='소파',cost=cost,list_price=2320000,category='가구>소파',supplied_at=1,expires_at=None,cert=True)
units=([] if mode.endswith('none') else [unit('u-b','B',1322319)] if mode.endswith('war') else [unit('u-a','A',1642751),unit('u-b','B',1322319)])
class Capture:
 def new_agent_message(self,**kw):return kw
 async def requires_input(self,message):self.message=message
 async def reject(self,message):self.message=dict(rejected=message)
 async def failed(self,message):raise AssertionError('failed')
shop=seller.Seller(None,'fixture',stock=StockBook(lambda:dict(round=3,units=units)))
cap=Capture();task=SimpleNamespace(id=str(uuid.uuid4()),context_id='ctx')
with patch.object(seller,'TaskUpdater',return_value=cap),patch.object(seller,'get_message_text',return_value=REAL),\
     patch.object(seller,'new_text_part',side_effect=lambda t:t):
 with patch.object(seller,'ask',side_effect=AssertionError('unexpected external harness')):
  asyncio.run(shop._execute(SimpleNamespace(current_task=task,message=None),None))
d=shop.open.get(task.id) or {}
seller.MEMORY_WRITER.shutdown(wait=True)
rows=[json.loads(x) for x in (root/'events.jsonl').read_text().splitlines()] if (root/'events.jsonl').exists() else []
print('SEAM_JSON '+json.dumps(dict(msg=str(cap.message)[:400],unit=d.get('unit'),ask=d.get('ask'),reserve=d.get('reserve'),arm=(d.get('grade_read') or {}).get('arm'),rows=rows),ensure_ascii=False))
"""


class SellerSeam(unittest.TestCase):
    """seller._execute opening the real G8ec51935 talk with an A unit (floor 1706590 on ops) and a cheaper B unit."""
    runs = {}

    @classmethod
    def run_mode(cls, mode):
        if mode not in cls.runs:
            with tempfile.TemporaryDirectory() as td:
                p = Path(td)
                (p / "pricing.json").write_text(json.dumps({"price": {"grade_break": {"A": 0.03, "B": 0.9, "C": 0.344}}})
                                                if mode.endswith("war") else "{}")
                (p / "memory.json").write_text('{"negotiations":[]}')
                env = dict(os.environ, MARKET_DIR=td, SHOP_PRICING=str(p / "pricing.json"),
                           SHOP_MEMORY=str(p / "memory.json"), NEGOTIATE_ENGINE="jevrule")
                env.pop("GRADE_READ_CONFIG", None)
                r = subprocess.run([sys.executable, "-B", "-c", SEAM, mode, REAL], env=env, cwd=HERE,
                                   capture_output=True, text=True, timeout=60)
                line = [x for x in r.stdout.splitlines() if x.startswith("SEAM_JSON ")]
                assert r.returncode == 0 and line, r.stdout[-3000:] + r.stderr[-3000:]
                cls.runs[mode] = json.loads(line[0][len("SEAM_JSON "):])
        return cls.runs[mode]

    def test_off_and_control_quote_the_a_unit(self):
        off, a = self.run_mode("off"), self.run_mode("A")
        for r in (off, a):
            self.assertEqual(r["unit"], "u-a")
            self.assertIn("grade A.", r["msg"])
        self.assertEqual((off["ask"], off["reserve"]), (a["ask"], a["reserve"]))
        self.assertEqual(off["rows"], [])
        self.assertEqual([x["event"] for x in a["rows"]], ["assigned", "selected"])
        self.assertEqual(a["rows"][1]["grade"], "A")
        self.assertEqual((a["rows"][1]["unit_a"], a["rows"][1]["unit_b"], a["rows"][1]["switch"]), ("u-a", "u-b", True))

    def test_treatment_quotes_the_b_unit_priced_as_b(self):
        b = self.run_mode("B")
        self.assertEqual((b["unit"], b["arm"]), ("u-b", "B"))
        self.assertIn("grade B.", b["msg"])
        self.assertIn("72 h warranty", b["msg"])
        self.assertLess(b["reserve"], self.run_mode("A")["reserve"])
        self.assertGreater(b["reserve"], 1322319)          # above the B unit's cost
        self.assertEqual(b["rows"][1]["grade"], "B")
        self.assertEqual((b["rows"][1]["unit_a"], b["rows"][1]["unit_b"], b["rows"][1]["switch"]), ("u-a", "u-b", True))

    def test_none_in_stock_leaves_a_no_quote_row(self):
        r = self.run_mode("Bnone")
        self.assertIn("none of that in stock", r["msg"])
        self.assertEqual([(x["event"], x.get("reason")) for x in r["rows"]],
                         [("assigned", None), ("selected", None), ("no_quote", "selection")])
        self.assertEqual(r["rows"][1]["miss"], "not in stock")
        self.assertFalse(r["rows"][1]["switch"])

    def test_warranty_refusal_leaves_a_no_quote_row(self):
        # only a grade B unit, and B breaks over warranty.max_break: arm B picks it and the quote refuses it
        r = self.run_mode("Bwar")
        self.assertIn("fair warranty", r["msg"])
        self.assertEqual([(x["event"], x.get("reason")) for x in r["rows"]],
                         [("assigned", None), ("selected", None), ("no_quote", "warranty")])
        self.assertEqual((r["rows"][2]["unit"], r["rows"][2]["grade"]), ("u-b", "B"))


if __name__ == "__main__":
    unittest.main()
