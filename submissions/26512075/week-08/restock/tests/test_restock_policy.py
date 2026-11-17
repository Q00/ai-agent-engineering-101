import copy
import hashlib
import json

import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from compare_restock import baseline, compare
from connect_restock import COMMAND, connect
from restock_policy import Config, InputError, plan, sale_stats
from restock_runtime import SalesStore, SupplyGuard, atomic_json
from run_market_restock import observe_completion

ROOT = Path(__file__).resolve().parent.parent


class PolicyTests(unittest.TestCase):
    def setUp(self):
        f = json.loads((ROOT / "../fixtures/01_sales_concentrated_in_fast_SKU.json").read_text())
        self.f = f
        self.now, self.inv, self.cat, self.dem, self.hist = (f[k] for k in ("now", "inventory", "catalog", "demand", "history"))
        self.cfg = Config(**f["config"])
        # Profitable stressed-price scenario; original replay fixtures remain unchanged.
        for event in self.hist["events"]:
            event["price"] = 160

    def result(self, **kw):
        return plan(self.inv, self.cat, self.dem, self.hist, now=self.now, cfg=kw.pop("cfg", self.cfg), **kw)

    def unit(self, i, **kw):
        return dict(unit=str(i), sku="fast", kind="goods", category="goods", grade="A", cost=65,
                    expires_at=None, supplied_at=self.now-100, **kw)

    def test_recent_sales_only_blocks_unproven_skus_but_keeps_seller(self):
        from dataclasses import replace
        cfg = replace(self.cfg, require_recent_sales=True)
        result = self.result(cfg=cfg)
        self.assertEqual(result['buy'], [{'sku': 'fast', 'qty': 4}])
        self.assertTrue(any(row['reason'] == 'no_recent_settled_sales' for row in result['skipped']))
        self.hist = None
        self.inv['surge'] = [{'sku': sku, 'factor': 1} for sku in self.cat]
        result = self.result(cfg=cfg)
        self.assertEqual(result['buy'], [])
        self.assertFalse(result['exploration'])

    def test_recent_sales_flag_requires_boolean(self):
        with self.assertRaises(InputError):
            Config(require_recent_sales='true')

    def test_sales_velocity_selects_exact_sku(self):
        r = self.result()
        self.assertEqual(r["buy"], [{"sku": "fast", "qty": 4}])
        self.assertEqual(r["evidence"]["eligible_sale_units"], 10)
        self.assertLessEqual(r["cash_commitment_upper"], r["budget"])

    def test_restart_retains_recent_sales_without_inflating_rate(self):
        history = {"market_run": "run", "observed_since": self.now - 60,
                   "as_of": self.now, "events": [
            {"id": "old", "market_run": "run", "role": "seller", "round": 3,
             "settled": True, "sku": "sold", "price": 160, "ts": self.now - 7200},
            {"id": "expired", "market_run": "run", "role": "seller", "round": 3,
             "settled": True, "sku": "stale", "price": 160, "ts": self.now - 25200},
            {"id": "other", "market_run": "other", "role": "seller", "round": 3,
             "settled": True, "sku": "other", "price": 160, "ts": self.now - 60}]}
        rates, prices, _, count = sale_stats(history, self.now, Config())
        self.assertEqual(count, 1)
        self.assertEqual(rates, {"sold": 0.5})
        self.assertEqual(prices, {"sold": [160]})

    def test_closed_and_boundary_windows(self):
        for changes, reason in [({"open": False}, "supply_closed"),
                                ({"closes_at": self.now}, "supply_closing"),
                                ({"opens_at": self.now+1}, "supply_closed")]:
            with self.subTest(changes=changes):
                old = copy.deepcopy(self.inv["restock"])
                self.inv["restock"].update(changes)
                r = self.result()
                self.assertEqual((r["buy"], r["reason"]), ([], reason))
                self.inv["restock"] = old

    def test_missing_window_or_wrong_round_fail_closed(self):
        del self.inv["restock"]
        with self.assertRaises(InputError): self.result()
        self.inv["round"] = 2
        with self.assertRaises(InputError): self.result()

    def test_cold_start_without_surge_evidence_holds(self):
        self.hist = None
        r = self.result()
        self.assertEqual(r["buy"], [])  # missing fresh surge evidence must not authorize exploration
        self.assertEqual(r["evidence"]["sales"], "no_eligible_sales")
        self.assertNotIn("food", [b["sku"] for b in r["buy"]])

    def test_live_stock_prevents_repeated_exploration(self):
        self.hist = None
        self.inv["units"] = [self.unit(1)]
        self.assertEqual(self.result()["buy"], [])

    def test_food_expiry_and_restock_ttl_guard(self):
        self.cat["fast"].update(kind="food")
        self.inv["units"] = [{**self.unit(1), "kind":"food", "expires_at":self.now-1}]
        r = self.result()
        self.assertEqual(r["expired_units"], 1)
        self.assertEqual(r["buy"], [])
        cfg = Config(**{**self.f["config"], "food_life_hours_by_sku":{"fast":4}})
        r = self.result(cfg=cfg)
        self.assertEqual(r["buy"], [{"sku":"fast", "qty":4}])
        cfg = Config(**{**self.f["config"], "food_life_hours_by_sku":{"fast":3.1}})
        self.assertEqual(self.result(cfg=cfg)["buy"], [])

    def test_near_expiry_stock_not_counted_as_missing_inventory(self):
        self.hist = None
        self.inv["units"] = [{**self.unit(1), "sku":"food", "kind":"food", "category":"food",
                              "expires_at":self.now+30}]
        self.assertEqual(self.result()["expired_units"], 0)

    def test_overstock_is_reported_and_not_reordered(self):
        self.inv["units"] = [self.unit(i) for i in range(6)]
        r = self.result()
        self.assertEqual(r["buy"], [])
        self.assertEqual(r["reason"], "hold_overstock")
        self.assertTrue(r["overstock"])

    def test_20_units_cap_includes_other_categories(self):
        self.inv["units"] = [{**self.unit(i), "sku":"unrelated"} for i in range(20)]
        self.assertEqual(self.result()["buy"], [])

    def test_insufficient_cash_and_window_budget(self):
        self.inv["balance"] = 499
        self.assertEqual(self.result()["buy"], [])
        self.inv["balance"] = 10000
        self.assertEqual(self.result(window_remaining=74)["buy"], [])
        r = self.result(window_remaining=262)
        self.assertEqual(r["buy"], [{"sku":"fast", "qty":2}])

    def test_holding_cost_can_veto_buy(self):
        self.cat["fast"]["list_price"] = 120
        self.hist["events"] = self.hist["events"][:1]
        self.hist["events"][0]["price"] = 100
        # Sale 100 - cost envelope 90 - refund buffer 10 - storage > 0 => veto.
        self.assertEqual(self.result()["buy"], [])

    def test_round2_buyer_and_unsettled_rows_are_not_sales(self):
        for event in self.hist["events"]:
            event["role"] = "buyer"
        self.assertEqual(self.result()["evidence"]["eligible_sale_units"], 0)
        for event in self.hist["events"]:
            event.update(role="seller", round=2)
        self.assertEqual(self.result()["evidence"]["eligible_sale_units"], 0)
        for event in self.hist["events"]:
            event.update(round=3, settled=False)
        self.assertEqual(self.result()["evidence"]["eligible_sale_units"], 0)

    def test_duplicates_conflicts_and_future_history(self):
        self.hist["events"].append(copy.deepcopy(self.hist["events"][0]))
        self.assertEqual(self.result()["evidence"]["eligible_sale_units"], 10)
        self.hist["events"][-1]["price"] += 1
        with self.assertRaises(InputError): self.result()
        self.hist["events"].pop()
        self.hist["as_of"] += 10
        with self.assertRaises(InputError): self.result()

    def test_refunds_make_previously_good_sku_unattractive(self):
        for event in self.hist["events"]:
            event["refund"] = event["price"]
        self.assertEqual(self.result()["buy"], [])

    def test_stale_history_not_treated_as_zero_sales(self):
        self.hist["as_of"] -= 61
        with self.assertRaises(InputError): self.result()

    def test_nan_bool_negative_and_bad_config(self):
        for value in [True, -1, float("nan"), float("inf")]:
            self.inv["balance"] = value
            with self.assertRaises(InputError): self.result()
        for kw in [{"max_lines":11}, {"spend_fraction":1.1}, {"max_units":True}]:
            with self.assertRaises(InputError): Config(**kw)

    def test_inputs_not_mutated_and_result_is_deterministic(self):
        before = copy.deepcopy([self.inv,self.cat,self.dem,self.hist])
        a, b = self.result(), self.result()
        self.assertEqual(a,b)
        self.assertEqual(before,[self.inv,self.cat,self.dem,self.hist])

    def test_same_inputs_seed_and_hypothetical_reversal(self):
        original = copy.deepcopy(self.f)
        self.assertEqual(compare(self.f), compare(self.f))
        self.assertEqual(self.f,original)
        reversal = json.loads((ROOT/'../fixtures/02_demand_reversal_can_favor_random_baseline.json').read_text())
        self.assertTrue(any(r['cash_delta_difference'] < 0 for r in compare(reversal)['runs']))

    def test_baseline_matches_fixed_seed_and_stock_limit(self):
        self.assertEqual(baseline(self.inv,self.cat,self.dem,7), baseline(self.inv,self.cat,self.dem,7))
        self.inv["units"]=[self.unit(i) for i in range(20)]
        self.assertEqual(baseline(self.inv,self.cat,self.dem,7),[])

    def test_cli_contract_stale_files_and_protected_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            for name,data in [("inventory",self.inv),("catalog",self.cat),("demand",self.dem),("restock_sales",self.hist)]:
                atomic_json(d/(name+".json"),data)
            os.utime(d/'inventory.json',(self.now,self.now))
            protected={"shop_key.pem":b"PRIVATE-SENTINEL", "buyer_key.pem":b"PRIVATE-2", "buyer_memory.json":b"[]", "seller.sqlite":b"DATABASE"}
            for name,data in protected.items(): (d/name).write_bytes(data)
            atomic_json(d/'config.json',self.f['config'])
            snap={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in d.iterdir()}
            cmd=[sys.executable,str(ROOT/'restock_policy.py'),'--state',str(d),'--config',str(d/'config.json'),'--now',str(self.now),'--explain']
            r=subprocess.run(cmd,capture_output=True,text=True,check=True)
            self.assertEqual(len(r.stdout.splitlines()),1)
            self.assertEqual(list(json.loads(r.stdout)),['buy'])
            self.assertTrue(json.loads(r.stdout)['buy'])
            self.assertEqual(snap,{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in d.iterdir()})
            os.utime(d/'inventory.json',(self.now-61,self.now-61))
            r=subprocess.run(cmd,capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(r.stdout),{'buy':[]})
            self.assertEqual(json.loads(r.stderr)['reason'],'invalid_input')


class Response:
    def __init__(self,status,body): self.status_code,self.body=status,body
    def json(self):
        if isinstance(self.body,Exception): raise self.body
        return copy.deepcopy(self.body)


class FakeMarket:
    def __init__(self,inv,replies=None):
        self.inv,self.replies,self.posts,self.gets=copy.deepcopy(inv),list(replies or []),[],0
        self.before_get=None
    def call(self,method,path,role,**kwargs):
        if method=='GET':
            self.gets+=1
            if self.before_get: self.before_get(self)
            return Response(200,self.inv)
        self.posts.append(kwargs['json'])
        value=self.replies.pop(0) if self.replies else Response(200,{'total':65})
        if isinstance(value,Exception): raise value
        return value


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.d=Path(self.tmp.name)
        self.t=1000.0
        self.cfg=Config(reserve_floor=0)
        self.inv={'round':3,'balance':1000,'units':[], 'restock':{'open':True,'opens_at':990,'closes_at':1180}}
        self.cat={'x':{'list_price':100}}
        self.g=SupplyGuard(self.d,'run',self.cfg,clock=lambda:self.t)
        self.proposal=lambda inv,remaining: {'buy':[{'sku':'x','qty':1}]} if remaining>=113 else {'buy':[]}
    def tearDown(self): self.tmp.cleanup()
    def step(self,m,proposal=None):
        return self.g.step(m,proposal or self.proposal,self.cat,lambda inv:200)

    def test_429_respects_both_signals_and_survives_restart(self):
        m=FakeMarket(self.inv,[Response(429,{'wait':70,'opens_at':1110})])
        self.assertEqual(self.step(m)['reason'],'supply_refused_429')
        self.assertEqual(self.g.read()['remaining'],200)
        self.t=1109
        self.g=SupplyGuard(self.d,'run',self.cfg,clock=lambda:self.t)
        self.assertEqual(self.step(m)['reason'],'cooldown')
        self.assertEqual(len(m.posts),1)
        self.t=1110
        self.assertEqual(self.step(m)['reason'],'bought')
        self.assertEqual(len(m.posts),2)

    def test_cooldown_and_per_window_budget(self):
        m=FakeMarket(self.inv)
        self.assertEqual(self.step(m)['reason'],'bought')
        self.assertEqual(self.step(m)['reason'],'cooldown')
        self.t+=10;self.assertNotEqual(self.step(m)['reason'],'bought')
        self.t+=10;self.assertNotEqual(self.step(m)['reason'],'bought')
        self.assertEqual(len(m.posts),1)
        self.inv['restock']={'open':True,'opens_at':2000,'closes_at':2180}
        m.inv=copy.deepcopy(self.inv);self.t=2001
        self.assertEqual(self.step(m)['reason'],'bought')

    def test_supply_closes_during_hook(self):
        m=FakeMarket(self.inv)
        def late(inv,remaining):
            self.t=1181
            return {'buy':[{'sku':'x','qty':1}]}
        self.assertNotEqual(self.step(m,late)['reason'],'bought')
        self.assertFalse(m.posts)

    def test_second_hook_cannot_cross_deadline(self):
        m=FakeMarket(self.inv);count=0
        def late(inv,remaining):
            nonlocal count
            count+=1
            if count==2: self.t=1181
            return {'buy':[{'sku':'x','qty':1}]}
        self.assertEqual(self.step(m,late)['reason'],'hook_finished_after_window')
        self.assertFalse(m.posts)

    def test_cash_and_stock_rechecked_after_first_hook(self):
        m=FakeMarket(self.inv)
        def change(market):
            if market.gets==2: market.inv['balance']=1
        m.before_get=change
        self.assertEqual(self.step(m)['reason'],'cash_changed_before_post')
        self.assertFalse(m.posts)

    def test_ambiguous_result_blocks_retries_after_restart(self):
        m=FakeMarket(self.inv,[TimeoutError('lost response')])
        self.assertEqual(self.step(m)['reason'],'ambiguous_supply_result')
        self.t+=100
        self.g=SupplyGuard(self.d,'run',self.cfg,clock=lambda:self.t)
        self.assertEqual(self.step(m)['reason'],'unresolved_supply_intent')
        self.assertEqual(len(m.posts),1)

    def test_malformed_success_and_500_block_retries(self):
        for reply in [Response(200,{}),Response(200,ValueError()),Response(503,{}),Response(200,{'total':114})]:
            with self.subTest(reply=reply.status_code):
                self.g.path.unlink(missing_ok=True)
                m=FakeMarket(self.inv,[reply]);self.step(m)
                self.assertIsNotNone(self.g.read()['pending'])

    def test_402_no_more_buys_in_same_window(self):
        m=FakeMarket(self.inv,[Response(402,{})])
        self.assertEqual(self.step(m)['reason'],'supply_refused_402')
        self.t+=30
        self.assertEqual(self.step(m)['reason'],'cooldown')
        self.assertEqual(len(m.posts),1)

    def test_wrong_run_rejected_without_state_rewrite(self):
        self.step(FakeMarket(self.inv));old=self.g.path.read_bytes()
        other=SupplyGuard(self.d,'another-run',self.cfg,clock=lambda:self.t)
        with self.assertRaises(InputError): other.read()
        self.assertEqual(old,self.g.path.read_bytes())

    def test_sales_store_exact_ledger_dedup_conflict_and_gap(self):
        s=SalesStore(self.d/'sales.json','run',clock=lambda:self.t)
        s.heartbeat();s.record(ledger=1,sku='x',price=100,unit='u',cost=65)
        self.t+=10;s.record(ledger=1,sku='x',price=100,unit='u',cost=65)
        self.assertEqual(len(json.loads(s.path.read_text())['events']),1)
        with self.assertRaises(InputError): s.record(ledger=1,sku='x',price=99,unit='u',cost=65)
        with self.assertRaises(InputError): SalesStore(s.path,'reset',clock=lambda:self.t)
        self.t+=100;s.heartbeat()
        self.assertEqual(json.loads(s.path.read_text())['observed_since'],self.t)

    def test_only_real_completion_with_matching_stock_maps_to_sale(self):
        s=SalesStore(self.d/'sales.json','run',clock=lambda:self.t)
        before={'round':3,'units':[{'unit':'u','sku':'x','cost':65}]}
        done={'status':'completed','order':{'mandate_receipt':{'ledger':4,'unit':'u'}},
              'line_items':[{'id':'x'}],'totals':[{'amount':100}]}
        observe_completion(s,before,done)
        self.assertEqual(json.loads(s.path.read_text())['events'][0]['sku'],'x')
        done['line_items'][0]['id']='different'
        with self.assertRaises(InputError): observe_completion(s,before,done)


class ConnectionTests(unittest.TestCase):
    def test_runtime_refuses_missing_keys_without_creating_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            command=[sys.executable,str(ROOT/'run_market_restock.py'),'--kit',str(ROOT/'reference_sources/market_kit.py'),
                     '--market-run','test','--','join','--dir',str(d),'--market','https://unused.invalid','--name','test']
            r=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            self.assertIn('Existing shop_key.pem and buyer_key.pem are required',r.stderr)
            self.assertFalse((d/'shop_key.pem').exists())
            self.assertFalse((d/'buyer_key.pem').exists())
            self.assertFalse((d/'.market').exists())

    def test_runtime_rejects_changed_kit_before_loading_or_touching_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            (d/'shop_key.pem').write_bytes(b'SHOP-SENTINEL')
            (d/'buyer_key.pem').write_bytes(b'BUYER-SENTINEL')
            (d/'agent.json').write_text(json.dumps({'restock':{'cmd':COMMAND}}))
            bad=d/'different-kit.py';bad.write_text('raise AssertionError("must not execute")')
            command=[sys.executable,str(ROOT/'run_market_restock.py'),'--kit',str(bad),'--market-run','test','--',
                     'join','--dir',str(d),'--market','https://unused.invalid','--name','test']
            r=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            self.assertIn('Official kit changed',r.stderr)
            self.assertEqual((d/'shop_key.pem').read_bytes(),b'SHOP-SENTINEL')
            self.assertEqual((d/'buyer_key.pem').read_bytes(),b'BUYER-SENTINEL')
            self.assertFalse((d/'.market').exists())

    def test_connect_preserves_all_fields_and_private_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);p=d/'agent.json'
            original={'seller':{'cmd':'old-seller'},'buyer':{'cmd':'old-buyer'},'market_buyer':{'cmd':'r3-buyer'},'deployed':'https://existing','custom':{'keep':1}}
            p.write_text(json.dumps(original))
            (d/'shop_key.pem').write_bytes(b'KEY')
            diff=connect(p)
            self.assertIn('restock',diff)
            self.assertEqual(json.loads(p.read_text()),original)
            before=p.read_bytes();connect(p,apply=True)
            after=json.loads(p.read_text());after.pop('restock')
            self.assertEqual(after,original)
            self.assertEqual((d/'agent.json.before-restock').read_bytes(),before)
            self.assertEqual((d/'shop_key.pem').read_bytes(),b'KEY')
            self.assertEqual(connect(p),'already_connected\n')

    def test_existing_hold_command_requires_explicit_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'agent.json';p.write_text('{"restock":{"cmd":"python restock_hold.py"}}')
            with self.assertRaises(ValueError): connect(p,apply=True)
            connect(p,apply=True,replace=True)
            self.assertEqual(json.loads(p.read_text())['restock']['cmd'],COMMAND)


if __name__=='__main__':
    unittest.main()






