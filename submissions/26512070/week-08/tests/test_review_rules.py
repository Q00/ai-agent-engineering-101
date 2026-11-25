"""calc.fact_stars with playbook.review: 4 for a deal, 5 only at five_share of its value or less, by price only.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, paths  # noqa: E402
from tests.test_calc_check import Base  # noqa: E402
from tools.registry import Ctx, call  # noqa: E402


class ReviewRulesTest(Base):
    def test_four_by_default_five_when_cheap(self):
        path = paths.memory() / "strategy" / "active.json"
        st = atomic.read_json(path)
        st["playbook"] = {"review": {"mode": "rules", "base": 4, "five_share": 0.6}}
        atomic.write_json(path, st)

        def stars(price, grade="A", value=10000):
            deal = {"receipt": {"price": price}, "settled": {"grade": grade, "value": value, "claim_honest": True}}
            return call(Ctx(caller="review"), "calc.fact_stars", {"deal": deal, "agreed_price": price})["result"]["stars"]
        self.assertEqual(stars(7500), 4)            # a fair deal
        self.assertEqual(stars(6000), 5)            # really cheap
        self.assertEqual(stars(5000, grade="B"), 5)  # by price only, not by grade (user 11/4 13:5x)
        self.assertEqual(stars(7500, grade="C"), 4)
        self.assertEqual(stars(9500), 3)            # over 90% of the value


if __name__ == "__main__":
    unittest.main()
