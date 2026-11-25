"""buyer.talk take_at: a metadata ask within it is offered back at once, with no harness call; otherwise the talk goes on."""
import unittest
from unittest import mock

import buyer


def task(state, ask=None, receipt=None):
    t = {"id": "t-1", "status": {"state": state, "message": {"parts": [{"text": f"asking {ask}"}],
                                                          **({"metadata": {"ask": ask}} if ask is not None else {})}}}
    if receipt:
        t["artifacts"] = [{"parts": [{"data": receipt}]}]
    return t


class TakeAt(unittest.TestCase):
    def test_ask_within_is_taken_without_the_harness(self):
        sent = []

        def send(url, text, task_id=None):
            sent.append(text)
            return task("TASK_STATE_COMPLETED", receipt={"price": int(text), "item": "G1"})
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", side_effect=AssertionError("no harness")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 9000), "김치", 10000, "", {"history": [], "agreed": None, "holds": 0},
                             take_at=10000)
        self.assertEqual(sent, ["9000"])
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(out["agreed"], 9000)
        self.assertTrue(out["took"])

    def test_ask_over_take_at_goes_to_the_harness(self):
        calls = []

        def decide(*a, **k):
            calls.append(a)
            return {"shop_price": 12000, "action": "walk_away", "price": 0}
        with mock.patch.object(buyer, "send", lambda *a, **k: None), mock.patch.object(buyer, "decide", decide), \
                mock.patch.object(buyer, "rpc", lambda *a: task("TASK_STATE_CANCELED")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 12000), "김치", 10000, "", {"history": [], "agreed": None, "holds": 0},
                             take_at=10000)
        self.assertEqual(len(calls), 1)
        self.assertFalse(out.get("took"))

    def test_take_at_never_above_budget(self):
        with mock.patch.object(buyer, "send", lambda *a, **k: None), \
                mock.patch.object(buyer, "decide", return_value={"shop_price": 10500, "action": "walk_away", "price": 0}), \
                mock.patch.object(buyer, "rpc", lambda *a: task("TASK_STATE_CANCELED")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 10500), "김치", 10000, "", {"history": [], "agreed": None, "holds": 0},
                             take_at=11000)
        self.assertFalse(out.get("took"))

    def test_no_metadata_goes_to_the_harness(self):
        with mock.patch.object(buyer, "send", lambda *a, **k: None), \
                mock.patch.object(buyer, "decide", return_value={"shop_price": 9000, "action": "walk_away", "price": 0}) as d, \
                mock.patch.object(buyer, "rpc", lambda *a: task("TASK_STATE_CANCELED")):
            t = task("TASK_STATE_INPUT_REQUIRED")
            t["status"]["message"]["parts"] = [{"text": "asking 9000"}]
            buyer.talk("u", t, "김치", 10000, "", {"history": [], "agreed": None, "holds": 0}, take_at=10000)
        self.assertEqual(d.call_count, 1)

    def test_same_ask_after_taking_it_goes_to_the_harness(self):
        sent = []

        def send(url, text, task_id=None):
            sent.append(text)
            return task("TASK_STATE_INPUT_REQUIRED", 9000)
        with mock.patch.object(buyer, "send", send), \
                mock.patch.object(buyer, "decide", return_value={"shop_price": 9000, "action": "walk_away", "price": 0}) as d, \
                mock.patch.object(buyer, "rpc", lambda *a: task("TASK_STATE_CANCELED")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 9000), "김치", 10000, "", {"history": [], "agreed": None, "holds": 0},
                             take_at=10000)
        self.assertEqual(sent, ["9000"])
        self.assertEqual(d.call_count, 1)
        self.assertIsNone(out["agreed"])
        self.assertEqual(out["history"][:2], [("shop asked", 9000), ("you offered", 9000)])

    def test_lower_ask_after_taking_is_taken_again(self):
        sent, answers = [], [task("TASK_STATE_INPUT_REQUIRED", 8500), task("TASK_STATE_COMPLETED", receipt={"price": 8500})]

        def send(url, text, task_id=None):
            sent.append(text)
            return answers.pop(0)
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", side_effect=AssertionError("no harness")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 9000), "김치", 10000, "", {"history": [], "agreed": None, "holds": 0},
                             take_at=10000)
        self.assertEqual(sent, ["9000", "8500"])
        self.assertEqual(out["agreed"], 8500)


class MarketBuyerPassesTakeAtForFoodOnly(unittest.TestCase):
    def test_source_sets_take_at_only_for_food(self):
        import ast
        import inspect
        import market_buyer
        src = inspect.getsource(market_buyer.main)
        calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == 'negotiate']
        self.assertEqual(len(calls), 1)
        actual = next(k.value for k in calls[0].keywords if k.arg == 'take_at')
        expression = compile(ast.Expression(actual), '<actual take_at argument>', 'eval')
        # Execute the actual call argument: punctuation/extra keyword arguments are not policy.
        cases = [(True, True, 'food', 9000, 10000, 6500),
                 (True, True, 'food', 5000, 10000, 5000),
                 (True, True, 'food', 9000, 5400, 3510),
                 (True, True, 'goods', 9000, 10000, None),
                 (False, True, 'food', 9000, 10000, None),
                 (True, False, 'food', 9000, 10000, None)]
        for r3, enabled, kind, limit, lp, expected in cases:
            with self.subTest(r3=r3, enabled=enabled, kind=kind, limit=limit, lp=lp):
                actual_value = eval(expression, {'__builtins__': {'min': min, 'int': int}},
                    {'r3': r3, 'FOOD_TAKE_ASK': enabled, 'order': {'kind': kind},
                     'limit': limit, 'offer': {'list_price': lp}, 'FOOD_TAKE_SHARE': market_buyer.FOOD_TAKE_SHARE})
                self.assertEqual(actual_value, expected)
        self.assertIn('limit = min(value_limit(offer, args.budget, chance, buyer_cfg()), saved_cap(offer, chance)) if r3', src)


class SavedCap(unittest.TestCase):
    """Patch 3.4 Saved: a deal must be expected to keep SAVED_MIN_SHARE of list; food is taken at once only at FOOD_TAKE_SHARE."""

    def test_food_cap_is_68_percent_of_list(self):
        import market_buyer as mb
        self.assertEqual(mb.SAVED_MIN_SHARE, 0.32)
        self.assertEqual(mb.saved_cap({"list_price": 10000}, None), 6799)
        self.assertEqual(mb.saved_cap({"list_price": 10000}, 0), 6799)

    def test_goods_cap_counts_the_refunded_breaks(self):
        import market_buyer as mb
        self.assertEqual(mb.saved_cap({"list_price": 100000}, 0.10), 64444)   # (1 - 0.1)(1 - p/L) >= 0.32
        self.assertEqual(mb.saved_cap({"list_price": 100000}, 0.5), 36000)
        self.assertEqual(mb.saved_cap({"list_price": 100000}, 0.10, 0.30), 66666)
        self.assertEqual(mb.saved_cap({"list_price": 100000}, 0.75), 0)
        self.assertEqual(mb.saved_cap({"list_price": 100000}, 1.0), 0)

    def test_food_take_share_is_below_the_cap(self):
        import market_buyer as mb
        self.assertEqual(mb.FOOD_TAKE_SHARE, 0.65)
        self.assertLess(mb.FOOD_TAKE_SHARE, 1 - mb.SAVED_MIN_SHARE)

    def test_best_quote_ranks_by_share_kept_not_money(self):
        import market_buyer as mb
        cheap = {"name": "a", "usable": [{"sku": "S1", "name": "snack", "qty": 1, "list_price": 10000}]}
        dear = {"name": "b", "usable": [{"sku": "S2", "name": "sofa", "qty": 1, "list_price": 1000000}]}
        asks = {"a": 6000, "b": 740000}     # a keeps 40% (4,000), b keeps 26% (260,000)

        def quote(s, name, sku, order, got, ctl):
            got[s["name"]] = {"ask": asks[s["name"]], "url": "u", "task": "t"}
        with mock.patch.object(mb, "quote", quote), mock.patch.object(mb, "cancel_quote", lambda *a: None), \
                mock.patch.object(mb, "log", lambda *a: None):
            best, got = mb.best_quote(dear, [cheap], {}, {}, 10 ** 7, lambda sku: None, {"quote_shops": 2}, wait=1)
        self.assertEqual(best["name"], "a")


class TextAsk(unittest.TestCase):
    """13:30 cycle: shops that write their ask only in the text ("asking 9708") were dropped as "no ask" at the quote."""

    def test_reads_one_asking_number(self):
        import buyer
        self.assertEqual(buyer.text_ask("국산콩 두부과자: asking 9708. SKU G762d9d25; grade B"), 9708)
        self.assertEqual(buyer.text_ask("(sku=G9a240832), asking price 6802. Remaining shelf life is 347.72h"), 6802)
        self.assertEqual(buyer.text_ask("A등급 상품입니다. Asking 1,590,000."), 1590000)

    def test_review_cases(self):
        import buyer
        self.assertEqual(buyer.text_ask("asking price is 1,590,000 for grade C"), 1590000)
        self.assertIsNone(buyer.text_ask("asking 9,708.60 KRW"))             # a decimal ask is not read
        self.assertEqual(buyer.text_ask("asking ₩12,000원"), 12000)
        self.assertEqual(buyer.text_ask("asking 9708 KRW"), 9708)
        self.assertIsNone(buyer.text_ask("asking 12.5%"))
        self.assertIsNone(buyer.text_ask("asking 12.5 units"))
        self.assertIsNone(buyer.text_ask("asking 1.5k"))
        self.assertEqual(buyer.text_ask("Asking: 12199. Send your offer as a number."), 12199)
        import time
        t0 = time.time()
        self.assertIsNone(buyer.text_ask("asking" + " " * 50000 + "5"))     # read only the first 2000 characters
        self.assertLess(time.time() - t0, 1)
        self.assertIsNone(buyer.text_ask("asking 10% off the list"))
        self.assertIsNone(buyer.text_ask("I am asking 2 units at 9000"))
        self.assertIsNone(buyer.text_ask("asking 1,2,3"))
        self.assertIsNone(buyer.text_ask("not asking 5000"))
        self.assertIsNone(buyer.text_ask("asking G762d9d25"))

    def test_no_or_two_asks_is_none(self):
        import buyer
        self.assertIsNone(buyer.text_ask("grade B, 571 h of shelf life left. List price 28900"))
        self.assertIsNone(buyer.text_ask("asking 9000, or asking 8000 for two"))
        self.assertIsNone(buyer.text_ask(""))
        self.assertEqual(buyer.text_ask("asking 9000. Reply ok to take the ask: 9000"), 9000)   # the same number twice

    def test_checked_against_the_list_price(self):
        import market_buyer as mb
        shop = {"_offer": {"list_price": 28900}}
        self.assertEqual(mb.text_ask_checked("asking 20000", shop), 20000)
        self.assertIsNone(mb.text_ask_checked("asking 1000", shop))          # under 5% of list: a misread
        self.assertIsNone(mb.text_ask_checked("asking 30000", shop))         # over list
        self.assertIsNone(mb.text_ask_checked("asking 20000", {}))           # no list price to check it with
        shop = {"_offer": {"list_price": 28900, "sku": "G07ec17d5"}}
        self.assertEqual(mb.text_ask_checked("매트리스 (G07ec17d5): asking 20000", shop), 20000)
        self.assertIsNone(mb.text_ask_checked("마녀스프 (G5e83d126): asking 20000", shop))   # another SKU's price
        self.assertIsNone(mb.text_ask_checked("G07ec17d5 none; G5e83d126 asking 20000", shop))  # two SKUs: whose price?

    def test_a_receipt_over_my_limit_is_not_paid(self):
        import market_buyer as mb
        offer = {"sku": "G4c4977ca", "list_price": 18120}
        self.assertIn("over my limit", mb.over_limit({"item": "G4c4977ca", "price": 13532.0}, offer, 13415, {}, 13600))
        self.assertIsNone(mb.over_limit({"item": "G4c4977ca", "price": 13400.0}, offer, 13415, {}, 13600))
        cat = {"G0df4f56f": {"list_price": 14900}}
        self.assertIn("over my limit", mb.over_limit({"item": "G0df4f56f", "price": 11324.0}, offer, 13415, cat, 20000))
        self.assertIsNone(mb.over_limit({"item": "Gffffffff", "price": 11324.0}, offer, 13415, cat, 20000))   # unknown SKU
        # an unknown SKU's list price cannot raise the limit: the asked SKU's limit holds, not the budget
        self.assertIn("over my limit", mb.over_limit({"item": "Gffffffff", "price": 14000.0}, offer, 13415, cat, 20000))
        self.assertIsNone(mb.over_limit({"item": "G4c4977ca"}, offer, 13415, {}, 13600))

    def test_a_metadata_ask_wins_a_tie_with_a_text_ask(self):
        import market_buyer as mb
        a = {"name": "a", "usable": [{"sku": "S1", "name": "snack", "qty": 1, "list_price": 10000}]}
        b = {"name": "b", "usable": [{"sku": "S1", "name": "snack", "qty": 1, "list_price": 10000}]}
        closed = []

        def quote(s, name, sku, order, got, ctl):
            got[s["name"]] = {"ask": 6000, "ask_src": "text" if s["name"] == "a" else "metadata", "url": "u", "task": "t"}
        with mock.patch.object(mb, "quote", quote), mock.patch.object(mb, "cancel_quote", lambda q, n: closed.append(n)), \
                mock.patch.object(mb, "log", lambda *a: None):
            best, got = mb.best_quote(a, [b], {}, {}, 10 ** 7, lambda sku: None, {"quote_shops": 2}, wait=1)
        self.assertEqual(best["name"], "b")
        self.assertTrue(got["a"]["closed"])

    def test_a_cheaper_text_ask_still_wins(self):
        import market_buyer as mb
        a = {"name": "a", "usable": [{"sku": "S1", "name": "snack", "qty": 1, "list_price": 10000}]}
        b = {"name": "b", "usable": [{"sku": "S1", "name": "snack", "qty": 1, "list_price": 10000}]}

        def quote(s, name, sku, order, got, ctl):
            got[s["name"]] = {"ask": 5000 if s["name"] == "a" else 7000, "ask_src": "text" if s["name"] == "a" else "metadata",
                              "url": "u", "task": "t"}
        with mock.patch.object(mb, "quote", quote), mock.patch.object(mb, "cancel_quote", lambda *a: None), \
                mock.patch.object(mb, "log", lambda *a: None):
            best, _ = mb.best_quote(b, [a], {}, {}, 10 ** 7, lambda sku: None, {"quote_shops": 2}, wait=1)
        self.assertEqual(best["name"], "a")

    def quote(self, task, offer=True):
        import market_buyer as mb
        card = mock.Mock(**{"json.return_value": {"supportedInterfaces": [
            {"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "http://s/rpc"}]}})
        got = {}
        shop = {"name": "s", "url": "http://s", **({"_offer": {"list_price": 13900}} if offer else {})}
        with mock.patch.object(mb, "serves_acp", lambda u: True), mock.patch.object(mb.httpx, "get", lambda *a, **k: card), \
                mock.patch.object(mb, "buyer_send", lambda *a, **k: task), mock.patch.object(mb, "log", lambda *a: None):
            mb.quote(shop, "두부과자", "G762d9d25", {"kind": "food"}, got)
        return got["s"]

    def test_quote_uses_the_text_ask_when_metadata_has_none(self):
        q = self.quote({"status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                   "message": {"parts": [{"text": "두부과자: asking 9708. Send your offer as a number."}]}}})
        self.assertEqual((q["ask"], q["ask_src"]), (9708, "text"))

    def test_metadata_ask_wins_over_the_text(self):
        q = self.quote({"status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                   "message": {"metadata": {"ask": 9000}, "parts": [{"text": "asking 9708"}]}}})
        self.assertEqual((q["ask"], q["ask_src"]), (9000, "metadata"))

    def test_without_an_offer_or_out_of_range_the_text_ask_is_only_recorded(self):
        t = {"status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"parts": [{"text": "asking 9708"}]}}}
        q = self.quote(t, offer=False)
        self.assertEqual((q["ask"], q["ask_src"], q["text_ask_raw"]), (None, "text_rejected", 9708))
        t = {"status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"parts": [{"text": "asking 99999"}]}}}
        q = self.quote(t)
        self.assertEqual((q["ask"], q["ask_src"]), (None, "text_rejected"))

    def test_a_closed_task_has_no_ask_even_with_a_number_in_text(self):
        q = self.quote({"status": {"state": "TASK_STATE_COMPLETED", "message": {"parts": [{"text": "asking 9708"}]}}})
        self.assertEqual((q["ask"], q["ask_src"]), (None, None))


if __name__ == "__main__":
    unittest.main()
