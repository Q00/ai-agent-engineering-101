"""outer.collect with a fake market: our sales get their cost, facts and the board snapshot.

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

from core import atomic, paths  # noqa: E402
from core.events import EventReader, EventWriter  # noqa: E402
from outer import collect  # noqa: E402
from tests.test_calc_check import NOW, Base  # noqa: E402


def result(seq, shop, card, buyer, sku, price, task, ts=NOW - 600):
    return {"seq": seq, "ts": ts, "kind": "result", "data": {"buyer": buyer, "order": f"o{seq}", "shop": shop,
            "receipt": {"shop": card, "item": sku, "price": price, "task": task}, "stars": 5}}


class FakeMarket:
    def __init__(self, rows, board=None):
        self.rows, self.board, self.calls = rows, board, []

    def get(self, path, **params):
        self.calls.append((path, params))
        if path == "/api/ledger":
            after = params["after"]
            if after == -1:
                return self.rows[-1:]
            return [r for r in self.rows if r["seq"] > after][:collect.PAGE]
        if path == "/api/board":
            if self.board is None:
                raise RuntimeError("502")
            return self.board
        raise AssertionError(path)


class CollectTest(Base):
    def setUp(self):
        super().setUp()
        shutil.copytree(HERE / "config", paths.root() / "config")
        os.environ["MARKET_DIR"] = str(self.market)
        w = EventWriter("seller", source="seller")
        w.emit("seller.closed", {"outcome": "completed", "price": 3000, "cost": 2500}, task="t-ours", unit="u-goods")
        w.close()

    def tearDown(self):
        os.environ.pop("MARKET_DIR", None)
        super().tearDown()

    def test_round_records_our_sale_and_facts(self):
        rows = [{"seq": 9, "ts": NOW - 900, "kind": "join", "data": {"name": "gom31-certified-goods", "role": "shop",
                                                                     "url": "https://our.tunnel"}},
                result(10, "https://elm", "Elm", "kim", "G0011e806", 18000, "t-x"),
                result(11, "https://our.tunnel", "gom31 Certified Safe Goods", "kim", "G0026722f", 3000, "t-ours"),
                {"seq": 12, "ts": NOW - 300, "kind": "order", "data": {"order": "o12", "buyer": "lee"}}]
        board = {"round": 3, "shops": [{"name": "gom31-certified-goods", "deals": 1, "stars": 5}, {"name": "elm", "deals": 9, "stars": 4}],
                 "buyers": [], "players": [{"owner": {"login": "gom31"}, "all": 120}, {"owner": {"login": "x"}, "all": 80}]}
        m = FakeMarket(rows, board)
        c = collect.Collector(market=m, clock=lambda: NOW)
        try:
            out = c.round(start=8)
        finally:
            c.close()
        self.assertEqual((out["rows"], out["our_sales"], out["cursor"], out["board"]), (4, 1, 12, True))
        ev = {e["kind"]: e for e in EventReader("t", "outer").iter_new()}
        self.assertEqual((ev["seller.settled"]["data"]["cost"], ev["seller.settled"]["keys"]["counterpart"]), (2500, "kim"))
        self.assertEqual(ev["guess.confirmed"]["data"]["actual"], "kim")
        live = atomic.read_json(paths.memory() / "facts" / "live.json")
        self.assertEqual(live["selling"]["deals"], 2)
        self.assertEqual(live["customer"]["kim"]["deals_with_us"], 1)
        self.assertNotIn("elm", [n for n in live["customer"]])
        sb = atomic.read_json(paths.memory() / "facts" / "scoreboard.json")
        self.assertEqual((sb["shop"]["deals"], sb["player"]["all"], sb["player_percentiles"]["all"]), (1, 120, 75.0))   # 11/4: ties count half (the board ranks so)
        kept = (paths.memory() / "market").glob("ledger-*.jsonl")
        self.assertEqual(sum(len(f.read_text(encoding="utf-8").splitlines()) for f in kept), 4)

    def test_rerun_counts_nothing_twice_and_board_502_is_skipped(self):
        rows = [result(10, "https://elm", "Elm", "kim", "G0011e806", 18000, "t-x")]
        for _ in range(2):
            c = collect.Collector(market=FakeMarket(rows), clock=lambda: NOW)
            try:
                out = c.round(start=9)
            finally:
                c.close()
        self.assertEqual(out["rows"], 0)
        self.assertTrue(out["board"].startswith("skipped"))
        self.assertEqual(atomic.read_json(paths.memory() / "facts" / "live.json")["selling"]["deals"], 1)

    def test_new_round_restarts_the_counts(self):
        r2 = dict(result(10, "https://elm", "Elm", "kim", "lamp", 100, "t-a"), round=2)
        r3 = dict(result(11, "https://elm", "Elm", "kim", "G0011e806", 18000, "t-b"), round=3)
        r3["data"]["verified"] = True
        unpaid = dict(result(12, "https://elm", "Elm", "lee", "G0011e806", 18000, "t-c"), round=3)  # not verified
        settle = {"seq": 13, "ts": NOW - 600, "kind": "settle", "round": 3,      # the same deal as r3, again
                  "data": {"order": "o11", "buyer": "kim", "shop": "https://elm", "item": "G0011e806", "amount": 18000}}
        c = collect.Collector(market=FakeMarket([r2, r3, unpaid, settle]), clock=lambda: NOW)
        try:
            c.round(start=9)
        finally:
            c.close()
        live = atomic.read_json(paths.memory() / "facts" / "live.json")
        self.assertEqual((live["round"], live["selling"]["deals"], [s["sku"] for s in live["selling"]["skus"]]),
                         (3, 1, ["G0011e806"]))

    def test_match_broken(self):
        news = [{"name": "lamp", "broke_at": NOW}]
        buys = [{"ts": NOW - 3600, "name": "lamp", "order": "o1"}, {"ts": NOW - 8 * 3600, "name": "lamp", "order": "o0"}]
        (n, cands), = collect.match_broken(news, buys)
        self.assertEqual([c["order"] for c in cands], ["o1"])


if __name__ == "__main__":
    unittest.main()
