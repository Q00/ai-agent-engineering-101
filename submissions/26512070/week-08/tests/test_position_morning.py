"""outer/position.py (our rank in each part) and outer/morning.py (the night report).

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import gzip
import json
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, paths  # noqa: E402
from outer import morning, position  # noqa: E402
from tests.test_calc_check import Base  # noqa: E402


def board(ts, our_surplus):
    shops = [{"name": "gom31-shop", "deals": 5, "profit": 10, "book": 17, "revenue": 50, "stars_rank": 4.5, "stock_cost": 7},
             {"name": "a", "deals": 5, "profit": 20, "book": 20, "revenue": 40, "stars_rank": 4.8},
             {"name": "idle", "deals": 0, "profit": 0, "book": 0, "revenue": 0, "stars_rank": 0}]
    buyers = [{"name": "gom31-shop", "deals": 3, "surplus": our_surplus, "forum": 0.5, "posts": 2},
              {"name": "b", "deals": 3, "surplus": 100, "forum": 0.7, "posts": 1},
              {"name": "c", "deals": 3, "surplus": 50, "forum": 0.1, "posts": 0}]
    players = [{"player": "gom31", "login": "gom31", "shop": {"name": "gom31-shop", "all": 0.5},
                "buyer": {"name": "gom31-shop", "all": 0.4}, "total": 0.9},
               {"player": "x", "shop": {}, "buyer": {}, "total": 1.2}]
    return {"ts": ts, "shops": shops, "buyers": buyers, "players": players}


class PositionMorningTest(Base):
    def test_ranks(self):
        r = position.ranks(board(0, 60))
        self.assertEqual((r["players"]["rank"], r["players"]["of"], r["players"]["next"]), (2, 2, "x"))
        self.assertEqual(r["shop"]["ranked"], 2)                        # the idle shop is not ranked
        p = r["shop"]["parts"]["book"]                                  # patch 3.3: book = cash + stock at cost
        self.assertEqual((p["rank"], p["next"], p["gap"]), (2, "a", 3))
        s = r["buyer"]["parts"]["surplus"]
        self.assertEqual((s["rank"], s["of"], s["percentile"]), (2, 3, 0.5))   # one below plus half of our own tie (core/score.py)
        self.assertEqual(r["stock_cost"], 7)

    def test_build_moves_and_morning(self):
        now = time.mktime(time.strptime("2026-11-05 03:00", "%Y-%m-%d %H:%M"))
        f = paths.memory() / "market" / "board-full-20261105.jsonl.gz"
        f.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(f, "at", encoding="utf-8") as g:
            g.write(json.dumps(board(now - 3600, 60)) + "\n")       # 1 h ago 2nd in surplus
            g.write(json.dumps(board(now, 120)) + "\n")             # now 1st
        cur = position.build(now=now)
        s = cur["buyer"]["parts"]["surplus"]
        self.assertEqual((s["rank"], s["rank_1h"]), (1, 2))
        md = (paths.root() / "logs" / "strategy" / "position.md").read_text(encoding="utf-8")
        self.assertIn("2위 / 2명", md)
        self.assertIn("▲1", md)
        out = morning.build("2026-11-05").read_text(encoding="utf-8")
        self.assertIn("| buyer 잉여 | 60 -> 120 | 2 -> 1 / 3 |", out)
        self.assertIn("설치 0건", out)

    def test_diff(self):
        a = {"version": 1, "playbook": {"restock": {"u": 4, "s": 0.3}}, "context": "x"}
        b = {"version": 2, "playbook": {"restock": {"u": 6, "s": 0.3}}, "context": "x"}
        self.assertEqual(morning.diff(a, b), {"playbook.restock.u": (4, 6)})


if __name__ == "__main__":
    unittest.main()
