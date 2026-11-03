"""strategist/focus.py on synthetic ledger rows: rolling 6 h counts that expire, eligibility, ranking,
hysteresis, the cap on new SKUs per run, and forum mentions that never move the list."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-focus-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import policy  # noqa: E402
from strategist import focus  # noqa: E402

NOW = 1_800_000_000.0
POL = copy.deepcopy(policy.DEFAULTS)            # focus_max 8, focus_min_settles 10, food_shelf_hours 720


def settles(sku, n, ts=NOW - 60, seq0=1):
    return [{"seq": seq0 + i, "ts": ts, "kind": "settle", "data": {"sku": sku, "unit": f"u{seq0 + i}"}}
            for i in range(n)]


def supply(sku, shop, ts=NOW - 60):
    return {"seq": 0, "ts": ts, "kind": "supply", "data": {"shop": shop, "sku": sku, "qty": 3}}


def catalog(*skus, kind="goods", shelf=None):
    return {s: ["name", "cat", 10000, kind, shelf] for s in skus}


def pol(**stock):
    p = copy.deepcopy(POL)
    p["stock"].update(stock)
    return p


class CountsTest(unittest.TestCase):
    def test_settles_suppliers_score_and_refs(self):
        fs = focus.update({}, settles("A", 12, seq0=100) + [supply("A", "s1"), supply("A", "s2"), supply("A", "s1"),
                                                           supply("A", "psyoung82")], NOW)
        self.assertEqual(focus.stats(fs, "A"), {"sku": "A", "settles_6h": 12, "suppliers_6h": 2, "score": 4.0,
                                                "refs": [109, 110, 111]})

    def test_old_buckets_and_suppliers_expire(self):
        fs = focus.update({}, settles("A", 12, ts=NOW - 5 * 3600) + [supply("A", "s1", ts=NOW - 5 * 3600)], NOW)
        self.assertEqual(focus.stats(fs, "A")["settles_6h"], 12)
        focus.update(fs, settles("A", 3, seq0=50), NOW + 2 * 3600)          # the 5 h old rows are now 7 h old
        st = focus.stats(fs, "A")
        self.assertEqual((st["settles_6h"], st["suppliers_6h"], st["refs"]), (3, 0, [50, 51, 52]))
        self.assertEqual(len(fs["settles"]), 1)
        self.assertEqual(fs["suppliers"], {})

    def test_rows_older_than_the_window_and_bad_rows_are_ignored(self):
        rows = settles("A", 5, ts=NOW - 7 * 3600) + settles("bad sku!", 5) + [{"kind": "settle"}, "x", None]
        fs = focus.update({}, rows, NOW)
        self.assertEqual(fs["settles"], {})

    def test_since_restarts_when_the_counts_ran_empty(self):
        fs = focus.update({}, settles("A", 1), NOW)
        self.assertEqual(focus.update(fs, [], NOW + 3600)["since"], NOW)
        self.assertEqual(focus.update(fs, [], NOW + 8 * 3600)["since"], NOW + 8 * 3600)    # down for > 6 h


class SelectTest(unittest.TestCase):
    def test_eligibility_and_ranking(self):
        rows = (settles("HOT", 30) + settles("BUSY", 40) + [supply("BUSY", f"s{i}") for i in range(7)]
                + settles("FOOD_SHORT", 50) + settles("FOOD_LONG", 20) + settles("FEW", 4) + settles("NOCAT", 50))
        fs = focus.update({}, rows, NOW)
        cat = catalog("HOT", "BUSY", "FEW") | catalog("FOOD_SHORT", kind="food", shelf=48) \
            | catalog("FOOD_LONG", kind="food", shelf=720)
        got = focus.select(fs, cat, POL, NOW)
        self.assertEqual([(r["sku"], r["score"]) for r in got], [("HOT", 30.0), ("FOOD_LONG", 20.0), ("BUSY", 5.0)])
        self.assertEqual(set(got[0]), {"sku", "settles_6h", "suppliers_6h", "score", "refs"})

    def test_food_without_shelf_hours_is_not_eligible(self):
        fs = focus.update({}, settles("F", 20), NOW)
        self.assertEqual(focus.select(fs, {"F": ["n", "c", 1, "food"]}, POL, NOW), [])


class ChooseTest(unittest.TestCase):
    def rows(self, *spec):
        return [{"sku": s, "settles_6h": n, "suppliers_6h": 0, "score": float(n), "refs": []} for s, n in spec]

    def test_at_most_three_new_per_run(self):
        rows = self.rows(*[(f"S{i}", 100 - i) for i in range(10)])
        self.assertEqual(focus.choose(rows, [], POL), ["S0", "S1", "S2"])
        self.assertEqual(focus.choose(rows, ["S0", "S1", "S2"], POL), ["S0", "S1", "S2", "S3", "S4", "S5"])
        self.assertEqual(focus.choose(rows, [f"S{i}" for i in range(6)], POL), [f"S{i}" for i in range(8)])

    def test_hysteresis_keeps_a_sku_within_twice_focus_max(self):
        rows = self.rows(*[(f"N{i}", 100 - i) for i in range(10)], ("OLD", 6))       # rank 10 < 16
        cur = ["OLD"] + [f"N{i}" for i in range(7)]
        self.assertEqual(focus.choose(rows, cur, POL), cur)                          # full: N7 waits
        far = self.rows(*[(f"N{i}", 100 - i) for i in range(16)], ("OLD", 50))       # rank 16: dropped
        self.assertEqual(focus.choose(far, cur, POL), [f"N{i}" for i in range(8)])

    def test_new_sku_needs_the_full_minimum_and_a_kept_one_half(self):
        rows = self.rows(("A", 9), ("B", 5))                    # select keeps rows down to half of 10
        self.assertEqual(focus.choose(rows, [], POL), [])
        self.assertEqual(focus.choose(rows, ["B"], POL), ["B"])
        self.assertEqual(focus.choose([], ["B"], POL), [])     # under half: not in select's rows

    def test_counts_shorter_than_6h_drop_nothing(self):
        self.assertEqual(focus.choose([], ["B", "C"], POL, covered=False), ["B", "C"])

    def test_smaller_focus_max_keeps_the_best_ranked(self):
        rows = self.rows(("A", 30), ("B", 20), ("C", 10))
        self.assertEqual(focus.choose(rows, ["C", "A", "B"], pol(focus_max=2)), ["A", "B"])
        self.assertEqual(focus.choose(rows, ["A"], pol(focus_max=0)), [])

    def test_end_to_end_result_validates(self):
        fs = focus.update({}, [r for i in range(12) for r in settles(f"G{i:08x}", 20 + i, seq0=100 * i)], NOW)
        cur = []
        for _ in range(4):
            cur = focus.choose(focus.select(fs, catalog(*[f"G{i:08x}" for i in range(12)]), POL, NOW), cur, POL)
        p = copy.deepcopy(POL)
        p["stock"]["focus_skus"] = cur
        self.assertEqual(len(cur), 8)
        self.assertEqual(policy.validate(p), [])


class MentionsTest(unittest.TestCase):
    def test_counted_per_post_and_never_selected(self):
        posts = [{"text": "buy Ga1b2c3d4 now! Ga1b2c3d4", "title": "Ga1b2c3d4"}, {"body": "also GFFFFFFFF, G12345"},
                 "junk", {"text": None}]
        self.assertEqual(focus.mentions(posts), {"Ga1b2c3d4": 1, "GFFFFFFFF": 1})
        self.assertEqual(focus.mentions(None), {})
        fs = focus.update({}, settles("Ga1b2c3d4", 3), NOW)               # mentioned, but too few settles
        self.assertEqual(focus.choose(focus.select(fs, catalog("Ga1b2c3d4"), POL, NOW), [], POL), [])


if __name__ == "__main__":
    unittest.main()
