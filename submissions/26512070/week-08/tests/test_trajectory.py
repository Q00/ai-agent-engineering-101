"""harness.trajectory on a small made-up day: joins, failure causes, unpaid sales, tool calls by process."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from harness import trajectory as T

DAY = "20261105"
T0 = time.mktime(time.strptime(DAY, "%Y%m%d")) + 3600


def ev(source, writer, pid, kind, dt, data=None, **keys):
    ts = T0 + dt
    return {"schema_version": 1, "event_id": f"{writer}-{pid}-{int(ts * 1000)}-000001", "ts": ts, "writer": writer,
            "source": source, "kind": kind, "strategy_version": 88, "epoch": "e88", "card_version": 3,
            "keys": keys, "data": data or {}}


def write(root, evs):
    for e in evs:
        p = root / "memory" / "raw" / e["source"] / f"{e['writer']}-{DAY}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(e) + "\n")


class TrajectoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HARNESS_ROOT", "HARNESS_OUT")}
        os.environ["HARNESS_ROOT"] = self.tmp.name
        os.environ["HARNESS_OUT"] = str(Path(self.tmp.name) / "out")

    def tearDown(self):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def test_build(self):
        tmp_path = Path(self.tmp.name)
        write(tmp_path, [
            # buyer process 7 runs two orders in turn
            ev("buyer", "buyer", 7, "buyer.order", 0, {"budget": 30000, "kind": "goods"}, order="o1"),
            ev("tools", "buyer", 7, "tool.result", 1, {"tool": "calc.extract", "ok": True, "ms": 2}),
            ev("buyer", "buyer", 7, "buyer.pick", 2, {"shop": "s1", "why": "rules: cheapest shop"}, order="o1",
               counterpart="s1"),
            ev("buyer", "buyer", 7, "buyer.closed", 3, {"outcome": "completed", "agreed": 20000, "verdict": "fair"},
               order="o1", counterpart="s1"),
            ev("buyer", "buyer", 7, "buyer.order", 4, {"budget": 9000, "kind": "food"}, order="o2"),
            ev("tools", "buyer", 7, "tool.result", 5, {"tool": "check.receipt", "ok": False, "reason": "no sig_seller"}),
            ev("buyer", "buyer", 7, "buyer.closed", 6, {"outcome": "completed", "agreed": 8000,
                                                        "verdict": "no deal: the receipt has no sig_seller"}, order="o2"),
            ev("outer", "collect", 9, "buyer.payment", 60, {"paid": True, "price": 20000, "ledger": 5}, order="o1"),
            ev("review", "review", 8, "review.written", 70, {"stars": 4, "fact_stars": 4}, order="o1", counterpart="s1"),
            # seller: one settled sale, one never paid, one abandoned
            ev("seller", "seller", 3, "seller.request", 0, {"excerpt": "hi"}, task="t1"),
            ev("seller", "seller", 3, "seller.closed", 5, {"outcome": "completed", "price": 100, "cost": 70}, task="t1",
               unit="u1"),
            ev("outer", "collect", 9, "seller.settled", 50, {"price": 100, "stars": 5}, task="t1", ledger_seq=11,
               counterpart="b1", unit="u1"),
            ev("seller", "seller", 3, "seller.closed", 6, {"outcome": "completed", "price": 90}, task="t2", unit="u2"),
            ev("seller", "seller", 3, "seller.request", 7, {"excerpt": "price?"}, task="t3"),
            ev("outer", "collect", 9, "outer.pass", 4 * 3600, {}),
        ])
        tr = T.build(DAY)
        o1, o2 = tr["o1"], tr["o2"]
        assert o1["result"]["paid"] is True and o1["after"]["our_stars"] == 4 and o1["result"]["cause"] is None
        assert [s.get("tool") for s in o1["steps"] if s.get("tool")] == ["calc.extract"]
        assert [s.get("tool") for s in o2["steps"] if s.get("tool")] == ["check.receipt"]
        assert o2["result"]["cause"] == "counterpart" and o2["steps"][0]["cause"] == "counterpart"
        assert o1["knowledge_used"][0]["about"] == "s1"
        assert tr["t1"]["result"]["settled"] is True and tr["t1"]["counterpart"]["buyer"] == "b1"
        assert tr["t2"]["result"]["settled"] is False and tr["t2"]["result"]["cause"] == "counterpart"
        assert tr["t3"]["result"]["outcome"] == "abandoned"
        assert all(T.complete(t) for t in tr.values())
        cov = T.coverage(tr)
        assert cov["buyer"]["with_tool_steps"] == 2 and cov["seller"]["trades"] == 3


    def test_cause(self):
        assert T.cause("no deal: shelf - grade C keeps at most 5 h") == "limit"
        assert T.cause('checkout refused: 409 {"error":"unit u-1 sold"}') == "counterpart"
        assert T.cause("no deal: could not reach the shop (ReadTimeout)") == "network"
        assert T.cause("no deal (rejected)") == "negotiation"
        assert T.cause(None) is None
