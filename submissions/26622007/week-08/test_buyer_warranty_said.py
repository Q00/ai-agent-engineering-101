import inspect
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import buyer_short_warranty as sw
import buyer_warranty_said as ws
import market_buyer

# measure/orders.jsonl, 11/09: o-7432c695ba11 and o-234498e79c5f (S .3096, history {seochanit, thisjonghoon, yeonsong2,
# yms979: 0}); the quote texts as recorded (300 characters), limits 192,767 and 127,121 from the 0 h history
THISJONGHOON = ("시몬스 뷰티레스트 시트러스. 슈퍼싱글 매트리스 (G85a1a856): grade A, market certificate attached, 72 h warranty. "
                "List price 921600, asking 57")
THISJONGHOON_2 = ("[에이스침대]BMA 1139-E CA등급/SS(슈퍼싱글사이즈) (G8095ddd6): grade A, market certificate attached, 72 h "
                  "warranty. List price 851400, ")
YMS979 = ("Thanks for asking! 시몬스 뷰티레스트 시트러스. 슈퍼싱글 매트리스 (G85a1a856), grade B, 72 h warranty, market "
          "certificate included. Asking 71")
SEOCHANIT = "G85a1a856: asking 708417. Send your integer offer. "
HIST = {"seochanit": 0, "thisjonghoon": 0, "yeonsong2": 0, "yms979": 0}
T0 = 1794220000.0


def quote(text):
    return {"warranty_h": market_buyer.warranty_hours_said(text), "warranty_said_ok": ws.stated_ok(text), "text": text}


def order_sw(arm):
    return {"S": 0.3096, "buffer": 1000000, "hist": dict(HIST),
            **({"said": {"study": "bw1", "v": ws.VERSION, "arm": arm}} if arm else {})}


class HoursForTest(unittest.TestCase):
    def test_arm_b_prices_a_quote_naming_72h_as_a_6h_deal(self):
        # before B-W1: warranty_hours_said(72 h) is None, so the 0 h history alone was left (limit 192,767)
        for text in (THISJONGHOON, THISJONGHOON_2, YMS979):
            q = quote(text)
            self.assertIsNone(q["warranty_h"])
            self.assertTrue(q["warranty_said_ok"])
            self.assertIsNone(sw.hours_for(order_sw("B"), "thisjonghoon", q))
            self.assertEqual(sw.hours_for(order_sw("A"), "thisjonghoon", q), 0)
            self.assertEqual(sw.hours_for(order_sw(None), "thisjonghoon", q), 0)

    def test_a_quote_that_names_no_warranty_keeps_the_history(self):
        q = quote(SEOCHANIT)
        self.assertFalse(q["warranty_said_ok"])
        self.assertEqual(sw.hours_for(order_sw("B"), "seochanit", q), 0)

    def test_a_short_or_mixed_warranty_is_never_set_aside(self):
        for text in ("grade C, no warranty. asking 5000", "warranty: 0 h", "three-hour full-refund warranty",
                     "grade C 3 h warranty, grade A 72 h warranty", "72 h warranty on A; this unit is sold as-is",
                     "Warranty: 2 hours. asking 100"):
            q = quote(text)
            self.assertFalse(q["warranty_said_ok"], text)
            self.assertEqual(sw.hours_for(order_sw("B"), "thisjonghoon", q), 0, text)     # the least with the 0 h history

    def test_a_negated_or_optional_warranty_is_never_set_aside(self):
        # independent review 08b282d I1: each of these read True before
        for text in ("No 72 h warranty on this unit.", "72 h warranty does not apply to this unit.",
                     "Grade C, 72 h warranty excluded", "72 hours warranty-free",
                     "Optional 72 h warranty (extra charge).",
                     "72-hour warranty available for +12%; base price excludes it.",
                     "Base price includes 0 h; 72 hour warranty on request.",
                     "Grade A, 72 h warranty. This unit: zero-hour warranty.",
                     "Grade A, 72 h warranty. Base price warranty: none.",
                     "warranty not included. Typical 72 h warranty", "72 hour warranty. 보증 미적용",
                     "72 h warranty, 안심케어 미포함", "Grade A 72 h warranty. Grade C refurbished: warranty void.",
                     "Warranty 0 h included in this total price. 72 h warranty",
                     "72 h warranty if you pay list", "72 h warranty isn't included",
                     # review 2 m3
                     "72 h warranty. Without it the price is 9000", "72 h warranty. This price ships without one.",
                     "72 h warranty, 1 h for this price", "72 h warranty; this price: warranty waived",
                     "72 h warranty. 0.5 h for grade C", "72 h warranty. 30 min for grade C", "72시간 보증, C등급 30분",
                     # review 3 m-B, m-C
                     "72 h warranty. \nThis price ships without one.", "72 h warranty! ...Without it 9000",
                     "72 h warranty, three hours for this price", "72 h warranty. grade C: two hours",
                     # review 4 m3, m4
                     "72 h warranty.\n-\nWithout it 9000", "72 h warranty. -. Without it 9000",
                     "72 h warranty. … Without it 9000",
                     "72 h warranty, grade C: five minutes", "72 h warranty; this price: delivered within an hour",
                     "72 h warranty, grade C half an hour", "72 h warranty, a few hours for this price",
                     # review 5: twelve minutes is 0.2 h, not 12; a couple/half/several
                     "72 h warranty, grade C: twelve minutes", "72 h warranty, a couple hours for this price",
                     "72 h warranty, grade C half-hour", "72 h warranty, several hours for grade C",
                     # Pro 1930 3-1: another grade's warranty
                     "72 h warranty for grade A; this unit is grade C.", "72 h warranty for grade A. This one is grade B.",
                     "A등급 72시간 warranty, C등급 별도",
                     # review 6 I1/I2: an article, lists, plurals, grade-C
                     "72 h warranty for grade A; this is a grade C unit.", "grade A 72 h warranty, a grade B unit",
                     "Grade A/B mixed lot, 72 h warranty", "grades A and C, 72 h warranty",
                     "Grade A-C mixed, 72 h warranty", "72 h warranty grade A, grade-C sold separately",
                     "A or B grade, 72 h warranty"):
            q = quote(text)
            self.assertFalse(q["warranty_said_ok"], text)
            self.assertEqual(sw.hours_for(order_sw("B"), "thisjonghoon", q), 0, text)
        for text in (THISJONGHOON, THISJONGHOON_2, YMS979, "No scratches. Grade A, 72 h warranty.",
                     # dyishappy's and moss-and-oak's own wording; product names with 없/선택 are no hedge
                     "누우리 토퍼 (G8526e66c). Grade A: normal. 6-hour breakage warranty: market refunds the full price if "
                     "it breaks within 6 hours of delivery. Asking 23000.",
                     "레이디가구 선반 (G38a0dca5), grade B, 12-hour warranty included (full refund if it breaks)",
                     "바숨 알러지케어 먼지없는 누빔 매트리스커버 (G575cddb8), grade A, 72 h warranty",
                     "지누스 루미 침대 프레임 색상선택 2color (Ge04f5cab), grade A, 72 h warranty, market certificate included",
                     # one grade named twice is one grade (review 6)
                     "Grade A, 72 h warranty. A-grade unit.", "Grade A 72 h warranty, a grade A phone",
                     "GRADE A, 72 h warranty. Grade A certified."):
            self.assertTrue(ws.stated_ok(text), text)       # a negation outside the warranty sentence is no hedge

    def test_a_reading_that_raises_is_not_set_aside(self):
        with mock.patch.object(ws, "_stated_ok", side_effect=RuntimeError):
            self.assertFalse(ws.stated_ok(THISJONGHOON))

    def test_a_quote_with_its_own_hours_is_not_set_aside(self):
        q = dict(quote(THISJONGHOON), warranty_h=3)
        self.assertFalse(ws.applies(order_sw("B"), q))
        self.assertEqual(sw.hours_for(order_sw("B"), "thisjonghoon", q), 0)

    def test_a_0h_receipt_after_a_6h_deal_is_refused_as_now(self):
        o = {"list_price": 921600, "sku": "G85a1a856"}
        s = order_sw("B")
        price = sw.cap(o["list_price"], 6, 0.03, s["S"])       # the arm B limit before the band
        self.assertGreater(price, sw.cap(o["list_price"], 0, 0.03, s["S"]))
        with tempfile.TemporaryDirectory() as md:
            ok, rec, why = sw.receipt_check(s, md, {"warranty_hours": 0, "price": price}, o["list_price"], 0.03)
            self.assertFalse(ok)
            self.assertIn("no warranty", why)            # block_zero_nonfood on: refused before any price
            ok, rec, why = sw.receipt_check(dict(s, block_zero=False), md, {"warranty_hours": 0, "price": price},
                                            o["list_price"], 0.03)
        self.assertFalse(ok)
        self.assertIn("p_max", why)                      # its rollback: over p_max(0 h), as before

    def test_stated_ok_readings(self):
        for text in ("72 h warranty", "warranty: 24 h", "six-hour full-refund warranty", "6 h warranty", "warranty of 3 days"):
            self.assertTrue(ws.stated_ok(text), text)
        for text in ("", None, "asking 5000", "5 h warranty", "warranty 0 hours"):
            self.assertFalse(ws.stated_ok(text), text)

    def test_short_cap_uses_the_6h_path_in_arm_b(self):
        o = {"list_price": 921600, "sku": "G85a1a856"}
        q = quote(THISJONGHOON)
        self.assertIsNone(market_buyer.short_cap(o, 0.03, {"_sw": order_sw("B")}, "thisjonghoon", q))
        self.assertIsNotNone(market_buyer.short_cap(o, 0.03, {"_sw": order_sw("A")}, "thisjonghoon", q))

    def test_the_receipt_check_is_untouched(self):
        order = {"kind": "goods", "_sw": order_sw("B")}
        self.assertIsNotNone(market_buyer.warranty_short({"warranty_hours": 0, "price": 500000}, order))
        self.assertIsNone(market_buyer.warranty_short({"warranty_hours": 72, "price": 500000}, order))

    def test_record_names_the_eligible_shops_in_both_arms(self):
        quotes = {"thisjonghoon": quote(THISJONGHOON), "yms979": quote(YMS979), "seochanit": quote(SEOCHANIT),
                  "mjforge": quote(THISJONGHOON)}          # mjforge: no 0 h history, nothing to set aside
        for arm in ("A", "B"):
            r = ws.record(order_sw(arm), quotes, "thisjonghoon", None, 192767)
            self.assertEqual(r["arm"], arm)
            self.assertEqual(r["eligible"], ["thisjonghoon", "yms979"])
            self.assertEqual((r["chosen"], r["chosen_eligible"], r["applied"], r["refused"], r["cap0"]),
                             ("thisjonghoon", True, arm == "B", False, 192767))
            r = ws.record(order_sw(arm), quotes, "seochanit", "warranty 0 h: price over p_max", 100)
            self.assertEqual((r["chosen_eligible"], r["applied"], r["refused"], r["cap0"]), (False, False, True, None))
        self.assertIsNone(ws.record(order_sw(None), quotes))
        self.assertIsNone(ws.record(None, quotes))

    def test_arm_b_rows_and_trades_are_kept_out_of_the_learning(self):
        self.assertTrue(ws.said_b({"warranty_said": {"arm": "B"}}))
        for row in ({"warranty_said": {"arm": "A"}}, {}, None, {"warranty_said": "B"}):
            self.assertFalse(ws.said_b(row), row)
        b = dict(order_sw("B"), said={"study": "bw1", "v": ws.VERSION, "arm": "B", "stop_at": T0 + 3600})
        self.assertEqual(ws.trade_mark({"_sw": b}), {"arm": "B", "stop_at": T0 + 3600})
        self.assertIsNone(ws.trade_mark({"_sw": order_sw(None)}))
        catalog = {"G1": {"kind": "goods"}}
        now = time.time()
        at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 60))
        memory = {"trades": [{"time": at, "shop": "thisjonghoon", "item": "G1", "result": "no_warranty",
                               "warranty_said": {"arm": "B", "stop_at": now + 3600}}]}
        self.assertEqual(market_buyer.short_warranty_shops(memory, catalog, now), set())
        memory["trades"][0]["warranty_said"] = {"arm": "B", "stop_at": now - 1}     # review 2 m2: after the window
        self.assertEqual(market_buyer.short_warranty_shops(memory, catalog, now), {"thisjonghoon"})
        for mark in ({"arm": "A", "stop_at": now + 3600}, None, "B"):
            memory["trades"][0]["warranty_said"] = mark
            self.assertEqual(market_buyer.short_warranty_shops(memory, catalog, now), {"thisjonghoon"}, mark)
        import buyer_rank_pg
        import buyer_shop_profile
        for mod in (buyer_rank_pg, buyer_shop_profile, market_buyer):
            self.assertIn("buyer_warranty_said.said_b(r)", Path(mod.__file__).read_text(encoding="utf-8"), mod.__name__)

    def test_rank_pg_stats_leave_arm_b_rows_out(self):
        import buyer_rank_pg
        row = {"ts": T0 - 600, "order": "o-1", "shop": "a", "kind": "goods", "quotes": [{"shop": "a", "ask": 1000}],
               "asks": [1000], "state": "TASK_STATE_COMPLETED", "price": 900, "passed_to_kit": True,
               "seconds": {"quotes": 5.0, "end": 15.0}}
        a = buyer_rank_pg.stats_from_rows([dict(row, warranty_said={"arm": "A"})], {"o-1": "confirmed"}, T0)
        b = buyer_rank_pg.stats_from_rows([dict(row, warranty_said={"arm": "B"})], {"o-1": "confirmed"}, T0)
        self.assertEqual(b["counts"]["excluded_rows"], a["counts"]["excluded_rows"] + 1)


class SwitchTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.md = self.td.name

    def tearDown(self):
        self.td.cleanup()

    def write(self, d):
        Path(self.md, ws.SWITCH).write_text(json.dumps(d))

    def good(self, **kw):
        return {"enabled": True, "study": "bw1-test", "salt": "bw1-salt", "share": 0.5,
                "start_at": T0, "stop_at": T0 + 9 * 3600, **kw}

    def test_on_only_inside_the_window(self):
        self.write(self.good())
        self.assertIsNone(ws.config(self.md, {}, T0 - 1))
        self.assertIsNotNone(ws.config(self.md, {}, T0))
        self.assertIsNotNone(ws.config(self.md, {}, T0 + 9 * 3600 - 0.001))
        self.assertIsNone(ws.config(self.md, {}, T0 + 9 * 3600))
        self.assertIsNone(ws.config(self.md, {}, T0 + 9 * 3600 + 1))

    def test_bad_or_missing_fields_are_off(self):
        self.assertIsNone(ws.config(self.md, {}, T0))                 # no file
        for bad in ({"enabled": False}, {"start_at": None}, {"stop_at": None}, {"stop_at": T0 + 13 * 3600},
                    {"stop_at": T0}, {"start_at": float("nan")}, {"share": 0}, {"share": 1}, {"share": 1.5}, {"share": True},
                    {"salt": ""}, {"study": None}, {"enabled": "true"}):
            self.write(self.good(**bad))
            self.assertIsNone(ws.config(self.md, {}, T0 + 60), bad)
        Path(self.md, ws.SWITCH).write_text("{not json")
        self.assertIsNone(ws.config(self.md, {}, T0 + 60))

    def test_environment_off(self):
        self.write(self.good())
        self.assertIsNone(ws.config(self.md, {"BUYER_WARRANTY_SAID": "0"}, T0 + 60))
        self.assertIsNotNone(ws.config(self.md, {"BUYER_WARRANTY_SAID": ""}, T0 + 60))

    def test_state_arms_split_by_its_own_salt(self):
        self.write(self.good())
        arms = [ws.state(self.md, f"o-{i:012x}", {}, T0 + 60)["arm"] for i in range(2000)]
        self.assertTrue(0.45 < arms.count("B") / len(arms) < 0.55)
        s = ws.state(self.md, "o-7432c695ba11", {}, T0 + 60)
        self.assertEqual((s["study"], s["v"], s["start_at"], s["stop_at"]), ("bw1-test", ws.VERSION, T0, T0 + 9 * 3600))
        self.assertEqual(s["arm"], ws.arm("bw1-salt", "o-7432c695ba11", 0.5))
        self.assertIsNone(ws.state(self.md, "", {}, T0 + 60))
        self.assertIsNone(ws.state(self.md, "o-1", {}, T0 + 10 * 3600))

    def test_off_while_goods_grade_is_on(self):
        self.write(self.good())
        import buyer_goods_grade
        with mock.patch.object(buyer_goods_grade, "config", return_value={"study": "g"}):
            self.assertIsNone(ws.state(self.md, "o-1", {}, T0 + 60))
        with mock.patch.object(buyer_goods_grade, "config", return_value=None):
            self.assertIsNotNone(ws.state(self.md, "o-1", {}, T0 + 60))

    def test_too_many_refused_receipts_send_every_order_to_arm_a(self):
        self.write(self.good())
        oid = next(f"o-{i}" for i in range(100) if ws.arm("bw1-salt", f"o-{i}", 0.5) == "B")
        said = {"study": "bw1-test", "v": ws.VERSION, "start_at": T0}
        for refused in (False,) * 9 + (True,) * 3:
            ws.note(self.md, said, refused, T0)
        ws.note(self.md, {"study": "other", "start_at": T0}, True, T0)              # another study's: never count
        for _ in range(5):
            ws.note(self.md, dict(said, start_at=T0 - 86400), True, T0 - 86400)    # a reused name's old window
        s = ws.state(self.md, oid, {}, T0 + 60)
        self.assertEqual((s["assigned"], s["arm"]), ("B", "B"))                    # 3 refused: under TRIP_MIN
        ws.note(self.md, said, True, T0)                                           # 4 of 13 refused: over 25%
        s = ws.state(self.md, oid, {}, T0 + 60)
        self.assertEqual((s["assigned"], s["arm"], s["tripped"]), ("B", "A", {"receipts": 13, "refused": 4}))
        self.assertEqual(stat.S_IMODE(os.stat(Path(self.md, ws.EVENTS)).st_mode), 0o600)
        Path(self.md, ws.EVENTS).write_text("{bad\n" + json.dumps({"study": "bw1-test", "refused": True}) + "\n")
        self.assertIsNone(ws.tripped(self.md, "bw1-test", T0))

    def test_only_receipts_count_for_the_stop(self):
        # review 2 I-b: an applied arm B order with no receipt (no deal, timeout) is not in the denominator
        quotes = {"thisjonghoon": quote(THISJONGHOON)}
        s = dict(order_sw("B"), said={"study": "bw1-test", "v": ws.VERSION, "assigned": "B", "arm": "B",
                                      "start_at": T0, "stop_at": T0 + 9 * 3600})
        for _ in range(20):
            r = ws.measure(self.md, s, quotes, "thisjonghoon", None, None, 100)
            self.assertTrue(r["applied"])
        self.assertFalse(Path(self.md, ws.EVENTS).exists())
        for _ in range(4):
            ws.measure(self.md, s, quotes, "thisjonghoon", {"price": 1}, "warranty 0 h: over p_max", 100)
        self.assertEqual(ws.tripped(self.md, "bw1-test", T0), {"receipts": 4, "refused": 4})
        with mock.patch.object(ws, "note", side_effect=IsADirectoryError):     # review 3 m-A: the row stays
            r = ws.measure(self.md, s, quotes, "thisjonghoon", {"price": 1}, None, 100)
        self.assertEqual((r["applied"], r["note_error"]), (True, "IsADirectoryError"))
        a = dict(s, said=dict(s["said"], assigned="A", arm="A"))
        r = ws.measure(self.md, a, quotes, "thisjonghoon", {"price": 1}, "short", 100)    # arm A: never noted
        self.assertFalse(r["applied"])
        self.assertEqual(ws.tripped(self.md, "bw1-test", T0)["receipts"], 4)

    def test_switch_on_writes_a_finite_window_0600(self):
        d = ws.switch_on(self.md, study="bw1-test", salt="s", start_at=T0, hours=9)
        self.assertEqual(d["stop_at"], T0 + 9 * 3600)
        p = Path(self.md, ws.SWITCH)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertIsNotNone(ws.config(self.md, {}, T0 + 1))
        before = p.read_text()
        for bad in ({"hours": 13}, {"hours": 0}, {"hours": None}, {"start_at": None}, {"salt": ""}):
            kw = {"study": "bw1-test", "salt": "s", "start_at": T0, "hours": 9, **bad}
            with self.assertRaises(ValueError):
                ws.switch_on(self.md, **kw)
            self.assertEqual(p.read_text(), before)
        self.assertEqual([n for n in os.listdir(self.md) if ".tmp-" in n], [])


class WiringTest(unittest.TestCase):
    def test_market_buyer_reads_the_switch_once_and_records_both_arms(self):
        src = Path(market_buyer.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("buyer_warranty_said.state(md, order.get(\"order\"))"), 1)
        self.assertEqual(src.count("q[\"warranty_said_ok\"] = buyer_warranty_said.stated_ok(text)"), 1)
        self.assertEqual(src.count("buyer_warranty_said.measure(md, order[\"_sw\"], quotes, shop[\"name\"], rec, short, cap0)"), 1)
        self.assertEqual(src.count('"warranty_said": buyer_warranty_said.trade_mark(order)'), 1)
        # arm B trades stay out of both readings of the 0 h history: short_warranty_shops and zero_warranty_shops
        for fn in (market_buyer.short_warranty_shops, market_buyer.zero_warranty_shops):
            self.assertEqual(inspect.getsource(fn).count("buyer_warranty_said.held_out(t, now)"), 1, fn.__name__)
        self.assertEqual(src.count("buyer_warranty_said.held_out(t, now)"), 2)
        self.assertEqual(src.count('cap0 = short_cap(offer, chance, {**order, "_sw": {**order["_sw"], "said": None}}'), 1)
        self.assertEqual(src.count("said = warranty_said_row(md, order, offer, chance, shop, quotes, rec, short)"), 1)
        self.assertEqual(src.count('**({"warranty_said": said} if said else {})'), 1)

    def test_a_failing_cap0_keeps_the_row_and_a_failing_note_is_logged(self):
        # review 4 m1/m2: warranty_said_row
        md = tempfile.mkdtemp()
        try:
            order = {"order": "o-1", "_sw": dict(order_sw("B"), said={"study": "bw1-test", "v": ws.VERSION,
                     "assigned": "B", "arm": "B", "start_at": T0, "stop_at": T0 + 9 * 3600})}
            quotes = {"thisjonghoon": quote(THISJONGHOON)}
            shop = {"name": "thisjonghoon"}
            lines = []
            with mock.patch.object(market_buyer, "short_cap", side_effect=ValueError("x")), \
                    mock.patch.object(market_buyer, "log", side_effect=lines.append):
                r = market_buyer.warranty_said_row(md, order, {}, 0.03, shop, quotes, None, None)
            self.assertEqual((r["applied"], r.get("cap0")), (True, None))
            self.assertTrue(any("cap0 failed" in s for s in lines), lines)
            lines.clear()
            with mock.patch.object(ws, "note", side_effect=IsADirectoryError), \
                    mock.patch.object(market_buyer, "short_cap", return_value=100), \
                    mock.patch.object(market_buyer, "log", side_effect=lines.append):
                r = market_buyer.warranty_said_row(md, order, {}, 0.03, shop, quotes, {"price": 1}, None)
            self.assertEqual(r["note_error"], "IsADirectoryError")
            self.assertTrue(any("stop note failed" in s for s in lines), lines)
            with mock.patch.object(ws, "measure", side_effect=KeyError("k")), \
                    mock.patch.object(market_buyer, "log", side_effect=lines.append):
                self.assertIsNone(market_buyer.warranty_said_row(md, order, {}, 0.03, shop, quotes, None, None))
            self.assertIsNone(market_buyer.warranty_said_row(md, {"_sw": order_sw("")}, {}, 0.03, shop, quotes,
                                                             None, None))
        finally:
            for n in os.listdir(md):
                os.unlink(os.path.join(md, n))
            os.rmdir(md)

    def test_cap0_is_arm_a_short_cap(self):
        o = {"list_price": 921600, "sku": "G85a1a856"}
        q = quote(THISJONGHOON)
        b = {"_sw": order_sw("B")}
        self.assertIsNone(market_buyer.short_cap(o, 0.03, b, "thisjonghoon", q))
        a_cap = market_buyer.short_cap(o, 0.03, {**b, "_sw": {**b["_sw"], "said": None}}, "thisjonghoon", q)
        self.assertEqual(a_cap, market_buyer.short_cap(o, 0.03, {"_sw": order_sw("A")}, "thisjonghoon", q))


if __name__ == "__main__":
    unittest.main()
