"""Regression scenarios from approvals lost to time, fresh cash and new arrivals."""
import json
from pathlib import Path
import unittest
from collections import Counter

import inventory_portfolio as portfolio
from market_memory import events
from decision_audit import journeys
from procurement_audit import select_lines
from replenishment_plan import MIN_BALANCE,product_signature
from supply_reconciliation import reconcile
import test_inventory_portfolio as fixtures
from test_inventory_portfolio import inventory


class ProcurementExecutionTests(unittest.TestCase):
    setUp=fixtures.PortfolioTests.setUp
    tearDown=fixtures.PortfolioTests.tearDown
    run_portfolio=fixtures.PortfolioTests.run_portfolio

    def execution(self):
        return [r for r in events() if r['event']=='portfolio_execution'][-1]

    def test_model_delay_cuts_five_of_eight_lines_and_keeps_approval_identity(self):
        inv=inventory()
        result=self.run_portfolio(inv,refresh=lambda:(inv,1150,True))
        self.assertEqual(len(result['buy']),3)
        row=self.execution()
        self.assertEqual((row['approved_units'],row['emitted_units'],row['cut_units']),(8,3,5))
        self.assertEqual(row['planning_elapsed_seconds'],150)
        self.assertEqual(row['execution_supply_lines'],3)
        self.assertTrue(all(r['reasons']==['supply_window_time'] for r in row['lines'][3:]))
        approval=next(r for r in events() if r['event']=='portfolio_approval')
        self.assertEqual(approval['decision_id'],row['decision_id'])
        saved=json.loads(self.path.read_text())
        self.assertEqual(len(saved['intents']),3)
        self.assertTrue(all(i['decision_id']==row['decision_id'] and i['intent_id'].startswith(row['decision_id'])
                            for i in saved['intents']))
        self.assertEqual(journeys(events())[-1]['status'],'emitted_pending')
        # A later confirmed arrival links to the original approval, not this hook.
        sku=result['buy'][0]['sku']
        later=inventory();later['restock']['open']=False
        later['units'].append({'unit':'new-arrival','sku':sku,'grade':'A','cost':5000,'supplied_at':1151})
        self.run_portfolio(later,now=1300,refresh=lambda:(later,1300,True))
        change=next(r for r in events() if r['event']=='portfolio_intent_reconciled')
        self.assertEqual(change['decision_id'],row['decision_id'])
        self.assertEqual(change['episode_id'],row['episode_id'])
        self.assertEqual(journeys(events())[-1]['status'],'arrival_observed')

    def test_fresh_cash_drop_is_distinguished_from_lost_window_time(self):
        changed=inventory();changed['balance']=MIN_BALANCE+15000
        result=self.run_portfolio(refresh=lambda:(changed,1000,True))
        self.assertEqual(len(result['buy']),1)
        row=self.execution()
        self.assertEqual(row['cut_units'],7)
        self.assertEqual(row['cut_reason_counts'],{'shared_budget':7})
        self.assertEqual(row['lines'][1]['checks']['budget_before'],3750)
        self.assertEqual(row['lines'][1]['checks']['cost_ceiling_per_unit'],11250)
        self.assertTrue(all(r['emitted_qty']==0 for r in row['lines'][1:]))

    def test_closed_or_stale_window_records_every_approved_unit_without_new_reservations(self):
        for fresh,at,reason in [(False,1000,'inventory_stale'),(True,1201,'supply_window_closed')]:
            with self.subTest(reason=reason):
                self.path.unlink(missing_ok=True)
                inv=inventory()
                self.assertEqual(self.run_portfolio(inv,refresh=lambda:(inv,at,fresh)),{'buy':[]})
                row=self.execution()
                self.assertEqual(row['approved_units'],8);self.assertEqual(row['emitted_units'],0)
                self.assertEqual(row['cut_reason_counts'],{reason:8})
                self.assertEqual(json.loads(self.path.read_text())['intents'],[])

    def test_new_arrivals_reduce_target_and_category_room_without_changing_priority(self):
        catalog={'b':{'kind':'goods','list_price':1000}}
        desired=[{'sku':'a','kind':'replenishment','qty':4,'target_stock':4,'category':'one','cost_ceiling':100},
                 {'sku':'b','kind':'exploration','qty':1,'category':'one','cost_ceiling':100,
                  'signature':product_signature(catalog['b'])}]
        # Three units arrived during model deliberation; category has one slot.
        buy,rows=select_lines(desired,stocks={'a':3},categories={'one':23},pending_stock={},catalog=catalog,
                   unit_room=10,supply_lines=5,budget=1000,allow_replenishment=True,
                   allow_new_skus=True,time_limited=False)
        self.assertEqual(buy,[{'sku':'a','qty':1}])
        self.assertEqual(rows[0]['cut_qty'],3)
        self.assertIn('target_already_satisfied',rows[0]['reasons'])
        self.assertIn('category_capacity',rows[0]['reasons'])
        self.assertEqual(rows[1]['reasons'],['category_capacity'])
        self.assertEqual(sum(r['approved_qty'] for r in rows),sum(r['emitted_qty']+r['cut_qty'] for r in rows))

    def test_published_window_can_be_open_but_inside_fifteen_second_safety_margin(self):
        inv=inventory()
        self.assertEqual(self.run_portfolio(inv,refresh=lambda:(inv,1190,True)),{'buy':[]})
        row=self.execution()
        self.assertEqual(row['cut_reason_counts'],{'supply_window_closing_buffer':8})
        self.assertTrue(row['observed_supply_window']['open'])
        self.assertEqual(row['seconds_until_window_close'],10)
        self.assertEqual(row['closing_safety_seconds'],15)
        self.assertEqual(json.loads(self.path.read_text())['intents'],[])

    def test_partial_delivery_is_recorded_once_before_full_quantity_arrives(self):
        intent={'sku':'a','at':1000,'qty':2,'prior_units':[],'status':'issued_unknown','window':'990:1200',
                'episode_id':'procurement:original','decision_id':'d1','intent_id':'d1:a'}
        inv={'units':[{'sku':'a','unit':'new','supplied_at':1001}]}
        changes=reconcile([intent],inv,[],1100)
        self.assertEqual(len(changes),1);self.assertEqual(changes[0]['status'],'issued_unknown')
        self.assertEqual(changes[0]['decision_id'],'d1')
        self.assertEqual(changes[0]['outcome_link_status'],'exact_intent_unit')
        self.assertEqual(reconcile([intent],inv,[],1101),[])

    def test_only_tightest_quantity_limit_is_reported_as_binding(self):
        desired=[{'sku':'a','kind':'replenishment','qty':10,'target_stock':5,'category':'one','cost_ceiling':100}]
        buy,rows=select_lines(desired,stocks={},categories={'one':17},pending_stock={},catalog={},
            unit_room=8,supply_lines=5,budget=10000,allow_replenishment=True,allow_new_skus=True,time_limited=False)
        self.assertEqual(buy,[{'sku':'a','qty':5}])
        self.assertEqual(rows[0]['reasons'],['target_already_satisfied'])


if __name__=='__main__':unittest.main()
