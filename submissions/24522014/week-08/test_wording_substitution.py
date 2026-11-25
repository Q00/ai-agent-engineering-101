"""Only a substitution invited by the old name-and-SKU request stops counting against a shop; every other mismatch still blocks."""

import unittest

from r3_selection import select, wording_substitution

SHOP = {"name": "s", "url": "http://s.test", "offers": [{"sku": "A", "qty": 3}]}
CATALOG = {"A": {"list_price": 1000}}


def mismatch(**changes):
    row = {"round": 3, "shop_name": "s", "item": "A", "outcome": "receipt_mismatch", "request_format": "name_and_sku", "agreed_price": 100, "task": "t",
           "receipt": {"item": "B", "price": 100, "task": "t"}}
    row.update(changes)
    return row


def candidates(*rows):
    return [e["shop"] for e in select([SHOP], list(rows), CATALOG, 1000)[4]]


class Substitution(unittest.TestCase):
    def test_old_wording_with_another_item_does_not_block(self):
        self.assertEqual(candidates(mismatch()), ["s"])

    def test_old_wording_with_a_category_name_as_item_does_not_block(self):
        self.assertEqual(candidates(mismatch(receipt={"item": "김치", "price": 100, "task": "t"})), ["s"])

    def test_every_other_mismatch_still_blocks(self):
        cases = {"sku_only": mismatch(request_format="sku_only"), "no_format": {k: v for k, v in mismatch().items() if k != "request_format"},
                 "same_item_other_price": mismatch(receipt={"item": "A", "price": 120, "task": "t"}),
                 "other_item_and_price": mismatch(receipt={"item": "B", "price": 120, "task": "t"}),
                 "other_task": mismatch(receipt={"item": "B", "price": 100, "task": "x"}),
                 "same_item": mismatch(receipt={"item": "A", "price": 100, "task": "t"}),
                 "no_item": mismatch(receipt={"price": 100, "task": "t"}), "no_receipt": mismatch(receipt=None)}
        for name, row in cases.items():
            with self.subTest(name):
                self.assertFalse(wording_substitution(row))
                self.assertEqual(candidates(row), [])

    def test_a_later_sku_mismatch_blocks_a_shop_with_an_excused_one(self):
        self.assertEqual(candidates(mismatch(), mismatch(request_format="sku_only")), [])

    def test_other_outcomes_are_not_substitutions(self):
        self.assertFalse(wording_substitution(mismatch(outcome="no_deal")))


if __name__ == "__main__":
    unittest.main()
