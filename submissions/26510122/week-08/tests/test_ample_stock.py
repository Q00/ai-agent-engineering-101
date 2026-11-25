from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import tempfile
import unittest

import inventory_portfolio as portfolio
import replenishment_plan as plans
from stock_policy import sales_evidence, replenishment_candidates
from supply_reconciliation import reconcile, pending_quantity
from test_inventory_portfolio import catalog, inventory, reviewed_model


def history(n=3):
    rows = []
    for i in range(n):
        at = datetime.fromtimestamp(990+i, timezone.utc).isoformat()
        rows += [{'event':'receipt_signed','sku':'s1','unit':f'sold{i}','cost':4000,'at':at},
                 {'event':'settled','sku':'s1','unit':f'sold{i}','ledger':i+1,'price':8000,'at':at}]
    return rows


class AmpleStockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.old = Path.cwd(); os.chdir(self.temp.name)

    def tearDown(self):
        os.chdir(self.old); self.temp.cleanup()

    def run_batch(self, model, inv=None, refresh=None, rows=None):
        inv = inv or inventory()
        return portfolio.run(inv, catalog(), rows if rows is not None else history(), 1000,
                             model=reviewed_model(model), refresh=refresh or (lambda:(inv,1000,True)), rng=random.Random(1))

    def mixed(self, role, prompt, schema, target=None, new_count=6):
        self.assertEqual(prompt['replenishment_candidates'][0]['sku'], 's1')
        # These reservation fixtures choose the near-window option explicitly.
        # Multi-hour choices are exercised separately by test_growth_coverage.
        target = target if target is not None else max(o['target_stock'] for o in prompt['replenishment_candidates'][0]['target_options'] if o['scenario'] in ('low','base','high'))
        anchor = prompt['exploration_anchor_sku']
        skus = ([anchor] + [r['sku'] for r in prompt['candidates'] if r['sku'] != anchor])[:new_count]
        return {'action':'buy','skus':skus,'replenishment':[{'sku':'s1','target_stock':target}],
                'reason':'Six-unit seller buffer plus a diverse new basket'}

    def test_exploration_and_proven_replenishment_share_one_approval(self):
        result = self.run_batch(self.mixed)
        self.assertEqual(result['buy'][0], {'sku':'s1','qty':2})
        self.assertEqual(len(result['buy']), 7)
        self.assertTrue(all(r['qty']==1 for r in result['buy'][1:]))
        state = json.loads(portfolio.STATE.read_text())
        self.assertEqual(state['intents'][0]['kind'], 'replenishment')
        used = sum(i['cost_ceiling']*i['qty'] for i in state['intents'] if i['kind']=='exploration')
        self.assertEqual(used,67500)
        self.assertEqual(sum(i['cost_ceiling']*pending_quantity(i) for i in state['intents']),90000)

    def test_fresh_stock_growth_cannot_raise_the_approved_target(self):
        inv = inventory()
        current = inventory()
        current['units'] += [dict(current['units'][1], unit='arrived')]
        result = self.run_batch(lambda *args:self.mixed(*args,new_count=2), inv,
                                refresh=lambda:(current,1000,True))
        self.assertEqual(result['buy'][0], {'sku':'s1','qty':1})  # fresh 2 -> forecast-approved 3

    def test_unknown_sku_or_nonexistent_depth_choice_cannot_be_bought(self):
        for choice in ({'sku':'not-a-candidate','target_stock':6}, {'sku':'s1','target_stock':99}):
            portfolio.STATE.unlink(missing_ok=True)
            def model(*args):
                answer = self.mixed(*args)
                answer['replenishment'] = [choice]
                return answer
            self.assertEqual(self.run_batch(model), {'buy':[]})
            self.assertEqual(json.loads(portfolio.STATE.read_text())['intents'], [])

    def test_combined_budget_is_checked_not_just_each_half(self):
        inv = inventory(); inv['units'][0]['cost'] = 19_624_000  # room=70k including max surge
        self.assertEqual(self.run_batch(self.mixed, inv), {'buy':[]})
        self.assertEqual(json.loads(portfolio.STATE.read_text())['intents'], [])

    def test_invalid_approval_gets_one_numeric_correction_before_any_intent(self):
        inv = inventory(); inv['units'][0]['cost'] = 19_624_000
        calls = []
        def model(role,prompt,schema):
            calls.append(prompt)
            if len(calls)==1:
                return self.mixed(role,prompt,schema,new_count=6)
            self.assertIn('combined spend',prompt['correction']['tool_validation_error'])
            self.assertFalse(json.loads(portfolio.STATE.read_text())['intents'])
            return self.mixed(role,prompt,schema,new_count=4)
        result = self.run_batch(model,inv)
        self.assertEqual(len(calls),2)
        self.assertEqual(len(result['buy']),5)
        self.assertEqual(sum(r['qty'] for r in result['buy']),6)
        self.assertEqual(self.run_batch(model,inv),{'buy':[]})
        self.assertEqual(len(calls),2)

    def test_two_linked_positive_settlements_qualify_for_maintenance(self):
        self.assertFalse(sales_evidence('s1',history(1),1000)['proven'])
        self.assertTrue(sales_evidence('s1',history(2),1000)['proven'])
        self.assertTrue(sales_evidence('s1',history(3),1000)['proven'])
        unknown_cost = [r for r in history(3) if r['event']=='settled']
        self.assertFalse(sales_evidence('s1',unknown_cost,1000)['proven'])
        self.assertEqual(replenishment_candidates(catalog(),inventory(),history(1),1000), [])

    def test_partial_bulk_arrival_keeps_remaining_units_and_cash_reserved(self):
        self.run_batch(self.mixed)
        state = json.loads(portfolio.STATE.read_text())
        bulk = state['intents'][0]
        event = {'event':'settled','sku':'s1','unit':'new0','ledger':99,
                 'at':datetime.fromtimestamp(1010,timezone.utc).isoformat()}
        reconcile(state['intents'], {'units':[]}, [event], 1020)
        self.assertEqual(bulk['status'],'issued_unknown')
        self.assertEqual(pending_quantity(bulk),bulk['qty']-1)
        self.assertEqual(bulk['cost_ceiling']*pending_quantity(bulk),11250)
        portfolio.STATE.write_text(json.dumps(state))
        self.assertEqual(plans.pending_exploration_cost({'units':[]}),78750)
        rest = [dict(event,unit=f'new{i}',ledger=99+i) for i in range(1,bulk['qty'])]
        reconcile(state['intents'], {'units':[]}, rest, 1020)
        self.assertEqual(bulk['status'],'settled')
        self.assertEqual(pending_quantity(bulk),0)

    def test_later_same_sku_restock_does_not_attach_to_an_already_received_batch(self):
        rows = [{'sku':'s1','at':900,'qty':2,'status':'observed_inventory','prior_units':[],
                 'cost_ceiling':7500,'observed_units':['old0','old1']},
                {'sku':'s1','at':1000,'qty':2,'status':'issued_unknown','prior_units':[], 'cost_ceiling':7500}]
        event = {'event':'settled','sku':'s1','unit':'new0','ledger':99,
                 'at':datetime.fromtimestamp(1010,timezone.utc).isoformat()}
        reconcile(rows, {'units':[]}, [event], 1020)
        self.assertNotIn('new0',rows[0]['observed_units'])
        self.assertEqual(rows[1]['observed_units'],['new0'])
        self.assertEqual(pending_quantity(rows[1]),1)

    def test_cached_six_unit_plan_rechecks_proven_status(self):
        obs = {'observed_visits':3,'observed_no_stock_visits':0,'observed_visit_rate_while_stocked':None,'proven':True}
        p = plans.create(sku='s1',product=catalog()['s1'],candidate={'qty':5,'target_stock':6},
                         approval={'valid_for_seconds':600,'max_total_qty':5},observation=obs,now=1000)
        result, _ = plans.execute(p,inventory=inventory(),product=catalog()['s1'],observation=obs,now=1000)
        self.assertEqual(result,{'buy':[{'sku':'s1','qty':5}]})
        result, reason = plans.execute(p,inventory=inventory(),product=catalog()['s1'],observation={**obs,'proven':False},now=1001)
        self.assertIsNone(result)
        self.assertEqual(reason,'sales_evidence_changed')

    def test_exploitation_window_can_replenish_without_requiring_a_new_sku(self):
        inv = inventory(portfolio.MIN_VARIETY); inv['balance'] = 2_000_000
        for unit in inv['units']: unit['cost'] = 100000
        def model(role,prompt,schema):
            self.assertEqual(prompt['maximum_new_skus'],0)
            self.assertEqual(prompt['policy']['mode'],'exploit')
            target = max(o['target_stock'] for o in prompt['replenishment_candidates'][0]['target_options'])
            return {'action':'buy','skus':[],'replenishment':[{'sku':'s1','target_stock':target}],
                    'reason':'Replace the proven seller only'}
        result = portfolio.run(inv,catalog(),history(),1000,model=model,
                               refresh=lambda:(inv,1000,True),rng=random.Random(2))
        self.assertEqual(result['buy'],[{'sku':'s1','qty':2}])


if __name__ == '__main__':
    unittest.main()
