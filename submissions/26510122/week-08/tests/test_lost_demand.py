from test_inventory_portfolio import reviewed_model
"""Demand includes eligible lost requests without inventing paid sales."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import random

import inventory_observation as observation
import procurement_scheduler as scheduler
from market_memory import events
from stock_policy import demand_forecast
import inventory_portfolio as portfolio
import test_inventory_portfolio as fixtures
from test_inventory_portfolio import catalog, inventory


def stockout(task,at=9500,skus=None):
    return {'event':'no_stock','at':at,'task_id':task,'requested_skus':['s1'] if skus is None else skus}


def opened(task,at=8000,sku='s1'):
    return {'event':'opening','at':at,'task_id':task,'sku':sku}


def settled(task,ledger=1,at=8100,sku='s1'):
    return {'event':'settled','at':at,'task_id':task,'sku':sku,'ledger':ledger,'unit':f'u{ledger}','price':8000}


class LostDemandTests(unittest.TestCase):
    def test_repeated_stockout_messages_are_one_request(self):
        rows=[stockout('a'),stockout('a',at=9501),
              {'event':'request_seen','at':9499,'task_id':'a','requested_skus':['s1']}]
        view=observation.evidence('s1',rows,10000,1)
        self.assertEqual(view['observed_no_stock_visits'],1)
        self.assertEqual(view['eligible_lost_stockout_visits'],1)

    def test_category_and_multi_sku_requests_cannot_inflate_each_product(self):
        rows=[stockout('category',skus=[]),stockout('several',skus=['s1','s2'])]
        for sku in ('s1','s2'):
            view=observation.evidence(sku,rows,10000,1)
            self.assertEqual(view['eligible_lost_stockout_visits'],0)
            self.assertEqual(view['unattributed_no_stock_visits'],1)
            self.assertEqual(view['ambiguous_stockout_visits'],1)

    def test_later_settlement_or_service_excludes_the_same_stockout_request(self):
        for outcome in (settled('a',at=9600),opened('a',at=9600)):
            view=observation.evidence('s1',[stockout('a'),outcome],10000,1)
            self.assertEqual(view['eligible_lost_stockout_visits'],0)
            self.assertEqual(view['already_served_stockout_visits'],1)

    def test_legacy_settlement_joins_only_a_unique_signed_receipt(self):
        receipt={'event':'receipt_signed','at':9550,'task_id':'a','sku':'s1','unit':'u1','price':8000}
        paid=settled(None,at=9600)
        view=observation.evidence('s1',[stockout('a'),receipt,paid],10000,1)
        self.assertEqual(view['eligible_lost_stockout_visits'],0)
        ambiguous=observation.demand_outcomes([opened('a'),opened('b'),receipt,
            {**receipt,'task_id':'b'},paid],10000)
        self.assertFalse(ambiguous['settled_tasks'].get('s1'))

    def test_conversion_uses_mature_completed_served_attempts_not_pending_or_receipts(self):
        rows=[opened('paid'),settled('paid'),settled('paid'),
              opened('declined'),{'event':'cancelled','at':8200,'task_id':'declined'},
              opened('pending'),opened('expired'),{'event':'negotiation_expired','at':8200,'task_id':'expired'},
              opened('receipt'),{'event':'receipt_signed','at':8200,'task_id':'receipt','sku':'s1','unit':'r','price':8000},
              opened('young',at=9900),settled('young',ledger=2,at=9950)]
        estimate=observation.conversion_estimate('s1',observation.demand_outcomes(rows,10000))
        self.assertEqual(estimate['sku_outcomes'],{'settled':1,'not_settled':1,'pending_or_immature':4})
        self.assertEqual(estimate['probability'],.5)

    def test_sparse_conversion_has_an_explicit_prior_and_shrinks_to_shop_evidence(self):
        empty=observation.conversion_estimate('s1',observation.demand_outcomes([],10000))
        self.assertEqual(empty['probability'],.5)
        rows=[opened('other',sku='s2'),settled('other',sku='s2')]
        estimate=observation.conversion_estimate('s1',observation.demand_outcomes(rows,10000))
        self.assertEqual(estimate['probability'],.6)
        self.assertEqual(estimate['sku_outcomes']['settled'],0)

    def test_sales_plus_weighted_losses_raise_forecast_without_changing_confirmed_sales(self):
        rows=[settled('one',at=9500),settled('two',ledger=2,at=9501)]
        base=demand_forecast('s1',catalog()['s1'],inventory(0),rows,10000)
        adjusted=demand_forecast('s1',catalog()['s1'],inventory(0),rows+
            [stockout(f'lost{i}') for i in range(4)],10000)
        first=adjusted['windows']['1']
        self.assertEqual(first['settlements'],2)
        self.assertEqual(first['expected_lost_sales'],2.)
        self.assertEqual(first['rate_per_hour'],4.)
        self.assertGreater(adjusted['sales_rate_per_hour'],base['sales_rate_per_hour'])
        self.assertGreater(adjusted['maximum_approval_target'],base['maximum_approval_target'])

    def test_exposure_and_lost_demand_are_not_added_twice(self):
        now=4000
        rows=[{'event':'inventory_snapshot','round':3,'source_time':t,
               'stock':{'s1':{'qty':1 if t<1600 else 0}}} for t in range(400,4001,60)]
        rows += [settled('a',at=600),settled('b',ledger=2,at=900),stockout('miss',at=3500)]
        view=demand_forecast('s1',catalog()['s1'],inventory(0),rows,now)['windows']['1']
        self.assertGreater(view['exposure_adjusted_sales_rate'],2.5)
        self.assertAlmostEqual(view['rate_per_hour'],view['exposure_adjusted_sales_rate'],places=4)
        self.assertNotAlmostEqual(view['rate_per_hour'],view['exposure_adjusted_sales_rate']+.5,places=4)

    def test_future_events_cannot_change_conversion_or_lost_demand(self):
        rows=[stockout('a'),opened('a',at=11000),settled('a',at=11001)]
        self.assertEqual(observation.evidence('s1',rows,10000,1)['eligible_lost_stockout_visits'],1)

    def test_recent_inferred_losses_cannot_erase_an_older_paid_sales_signal(self):
        rows=[settled(f'paid{i}',ledger=i,at=5000) for i in range(30)]
        baseline=demand_forecast('s1',catalog()['s1'],inventory(0),rows,10000)
        adjusted=demand_forecast('s1',catalog()['s1'],inventory(0),rows+
            [stockout(f'lost{i}') for i in range(4)],10000)
        self.assertGreaterEqual(adjusted['sales_rate_per_hour'],baseline['sales_rate_per_hour'])


class LostDemandApprovalTests(unittest.TestCase):
    setUp=fixtures.PortfolioTests.setUp
    tearDown=fixtures.PortfolioTests.tearDown

    def test_adjusted_demand_reaches_model_approval_audit_and_cached_execution(self):
        inv=inventory(0);inv['restock']={'open':False,'opens_at':10060,'closes_at':10240}
        rows=[settled('paid1',at=9500),settled('paid2',ledger=2,at=9501)]
        rows += [{'event':'receipt_signed','at':9400,'task_id':r['task_id'],'sku':'s1',
                  'unit':r['unit'],'price':r['price'],'cost':4000} for r in list(rows)]
        rows += [stockout(f'lost{i}') for i in range(4)]
        calls=[]
        def model(role,prompt,schema):
            calls.append(prompt);winner=prompt['replenishment_candidates'][0]
            view=winner['forecast']['windows']['1']
            self.assertEqual(view['settlements'],2)
            self.assertEqual(view['expected_lost_sales'],2)
            anchor=prompt['exploration_anchor_sku']
            new=[anchor]+[r['sku'] for r in prompt['candidates'] if r['sku']!=anchor][:1]
            return {'action':'buy','skus':new,'replenishment':[{'sku':'s1',
                'target_stock':winner['target_options'][-1]['target_stock']}],'reason':'Model accepts calculated demand with explicit uncertainty'}
        portfolio.run(inv,catalog(),rows,10000,model=reviewed_model(model),rng=random.Random(1),
            refresh=lambda:(inv,10000,True),state_path=self.path,prepare_only=True)
        ready=portfolio.read_ready();basis=ready['desired'][0]['demand_basis']
        self.assertEqual(basis['windows']['1']['expected_lost_sales'],2)
        self.assertEqual(basis['conversion_probability'],.5)
        approval=next(e for e in events() if e['event']=='portfolio_approval')
        self.assertEqual(approval['approved_lines'][0]['demand_basis'],basis)
        current={**inv,'restock':{**inv['restock'],'open':True}}
        result=portfolio.execute_prepared(current,catalog(),rows,10060,
            refresh=lambda:(current,10060,True),state_path=self.path)
        self.assertEqual(result['buy'][0]['qty'],ready['desired'][0]['qty'])
        self.assertEqual(len(calls),1)
        self.assertEqual(portfolio.execute_prepared(current,catalog(),rows,10060,
            refresh=lambda:(current,10060,True),state_path=self.path),{'buy':[]})


class SchedulerObservationTests(unittest.TestCase):
    def test_fresh_periodic_samples_restore_exposure_without_inventing_the_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            previous=Path.cwd();os.chdir(directory)
            try:
                root=Path(directory);market=root/'.market';market.mkdir()
                inv=inventory();path=market/'inventory.json'
                path.write_text(json.dumps(inv));(market/'catalog.json').write_text(json.dumps(catalog()))
                for at in (1000,1060,1120):
                    os.utime(path,(at,at));scheduler.snapshot(root,now=at,observe=True)
                scheduler.snapshot(root,now=1120,observe=True)  # same source is not a new observation
                history=events();self.assertEqual(len(history),3)
                view=observation.evidence('s1',history,1120,1)
                self.assertAlmostEqual(view['in_stock_exposure_hours'],120/3600,places=4)
                self.assertLess(view['snapshot_coverage_hours'],.04)  # not an invented whole hour
                with patch.object(scheduler,'maintain'),patch.object(scheduler,'record_inventory_snapshot') as record:
                    _,_,_,fresh=scheduler.snapshot(root,now=1160,observe=True)
                    self.assertFalse(fresh);record.assert_not_called()
            finally:os.chdir(previous)

    def test_inventory_observation_uses_mtime_of_the_opened_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);market=root/'.market';market.mkdir()
            path=market/'inventory.json';path.write_text(json.dumps(inventory()))
            (market/'catalog.json').write_text('{}');os.utime(path,(1000,1000))
            with patch.object(scheduler,'record_inventory_snapshot') as record:
                scheduler.snapshot(root,now=1001,observe=True)
                self.assertEqual(record.call_args.kwargs['source_time'],1000)


if __name__=='__main__':unittest.main()
