"""solaccept-v2: a fresh strict-int offer at/above a valid Sol min_accept (and above cost) closes on the fast path,
capped at the current ask, instead of waiting for the 5%-of-list schedule to walk down to it.
Numbers from parent-above-sol-min.json (db46a613, 6c6b094a: cost 16898, offer 25536)."""
import unittest

import seller_r3


def unit(cost=16898, lst=22180):
    return {'unit':'u1', 'sku':'G0be9ed07', 'name':'x', 'category':'', 'list_price':lst, 'kind':'goods',
            'cost':cost, 'supplied_at':0, 'expires_at':None, 'grade':'A', 'cert':{}}


class SolAccept(unittest.TestCase):
    def test_offer_at_sol_min_below_next_counter_closes(self):
        u = unit()   # ask 27758 -> next counter 26649 (step 5% of 22180 = 1109)
        self.assertEqual(seller_r3.next_quote(u, 27758), 26649)
        self.assertEqual(seller_r3.fast_move(u, 27758, 25536, None, 25536), ('accept', 25536))   # db46a613
        self.assertEqual(seller_r3.fast_move(u, 31920, 25536, None, 23636), ('accept', 25536))   # 6c6b094a

    def test_offer_below_sol_min_still_counters(self):
        self.assertEqual(seller_r3.fast_move(unit(), 27758, 25535, None, 25536), ('counter', 26649))

    def test_cost_equal_offer_does_not_shortcut(self):
        # Sol min at cost: an offer equal to cost is not a positive margin, so the schedule decides (it counters here).
        self.assertEqual(seller_r3.fast_move(unit(), 27758, 16898, None, 16898), ('counter', 26649))

    def test_offer_above_ask_is_capped_at_the_ask(self):
        self.assertEqual(seller_r3.fast_move(unit(), 27758, 30000, None, 25536), ('accept', 27758))

    def test_no_sol_min_keeps_the_existing_schedule(self):
        self.assertEqual(seller_r3.fast_move(unit(), 27758, 25536, None, None), ('counter', 26649))
        self.assertEqual(seller_r3.fast_move(unit(), 27758, 26649, None, None), ('accept', 26649))

    def test_non_int_or_missing_ask_never_shortcuts(self):
        self.assertEqual(seller_r3.fast_move(unit(), 27758, 25536.0, None, 25536), ('counter', 26649))
        self.assertEqual(seller_r3.fast_move(unit(), 27758, True, None, 1), ('counter', 26649))
        self.assertEqual(seller_r3.fast_move(unit(), None, 25536, 27758, 25536)[0], 'counter')   # opening: quote


if __name__ == '__main__':
    unittest.main()
