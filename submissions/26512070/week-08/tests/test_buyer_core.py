"""agents.buyer_core with a scripted model and a scripted shop (no network, no model).

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import json
import os
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from agents.buyer_core import BuyerCore  # noqa: E402
from core import paths, reflex  # noqa: E402
from core.events import EventReader  # noqa: E402
from tests.test_calc_check import NOW, Base  # noqa: E402

ORDER = {"order": "o1", "item": "음료", "budget": 22000, "acceptable": ["G0011e806"], "use_within_hours": 48}
SHOPS = [{"name": "elm", "url": "http://elm", "reviews": [{"stars": 5, "review": "Deal at 18000; receipt matched."}],
          "offers": [{"sku": "G0011e806", "qty": 3}]},
         {"name": "oak", "url": "http://oak", "reviews": [], "offers": [{"sku": "G0026722f", "qty": 1}]}]


class Shop:
    """Answers each message from a script: (state, text, receipt)."""
    def __init__(self, script):
        self.script, self.sent, self.canceled = list(script), [], False

    def __call__(self, url):
        self.url = url
        return self

    def send(self, text, timeout=None):
        self.sent.append(text)
        return self.script.pop(0)

    def cancel(self):
        self.canceled = True


def receipt(price):
    return {"shop": "elm", "item": "G0011e806", "price": price, "task": "t", "sig_seller": "sig", "cert": True}


class BuyerCoreTest(Base):
    def setUp(self):
        super().setUp()
        shutil.copytree(HERE / "card", paths.root() / "card")
        os.environ["MARKET_DIR"] = str(self.market)

    def tearDown(self):
        os.environ.pop("MARKET_DIR", None)
        super().tearDown()

    def run_core(self, answers, shop, shops=SHOPS):
        it = iter(answers)
        core = BuyerCore(dict(ORDER), shops, model_call=lambda p, s, t: next(it), connect=shop, clock=lambda: NOW)
        try:
            return core.run()
        finally:
            core.close()

    def test_honest_deal_with_accept_policy(self):
        shop = Shop([("TASK_STATE_INPUT_REQUIRED", "Asking 21000.", None),
                     ("TASK_STATE_COMPLETED", "Deal at 18000.", receipt(18000))])
        out = self.run_core(['{"tool": "act.pick", "args": {"shop": "elm", "sku": "G0011e806"}}',
                             '{"tool": "act.offer", "args": {"price": 18000}}',
                             '{"tool": "act.finish", "args": {"accept": {"cert": true, "min_hours_left": 48}}}'], shop)
        self.assertEqual((out["shop"], out["receipt"]["price"], out["accept"]), ("http://elm", 18000,
                                                                                  {"cert": True, "min_hours_left": 48}))
        self.assertTrue(shop.sent[0].startswith("I want to buy"))
        kinds = [e["kind"] for e in EventReader("t", "buyer").iter_new()]
        self.assertEqual(kinds, ["buyer.order", "buyer.pick", "buyer.decision", "buyer.closed"])
        self.assertEqual(reflex.load()["shops"]["elm"]["fair"], 1)

    def test_cheating_receipt_goes_out_as_null_and_blocks(self):
        shop = Shop([("TASK_STATE_INPUT_REQUIRED", "Asking 21000.", None),
                     ("TASK_STATE_COMPLETED", "Deal.", receipt(21000))])
        out = self.run_core(['{"tool": "act.pick", "args": {"shop": "elm", "sku": "G0011e806"}}',
                             '{"tool": "act.offer", "args": {"price": 18000}}'], shop)
        self.assertIsNone(out["receipt"])
        self.assertIn("elm", reflex.blocked())
        out = self.run_core([], Shop([]))                         # next order: elm is left out, oak has nothing
        self.assertEqual(out["shop"], "")

    def test_walk_away_and_bad_model(self):
        shop = Shop([("TASK_STATE_INPUT_REQUIRED", "Asking 90000.", None)])
        out = self.run_core(["junk", "junk", "junk", '{"tool": "act.walk_away", "args": {"why": "too dear"}}'], shop)
        self.assertIsNone(out["receipt"])
        self.assertTrue(shop.canceled)
        picks = [e["data"] for e in EventReader("t", "buyer").iter_new() if e["kind"] == "buyer.pick"]
        self.assertEqual((picks[0]["shop"], picks[0]["guard"]), ("elm", "no valid pick from the model"))

    def test_offer_over_budget_is_refused_then_corrected(self):
        shop = Shop([("TASK_STATE_INPUT_REQUIRED", "Asking 25000.", None),
                     ("TASK_STATE_COMPLETED", "Deal at 22000.", receipt(22000))])
        out = self.run_core(['{"tool": "act.pick", "args": {"shop": "elm", "sku": "G0011e806"}}',
                             '{"tool": "act.offer", "args": {"price": 25000}}',
                             '{"tool": "act.offer", "args": {"price": 22000}}',
                             '{"tool": "act.finish", "args": {"accept": {}}}'], shop)
        self.assertEqual(out["receipt"]["price"], 22000)
        self.assertEqual(shop.sent[1], "Offer: 22000.")

    def test_receipt_before_any_offer_is_not_paid(self):
        shop = Shop([("TASK_STATE_COMPLETED", "Sold at 21000.", receipt(21000))])
        out = self.run_core(['{"tool": "act.pick", "args": {"shop": "elm", "sku": "G0011e806"}}'], shop)
        self.assertIsNone(out["receipt"])

    def test_surplus_first_prices_and_food_targets(self):
        # user 11/3 23:4x: each shop's market median share of list, and a target on food orders too
        import json as _json
        from agents import buyer_core
        from core import atomic
        (paths.root() / ".market").mkdir(exist_ok=True)
        (paths.root() / ".market" / "catalog.json").write_text(_json.dumps(
            {"Gx": {"sku": "Gx", "list_price": 10000, "kind": "goods"}}), encoding="utf-8")
        (paths.memory() / "market").mkdir(parents=True, exist_ok=True)
        rows = [{"kind": "settle", "data": {"shop": "https://cheap.example/", "sku": "Gx", "amount": a}} for a in (5000, 6000, 7000)]
        (paths.memory() / "market" / "ledger-20261103.jsonl").write_text(
            "".join(_json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        got = buyer_core.shop_prices([{"name": "cheap", "url": "https://cheap.example"}, {"name": "new", "url": "https://x"}], now=NOW)
        self.assertEqual(got, {"cheap": {"median_share": 0.6, "deals": 3, "goods_warranty_share": 0.0}, "new": None})
        core = BuyerCore.__new__(BuyerCore)
        core.ctx = type("C", (), {"strategy": {"buyer": {"big_list": 30000, "target_share": 0.5, "open_share": 0.4,
                                                          "food_target_share": 0.6, "food_open_share": 0.45}}})()
        self.assertIn("about 6000 (60% of list)", core.big_goods_line({"list_price": 10000}, "food")[0])
        self.assertIn("about 20000 (50% of list)", core.big_goods_line({"list_price": 40000}, "goods")[0])
        core.ctx.strategy = {"buyer": {"big_list": 30000}}
        self.assertEqual(core.big_goods_line({"list_price": 10000}, "food"), [])
    def test_rules_mode_haggles_without_the_model(self):
        # 11/4 03:2x (HARNESS 14 step 4, benchmark myeonggi): offers by rule, no model call at all
        from core import atomic
        path = paths.memory() / "strategy" / "active.json"
        st = atomic.read_json(path)
        st["buyer"] = {"big_list": 30000, "target_share": 0.5, "open_share": 0.4,
                       "food_target_share": 0.6, "food_open_share": 0.45}
        st["playbook"] = {"buyer": {"mode": "rules", "ceiling_share": 0.8}}
        atomic.write_json(path, st)
        shop = Shop([("TASK_STATE_INPUT_REQUIRED", "Asking 21000.", None),
                     ("TASK_STATE_INPUT_REQUIRED", "Asking 14000.", None),
                     ("TASK_STATE_INPUT_REQUIRED", "Asking 13900.", None),
                     ("TASK_STATE_COMPLETED", "Deal at 13900.", receipt(13900))])

        def no_model(p, s, t):
            raise AssertionError("the model was called in rules mode")
        core = BuyerCore(dict(ORDER), SHOPS, model_call=no_model, connect=shop, clock=lambda: NOW)
        try:
            out = core.run()
        finally:
            core.close()
        self.assertEqual(shop.sent[1:], ["I can pay 10454 KRW.", "I can pay 11470 KRW.", "I can pay 13900 KRW."])
        self.assertEqual((out["receipt"]["price"], out["accept"]), (13900, {"min_hours_left": 48}))
    def test_rules_mode_tries_the_next_shop(self):
        # 11/4 03:1x: a shop that refuses at the first message no longer ends the order
        from core import atomic
        path = paths.memory() / "strategy" / "active.json"
        st = atomic.read_json(path)
        st["buyer"] = {"big_list": 30000, "target_share": 0.5, "open_share": 0.4,
                       "food_target_share": 0.6, "food_open_share": 0.45}
        st["playbook"] = {"buyer": {"mode": "rules", "ceiling_share": 0.8}}
        atomic.write_json(path, st)
        two = [dict(SHOPS[0]), dict(SHOPS[0], name="oak", url="http://oak")]
        shop = Shop([("TASK_STATE_REJECTED", "Sorry, none in stock.", None),
                     ("TASK_STATE_INPUT_REQUIRED", "Asking 13000.", None),
                     ("TASK_STATE_COMPLETED", "Deal at 13000.", receipt(13000))])

        def no_model(p, s, t):
            raise AssertionError("the model was called in rules mode")
        core = BuyerCore(dict(ORDER), two, model_call=no_model, connect=shop, clock=lambda: NOW)
        try:
            out = core.run()
        finally:
            core.close()
        self.assertEqual((out["receipt"]["price"], len([t for t in shop.sent if t.startswith("I want to buy")])), (13000, 2))
    def shelf_run(self, guard):
        # 11/4 21:1x design 1: SKU shelf 96 h, the order needs 48 h - grade C keeps at most 43 h, B up to 77 h
        from core import atomic
        path = paths.memory() / "strategy" / "active.json"
        st = atomic.read_json(path)
        st["buyer"] = {"food_target_share": 0.6, "food_open_share": 0.45}
        st["playbook"] = {"buyer": {"mode": "rules", "ceiling_share": 0.8, "shelf_guard": guard}}
        atomic.write_json(path, st)
        two = [dict(SHOPS[0]), dict(SHOPS[0], name="oak", url="http://oak")]
        shop = Shop([("TASK_STATE_INPUT_REQUIRED", "Asking 13000.", None),
                     ("TASK_STATE_COMPLETED", "Deal at 13000.", dict(receipt(13000), claim="C")),
                     ("TASK_STATE_INPUT_REQUIRED", "Asking 13500.", None),
                     ("TASK_STATE_COMPLETED", "Deal at 13500.", dict(receipt(13500), shop="oak", claim="B"))])
        core = BuyerCore(dict(ORDER), two, model_call=None, connect=shop, clock=lambda: NOW)
        try:
            return core.run()
        finally:
            core.close()

    def test_shelf_guard_leaves_a_receipt_that_cannot_last(self):
        out = self.shelf_run(True)
        self.assertEqual((out["receipt"]["price"], out["receipt"]["claim"]), (13500, "B"))
        closed = [e["data"]["verdict"] for e in EventReader("t2", "buyer").iter_new() if e["kind"] == "buyer.closed"]
        self.assertTrue(closed[0].startswith("no deal: shelf - grade C keeps at most 43 h of 96 h"), closed)

    def test_shelf_guard_off_keeps_todays_behaviour(self):
        out = self.shelf_run(False)
        self.assertEqual((out["receipt"]["price"], out["receipt"]["claim"]), (13000, "C"))

    def test_skip_unsigned_leaves_out_a_shop_that_never_signs(self):
        # 11/5 01:4x: willow-goods sent 44 receipts without sig_seller in an hour; skipped, retried after an hour
        for i in range(3):
            reflex.record_outcome("elm", "http://elm", "no deal: the receipt has no sig_seller", 13000, 13000,
                                  f"o{i}", NOW - 60 * (3 - i))
        self.assertEqual(reflex.unsigned(NOW), {"elm"})
        self.assertEqual(reflex.unsigned(NOW + reflex.UNSIGNED_RETRY), set())
        from core import atomic
        path = paths.memory() / "strategy" / "active.json"
        st = atomic.read_json(path)
        st["buyer"] = {"food_target_share": 0.6, "food_open_share": 0.45}
        st["playbook"] = {"buyer": {"mode": "rules", "ceiling_share": 0.8, "skip_unsigned": True}}
        atomic.write_json(path, st)
        two = [dict(SHOPS[0]), dict(SHOPS[0], name="oak", url="http://oak")]
        shop = Shop([("TASK_STATE_INPUT_REQUIRED", "Asking 13000.", None),
                     ("TASK_STATE_COMPLETED", "Deal at 13000.", dict(receipt(13000), shop="oak"))])
        core = BuyerCore(dict(ORDER), two, model_call=None, connect=shop, clock=lambda: NOW)
        try:
            out = core.run()
        finally:
            core.close()
        self.assertEqual((shop.url, out["receipt"]["price"]), ("http://oak", 13000))

    def test_goods_order_no_shop_serves_is_recorded(self):
        # 11/4 10:3x: restock stocks what no shop could serve
        order = dict(ORDER, order="o9", item="소파", budget=900000, acceptable=["G0026722f"], kind="goods")
        core = BuyerCore(order, [dict(SHOPS[0], offers=[])], model_call=None, connect=Shop([]), clock=lambda: NOW)
        try:
            out = core.run()
        finally:
            core.close()
        self.assertIsNone(out["receipt"])
        rows = (paths.memory() / "facts" / "unserved.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(rows[-1])["skus"], ["G0026722f"])

if __name__ == "__main__":
    unittest.main()
