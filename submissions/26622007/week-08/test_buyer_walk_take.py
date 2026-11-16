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
        # review c55c66e m4: the take is counted apart from the turns, which still sum to the turns priced (code, harness,
        # harness: the harness call happened on the turn the take answered)
        self.assertEqual(out.get("walk_takes"), 1)
        self.assertNotIn("walk_take", out["turns"])
        self.assertEqual(out["turns"], {"code": 1, "harness": 2})

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

    def test_a_turned_down_take_is_never_sent_again(self):
        # review c55c66e m3 (P4): the shop repeats "Counteroffer: 7200." and the harness my limit 6799; the take went out
        # once, the shop did not close at its own ask, and a limit offer between must not bring the same take back
        seen, sent = [], []
        replies = [task("Counteroffer: 7200.") for _ in range(buyer.MAX_TURNS)]

        def send(url, text, task_id=None):
            sent.append(text)
            return replies.pop(0)
        out = {"state": "", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}
        with mock.patch.object(buyer, "send", send), \
                mock.patch.object(buyer, "decide", lambda *a, **k: {"shop_price": 7200, "action": "offer", "price": 6799}), \
                mock.patch.object(buyer, "log"), mock.patch.object(buyer, "rpc", lambda *a: None):
            buyer.talk("u", task("Counteroffer: 7200."), "컵", 6799, "", out, ceiling=7293,
                       walk_take=wt.hook(6799, 7293, goods=True, record=seen.append))
        self.assertEqual(sent.count("7200"), 1, sent)
        self.assertEqual(sent[:3], ["6799", "7200", "6799"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(out.get("walk_takes"), 1)
        self.assertEqual(sum(out["turns"].values()), len(sent))

    def test_a_held_metadata_ask_is_taken_on_the_stall(self):
        # review c55c66e m4: the stall with the ask in the message metadata (probe P7)
        seen = []
        meta = lambda t, a: task(t, meta=a)  # noqa: E731
        reads = [{"shop_price": 231698, "action": "offer", "price": 212055}] * 2
        sent, out = self.run_talk([meta("Here you go.", 231698), meta("Here you go.", 231698), done(231698)], reads,
                                  wt.hook(self.LIMIT, self.TOP, goods=True, record=seen.append),
                                  first=meta("Counteroffer: 231698.", 231698))
        self.assertEqual(sent, ["208528", "212055", "231698"])
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(seen[0]["turn"], "stall")

    def test_a_code_turn_walk_is_taken(self):
        # review c55c66e m4: code (the ladder) walks away, not the harness (probe P11)
        seen, sent = [], []
        replies = [task(LOWEST), done(231698)]

        def send(url, text, task_id=None):
            sent.append(text)
            return replies.pop(0)

        def ladder(meta, text, history):
            if not history:
                return {"shop_price": 231698, "action": "offer", "price": self.LIMIT}
            return {"shop_price": 231698, "action": "walk_away", "price": None}
        out = {"state": "", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", side_effect=AssertionError), \
                mock.patch.object(buyer, "log"), mock.patch.object(buyer, "rpc", lambda *a: None):
            buyer.talk("u", task("Counteroffer: 231698."), "매트리스", self.LIMIT, "", out, ladder=ladder,
                       ceiling=self.TOP, walk_take=wt.hook(self.LIMIT, self.TOP, goods=True, record=seen.append))
        self.assertEqual(sent, ["212055", "231698"])
        self.assertEqual(seen[0]["turn"], "code")
        self.assertEqual(out["turns"], {"code": 2})

    def test_the_resumed_talk_passes_the_hook(self):
        # review c55c66e m4: buyer.negotiate(resume=...) (the quote main keeps open) hands walk_take to talk
        seen, sent = [], []
        replies = [task(LOWEST), task(LOWEST), done(231698)]
        reads = list(self.READS)

        def send(url, text, task_id=None):
            sent.append(text)
            return replies.pop(0)
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", lambda *a, **k: reads.pop(0)), \
                mock.patch.object(buyer, "log"), mock.patch.object(buyer, "rpc", lambda *a: None):
            out = buyer.negotiate("card", "매트리스", self.LIMIT, resume=("u", task("Counteroffer: 231698.")),
                                  ladder=lambda m, t, h: None if h else {"shop_price": 231698, "action": "offer",
                                                                         "price": 208528},
                                  ceiling=self.TOP, walk_take=wt.hook(self.LIMIT, self.TOP, goods=True, record=seen.append))
        self.assertEqual(sent, ["208528", "212055", "231698"])
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(len(seen), 1)

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

    def test_the_shop_texts_seen_are_still_taken_on_both_paths(self):
        # review c55c66e m1: the allow-list keeps the moss and usagi texts
        for meta in (None, 7200):
            for text in ("Four offers left. Final price: 7200. ", "Counteroffer: 7200. This is my lowest price."):
                self.assertEqual(wt.decide(7200, meta, text, self.H, 6799, 7293), 7200, (meta, text))

    def test_words_outside_the_shops_vocabulary_are_not_read(self):
        # review c55c66e m1: a deny list (OTHER_UNIT) let through any word not on it; the text path now reads only the
        # words the shops write around an ask (ask_read.VOCAB), and the metadata path names these too
        for meta in (None, 7200):
            for tail in ("It is a demo unit.", "Floor unit.", "Pre-owned.", "B-stock.", "Returned unit.", "Frame alone.",
                         "전시 상품입니다.", "중고입니다."):
                text = f"Counteroffer: 7200. {tail}"
                self.assertIsNone(wt.decide(7200, meta, text, self.H, 6799, 7293), (meta, text))
        # the text path's allow-list: a word on no list at all
        self.assertIsNone(wt.decide(7200, None, "Counteroffer: 7200. Shiny.", self.H, 6799, 7293))

    def test_the_quoted_grade_named_again_is_taken_another_grade_is_not(self):
        # review c55c66e m2: shops that write the quote again each turn name its grade; the quote's own grade is the
        # unit the cap was priced for
        texts = ("Counteroffer: 7200. This is my lowest price. grade A.", "Counteroffer: 7200. grade=A",
                 "Grade A: normal. Counteroffer: 7200.", "Inventory condition: A. Counteroffer: 7200.")
        for meta in (None, 7200):
            for text in texts:
                self.assertEqual(wt.decide(7200, meta, text, self.H, 6799, 7293, True, grade="A"), 7200, (meta, text))
                self.assertIsNone(wt.decide(7200, meta, text, self.H, 6799, 7293, True, grade="B"), (meta, text))
                self.assertIsNone(wt.decide(7200, meta, text, self.H, 6799, 7293, True), (meta, text))
            self.assertIsNone(wt.decide(7200, meta, "Counteroffer: 7200. grade A. Or grade C.", self.H, 6799, 7293,
                                        True, grade="A"))
            self.assertIsNone(wt.decide(7200, meta, "Counteroffer: 7200. grade a little damaged.", self.H, 6799, 7293,
                                        True, grade="A"))

    def test_an_ask_the_shop_turned_down_anywhere_before_is_none(self):
        # review c55c66e m3: not only the pair just before
        h = self.H + [("shop asked", 7200), ("you offered", 7200), ("shop asked", 7200), ("you offered", 6799)]
        self.assertIsNone(wt.decide(7200, None, "Counteroffer: 7200.", h, 6799, 7293))


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

    def talk_through(self, texts, reads):
        """main's negotiate kwargs (ceiling, walk_take) into the real buyer.talk; the shop closes at the take."""
        sent, got = [], {}
        rec = {"item": "G1", "price": 7200, "sig_seller": "s", "task": "t", "shop": "s", "order": "o-1",
               "warranty_hours": 6}

        def neg(*a, **k):
            replies = [task(t) for t in texts[1:]] + [done(7200)]
            reads_left = list(reads)
            out = {"state": "", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}

            def send(url, text, task_id=None):
                sent.append(text)
                return replies.pop(0)
            with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", lambda *x, **y: reads_left.pop(0)), \
                    mock.patch.object(buyer, "log"), mock.patch.object(buyer, "rpc", lambda *x: None):
                buyer.talk("u", task(texts[0]), a[1], a[2], "", out, ceiling=k.get("ceiling"), walk_take=k.get("walk_take"))
            got["out"] = out
            if out["state"] == "TASK_STATE_COMPLETED":
                out["receipt"] = dict(rec)
            return out
        row, _ = self.run_main({"enabled": True, "take": True}, talk=neg)
        return row, sent, got["out"]

    def test_the_real_talk_with_mains_hook_takes_and_records(self):
        # review c55c66e m4: buyer.talk itself with the hook main built (not the hook called by hand)
        reads = [{"shop_price": 7200, "action": "offer", "price": 6799}, {"shop_price": 7200, "action": "walk_away", "price": 0}]
        row, sent, out = self.talk_through(["Counteroffer: 7200.", "Counteroffer: 7200. This is my lowest price."], reads)
        self.assertEqual(sent, ["6799", "7200"])
        self.assertEqual(row["margin_cap"]["walk_take"], {"taken": 7200, "limit": 6799, "ceiling": 7293, "turn": "harness"})
        self.assertEqual(row["turns"], {"harness": 2})

    def test_mains_hook_knows_the_quoted_grade(self):
        # review c55c66e m2: the quote is grade A (TEXT); the shop names grade A again and is taken, grade B walks
        reads = [{"shop_price": 7200, "action": "offer", "price": 6799}, {"shop_price": 7200, "action": "walk_away", "price": 0}]
        _, sent, _ = self.talk_through(["Counteroffer: 7200.", "Grade A: normal. Counteroffer: 7200."], reads)
        self.assertEqual(sent, ["6799", "7200"])
        _, sent, out = self.talk_through(["Counteroffer: 7200.", "Grade B: normal. Counteroffer: 7200."], reads)
        self.assertEqual(sent, ["6799"])
        self.assertIsNone(out["agreed"])


class Wrapper(unittest.TestCase):
    """review c55c66e m4: market_buyer.negotiate goes through buyer_goods_take30.negotiate for goods; walk_take reaches
    buyer.negotiate on its fallback and on its arm B path (which rewrites ladder and decision_guard only)."""

    def test_the_goods_take30_wrapper_keeps_walk_take(self):
        import buyer_goods_take30 as gt
        import test_buyer_goods_take30 as tg
        fx = tg.GoodsTake30Test("test_inactive_B_and_later_turn_preserve_treatment_reason")
        fx.setUp()
        self.addCleanup(fx.tmp.cleanup)
        hook = wt.hook(6000, 6500, goods=True)
        got = []
        run = lambda *a, **k: got.append(k.get("walk_take")) or {"history": [], "state": ""}  # noqa: E731
        # fallback: no arm on the order
        gt.negotiate(run, fx.md, dict(fx.order), fx.offer, fx.q, (.9,), fx.shop, "G12345678", "fixture", "goods", 6000,
                     "", resume=("fixture", fx.q["task"]), walk_take=hook)
        # arm B, the window open: the wrapped ladder and guard, and the hook still there
        fx.assign()
        with mock.patch.object(gt.time, "time", return_value=fx.now):
            gt.negotiate(run, fx.md, fx.order, fx.offer, fx.q, (.9,), fx.shop, "G12345678", "fixture", "goods", 6000, "",
                         resume=("fixture", fx.q["task"]), ladder=lambda *a: None, walk_take=hook)
        self.assertEqual(fx.order[gt.KEY]["arm"], "B")
        self.assertEqual(got, [hook, hook])


class Template(unittest.TestCase):
    def test_the_lowest_price_tail_is_left_to_the_harness_and_read_here(self):
        # oneshot's TEMPLATE stays as it was (buyer_firm_trial's tests and the expired firm study use this text as a
        # harness turn); the hook reads it on its own
        h = [("shop asked", 231698), ("you offered", 208528)]
        self.assertIsNone(oneshot.text_price(LOWEST, h))
        self.assertEqual(wt.verified_ask(None, LOWEST, h, goods=True), 231698)


if __name__ == "__main__":
    unittest.main()
