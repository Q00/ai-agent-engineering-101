import json
import shlex
import subprocess

import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

from restock_policy import Config
from restock_runtime import atomic_json

from run_market_restock import install_runtime
import restock_policy
ROOT=Path(restock_policy.__file__).resolve().parent


class HookIntegration(unittest.TestCase):
    def test_inventory_timeout_defers_without_purchase_and_next_poll_recovers(self):
        import httpx
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)
            inv = {'round': 3, 'units': [], 'restock': {
                'open': False, 'opens_at': time.time() + 600,
                'closes_at': time.time() + 780}}
            atomic_json(md / 'catalog.json', {})
            logs = []
            kit = types.SimpleNamespace(AcpShop=type('Shop', (), {}),
                                        restock=lambda *a: None, log=logs.append)
            _, guard = install_runtime(kit, md, 'timeout-test', Config())
            market = types.SimpleNamespace(call=Mock(side_effect=[
                httpx.ReadTimeout('inventory response timed out'),
                types.SimpleNamespace(status_code=200, json=lambda: inv),
            ]))
            agent = {'restock': {'cmd': 'restock_policy.py --state {state}'}}
            kit.restock(agent, md, market, md, inv, [])
            self.assertIn('ReadTimeout', logs[-1])
            self.assertIn('deferred to next poll', logs[-1])
            self.assertIsNone(guard.read()['pending'])
            kit.restock(agent, md, market, md, inv, [])
            self.assertEqual(logs[-1], '[restock] supply_closed')
            self.assertEqual(market.call.call_count, 2)
            for call in market.call.call_args_list:
                self.assertEqual(call.args, ('GET', '/api/inventory', 'shop'))

    def test_completion_logs_verified_task_and_sku(self):
        import asyncio
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)
            inv = {"round": 3, "units": [{"unit": "u1", "sku": "sku1", "cost": 85,
                   "kind": "goods", "supplied_at": time.time()-60}]}
            atomic_json(md / "inventory.json", inv)
            completed = {"status": "completed", "checkout_jwt": "signed-checkout",
                         "line_items": [{"id": "sku1"}], "totals": [{"amount": 100}],
                         "order": {"mandate_receipt": {"ledger": 123, "unit": "u1"}}}
            class Shop:
                async def handle(self, *args):
                    return types.SimpleNamespace(status_code=200, body=json.dumps(completed))
            logs = []
            verify = Mock(return_value={"task": "verified-task"})
            kit = types.SimpleNamespace(AcpShop=Shop, restock=lambda *a: None,
                                        log=logs.append, jwt_payload=verify)
            install_runtime(kit, md, 'test', Config())
            shop = kit.AcpShop()
            shop.md, shop.pub = md, 'shop-public-key'
            asyncio.run(shop.handle(types.SimpleNamespace(method='POST'),
                                    'acp/checkout_sessions/session/complete', b'{}', 'shop'))
            verify.assert_called_once_with('signed-checkout', 'shop-public-key')
            event = json.loads(next(x for x in logs if x.startswith('[settlement] ')).split(' ', 1)[1])
            self.assertEqual((event['task'], event['sku'], event['ledger']), ('verified-task', 'sku1', 123))
            evidence = json.loads(next((md/'history/settlements').glob('*.json')).read_text())
            self.assertEqual(evidence['task'], 'verified-task')

    def test_exploration_real_hook_and_persistent_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            md=Path(tmp); now=time.time()
            inv={'round':3,'balance':5_000_000,'units':[],'surge':[],
                 'restock':{'open':True,'opens_at':now-1,'closes_at':now+170}}
            catalog={'sku':{'kind':'goods','category':'goods','list_price':40000}}
            for name,value in [('inventory',inv),('catalog',catalog),('demand',[{'category':'goods'}])]:
                atomic_json(md/(name+'.json'),value)
            class Shop: pass
            def run(command,cwd):
                return subprocess.run(shlex.split(command),cwd=cwd,capture_output=True,text=True,check=True).stdout.splitlines()
            kit=types.SimpleNamespace(AcpShop=Shop,restock=lambda *a:None,log=lambda x:None,RESTOCK_EVERY=60,
                run_buyer=run,fill=lambda command,**kw:command.replace('{state}',shlex.quote(str(kw['state']))),
                last_json=lambda lines:json.loads(lines[-1]) if lines else None)
            store,guard=install_runtime(kit,md,'probe-test',Config(exploration_fraction=.01))
            class Market:
                posts=[]
                def call(self,method,path,role,**kw):
                    if method=='GET': return types.SimpleNamespace(status_code=200,json=lambda:inv)
                    self.posts.append(kw['json'])
                    inv['units']=[{'unit':'probe','sku':'sku','kind':'goods','category':'goods','cost':28000,
                                   'grade':'B','supplied_at':time.time(),'expires_at':None}]
                    inv['balance']-=28000
                    return types.SimpleNamespace(status_code=200,json=lambda:{'total':28000})
            market=Market()
            command=f'{shlex.quote(sys.executable)} {shlex.quote(str(ROOT/"restock_policy.py"))} --state {{state}}'
            kit.restock({'restock':{'cmd':command}},md,market,md,inv,[{'category':'goods'}])
            self.assertEqual(market.posts,[{'sku':'sku','qty':1}])
            self.assertEqual(guard.read()['exploration']['status'],'reconciled')
            self.assertEqual(guard.read()['exploration']['unit']['cost'],28000)
            self.assertEqual(len(list((md/'history/acquisitions').glob('*.json'))),1)

    def test_real_command_guard_and_inventory_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            md=Path(tmp)/'.market';md.mkdir()
            now=time.time()
            inv={'round':3,'balance':1_000_000,'units':[],
                 'restock':{'open':True,'opens_at':now-1,'closes_at':now+170}}
            catalog={'sku':{'kind':'goods','category':'goods','list_price':100}}
            atomic_json(md/'inventory.json',inv)
            atomic_json(md/'catalog.json',catalog)
            atomic_json(md/'demand.json',[{'category':'goods'}])
            log=[];commands=[]
            class Shop: pass
            def run(command,cwd):
                commands.append(command)
                r=subprocess.run(shlex.split(command),cwd=cwd,capture_output=True,text=True,check=True)
                return r.stdout.splitlines()
            kit=types.SimpleNamespace(AcpShop=Shop,restock=lambda *a:None,log=log.append,RESTOCK_EVERY=60,
                  run_buyer=run,fill=lambda command,**kw: command.replace('{state}',shlex.quote(str(kw['state']))),
                  last_json=lambda lines:json.loads(lines[-1]) if lines else None)
            store,guard=install_runtime(kit,md,'integration-test',Config(max_sku_units=1))
            store.heartbeat()
            store.record(ledger=1, sku='sku', price=160, unit='sold', cost=65)
            class Market:
                posts=[]
                def call(self,method,path,role,**kw):
                    if method=='GET': return types.SimpleNamespace(status_code=200,json=lambda:inv)
                    self.posts.append(kw['json'])
                    inv['units']=[{'unit':'new','sku':'sku','category':'goods','kind':'goods','cost':65,'grade':'A'}]
                    inv['balance']-=65
                    return types.SimpleNamespace(status_code=200,json=lambda:{'total':65})
            market=Market()
            cmd=f'{shlex.quote(sys.executable)} {shlex.quote(str(ROOT/"restock_policy.py"))} --state {{state}}'
            kit.restock({'restock':{'cmd':cmd}},Path(tmp),market,md,inv,[{'category':'goods'}])
            self.assertEqual(market.posts,[{'sku':'sku','qty':1}])
            self.assertEqual(len(commands),2)
            self.assertIsNone(guard.read()['pending'])
            self.assertEqual(kit.RESTOCK_EVERY,10)

    def test_real_hook_veto_is_authoritative(self):
        with tempfile.TemporaryDirectory() as tmp:
            md=Path(tmp);now=time.time()
            inv={'round':3,'balance':1_000_000,'units':[],
                 'restock':{'open':True,'opens_at':now-1,'closes_at':now+170}}
            atomic_json(md/'inventory.json',inv)
            atomic_json(md/'catalog.json',{'sku':{'kind':'goods','category':'goods','list_price':100}})
            atomic_json(md/'demand.json',[{'category':'goods'}])
            class Shop: pass
            kit=types.SimpleNamespace(AcpShop=Shop,restock=lambda *a:None,log=lambda x:None,RESTOCK_EVERY=60,
                 run_buyer=lambda *a:['{"buy":[]}'],fill=lambda command,**kw:command,
                 last_json=lambda lines:json.loads(lines[-1]))
            install_runtime(kit,md,'veto-test',Config())
            class Market:
                def call(self,method,*args,**kw):
                    assert method=='GET','a veto must prevent all POSTs'
                    return types.SimpleNamespace(status_code=200,json=lambda:inv)
            kit.restock({'restock':{'cmd':'python restock_policy.py --state {state}'}},Path(tmp),Market(),md,inv,[])


if __name__=='__main__':unittest.main()






