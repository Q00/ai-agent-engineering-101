"""Payment attribution across reuse of a timed-out unit (synthetic inventory)."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from shop_stock import StockBook, PAY_SECONDS


class LateReuseTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.clock = patch('shop_stock.time.time', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.inventory = [{'unit': 'u-000000000001', 'sku': 'G00000001', 'grade': 'A'},
                          {'unit': 'u-000000000002', 'sku': 'G00000001', 'grade': 'A'}]
        self.unit = self.inventory[0]['unit']
        self.other = self.inventory[1]['unit']
        self.book = StockBook(load=lambda: {'round': 3, 'units': self.inventory})
        self.events = []
        self.book.on_paid = lambda info: self.events.append(('paid', info['task']))
        self.book.on_unpaid = lambda info, why: self.events.append(('unpaid', info['task']))

    def agree(self, task, unit=None):
        self.book.agree(unit or self.unit, {'task': task, 'sku': 'G00000001', 'price': 100})

    def timeout(self):
        self.now += PAY_SECONDS + 1
        self.book.settle()

    def remove(self, unit=None):
        self.inventory[:] = [u for u in self.inventory if u['unit'] != (unit or self.unit)]

    def test_new_sale_does_not_pay_previous_timed_out_task(self):
        self.agree('A')
        self.timeout()
        self.agree('B')
        self.book.mark_sold(self.unit)
        self.remove()
        self.book.settle()
        self.book.mark_sold(self.unit)
        self.book.settle()
        self.assertEqual(self.events, [('unpaid', 'A'), ('paid', 'B')])

    def test_inventory_only_new_sale_consumes_old_candidate(self):
        self.agree('A')
        self.timeout()
        self.agree('B')
        self.remove()
        self.book.settle()
        self.book.settle()
        self.assertEqual(self.events, [('unpaid', 'A'), ('paid', 'B')])

    def test_cancelled_new_deal_keeps_old_late_payment_eligible(self):
        self.agree('A')
        self.timeout()
        self.agree('B')
        self.book.release(self.unit)
        self.remove()
        self.book.settle()
        self.book.settle()
        self.assertEqual(self.events, [('unpaid', 'A'), ('unpaid', 'B'), ('paid', 'A')])

    def test_hold_without_new_deal_does_not_erase_late_candidate(self):
        self.agree('A')
        self.timeout()
        self.book.hold(self.unit, 'B')
        self.book.release(self.unit)
        self.remove()
        self.book.settle()
        self.assertEqual(self.events, [('unpaid', 'A'), ('paid', 'A')])

    def test_same_task_late_payment_is_credited_once_for_both_ingresses(self):
        for ingress in ('inventory', 'mark_sold'):
            with self.subTest(ingress=ingress):
                self.inventory = [{'unit': self.unit, 'sku': 'G00000001', 'grade': 'A'}]
                self.book = StockBook(load=lambda: {'round': 3, 'units': self.inventory})
                self.events = []
                self.book.on_paid = lambda info: self.events.append(('paid', info['task']))
                self.book.on_unpaid = lambda info, why: self.events.append(('unpaid', info['task']))
                self.agree('A')
                self.timeout()
                if ingress == 'mark_sold':
                    self.book.mark_sold(self.unit)
                    self.book.mark_sold(self.unit)
                self.remove()
                self.book.settle()
                self.book.settle()
                self.assertEqual(self.events, [('unpaid', 'A'), ('paid', 'A')])

    def test_sale_of_other_unit_leaves_late_payment_eligible(self):
        self.agree('A')
        self.timeout()
        self.agree('C', self.other)
        self.book.mark_sold(self.other)
        self.remove(self.other)
        self.book.settle()
        self.remove()
        self.book.settle()
        self.assertEqual(self.events, [('unpaid', 'A'), ('paid', 'C'), ('paid', 'A')])

    def test_expired_late_inventory_is_not_paid(self):
        self.agree('A')
        self.timeout()
        self.inventory[0]['grade'] = 'expired'
        self.book.settle()
        self.remove()
        self.book.settle()
        self.assertEqual(self.events, [('unpaid', 'A'), ('unpaid', 'A')])

    def test_callback_reentry_cannot_find_stale_candidate(self):
        self.agree('A')
        self.timeout()
        self.agree('B')
        def paid(info):
            self.events.append(('paid', info['task']))
            self.remove()
            self.book.settle()
        self.book.on_paid = paid
        self.book.mark_sold(self.unit)
        self.assertEqual(self.events, [('unpaid', 'A'), ('paid', 'B')])

    def test_concurrent_mark_sold_calls_credit_one_new_deal(self):
        self.agree('A')
        self.timeout()
        self.agree('B')
        barrier = Barrier(8)
        def sell(_):
            barrier.wait(timeout=5)
            self.book.mark_sold(self.unit)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(sell, range(8)))
        self.remove()
        self.book.settle()
        self.assertEqual(self.events, [('unpaid', 'A'), ('paid', 'B')])


if __name__ == '__main__':
    unittest.main()
