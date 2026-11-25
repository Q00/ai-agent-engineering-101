"""No goods are bought without a warranty, with buyer_short_warranty on too (fixed rule: 무보증 비식품 구매 금지; decision
ax-0h-nonfood-block, 11/09 22:44 default). Before this, the switch priced a goods unit expected with no warranty at
p_max(0 h) and paid a 0 h (or unnamed) receipt under it: 9 such receipts were passed to the kit 11/08-11/09 12:09
(orders.jsonl short_warranty_buy h 0 ok true). The p_max(h) formula itself is not touched: the block stands before it.
block_zero_nonfood in the switch file (default true) is the one rollback flag: false is the 11/08 behaviour exactly."""
import json
import math
import os
import tempfile
import time
import unittest
from unittest import mock

import buyer_short_warranty as sw
import market_buyer
import test_buyer_short_warranty as base


def st(**kw):
    return {"S": 0.2955, "buffer": 34000000, "hist": {}, **kw}


class SwitchFlagTest(unittest.TestCase):
    def state(self, switch):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, sw.SWITCH), "w") as f:
                json.dump(switch, f)
            return sw.state(d, {"kind": "goods"}, env={})

    def test_on_unless_the_switch_says_false(self):
        self.assertTrue(self.state({"enabled": True})["block_zero"])                 # missing: on
        self.assertTrue(self.state({"enabled": True, "block_zero_nonfood": True})["block_zero"])
        self.assertTrue(self.state({"enabled": True, "block_zero_nonfood": "false"})["block_zero"])   # not JSON false
        self.assertTrue(self.state({"enabled": True, "block_zero_nonfood": 0})["block_zero"])
        self.assertFalse(self.state({"enabled": True, "block_zero_nonfood": False})["block_zero"])

    def test_block_zero_reads_fail_closed(self):
        self.assertTrue(sw.block_zero(st()))                     # a state built without the key: on
        self.assertTrue(sw.block_zero(st(block_zero=None)))
        self.assertFalse(sw.block_zero(st(block_zero=False)))
        self.assertFalse(sw.block_zero(None))                    # switch off: nothing to block, the 6 h rule holds

    def test_food_has_no_state(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, sw.SWITCH), "w") as f:
                json.dump({"enabled": True}, f)
            self.assertIsNone(sw.state(d, {"kind": "food"}, env={}))


class FormulaKeptTest(unittest.TestCase):
    def test_p_max_is_the_11_08_formula(self):
        # the owner's example (b .072, S .2955): 6 h 68.16%, 3 h 64.28%, 0 h 60.40%; a break outside counts -1
        for h, want in ((6, .6816), (3, .6428), (0, .6040)):
            self.assertAlmostEqual(sw.p_max(h, .072, .2955), want, places=4)
        self.assertAlmostEqual(sw.expected(.604, 0, .072), .2955, places=3)


class ReceiptGateTest(unittest.TestCase):
    def test_a_receipt_without_warranty_hours_is_refused_before_pricing_and_reserve(self):
        for w in (0, 0.0, -0.0, None, -1, -0.5, float("nan"), float("inf"), float("-inf"), True, "3", "6 h"):
            with tempfile.TemporaryDirectory() as d:
                rec = {"price": 1000, "warranty_hours": w}
                with mock.patch.object(sw, "reserve", side_effect=AssertionError("reserved")), \
                        mock.patch.object(sw, "p_max", side_effect=AssertionError("priced")):
                    ok, note, why = sw.receipt_check(st(), d, rec, 10000, 0.072, "o-1")
                self.assertFalse(ok, w)
                self.assertEqual(note, {"h": 0, "ok": False, "blocked": "no_warranty"}, w)
                self.assertIn("no warranty", why)
                self.assertEqual(os.listdir(d), [], w)               # no exposure line, no lock file
        with tempfile.TemporaryDirectory() as d:                     # the key missing from the receipt
            ok, note, _ = sw.receipt_check(st(), d, {"price": 1000}, 10000, 0.072)
            self.assertEqual((ok, note["blocked"]), (False, "no_warranty"))

    def test_a_positive_short_warranty_is_priced_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            ok, note, why = sw.receipt_check(st(), d, {"price": 6400, "warranty_hours": 3}, 10000, 0.072)
            self.assertTrue(ok, why)
            self.assertNotIn("blocked", note)
            ok, _, why = sw.receipt_check(st(), d, {"price": 6450, "warranty_hours": 3}, 10000, 0.072)
            self.assertFalse(ok)
            self.assertIn("over p_max", why)
            ok, note, why = sw.receipt_check(st(), d, {"price": 3000, "warranty_hours": 1}, 10000, 0.072)
            self.assertTrue(ok, why)
            self.assertEqual(note["h"], 1)

    def test_a_small_positive_warranty_is_priced_exactly_as_before(self):
        # 0 < h < 6, however small, is a short warranty: no new least warranty (only none is blocked)
        for w, price in ((1e-9, 6000), (0.01, 6000), (0.5, 6000), (0.5, 6100), (0.99, 6100), (0.5, 9000)):
            got = []
            for state in (st(), st(block_zero=False)):
                with tempfile.TemporaryDirectory() as d:
                    with mock.patch.object(sw, "p_max", wraps=sw.p_max) as pm:
                        ok, note, why = sw.receipt_check(state, d, {"price": price, "warranty_hours": w}, 10000, 0.072,
                                                         "o-1", now=1000.0)
                    self.assertTrue(pm.called, w)                       # priced
                    got.append((ok, note, why, sorted(os.listdir(d))))
            self.assertEqual(got[0], got[1], w)
            self.assertNotIn("blocked", got[0][1] or {}, w)
            self.assertEqual(got[0][1]["h"], w)

    def test_rollback_false_is_the_old_0h_check(self):
        with tempfile.TemporaryDirectory() as d:
            ok, note, why = sw.receipt_check(st(block_zero=False), d, {"price": 6039, "warranty_hours": 0}, 10000, 0.072)
            self.assertTrue(ok, why)
            self.assertEqual((note["h"], note["p_max"]), (0, 0.604))


class ZeroExpectedTest(unittest.TestCase):
    def test_what_blocks_before_pricing(self):
        s = st(hist={"z": 0})
        self.assertTrue(sw.zero_expected(s, "a", {"warranty_h": 0}))                      # the quote says none / 0 h
        self.assertTrue(sw.zero_expected(s, "z", {"warranty_h": None}))                   # 0 h history, quote silent
        self.assertTrue(sw.zero_expected(s, "z", None))                                   # 0 h history, no quote
        self.assertTrue(sw.zero_expected(s, "z", {"warranty_h": None, "warranty_said_ok": False}))

    def test_only_none_is_no_warranty_a_small_positive_is_one(self):
        s = st(hist={"z": 0}, zero_hist=["z"])
        for w in (0, 0.0, -0.0, -1, -0.5):
            self.assertEqual(sw.zero_expected(s, "a", {"warranty_h": w}), sw.ZERO_QUOTE, w)
        for w in (1e-9, 0.01, 0.5, 0.99, 1, 3):
            self.assertIsNone(sw.zero_expected(s, "a", {"warranty_h": w}), w)
            self.assertIsNone(sw.zero_expected(s, "z", {"warranty_h": w}), w)       # its own words over the history
        for w in (None, 0, -0.0, -2, True, "2", float("nan"), float("inf"), float("-inf")):
            self.assertTrue(sw.no_warranty(w), w)
        for w in (1e-9, 0.01, 0.5, 0.999, 1, 3.0, 5.99, 6, 72):
            self.assertFalse(sw.no_warranty(w), w)

    def test_the_history_blocks_only_shops_whose_short_receipts_had_none(self):
        self.assertEqual(sw.zero_expected(st(hist={"z": 0}, zero_hist=["z"]), "z", None), sw.ZERO_HIST)
        self.assertIsNone(sw.zero_expected(st(hist={"z": 0}, zero_hist=[]), "z", None))     # its named hours over 0
        self.assertIsNone(sw.zero_expected(st(hist={"z": 0}, zero_hist=[]), "z", {"warranty_h": None}))
        self.assertEqual(sw.zero_expected(st(hist={"z": 0}, zero_hist="z"), "z", None), sw.ZERO_HIST)   # not a list

    def test_what_does_not(self):
        s = st(hist={"z": 0})
        self.assertIsNone(sw.zero_expected(s, "a", {"warranty_h": None}))                 # no history, quote silent
        self.assertIsNone(sw.zero_expected(s, "a", None))
        self.assertIsNone(sw.zero_expected(s, "a", {"warranty_h": 3}))                    # a short warranty: priced
        self.assertIsNone(sw.zero_expected(s, "z", {"warranty_h": 3}))                    # its own words over the history
        # BW1 (any arm, or off): a quote naming only 6 h or more is not blocked by the 0 h history
        for said in (None, {"arm": "A"}, {"arm": "B"}):
            t = dict(s, said=said)
            self.assertIsNone(sw.zero_expected(t, "z", {"warranty_h": None, "warranty_said_ok": True}), said)
        self.assertIsNone(sw.zero_expected(st(hist={"z": 0}, block_zero=False), "z", {"warranty_h": 0}))
        self.assertIsNone(sw.zero_expected(None, "z", {"warranty_h": 0}))

    def test_hours_for_is_untouched(self):
        s = st(hist={"z": 0})
        self.assertEqual(sw.hours_for(s, "z", {"warranty_h": 3}), 0)      # the least, as before (the cap stays p_max(0))
        self.assertEqual(sw.hours_for(s, "a", {"warranty_h": 3}), 3)
        self.assertIsNone(sw.hours_for(s, "a", {"warranty_h": None}))


class HugeIntTest(unittest.TestCase):
    def test_an_int_past_float_range_is_not_a_number_and_does_not_raise(self):
        for v in (10 ** 400, -10 ** 400, json.loads("1" + "0" * 400)):
            self.assertFalse(sw.number(v))
            self.assertTrue(sw.no_warranty(v))
        for v in (6, 72, 10 ** 300, 0.5):
            self.assertTrue(sw.number(v))
        for v in (0, -1, None, True, float("inf"), float("nan")):
            self.assertTrue(sw.no_warranty(v))


class WarrantyShortTest(unittest.TestCase):
    G = {"kind": "goods"}

    def test_not_a_finite_number_is_no_warranty(self):
        for w in (float("inf"), json.loads("1e309"), float("-inf"), float("nan"), None, "72", True):
            self.assertEqual(market_buyer.warranty_short({"warranty_hours": w}, self.G),
                             "warranty 0 h, I buy goods only with 6 h", w)

    def test_finite_hours_as_before(self):
        for w in (6, 6.0, 72, 73, 1e300, json.loads("1" + "0" * 400)):     # an int past float range: no OverflowError
            self.assertIsNone(market_buyer.warranty_short({"warranty_hours": w}, self.G), w)
        self.assertEqual(market_buyer.warranty_short({"warranty_hours": False}, self.G), "warranty 0 h, I buy goods only with 6 h")
        self.assertEqual(market_buyer.warranty_short({"warranty_hours": 5}, self.G), "warranty 5 h, I buy goods only with 6 h")
        self.assertEqual(market_buyer.warranty_short({"warranty_hours": 0.5}, self.G), "warranty 0.5 h, I buy goods only with 6 h")
        self.assertIsNone(market_buyer.warranty_short({"warranty_hours": float("inf")}, {"kind": "food"}))


class ZeroWarrantyShopsTest(unittest.TestCase):
    CAT = {"G1": {"kind": "goods"}, "F1": {"kind": "food"}}

    def trade(self, shop, warning, item="G1", ago=60, **kw):
        return {"shop": shop, "item": item, "result": "no_warranty", "warnings": [warning] if warning else [],
                "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - ago)), **kw}

    def test_read_from_the_warranty_warning(self):
        # the warning as market_buyer.warranty_short writes it (memory/market_memory_r3.json, 11/09)
        mem = {"trades": [
            self.trade("a", "warranty 0 h, I buy goods only with 6 h; warranty 0 h: price 64.8% of list is over p_max 0.0%"),
            self.trade("b", "warranty 3.0 h, I buy goods only with 6 h"),
            self.trade("c", "warranty 3.0 h, I buy goods only with 6 h"),
            self.trade("c", "warranty 0.5 h, I buy goods only with 6 h"),       # a warranty, however short
            self.trade("i", "warranty 0.0 h, I buy goods only with 6 h"),
            self.trade("j", "warranty -1 h, I buy goods only with 6 h"),
            self.trade("d", None),                                          # no hours said: fails closed
            self.trade("e", "warranty True h, I buy goods only with 6 h"),
            self.trade("f", "warranty 0 h, I buy goods only with 6 h", ago=4 * 3600),   # older than the window
            self.trade("g", "warranty 0 h, I buy goods only with 6 h", item="F1"),       # food
            self.trade("h", "warranty 0 h, I buy goods only with 6 h", warranty_said={"arm": "B", "stop_at": time.time() + 99}),
        ]}
        names = set("abcdefghijz")
        self.assertEqual(market_buyer.zero_warranty_shops(mem, self.CAT, names), {"a", "d", "e", "i", "j"})
        self.assertEqual(market_buyer.zero_warranty_shops(mem, self.CAT, {"b"}), set())

    def test_skip_short_warranty_sets_it(self):
        mem = {"trades": [self.trade("a", "warranty 0 h, I buy goods only with 6 h"),
                          self.trade("b", "warranty 3.0 h, I buy goods only with 6 h")]}
        order = {"kind": "goods", "_sw": st()}
        left, brief = market_buyer.skip_short_warranty([{"name": "a"}, {"name": "b"}, {"name": "x"}], mem, self.CAT, order)
        self.assertEqual(([s["name"] for s in left], brief), (["a", "b", "x"], []))
        self.assertEqual((order["_sw"]["hist"], order["_sw"]["zero_hist"]), ({"a": 0, "b": 0}, ["a"]))


class QuoteAndTextTest(unittest.TestCase):
    ON = {"kind": "goods", "_sw": st(hist={"z": 0})}

    def q(self, text, order, name="a"):
        q = {"ask": 5000, "text": text, "refused": market_buyer.quote_refused(text, order)}
        market_buyer.short_warranty_quote(q, text, order, name)
        return q

    def test_a_quote_without_warranty_is_refused(self):
        for text in ("Warranty: 0 hours. Price 9000", "sold without a warranty", "no warranty on this unit, 5000"):
            q = self.q(text, self.ON)
            self.assertEqual(q["warranty_h"], 0, text)
            self.assertIn("no warranty", q["refused"], text)

    def test_a_small_positive_quote_is_kept_as_before(self):
        rb = {"kind": "goods", "_sw": st(hist={"z": 0}, block_zero=False)}
        for text in ("0.5 h warranty, 5000", "warranty: 0.5 hours, 5000"):
            for name in ("a", "z"):
                q = self.q(text, self.ON, name)
                self.assertEqual(q["warranty_h"], 0.5, text)
                self.assertIsNone(q["refused"], (text, name))
                self.assertEqual(q, self.q(text, rb, name), (text, name))
            self.assertIsNone(market_buyer.goods_text_refused(text, self.ON), text)

    def test_a_silent_quote_from_a_0h_shop_is_refused_others_kept(self):
        self.assertIn("no warranty", self.q("asking 5000", self.ON, "z")["refused"])
        self.assertIsNone(self.q("asking 5000", self.ON, "a")["refused"])
        self.assertIsNone(self.q("three-hour full-refund warranty, 5000", self.ON, "a")["refused"])
        self.assertIsNone(self.q("three-hour full-refund warranty, 5000", self.ON, "z")["refused"])

    def test_a_72h_quote_from_a_0h_shop_is_kept_in_every_arm(self):
        text = "Grade A, market certificate attached, 72 h warranty. Asking 5000."
        for said in (None, {"arm": "A"}, {"arm": "B"}):
            order = {"kind": "goods", "_sw": st(hist={"z": 0}, said=said)}
            q = self.q(text, order, "z")
            self.assertIsNone(q["refused"], said)
            self.assertTrue(q["warranty_said_ok"])

    def test_rollback_and_off_and_food_unchanged(self):
        rb = {"kind": "goods", "_sw": st(hist={"z": 0}, block_zero=False)}
        self.assertIsNone(self.q("Warranty: 0 hours. Price 9000", rb)["refused"])
        self.assertIsNone(self.q("asking 5000", rb, "z")["refused"])
        off = {"kind": "goods"}
        q = self.q("Warranty: 0 hours. Price 9000", off)
        self.assertNotIn("warranty_h", q)                                   # off: the old refusal, nothing added
        self.assertEqual(q["refused"], market_buyer.quote_refused("Warranty: 0 hours. Price 9000", off))
        food = {"kind": "food", "use_within_hours": 24}
        self.assertNotIn("warranty_h", self.q("no warranty", food))

    def test_talk_text_without_warranty_stops_the_talk(self):
        for text in ("Warranty: 0 hours. Price 9000", "sold without a warranty", "no warranty on this unit"):
            self.assertIsNotNone(market_buyer.goods_text_refused(text, self.ON), text)
            self.assertIsNone(market_buyer.goods_text_refused(text, {"kind": "goods", "_sw": st(block_zero=False)}), text)
        for text in ("three-hour full-refund warranty, 9000", "72 h warranty", "price 9000", "0.5 h warranty, 9000"):
            self.assertIsNone(market_buyer.goods_text_refused(text, self.ON), text)
        self.assertIsNone(market_buyer.goods_text_refused("no warranty", {"kind": "food"}))

    def test_quote_end_to_end(self):
        card = mock.Mock()
        card.json.return_value = {"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0",
                                                             "url": "https://z.example/rpc"}]}

        def ask(name, text):
            out = {}
            with mock.patch.object(market_buyer, "serves_acp", lambda u: True), \
                    mock.patch.object(market_buyer.httpx, "get", lambda *a, **k: card), \
                    mock.patch.object(market_buyer, "buyer_send", lambda *a, **k: {
                        "id": "t1", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}), \
                    mock.patch.object(market_buyer, "buyer_message_text", lambda t: text), \
                    mock.patch.object(market_buyer, "buyer_metadata_ask", lambda t: 5000), \
                    mock.patch.object(market_buyer, "log"):
                market_buyer.quote({"name": name, "url": f"https://{name}.example", "_offer": {"sku": "G1", "list_price": 10000}},
                                   "cup", "G1", dict(self.ON), out)
            return out[name]
        self.assertIn("no warranty", ask("z", "asking 5000")["refused"])
        self.assertIn("no warranty", ask("a", "Warranty: 0 hours. asking 5000")["refused"])
        self.assertIsNone(ask("a", "asking 5000")["refused"])
        self.assertIsNone(ask("z", "72 h warranty, grade A. asking 5000")["refused"])
        self.assertFalse(market_buyer.resumable(ask("z", "asking 5000")))      # never the talk that goes on

    def test_quote_calls_the_helper_once(self):
        import inspect
        src = inspect.getsource(market_buyer.quote)
        self.assertEqual(src.count("short_warranty_quote(q, text, order, shop[\"name\"])"), 1)
        self.assertNotIn('q["warranty_h"] = ', src)


class _Main:
    CATALOG = base.MainTest.CATALOG
    ORDER = base.MainTest.ORDER
    CFG = {}
    run_main = base.MainTest.run_main


class MainZeroTest(unittest.TestCase):
    """market_buyer.main end to end, the switch on with block_zero_nonfood as given."""
    ON = {"enabled": True, "cash_buffer": 34000000}
    ROLLBACK = {"enabled": True, "cash_buffer": 34000000, "block_zero_nonfood": False}
    MEM = {"trades": [{"shop": "s", "item": "G1", "result": "no_warranty",
                       "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 60))}]}

    def main(self, switch, price, warranty, known_short=False):
        def setup(d):
            if known_short:
                with open(os.path.join(d, "m.json"), "w") as f:
                    json.dump(self.MEM, f)
        reserved = []
        real = sw.reserve
        with mock.patch.object(sw, "reserve", lambda *a, **k: reserved.append(a) or real(*a, **k)), \
                mock.patch.dict(market_buyer._SW_CTX):        # main() sets the run's context: put back after
            out, calls, o = _Main().run_main(switch, price, warranty, setup)
        return out, calls, o, reserved

    def test_a_0h_receipt_is_not_passed_to_the_kit(self):
        for w in (0, None, -2, "0", -0.0, float("nan"), True):
            out, calls, o, reserved = self.main(self.ON, 5800, w)
            self.assertIsNone(out["receipt"], w)
            self.assertEqual(out["accept"], {"warranty_hours": 6}, w)
            self.assertEqual(o["short_warranty_buy"], {"h": 0, "ok": False, "blocked": "no_warranty"}, w)
            self.assertFalse(o["passed_to_kit"], w)
            self.assertEqual(reserved, [], w)
            self.assertIn("no warranty", " ".join(o["warnings"]))

    def test_an_infinite_receipt_is_not_passed_to_the_kit(self):
        # inf >= 6 made warranty_short say "fine": the short branch and its no_warranty block were skipped and an
        # inf-hour goods receipt was paid (json.loads takes Infinity and 1e309 as inf). Not seen in settle (max 72 h).
        for w in (float("inf"), json.loads("1e309"), json.loads("Infinity"), float("-inf")):
            out, calls, o, reserved = self.main(self.ON, 5800, w)
            self.assertIsNone(out["receipt"], w)
            self.assertEqual(out["accept"], {"warranty_hours": 6}, w)
            self.assertEqual(o["short_warranty_buy"], {"h": 0, "ok": False, "blocked": "no_warranty"}, w)
            self.assertFalse(o["passed_to_kit"], w)
            self.assertEqual(reserved, [], w)
            self.assertIn("warranty 0 h, I buy goods only with 6 h", " ".join(o["warnings"]))

    def test_a_huge_negative_int_receipt_is_blocked_not_a_crash(self):
        # -10**400 is short (warranty_short keeps ints) and then no_warranty's math.isfinite raised OverflowError outside
        # any try: main() died with no result line, no trade and no orders row
        for w in (json.loads("-1" + "0" * 400), -10 ** 309):
            out, calls, o, reserved = self.main(self.ON, 5800, w)
            self.assertIsNone(out["receipt"])
            self.assertEqual(o["short_warranty_buy"], {"h": 0, "ok": False, "blocked": "no_warranty"})
            self.assertFalse(o["passed_to_kit"])
            self.assertEqual(reserved, [])

    def test_a_huge_int_receipt_is_paid_as_before(self):
        big = json.loads("1" + "0" * 400)
        on, off = self.main(self.ON, 6500, big), self.main(None, 6500, big)
        self.assertIsNotNone(on[0]["receipt"])
        self.assertEqual((on[0], on[1]), (off[0], off[1]))

    def test_switch_off_an_infinite_receipt_is_refused_like_a_0h_one(self):
        inf, zero = self.main(None, 5800, float("inf")), self.main(None, 5800, 0)
        self.assertIsNone(inf[0]["receipt"])
        self.assertEqual((inf[0], inf[1], inf[2]["warnings"], inf[3]), (zero[0], zero[1], zero[2]["warnings"], zero[3]))

    def test_rollback_pays_a_0h_receipt_within_p_max_as_on_11_08(self):
        out, calls, o, reserved = self.main(self.ROLLBACK, 5800, 0)
        self.assertIsNotNone(out["receipt"])
        self.assertEqual(out["accept"], {"warranty_hours": 0, "grades": ["A", "B", "C"]})
        self.assertTrue(o["short_warranty_buy"]["ok"])
        self.assertEqual(len(reserved), 1)

    def test_a_small_positive_receipt_is_paid_exactly_as_before(self):
        for w in (1e-9, 0.5, 0.99):
            on, rb = self.main(self.ON, 5800, w), self.main(self.ROLLBACK, 5800, w)
            self.assertIsNotNone(on[0]["receipt"], w)
            self.assertEqual(on[0]["accept"]["warranty_hours"], w)
            self.assertEqual((on[0], on[1], on[2]["short_warranty_buy"], len(on[3])),
                             (rb[0], rb[1], rb[2]["short_warranty_buy"], len(rb[3])), w)
            self.assertNotIn("blocked", on[2]["short_warranty_buy"])

    def test_a_3h_receipt_within_p_max_is_still_paid(self):
        out, calls, o, reserved = self.main(self.ON, 6000, 3)
        self.assertIsNotNone(out["receipt"])
        self.assertEqual(out["accept"]["warranty_hours"], 3)
        self.assertTrue(o["short_warranty_buy"]["ok"])
        self.assertEqual(len(reserved), 1)

    def test_a_6h_receipt_is_the_usual_deal(self):
        on = self.main(self.ON, 6900, 6)
        off = self.main(None, 6900, 6)
        self.assertEqual(on[0], off[0])
        self.assertEqual(on[1], off[1])

    def test_a_shop_whose_short_receipts_named_hours_is_talked_to_at_p_max_0_as_before(self):
        mem = self.MEM
        self.MEM = {"trades": [dict(mem["trades"][0], warnings=["warranty 3.0 h, I buy goods only with 6 h; warranty 3.0 h: "
                                                               "price 70.0% of list is over p_max 64.3%"])]}
        try:
            out, calls, o, reserved = self.main(self.ON, 5500, 3, known_short=True)
            rb = self.main(self.ROLLBACK, 5500, 3, known_short=True)
        finally:
            self.MEM = mem
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][2], rb[1][0][0][2])               # the same p_max(0) limit as before
        self.assertEqual(o["short_warranty"]["zero_hist"], [])
        self.assertNotIn("zero", o["short_warranty"])
        self.assertIsNotNone(out["receipt"])                           # its 3 h receipt within the limit: paid
        self.assertEqual((out, o["short_warranty_buy"]), (rb[0], rb[2]["short_warranty_buy"]))   # as before

    def test_a_shop_known_0h_is_not_talked_to(self):
        out, calls, o, reserved = self.main(self.ON, 5800, 0, known_short=True)
        self.assertEqual(calls, [])                                # no talk, so no price is offered
        self.assertIsNone(out["receipt"])
        self.assertEqual(reserved, [])
        self.assertEqual(o["short_warranty"]["zero"], sw.ZERO_HIST)
        self.assertEqual(o["short_warranty"]["zero_hist"], ["s"])        # its trade names no hours: fails closed
        rb = self.main(self.ROLLBACK, 5800, 0, known_short=True)  # rollback: talked to at p_max(0) as before
        self.assertEqual(len(rb[1]), 1)
        self.assertNotIn("zero", rb[2]["short_warranty"])


class NoPriceBeforeTheGateTest(unittest.TestCase):
    """A known no-warranty pick never reaches a price: main() works out no value_limit, saved_cap, accept_percent, short
    cap or band for it, and p_max / expected / short_terms are never called (the gate stands before them)."""
    PRICING = (("market_buyer", "value_limit"), ("market_buyer", "saved_cap"), ("market_buyer", "accept_percent"),
               ("market_buyer", "short_cap"), ("market_buyer", "short_terms"), ("market_buyer.buyer_accept_band", "ceiling"),
               ("sw", "p_max"), ("sw", "expected"), ("sw", "cap"), ("sw", "grades_ok"), ("sw", "receipt_check"))

    def events(self, switch):
        ev = []
        mods = {"market_buyer": market_buyer, "market_buyer.buyer_accept_band": market_buyer.buyer_accept_band, "sw": sw}
        patches = []
        for mod, name in self.PRICING:
            real = getattr(mods[mod], name)
            patches.append(mock.patch.object(mods[mod], name,
                                             (lambda real, name: lambda *a, **k: ev.append(name) or real(*a, **k))(real, name)))
        patches.append(mock.patch.object(market_buyer, "log", lambda m: ev.append(("log", m))))
        for p in patches:
            p.start()
        try:
            out, calls, o, _ = MainZeroTest.main(MainZeroTest(), switch, 5800, 0, known_short=True)
        finally:
            for p in reversed(patches):
                p.stop()
        return ev, out, calls, o

    @staticmethod
    def at(ev, text):
        return next(i for i, e in enumerate(ev) if isinstance(e, tuple) and text in e[1])

    def test_a_known_0h_pick_reaches_no_price(self):
        ev, out, calls, o = self.events(MainZeroTest.ON)
        chose, gate, stop = self.at(ev, "[choose] https"), self.at(ev, "no warranty expected"), self.at(ev, "[offer] limit 0")
        self.assertLess(chose, gate)
        self.assertLess(gate, stop)
        self.assertEqual([e for e in ev[chose:] if isinstance(e, str)], [])   # from the pick on: no price at all
        # before the pick, only buyer_food_sku.reserve's limit for every shop (another study, as before): the same calls
        # under rollback, so the gate changes nothing there
        rb = self.events(MainZeroTest.ROLLBACK)[0]
        before = lambda e: [x for x in e[:self.at(e, "[choose] https")] if isinstance(x, str)]
        self.assertEqual(before(ev), before(rb))
        self.assertNotIn("p_max", before(ev))
        self.assertEqual(calls, [])
        self.assertEqual(o["short_warranty"]["zero"], sw.ZERO_HIST)
        self.assertNotIn("short_warranty_buy", o)

    def test_the_probe_sees_prices_under_rollback(self):
        ev, out, calls, o = self.events(MainZeroTest.ROLLBACK)
        priced = [e for e in ev if isinstance(e, str)]
        for name in ("value_limit", "saved_cap", "accept_percent", "short_cap", "short_terms", "p_max"):
            self.assertIn(name, priced)
        self.assertEqual(len(calls), 1)


class ShortCapGateTest(unittest.TestCase):
    """short_cap called on its own (quote ranking, the order record) gives 0 for a no-warranty unit before short_terms,
    cap or p_max, with the run's catalog and with the fallback (no catalog) alike."""
    O = {"sku": "G1", "list_price": 10000}

    def cap(self, sw_state, name, q, catalog):
        with mock.patch.dict(market_buyer._SW_CTX, {"catalog": catalog, "md": None}, clear=True), \
                mock.patch.object(market_buyer, "short_terms", side_effect=AssertionError("short_terms")), \
                mock.patch.object(sw, "cap", side_effect=AssertionError("cap")), \
                mock.patch.object(sw, "p_max", side_effect=AssertionError("p_max")), \
                mock.patch.object(sw, "expected", side_effect=AssertionError("expected")):
            return market_buyer.short_cap(self.O, 0.072, {"kind": "goods", "_sw": sw_state}, name, q, None)

    def test_zero_gives_0_unpriced(self):
        s = st(hist={"z": 0}, zero_hist=["z"])
        for catalog in ({"G1": {"kind": "goods", "list_price": 10000}}, None):
            self.assertEqual(self.cap(s, "z", None, catalog), 0)
            self.assertEqual(self.cap(s, "z", {"warranty_h": None}, catalog), 0)
            self.assertEqual(self.cap(s, "a", {"warranty_h": 0, "refused": sw.ZERO_QUOTE}, catalog), 0)
            self.assertEqual(self.cap(s, "a", {"warranty_h": -1}, catalog), 0)

    def test_others_priced_as_before(self):
        s = st(hist={"z": 0}, zero_hist=["z"])
        with mock.patch.dict(market_buyer._SW_CTX, {}, clear=True):
            for name, q in (("a", {"warranty_h": 0.5}), ("a", {"warranty_h": 3}), ("z", {"warranty_h": 0.5})):
                on = market_buyer.short_cap(self.O, 0.072, {"kind": "goods", "_sw": s}, name, q)
                rb = market_buyer.short_cap(self.O, 0.072, {"kind": "goods", "_sw": dict(s, block_zero=False)}, name, q)
                self.assertEqual(on, rb, (name, q))
                self.assertGreater(on, 0, (name, q))
            self.assertIsNone(market_buyer.short_cap(self.O, 0.072, {"kind": "goods", "_sw": s}, "a", {"warranty_h": None}))
            rb0 = market_buyer.short_cap(self.O, 0.072, {"kind": "goods", "_sw": dict(s, block_zero=False)}, "z", None)
            self.assertGreater(rb0, 0)                                    # rollback: p_max(0) as on 11/08


if __name__ == "__main__":
    unittest.main()
