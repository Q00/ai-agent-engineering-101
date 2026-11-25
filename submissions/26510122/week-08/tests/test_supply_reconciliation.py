from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import inventory_portfolio
import market_memory
import replenishment_plan
from supply_reconciliation import reconcile


def intent(sku='F1', at=1000):
    return {'sku': sku, 'at': at, 'prior_units': [], 'cost_ceiling': 7500, 'status': 'issued_unknown'}


def proof(event='settled', **extra):
    return {'event': event, 'at': datetime.fromtimestamp(1050, timezone.utc).isoformat(),
            'sku': 'F1', 'unit': 'new', 'ledger': 1, **extra}


class ReconciliationTests(unittest.TestCase):
    def test_settled_unit_need_not_still_be_in_inventory(self):
        rows = [intent()]
        changes = reconcile(rows, {'units': []}, [proof()], 1100)
        self.assertEqual(rows[0]['status'], 'settled')
        self.assertEqual(rows[0]['settled_ledgers'], [1])
        self.assertEqual(len(changes), 1)
        self.assertEqual(reconcile(rows, {'units': []}, [proof()], 1100), [])
        self.assertEqual(rows[0]['cost_ceiling'], 7500)  # rolling spend is not refunded

    def test_signed_receipt_proves_arrival_but_not_settlement(self):
        rows = [intent()]
        reconcile(rows, {'units': []}, [proof(event='receipt_signed', ledger=None)], 1100)
        self.assertEqual(rows[0]['status'], 'observed_inventory')
        self.assertNotIn('settled_ledgers', rows[0])

    def test_old_units_old_events_and_ambiguous_repeated_intents_stay_reserved(self):
        for rows, event in [([intent()], proof(at=datetime.fromtimestamp(999, timezone.utc).isoformat())),
                            ([dict(intent(), prior_units=['new'])], proof()),
                            ([intent(), intent(at=1020)], proof()),
                            ([intent()], proof(ledger=None))]:
            reconcile(rows, {'units': []}, [event], 1100)
            self.assertTrue(all(r['status'] == 'issued_unknown' for r in rows))

    def test_reconcile_while_closed_updates_both_capacity_and_durable_status(self):
        with tempfile.TemporaryDirectory() as directory:
            before = Path.cwd(); os.chdir(directory)
            try:
                path = Path('state/inventory-portfolio.json'); path.parent.mkdir()
                path.write_text(json.dumps({'version': 1, 'windows': {}, 'intents': [intent()]}))
                market_memory.record('settled', **{k: v for k, v in proof().items() if k != 'event'})
                inv = {'round': 3, 'balance': 5000000, 'units': [],
                       'restock': {'open': False, 'opens_at': 2000, 'closes_at': 2180}}
                self.assertEqual(replenishment_plan.pending_exploration_cost(inv), 0)
                model = Mock(side_effect=AssertionError('no model call while supply closed'))
                result = inventory_portfolio.run(inv, {}, market_memory.events(), 1100,
                                                 model=model, refresh=lambda: (inv, 1100, True))
                self.assertEqual(result, {'buy': []})
                state = json.loads(path.read_text())
                self.assertEqual(state['intents'][0]['status'], 'settled')
                self.assertEqual(sum(i['cost_ceiling'] for i in state['intents']), 7500)
                model.assert_not_called()
            finally:
                os.chdir(before)


if __name__ == '__main__':
    unittest.main()
