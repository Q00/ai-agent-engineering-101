"""oneshot: the talk priced by code (one offer under the shop's first ask, then its counter taken), with no harness call."""
import unittest
from unittest import mock

import buyer
import oneshot


def task(state, ask=None, receipt=None, text=None):
    t = {"id": "t-1", "status": {"state": state, "message": {"parts": [{"text": text if text is not None else f"asking {ask}"}],
                                                          **({"metadata": {"ask": ask}} if ask is not None else {})}}}
    if receipt:
        t["artifacts"] = [{"parts": [{"data": receipt}]}]
    return t


def fresh():
    return {"history": [], "agreed": None, "holds": 0}


class CodeTurn(unittest.TestCase):
    def turn(self, ask, history, limit=10000, steps=(0.9,)):
        return oneshot.code_turn(ask, history, limit, steps)

    def test_first_offer_is_the_step_share_of_the_first_ask(self):
        self.assertEqual(self.turn(10000, []), {"shop_price": 10000, "action": "offer", "price": 9000})

    def test_first_offer_never_over_my_limit(self):
        self.assertEqual(self.turn(20000, [], limit=12000)["price"], 12000)

    def test_counter_within_my_limit_is_taken(self):
        h = [("shop asked", 10000), ("you offered", 9000)]
        self.assertEqual(self.turn(9600, h), {"shop_price": 9600, "action": "offer", "price": 9600})

    def test_counter_over_my_limit_gets_my_limit_once(self):
        h = [("shop asked", 12000), ("you offered", 9000)]
        self.assertEqual(self.turn(11000, h, limit=10000)["price"], 10000)
        h += [("shop asked", 11000), ("you offered", 10000)]
        self.assertEqual(self.turn(10500, h, limit=10000)["action"], "walk_away")

    def test_same_ask_after_i_offered_it_walks(self):
        """The shop did not take its own ask back: no loop of the same number."""
        h = [("shop asked", 10000), ("you offered", 9000), ("shop asked", 9600), ("you offered", 9600)]
        self.assertEqual(self.turn(9600, h)["action"], "walk_away")

    def test_ask_at_or_under_my_last_offer_is_taken(self):
        h = [("shop asked", 10000), ("you offered", 9000)]
        self.assertEqual(self.turn(8800, h)["price"], 8800)

    def test_ladder_of_steps_rises_from_the_first_ask(self):
        steps = (0.84, 0.9)
        self.assertEqual(self.turn(10000, [], steps=steps)["price"], 8400)
        h = [("shop asked", 10000), ("you offered", 8400)]
        self.assertEqual(self.turn(9800, h, steps=steps)["price"], 9000)
        h += [("shop asked", 9800), ("you offered", 9000)]
        self.assertEqual(self.turn(9700, h, steps=steps)["price"], 9700)

    def test_no_ask_read_leaves_the_turn_to_the_harness(self):
        self.assertIsNone(self.turn(None, []))
        self.assertIsNone(self.turn(0, []))

    def test_offers_end_after_the_steps_and_two_more(self):
        h = [("shop asked", 10000), ("you offered", 9000), ("shop asked", 9800), ("you offered", 9800),
             ("shop asked", 9900), ("you offered", 9900)]
        self.assertEqual(self.turn(9700, h)["action"], "walk_away")


class Settings(unittest.TestCase):
    def test_env_wins_over_pricing(self):
        self.assertTrue(oneshot.enabled({"oneshot": False}, {"BUYER_ONESHOT": "1"}))
        self.assertFalse(oneshot.enabled({"oneshot": True}, {"BUYER_ONESHOT": "0"}))
        self.assertFalse(oneshot.enabled({"oneshot": True}, {"BUYER_ONESHOT": "false"}))
        self.assertFalse(oneshot.enabled({"oneshot": True}, {"BUYER_ONESHOT": " OFF "}))
        self.assertTrue(oneshot.enabled({}, {"BUYER_ONESHOT": "true"}))
        self.assertTrue(oneshot.enabled({"oneshot": True}, {"BUYER_ONESHOT": ""}))

    def test_meta_harness_may_not_move_the_switch(self):
        import hard_limits
        old = {"buyer": {"oneshot": True, "quote_shops": 10}}
        self.assertTrue(any("FIXED" in p for p in hard_limits.check_policy_change(old, {"buyer": {"oneshot": False, "quote_shops": 10}})))
        self.assertTrue(any("removed" in p for p in hard_limits.check_policy_change(old, {"buyer": {"quote_shops": 10}})))
        self.assertEqual(hard_limits.check_policy_change(old, old), [])

    def test_pricing_flag_and_default_off(self):
        self.assertTrue(oneshot.enabled({"oneshot": True}, {}))
        self.assertFalse(oneshot.enabled({}, {}))
        self.assertFalse(oneshot.enabled({"oneshot": "yes"}, {}))

    def test_steps_kept_only_when_sane(self):
        self.assertEqual(oneshot.steps({"oneshot_steps": [0.85, 0.92]}), (0.85, 0.92))
        for bad in ([1.2], [0.3], [0.9, 0.8], "0.9", [True], [], [0.9] * 6):
            self.assertEqual(oneshot.steps({"oneshot_steps": bad}), oneshot.STEPS, bad)
        self.assertEqual(oneshot.steps({}), oneshot.STEPS)


class InTalk(unittest.TestCase):
    def test_talk_with_a_ladder_calls_no_harness(self):
        sent = []
        replies = iter([task("TASK_STATE_INPUT_REQUIRED", 9500),
                        task("TASK_STATE_COMPLETED", receipt={"price": 9500, "item": "G1"})])

        def send(url, text, task_id=None):
            sent.append(text)
            return next(replies)
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 10000, (0.9,))
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", side_effect=AssertionError("no harness")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 10000), "x", 10000, "", fresh(), ladder=lad)
        self.assertEqual(sent, ["9000", "9500"])
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(out["agreed"], 9500)

    def test_counter_template_is_priced_by_code(self):
        sent = []
        replies = iter([task("TASK_STATE_INPUT_REQUIRED", text="7200 is too low. Counteroffer price: 7600."),
                        task("TASK_STATE_COMPLETED", receipt={"price": 7600, "item": "G1"})])

        def send(url, text, task_id=None):
            sent.append(text)
            return next(replies)
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 10000, (0.9,))
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", side_effect=AssertionError("no harness")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 8000), "x", 10000, "", fresh(), ladder=lad)
        self.assertEqual(sent, ["7200", "7600"])
        self.assertEqual(out["agreed"], 7600)

    def test_final_price_talk_calls_the_harness_on_the_quote_turn_only(self):
        """moss-and-oak 03:20:42 c9708175: the quote turn, then Five/Four/Three offers left. Final price: 108635 - four
        harness calls for a talk that ended with my limit refused. By code: my limit once, then the next order."""
        sent = []
        replies = iter([task("TASK_STATE_INPUT_REQUIRED", text="Five offers left. Final price: 108635. "),
                        task("TASK_STATE_INPUT_REQUIRED", text="Four offers left. Final price: 108635. ")])

        def send(url, text, task_id=None):
            sent.append(text)
            return next(replies)
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 94145, (0.9,))
        first = {"shop_price": 108635, "action": "offer", "price": 65000}
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", return_value=first) as h, \
                mock.patch.object(buyer, "rpc", return_value={}):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", text="a quote, 108635 by no rule"), "x", 94145, "",
                             fresh(), ladder=lad)
        self.assertEqual(h.call_count, 1)
        self.assertEqual(sent, ["65000", "94145"])
        self.assertEqual(out["turns"], {"harness": 1, "code": 2})
        self.assertNotEqual(out.get("state"), "TASK_STATE_COMPLETED")
        self.assertIsNone(out.get("receipt"))

    def test_final_price_within_my_limit_is_taken_by_code(self):
        sent = []
        replies = iter([task("TASK_STATE_INPUT_REQUIRED", text="Five offers left. Final price: 84902. "),
                        task("TASK_STATE_COMPLETED", receipt={"price": 84902, "item": "G1"})])

        def send(url, text, task_id=None):
            sent.append(text)
            return next(replies)
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 84967, (0.9,))
        first = {"shop_price": 84902, "action": "offer", "price": 56000}
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", return_value=first) as h:
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", text="a quote, 84902 by no rule"), "x", 84967, "",
                             fresh(), ladder=lad)
        self.assertEqual(h.call_count, 1)
        self.assertEqual(sent, ["56000", "84902"])
        self.assertEqual(out["agreed"], 84902)

    def test_two_text_prices_go_to_the_harness(self):
        """"Was asking 20000, now just 12000": text_ask reads 20000, and code would offer 18000 against 12000."""
        calls, sent = [], []

        def decide(*a, **k):
            calls.append(a)
            return {"shop_price": 12000, "action": "offer", "price": 11000}

        def send(url, text, task_id=None):
            sent.append(text)
            return task("TASK_STATE_COMPLETED", receipt={"price": int(text), "item": "G1"})
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 25000, (0.9,), quoted=20000)
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", decide):
            buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", text="Was asking 20000, now just 12000."), "x", 25000, "", fresh(),
                       ladder=lad)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sent, ["11000"])

    def test_no_price_in_text_goes_to_the_harness(self):
        calls = []

        def decide(*a, **k):
            calls.append(a)
            return {"shop_price": 0, "action": "walk_away", "price": 0}
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 10000, (0.9,))
        with mock.patch.object(buyer, "send", lambda *a, **k: None), mock.patch.object(buyer, "decide", decide), \
                mock.patch.object(buyer, "rpc", lambda *a: task("TASK_STATE_CANCELED")):
            buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", text="What do you need it for?"), "x", 10000, "", fresh(),
                       ladder=lad)
        self.assertEqual(len(calls), 1)

    def test_walk_away_from_the_ladder_cancels(self):
        cancelled = []
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 10000, (0.9,))
        replies = iter([task("TASK_STATE_INPUT_REQUIRED", 13000), task("TASK_STATE_INPUT_REQUIRED", 13000)])
        with mock.patch.object(buyer, "send", lambda *a, **k: next(replies)), \
                mock.patch.object(buyer, "decide", side_effect=AssertionError("no harness")), \
                mock.patch.object(buyer, "rpc", lambda url, m, p: cancelled.append(m) or task("TASK_STATE_CANCELED")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 14000), "x", 10000, "", fresh(), ladder=lad)
        self.assertEqual(cancelled, ["CancelTask"])
        self.assertIsNone(out["agreed"])
        self.assertLessEqual(max(p for who, p in out["history"] if who == "you offered"), 10000)


class TurnCounts(unittest.TestCase):
    """Which branch priced each turn (Pro 03:01: talk_mode only says the switch was on): code, fast, take or harness."""

    def test_code_and_harness_turns_are_counted(self):
        replies = iter([task("TASK_STATE_INPUT_REQUIRED", text="Fine, 9600 and it is yours."),
                        task("TASK_STATE_COMPLETED", receipt={"price": 9600, "item": "G1"})])
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 10000, (0.9,))
        with mock.patch.object(buyer, "send", lambda *a, **k: next(replies)), \
                mock.patch.object(buyer, "decide", lambda *a, **k: {"shop_price": 9600, "action": "offer", "price": 9600}):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 10000), "x", 10000, "", fresh(), ladder=lad)
        self.assertEqual(out["turns"], {"code": 1, "harness": 1})

    def test_a_talk_with_no_turn_counts_nothing(self):
        with mock.patch.object(buyer, "send", lambda *a, **k: None):
            out = buyer.talk("u", task("TASK_STATE_CANCELED"), "x", 10000, "", fresh())
        self.assertEqual(out["turns"], {})


class TextPrice(unittest.TestCase):
    H = [("shop asked", 20000), ("you offered", 16000)]

    def test_shop_templates_in_the_log_are_read(self):
        self.assertEqual(oneshot.text_price("Counteroffer: 18500.", self.H), 18500)
        self.assertEqual(oneshot.text_price("Counteroffer: 18500", self.H), 18500)
        self.assertEqual(oneshot.text_price("3 offers left. Counteroffer: 18500", self.H), 18500)
        self.assertEqual(oneshot.text_price("16000 is too low. Counteroffer price: 18500.", self.H), 18500)
        self.assertEqual(oneshot.text_price("Our lowest price for this unit: 18500 Reply ok to take it.", self.H), 18500)
        self.assertEqual(oneshot.text_price(
            "Counteroffer: 18500. SKU G3323e2b7, grade A; market certificate at checkout. Includes a 6-hour warranty: "
            "market-enforced full refund if the goods break during warranty; the quote includes the warranty surcharge.",
            self.H), 18500)

    def test_final_price_templates_in_the_log_are_read(self):
        """moss-and-oak ("Five offers left. Final price: 108635. ") and two food shops ("Final price: 9115. ") write only
        this on every later turn: 45 of the 76 buyer-harness calls 11/10 03:55-04:25 read it (market-kit.log)."""
        self.assertEqual(oneshot.text_price("Five offers left. Final price: 18500. ", self.H), 18500)
        self.assertEqual(oneshot.text_price("Four offers left. Final price: 18500.", self.H), 18500)
        self.assertEqual(oneshot.text_price("Three offers left. Final price: 18500.", self.H), 18500)
        self.assertEqual(oneshot.text_price("One offer left. Final price: 18500.", self.H), 18500)
        self.assertEqual(oneshot.text_price("2 offers left. Final price: 18500.", self.H), 18500)
        self.assertEqual(oneshot.text_price("Final price: 18500. ", self.H), 18500)

    def test_final_text_is_the_whole_template_only(self):
        for text in ("Five offers left. Final price: 108635. ", "Final price: 9115.", "2 offers left. Final price: 7",
                     "Final price: 18500. SKU G0123abcd, grade A; market certificate at checkout.",
                     "Three offers left. Final price: 18500. Reply ok to take it."):
            self.assertTrue(oneshot.final_text(text), text)
        for text in ("Counteroffer: 18500.", "Final price: 19000 (17000 if paid now)", "Not my final price: 19000.",
                     "Final price: 18,500.", "", None, "3 offers left. Counteroffer: 18500.",
                     "Counteroffer: 18500. Reply ok to take it."):
            self.assertFalse(oneshot.final_text(text), text)
        for text in ("Final price: 18500.", "Final price: 18500. SKU G0123abcd, grade A; market certificate at checkout."):
            self.assertEqual(oneshot.final_text(text), oneshot.text_price(text, self.H) is not None, text)

    def test_final_price_keeps_the_ask_bounds(self):
        self.assertIsNone(oneshot.text_price("Final price: 20400.", self.H))      # over the shop's last ask
        self.assertIsNone(oneshot.text_price("Final price: 15000.", self.H))      # under my last offer
        self.assertIsNone(oneshot.text_price("Final price: 18500.", []))          # a first turn is read only as quoted

    def test_a_long_digit_run_goes_to_the_harness(self):
        self.assertIsNone(oneshot.text_price("Counteroffer: " + "1" * 5000 + ".", self.H))

    def test_too_low_must_name_my_last_offer(self):
        self.assertIsNone(oneshot.text_price("15000 is too low. Counteroffer price: 18500.", self.H))

    def test_never_over_the_last_ask_nor_under_my_last_offer(self):
        self.assertIsNone(oneshot.text_price("Counteroffer: 20400.", self.H))
        self.assertIsNone(oneshot.text_price("Counteroffer: 15000.", self.H))
        self.assertEqual(oneshot.text_price("Counteroffer: 16000.", self.H), 16000)

    def test_first_turn_text_is_read_only_as_quoted(self):
        self.assertEqual(oneshot.text_price("Counteroffer: 18500.", [], quoted=18500), 18500)
        self.assertIsNone(oneshot.text_price("Counteroffer: 18500.", [], quoted=None))
        self.assertIsNone(oneshot.text_price("Counteroffer: 18500.", [], quoted=18000))
        self.assertIsNone(oneshot.text_price("Grade A, asking 18500.", [("shop asked", 20000)], quoted=18500))

    def test_free_text_goes_to_the_harness(self):
        """Every bypass four independent reviews (11/07) found in word-list readings of free text."""
        for text in ("Was asking 20000, now just 12000.", "Down from 20000 to your 18000, deal?", "Fine, 18,000 instead of 20,000",
                     "asking 19000 minus 2000 for loyal customers", "asking 19000. Now 17500.00 final", "asking 19000, now \uff11\uff17\uff15\uff10\uff10",
                     "asking 19000 \uc9c0\uae08\uc740 \ub9cc\uce60\ucc9c\uc6d0", "Others ask 19000, I accept your offer.", "Agreed at your offer. Retail price is 19000.",
                     "asking 19000, \uc624\ub298\ub9cc \ubc18\uac12", "asking 19000 for a pair", "Counteroffer: 19000 (17000 if paid now)",
                     "Counteroffer: 19000. Or 17000 tomorrow.", "Counteroffer: 19000 minus 2000",
                     "Counteroffer: 19000.00", "Counteroffer: \uff11\uff19\uff10\uff10\uff10", "Counteroffer: 19000. Reply ok to take it at 17000.",
                     "Counteroffer: 19000\nJust kidding, 12000",
                     "Final price: 19000 (17000 if paid now)", "Final price: 19000. Or 17000 tomorrow.",
                     "Final price: 19000.00", "Five offers left. Final price: 19000 each", "Not my final price: 19000.",
                     "Many offers left. Final price: 19000.", "Final price: 19000. Reply ok to take it at 17000.",
                     "Final price: １９０００"):
            self.assertIsNone(oneshot.text_price(text, self.H), text)

    def test_thousands_separators_are_the_same_price(self):
        self.assertEqual(oneshot.text_price("Counteroffer: 19,000.", self.H), 19000)
        self.assertEqual(oneshot.text_price("Grade A. Asking price: 19,500.", self.H), 19500)
        self.assertIsNone(oneshot.text_price("Grade A, asking price: 19,500.", self.H))   # mid-sentence: not a field

    def test_turn_prefers_metadata(self):
        self.assertEqual(oneshot.turn(10000, "asking 99999", [], 10000)["price"], 9000)
        self.assertEqual(oneshot.turn(None, "asking 10000", [], 10000, quoted=10000)["price"], 9000)
        self.assertIsNone(oneshot.turn(None, "asking 10000", [], 10000))


if __name__ == "__main__":
    unittest.main()
