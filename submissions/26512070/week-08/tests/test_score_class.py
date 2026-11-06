"""harness.score_class: students only, rank and gaps, part percentiles among the students' ranked rows."""
import unittest

from harness.score_class import class_view


def board():
    players, shops, buyers = [], [], []
    for i, (login, student, tot) in enumerate([("a", True, 1.6), ("gom31", True, 1.3), ("b", True, 1.4),
                                               ("house", False, 1.9), ("c", True, 1.0)]):
        players.append({"login": login, "student": student, "student_total": tot, "shop": {"name": f"{login}-s"},
                        "buyer": {"name": f"{login}-b"}, "student_shop": 0.7, "student_buyer": 0.6,
                        "student_pct": 0.5})
        shops.append({"name": f"{login}-s", "deals": 1, "book": 100 - i, "revenue": i, "stars_rank": 4.5})
        buyers.append({"name": f"{login}-b", "deals": i, "surplus": 10 * i, "forum": 0.5, "posts": i})
    return {"ts": 1, "players": players, "shops": shops, "buyers": buyers}


class ClassViewTest(unittest.TestCase):
    def test_view(self):
        v = class_view(board())
        self.assertEqual((v["students"], v["rank"]), (4, 3))          # the house player is left out
        self.assertEqual((v["next"], v["gap_next"]), ("b", 0.1))
        self.assertAlmostEqual(v["percentile_calc"], 1 / 3, places=3)
        self.assertEqual(v["shop"]["ranked"], 4)
        self.assertEqual(v["buyer"]["ranked"], 3)                    # a buyer with no deal is not ranked
        self.assertEqual(v["shop"]["parts"]["book"]["next"], "a-s")  # book 99 vs a's 100
        self.assertIsNone(class_view(board(), me="nobody"))


if __name__ == "__main__":
    unittest.main()
