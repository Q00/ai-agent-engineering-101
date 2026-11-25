"""Goods offered without the 6 h warranty are never bought: the code walks away on the turn the shop says so, on any
turn, with no harness call (대표님 11/07 09:15: the LLM share is a KPI; goods without a warranty are off limits)."""
import unittest
from unittest import mock

import buyer
import market_buyer

GOODS = {"kind": "goods"}
FOOD = {"kind": "food", "use_within_hours": 10}
NO_WARRANTY = [
    "Inventory condition: B. Confirm the certificate at checkout; condition can change while negotiating. Warranty: 0 "
    "hours.\n바른잠 토퍼 매트리스 6cm Q: asking 84460. Send your offer as a number.",
    "에이스침대 BMA (G8095ddd6). Grade C: refurbished. No breakage warranty offered. Asking price: 520000.",
    "자코모 소파 (grade C, three-hour full-refund warranty, price capped by grade): asking 1624000.",
    "지누스 매트리스 (G1515d951), grade C. Market certificate included; warranty 0 h. Asking price: 243824.",
    "자코모 소파: asking 1809600. Refurbished unit sold as is, without a break warranty.",
    "Counteroffer: 18000. Includes a 0-hour warranty: market-enforced full refund if the goods break during warranty.",
    "We lowered the price. Counteroffer: 18000. No warranty on this one.",
]
WARRANTED = [
    "Simmons bed (G1234abcd): asking 900000. Includes a 6-hour warranty: market-enforced full refund if the goods break "
    "during warranty; the quote includes the warranty surcharge.",
    "We lowered the price. It comes with a three-day warranty. Our price is 850000.",
    "Asking 900000. Without Safe-Care: 800000 (no refund); say 'without care' to take that option.",
    "Grade B unit without defects, 6 h warranty. asking 18000.",
]


def task(text, meta=None, state="TASK_STATE_INPUT_REQUIRED"):
    msg = {"parts": [{"text": text}]}
    if meta is not None:
        msg["metadata"] = {"ask": meta}
    return {"id": "t1", "status": {"state": state, "message": msg}}


class GoodsTextRefused(unittest.TestCase):
    def test_goods_without_the_warranty_are_refused(self):
        for text in NO_WARRANTY:
            self.assertTrue(market_buyer.goods_text_refused(text, GOODS), text)

    def test_goods_with_the_warranty_are_not_refused(self):
        for text in WARRANTED:
            self.assertIsNone(market_buyer.goods_text_refused(text, GOODS), text)

    def test_food_is_never_refused_for_its_warranty(self):
        for text in NO_WARRANTY:
            self.assertIsNone(market_buyer.goods_text_refused(text, FOOD), text)


class TalkWalksAwayByCode(unittest.TestCase):
    """buyer.talk with market_buyer's stop_if: no offer is sent to the no-warranty turn and the harness is never asked."""

    def run_talk(self, first, replies, order=GOODS, meta=None):
        sent, asked = [], []

        def send(url, text, task_id=None):
            sent.append(text)
            return replies.pop(0)

        def decide(item, budget, history, text, meta, turn, note=""):
            asked.append(text)
            return {"shop_price": 20000, "action": "offer", "price": 18000}
        stop_if = (lambda text, turn: (market_buyer.quote_refused(text, order) if turn == 1 else None)
                   or market_buyer.goods_text_refused(text, order))
        out = {"state": "", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", decide), \
                mock.patch.object(buyer, "log"), mock.patch.object(buyer, "rpc", lambda *a: None):
            buyer.talk("u", task(first, meta), "침대", 30000, "", out, stop_if=stop_if)
        return sent, asked, out

    def test_first_turn_without_warranty_sends_nothing(self):
        for text in NO_WARRANTY:
            sent, asked, out = self.run_talk(text, [])
            self.assertEqual((sent, asked), ([], []), text)
            self.assertTrue(out.get("stopped"), text)

    def test_metadata_ask_does_not_bypass_the_walk(self):
        sent, asked, out = self.run_talk("Asking 20000. No warranty.", [], meta=20000)
        self.assertEqual((sent, asked), ([], []))

    def test_a_later_turn_that_drops_the_warranty_ends_the_talk(self):
        first = "Widget (G1234abcd): asking 20000. Includes a 6-hour warranty."
        sent, asked, out = self.run_talk(first, [task("Counteroffer: 19000. Warranty: 0 hours.", 19000)], meta=20000)
        self.assertEqual(len(sent), 1)               # the first offer only; nothing answers the no-warranty counter
        self.assertTrue(out.get("stopped"))
        self.assertNotIn(19000, [int(s) for s in sent if s.isdigit()])

    def test_ask_at_my_offer_without_warranty_is_not_taken(self):
        # the fast path would offer an ask at or under my last offer: the walk comes first
        first = "Widget (G1234abcd): asking 20000. Includes a 6-hour warranty."
        sent, _, out = self.run_talk(first, [task("OK, 18000 then. No warranty though.", 18000)], meta=20000)
        self.assertEqual(sent, ["18000"])
        self.assertTrue(out.get("stopped"))

    def test_food_without_warranty_goes_on(self):
        sent, asked, out = self.run_talk("Apples (G1234abcd): asking 20000, no warranty.", [task("done", None,
                                         "TASK_STATE_COMPLETED")], order={"kind": "food"}, meta=20000)
        self.assertEqual(len(sent), 1)
        self.assertFalse(out.get("stopped"))


class QuestionAnsweredByCode(unittest.TestCase):
    """mjforge asks how long the unit must last, beside its ask, and takes no number before the answer (11/07 09:19)."""
    Q = ("펩시콜라 245ml 15개 (sku=G9a240832), asking price 6873. Remaining shelf life is 482.44h (not a long-term "
         "guarantee). How many hours/days must it remain usable? Reply e.g. 'use within 72h'.")

    def test_the_answer_goes_first_with_no_harness_call(self):
        sent, asked = [], []
        replies = [task("asking 6873", 6873), task("done", None, "TASK_STATE_COMPLETED")]

        def send(url, text, task_id=None):
            sent.append(text)
            return replies.pop(0)

        def decide(*a, **k):
            asked.append(a)
            return {"shop_price": 6873, "action": "offer", "price": 6339}
        order = {"kind": "food", "use_within_hours": 359.2}
        out = {"state": "", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}
        import oneshot
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", decide), \
                mock.patch.object(buyer, "log"), mock.patch.object(buyer, "rpc", lambda *a: None):
            buyer.talk("u", task(self.Q), "펩시", 7419, "", out,
                       reply_to=lambda text: market_buyer.shop_question_reply(text, order),
                       ladder=lambda meta, text, history: oneshot.turn(meta, text, history, 7419, (0.9,)))
        self.assertEqual(sent[0], "use within 360h")
        self.assertEqual(asked, [])


if __name__ == "__main__":
    unittest.main()
