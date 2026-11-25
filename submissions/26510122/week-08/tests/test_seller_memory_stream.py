import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import market_memory as memory
import procurement_scheduler as scheduler


class SellerMemoryStreamTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = self.root / 'events.jsonl'

    def test_stream_keeps_legacy_and_task_only_outcomes(self):
        opening = {'event': 'opening', 'at': 1, 'task_id': 'a', 'sku': 'tea', 'grade': 'A'}
        offer = {'event': 'offer', 'at': 2, 'task_id': 'a', 'price': 1000}
        settled = {'at': '2026-11-07T00:00:00Z', 'event': 'settled', 'task_id': 'a',
                   'price': 1000, 'ledger': 10, 'grade': 'A'}
        with self.log.open('w') as log:
            log.write('broken\n')
            for row in (opening, offer, settled, {'event': 'seller_message'}):
                log.write(json.dumps(row, separators=(',', ':')) + '\n')
            log.write('{"event":"opening"')

        actual = memory.trade_attempts({'tea'}, path=self.log)
        self.assertEqual(actual, memory._attempts([opening, offer, settled]))
        self.assertEqual(actual[0]['outcome'], 'SETTLED')
        self.assertEqual(actual[0]['offers'], [1000])
        self.assertEqual(list(memory.iter_events(self.log, kinds={'settled'})), [settled])

    def test_settlement_lookup_only_when_a_complete_settlement_arrives(self):
        kit = self.root / 'kit.log'
        cursor = self.root / 'cursor.json'
        kit.write_text('inventory refreshed\n[acp] settled a at 1000, ledger #10, unit u grade A')
        with patch.object(memory, 'iter_events', side_effect=AssertionError('scanned history')):
            self.assertEqual(memory.sync_settlements(kit, event_log=self.log, cursor_path=cursor), 0)
        memory.record('receipt_signed', path=self.log, task_id='a', sku='tea', price=1000, unit='u')
        with kit.open('a') as source:
            source.write('\n')
        self.assertEqual(memory.sync_settlements(kit, event_log=self.log, cursor_path=cursor), 1)
        self.assertEqual(memory.sync_settlements(kit, event_log=self.log, cursor_path=cursor), 0)
        rows = memory.events(self.log, kinds={'settled'})
        self.assertEqual([(r['ledger'], r['task_id'], r['sku']) for r in rows], [(10, 'a', 'tea')])

    def test_planning_keeps_historical_costs_and_snapshot_boundary(self):
        start = 5000
        now = start + scheduler.CONVERSION_WINDOW_HOURS * 3600
        old_receipt = {'event': 'receipt_signed', 'at': 1, 'unit': 'u', 'cost': 400}
        old_paid = {'event': 'settled', 'at': 2, 'unit': 'u', 'ledger': 5}
        response = {'event': 'procurement_supply_result', 'at': 3, 'intent_id': 'i'}
        edge_snapshot = {'event': 'inventory_snapshot', 'at': start - 120, 'source_time': start - 120}
        recent = {'event': 'request_seen', 'at': start}
        stale = [{'event': 'request_seen', 'at': start - 1},
                 {'event': 'inventory_snapshot', 'source_time': start - 121}]
        with patch.object(scheduler, 'iter_events', return_value=iter(
                [old_receipt, old_paid, response, *stale, edge_snapshot, recent])):
            self.assertEqual(scheduler.planning_history(now),
                             [old_receipt, old_paid, response, edge_snapshot, recent])
