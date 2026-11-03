"""Round 3 wiring against kit v4: the kit's own buyer code (scripts/market_kit.py, read only) is the reference.

    python -m unittest test_round3 -v        (from submissions/26622007/week-08)

There is no Round 3 market to try before it opens, so the market here is a stub: real seller.py over real HTTP,
real buyer.negotiate, the kit's real acp_pay and check_accept, a fake /api/settle. The harness is replaced by a
rule, so no key is needed. Whether the real market accepts all this shows only when Round 3 opens.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import httpx
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "scripts"))
os.chdir(HERE)
import acp_shop                    # noqa: E402
import buyer                       # noqa: E402
import market_buyer                # noqa: E402
import market_kit as kit           # noqa: E402
import restock                     # noqa: E402
import review                      # noqa: E402
import seller                      # noqa: E402
import shop_memory                 # noqa: E402
from catalog import Catalog        # noqa: E402
from shop_stock import StockBook, markdown   # noqa: E402

MARKET_KEY = Ed25519PrivateKey.generate()
MARKET_PUB = base64.b64encode(MARKET_KEY.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def unit(uid="u1", sku="G1", cost=6000, list_price=10000, grade="A", kind="food", hours=500.0, category="식품>김치>김치"):
    expires = time.time() + hours * 3600 if kind == "food" else None
    body = {"unit": uid, "sku": sku, "grade": grade, "expires_at": expires}
    return {"unit": uid, "sku": sku, "name": f"포기김치 10kg {sku}", "category": category, "list_price": list_price,
            "kind": kind, "cost": cost, "grade": grade, "supplied_at": time.time() - 3600, "expires_at": expires,
            "cert": {"cert": body, "sig": base64.b64encode(MARKET_KEY.sign(kit.canon(body))).decode()}}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StockBookTest(unittest.TestCase):
    def book(self, *units, rnd=3):
        return StockBook(lambda: {"round": rnd, "units": list(units)})

    def test_economy_starts_at_round_3(self):
        self.assertFalse(self.book(rnd=2).economy())
        self.assertTrue(self.book(rnd=3).economy())
        self.assertFalse(StockBook(lambda: {}).economy())

    def test_named_sku_comes_first_then_first_to_expire(self):
        b = self.book(unit("late", "G1", hours=500), unit("soon", "G1", hours=5), unit("other", "G2", hours=1))
        self.assertEqual(b.find("I want to buy kimchi (G1)")["unit"], "soon")
        self.assertEqual(b.find("김치 please")["unit"], "other")        # category: first to expire of any SKU
        self.assertIsNone(b.find("a sofa"))

    def test_expired_held_and_sold_units_are_not_offered(self):
        gone = dict(unit("old", "G1", hours=-1))
        b = self.book(gone, unit("a", "G1"), unit("b", "G1", hours=600))
        self.assertEqual(b.find("G1")["unit"], "a")
        b.hold("a")
        self.assertEqual(b.find("G1")["unit"], "b")
        b.mark_sold("b")
        self.assertIsNone(b.find("G1"))
        b.release("a")
        self.assertEqual(b.find("G1")["unit"], "a")

    def test_a_hold_runs_out(self):
        b = self.book(unit("a", "G1"))
        b.hold("a")
        b.held["a"] -= 10000
        self.assertEqual(b.find("G1")["unit"], "a")

    def test_ship_follows_the_promised_unit_and_the_acceptable_list(self):
        b = self.book(unit("a", "G1"), unit("b", "G2"))
        self.assertEqual(b.ship({"item": "G1", "unit": "a"}, ["G1"])[0]["unit"], "a")
        self.assertIsNotNone(b.ship({"item": "G1", "unit": "a"}, ["G2"])[1])     # promised unit does not fit the order
        self.assertEqual(b.ship({"item": "김치"}, ["G2"])[0]["unit"], "b")
        self.assertIsNotNone(b.ship({"item": "G9"}, ["G9"])[1])


class MarkdownTest(unittest.TestCase):
    NOW = 1800000000.0

    def food(self, left, supplied_ago=500):
        return {"cost": 1000, "list_price": 2000, "supplied_at": self.NOW - supplied_ago * 3600, "expires_at": self.NOW + left * 3600}

    def goods(self, days):
        return {"cost": 1000, "list_price": 2000, "supplied_at": self.NOW - days * 86400, "expires_at": None}

    def floor(self, u, ends=None):
        return markdown(u, None, ends, self.NOW)

    def test_fresh_stock_keeps_its_cost(self):
        self.assertEqual(self.floor(self.food(100))[:2], (1000, 1.0))
        self.assertEqual(self.floor(self.goods(1))[:2], (1000, 1.0))

    def test_food_falls_toward_its_date(self):
        self.assertEqual(self.floor(self.food(12))[0], 500)
        self.assertEqual(self.floor(self.food(0.1))[0], 200)      # never under the share of list: min_floor_share
        self.assertIn("shelf life", self.floor(self.food(12))[2])

    def test_a_short_lived_food_is_not_cleared_on_arrival(self):
        self.assertEqual(self.floor(self.food(20, supplied_ago=4))[1], 1.0)      # 24 h life: clears in its last 12 h
        self.assertEqual(self.floor(self.food(6, supplied_ago=18))[1], 0.5)

    def test_goods_fall_after_they_sit(self):
        self.assertEqual(self.floor(self.goods(3))[1], 1.0)
        self.assertAlmostEqual(self.floor(self.goods(8))[1], 0.5)
        self.assertEqual(self.floor(self.goods(100))[0], 200)

    def test_the_end_of_the_round_clears_everything_and_the_lowest_wins(self):
        ends = self.NOW + 24 * 3600
        self.assertEqual(self.floor(self.goods(1), ends)[0], 500)
        self.assertEqual(self.floor(self.food(6), ends)[0], 250)
        self.assertEqual(self.floor(self.goods(1), self.NOW - 1)[0], 200)
        self.assertEqual(self.floor(self.goods(1), self.NOW + 49 * 3600)[1], 1.0)

    def test_the_floor_is_never_above_a_low_cost(self):
        cheap = dict(self.goods(100), cost=100, list_price=2000)
        self.assertEqual(self.floor(cheap)[0], 100)


class OpeningTest(unittest.TestCase):
    def test_arms_are_shares_of_list_and_never_below_cost(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(shop_memory, "MEMORY", os.path.join(d, "m.json")), \
                mock.patch.object(shop_memory, "reviews", lambda: (None, None)), \
                mock.patch.object(shop_memory, "market_means", lambda: {}), \
                mock.patch.object(shop_memory, "review_stars", lambda: {}):
            for _ in range(40):
                price, arm, _why = shop_memory.choose_opening("김치", 6000, 10000)
                self.assertIn(arm, [0.7, 0.8, 0.9, 1.0])
                self.assertEqual(price, round(10000 * arm))
                self.assertEqual(shop_memory.choose_opening("김치", 9500, 10000)[0] >= 9500, True)


class EndToEnd(unittest.TestCase):
    """seller.py <- buyer.negotiate, then the kit's acp_pay -> our checkout -> a stub of the market's /api/settle."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.units = [unit("u-food", "G1"), unit("u-goods", "G2", kind="goods", grade="B", category="가전>청소기")]
        self.key = Ed25519PrivateKey.generate()
        self.settled = []
        patches = [
            mock.patch.object(shop_memory, "MEMORY", os.path.join(self.tmp.name, "seller.json")),
            mock.patch.object(shop_memory, "reviews", lambda: (None, None)),
            mock.patch.object(shop_memory, "market_means", lambda: {}),
            mock.patch.object(shop_memory, "review_stars", lambda: {}),
            mock.patch.object(seller, "load_shop_key", lambda path="": self.key),
            mock.patch.object(seller, "ask", self.shop_rule),
            mock.patch.object(buyer, "ask", self.buyer_rule),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        cat = Catalog("catalog.json")
        self.stock = StockBook(lambda: {"round": 3, "units": list(self.units)})
        self.acp = acp_shop.AcpCheckout(self.url, lambda: self.key, lambda m: None, stock=self.stock,
                                        transport=httpx.MockTransport(self.market))
        with mock.patch.object(seller, "AcpCheckout", lambda *a, **k: self.acp), \
                mock.patch.object(seller, "StockBook", lambda: self.stock):
            app = seller.build(self.url + "/", cat, "Nocha Test")
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        self.addCleanup(setattr, self.server, "should_exit", True)

    def market(self, request):
        body = json.loads(request.content)
        co = kit.jwt_payload(body["checkout_jwt"], kit.pub_b64(self.key))
        self.settled.append(co)
        return httpx.Response(200, json={"settled": True, "ledger": 7, "mandate_receipt": {
            "ledger": 7, "unit": co.get("unit"), "name": "x", "grade": "A", "claim": co.get("claim"), "claim_honest": True}})

    @staticmethod
    def shop_rule(prompt, schema, system, tag=""):
        offer = int(prompt.rsplit("<<<\n", 1)[1].split("\n")[0])
        return {"buyer_offer": offer, "action": "accept", "price": offer, "message": "Deal."}

    @staticmethod
    def buyer_rule(prompt, schema, system, tag=""):
        ask = int(prompt.split("current price is ")[1].split(".")[0])
        budget = int(prompt.split("Your budget: ")[1].split(".")[0])
        return {"shop_price": ask, "action": "offer", "price": min(ask, budget)}

    def order(self, acceptable, kind="food", use=48):
        sign = lambda m: {"mandate": m, "sig": "s"}
        return {"order": "o-1", "item": "김치", "budget": 20000, "acceptable": acceptable, "kind": kind, "shelf_hours": 720,
                "use_within_hours": use, "market_pubkey": MARKET_PUB,
                "mandates": {"checkout": sign({"vct": "mandate.checkout.open.1"}),
                             "payment": sign({"vct": "mandate.payment.open.1", "constraints": [
                                 {"type": "payment.amount_range", "max": 20000}]})}}

    def deal(self, sku, limit=10000):
        out = buyer.negotiate(self.url, "포기김치", limit, "", f"I want to buy 포기김치 10kg {sku} ({sku})")
        if out["receipt"]:       # A2A data Parts carry numbers as doubles; kit.handle_order turns the price back to int
            out["receipt"]["price"] = int(out["receipt"]["price"])
        return out

    def test_food_deal_goes_all_the_way_with_a_checkable_certificate(self):
        out = self.deal("G1")
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        rec = out["receipt"]
        self.assertEqual((rec["item"], rec["unit"], rec["claim"]), ("G1", "u-food", "A"))
        self.assertTrue(kit.verify_seller(kit.pub_b64(self.key), rec))
        why = []
        mr = kit.acp_pay(self.order(["G1"]), self.url, kit.pub_b64(self.key), rec, Ed25519PrivateKey.generate(),
                         {"cert": True, "min_hours_left": 48.1}, why)
        self.assertEqual(why, [])
        self.assertEqual(mr["ledger"], 7)
        co = self.settled[0]
        self.assertEqual((co["item"], co["unit"], co["claim"], co["price"]), ("G1", "u-food", "A", rec["price"]))
        self.assertIn("cert", co)
        self.assertNotIn("warranty_hours", co)
        self.assertIn("u-food", self.stock.sold)
        self.assertIsNone(self.stock.find("G1"))

    def test_terms_come_from_the_seller_not_the_buyers_copy(self):
        rec = self.deal("G1")["receipt"]
        forged = dict(rec, unit="u-goods", claim="C", warranty_hours=99)
        kit.acp_pay(self.order(["G1", "G2"]), self.url, kit.pub_b64(self.key), forged, Ed25519PrivateKey.generate())
        self.assertEqual((self.settled[0]["unit"], self.settled[0]["claim"]), ("u-food", "A"))
        self.assertNotIn("warranty_hours", self.settled[0])

    def test_kit_refuses_food_that_will_not_last_and_the_unit_comes_back(self):
        self.units[0] = unit("u-food", "G1", hours=10)
        rec = self.deal("G1")["receipt"]
        why = []
        mr = kit.acp_pay(self.order(["G1"]), self.url, kit.pub_b64(self.key), rec, Ed25519PrivateKey.generate(),
                         {"cert": True, "min_hours_left": 48.1}, why)
        self.assertIsNone(mr)
        self.assertIn("48.1", why[0])
        self.assertEqual(self.settled, [])
        self.assertEqual(self.stock.find("G1")["unit"], "u-food")

    def test_warranty_only_on_goods_and_only_when_configured(self):
        with mock.patch.object(shop_memory, "load_pricing",
                               lambda: {**shop_memory.DEFAULT_PRICING, "warranty_hours": 6, "list_arms": [1.0]}):
            rec = self.deal("G2")["receipt"]
            self.assertEqual(rec["warranty_hours"], 6)
            self.assertNotIn("warranty_hours", self.deal("G1")["receipt"])

    def test_warranty_on_request_is_granted_with_the_break_chance_priced_in(self):
        with mock.patch.object(seller, "break_chance", lambda sku: 0.1):
            out = buyer.negotiate(self.url, "청소기", 10000, "", "I want to buy (G2) with a 6 h warranty")
        rec = out["receipt"]
        self.assertEqual(rec["warranty_hours"], 6)
        self.assertGreaterEqual(int(rec["price"]), 6667)      # cost 6000 / (1 - 0.1)
        self.assertEqual(shop_memory.load()["negotiations"][-1]["warranty"], 6)

    def test_warranty_is_declined_when_the_sku_breaks_too_often(self):
        with mock.patch.object(seller, "break_chance", lambda sku: 0.5):
            out = buyer.negotiate(self.url, "청소기", 10000, "", "I want to buy (G2) with a warranty")
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertNotIn("warranty_hours", out["receipt"])

    def test_food_never_gets_a_warranty(self):
        out = buyer.negotiate(self.url, "포기김치", 10000, "", "I want to buy (G1) with a warranty")
        self.assertNotIn("warranty_hours", out["receipt"])

    def test_a_unit_near_its_date_sells_below_cost_and_stays_out_of_the_bandit(self):
        self.units[0] = dict(unit("u-food", "G1", hours=6), supplied_at=time.time() - 500 * 3600)
        out = buyer.negotiate(self.url, "포기김치", 4000, "", "I want to buy (G1)")
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertLess(out["receipt"]["price"], 6000)
        got = shop_memory.load()["negotiations"][-1]
        self.assertEqual((got["cost"], got["arm"]), (6000, None))
        self.assertLess(got["reserve"], 6000)

    def test_unit_is_not_promised_twice_and_out_of_stock_is_refused(self):
        self.deal("G1")
        self.assertEqual(self.deal("G1")["state"], "TASK_STATE_REJECTED")


class RoundTwoStillWorks(EndToEnd):
    """Kit v4 trades as in Round 2 until Round 3 opens, and the shop goes on selling from catalog.json until then."""

    def setUp(self):
        super().setUp()
        self.stock.load = lambda: {"round": 2, "units": []}

    @staticmethod
    def shop_rule(prompt, schema, system, tag=""):
        if "ask" in schema["properties"]:
            return {"ask": 100, "message": "Welcome."}
        return EndToEnd.shop_rule(prompt, schema, system, tag)

    def test_lamp_deal_has_no_unit(self):
        out = buyer.negotiate(self.url, "lamp", 150)
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        rec = dict(out["receipt"], price=int(out["receipt"]["price"]))
        self.assertEqual(rec["item"], "lamp")
        self.assertNotIn("unit", rec)
        order = dict(self.order([]), budget=150)
        order["mandates"]["payment"]["mandate"]["constraints"][0]["max"] = 150
        self.assertEqual(kit.acp_pay(order, self.url, kit.pub_b64(self.key), rec, Ed25519PrivateKey.generate())["ledger"], 7)
        self.assertNotIn("unit", self.settled[0])
        self.assertEqual(self.settled[0]["item"], "lamp")

    test_food_deal_goes_all_the_way_with_a_checkable_certificate = None
    test_terms_come_from_the_seller_not_the_buyers_copy = None
    test_kit_refuses_food_that_will_not_last_and_the_unit_comes_back = None
    test_warranty_only_on_goods_and_only_when_configured = None
    test_warranty_on_request_is_granted_with_the_break_chance_priced_in = None
    test_warranty_is_declined_when_the_sku_breaks_too_often = None
    test_food_never_gets_a_warranty = None
    test_unit_is_not_promised_twice_and_out_of_stock_is_refused = None
    test_a_unit_near_its_date_sells_below_cost_and_stays_out_of_the_bandit = None


class BuyerTest(unittest.TestCase):
    CATALOG = {"G1": {"list_price": 10000, "shelf_hours": 720}, "G2": {"list_price": 20000, "shelf_hours": 24},
               "G3": {"list_price": 15000, "shelf_hours": 720}, "G4": {"list_price": 90000}}
    ORDER = {"order": "o-1", "item": "김치", "budget": 16000, "acceptable": ["G1", "G2", "G3"], "kind": "food",
             "use_within_hours": 48}

    def offers(self, *skus):
        return {"offers": [{"sku": s, "name": s, "qty": 1} for s in skus]}

    def test_offers_must_fit_the_order_and_last(self):
        fit = market_buyer.usable_offers(self.offers("G1", "G2", "G4"), self.ORDER, self.CATALOG)
        self.assertEqual([o["sku"] for o in fit], ["G1"])
        self.assertEqual(market_buyer.usable_offers({"offers": [{"sku": "G1", "qty": 0}]}, self.ORDER, self.CATALOG), [])

    def test_picks_the_most_valuable_sku_the_budget_covers(self):
        fit = market_buyer.usable_offers(self.offers("G1", "G3"), self.ORDER, self.CATALOG)
        self.assertEqual(market_buyer.pick_offer(fit, 16000)["sku"], "G3")
        self.assertEqual(market_buyer.pick_offer(fit, 12000)["sku"], "G1")
        self.assertEqual(market_buyer.pick_offer(fit, 5000)["sku"], "G1")

    def test_accept_asks_proof_only_for_food(self):
        self.assertEqual(market_buyer.accept_policy(self.ORDER), {"cert": True, "min_hours_left": 48.1})
        self.assertIsNone(market_buyer.accept_policy({"kind": "goods"}))

    def test_receipt_checks(self):
        out = {"agreed": 9000, "task": "t", "asked": "G1"}
        rec = {"item": "G1", "price": 9000, "task": "t", "sig_seller": "x"}
        check = lambda r, o=out: market_buyer.check_receipt(r, o, "김치", 16000, self.ORDER, self.CATALOG)
        self.assertEqual(check(rec), ([], []))
        self.assertEqual(check(dict(rec, item="G3")), ([], ["asked for G1, the receipt names G3"]))
        self.assertIn("does not fit", check(dict(rec, item="G9"))[0][0])
        self.assertIn("over the list price", check(dict(rec, price=11000), dict(out, agreed=11000))[0][0])

    def test_kit_refusals_reach_memory(self):
        memory = {"trades": [{"order": "o-1", "shop": "s", "item": "G1", "result": "deal"},
                             {"order": "o-2", "shop": "s", "item": "G1", "result": "deal"}]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "payments.jsonl")
            with open(path, "w") as f:
                f.write('{"order": "o-1", "paid": false, "reason": "no certificate"}\n{"order": "o-2", "paid": true, "ledger": 3}\nnot json\n')
            market_buyer.sync_payments(memory, path)
        self.assertEqual(memory["trades"][0]["result"], "unpaid")
        self.assertEqual(memory["trades"][0]["unpaid_reason"], "no certificate")
        self.assertEqual((memory["trades"][1]["result"], memory["trades"][1]["paid"]), ("deal", True))
        self.assertTrue(market_buyer.unreachable([{"result": "unpaid"}, {"result": "unpaid"}]))

    def run_main(self, shops, order=None):
        printed = []
        with tempfile.TemporaryDirectory() as d:
            sp, op = os.path.join(d, "shops.json"), os.path.join(d, "order.json")
            with open(sp, "w") as f:
                json.dump(shops, f)
            with open(op, "w") as f:
                json.dump(order or self.ORDER, f)
            with open(os.path.join(d, "catalog.json"), "w") as f:
                json.dump(self.CATALOG, f)
            result = {"state": "TASK_STATE_COMPLETED", "task": "t", "history": [("shop asked", 11000)], "agreed": 11000,
                      "holds": 0, "receipt": {"item": "G1", "price": 11000, "task": "t", "sig_seller": "x"}}
            calls = []

            def neg(*a):
                calls.append(a)
                return dict(result)

            with mock.patch.object(market_buyer, "MEMORY", os.path.join(d, "m.json")), \
                    mock.patch.object(market_buyer, "market_round", lambda: 3), \
                    mock.patch.object(market_buyer, "serves_acp", lambda u: True), \
                    mock.patch.object(market_buyer, "choose", lambda s, m, i, b: (s[0], "t")), \
                    mock.patch.object(market_buyer, "negotiate", neg), \
                    mock.patch("builtins.print", lambda *a, **k: printed.append(a[0])), \
                    mock.patch.object(sys, "argv", ["x", "--shops", sp, "--item", "김치", "--budget", "16000", "--order", op]):
                market_buyer.main()
        return json.loads(printed[-1]), calls

    def test_main_asks_for_the_sku_within_its_value_and_withholds_a_bad_receipt(self):
        shops = [dict(self.offers("G1"), name="s", url="https://s.example", reviews=[])]
        out, calls = self.run_main(shops)
        self.assertEqual(calls[0][1:3], ("G1", 10000))             # limit is the list price, under the budget
        self.assertIn("(G1)", calls[0][4])
        self.assertIsNone(out["receipt"])                          # price over the list price: the kit must not pay
        self.assertEqual(out["stars"], 1)
        self.assertEqual(out["accept"], {"cert": True, "min_hours_left": 48.1})

    def test_main_with_no_stock_anywhere_closes_the_order_without_stars(self):
        out, calls = self.run_main([dict(self.offers("G4"), name="s", url="https://s.example")])
        self.assertEqual(calls, [])
        self.assertEqual(out, {"shop": "", "receipt": None})


class HooksTest(unittest.TestCase):
    CATALOG = {"F1": {"kind": "food", "category": "식품>김치", "list_price": 20000, "shelf_hours": 720},
               "F2": {"kind": "food", "category": "식품>김치", "list_price": 90000, "shelf_hours": 720},
               "F3": {"kind": "food", "category": "식품>우유", "list_price": 3000, "shelf_hours": 24},
               "H1": {"kind": "goods", "category": "가전>컵", "list_price": 40000},
               "H2": {"kind": "goods", "category": "가구>소파", "list_price": 900000}}
    DEMAND = [{"category": c, "median_price": 20000} for c in ("식품>김치", "식품>우유", "가전>컵", "가구>소파")]
    CFG = dict(restock.DEFAULTS)

    def test_first_stock_is_long_shelf_food_and_cheap_goods_near_the_median(self):
        out = restock.plan({"balance": 5000000, "units": []}, self.CATALOG, self.DEMAND, self.CFG)
        self.assertEqual(sorted(b["sku"] for b in out["buy"]), ["F1", "H1"])
        self.assertTrue(all(b["qty"] == 2 for b in out["buy"]))

    def test_only_what_is_missing_and_never_past_the_cash_line(self):
        units = [{"unit": "a", "category": "식품>김치", "grade": "A"}, {"unit": "b", "category": "식품>김치", "grade": "A"},
                 {"unit": "c", "category": "가전>컵", "grade": "A"}]
        out = restock.plan({"balance": 5000000, "units": units}, self.CATALOG, self.DEMAND, self.CFG)
        self.assertEqual(out["buy"], [{"sku": "H1", "qty": 1}])
        self.assertEqual(restock.plan({"balance": 1500000, "units": []}, self.CATALOG, self.DEMAND, self.CFG)["buy"], [])

    def test_expired_units_do_not_count_and_files_when_stuck(self):
        units = [{"unit": "a", "category": "식품>김치", "grade": "expired"}]
        got = restock.plan({"balance": 5000000, "units": units}, self.CATALOG, self.DEMAND, self.CFG)["buy"]
        self.assertIn({"sku": "F1", "qty": 2}, got)
        stuck = {"balance": 100, "bankrupt_line": 500000, "bankruptcies_left": 2, "units": []}
        self.assertTrue(restock.plan(stuck, self.CATALOG, self.DEMAND, self.CFG)["bankrupt"])
        self.assertNotIn("bankrupt", restock.plan(dict(stuck, bankruptcies_left=0), self.CATALOG, self.DEMAND, self.CFG))

    def test_review_rates_the_facts(self):
        s = {"name": "n", "grade": "A", "value": 10000, "claim_honest": True}
        self.assertEqual(review.rate(7000, s)[0], 5)
        self.assertEqual(review.rate(9000, s)[0], 4)
        self.assertEqual(review.rate(10000, s)[0], 3)
        self.assertEqual(review.rate(11000, s)[0], 2)
        self.assertEqual(review.rate(7000, dict(s, value=0))[0], 1)
        self.assertEqual(review.rate(7000, dict(s, claim_honest=False))[0], 1)

    def test_review_hook_prints_for_the_kit_and_remembers_the_deal(self):
        with tempfile.TemporaryDirectory() as d:
            deal, kept = os.path.join(d, "deal.json"), os.path.join(d, "settled.jsonl")
            with open(deal, "w") as f:
                json.dump({"order": {"order": "o-1"}, "receipt": {"price": 7000},
                           "settled": {"shop": "https://s", "sku": "G1", "name": "n", "value": 10000, "warranty_hours": 6,
                                       "ledger": 9}}, f)
            r = subprocess.run([sys.executable, "review.py", "--deal", deal], capture_output=True, text=True,
                               env={**os.environ, "BUYER_SETTLED": kept}, cwd=HERE)
            self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["stars"], 5)
            with open(kept) as f:
                row = json.loads(f.readline())
            self.assertEqual((row["order"], row["shop"], row["warranty_hours"], row["ledger"]), ("o-1", "https://s", 6, 9))

    def test_agent_json_gives_the_kit_what_it_fills(self):
        with open("agent.json", encoding="utf-8") as f:
            a = json.load(f)
        self.assertIn("{order}", a["market_buyer"]["cmd"])
        self.assertIn("{state}", a["restock"]["cmd"])
        self.assertIn("{deal}", a["review"]["cmd"])


if __name__ == "__main__":
    unittest.main()
