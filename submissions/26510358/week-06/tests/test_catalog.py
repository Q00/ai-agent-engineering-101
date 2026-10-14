from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog import load_catalog, search_catalog
from generate_catalog import build_catalog


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = load_catalog()

    def test_committed_catalog_has_thousands_and_prescribed_reserves(self):
        self.assertEqual(len(self.items), 3003)
        self.assertEqual(len({item["id"] for item in self.items}), len(self.items))
        self.assertEqual({item["id"]: item["reserve"] for item in self.items[:3]},
                         {"lamp": 100, "chair": 180, "bookshelf": 260})
        self.assertEqual(self.items, build_catalog())

    def test_described_english_bookshelf_meets_all_requirements(self):
        results = search_catalog("something wooden to keep about 40 books, not taller than 75 cm", self.items)
        self.assertGreater(len(results), 0)
        self.assertLessEqual(len(results), 8)
        for item in results:
            attrs = item["attributes"]
            self.assertEqual(item["category"], "bookshelf")
            self.assertEqual(attrs["material"], "wood")
            self.assertLessEqual(attrs["height_cm"], 75)
            self.assertGreaterEqual(attrs["capacity_books"], 40)

    def test_korean_description_matches_and_respects_strict_height(self):
        results = search_catalog("책 40권 정도를 보관할 나무 책장, 높이 80cm 미만", self.items)
        self.assertTrue(results)
        for item in results:
            self.assertEqual(item["attributes"]["material"], "wood")
            self.assertLess(item["attributes"]["height_cm"], 80)
            self.assertGreaterEqual(item["attributes"]["capacity_books"], 40)

    def test_impossible_requirements_have_no_candidates(self):
        self.assertEqual(search_catalog("wooden shelf for 5000 books, under 30cm", self.items), [])
        self.assertEqual(search_catalog("wooden shelf at least 150cm and at most 60cm", self.items), [])

    def test_reference_desk_does_not_select_desks(self):
        results = search_catalog("wooden storage for 40 books, not taller than my desk", self.items)
        self.assertTrue(results)
        self.assertTrue(all(item["category"] == "bookshelf" for item in results))

    def test_base_ids_are_first_and_item_constraints_still_apply(self):
        for product_id in ("lamp", "chair", "bookshelf"):
            self.assertEqual(search_catalog(f"I want to buy a {product_id}", self.items)[0]["id"], product_id)
        results = search_catalog("bookshelf under 75cm", self.items)
        self.assertTrue(results)
        self.assertNotIn("bookshelf", [item["id"] for item in results])

    def test_subset_catalogs_do_not_leak_other_shop_products(self):
        shop_items = [item for item in self.items if item["id"] in {"lamp-0001", "lamp-0002"}]
        results = search_catalog("lamp", shop_items)
        self.assertEqual({item["id"] for item in results}, {"lamp-0001", "lamp-0002"})

    def test_search_preserves_schema_and_deep_copies(self):
        before = copy.deepcopy(self.items)
        results = search_catalog("lamp", self.items, limit=1)
        original = next(item for item in self.items if item["id"] == results[0]["id"])
        self.assertEqual(set(results[0]), set(original))
        results[0]["reserve"] = 1
        results[0]["attributes"]["height_cm"] = -100
        self.assertEqual(self.items, before)

    def test_empty_unknown_and_zero_limit_do_not_pick_arbitrary_items(self):
        self.assertEqual(search_catalog("", self.items), [])
        self.assertEqual(search_catalog("spaceship quantum engine", self.items), [])
        self.assertEqual(search_catalog("lamp", self.items, limit=0), [])

    def test_bad_catalog_rejected_before_negotiation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "bad.json"
            target.write_text(json.dumps([self.items[0], self.items[0]]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                load_catalog(target)


if __name__ == "__main__":
    unittest.main()
