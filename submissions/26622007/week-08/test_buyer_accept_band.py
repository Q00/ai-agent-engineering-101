"""Plan A (owner 11/08, telegram 7918): a shop's counter within 105% of my limit, and within the order's budget, is taken."""
import os, tempfile, unittest
from unittest import mock

import buyer
import buyer_accept_band as band
import market_buyer as mb
import oneshot


def switch(md, body):
    with open(os.path.join(md, band.SWITCH), "w") as f:
        f.write(body)


class SwitchTest(unittest.TestCase):
    def test_off_unless_the_switch_file_says_on(self):
        with tempfile.TemporaryDirectory() as md:
            self.assertIsNone(band.percent(md, env={}))
            for body, want in (('{"enabled": true}', 105), ('{"enabled": false}', None), ('{"enabled": 1}', None),
                               ("not json", None), ("[]", None)):
                switch(md, body)
                self.assertEqual(band.percent(md, env={}), want, body)
            switch(md, '{"enabled": true}')
            self.assertIsNone(band.percent(md, env={"BUYER_ACCEPT_BAND": "0"}))   # the environment off wins


class CeilingTest(unittest.TestCase):
    def test_105_percent_of_the_limit_inside_the_budget(self):
        self.assertEqual(band.ceiling(10000, 20000, 105), 10500)
        self.assertEqual(band.ceiling(10001, 20000, 105), 10501)         # 10501.05, never rounded up
        self.assertEqual(band.ceiling(10000, 10300, 105), 10300)         # the order's budget holds
        self.assertEqual(band.ceiling(10000, 9000, 105), 10000)          # never under the limit itself

    def test_off_or_no_limit_is_the_limit(self):
        self.assertEqual(band.ceiling(10000, 20000, None), 10000)
        self.assertEqual(band.ceiling(0, 20000, 105), 0)                 # limit 0: no talk, as before

    def test_the_ceiling_never_passes_value_limit(self):
        # max_price_share under 0.7: the limit is value_limit, 105% of it would pass it (안A 담당 11/08 18:12)
        self.assertEqual(band.ceiling(10000, 20000, 105, cap=10000), 10000)
        self.assertEqual(band.ceiling(10000, 20000, 105, cap=10200), 10200)
        self.assertEqual(band.ceiling(10000, 20000, 105, cap=11000), 10500)
        self.assertEqual(band.ceiling(10000, 10100, 105, cap=10300), 10100)  # the budget still holds
        self.assertEqual(band.ceiling(10000, 20000, 105, cap=None), 10500)   # no cap: as before


def walk_after_my_limit(ask):
    """oneshot after my 0.9 offer and my one raise to the limit 10000: the shop's next counter."""
    history = [("shop asked", 12000), ("you offered", 9000), ("shop asked", 11000), ("you offered", 10000)]
    return oneshot.code_turn(ask, history, 10000, (0.9,)), history


class TurnTest(unittest.TestCase):
    def test_boundaries_104_9_105_105_1(self):
        for ask, taken in ((10490, True), (10500, True), (10510, False)):
            base, history = walk_after_my_limit(ask)
            self.assertEqual(base["action"], "walk_away")
            out, note = band.turn(base, history, 10000, 10500, (0.9,))
            if taken:
                self.assertEqual(out, {"shop_price": ask, "action": "offer", "price": ask, "accept_band": True}, ask)
                self.assertEqual(note, {"taken": ask, "limit": 10000, "ceiling": 10500})
            else:
                self.assertEqual((out, note), (base, None), ask)

    def test_my_raise_to_the_limit_comes_first(self):
        history = [("shop asked", 12000), ("you offered", 9000)]
        base = oneshot.code_turn(10400, history, 10000, (0.9,))
        self.assertEqual(base, {"shop_price": 10400, "action": "offer", "price": 10000})
        self.assertEqual(band.turn(base, history, 10000, 10500, (0.9,)), (base, None))

    def test_over_the_budget_is_not_taken(self):
        base, history = walk_after_my_limit(10400)
        out, note = band.turn(base, history, 10000, band.ceiling(10000, 10300, 105), (0.9,))
        self.assertEqual((out, note), (base, None))

    def test_off_is_the_old_turn(self):
        base, history = walk_after_my_limit(10400)
        self.assertEqual(band.turn(base, history, 10000, 10000, (0.9,)), (base, None))

    def test_the_opening_ask_still_gets_my_first_offer(self):
        base = oneshot.code_turn(10400, [], 10000, (0.9,))
        self.assertEqual(band.turn(base, [], 10000, 10500, (0.9,)), (base, None))

    def test_a_shop_that_did_not_take_its_own_ask_is_left(self):
        history = [("shop asked", 12000), ("you offered", 9000), ("shop asked", 10400), ("you offered", 10400)]
        base = oneshot.code_turn(10400, history, 10000, (0.9,))
        self.assertEqual(band.turn(base, history, 10000, 10500, (0.9,)), (base, None))

    def test_steps_left_keep_their_counter(self):
        history = [("shop asked", 12000), ("you offered", 9000)]
        base = oneshot.code_turn(10400, history, 10000, (0.75, 0.85))
        self.assertEqual(band.turn(base, history, 10000, 10500, (0.75, 0.85)), (base, None))

    def test_the_harness_turn_is_left_alone(self):
        self.assertEqual(band.turn(None, [("shop asked", 12000), ("you offered", 9000)], 10000, 10500, (0.9,)), (None, None))


def task(state, ask=None, receipt=None):
    t = {"id": "t-1", "status": {"state": state, "message": {"parts": [{"text": f"asking {ask}"}],
                                                          **({"metadata": {"ask": ask}} if ask is not None else {})}}}
    if receipt:
        t["artifacts"] = [{"parts": [{"data": receipt}]}]
    return t


class TalkTest(unittest.TestCase):
    def talk(self, ceiling, turn, history=(("shop asked", 12000), ("you offered", 9000)), ask=10400):
        sent = []

        def send(url, text, task_id=None):
            sent.append(text)
            return task("TASK_STATE_COMPLETED", receipt={"price": int(text), "item": "G1"})
        ladder = lambda meta, text, history: dict(turn, shop_price=meta)
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", side_effect=AssertionError("no harness")):
            buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", ask), "x", 10000, "",
                       {"history": list(history), "agreed": None, "holds": 0}, ladder=ladder, ceiling=ceiling)
        return sent

    def test_the_band_turn_goes_out_up_to_the_ceiling(self):
        self.assertEqual(self.talk(10500, {"action": "offer", "price": 10400, "accept_band": True}), ["10400"])

    def test_any_other_code_turn_keeps_the_limit_guard(self):
        self.assertEqual(self.talk(10500, {"action": "offer", "price": 10400}), ["10000"])
        self.assertEqual(self.talk(None, {"action": "offer", "price": 10400, "accept_band": True}), ["10000"])

    def test_a_fast_turn_under_my_band_offer_is_not_pulled_back_to_the_limit(self):
        history = [("shop asked", 12000), ("you offered", 9000), ("shop asked", 10450), ("you offered", 10450)]
        self.assertEqual(self.talk(10500, {"action": "walk_away", "price": 0}, history, ask=10420), ["10420"])
        self.assertEqual(self.talk(None, {"action": "walk_away", "price": 0}, history, ask=10420), ["10000"])


class ReceiptTest(unittest.TestCase):
    offer = {"sku": "G4c4977ca", "list_price": 18120}

    def test_a_receipt_within_the_ceiling_is_paid(self):
        self.assertIsNone(mb.over_limit({"item": "G4c4977ca", "price": 10500.0}, self.offer, 10000, {}, 20000, band=105))
        self.assertIn("over my limit", mb.over_limit({"item": "G4c4977ca", "price": 10501.0}, self.offer, 10000, {}, 20000,
                                                      band=105))
        self.assertIn("over my limit", mb.over_limit({"item": "G4c4977ca", "price": 10400.0}, self.offer, 10000, {}, 10300,
                                                      band=105))           # over the order's budget

    def test_off_is_the_limit(self):
        self.assertIn("over my limit", mb.over_limit({"item": "G4c4977ca", "price": 10001.0}, self.offer, 10000, {}, 20000))

    def test_the_band_never_pays_over_value_limit(self):
        # max_price_share 0.56: value_limit 18120 x 0.56 = 10147, the band's 10500 would pass it
        with mock.patch.object(mb, "buyer_cfg", return_value={"max_price_share": 0.56}):
            self.assertIsNone(mb.over_limit({"item": "G4c4977ca", "price": 10147.0}, self.offer, 10000, {}, 20000, band=105))
            self.assertIn("over my limit", mb.over_limit({"item": "G4c4977ca", "price": 10148.0}, self.offer, 10000, {},
                                                          20000, band=105))


class RealLadderTest(unittest.TestCase):
    """market_buyer.main's own price_ladder: the band moves only the turn where my steps are spent."""

    def ladder(self, order, top):
        import ast, inspect
        from types import SimpleNamespace
        node = next(n for n in ast.walk(ast.parse(inspect.getsource(mb.main)))
                    if isinstance(n, ast.FunctionDef) and n.name == "price_ladder")
        ns = {**vars(mb), "discount_arm": None, "probe_arm": None, "goods_first_arm": None, "limit": 10000,
              "steps": (.9,), "q": {"ask": 11000}, "resume": True, "order": order, "args": SimpleNamespace(budget=20000),
              "shop": {"name": "fixture"}, "key": "G12345678", "r3": False}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<ladder>", "exec"), ns)
        order["_accept_band_top"] = top if top > 10000 else None
        return ns["price_ladder"]

    def test_on_takes_the_final_ask_within_105_after_my_raise_off_walks(self):
        history = [("shop asked", 11000), ("you offered", 9900)]
        for top in (10500, 10000):     # my raise to the limit first, on or off
            self.assertEqual(self.ladder({"kind": "goods"}, top)(10500, "", history),
                             {"shop_price": 10500, "action": "offer", "price": 10000})
        history += [("shop asked", 10500), ("you offered", 10000)]
        on = {"kind": "goods"}
        self.assertEqual(self.ladder(on, 10500)(10500, "", history),
                         {"shop_price": 10500, "action": "offer", "price": 10500, "accept_band": True})
        self.assertEqual(on["_accept_band"], {"taken": 10500, "limit": 10000, "ceiling": 10500})
        off = {"kind": "goods"}
        self.assertEqual(self.ladder(off, 10000)(10500, "", history)["action"], "walk_away")
        self.assertNotIn("_accept_band", off)
        self.assertEqual(self.ladder({"kind": "goods"}, 10500)(10510, "", history)["action"], "walk_away")   # 105.1%

    def test_a_final_price_text_read_by_code_walks_as_the_harness_did(self):
        """"Four offers left. Final price: N." went to the harness, which the band never moves, until oneshot read it by
        code (11/10): the band leaves that turn alone, so each talk ends as before (3 of 31 logged talks 11/10 had N within 105%,
        and sf2w-band-1110-0242-v1 turns the band off in one arm). A metadata ask keeps the band."""
        history = [("shop asked", 11000), ("you offered", 9900), ("shop asked", 10500), ("you offered", 10000)]
        on = {"kind": "goods"}
        d = self.ladder(on, 10500)(None, "Four offers left. Final price: 10500. ", history)
        self.assertEqual(d, {"shop_price": 10500, "action": "walk_away", "price": 0})
        self.assertNotIn("_accept_band", on)
        self.assertEqual(self.ladder({"kind": "goods"}, 10500)(None, "Final price: 10500.", history)["action"], "walk_away")
        tail = "Final price: 10500. SKU G0123abcd, grade A; market certificate at checkout. Reply ok to take it."
        self.assertEqual(self.ladder({"kind": "goods"}, 10500)(None, tail, history)["action"], "walk_away")   # re-review M1
        self.assertTrue(self.ladder({"kind": "goods"}, 10500)(10500, "Final price: 10500.", history)["accept_band"])
        self.assertEqual(self.ladder({"kind": "goods"}, 10500)(None, "Final price: 10000.", history),
                         {"shop_price": 10000, "action": "offer", "price": 10000})   # within my limit: taken

    def test_b3_leaves_a_final_price_text_alone_too(self):
        """B3 (off since 11/08 16:50) cuts the offer after my first one; a code-read "Final price" was a harness turn B3
        never touched, so its ask within my limit is taken as the harness took it (re-review M2). A metadata ask keeps B3."""
        history = [("shop asked", 10500), ("you offered", 9450)]
        on = {"kind": "goods", "_b3": {"applied": False}}
        self.assertEqual(self.ladder(on, 10000)(None, "Four offers left. Final price: 9800.", history),
                         {"shop_price": 9800, "action": "offer", "price": 9800})
        self.assertEqual(on["_b3"], {"applied": False})
        meta = {"kind": "goods", "_b3": {"applied": False}}
        self.assertEqual(self.ladder(meta, 10000)(9800, "", history)["price"], 9660)     # 0.92 of the first ask
        self.assertTrue(meta["_b3"]["applied"])

if __name__ == "__main__":
    unittest.main()
