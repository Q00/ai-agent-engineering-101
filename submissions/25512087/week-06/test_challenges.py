"""Contracts for the required large-catalog and many-shops demos."""

from __future__ import annotations

import unittest

from catalog import CatalogIndex
from challenge_catalog import select_catalog_product
from challenge_directory import build_demo_shops, select_shop
from generate_catalog import generate_products
from harness import Decision


class FakeHarness:
    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        self.prompts: list[str] = []

    def decide(self, prompt: str, fallback: Decision) -> Decision:
        self.prompts.append(prompt)
        return self.decision


class LargeCatalogTests(unittest.TestCase):
    def test_generator_builds_at_least_one_thousand_unique_products(self) -> None:
        products = generate_products(1200)
        self.assertEqual(len(products), 1200)
        self.assertEqual(len({product["id"] for product in products}), 1200)
        self.assertTrue(all(product["reserve"] > 0 for product in products))

    def test_harness_can_only_select_a_retrieved_candidate(self) -> None:
        products = generate_products(1200)
        index = CatalogIndex.load_values(products)
        harness = FakeHarness(Decision("select", None, "not-a-real-id"))
        selected, candidates = select_catalog_product(
            "wooden storage for about 40 books below desk height",
            index,
            harness,
        )
        self.assertIn(selected, [match.product for match in candidates])
        self.assertLessEqual(len(candidates), 5)
        self.assertLess(len(harness.prompts[0]), 3000)


class ManyShopsTests(unittest.TestCase):
    def test_demo_directory_contains_twenty_unique_cards(self) -> None:
        shops = build_demo_shops(20, first_port=12000)
        self.assertEqual(len(shops), 20)
        self.assertEqual(len({shop.card.url for shop in shops}), 20)

    def test_invalid_harness_shop_choice_falls_back_to_candidate(self) -> None:
        shops = build_demo_shops(20, first_port=12000)
        candidates = [shop.card for shop in shops[:4]]
        harness = FakeHarness(Decision("select", None, "unknown shop"))
        selected = select_shop("lamp", candidates, harness)
        self.assertIn(selected, candidates)
        self.assertLess(len(harness.prompts[0]), 2500)


if __name__ == "__main__":
    unittest.main()
