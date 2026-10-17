"""Offline guards for catalog selection, not evidence of model comprehension."""
import json
import unittest

from negotiation.catalog import Catalog, matches
from negotiation.catalog_contracts import (
    NeedContext, SelectionContext, catalog_schema, parse_need_decision, parse_selection_decision,
)
from negotiation.config import ROOT
from negotiation.contracts import DecisionError, SellerContext, parse_seller_decision


def need(**changes):
    return {"action": "search", "category": "bookshelf", "material": "wood", "color": None,
            "min_books": 40, "max_height_cm": 80, "max_width_cm": None,
            "reason": "Open storage for forty books.", **changes}


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog(ROOT / "catalog/products.jsonl")

    def test_inventory_has_thousands_of_distinct_complete_products(self):
        self.assertEqual(len(self.catalog.products), 3000)
        self.assertEqual({len(rows) for rows in self.catalog.by_category.values()}, {600})
        self.assertEqual(len({p.name for p in self.catalog.products.values()}), 3000)

    def test_filter_checks_combined_constraints_and_caps_context(self):
        query = need(color="blue", max_width_cm=40, min_books=60, max_height_cm=60)
        rows, total = self.catalog.retrieve(query)
        self.assertEqual(total, 1)
        product = rows[0]
        self.assertEqual((product.material, product.color, product.height_cm, product.width_cm,
                          product.capacity_books), ("wood", "blue", 60, 40, 60))
        rows, total = self.catalog.retrieve(need(material=None, min_books=None, max_height_cm=None))
        self.assertEqual(total, 600)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all("reserve" not in p.public() for p in rows))

    def test_impossible_constraints_do_not_relax_to_nearby_products(self):
        rows, total = self.catalog.retrieve(need(max_height_cm=20))
        self.assertEqual((rows, total), ([], 0))

    def test_boundary_height_and_capacity_are_inclusive(self):
        product = next(p for p in self.catalog.products.values()
                       if p.category == "bookshelf" and p.height_cm == 40 and p.width_cm == 40)
        self.assertTrue(matches(product, need(material=None, min_books=40, max_height_cm=40)))
        self.assertFalse(matches(product, need(material=None, min_books=41, max_height_cm=40)))
        self.assertFalse(matches(product, need(material=None, min_books=40, max_height_cm=39)))

    def test_invalid_retrieval_does_not_fall_back_to_full_inventory(self):
        for query, limit in ((need(category=None), 8), (need(action="clarify"), 8), (need(), 100)):
            with self.assertRaises(ValueError):
                self.catalog.retrieve(query, limit)

    def test_search_requires_valid_category_and_positive_integer_dimensions(self):
        for change in ({"category": None}, {"max_height_cm": -1}, {"max_height_cm": True},
                       {"max_height_cm": 75.5}, {"category": "chair"}):
            with self.assertRaises(DecisionError):
                parse_need_decision(json.dumps(need(**change)), NeedContext())

    def test_partial_clarification_is_valid_but_cannot_trigger_retrieval(self):
        decision = parse_need_decision(json.dumps(need(action="clarify", max_height_cm=None)), NeedContext())
        self.assertEqual(decision.category, "bookshelf")
        with self.assertRaises(ValueError):
            self.catalog.retrieve(decision.model_dump())

    def test_empty_reason_is_blocked_and_length_rule_is_sent_in_schema(self):
        for role in ("need", "select"):
            schema = catalog_schema(role)
            self.assertEqual(schema["properties"]["reason"]["minLength"], 1)
            self.assertEqual(schema["properties"]["reason"]["maxLength"], 240)
        with self.assertRaises(DecisionError):
            parse_selection_decision('{"action":"clarify","item_id":null,"reason":""}',
                                     SelectionContext(need(), []))

    def test_known_supported_combination_cannot_be_declared_unsupported(self):
        with self.assertRaisesRegex(DecisionError, "supported_attributes_require_search_or_clarification"):
            parse_need_decision(json.dumps(need(action="unsupported", category="chair", material="wood",
                                                color="white", min_books=None)), NeedContext())

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaisesRegex(DecisionError, "duplicate_json_key"):
            parse_selection_decision('{"action":"select","action":"clarify","item_id":null,"reason":"Which size?"}',
                                     SelectionContext(need(), []))

    def test_fabricated_or_unretrieved_item_is_rejected(self):
        products, _ = self.catalog.retrieve(need())
        context = SelectionContext(need(), [p.public() for p in products])
        for item in ("fake-1", "chair-0001", None):
            with self.assertRaisesRegex(DecisionError, "selection_outside_candidates"):
                parse_selection_decision(json.dumps({"action": "select", "item_id": item, "reason": "Fits."}), context)

    def test_even_listed_candidate_must_meet_constraints(self):
        product = self.catalog.products["chair-0001"]
        context = SelectionContext(need(), [product.public()])
        with self.assertRaisesRegex(DecisionError, "selected_product_violates_constraints"):
            parse_selection_decision(json.dumps({"action": "select", "item_id": product.item_id, "reason": "Fits."}), context)

    def test_selected_product_uses_its_own_reserve_and_fixed_id(self):
        products, _ = self.catalog.retrieve(need())
        product = products[0]
        context = SellerContext(product.item_id, product.reserve, "negotiating", product.reserve - 1)
        for action in ("counter", "accept"):
            raw = json.dumps({"action": action, "item_id": product.item_id, "price": product.reserve - 1,
                              "reason": "A model mistake.", "summary": "Attempted below reserve."})
            with self.assertRaisesRegex(DecisionError, "price_below_reserve"):
                parse_seller_decision(raw, context)
        raw = json.dumps({"action": "counter", "item_id": "lamp", "price": product.reserve,
                          "reason": "Changed product.", "summary": "Wrong item."})
        with self.assertRaisesRegex(DecisionError, "item_changed"):
            parse_seller_decision(raw, context)


if __name__ == "__main__":
    unittest.main(verbosity=2)
