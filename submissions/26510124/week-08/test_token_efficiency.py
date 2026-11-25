"""Offline cost controls: no model, network, or real trading calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import model_backend as backend
import model_usage
from shop_ops_efficiency import model_gate
from shop_ops_policy import config_at
from buyer_fast_selection import exploration_turn
from buyer_policy import buying_terms, load_policy
from market_buyer import choose_shop
from test_buyer_selection import shop

ROOT = Path(__file__).resolve().parent


class UsageTests(unittest.TestCase):
    def test_claude_cache_counts_are_not_double_counted_and_absence_is_unknown(self):
        raw = json.dumps({'usage': {'input_tokens': 10, 'output_tokens': 2, 'cache_read_input_tokens': 300},
                          'modelUsage': {'haiku': {'inputTokens': 10, 'outputTokens': 2, 'cacheReadInputTokens': 300}}})
        row = model_usage.extract('haiku', raw)
        self.assertEqual(row['input_tokens'], 10)
        self.assertEqual(row['cache_read_tokens'], 300)
        self.assertIsNone(row['cache_creation_tokens'])
        self.assertFalse(row['input_includes_cache'])
        self.assertIsNone(model_usage.extract('opus5.5', '{}')['input_tokens'])

    def test_codex_input_already_includes_cache(self):
        row = model_usage.extract('sol', json.dumps({'type': 'turn.completed', 'usage': {
            'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 4}}))
        self.assertEqual(row['input_tokens'], 100)
        self.assertEqual(row['cache_read_tokens'], 80)
        self.assertTrue(row['input_includes_cache'])

    def test_concurrent_records_are_distinct_and_private_content_is_omitted(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.market') as folder:
            logs = Path(folder)/'logs'; logs.mkdir()
            with (logs/'synthetic.log').open('w') as log:
                def write(_):
                    return model_usage.record(log, profile='haiku', prompt_name='seller_inventory.txt',
                        prompt='SECRET PROMPT', started=time.monotonic(), stdout='SECRET OUTPUT', status='timeout')
                with ThreadPoolExecutor(max_workers=4) as pool:
                    self.assertTrue(all(pool.map(write, range(12))))
            report = model_usage.report(folder)
            self.assertEqual(report['calls_observed'], 12)
            group = report['groups']['shop/haiku']
            self.assertEqual(group['missing']['input_tokens'], 12)
            self.assertEqual(group['cli_errors'], 12)
            for path in (logs/'model-usage').glob('*.json'):
                self.assertNotIn('SECRET', path.read_text())

    def test_timeout_is_metered_without_changing_exception(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.market') as folder:
            logs=Path(folder)/'logs';logs.mkdir()
            with (logs/'timeout.log').open('w') as log, patch.object(backend, 'execute', side_effect=subprocess.TimeoutExpired('cli', 1)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    backend.measured_execute([], profile='haiku', prompt_name='buyer.txt', log=log, prompt='x')
            self.assertEqual(model_usage.report(folder)['groups']['buyer_negotiation/haiku']['cli_errors'], 1)

    def test_unwritable_telemetry_does_not_interrupt_model_result(self):
        with patch('model_usage.Path.mkdir', side_effect=OSError('disk full')), patch.object(backend, 'execute', return_value='answer'):
            log=io.StringIO();log.name=str(ROOT/'logs/test.log')
            self.assertFalse(model_usage.record(log, profile='haiku', prompt_name='buyer.txt', prompt='x',
                started=time.monotonic(), stdout=None, status='timeout'))

    def test_haiku_json_unwrap_and_tool_rejection(self):
        envelope = {'type':'result','subtype':'success','is_error':False,'num_turns':1,
                    'permission_denials':[], 'modelUsage':{'claude-haiku-4-5':{'outputTokens':4}},
                    'result':'{"action":"accept","price":10}'}
        self.assertEqual(backend.answer('haiku', subprocess.CompletedProcess([],0,json.dumps(envelope),''), ROOT), envelope['result'])
        for change in ({'num_turns':2}, {'modelUsage':{'claude-opus-5-5':{'outputTokens':4}}}, {'permission_denials':[{}]}):
            with self.assertRaises(ValueError):
                backend.answer('haiku', subprocess.CompletedProcess([],0,json.dumps({**envelope,**change}),''), ROOT)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.cfg=config_at(ROOT)
        self.obs={'round':3,'rules_version':'3.6','current':{'observed_tasks':20},
                  'candidates':[{'sku':'G12345678','eligible':True}], 'task_sample':[{'task_id':'one','receipt':False}]}
        self.state={'version':'v1'}
        self.control={}

    def gate(self, now, **kw):
        return model_gate(self.obs, self.state, self.control, self.cfg, now=now, **kw)

    def initial(self):
        yes, _, fingerprint=self.gate(10000)
        self.assertTrue(yes)
        self.control.update(last_model_at=10000,last_model_evidence=fingerprint)

    def test_unchanged_snapshot_clock_and_storage_do_not_call_again(self):
        self.initial()
        self.obs.update(snapshot_id='new',generated_at=20000)
        self.obs['current'].update(estimated_storage=123, timing={'p50':7}, net_estimate=10)
        self.assertEqual(self.gate(20000)[1], 'unchanged_evidence')

    def test_new_tasks_wait_two_hours_even_when_counts_unchanged(self):
        self.initial();self.obs['task_sample'][0]['task_id']='two'
        self.assertEqual(self.gate(11800)[1], 'model_interval_wait')
        self.assertTrue(self.gate(17200)[0])

    def test_technical_events_bypass_regular_interval_but_not_minimum_spacing(self):
        self.initial()
        self.assertFalse(self.gate(10001,event=True)[0])
        self.assertTrue(self.gate(10300,event=True)[0])
        self.assertTrue(self.gate(10300,requested=True)[0])
        self.assertFalse(self.gate(9999,event=True)[0])

    def test_no_eligible_experiment_skips_model_and_policy_change_invalidates_digest(self):
        self.initial();self.obs['candidates']=[]
        self.assertEqual(self.gate(20000)[1], 'no_eligible_experiment_targets')
        self.obs['candidates']=[{'sku':'G12345678','eligible':True}]
        self.state['version']='v2'
        self.assertTrue(self.gate(20000)[0])


class RetrievalTests(unittest.TestCase):
    def test_verified_sku_beyond_five_offer_projection_skips_selection_model(self):
        now=10000
        config=load_policy()
        seed=next(str(n) for n in range(50) if not exploration_turn(str(n)))
        order={'round':3,'kind':'goods','order':seed}
        catalog={f'G{n:05}':{'list_price':100000} for n in range(1,21)}
        shops=[shop('proven',[4,5])]
        shops[0]['offers']=[{'sku':sku,'name':sku,'qty':1} for sku in catalog]
        # No price-history builder input: recent verified receipt alone qualifies.
        record={'name':'proven','item':'G00020','round':3,'result':'settled',
            'time':datetime.fromtimestamp(now-30,timezone.utc).isoformat(),'ledger':17,'price':70000,
            'verified_review':True,'review_issues':[], 'stars':4,
            'purchase_policy':buying_terms(100000,product=catalog['G00020'],order=order,config=config),
            'observed_receipt':{'item':'G00020','price':70000,'task':'old-task'}}
        state={}
        with patch('buyer_fast_selection.time.time',return_value=now), \
             patch('market_buyer.build_value_evidence',return_value={}), \
             patch('market_buyer.decide',side_effect=AssertionError('No selection model needed')):
            selected,_=choose_shop(shops,[record],item='goods',budget=100000,deadline=time.monotonic()+30,
                                   policy=config,order=order,catalog=catalog,selection_state=state)
        self.assertEqual(selected['selected_sku'],'G00020')
        self.assertLessEqual(len(state['candidates'][0]['offers']),5)
        self.assertNotIn('observed_receipt',selected)  # old receipt cannot authorize new payment
