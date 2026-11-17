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
            store,guard=install_runtime(kit,md,'integration-test',Config())
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






