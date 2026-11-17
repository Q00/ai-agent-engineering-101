import unittest
from unittest.mock import patch
import analyst


class CatchupTests(unittest.TestCase):
    def test_full_page_advances_to_next_page(self):
        first = [{'seq': i} for i in range(1, 501)]
        with patch.object(analyst, 'ledger_batch', side_effect=[first, [{'seq': 501}]]) as fetch:
            rows, ready, pages = analyst.catch_up_ledger('https://example', 0)
        self.assertEqual(len(rows), 501)
        self.assertTrue(ready)
        self.assertEqual(pages, 2)
        self.assertEqual(fetch.call_args_list[-1].args[1], 500)

    def test_page_limit_cannot_claim_complete_coverage(self):
        with patch.object(analyst, 'ledger_batch', return_value=[{'seq': i} for i in range(1, 501)]):
            _, ready, pages = analyst.catch_up_ledger('https://example', 0, max_pages=1)
        self.assertFalse(ready)
        self.assertEqual(pages, 1)

    def test_repeated_page_is_rejected(self):
        first = [{'seq': i} for i in range(1, 501)]
        with patch.object(analyst, 'ledger_batch', side_effect=[first, first]):
            with self.assertRaises(ValueError):
                analyst.catch_up_ledger('https://example', 0)
