from test_inventory_portfolio import reviewed_model
"""Pre-open model decisions; fresh model-free, once-only kit execution."""
import json
import random
from pathlib import Path
import unittest
from unittest.mock import Mock,patch

import inventory_portfolio as portfolio
import procurement_scheduler as scheduler
from market_memory import events
from replenishment_plan import MIN_BALANCE
import test_inventory_portfolio as fixtures
from test_ample_stock import history


class PreparedProcurementTests(unittest.TestCase):
    setUp=fixtures.PortfolioTests.setUp
    tearDown=fixtures.PortfolioTests.tearDown

    def prepare(self,model=fixtures.approve,inv=None,now=800):
        inv=inv or fixtures.inventory(0);inv['restock']['open']=False
        portfolio.run(inv,fixtures.catalog(),[],now,model=model,refresh=lambda:(inv,now,True),
            rng=random.Random(1),state_path=self.path,prepare_only=True)
        return inv,portfolio.read_ready()

    def execute(self,inv=None,now=900):
        inv=inv or fixtures.inventory(0)
        return portfolio.execute_prepared(inv,fixtures.catalog(),[],now,
            refresh=lambda:(inv,now,True),state_path=self.path)

    def test_closed_window_approval_reserves_nothing_and_emits_without_another_model(self):
        model=Mock(side_effect=fixtures.approve)
        _,ready=self.prepare(model)
        self.assertEqual(model.call_count,1);self.assertEqual(ready['status'],'ready')
        self.assertFalse(self.path.exists())  # no budget or attempted-window reservation
        self.assertFalse(any(r['event']=='portfolio_execution' for r in events()))
        result=self.execute()
        self.assertEqual(len(result['buy']),8);self.assertEqual(model.call_count,1)
        self.assertEqual(portfolio.read_ready()['status'],'claimed')
        intent=json.loads(self.path.read_text())['intents'][0]
        self.assertEqual(intent['parent_decision_id'],ready['audit']['decision_id'])
        self.assertEqual(self.execute(),{'buy':[]})
        self.assertEqual(len(json.loads(self.path.read_text())['intents']),8)

    def test_never_executes_before_open_or_calls_model_while_window_is_open(self):
        inv,_=self.prepare()
        self.assertIsNone(self.execute(inv,now=850))
        forbidden=Mock(side_effect=AssertionError('no model during supply window'))
        opened=fixtures.inventory(0)
        self.assertEqual(portfolio.run(opened,fixtures.catalog(),[],900,model=forbidden,
            refresh=lambda:(opened,900,True),state_path=self.path,prepare_only=True),{'buy':[]})
        forbidden.assert_not_called()
        self.assertEqual(len(self.execute()['buy']),8)

    def test_fresh_cash_is_rechecked_without_spending_the_old_budget(self):
        self.prepare();current=fixtures.inventory(0);current['balance']=MIN_BALANCE+15000
        result=self.execute(current)
        self.assertEqual(len(result['buy']),1)
        row=[r for r in events() if r['event']=='portfolio_execution'][-1]
        self.assertEqual(row['cut_reason_counts'],{'shared_budget':7})

    def test_expired_changed_catalog_and_changed_policy_never_emit(self):
        for case in ('expiry','catalog','policy'):
            with self.subTest(case=case):
                self.path.unlink(missing_ok=True);portfolio.READY.unlink(missing_ok=True)
                self.prepare();inv=fixtures.inventory(0);products=fixtures.catalog()
                if case=='expiry':
                    ready=portfolio.read_ready();ready['prepared_at']=0
                    portfolio.save(portfolio.READY,ready)
                if case=='catalog':
                    sku=portfolio.read_ready()['desired'][0]['sku'];products[sku]['list_price']*=2
                policy=portfolio.operating_policy(900)
                if case=='policy':policy={**policy,'meta_version':'changed'}
                with patch.object(portfolio,'operating_policy',return_value=policy):
                    result=portfolio.execute_prepared(inv,products,[],900,
                        refresh=lambda:(inv,900,True),state_path=self.path)
                self.assertEqual(result,{'buy':[]})
                self.assertEqual(portfolio.read_ready()['status'],'invalidated')
                self.assertFalse(self.path.exists())

    def test_future_inventory_arrival_reduces_replenishment_target(self):
        inv=fixtures.inventory(0);inv['restock']['open']=False
        rows=[{**r,'at':790} for r in history(2)]
        def choose(role,prompt,schema):
            r=prompt['replenishment_candidates'][0]
            anchor=prompt['exploration_anchor_sku']
            new=[anchor]+[c['sku'] for c in prompt['candidates'] if c['sku']!=anchor][:prompt['minimum_new_skus_if_buy']-1]
            return {'action':'buy','skus':new,'replenishment':[{'sku':r['sku'],
                'target_stock':max(o['target_stock'] for o in r['target_options'])}],'reason':'forecast target'}
        portfolio.run(inv,fixtures.catalog(),rows,800,model=reviewed_model(choose),refresh=lambda:(inv,800,True),
            rng=random.Random(1),state_path=self.path,prepare_only=True)
        ready=portfolio.read_ready();row=ready['desired'][0]
        target=row['target_stock'];self.assertGreater(target,1)
        inv=fixtures.inventory(0)
        inv['units']=[{'unit':f'new{i}','sku':row['sku'],'grade':'A','category':row['category'],'cost':1000} for i in range(target-1)]
        result=self.execute(inv)
        self.assertEqual(result['buy'][0],{'sku':row['sku'],'qty':1})
        self.assertEqual(len(result['buy']),3)  # replenishment plus required recovery exploration
        self.assertIn('decision_id',result['_procurement'])
        executed=[r for r in events() if r['event']=='portfolio_execution'][-1]
        self.assertEqual(executed['cut_units'],target-1)
        self.assertEqual(executed['lines'][0]['reasons'],['target_already_satisfied'])

    def test_ready_approval_is_reused_without_redrawing_or_a_second_model_call(self):
        model=Mock(side_effect=fixtures.approve)
        self.prepare(model);first=portfolio.read_ready()
        self.prepare(model,now=810)
        self.assertEqual(model.call_count,1)
        self.assertEqual(portfolio.read_ready(),first)

    def test_failed_preparation_keeps_its_draw_and_retry_state_without_reservations(self):
        failed=Mock(side_effect=RuntimeError('synthetic timeout'))
        self.prepare(failed);held=portfolio.read_ready()
        self.assertEqual(held['status'],'held');self.assertEqual(held['held_reason'],'model_unavailable')
        self.prepare(failed,now=810)
        self.assertEqual(failed.call_count,1)
        self.assertEqual(portfolio.read_ready()['policy']['draw'],held['policy']['draw'])
        self.assertFalse(self.path.exists())

    def test_stale_snapshot_waits_without_claiming_and_fresh_snapshot_executes(self):
        self.prepare();inv=fixtures.inventory(0);fresh=[False]
        with patch.object(scheduler,'snapshot',side_effect=lambda *a:(inv,fixtures.catalog(),900,fresh[0])):
            self.assertIsNone(scheduler.execute_once(Path.cwd(),now=900))
            self.assertEqual(portfolio.read_ready()['status'],'ready')
            fresh[0]=True
            self.assertEqual(len(scheduler.execute_once(Path.cwd(),now=900)['buy']),8)

    def test_cached_approval_cannot_be_silently_expanded(self):
        self.prepare();ready=portfolio.read_ready();ready['desired'][0]['qty']=20
        portfolio.save(portfolio.READY,ready)
        self.assertEqual(self.execute(),{'buy':[]})
        self.assertEqual(portfolio.read_ready()['invalid_reason'],'approval_content_changed')
        self.assertFalse(self.path.exists())

    def test_preparation_never_waits_for_execution_portfolio_lock(self):
        import fcntl
        self.path.parent.mkdir(exist_ok=True)
        with self.path.with_suffix('.lock').open('a') as locked:
            fcntl.flock(locked,fcntl.LOCK_EX|fcntl.LOCK_NB)
            _,ready=self.prepare()
            self.assertEqual(ready['status'],'ready')

    def test_watch_detects_opening_without_waiting_for_kit_next_minute(self):
        elapsed=[0];calls=[]
        def execute(runtime):
            calls.append(elapsed[0]);return {'buy':[{'sku':'one','qty':1}]} if elapsed[0]>=4 else None
        result=scheduler.watch(Path.cwd(),timeout=210,clock=lambda:elapsed[0],
            sleep=lambda t:elapsed.__setitem__(0,elapsed[0]+t),execute=execute)
        self.assertEqual(result,{'buy':[{'sku':'one','qty':1}]})
        self.assertEqual(calls,[0,2,4])

    def test_frozen_seller_hook_imports_without_workspace_modules(self):
        import subprocess,sys
        from ops import seller_service
        keys=Path('keys');keys.mkdir()
        for name in ('shop_key.pem','buyer_key.pem'):(keys/name).write_text('synthetic fixture only')
        runtime=Path.cwd()/'frozen'
        seller_service.prepare(runtime,keys)
        config=json.loads((runtime/'agent.json').read_text())
        self.assertIn('procurement_scheduler.py',config['restock']['cmd'])
        self.assertIn('--execute',config['restock']['cmd'])
        completed=subprocess.run([sys.executable,'-c','import procurement_scheduler'],cwd=runtime/'release',
                                  capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)


if __name__=='__main__':unittest.main()
