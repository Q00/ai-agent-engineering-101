import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

from market_memory import events
from procurement_transport import instrument,lines
from procurement_legacy import discover
from procurement_monitor import maintain,health
from supply_reconciliation import reconcile,pending_quantity,reserved_quantity

ROOT=Path(__file__).resolve().parents[1]
KIT=ROOT.parents[2]/'scripts/market_kit.py'


def intent(sku='s0',qty=1):
    return {'sku':sku,'qty':qty,'at':1000,'window':'900:1200','cost_ceiling':100,
        'prior_units':[],'status':'issued_unknown','decision_id':'d1','intent_id':f'd1:{sku}'}


def protocol(count=2):
    return {'buy':[{'sku':f's{i}','qty':1} for i in range(count)],
        '_procurement':{'decision_id':'d1','episode_id':'e1','window':'900:1200'}}


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.previous=Path.cwd();os.chdir(self.temp.name)
        spec=importlib.util.spec_from_file_location('supply_test_official_kit',KIT)
        self.kit=importlib.util.module_from_spec(spec);sys.modules[spec.name]=self.kit;spec.loader.exec_module(self.kit)
        self.kit.run_buyer=Mock(return_value=[json.dumps(protocol())])
        self.kit.refresh_inventory=Mock();self.kit.log=Mock()
        instrument(self.kit)
    def tearDown(self):
        os.chdir(self.previous);self.temp.cleanup()
    def run_batch(self,responses):
        market=Mock();market.call.side_effect=[Mock(status_code=s,text='',json=lambda:{'total':100,'balance':5000000})
            if isinstance(s,int) else s for s in responses]
        with patch.object(self.kit.time,'sleep'):
            self.kit.restock({'restock':{'cmd':'test'}},Path('.'),market,Path('.market'),
                {'balance':5000000,'bankrupt_line':500000},{})
        return market
    def test_real_kit_402_releases_rejected_and_unstarted_lines(self):
        market=self.run_batch([402]);self.assertEqual(market.call.call_count,1)
        rows=[intent('s0'),intent('s1')];reconcile(rows,{'units':[]},events(),1100)
        self.assertEqual([r['supply_resolution'] for r in rows],['rejected','not_sent'])
        self.assertEqual(sum(map(pending_quantity,rows)),0)
        self.assertEqual(sum(map(reserved_quantity,rows)),0)
        self.assertEqual(len([e for e in events() if e['event']=='procurement_protocol_received']),2)
    def test_success_and_server_error_stay_reserved_without_physical_evidence(self):
        self.run_batch([200,502]);rows=[intent('s0'),intent('s1')]
        reconcile(rows,{'units':[]},events(),1100)
        self.assertEqual([r['supply_resolution'] for r in rows],['accepted','unknown'])
        self.assertEqual(sum(map(pending_quantity,rows)),2)
        self.assertTrue(all('response' not in e and 'body' not in e for e in events()))
    def test_network_exception_holds_attempted_but_releases_never_sent(self):
        with self.assertRaises(TimeoutError):self.run_batch([TimeoutError('sensitive response omitted')])
        rows=[intent('s0'),intent('s1')];reconcile(rows,{'units':[]},events(),1100)
        self.assertEqual([pending_quantity(r) for r in rows],[1,0])
        self.assertNotIn('sensitive response',json.dumps(events()))
    def test_exhausted_429_is_final_rejection_and_retry_success_is_accepted(self):
        self.run_batch([429,429,429,200]);rows=[intent('s0'),intent('s1')]
        reconcile(rows,{'units':[]},events(),1100)
        self.assertEqual([r['supply_resolution'] for r in rows],['rejected','accepted'])
    def test_unlinked_protocol_cannot_release_reservations(self):
        self.assertEqual(lines({'buy':[{'sku':'s0','qty':1}]}),[])
        self.assertEqual(lines({**protocol(),'buy':[{'sku':'s0','qty':1}]*2}),[])
    def test_contradictory_evidence_restores_conservative_reservation(self):
        row=intent(qty=2);e={**lines(protocol())[0],'event':'procurement_supply_result','outcome':'rejected','qty':2}
        reconcile([row],{'units':[]},[e],1100);self.assertEqual(pending_quantity(row),0)
        reconcile([row],{'units':[{'sku':'s0','unit':'u','supplied_at':1005}]},[e],1101)
        self.assertEqual(row['supply_resolution'],'conflicting_evidence');self.assertEqual(pending_quantity(row),1)
    def test_wrong_identity_quantity_and_accepted_evidence_prevent_release(self):
        base={**lines(protocol())[0],'event':'procurement_supply_result','outcome':'rejected'}
        for delta in ({'decision_id':'other'},{'qty':2},{'window':'0:1'}):
            row=intent();reconcile([row],{'units':[]},[{**base,**delta}],1100)
            self.assertEqual(pending_quantity(row),1)
        row=intent();reconcile([row],{'units':[]},[{**base,'outcome':'accepted'},base],1100)
        self.assertEqual(pending_quantity(row),1)
        row={**intent(),'supply_resolution':'accepted'}
        reconcile([row],{'units':[]},[base],1100)
        self.assertEqual(pending_quantity(row),1)
    def test_monitor_persists_reconciliation_and_deduplicates_warnings(self):
        state={'version':1,'windows':{},'intents':[intent('s0'),intent('s1')]}
        path=Path('state/inventory-portfolio.json');path.parent.mkdir();path.write_text(json.dumps(state))
        self.run_batch([400,502]);history=events()
        first=maintain({'units':[]},history,2000,state_path=path,ready_path=Path('missing'))
        self.assertEqual(first['pending_units'],1);self.assertEqual(first['alerts'][0]['stage'],'arrival_unconfirmed')
        count=len([e for e in events() if e['event']=='procurement_health_warning'])
        maintain({'units':[]},history,2100,state_path=path,ready_path=Path('missing'))
        self.assertEqual(count,len([e for e in events() if e['event']=='procurement_health_warning']))
    def test_missing_execution_and_missing_response_are_visible(self):
        result=health({'intents':[]},{'status':'claimed','claimed_at':1000,'window':'900:1200'},
            [{'event':'procurement_request_started','request_id':'r','window':'900:1200','at':1000}],1100)
        self.assertEqual({a['stage'] for a in result['alerts']},{'claimed_without_execution','request_without_response'})


class LegacyTests(unittest.TestCase):
    def sample(self):
        buy=[{'sku':f's{i}','qty':1} for i in range(12)]
        rows=[intent(f's{i}') for i in range(12)]
        history=[{'event':'portfolio_exploration_intent','at':1000,'buy':buy,'decision_id':'d1'}]
        clock=time.strftime('%H:%M:%S',time.localtime(1000))
        text=f'{clock} [buyer] '+json.dumps({'buy':buy})+'\n'
        text+=f"{clock} [supply] {{'sku': 's0', 'qty': 1}} refused: 400 invalid\n"
        text+=f"{clock} [supply] {{'sku': 's1', 'qty': 1}} refused: 502\n"
        return rows,history,text
    def test_only_explicit_rejection_and_actual_kit_10_line_omissions_release(self):
        rows,h,text=self.sample();proof=discover(rows,h,text)
        reconcile(rows,{'units':[]},proof,1100)
        self.assertEqual({r['sku'] for r in rows if not pending_quantity(r)},{'s0','s10','s11'})
        self.assertEqual(len(proof),3)
        from io import StringIO
        self.assertEqual(discover(copy.deepcopy(rows),h,StringIO(text)),proof)
    def test_duplicate_batches_days_or_intents_are_ambiguous(self):
        rows,h,text=self.sample()
        self.assertEqual(discover(copy.deepcopy(rows),h,text+text),[])
        self.assertEqual(discover(copy.deepcopy(rows),h+[dict(h[0],at=1000+86400)],text),[])
        rows.append(copy.deepcopy(rows[0]));proof=discover(rows,h,text)
        self.assertFalse(any(e['sku']=='s0' for e in proof))


if __name__=='__main__':unittest.main()
