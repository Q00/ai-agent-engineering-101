"""Real SDK publication with TEST ONLY model outputs; no Market purchases."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_memory import MarketMemory
import market_buyer
import seller
from test_seller_cancel_http import actual_sdk_server, rpc, result_task, send_params, RecordingTrace


def stages(trace):
    return [row for row in trace.events if row['event'] == 'trade_stage']


class SellerStages(unittest.TestCase):
    def test_same_task_completion_records_validated_stages_and_local_priority(self):
        with actual_sdk_server() as state:
            priorities = []
            def decide(context, **kwargs):
                priorities.append(kwargs.get('priority', 'normal'))
                return kwargs['validator']({'action': 'open' if context['opening'] else 'accept',
                    'item_id': 'lamp', 'price': 130 if context['opening'] else context['observed_offer'],
                    'offer': context['observed_offer'], 'reason': 'TEST ONLY'})
            state.executor.harness.decide = decide
            request = send_params('lamp')
            request['message']['metadata'] = {'priority': 'completion'}
            opened = result_task(rpc(state, 'SendMessage', request))
            task_id = opened['id']
            accepted = result_task(rpc(state, 'SendMessage', send_params('130', task_id)))
            self.assertEqual(accepted['status']['state'], 'TASK_STATE_COMPLETED')
            self.assertEqual(priorities, ['normal', 'completion'])
            rows = stages(state.executor.trace)
            self.assertEqual([r['stage'] for r in rows], ['negotiation_started', 'quote_issued',
                'offer_received', 'offer_accepted', 'receipt_issued', 'ended'])
            self.assertTrue(all(r['task_id'] == task_id and r['side'] == 'seller' for r in rows))
            self.assertEqual(next(r for r in rows if r['stage'] == 'offer_accepted')['evidence'], 'validated_decision')
            self.assertEqual(next(r for r in rows if r['stage'] == 'receipt_issued')['evidence'], 'local_signature')
            self.assertNotIn('market_verified', [r['stage'] for r in rows])
            self.assertNotIn('reserve', json.dumps(rows))

    def test_low_bid_cannot_claim_completion_priority_through_metadata(self):
        with actual_sdk_server() as state:
            priorities = []
            def decide(context, **kwargs):
                priorities.append(kwargs.get('priority', 'normal'))
                return kwargs['validator']({'action': 'open' if context['opening'] else 'counter',
                    'item_id': 'lamp', 'price': 130, 'offer': context['observed_offer'], 'reason': 'TEST ONLY'})
            state.executor.harness.decide = decide
            opened = result_task(rpc(state, 'SendMessage', send_params('lamp')))
            request = send_params('90', opened['id'])
            request['message']['metadata'] = {'priority': 'completion', 'budget': 99999}
            counter = result_task(rpc(state, 'SendMessage', request))
            self.assertEqual(counter['status']['state'], 'TASK_STATE_INPUT_REQUIRED')
            self.assertEqual(priorities, ['normal', 'normal'])
            self.assertNotIn('offer_accepted', [r['stage'] for r in stages(state.executor.trace)])

    def test_signature_failure_preserves_acceptance_but_does_not_claim_receipt_issue(self):
        with actual_sdk_server() as state:
            opened = result_task(rpc(state, 'SendMessage', send_params('lamp')))
            with patch.object(seller, 'sign_receipt', side_effect=ValueError('TEST ONLY signing failure')):
                failed = result_task(rpc(state, 'SendMessage', send_params('100', opened['id'])))
            self.assertEqual(failed['status']['state'], 'TASK_STATE_FAILED')
            rows = stages(state.executor.trace)
            self.assertIn('offer_accepted', [r['stage'] for r in rows])
            self.assertNotIn('receipt_issued', [r['stage'] for r in rows])
            self.assertEqual(rows[-1]['outcome'], 'unresolved')
            self.assertEqual(rows[-1]['reason_code'], 'receipt_validation_failed')

    def test_cancellation_records_explicit_outcome(self):
        with actual_sdk_server() as state:
            opened = result_task(rpc(state, 'SendMessage', send_params('lamp')))
            result_task(rpc(state, 'CancelTask', {'id': opened['id']}))
            self.assertEqual(stages(state.executor.trace)[-1]['outcome'], 'canceled')


class AuthoritativeFeedbackStages(unittest.TestCase):
    def test_market_success_requires_both_kit_signature_and_ledger_verdict(self):
        for signature, verified, expected in [('ok', True, 'market_verified'),
                                               ('ok', False, 'ended'), ('invalid', True, 'ended')]:
            with self.subTest(signature=signature, verified=verified), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root/'logs').mkdir()
                sign = 'sig_seller ok, sig_buyer added' if signature == 'ok' else 'sig_seller does not verify with the shop\'s registered key'
                (root/'logs/market-kit.log').write_text(
                    '00:00:00 [order order-1] lamp, budget 120, round 2\n'
                    '00:00:01 [buyer] {"event":"market_shop_selected","shop":"fixture-shop"}\n'
                    '00:00:02 [buyer] {"event":"market_trade_observed","receipt":{"task":"same-task","price":100}}\n'
                    f'00:00:03 [sign] {sign}\n'
                    f'00:00:04 [market] ledger #123: lamp at 100, verified {verified}\n')
                memory = MarketMemory(root/'memory.json')
                trace = RecordingTrace()
                with patch.object(market_buyer, 'ROOT', root):
                    market_buyer.buy([], 'lamp', 120, memory, trace)
                rows = stages(trace)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['stage'], expected)
                self.assertEqual(rows[0]['task_id'], 'same-task')
                if verified and signature == 'ok':
                    self.assertEqual(rows[0]['time_basis'], 'feedback_ingested')
                    self.assertEqual(rows[0]['evidence'], 'market_kit_feedback')
                else:
                    self.assertEqual(rows[0]['outcome'], 'unresolved')


if __name__ == '__main__':
    unittest.main()
