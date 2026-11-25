"""Partial recovery uses temporary files and never contacts the market."""
import json
from pathlib import Path
import socket
import tempfile
import unittest

from r3_checkout import CheckoutGate
from r3_common import atomic_json, read_json
from r3_host_recovery import hold_old_units, recover, restore_memories
from r3_memory import Memory
from r3_seller import Reservations


class HostRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.md = self.root / 'market'
        self.md.mkdir()

    def test_recovery_hold_cannot_expire_reopen_or_sell_an_old_unit(self):
        reservations = Reservations(self.md)
        old = dict(unit='old', sku='sku', kind='goods')
        new = dict(unit='new', sku='sku', kind='goods')
        hold_old_units(reservations, {'units': [old]}, {'units': []})
        report = reservations.reconcile({'units': [old, new]}, [], 'offline', 10**10)
        self.assertEqual(report['recovery_held'], 1)
        self.assertEqual(report['blocked_in_inventory'], 1)
        self.assertFalse(report['safe_unlock_available'])
        with self.assertRaises(ValueError):
            reservations.begin('old-request', [old], {'old'}, 100, 200)
        picked, _, _, _ = reservations.begin('new-request', [old, new], {'old', 'new'}, 100, 200)
        self.assertEqual(picked, 'new')
        with self.assertRaises(ValueError):
            CheckoutGate(self.md).begin({'task': 'host-recovery:old'}, now=100)

    def test_sold_unit_tombstones_are_not_turned_into_sellable_inventory(self):
        reservations = Reservations(self.md)
        hold_old_units(reservations, {'units': []},
                       {'units': [{'unit': 'sold', 'sku': 'sku', 'status': 'settled'}]})
        with self.assertRaises(ValueError):
            reservations.begin('delayed-snapshot', [{'unit': 'sold', 'sku': 'sku'}], {'sold'}, 100, 200)

    def test_purchase_memory_requires_matching_original_paid_evidence(self):
        payment = dict(order='o', paid=True, ledger=23, price=60, item='sku', shop='https://shop.test')
        (self.md/'payments.jsonl').write_text(json.dumps(payment)+'\n')
        event = dict(kind='settlement', key='23', ts=100,
                     data=dict(order='o', shop='shop-name', sku='sku',
                               receipt={'price': 60}, settled={'ledger': 23, 'sku': 'sku', 'unit': 'u'}))
        bad = dict(event, key='24', data=dict(event['data'], order='not-paid',
                                           settled={'ledger': 24, 'sku': 'sku', 'unit': 'other'}))
        log = self.root/'old.log'
        row = dict(time='2020-01-01T00:00:00+00:00', event='decision_input', order='o',
                   state={'personal_records': {'recent': [event, bad], 'pending': []}})
        log.write_text(json.dumps(row)+'\n')
        with Memory(self.md) as memory:
            restore_memories(memory, self.md, [log])
            restored = memory.get('settlement', '23')
            self.assertEqual(restored['data']['shop_url'], payment['shop'])
            self.assertEqual(restored['ts'], 100)
            self.assertIsNone(memory.get('settlement', '24'))
            self.assertIsNotNone(memory.get('recovered_context', 'settlement:24'))
            self.assertEqual(memory.get('payment', 'o')['ts'], 1577836800)
            self.assertFalse((self.md/'outbox.jsonl').exists())

    def test_flat_curator_projection_can_restore_paid_history_without_inventing_unit_or_signature(self):
        payment = dict(order='o', paid=True, ledger=23, price=60, item='sku', shop='https://shop.test')
        (self.md/'payments.jsonl').write_text(json.dumps(payment)+'\n')
        event = dict(kind='settlement', key='23', ts=100,
                     data=dict(shop='shop-name', ledger=23, sku='sku', price=60, value=100))
        log = self.root/'old.log'
        log.write_text(json.dumps(dict(time='2020-01-01T00:00:00+00:00', event='decision_input',
                                     state={'personal_records': {'recent': [event]}}))+'\n')
        with Memory(self.md) as memory:
            restore_memories(memory, self.md, [log])
            restored = memory.get('settlement', '23')['data']
            self.assertEqual(restored['order'], 'o')
            self.assertEqual(restored['settled']['value'], 100)
            self.assertIsNone(restored['unit'])
            self.assertNotIn('sig_seller', restored['receipt'])

    def backup(self):
        backup = self.root/'backup'
        source = backup/'original-market'
        source.mkdir(parents=True)
        pointer = {'host': 'missing-old-host', 'path': str(self.root/'unavailable-original-db')}
        atomic_json(self.md/'db-location.json', pointer)
        atomic_json(source/'db-location.json', pointer)
        (source/'payments.jsonl').write_text('')
        logs = backup/'original-logs'
        logs.mkdir()
        for name in ('market-kit.log', 'market-kit.log.1'):
            (logs/name).write_text('')
        return backup, pointer

    def test_explicit_recovery_preserves_original_pointer_and_creates_three_valid_dbs(self):
        backup, pointer = self.backup()
        destination = self.root/'new-db'
        result = recover(self.md, destination, backup)
        self.assertEqual(result['mode'], 'partial_recovery_not_original_database')
        self.assertEqual(read_json(backup/'original-market/db-location.json'), pointer)
        self.assertEqual(read_json(self.md/'db-location.json'),
                         {'host': socket.gethostname(), 'path': str(destination)})
        self.assertEqual(len(list(destination.glob('*.sqlite3'))), 3)

    def test_existing_destination_or_changed_pointer_is_never_overwritten(self):
        backup, pointer = self.backup()
        destination = self.root/'existing-db'
        destination.mkdir()
        with self.assertRaises(RuntimeError):
            recover(self.md, destination, backup)
        self.assertEqual(read_json(self.md/'db-location.json'), pointer)
        atomic_json(self.md/'db-location.json', dict(pointer, host=socket.gethostname()))
        with self.assertRaises(RuntimeError):
            recover(self.md, self.root/'new-db', backup)
