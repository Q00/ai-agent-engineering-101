"""strategist/migrate.py: one-time migrations of old shipped defaults, only for keys still at the old value."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-migrate-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import policy
from strategist import guard, migrate


class Entries(unittest.TestCase):
    def test_ids_unique_and_every_key_tunable_with_both_values_inside_its_bounds(self):
        self.assertEqual(len({m["id"] for m in migrate.MIGRATIONS}), len(migrate.MIGRATIONS))
        for m in migrate.MIGRATIONS:
            for key, (old, new) in m["changes"].items():
                with self.subTest(id=m["id"], key=key):
                    self.assertIn(key, guard.TUNABLE)
                    lo, hi, _ = guard.bounds(key)
                    self.assertTrue(lo <= old <= hi and lo <= new <= hi)
                    self.assertEqual(policy.get(policy.DEFAULTS, key), new)     # the new value is the shipped one

    def test_patch_3_3_end_of_round(self):
        m = next(m for m in migrate.MIGRATIONS if m["id"] == "2026-11-06-patch-3.3-end-of-round")
        self.assertEqual(m["changes"], {"stock.taper_h": (72, 0), "stock.goods_stop_h": (24, 6),
                                        "stock.clear_70_h": (24, 0), "stock.clear_any_h": (6, 0)})

    def test_big_ticket_split(self):
        got = {m["id"]: m for m in migrate.MIGRATIONS if m["id"].startswith("2026-11-06-big-ticket-split")}
        self.assertEqual({i: m["changes"] for i, m in got.items()},
                         {"2026-11-06-big-ticket-split-40k": {"stock.goods_max_list": (40000, 600000)},
                          "2026-11-06-big-ticket-split-50k": {"stock.goods_max_list": (50000, 600000)}})
        self.assertTrue(all("stock.big_ticket_list" in m["reason"] for m in got.values()))


class Rows(unittest.TestCase):
    def pol(self, **stock):
        p = copy.deepcopy(policy.DEFAULTS)
        p["stock"].update(taper_h=72, goods_stop_h=24, clear_70_h=24, clear_any_h=6)
        p["stock"].update(stock)
        return p

    def test_rows_only_for_keys_still_at_the_old_value(self):
        rows, skipped = migrate.rows(migrate.MIGRATIONS, self.pol(taper_h=60, clear_any_h=6.0))
        self.assertEqual([(r["key"], r["value"], r["source"]) for r in rows],
                         [("stock.goods_stop_h", 6, "migration"), ("stock.clear_70_h", 0, "migration"),
                          ("stock.clear_any_h", 0, "migration")])
        self.assertTrue(rows[0]["reason"].startswith("2026-11-06-patch-3.3-end-of-round: Patch 3.3"))
        self.assertEqual(skipped, {"2026-11-06-patch-3.3-end-of-round": {"stock.taper_h": {"live": 60, "from": 72}},
                                   "2026-11-06-big-ticket-split-40k":            # already at the new default
                                       {"stock.goods_max_list": {"live": 600000, "from": 40000}},
                                   "2026-11-06-big-ticket-split-50k":
                                       {"stock.goods_max_list": {"live": 600000, "from": 50000}}})

    def test_goods_max_list_40k_or_50k_moves_to_600k_and_a_tuned_one_stays(self):
        for live, other in ((40000, "50k"), (50000, "40k")):
            rows, skipped = migrate.rows(migrate.MIGRATIONS, self.pol(goods_max_list=live))
            self.assertEqual([(r["key"], r["value"]) for r in rows if r["key"] == "stock.goods_max_list"],
                             [("stock.goods_max_list", 600000)])
            self.assertEqual(skipped[f"2026-11-06-big-ticket-split-{other}"]["stock.goods_max_list"]["live"], live)
        rows, skipped = migrate.rows(migrate.MIGRATIONS, self.pol(goods_max_list=70000))
        self.assertNotIn("stock.goods_max_list", [r["key"] for r in rows])
        self.assertEqual(len([i for i in skipped if i.startswith("2026-11-06-big-ticket-split")]), 2)

    def test_a_key_missing_from_the_file_runs_at_the_new_default(self):
        p = self.pol()
        del p["stock"]["taper_h"]
        rows, skipped = migrate.rows(migrate.MIGRATIONS, p)
        self.assertNotIn("stock.taper_h", [r["key"] for r in rows])
        self.assertEqual(skipped["2026-11-06-patch-3.3-end-of-round"]["stock.taper_h"], {"live": 0, "from": 72})

    def test_pending_leaves_out_done_ids(self):
        self.assertEqual(migrate.pending({}), list(migrate.MIGRATIONS))
        self.assertEqual(migrate.pending({"migrations": "bad"}), list(migrate.MIGRATIONS))
        self.assertEqual(migrate.pending({"migrations": [m["id"] for m in migrate.MIGRATIONS]}), [])
        self.assertEqual(migrate.rows([], self.pol()), ([], {}))


if __name__ == "__main__":
    unittest.main()
