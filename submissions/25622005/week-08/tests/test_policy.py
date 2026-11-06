"""policy.py: RANGES, filling keys missing from the file from DEFAULTS, and the fallbacks of load()."""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

W = Path(__file__).resolve().parents[1]
os.environ.setdefault("R3_TEST_HOME", tempfile.mkdtemp(prefix="r3-test-"))
os.environ["R3_HOME"] = os.environ["R3_TEST_HOME"]
sys.path.insert(0, str(W))

import memory.store  # noqa: E402
import policy  # noqa: E402


def defaults():
    return json.loads(json.dumps(policy.DEFAULTS))


class RangeTests(unittest.TestCase):
    def test_every_range_key_is_a_default_number(self):
        for k in policy.RANGES:
            with self.subTest(key=k):
                v = policy.get(policy.DEFAULTS, k)
                self.assertTrue(isinstance(v, (int, float)) and not isinstance(v, bool))
                self.assertTrue(policy.RANGES[k][0] <= v <= policy.RANGES[k][1])

    def test_ranges_are_checked(self):
        self.assertEqual(policy.validate(defaults()), [])
        for key, bad in [("stock.cover_min", 1), ("stock.cover_safety", 50), ("buyer.food_margin_h", -1),
                         ("buyer.min_surplus_list", 0.6), ("seller.warranty_cap_6h", 30000000)]:
            with self.subTest(key=key):
                p = defaults()
                sec, k = key.split(".")
                p[sec][k] = bad
                self.assertTrue(any(e.startswith(key) for e in policy.validate(p)), policy.validate(p))


    def test_listed_skus_and_end_of_round_keys(self):
        p = defaults()
        self.assertEqual((p["stock"]["taper_h"], p["stock"]["clear_70_h"], p["stock"]["clear_any_h"]), (0, 0, 0))
        for skus, want in ((["G1", "G1"], "stock.trend_skus: more than"), (["G 1"], "stock.trend_skus: expected"),
                           ([f"G{i}" for i in range(17)], "stock.trend_skus: more than")):
            with self.subTest(skus=skus):
                p = defaults()
                p["stock"]["trend_skus"] = skus
                self.assertTrue(any(e.startswith(want) for e in policy.validate(p)), policy.validate(p))
        p = defaults()
        p["stock"].update(trend_skus=["G1", "G2"], focus_skus=["G1"])
        self.assertEqual(policy.validate(p), [])
        p["stock"]["big_ticket_share"] = 0.85                     # Patch 3.3: stock counts at cost, big-ticket may be most of it
        self.assertEqual(policy.validate(p), [])
        p["stock"]["big_ticket_share"] = 1.05
        self.assertTrue(any(e.startswith("stock.big_ticket_share") for e in policy.validate(p)))
        p["stock"]["big_ticket_share"] = 0.4
        p["buyer"]["min_kept"] = 0.7                                # above RANGES (0.0, 0.6)
        self.assertTrue(any(e.startswith("buyer.min_kept") for e in policy.validate(p)))


class FillDefaultsTests(unittest.TestCase):
    def test_fills_missing_keys_only(self):
        p = defaults()
        del p["stock"]["focus_max"], p["buyer"]["food_margin_h"]
        p["buyer"]["cap_food"] = 0.9
        out, filled = policy.fill_defaults(p)
        self.assertEqual(filled, ["stock.focus_max", "buyer.food_margin_h"])
        self.assertEqual((out["stock"]["focus_max"], out["buyer"]["food_margin_h"], out["buyer"]["cap_food"]),
                         (8, 0.2, 0.9))
        self.assertNotIn("focus_max", p["stock"])                 # the input is not changed
        self.assertEqual(policy.validate(out), [])

    def test_a_missing_added_section_is_filled_whole(self):
        p = defaults()
        del p["market"]
        out, filled = policy.fill_defaults(p)
        self.assertEqual(filled, ["market.surge_free", "market.surge_per_unit", "market.surge_max"])
        self.assertEqual(out["market"], policy.DEFAULTS["market"])
        self.assertEqual(policy.validate(out), [])
        p["market"] = {}                                           # present but empty: still a broken file
        self.assertEqual(policy.fill_defaults(p)[1], [])

    def test_validate_stays_strict(self):
        p = defaults()
        p["buyer"]["typo_key"] = 1
        p["buyer"]["cap_food"] = 9
        del p["llm"]                                               # a whole missing section is not filled
        out, filled = policy.fill_defaults(p)
        self.assertEqual(filled, [])
        errors = policy.validate(out)
        for want in ("buyer.typo_key: unknown key", "buyer.cap_food: 9 outside", "llm: missing"):
            self.assertTrue(any(e.startswith(want) for e in errors), errors)
        self.assertEqual(policy.fill_defaults(None), (None, []))
        p = defaults()
        p["llm"] = {}                                              # an empty section is a broken file, not a new key
        out, filled = policy.fill_defaults(p)
        self.assertEqual((filled, out["llm"]), ([], {}))
        self.assertTrue(policy.validate(out))


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="r3-policy-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        for p in (patch.object(policy, "LASTGOOD", self.dir / "policy.lastgood.json"),
                  patch.object(memory.store, "LOGS", self.dir / "logs")):
            p.start()
            self.addCleanup(p.stop)
        policy._cache.clear()
        self.addCleanup(policy._cache.clear)
        self.skill = self.dir / "SKILL.md"
        text = (W / "strategy" / "SKILL.md").read_text(encoding="utf-8")
        self.skill.write_text(re.sub(r"(?m)^    focus_cap_share:.*\n", "", text), encoding="utf-8")

    def warnings(self):
        log = self.dir / "logs" / "hooks" / "policy.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def test_missing_key_is_filled_with_one_warning(self):
        pol, ver, errors = policy.load(self.skill)
        self.assertEqual(errors, [])
        self.assertEqual(pol["stock"]["focus_cap_share"], 0.5)
        front, _ = policy.split_skill(self.skill.read_text(encoding="utf-8"))
        self.assertEqual(ver, f"{front['version']}/{policy._sha8(front['policy'])}")   # as the strategist sees it
        self.assertEqual(len(self.warnings()), 1)
        self.assertIn("stock.focus_cap_share=0.5", self.warnings()[0])
        policy._cache.clear()                                      # the next hook process: same file, no new warning
        self.assertEqual(policy.load(self.skill), (pol, ver, []))
        self.assertEqual(len(self.warnings()), 1)
        self.assertEqual(policy._read_lastgood()["policy"], pol)

    def test_a_changed_default_is_filled_and_warned_again(self):
        pol, ver, _ = policy.load(self.skill)
        policy._cache.clear()
        with patch.dict(policy.DEFAULTS["stock"], focus_cap_share=0.6):     # a new image with another default
            self.assertEqual(policy.load(self.skill)[:2], (dict(pol, stock=dict(pol["stock"], focus_cap_share=0.6)), ver))
        self.assertEqual(len(self.warnings()), 2)
        self.assertIn("stock.focus_cap_share=0.6", self.warnings()[1])
        self.assertEqual(policy._read_lastgood()["policy"]["stock"]["focus_cap_share"], 0.6)

    def test_a_file_without_the_market_section_loads(self):
        text = self.skill.read_text(encoding="utf-8")
        self.skill.write_text(re.sub(r"(?m)^  market:\n(    .*\n)+", "", text), encoding="utf-8")
        self.assertNotIn("\n  market:\n", self.skill.read_text(encoding="utf-8"))
        pol, _, errors = policy.load(self.skill)
        self.assertEqual(errors, [])
        self.assertEqual(pol["market"], {"surge_free": 50, "surge_per_unit": 0.002, "surge_max": 1.5})
        self.assertIn("market.surge_free=50", self.warnings()[0])

    def test_lastgood_from_before_a_new_key_still_serves(self):
        old = defaults()
        old["seller"]["floor_list"] = 0.55
        del old["stock"]["focus_max"]
        policy.LASTGOOD.write_text(json.dumps({"ver": "7/abcd1234", "policy": old}), encoding="utf-8")
        self.skill.write_text("not a skill file", encoding="utf-8")
        pol, ver, errors = policy.load(self.skill)
        self.assertTrue(errors)
        self.assertEqual(ver, "7/abcd1234+lastgood")
        self.assertEqual((pol["seller"]["floor_list"], pol["stock"]["focus_max"]), (0.55, 8))


if __name__ == "__main__":
    unittest.main()
