"""buyer_walk_take (W-20261115-1530): the margin cap take where any turn would walk away, the harness's included.

13:49:04-07 (logs/market-kit.log.1 151002-151019): usagi asked 231698 (cap 231985, limit 212055); code offered 208528;
usagi answered "Counteroffer: 231698. This is my lowest price.", which no TEMPLATE matched, so the harness offered 212055
and then walked away, and we cancelled the talk: the take (buyer_accept_band.turn in market_buyer's ladder) only saw code
turns. 16-20 such talks in the hour after the take went on (usagi, moss-and-oak)."""
import unittest
from unittest import mock

import buyer
import buyer_walk_take as wt
import oneshot
import test_buyer_margin_cap

LOWEST = "Counteroffer: 231698. This is my lowest price."


def task(text, meta=None, state="TASK_STATE_INPUT_REQUIRED"):
    msg = {"parts": [{"text": text}]}
    if meta is not None:
        msg["metadata"] = {"ask": meta}
    return {"id": "t1", "status": {"state": state, "message": msg}}


def done(price):
    return {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED"}, "artifacts": [{"parts": [{"data": {"price": price}}]}]}


class Talk(unittest.TestCase):
    """buyer.talk: the usagi talk replayed with the harness's two answers from the log."""
    LIMIT, TOP = 212055, 231985

    def run_talk(self, replies, reads, walk_take, first=None):
        sent, reads = [], list(reads)

        def send(url, text, task_id=None):
            sent.append(text)
            return replies.pop(0)

        def ladder(meta, text, history):     # the quote is priced by code (0.9 of the first ask), later turns by the harness
            if history:
                return None
            return {"shop_price": int(text.split(": ")[1].rstrip(".")), "action": "offer", "price": 208528}
        out = {"state": "", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", lambda *a, **k: reads.pop(0)), \
                mock.patch.object(buyer, "log"), mock.patch.object(buyer, "rpc", lambda *a: None):
            buyer.talk("u", first or task("Counteroffer: 231698."), "매트리스", self.LIMIT, "", out, ladder=ladder,
                       ceiling=self.TOP, walk_take=walk_take)
        return sent, out

    READS = [{"shop_price": 231698, "action": "offer", "price": 212055},
             {"shop_price": 231698, "action": "walk_away", "price": 212055}]

    def test_the_harness_walk_on_a_checked_ask_within_the_cap_is_taken(self):
        seen = []
        take = wt.hook(self.LIMIT, self.TOP, goods=True, record=seen.append)
        sent, out = self.run_talk([task(LOWEST), task(LOWEST), done(231698)], self.READS, take)
        self.assertEqual(sent, ["208528", "212055", "231698"])
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(out["agreed"], 231698)
        self.assertEqual(seen, [{"taken": 231698, "limit": 212055, "ceiling": 231985, "turn": "harness"}])
        self.assertEqual(out["turns"].get("walk_take"), 1)

    def test_without_the_hook_the_talk_walks_as_before(self):
        sent, out = self.run_talk([task(LOWEST), task(LOWEST)], self.READS, None)
        self.assertEqual(sent, ["208528", "212055"])
        self.assertNotEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertIsNone(out["agreed"])

    def test_an_ask_over_the_cap_still_walks(self):
        text = "Counteroffer: 231986. This is my lowest price."
        reads = [{"shop_price": 231986, "action": "offer", "price": 212055},
                 {"shop_price": 231986, "action": "walk_away", "price": 212055}]
        sent, out = self.run_talk([task(text), task(text)], reads, wt.hook(self.LIMIT, self.TOP, goods=True),
                                  first=task("Counteroffer: 231986."))
        self.assertEqual(sent, ["208528", "212055"])
        self.assertNotEqual(out["state"], "TASK_STATE_COMPLETED")

    def test_a_shop_that_turns_down_its_own_ask_is_left_to_walk(self):
        # taken once, the shop asks the same again instead of closing: no second take, the talk ends
        reads = self.READS + [{"shop_price": 231698, "action": "walk_away", "price": 0}]
        sent, out = self.run_talk([task(LOWEST), task(LOWEST), task(LOWEST)], reads,
                                  wt.hook(self.LIMIT, self.TOP, goods=True))
        self.assertEqual(sent, ["208528", "212055", "231698"])
        self.assertNotEqual(out["state"], "TASK_STATE_COMPLETED")

    def test_a_hold_comes_first(self):
        # review 576e714 I2: the ask is still coming down within 10% of my limit: HOLD repeats the limit, no take
        reads = [{"shop_price": 220000, "action": "offer", "price": 212055},
                 {"shop_price": 218000, "action": "walk_away", "price": 0}]
        sent, out = self.run_talk([task("Counteroffer: 220000. This is my lowest price."),
                                   task("Counteroffer: 218000. This is my lowest price."), task("x", state="TASK_STATE_CANCELED")],
                                  reads, wt.hook(self.LIMIT, self.TOP, goods=True), first=task("Counteroffer: 231698."))
        self.assertEqual(sent, ["208528", "212055", "212055"])
        self.assertEqual(out["holds"], 1)

    def test_a_harness_that_repeats_my_limit_to_a_held_ask_takes_it(self):
        # review 576e714 I3: no walk, the talk would run out its turns and be cancelled
        seen = []
        reads = [self.READS[0], {"shop_price": 231698, "action": "offer", "price": 212055}]
        sent, out = self.run_talk([task(LOWEST), task(LOWEST), done(231698)], reads,
                                  wt.hook(self.LIMIT, self.TOP, goods=True, record=seen.append))
        self.assertEqual(sent, ["208528", "212055", "231698"])
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(seen[0]["turn"], "stall")

    def test_a_decision_guard_veto_records_nothing(self):
        # review 576e714 M1: the row records the take only when its price goes on the wire
        seen, sent = [], []
        replies = [task(LOWEST), task(LOWEST)]
        reads = list(self.READS)
        out = {"state": "", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}

        def send(url, text, task_id=None):
            sent.append(text)
            return replies.pop(0)
        guard = lambda d, h: d if d["price"] <= self.LIMIT else {"action": "walk_away"}  # noqa: E731
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", lambda *a, **k: reads.pop(0)), \
                mock.patch.object(buyer, "log"), mock.patch.object(buyer, "rpc", lambda *a: None):
            buyer.talk("u", task("Counteroffer: 231698."), "매트리스", self.LIMIT, "", out,
                       ladder=lambda m, t, h: None if h else {"shop_price": 231698, "action": "offer", "price": 208528},
                       ceiling=self.TOP, decision_guard=guard,
                       walk_take=wt.hook(self.LIMIT, self.TOP, goods=True, record=seen.append))
        self.assertEqual(sent, ["208528", "212055"])
        self.assertEqual(seen, [])
        self.assertNotIn("walk_take", out["turns"])

    def test_the_harness_number_must_be_the_one_the_text_holds(self):
        # the harness reads 220000 though the shop wrote 231698: the guard walks as before, nothing is taken
        reads = [self.READS[0], {"shop_price": 220000, "action": "walk_away", "price": 0}]
        sent, out = self.run_talk([task(LOWEST), task(LOWEST)], reads, wt.hook(self.LIMIT, self.TOP, goods=True))
        self.assertEqual(sent, ["208528", "212055"])


class Verified(unittest.TestCase):
    """verified_ask: the shop's ask from its own message, never the harness's number."""
    H = [("shop asked", 231698), ("you offered", 208528)]

    def test_the_template_and_its_lowest_price_tail(self):
        self.assertEqual(wt.verified_ask(None, LOWEST, self.H, goods=True), 231698)
        self.assertEqual(wt.verified_ask(None, "Counteroffer: 231698.", self.H, goods=True), 231698)

    def test_the_metadata_ask_wins(self):
        self.assertEqual(wt.verified_ask(231000, "whatever 5", self.H, goods=True), 231000)

    def test_one_price_left_after_the_fields_the_shops_write(self):
        text = "208528 is too low. Counteroffer: 231698. Includes a 6-hour warranty. This is my lowest price."
        self.assertEqual(wt.verified_ask(None, text, self.H, goods=True), 231698)

    def test_a_number_with_no_ask_word_or_another_unit_is_not_read(self):
        for text in ("We hold at 231698. This is my lowest price.", "Counteroffer: 231698. Grade C refurbished.",
                     "Counteroffer: 231698 for the box-damaged one."):
            self.assertIsNone(wt.verified_ask(None, text, self.H, goods=True), text)

    def test_texts_that_can_mean_another_price_are_not_read(self):
        for text in ("Was 240000, now 231698.", "231698 minus 2000 if you pay today.", "Others ask 231698.",
                     "Counteroffer: 231698 each.", "Counteroffer: 231698. Or 225000 for grade C.",
                     "Counteroffer: 231698, no warranty.", "Counteroffer: two hundred thousand.", ""):
            self.assertIsNone(wt.verified_ask(None, text, self.H, goods=True), text)

    def test_never_over_the_shops_earlier_ask_nor_at_or_under_my_offer(self):
        self.assertIsNone(wt.verified_ask(None, "Counteroffer: 231699. This is my lowest price.", self.H, goods=True))
        self.assertIsNone(wt.verified_ask(None, "Counteroffer: 208528. This is my lowest price.", self.H, goods=True))

    def test_goods_text_without_a_warranty_is_never_read_even_with_metadata(self):
        self.assertIsNone(wt.verified_ask(231000, "Sold as-is, no warranty.", self.H, goods=True))


class Decide(unittest.TestCase):
    H = [("shop asked", 7500), ("you offered", 6120), ("shop asked", 7300), ("you offered", 6799)]

    def test_boundaries(self):
        for ask, want in ((6799, None), (6800, 6800), (7293, 7293), (7294, None)):
            self.assertEqual(wt.decide(ask, None, f"Counteroffer: {ask}. This is my lowest price.", self.H, 6799, 7293),
                             want, ask)

    def test_off_or_no_room_is_none(self):
        self.assertIsNone(wt.decide(7200, None, "Counteroffer: 7200.", self.H, 6799, None))
        self.assertIsNone(wt.decide(7200, None, "Counteroffer: 7200.", self.H, 6799, 6799))

    def test_no_offer_of_mine_yet_is_none(self):
        self.assertIsNone(wt.decide(7200, None, "Counteroffer: 7200.", [("shop asked", 7500)], 6799, 7293))

    def test_an_early_walk_before_i_offered_my_limit_is_none(self):
        # review 576e714 I2: the harness may walk on any turn; my own steps up to the limit come first
        self.assertIsNone(wt.decide(7200, None, "Counteroffer: 7200.", [("shop asked", 7500), ("you offered", 5000)],
                                    6799, 7293))

    def test_another_unit_is_none(self):
        for text in ("Grade C unit at 7200.", "Counteroffer: 7200 for a grade C unit.", "7200 for the refurbished unit.",
                     "Display model, 7200.", "Rent it for 7200.", "7200 for the empty box.", "Add 7200 to your offer.",
                     "Starting from 7200", "Different unit G1234abcd at 7200.", "7200 gets you the floor sample."):
            self.assertIsNone(wt.decide(7200, None, text, self.H, 6799, 7293), text)

    def test_the_moss_final_price_is_taken(self):
        self.assertEqual(wt.decide(7200, None, "Four offers left. Final price: 7200. ", self.H, 6799, 7293), 7200)


class Main(test_buyer_margin_cap.Run, unittest.TestCase):
    """market_buyer.main: a grade A unit of a 10,000 list, limit 6799, take 7293 (test_buyer_margin_cap.MainTake)."""
    TEXT = "컵 (G1), grade A, 6 h warranty: asking 6500"
    HIST = [("shop asked", 7500), ("you offered", 6120), ("shop asked", 7300), ("you offered", 6799)]

    def test_take_off_passes_no_hook(self):
        _, calls = self.run_main({"enabled": True})
        self.assertNotIn("walk_take", calls[0][1])

    def test_take_on_the_hook_takes_up_to_the_cap_and_the_row_says_so(self):
        got = {}

        def neg(*a, **k):
            got["over"] = k["walk_take"](7294, None, "Counteroffer: 7294. This is my lowest price.", self.HIST, "harness")
            got["in"] = k["walk_take"](7200, None, "Counteroffer: 7200. This is my lowest price.", self.HIST, "harness")
            k["walk_take"].sent(7200, "harness")
            rec = {"item": "G1", "price": 7200, "sig_seller": "s", "task": "t", "shop": "s", "order": "o-1",
                   "warranty_hours": 6}
            return {"state": "TASK_STATE_COMPLETED", "task": "t", "agreed": 7200, "holds": 0, "receipt": rec,
                    "history": self.HIST + [("shop asked", 7200), ("you offered", 7200)]}
        row, _ = self.run_main({"enabled": True, "take": True}, talk=neg)
        self.assertEqual((got["over"], got["in"]), (None, 7200))
        self.assertEqual(row["margin_cap"]["walk_take"],
                         {"taken": 7200, "limit": 6799, "ceiling": 7293, "turn": "harness"})
        self.assertEqual(row["margin_cap"]["taken"], 7200)
        self.assertFalse(any("over my limit" in w for w in row["warnings"]), row["warnings"])


class Template(unittest.TestCase):
    def test_the_lowest_price_tail_is_left_to_the_harness_and_read_here(self):
        # oneshot's TEMPLATE stays as it was (buyer_firm_trial's tests and the expired firm study use this text as a
        # harness turn); the hook reads it on its own
        h = [("shop asked", 231698), ("you offered", 208528)]
        self.assertIsNone(oneshot.text_price(LOWEST, h))
        self.assertEqual(wt.verified_ask(None, LOWEST, h, goods=True), 231698)


if __name__ == "__main__":
    unittest.main()
