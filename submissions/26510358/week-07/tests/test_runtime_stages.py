"""Real SDK publication with TEST ONLY model outputs; no Market purchases."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_memory import MarketMemory
import market_buyer
import seller
from harness import Harness
from test_seller_cancel_http import actual_sdk_server, rpc, result_task, send_params, RecordingTrace, ITEM
import test_task_recovery as recovery_cases
from test_task_recovery import RecoveryPeer, completed, TASK_ID


def stages(trace):
    return [row for row in trace.events if row['event'] == 'trade_stage']


class SellerStages(unittest.TestCase):
    def test_opening_bid_does_not_guess_product_before_model_selection(self):
        with actual_sdk_server() as state:
            alternate = {**deepcopy(ITEM), 'id': 'lamp-alt'}
            state.executor.catalog.append(alternate)
            def decide(context, **kwargs):
                return kwargs['validator']({'action': 'open', 'item_id': 'lamp-alt',
                    'price': 110, 'offer': 110, 'reason': 'TEST ONLY alternate selected'})
            state.executor.harness.decide = decide
            with patch.object(seller, 'search_catalog', return_value=[deepcopy(ITEM), alternate]):
                opened = result_task(rpc(state, 'SendMessage', send_params('110')))
            rows = stages(state.executor.trace)
            self.assertNotIn('item', next(r for r in rows if r['stage'] == 'offer_received'))
            self.assertEqual(next(r for r in rows if r['stage'] == 'quote_issued')['item'], 'lamp-alt')
            self.assertEqual(opened['status']['message']['metadata']['itemId'], 'lamp-alt')

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
                    '00:00:02 [buyer] {"event":"market_trade_observed","task_id":"same-task","status":"receipt_matched","receipt":{"task":"same-task","price":100}}\n'
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

    def test_wrong_receipt_task_cannot_relabel_runtime_negotiation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'logs').mkdir()
            (root/'logs/market-kit.log').write_text(
                '00:00:00 [order order-1] lamp, budget 120, round 2\n'
                '00:00:01 [buyer] {"event":"market_shop_selected","shop":"fixture-shop"}\n'
                '00:00:02 [buyer] {"event":"market_trade_observed","task_id":"actual-task","status":"disputed","receipt":{"task":"wrong-task","price":100}}\n'
                '00:00:03 [sign] sig_seller ok, sig_buyer added\n'
                '00:00:04 [market] ledger #124: lamp at 100, verified True\n')
            memory = MarketMemory(root/'memory.json')
            trace = RecordingTrace()
            with patch.object(market_buyer, 'ROOT', root):
                market_buyer.buy([], 'lamp', 120, memory, trace)
            rows = stages(trace)
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]['task_id'], rows[0]['stage'], rows[0]['reason_code']),
                             ('actual-task', 'ended', 'local_contract_failed'))
            self.assertEqual(memory.read()['shops']['fixture-shop'].get('verified_completions', 0), 0)


class BuyerStages(unittest.TestCase):
    def test_market_boundary_keeps_disputed_receipt_as_evidence_without_submitting_it(self):
        trace = RecordingTrace()
        def decide(instance, context, **kwargs):
            output = ({'action': 'select_shop', 'item_id': 'fixture-shop', 'price': None,
                       'offer': None, 'reason': 'TEST ONLY one reachable shop'} if instance.role == 'shop_selector'
                      else {'action': 'accept', 'price': 100, 'item_id': 'lamp',
                            'offer': None, 'reason': 'TEST ONLY accepted quote'})
            return kwargs['validator'](output)
        with tempfile.TemporaryDirectory() as d, RecoveryPeer(reads=[completed(task='forged-task')]) as peer:
            memory = MarketMemory(Path(d)/'memory.json')
            with patch.object(Harness, 'decide', decide), patch.object(market_buyer, 'ROOT', Path(d)):
                result = market_buyer.buy([{'name': 'fixture-shop', 'url': peer.url, 'reviews': []}],
                                          'lamp', 120, memory, trace)
            self.assertIsNone(result['receipt'])
            observed = next(row for row in trace.events if row['event'] == 'market_trade_observed')
            self.assertEqual(observed['receipt']['task'], 'forged-task')
            self.assertEqual(observed['task_id'], TASK_ID)
            self.assertEqual(memory.read()['shops']['fixture-shop']['recent'][-1]['task'], TASK_ID)
            self.assertEqual([request['method'] for request in peer.requests], ['SendMessage', 'SendMessage', 'GetTask'])

    def test_lost_response_recovery_emits_one_dispatch_and_contract_match(self):
        helper = recovery_cases.RecoveryTests('test_lost_completed_response_recovers_same_task_without_replaying_offer')
        with RecoveryPeer(reads=[completed()]) as peer:
            result, _, trace, reads = helper.run_buyer(peer)
        rows = stages(trace)
        self.assertEqual([r['stage'] for r in rows], ['negotiation_started', 'quote_observed',
                         'offer_sent', 'receipt_matched', 'ended'])
        self.assertEqual(len(reads), 1)
        self.assertEqual(result['task_id'], TASK_ID)
        self.assertTrue(all(r['task_id'] == TASK_ID for r in rows))
        self.assertEqual(rows[2]['reason_code'], 'dispatch_attempt')
        self.assertEqual(rows[-2]['evidence'], 'receipt_contract')
        self.assertNotIn('market_verified', [r['stage'] for r in rows])

    def test_disputed_receipt_keeps_actual_task_linkage_and_never_claims_match(self):
        helper = recovery_cases.RecoveryTests('test_lost_completed_response_recovers_same_task_without_replaying_offer')
        with RecoveryPeer(reads=[completed(task='forged-task')]) as peer:
            result, ledger, trace, _ = helper.run_buyer(peer)
        self.assertEqual((result['status'], result['task_id']), ('disputed', TASK_ID))
        self.assertEqual(ledger['reserved_total'], 100)
        rows = stages(trace)
        self.assertNotIn('receipt_matched', [r['stage'] for r in rows])
        self.assertEqual((rows[-1]['task_id'], rows[-1]['outcome']), (TASK_ID, 'unresolved'))


if __name__ == '__main__':
    unittest.main()
