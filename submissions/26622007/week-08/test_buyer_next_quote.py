"""buyer_next_quote: after a failed talk, the next shop's open quote that a talk can close goes on once."""
import os
import time
import unittest
from unittest import mock

import buyer_next_quote as bnq
import buyer_rank_pg
import market_buyer
from test_buyer_margin_cap import Run

ON = {"enabled": True, "take": True}           # the margin cap take: grade A cap 7293 over the limit 6799
NQ_ON = lambda md, kind, env=None: {"enabled": True}   # noqa: E731
CANCELED = {"state": "TASK_STATE_CANCELED", "task": "t", "history": [("shop asked", 7600), ("you offered", 6799),
                                                                   ("shop asked", 7500)],
            "agreed": None, "holds": 0, "receipt": None}


def rec(price, shop="t"):
    return {"item": "G1", "price": price, "sig_seller": "s", "task": "t", "shop": shop, "order": "o-1",
            "warranty_hours": 6}


def deal(price):
    return {"state": "TASK_STATE_COMPLETED", "task": "t", "agreed": price, "holds": 0, "receipt": rec(price),
            "history": [("shop asked", 6500), ("you offered", price)]}


class Main(Run, unittest.TestCase):
    TEXT = "컵 (G1), grade A, 6 h warranty: asking 6500"

    def run2(self, first, second=None, nq=NQ_ON, sw=ON):
        outs = [first, second]

        def talk(*a, **k):
            return dict(outs.pop(0))
        with mock.patch.object(market_buyer.buyer_next_quote, "config", nq):
            return self.run_main(sw, talk=talk)

    def test_off_one_talk_as_before(self):
        row, calls = self.run2(CANCELED, nq=lambda md, kind, env=None: None)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("next_quote", row)

    def test_a_failed_talk_goes_on_with_the_other_open_quote(self):
        row, calls = self.run2(CANCELED, deal(7200))
        self.assertEqual(len(calls), 2)
        first, second = calls[0][0][0], calls[1][0][0]
        self.assertNotEqual(first, second)
        self.assertEqual(calls[1][0][2], 6799)                       # my limit for the next shop, as main's
        self.assertEqual(calls[1][1]["ceiling"], 7293)               # its take
        self.assertEqual(calls[1][1]["resume"][1]["id"], "t")        # the open quote's task
        self.assertIn("walk_take", calls[1][1])
        self.assertEqual(row["shop"], second.split("//")[1].split(".")[0])
        self.assertTrue(row["next_quote"]["used"])
        self.assertEqual(row["next_quote"]["first"]["state"], "TASK_STATE_CANCELED")
        self.assertEqual(row["next_quote"]["first"]["asks"], [7600, 7500])
        self.assertFalse(any("over my limit" in w for w in row["warnings"]), row["warnings"])
        self.assertEqual(row["price"], 7200)

    def test_the_receipt_checks_hold_for_the_next_shop(self):
        row, _ = self.run2(CANCELED, dict(deal(7294), receipt=rec(7294)))
        self.assertTrue(any("over my limit 7293" in w for w in row["warnings"]), row["warnings"])

    def test_a_deal_or_no_talk_keeps_one_talk(self):
        row, calls = self.run2(deal(6799))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("used", row["next_quote"])
        self.assertIn("COMPLETED", row["next_quote"]["skip"])

    def test_without_the_take_the_other_quote_must_fit_the_limit(self):
        # take off: 6500 is within the limit 6799 either way, so the next quote is kept
        row, calls = self.run2(CANCELED, deal(6500), sw={"enabled": True})
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[1][1].get("ceiling"))


class Unit(unittest.TestCase):
    def test_failed_talk(self):
        self.assertTrue(bnq.failed_talk(CANCELED))
        self.assertFalse(bnq.failed_talk(dict(CANCELED, history=[])))                 # no ask: nothing was talked
        self.assertFalse(bnq.failed_talk(dict(CANCELED, state="TASK_STATE_INPUT_REQUIRED")))   # may still close
        self.assertFalse(bnq.failed_talk(dict(CANCELED, state="UNKNOWN_RPC_OUTCOME")))
        self.assertFalse(bnq.failed_talk(dict(CANCELED, state="NO_VALUE")))
        self.assertFalse(bnq.failed_talk(deal(1)))

    def test_switch(self):
        import json, tempfile
        md = tempfile.mkdtemp()
        self.assertIsNone(bnq.config(md, "goods", env={}))
        with open(os.path.join(md, bnq.SWITCH), "w") as f:
            json.dump({"enabled": True, "kinds": ["goods"]}, f)
        self.assertIsNotNone(bnq.config(md, "goods", env={}))
        self.assertIsNone(bnq.config(md, "food", env={}))
        self.assertIsNone(bnq.config(md, "goods", env={"BUYER_NEXT_QUOTE": "0"}))

    def next_talk(self, order=None, out=CANCELED, q=None, started=None):
        s = {"name": "t", "url": "https://t.example", "_offer": {"sku": "G1", "name": "G1", "list_price": 10000}}
        q = q or {"ask": 7000, "text": "G1, grade A: asking 7000", "url": s["url"],
                  "task": {"id": "t", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}}
        nq = {"shop": "t", "ask": q["ask"]}
        first = ({"name": "s"}, {"list_price": 10000}, "G1", 6799)
        with mock.patch.object(market_buyer, "MARGIN_CAP", {"cfg": dict(ON, target=0.75), "stats": None}), \
                mock.patch.object(market_buyer, "selected_offer", lambda s, b: s["_offer"]), \
                mock.patch.object(market_buyer, "ledger_breaks", lambda md, c: None), mock.patch.object(market_buyer.market_model, "break_chance", lambda st, k: None), \
                mock.patch.object(market_buyer, "STARTED", started or time.time()):
            got = market_buyer.next_talk(nq, out, {"kind": "goods", **(order or {})}, {"t": q}, [s], ".", {},
                                         20000, {"trades": []}, first)
        return got, nq

    def test_next_talk_limits(self):
        got, nq = self.next_talk()
        self.assertEqual((got["limit"], got["top"], got["margin_cap"]["take_top"]), (6799, 7293, 7293))
        self.assertEqual(nq["first"]["shop"], "s")

    def test_next_talk_skips(self):
        over = {"ask": 7300, "text": "G1, grade A: asking 7300", "url": "u",
                "task": {"id": "t", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}}
        self.assertEqual(self.next_talk(q=over)[1]["skip"], "over")
        self.assertEqual(self.next_talk(q=dict(over, ask=7000, closed=True))[1]["skip"], "closed")
        self.assertEqual(self.next_talk(started=time.time() - 200)[1]["skip"], "late")
        self.assertEqual(self.next_talk(order={"_goods_risk_sku30": {}})[1]["skip"], "study")
        self.assertIsNone(self.next_talk(order={"_saved_floor": {"band_off": True}})[0])   # 7000 over 6799, no take
        with mock.patch.object(market_buyer.buyer_short_warranty, "hours_for", lambda *a: 2.0):
            self.assertEqual(self.next_talk(order={"_sw": {}})[1]["skip"], "short warranty")
        with mock.patch.object(market_buyer.buyer_short_warranty, "zero_expected", lambda *a: "no warranty"):
            self.assertEqual(self.next_talk(order={"_sw": {}})[1]["skip"], "short warranty")

    def test_rank_pg_learns_the_first_talk_as_a_failure(self):
        now = time.time()
        row = {"ts": now, "shop": "t", "kind": "goods", "order": "o-1", "state": "TASK_STATE_CANCELED", "quotes": [],
               "seconds": {}, "price": None, "passed_to_kit": False,
               "next_quote": {"shop": "t", "used": True, "first": {"shop": "s", "state": "TASK_STATE_CANCELED"}}}
        st = buyer_rank_pg.stats_from_rows([row], {}, now)
        self.assertEqual(st["shops"]["s|goods"]["failed"], 1)
        self.assertEqual(st["shops"]["t|goods"]["failed"], 1)
        self.assertEqual(st["pool"]["goods"]["n"], 2)
        unused = dict(row, next_quote={"shop": "t", "skip": "over"})
        self.assertNotIn("s|goods", buyer_rank_pg.stats_from_rows([unused], {}, now)["shops"])


if __name__ == "__main__":
    unittest.main()
