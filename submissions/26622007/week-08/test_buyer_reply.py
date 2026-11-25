"""A shop that asks a question instead of a price gets an answer, not a walk-away (11/06 23:15 mjforge: "How many
hours/days must it remain usable? Reply e.g. 'use within 72h'", our harness repeated the old price, the guard walked away;
mjforge ended 45 of its 51 talks with us since 11/06 06:00)."""
import unittest
from unittest import mock

import buyer
import market_buyer

QUESTION = ("Remaining shelf life is 225.56h (not a long-term guarantee). How many hours/days must it remain usable? "
            "Reply e.g. 'use within 72h'.")


def task(text, meta=None, state="TASK_STATE_INPUT_REQUIRED"):
    msg = {"parts": [{"text": text}]}
    if meta is not None:
        msg["metadata"] = {"ask": meta}
    return {"id": "t1", "status": {"state": state, "message": msg}}


class ReplyTest(unittest.TestCase):
    def run_talk(self, replies, reply_to):
        sent = []

        def send(url, text, task_id=None):
            sent.append(text)
            return replies.pop(0)
        decide = lambda item, budget, history, text, meta, turn, note="": {"shop_price": 23133, "action": "offer",
                                                                             "price": 19000}
        out = {"state": "", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", decide), \
                mock.patch.object(buyer, "log"), mock.patch.object(buyer, "rpc", lambda *a: None):
            buyer.talk("u", task("asking 23133", 23133), "된장", 25830, "", out, reply_to=reply_to)
        return sent, out

    def test_a_shelf_life_question_is_answered_and_the_talk_goes_on(self):
        done = {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [{"parts": [{"data": {"price": 19000}}]}]}
        sent, out = self.run_talk([task(QUESTION), task("asking 20000", 20000), done],
                                  lambda text: "use within 360h" if "usable" in text else None)
        self.assertEqual(sent, ["19000", "use within 360h", "19000"])
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")

    def test_without_an_answer_the_guard_still_walks_away(self):
        sent, out = self.run_talk([task(QUESTION)], lambda text: None)
        self.assertEqual(sent, ["19000"])
        self.assertNotEqual(out["state"], "TASK_STATE_COMPLETED")

    def test_the_market_buyer_answer(self):
        self.assertEqual(market_buyer.shop_question_reply(QUESTION, {"kind": "food", "use_within_hours": 359.2}),
                         "use within 360h")
        self.assertIsNone(market_buyer.shop_question_reply("asking 9000", {"kind": "food", "use_within_hours": 10}))
        self.assertIsNone(market_buyer.shop_question_reply(QUESTION, {"kind": "food", "use_within_hours": None}))


SWAP = ("We can't go lower on the grade A unit, but thank you for the offer. We have the same set in grade C refurbished, "
        "with a three-day warranty: if it breaks within that time, we refund the full price from our balance. Reply yes if "
        "you want it for 1700000")


class StandingAskTest(unittest.TestCase):
    """A shop that keeps its grade A ask and offers grade C at my price writes only my number (11/07 00:00:13: harness read
    1750949, its ask a turn before, limit 1800234; the guard walked away, 7 talks 11/06 22:23 - 11/07 01:30)."""

    def run_talk(self, second_read, replies):
        sent, reads = [], [{"shop_price": 1750949, "action": "offer", "price": 1700000}, second_read]

        def send(url, text, task_id=None):
            sent.append(text)
            return replies.pop(0)
        decide = lambda *a, **k: reads.pop(0)
        out = {"state": "", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", decide), \
                mock.patch.object(buyer, "log"), mock.patch.object(buyer, "rpc", lambda *a: None):
            buyer.talk("u", task("Grade A, asking 1750949."), "침대", 1800234, "", out)
        return sent, out

    def test_the_ask_it_stated_before_stands_and_the_talk_goes_on(self):
        done = {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [{"parts": [{"data": {"price": 1750949}}]}]}
        sent, out = self.run_talk({"shop_price": 1750949, "action": "offer", "price": 1750949}, [task(SWAP), done])
        self.assertEqual(sent, ["1700000", "1750949"])
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")

    def test_my_offer_again_is_never_sent_as_a_yes_to_the_swap(self):
        done = {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [{"parts": [{"data": {"price": 1750949}}]}]}
        sent, _ = self.run_talk({"shop_price": 1750949, "action": "offer", "price": 1700000}, [task(SWAP), done])
        self.assertEqual(sent, ["1700000", "1750949"])

    def test_a_new_number_beside_my_offer_still_walks_away(self):
        sent, _ = self.run_talk({"shop_price": 1750949, "action": "offer", "price": 1750949},
                                [task(SWAP + ", or grade A for 1720000.")])
        self.assertEqual(sent, ["1700000"])

    def test_a_price_the_shop_never_said_still_walks_away(self):
        sent, out = self.run_talk({"shop_price": 1740000, "action": "offer", "price": 1740000}, [task(SWAP)])
        self.assertEqual(sent, ["1700000"])

    def test_an_old_ask_without_my_offer_in_the_message_still_walks_away(self):
        sent, out = self.run_talk({"shop_price": 1750949, "action": "offer", "price": 1750949},
                                  [task("How many days must it last? Reply e.g. 'use within 72h'.")])
        self.assertEqual(sent, ["1700000"])


if __name__ == "__main__":
    unittest.main()
