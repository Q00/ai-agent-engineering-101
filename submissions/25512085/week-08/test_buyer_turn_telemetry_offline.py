"""Synthetic-only turn telemetry; no live market, key, state or network reads."""
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import copy
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
import market_buyer as buyer
import test_buyer_price_offline as price_tests


class BuyerTurnTelemetryTests(unittest.TestCase):
    setUp = price_tests.BuyerPriceTests.setUp
    receipt = price_tests.BuyerPriceTests.receipt
    run_buyer = price_tests.BuyerPriceTests.run_buyer

    def simulate(self, asks=(100000, 100000, 100000, 80000, 80000), done=4,
                 fail_at=None, failure=None, card_failure=None, reject_at=None,
                 bad_parse=False, sink=None, legacy=False, timeout_at=None):
        self.catalog[self.sku]['list_price'] = 103449
        calls, offers = [], []
        order = {} if legacy else self.order
        order = dict(order, account='SECRET_ACCOUNT', order='SECRET_ORDER', budget='SECRET_BUDGET')
        if legacy:
            order.pop('acceptable', None)
        @contextmanager
        def hub():
            yield self.hub
        def rpc(url, method, params):
            calls.append(method)
            if method == 'CancelTask':
                return {}
            text = params['message']['parts'][0]['text']
            if text.isascii() and text.isdigit():
                offers.append(int(text))
            elif text.startswith('I offer '):
                offers.append(int(text.split()[2]))
            index = len(offers)
            if index == fail_at:
                if isinstance(failure, Exception):
                    raise failure
                return {'error': 'SECRET_RPC_TEXT credential=https://secret.invalid'}
            state = ('TASK_STATE_REJECTED' if index == reject_at else
                     'TASK_STATE_COMPLETED' if index >= done else 'TASK_STATE_INPUT_REQUIRED')
            ask = asks[min(index, len(asks)-1)]
            task = dict(id='SECRET_TASK', status=dict(state=state, message=dict(parts=[
                {'text': f'ask {ask} cert warranty SECRET_DIALOG'}])))
            if state == 'TASK_STATE_COMPLETED':
                task['artifacts'] = [{'parts': [{'data': dict(self.receipt(ask), task='SECRET_TASK')}]}]
            return task
        output, errors = io.StringIO(), sink or io.StringIO()
        timeout_calls = [0]
        def timeout(default):
            timeout_calls[0] += 1
            if timeout_calls[0] == timeout_at:
                raise TimeoutError('SECRET_DEADLINE')
            return default
        with ExitStack() as stack:
            for name, value in {'rpc_call': rpc, 'update_memory': lambda *a: None,
                                'mutate_hub': hub, 'call_harness': lambda *a: 'OFFER 100',
                                'request_timeout': timeout}.items():
                stack.enter_context(patch.object(buyer, name, value))
            stack.enter_context(patch.object(buyer.httpx, 'get', side_effect=card_failure,
                return_value=SimpleNamespace(json=lambda: {'description': 'SECRET_CARD'})))
            if bad_parse:
                stack.enter_context(patch.object(buyer, 'extract_last_number', side_effect=ValueError('SECRET_PARSE')))
            with redirect_stderr(errors), redirect_stdout(output):
                result = buyer.run_attempt({'name': 'SECRET_ACCOUNT', 'url': 'https://secret.invalid'},
                    dict(sku=self.sku, name='synthetic', list_price=103449), 'synthetic', {}, self.hub,
                    self.catalog, order, 'food', 120000)
        raw = errors.getvalue()
        events = [json.loads(json.loads(line)[8:]) for line in raw.splitlines()
                  if json.loads(line).startswith('[buyer] {"event":"buyer_turn"')]
        self.assertEqual(output.getvalue(), '')
        return result, offers, calls, events, raw

    def test_every_boundary_and_parsed_prices_no_extra_rpc(self):
        result, offers, calls, events, raw = self.simulate()
        self.assertEqual(offers, [60000, 74000, 93104, 80000])
        self.assertEqual(calls, ['SendMessage'] * 5)
        self.assertEqual(len(events), 13)
        self.assertEqual([e['phase'] for e in events], ['card']*2 + ['opening']*2 + ['offer']*8 + ['attempt'])
        self.assertEqual([e['boundary'] for e in events[:-1]], ['request', 'response']*6)
        requests = [e for e in events if e['phase']=='offer' and e['boundary']=='request']
        self.assertEqual([e['policy_branch'] for e in requests], ['first','counter35','third','finalaccept'])
        self.assertEqual([e['turn_index'] for e in requests], [1,2,3,4])
        self.assertEqual([e['ask_before'] for e in requests], [100000,100000,100000,80000])
        self.assertEqual([e['offer_sent'] for e in requests], offers)
        responses = [e for e in events if e['phase']=='offer' and e['boundary']=='response']
        self.assertEqual([e['ask_after'] for e in responses], [100000,100000,80000,80000])
        self.assertEqual(events[3]['ask_after'], 100000)
        self.assertEqual(events[-1]['receipt_selected'], result['receipt'] is not None)
        self.assertTrue(events[-1]['receipt_selected'])
        self.assertFalse(events[-1]['payment_confirmed'])
        self.assertEqual({e['attempt_id'] for e in events}, {events[0]['attempt_id']})
        self.assertEqual([e['seq'] for e in events], list(range(1,14)))
        self.assertTrue(all(e['schema_version']==1 and e['timestamp_utc'].endswith('+00:00') for e in events))
        self.assertNotIn('SECRET_', '\n'.join(json.dumps(e) for e in events))
        self.assertNotIn('offline-signature-placeholder', '\n'.join(json.dumps(e) for e in events))
        self.assertNotIn('120000', json.dumps(events))
        self.assertTrue(all(len(json.dumps(e,separators=(',',':')).encode())+1<=buyer.TURN_EVENT_BYTES for e in events))
        wire_lines = [line for line in raw.splitlines() if 'buyer_turn' in line]
        lengths = [len(line.encode('utf-8'))+1 for line in wire_lines]
        self.assertLessEqual(max(lengths), buyer.TURN_EVENT_BYTES)
        print('SYNTHETIC_TELEMETRY_MEASURE='+json.dumps(dict(
            events=len(events), max_line_bytes=max(lengths), total_bytes=sum(lengths))))

    def test_card_opening_all_counter_and_final_network_failures(self):
        for index in range(5):
            with self.subTest(index=index):
                result, _, _, events, _ = self.simulate(fail_at=index,
                    failure=httpx.ReadTimeout('SECRET_TIMEOUT credential'))
                self.assertIsNone(result['receipt'])
                error = [e for e in events if e['outcome']=='timeout'][0]
                self.assertEqual(error['turn_index'], index)
                self.assertEqual(error['error_type'], 'TimeoutError')
                self.assertEqual(error['boundary'], 'response')
                self.assertEqual(events[-1]['outcome'], 'timeout')
                self.assertNotIn('SECRET_', json.dumps(events))
        result, _, calls, events, _ = self.simulate(card_failure=httpx.ConnectError('SECRET_HOST'))
        self.assertFalse(calls)
        self.assertEqual(events[1]['error_type'], 'HTTPError')
        self.assertEqual(events[-1]['outcome'], 'error')

    def test_deadlines_between_turns_and_before_receipt_return(self):
        for boundary in (3, 6, 7):
            with self.subTest(boundary=boundary):
                result, _, _, events, _ = self.simulate(timeout_at=boundary)
                self.assertIsNone(result['receipt'])
                self.assertEqual(events[-1]['outcome'], 'timeout')
                self.assertEqual(events[-1]['error_type'], 'TimeoutError')

    def test_existing_price_parser_is_not_called_again_for_telemetry(self):
        with patch.object(buyer, 'extract_last_number', wraps=buyer.extract_last_number) as parser:
            _, _, calls, _, _ = self.simulate()
        self.assertEqual(parser.call_count, calls.count('SendMessage'))

    def test_rpc_errors_rejections_and_parser_error(self):
        for index in range(5):
            with self.subTest(index=index):
                _, _, _, events, _ = self.simulate(fail_at=index)
                self.assertIn('RPCError', [e['error_type'] for e in events])
                self.assertEqual(events[-1]['outcome'], 'error')
                self.assertNotIn('SECRET_', json.dumps(events))
        _, _, _, events, _ = self.simulate(reject_at=1)
        self.assertEqual(events[-2]['response_state'], 'rejected')
        _, _, _, events, _ = self.simulate(bad_parse=True)
        self.assertEqual(events[-2]['phase'], 'opening')
        self.assertEqual(events[-2]['error_type'], 'ParseError')

    def test_immediate_receipt_and_third_no_final(self):
        for done in (0,1,2,3):
            with self.subTest(done=done):
                _, offers, _, events, _ = self.simulate(asks=(60000,)*5, done=done)
                self.assertEqual(len(offers), done)
                self.assertTrue(events[-1]['receipt_selected'])
                self.assertEqual(events[-2]['response_state'], 'completed')
                self.assertNotIn('finalaccept', [e['policy_branch'] for e in events])

    def test_logging_failure_preserves_result_calls_and_hub(self):
        original = copy.deepcopy(self.hub)
        expected, offers, calls, _, _ = self.simulate()
        expected_hub = copy.deepcopy(self.hub)
        self.hub = copy.deepcopy(original)
        class BrokenEvents(io.StringIO):
            def write(self, text):
                if 'buyer_turn' in text:
                    raise OSError('broken stderr')
                return super().write(text)
        actual, actual_offers, actual_calls, _, _ = self.simulate(sink=BrokenEvents())
        self.assertEqual((actual,actual_offers,actual_calls), (expected,offers,calls))
        self.assertEqual(self.hub, expected_hub)

    def test_bounded_hostile_fields_and_sink_serializer_clock_failures(self):
        active = dict(telemetry_enabled=True, telemetry_sku='SECRET_'*10000)
        out = io.StringIO()
        with redirect_stderr(out):
            for _ in range(100):
                buyer.turn_event(active,'opening','response',ask_after=10**1000, state='SECRET_'*10000)
        events = [json.loads(json.loads(line)[8:]) for line in out.getvalue().splitlines()]
        self.assertEqual(len(events), buyer.TURN_EVENT_MAX)
        self.assertNotIn('SECRET_', out.getvalue())
        self.assertTrue(all(e['sku'] is None and e['ask_after'] is None and e['response_state']=='other' for e in events))
        self.assertLessEqual(max(len(line.encode())+1 for line in out.getvalue().splitlines()), buyer.TURN_EVENT_BYTES)
        for name in ('json.dumps','datetime'):
            with self.subTest(name=name), patch('market_buyer.'+name, side_effect=RuntimeError('SECRET')):
                buyer.turn_event(dict(telemetry_enabled=True),'card','request')

    def test_legacy_has_no_events_and_attempt_ids_are_fresh(self):
        _, _, _, first, _ = self.simulate(done=0)
        _, _, _, second, _ = self.simulate(done=0)
        self.assertNotEqual(first[0]['attempt_id'], second[0]['attempt_id'])
        _, _, _, events, _ = self.simulate(done=0, legacy=True)
        self.assertEqual(events, [])

    def test_main_keeps_single_stdout_json_contract(self):
        capture, prints = io.StringIO(), []
        original = print
        def spy(*args, **kwargs):
            if kwargs.get('file') is None:
                prints.append(args[0])
            return original(*args, **kwargs)
        with redirect_stderr(capture), patch('builtins.print', side_effect=spy):
            result, _, _, _ = self.run_buyer(receipt=self.receipt(9000))
        self.assertEqual(len(prints), 1)
        self.assertEqual(json.loads(prints[0]), result)
        self.assertNotIn('buyer_turn', prints[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
