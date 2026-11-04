"""Regression fixtures for the first live startup; no network, model or real keys."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r3_buyer import acceptance, load_order
from r3_common import load_inventory
from r3_market_kit import host_timestamped_writer


class LiveContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.md = Path(tmp.name)

    def test_goods_null_horizon_is_valid_but_food_null_is_not(self):
        data = dict(order='offline-order', item='chair', category='furniture',
                    budget=100, acceptable=['G001'], kind='goods',
                    use_within_hours=None, expires=2_000_000_000)
        path = self.md / 'order.json'
        path.write_text(json.dumps(data))
        order = load_order(path, 'chair', 100)
        self.assertEqual(order['use_within_hours'], 0)
        self.assertEqual(acceptance(order, {})['min_hours_left'], 0)
        self.assertIsNone(json.loads(path.read_text())['use_within_hours'])
        for kind, horizon in (('food', None), ('goods', -1), ('goods', True)):
            path.write_text(json.dumps(dict(data, kind=kind, use_within_hours=horizon)))
            with self.assertRaises(ValueError):
                load_order(path, 'chair', 100)

    def test_goods_null_horizon_completes_the_buyer_fixture(self):
        from test_r3_buyer import BuyerTests
        fixture = BuyerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.order.update(kind='goods', use_within_hours=None)
        fixture.catalog['F001']['kind'] = 'goods'
        fixture.write()
        self.assertIsNotNone(fixture.execute()['receipt'])

    def test_nfs_clock_skew_corrected_without_disabling_staleness_check(self):
        path = self.md / 'inventory.json'
        data = dict(round=3, balance=100, units=[])
        def nfs_writer(target, value):
            target.write_text(json.dumps(value))
            os.utime(target, (1000, 1000))
        with patch('r3_market_kit.time.time', return_value=1620):
            host_timestamped_writer(nfs_writer)(path, data)
        self.assertEqual(path.stat().st_mtime, 1620)
        self.assertEqual(load_inventory(self.md, now=1625), data)
        with self.assertRaises(ValueError):
            load_inventory(self.md, now=1666)

    def test_failed_inventory_write_cannot_freshen_old_snapshot(self):
        path = self.md / 'inventory.json'
        path.write_text(json.dumps(dict(round=3, balance=100, units=[])))
        os.utime(path, (1000, 1000))
        def fail(target, value):
            raise OSError('offline simulated write failure')
        with self.assertRaises(OSError):
            host_timestamped_writer(fail)(path, {})
        self.assertEqual(path.stat().st_mtime, 1000)

    def test_non_inventory_writes_keep_the_original_writer_contract(self):
        path = self.md / 'forum.json'
        def writer(target, value):
            target.write_text(json.dumps(value))
            os.utime(target, (1000, 1000))
        host_timestamped_writer(writer)(path, [])
        self.assertEqual(path.stat().st_mtime, 1000)
