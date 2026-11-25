"""outer.derive: the events no agent writes as it acts, derived from the kit's files and the ledger.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import json
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, paths  # noqa: E402
from core.events import EventReader, EventWriter  # noqa: E402
from outer import derive  # noqa: E402
from tools.query import opening_template  # noqa: E402
from tools.registry import call  # noqa: E402
from tests.test_calc_check import NOW, Base  # noqa: E402

H = 3600


def unit(uid, kind="goods", cost=1000, grade="A", expires_at=None, sku="G0026722f"):
    return {"unit": uid, "sku": sku, "kind": kind, "cost": cost, "grade": grade, "expires_at": expires_at}


class DeriveTest(Base):
    def setUp(self):
        super().setUp()
        self.w = EventWriter("collect", source="outer")
        self.state = {}

    def tearDown(self):
        self.w.close()
        super().tearDown()

    def kinds(self):
        self.w.close()
        self.w = EventWriter("collect", source="outer")
        return [(e["kind"], e["data"]) for e in EventReader("t", "outer").iter_new()]

    def test_inventory_bought_expired_seized_storage(self):
        inv = {"bankruptcies_left": 2, "units": [unit("g1", cost=10000), unit("g2"), unit("f1", "food", 500, expires_at=NOW + H, sku="G0011e806")]}
        out = derive.inventory(self.state, self.w, inv, NOW)
        self.assertEqual(out["bought"], 3)
        # an hour later: f1 is past its date and gone, g2 shows expired grade? (goods do not) - g2 sold, g1 stays
        s = EventWriter("seller", source="seller")
        s.emit("seller.settled", {"price": 1500, "cost": 1000}, unit="g2")
        s.emit("seller.closed", {"outcome": "completed", "cost": 700, "sku": "G0026722f"}, unit="g9")
        s.emit("seller.settled", {"price": 900, "cost": 700}, unit="g9")      # bought and sold between rounds
        s.close()
        inv2 = {"bankruptcies_left": 2, "units": [unit("g1", cost=10000)]}
        out = derive.inventory(self.state, self.w, inv2, NOW + 2 * H)
        self.assertEqual((out["bought"], out["expired"], out["storage"]), (1, 1, round(10000 * 0.01 * 2 / 24 + 0)))
        out = derive.inventory(self.state, self.w, {"bankruptcies_left": 1, "units": []}, NOW + 3 * H)
        self.assertEqual(out["seized"], 1)
        got = self.kinds()
        exp = [d for k, d in got if k == "loss.expired"]
        self.assertEqual([d["cost"] for d in exp], [500])
        self.assertTrue(all(d["estimated"] for k, d in got if k == "loss.storage"))
        self.assertEqual(sum(d["qty"] for k, d in got if k == "restock.bought"), 4)

    def test_payments_and_votes(self):
        lines = [{"order": "o1", "shop": "s", "item": "G1", "price": 100, "paid": True, "ledger": 5},
                 {"order": "o2", "shop": "s", "item": "G1", "price": 90, "paid": False, "reason": "no certificate"}]
        (self.market / "payments.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        self.assertEqual(derive.payments(self.state, self.w, self.market), 2)
        self.assertEqual(derive.payments(self.state, self.w, self.market), 0)
        me = {"join_name": "gom31-honest-shop", "card_name": "gom31 Honest Shop"}
        posts = [{"id": 7, "author": "gom31-honest-shop", "role": "buyer", "likes": 2, "dislikes": 0},
                 {"id": 8, "author": "elm", "likes": 9}]
        atomic.write_json(self.market / "forum.json", posts)
        self.assertEqual(derive.forum_votes(self.state, self.w, self.market, me), 1)
        self.assertEqual(derive.forum_votes(self.state, self.w, self.market, me), 0)     # unchanged
        got = self.kinds()
        self.assertEqual([d["paid"] for k, d in got if k == "buyer.payment"], [True, False])
        self.assertEqual([d["likes"] for k, d in got if k == "forum.votes"], [2])

    def test_refund_only_for_our_shop(self):
        ours = lambda url, name: name == "gom31 Honest Shop"
        self.assertEqual(derive.refund({"kind": "broken", "seq": 3, "data": {"shop_name": "gom31 Honest Shop",
                                                                           "refund": 4500, "buyer": "kim"}}, self.w, ours), 4500)
        self.assertIsNone(derive.refund({"kind": "broken", "data": {"shop_name": "elm", "refund": 1}}, self.w, ours))
        self.assertEqual([d["amount"] for k, d in self.kinds() if k == "seller.refund"], [4500])

    def test_fingerprints_and_guess(self):
        s = EventWriter("seller", source="seller")
        o = EventWriter("collect", source="outer")
        for i, (name, text) in enumerate([("kim", "I want to buy G0011e806 for 21000"), ("kim", "I want to buy G002c5cfb for 9000"),
                                          ("lee", "hello shop, price for G0011e806?")]):
            s.emit("seller.request", {"excerpt": derive.MARKER + text, "first": True}, task=f"t{i}")
            s.emit("guess.made", {"task": f"t{i}", "top": "kim"}, task=f"t{i}")
            o.emit("guess.confirmed", {"task": f"t{i}", "actual": name}, task=f"t{i}")
        s.close()
        o.close()
        r = derive.fingerprints(NOW)
        self.assertEqual((r["participants"], r["accuracy"]), (2, round(2 / 3, 3)))
        self.assertEqual(opening_template("I want to buy G0011e806 for 21,000"), "i want to buy <sku> for <n>")
        g = call(self.ctx, "calc.guess_counterpart", {"task": "x", "messages": ["I want to buy G00200492 for 50000"]})
        self.assertEqual(g["result"]["candidates"][0]["name"], "kim")


if __name__ == "__main__":
    unittest.main()
