import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import buyer_short_warranty as sw
import hard_limits
import market_buyer


class FormulaTest(unittest.TestCase):
    def test_six_hours_is_the_saved_cap_already_in_use(self):
        for b in (0.0, 0.03, 0.072, 0.0837, 0.2):
            for S in (0.25, 0.2955, 0.30):
                self.assertAlmostEqual(sw.p_max(6, b, S), 1 - S / (1 - b), places=12)
                self.assertEqual(sw.cap(10000, 6, b, S), int(10000 * (1 - S / (1 - b))))
        self.assertEqual(sw.cap(10000, 6, 0.072, market_buyer.SAVED_MIN_SHARE),
                         market_buyer.saved_cap({"list_price": 10000}, 0.072))

    def test_the_owners_example(self):
        b, S = 0.072, 0.2955
        self.assertAlmostEqual(sw.p_max(6, b, S), 0.6816, places=4)
        self.assertAlmostEqual(sw.p_max(3, b, S), 0.6428, places=4)
        self.assertAlmostEqual(sw.p_max(0, b, S), 0.6040, places=4)

    def test_a_break_outside_the_warranty_counts_minus_one(self):
        # agent.md 229: a deal that served nothing counts -1 (0 if refunded), so E = (1 - b)(1 - p) - q, not - q p
        for p in (0.3, 0.6, 0.9):
            self.assertAlmostEqual(sw.expected(p, 0, 0.1), 0.9 * (1 - p) - 0.1, places=12)
            self.assertAlmostEqual(sw.expected(p, 3, 0.1), 0.9 * (1 - p) - 0.05, places=12)
            self.assertAlmostEqual(sw.expected(p, 6, 0.1), 0.9 * (1 - p), places=12)
        self.assertEqual(sw.p_max(0, 1.0, 0.3), 0.0)                # 1 - b <= 0: nothing
        self.assertEqual(sw.p_max(6, 1.0, 0.0), 0.0)
        self.assertEqual(sw.p_max(0, 0.4, 0.3), 0.0)                # 1 - (.3 + .4) / .6 < 0: nothing

    def test_expected_value_at_p_max_is_S(self):
        for h in (0, 1, 2.5, 4, 6):
            pm = sw.p_max(h, 0.08, 0.29)
            self.assertAlmostEqual(sw.expected(pm, h, 0.08), 0.29, places=12)
            self.assertLess(sw.expected(pm + 0.001, h, 0.08), 0.29)

    def test_shorter_is_never_dearer_and_nothing_left_is_zero(self):
        self.assertTrue(sw.p_max(0, .1, .3) < sw.p_max(3, .1, .3) < sw.p_max(6, .1, .3) == sw.p_max(72, .1, .3))
        self.assertEqual(sw.p_max(0, 0.75, 0.3), 0.0)
        self.assertEqual(sw.cap(0, 0, .1, .3), 0)
        self.assertAlmostEqual(sw.p_max(0, None, .3), sw.p_max(0, sw.FALLBACK_BREAK, .3))


class SwitchAndBoardTest(unittest.TestCase):
    def test_off_unless_the_file_says_enabled_true(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sw.config(d, env={}))
            self.assertIsNone(sw.state(d, {"kind": "goods"}, env={}))
            for body in ('{"enabled": false}', '{"enabled": "true"}', "not json", "[]"):
                with open(os.path.join(d, sw.SWITCH), "w") as f:
                    f.write(body)
                self.assertIsNone(sw.config(d, env={}))
            with open(os.path.join(d, sw.SWITCH), "w") as f:
                f.write('{"enabled": true, "cash_buffer": 34000000}')
            self.assertIsNotNone(sw.config(d, env={}))
            self.assertIsNone(sw.config(d, env={"BUYER_SHORT_WARRANTY": "0"}))
            self.assertIsNone(sw.state(d, {"kind": "food"}, env={}))
            st = sw.state(d, {"kind": "goods"}, env={})
            self.assertEqual((st["S"], st["buffer"], st["hist"]), (sw.FALLBACK_SAVED, 34000000, {}))
            with open(os.path.join(d, sw.SWITCH), "w") as f:
                f.write('{"enabled": true}')
            self.assertEqual(sw.state(d, {"kind": "goods"}, env={})["buffer"], hard_limits.STOP_BUYING_BELOW)

    def test_board_saved_fresh_else_fallback(self):
        now = 1794125058.0
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(sw.board_saved(d, now), sw.FALLBACK_SAVED)
            with open(os.path.join(d, "board_history.jsonl"), "w") as f:
                f.write(json.dumps({"ts": now - 7200, "buyers": {"Nocha12": [0.25, 1, 1]}}) + "\n")
                f.write(json.dumps({"ts": now - 60, "buyers": {"nocha12": [0.2958, 0.889, 0.801], "x": [0.4]}}) + "\n")
            self.assertEqual(sw.board_saved(d, now), 0.2958)
            self.assertEqual(sw.board_saved(d, now + sw.BOARD_MAX_AGE), sw.FALLBACK_SAVED)     # stale


class ReceiptCheckTest(unittest.TestCase):
    """The p_max and exposure checks at 0 h, with block_zero_nonfood off (its rollback): on, a 0 h receipt is refused
    before them (test_buyer_zero_block)."""
    def st(self, buffer=34000000):
        return {"S": 0.2955, "buffer": buffer, "hist": {}, "block_zero": False}

    def test_within_p_max_is_paid_over_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            ok, rec, why = sw.receipt_check(self.st(), d, {"price": 6039, "warranty_hours": 0}, 10000, 0.072)
            self.assertTrue(ok, why)
            self.assertEqual((rec["h"], rec["b"], rec["S"], rec["p_max"], rec["p"]), (0, 0.072, 0.2955, 0.604, 0.6039))
            ok, rec, why = sw.receipt_check(self.st(), d, {"price": 6041, "warranty_hours": 0}, 10000, 0.072)
            self.assertFalse(ok)
            self.assertIn("over p_max", why)
            ok, _, _ = sw.receipt_check(self.st(), d, {"price": 6400, "warranty_hours": 3}, 10000, 0.072)
            self.assertTrue(ok)
            ok, _, _ = sw.receipt_check(self.st(), d, {"price": 6450, "warranty_hours": 3}, 10000, 0.072)
            self.assertFalse(ok)                                   # p_max(3) 64.28%
            ok, _, _ = sw.receipt_check(self.st(), d, {"price": 6400, "warranty_hours": None}, 10000, 0.072)
            self.assertFalse(ok)                                   # no warranty named: 0 h
            ok, rec, _ = sw.receipt_check(self.st(), d, {"price": 6000}, 10000, None)
            self.assertEqual(rec["b"], sw.FALLBACK_BREAK)          # no break chance known: the p90
            ok, rec, _ = sw.receipt_check(self.st(), d, {"price": 6000, "warranty_hours": True}, 10000, 0.072)
            self.assertEqual(rec["h"], 0)                            # a bool is not hours

    def test_the_hours_exposure_caps_it_and_is_reserved(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, sw.EXPOSURE_FILE)
            with open(path, "w") as f:
                for ts, loss in ((now - 100, 15000), (now - 4000, 15000)):     # the second is over an hour old
                    f.write(json.dumps({"ts": ts, "loss": loss}) + "\n")
            # buffer 1,000,000: room 20,000; 15,000 used + 0.072 x 6,000 = 432 -> fits and is reserved
            ok, rec, _ = sw.receipt_check(self.st(1000000), d, {"price": 6000, "warranty_hours": 0}, 10000, 0.072, "o-1", now)
            self.assertTrue(ok)
            self.assertEqual(rec["exposure"], 15000)
            with open(path) as f:
                self.assertEqual(json.loads(f.readlines()[-1])["order"], "o-1")
            # 15,432 used + 0.072 x 100,000 = 7,200 does not fit, and is not reserved
            ok, rec, why = sw.receipt_check(self.st(1000000), d, {"price": 100000, "warranty_hours": 0}, 200000, 0.072, "o-2", now)
            self.assertFalse(ok)
            self.assertIn("expected loss", why)
            with open(path) as f:
                self.assertEqual(len(f.readlines()), 3)

    def test_an_unreadable_exposure_pays_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, sw.EXPOSURE_FILE), "w") as f:
                f.write("broken\n")
            ok, _, why = sw.receipt_check(self.st(), d, {"price": 6000, "warranty_hours": 0}, 10000, 0.072)
            self.assertFalse(ok)
            self.assertIn("exposure unknown", why)
            ok, _, why = sw.receipt_check(self.st(), os.path.join(d, "missing-dir"), {"price": 6000}, 10000, 0.072)
            self.assertFalse(ok)

    def test_parallel_buyers_cannot_both_take_the_last_room(self):
        import threading
        with tempfile.TemporaryDirectory() as d:
            st = self.st(1000000)                       # room 20,000; each 0 h unit at 100,000 x .1 = 10,000... x2 fits,
            res = []                                    # a third does not, however the threads interleave
            ts = [threading.Thread(target=lambda: res.append(
                sw.receipt_check(st, d, {"price": 100000, "warranty_hours": 0}, 200000, 0.1)[0])) for _ in range(5)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(sorted(res), [False, False, False, True, True])

    def test_only_grades_that_keep_S_are_accepted(self):
        # 11/06 grade rates: A 3%, B 12.2%, C 34.4%; at h 0, S .2955: p_max A .6644, B .5245, C .0251
        rates = {"A": 0.03, "B": 0.122, "C": 0.344}
        self.assertEqual(sw.grades_ok(6500, 10000, 0, 0.2955, rates), (["A"], 0.03))
        self.assertEqual(sw.grades_ok(5200, 10000, 0, 0.2955, rates), (["A", "B"], 0.122))
        self.assertEqual(sw.grades_ok(3000, 10000, 0, 0.2955, rates), (["A", "B"], 0.122))
        self.assertEqual(sw.grades_ok(200, 10000, 0, 0.2955, rates), (["A", "B", "C"], 0.344))
        self.assertEqual(sw.grades_ok(6800, 10000, 0, 0.2955, rates), ([], None))
        self.assertEqual(sw.grades_ok(5800, 10000, 0, 0.2955, {"A": None, "B": 1.0}), (["A"], sw.FALLBACK_BREAK))
        self.assertEqual(sw.grades_ok(6000, None, 0, 0.2955, rates), ([], None))

    def test_a_switch_cash_buffer_is_capped(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, sw.SWITCH), "w") as f:
                f.write('{"enabled": true, "cash_buffer": 1e12}')
            self.assertEqual(sw.state(d, {"kind": "goods"}, env={})["buffer"], sw.BUFFER_MAX)

    def test_b_of_one_or_worse_buys_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            for b in (1.0, 1.5):
                ok, _, _ = sw.receipt_check(self.st(), d, {"price": 100, "warranty_hours": 0}, 10000, b)
                self.assertFalse(ok)
        self.assertEqual(sw.worst([10000, 7000, None, 0], [0.05, None, 1.2]), (7000, 1.0))
        self.assertEqual(sw.worst([], []), (None, sw.FALLBACK_BREAK))


class BuyerHooksTest(unittest.TestCase):
    MEM = {"trades": [{"shop": "s", "item": "G1", "result": "no_warranty",
                       "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 60))}]}
    CAT = {"G1": {"kind": "goods", "list_price": 10000}}

    def test_skip_off_is_the_old_skip_on_keeps_the_shop_at_0h(self):
        shops = [{"name": "s"}, {"name": "t"}]
        off = {"kind": "goods"}
        left, brief = market_buyer.skip_short_warranty(shops, self.MEM, self.CAT, off)
        self.assertEqual(([s["name"] for s in left], brief), (["t"], ["s"]))
        self.assertEqual(off, {"kind": "goods"})
        on = {"kind": "goods", "_sw": {"S": .3, "buffer": 1e6, "hist": {}}}
        left, brief = market_buyer.skip_short_warranty(shops, self.MEM, self.CAT, on)
        self.assertEqual(([s["name"] for s in left], brief), (["s", "t"], []))
        self.assertEqual(sw.hours_for(on["_sw"], "s"), 0)
        self.assertIsNone(sw.hours_for(on["_sw"], "t"))

    def test_text_refusals_off_unchanged_on_priced(self):
        off, on = {"kind": "goods"}, {"kind": "goods", "_sw": {"S": .3, "buffer": 1e6, "hist": {}, "block_zero": False}}
        blocked = {"kind": "goods", "_sw": {"S": .3, "buffer": 1e6, "hist": {}}}     # block_zero_nonfood on (default)
        for text, h in (("Warranty: 0 hours. Price 9000", 0), ("three-hour full-refund warranty, 9000", 3),
                        ("sold without a warranty", 0), ("no warranty on this unit", 0)):
            self.assertIsNotNone(market_buyer.quote_refused(text, off) or market_buyer.goods_text_refused(text, off), text)
            self.assertIsNone(market_buyer.quote_refused(text, on), text)
            self.assertIsNone(market_buyer.goods_text_refused(text, on), text)
            self.assertEqual(market_buyer.warranty_hours_said(text), h, text)
            # on: none / 0 h ends the talk, a short warranty is still priced
            self.assertEqual(market_buyer.goods_text_refused(text, blocked) is not None, h == 0, text)
        self.assertIsNone(market_buyer.warranty_hours_said("6-hour full-refund warranty, 9000"))
        self.assertIsNone(market_buyer.warranty_hours_said("72 h warranty"))
        self.assertIsNone(market_buyer.warranty_hours_said("price 9000"))

    @mock.patch.dict(market_buyer._SW_CTX, clear=True)       # no run context: the cap of the quoted SKU's own list
    def test_quote_ranking_uses_the_short_cap(self):
        o = {"sku": "G1", "list_price": 10000}
        on = {"kind": "goods", "_sw": {"S": .3, "buffer": 1e6, "hist": {}}}
        self.assertIsNone(market_buyer.short_cap(o, .072, {"kind": "goods"}, "s", {"warranty_h": 0}))
        self.assertIsNone(market_buyer.short_cap(o, .072, on, "s", {"warranty_h": None}))
        rb = {"kind": "goods", "_sw": dict(on["_sw"], block_zero=False)}       # the rollback: 0 h priced at p_max(0)
        self.assertEqual(market_buyer.short_cap(o, .072, rb, "s", {"warranty_h": 0}), sw.cap(10000, 0, .072, .3))
        self.assertEqual(market_buyer.short_cap(o, .072, on, "s", {"warranty_h": 0}), 0)    # on: none, never bought
        self.assertEqual(market_buyer.short_cap(o, .072, on, "s", {"warranty_h": 3}), sw.cap(10000, 3, .072, .3))
        shops = [{"name": "s", "_offer": o, "_b": .072}] * 3
        got = {"s": {"ask": 6300, "warranty_h": 0}}
        self.assertTrue(market_buyer.quotes_enough(shops, got, 20000, {}))           # off: 6,300 under the .33 six-hour cap, above short-cap 5,991
        self.assertFalse(market_buyer.quotes_enough(shops, got, 20000, {}, rb))      # rollback: over p_max(0) = 5,991
        self.assertFalse(market_buyer.quotes_enough(shops, got, 20000, {}, on))      # on: a 0 h quote never fits


class MainTest(unittest.TestCase):
    """market_buyer.main end to end with a 0 h goods receipt: off withholds it as before, on with block_zero_nonfood false
    (its rollback) pays it at p_max(0); on by default refuses it (test_buyer_zero_block)."""
    RB = {"block_zero_nonfood": False}
    CATALOG = {"G1": {"kind": "goods", "category": "가전>컵", "list_price": 10000}}
    ORDER = {"order": "o-1", "item": "컵", "budget": 16000, "acceptable": ["G1"], "kind": "goods", "category": "가전>컵"}
    CFG = {}

    def run_main(self, switch, price, warranty, setup=None):
        printed, calls = [], []
        with tempfile.TemporaryDirectory() as d:
            sp, op = os.path.join(d, "shops.json"), os.path.join(d, "order.json")
            with open(sp, "w") as f:
                json.dump([{"name": "s", "url": "https://s.example", "reviews": [],
                            "offers": [{"sku": "G1", "name": "G1", "qty": 1}]}], f)
            with open(op, "w") as f:
                json.dump(self.ORDER, f)
            with open(os.path.join(d, "catalog.json"), "w") as f:
                json.dump(self.CATALOG, f)
            if switch is not None:
                with open(os.path.join(d, sw.SWITCH), "w") as f:
                    json.dump(switch, f)
            if setup:
                setup(d)
            rec = {"item": "G1", "price": price, "task": "t", "sig_seller": "x"}
            if warranty is not None:
                rec["warranty_hours"] = warranty
            result = {"state": "TASK_STATE_COMPLETED", "task": "t", "history": [("shop asked", price)], "agreed": price,
                      "holds": 0, "receipt": rec}

            def neg(*a, **k):
                calls.append((a, {n: k.get(n) for n in ("ceiling",)}))
                return dict(result)

            with mock.patch.object(market_buyer, "MEMORY", os.path.join(d, "m.json")), \
                    mock.patch.object(market_buyer, "SETTLED", os.path.join(d, "settled.jsonl")), \
                    mock.patch.object(market_buyer, "market_round", lambda: 3), \
                    mock.patch.object(market_buyer, "serves_acp", lambda u: True), \
                    mock.patch.object(market_buyer, "buyer_cfg", lambda: dict(self.CFG)), \
                    mock.patch.object(market_buyer, "choose", lambda s, m, i, b: (s[0], "t")), \
                    mock.patch.object(market_buyer, "negotiate", neg), \
                    mock.patch.dict(os.environ, {"BUYER_SHORT_WARRANTY": "", "BUYER_ACCEPT_BAND": ""}), \
                    mock.patch("builtins.print", lambda *a, **k: printed.append(a[0])), \
                    mock.patch.object(sys, "argv", ["x", "--shops", sp, "--item", "컵", "--budget", str(self.ORDER["budget"]),
                                                          "--order", op]):
                market_buyer.main()
            with open(os.path.join(d, "measure", "orders.jsonl"), encoding="utf-8") as f:
                orders = [json.loads(x) for x in f]
        return json.loads(printed[-1]), calls, orders[-1]

    def strip(self, o):
        return {k: v for k, v in o.items() if k not in ("ts", "seconds", "took")}

    def test_off_is_the_old_behaviour_exactly(self):
        for warranty, price in ((0, 6000), (None, 6000), (6, 6500), (72, 6900)):
            base = self.run_main(None, price, warranty)
            for switch in ({"enabled": False}, {"enabled": "yes"}):
                got = self.run_main(switch, price, warranty)
                self.assertEqual(got[0], base[0])
                self.assertEqual(got[1], base[1])
                self.assertEqual(self.strip(got[2]), self.strip(base[2]))
            self.assertNotIn("short_warranty_buy", base[2])
            self.assertNotIn("short_warranty", base[2])
        out, calls, o = self.run_main(None, 6000, 0)
        self.assertIsNone(out["receipt"])                            # 0 h: not paid, as before
        self.assertEqual(out["accept"], {"warranty_hours": 6})
        self.assertIn("warranty 0 h", " ".join(o["warnings"]))

    def test_on_pays_a_0h_receipt_within_p_max_and_tells_the_kit(self):
        out, calls, o = self.run_main({"enabled": True, "cash_buffer": 34000000, **self.RB}, 5800, 0)
        self.assertIsNotNone(out["receipt"])
        self.assertEqual(out["accept"], {"warranty_hours": 0, "grades": ["A", "B", "C"]})   # no ledger: b = p90 for all
        s = o["short_warranty_buy"]
        self.assertTrue(s["ok"])
        self.assertEqual((s["h"], s["S"], s["p"]), (0, 0.3, 0.58))
        self.assertAlmostEqual(s["p_max"], sw.p_max(0, s["b"], 0.3), places=4)
        self.assertTrue(o["passed_to_kit"])

    def test_on_prices_at_the_cheapest_acceptable_sku(self):
        # G2 (list 7,000) is acceptable too: the kit may ship it at the agreed 6,000, 85.7% of its list
        self.CATALOG = dict(self.CATALOG, G2={"kind": "goods", "category": "가전>컵", "list_price": 7000})
        self.ORDER = dict(self.ORDER, acceptable=["G1", "G2"])
        out, calls, o = self.run_main({"enabled": True, **self.RB}, 6000, 0)
        self.assertIsNone(out["receipt"])
        self.assertEqual((o["short_warranty_buy"]["list"], o["short_warranty_buy"]["ok"]), (7000, False))
        out, calls, o = self.run_main({"enabled": True, **self.RB}, 4000, 0)      # 57.1% of 7,000: under p_max(0) 58.1% at b .0837
        self.assertIsNotNone(out["receipt"])

    def test_on_refuses_a_0h_receipt_over_p_max(self):
        out, calls, o = self.run_main({"enabled": True, **self.RB}, 6900, 0)     # 69% of list: under the 6 h cap 70%, over p_max(0)
        self.assertIsNone(out["receipt"])
        self.assertEqual(out["accept"], {"warranty_hours": 6})
        self.assertFalse(o["short_warranty_buy"]["ok"])
        self.assertIn("over p_max", " ".join(o["warnings"]))

    def test_on_6h_receipt_is_the_usual_deal(self):
        off = self.run_main(None, 6900, 6)
        on = self.run_main({"enabled": True}, 6900, 6)
        self.assertEqual(on[0], off[0])
        self.assertEqual(on[1], off[1])
        self.assertNotIn("short_warranty_buy", on[2])

    def test_on_a_shop_known_short_is_talked_to_at_p_max_0_without_band(self):
        mem = {"trades": [{"shop": "s", "item": "G1", "result": "no_warranty",
                           "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 60))}]}

        def setup(d):
            with open(os.path.join(d, "m.json"), "w") as f:
                json.dump(mem, f)
            with open(os.path.join(d, "buyer_accept_band.json"), "w") as f:
                json.dump({"enabled": True, **self.RB}, f)
        on = self.run_main({"enabled": True, **self.RB}, 6000, 0, setup)
        limit = on[1][0][0][2]
        self.assertEqual(limit, sw.cap(10000, 0, on[2]["short_warranty_buy"]["b"], 0.3))
        self.assertIsNone(on[1][0][1]["ceiling"])                    # no 105% band over p_max(h)
        self.assertEqual(on[2]["short_warranty"]["h_est"], 0)
        off = self.run_main(None, 6000, 0, setup)                    # off: the only shop is skipped-but-kept as before
        self.assertEqual(off[1][0][0][2], 6799)

    def test_on_p_max_never_passes_value_limit_nor_the_budget(self):
        # p_max(0) is about 5,812 of 10,000: max_price_share 0.5 and a budget of 4,000 each hold it lower (11/08 18:12)
        mem = {"trades": [{"shop": "s", "item": "G1", "result": "no_warranty",
                           "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 60))}]}

        def setup(d):
            with open(os.path.join(d, "m.json"), "w") as f:
                json.dump(mem, f)
        self.CFG = {"max_price_share": 0.5}
        on = self.run_main({"enabled": True, **self.RB}, 5000, 0, setup)
        self.assertEqual(on[1][0][0][2], 5000)
        self.CFG = {}
        self.ORDER = dict(self.ORDER, budget=4000)
        on = self.run_main({"enabled": True, **self.RB}, 4000, 0, setup)
        self.assertEqual(on[1][0][0][2], 4000)


if __name__ == "__main__":
    unittest.main()
