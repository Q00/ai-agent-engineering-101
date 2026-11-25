from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import httpx
import market_memory
import seller_r3
from context_compaction import SELLER_MEMORY_BYTES,size


class SellerContextTests(unittest.TestCase):
    def test_large_negotiation_preserves_conditions_and_price_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'events.jsonl'
            at='2026-11-03T00:00:00+00:00';expires=datetime(2026,11,3,2,tzinfo=timezone.utc).timestamp()
            market_memory.record('opening',path=p,at=at,task_id='one',sku='F1',grade='C',ask=1000,cost=500,expires_at=expires)
            for i in range(1000):
                market_memory.record('offer',path=p,task_id='one',price=500+i)
                market_memory.record('counter',path=p,task_id='one',ask=900+i)
            market_memory.record('settled',path=p,task_id='one',sku='F1',grade='C',price=800,ledger=1)
            raw=p.read_bytes();view=market_memory.compact_context('F1','C',path=p)
            self.assertLessEqual(size(view),SELLER_MEMORY_BYTES)
            case=view['recent_same_grade'][0]
            self.assertEqual(case['offer_count'],1000)
            self.assertEqual(case['offers'],[1496,1497,1498,1499])
            self.assertEqual(case['hours_left_at_opening'],2)
            self.assertEqual(case['cost'],500)
            self.assertEqual(case['settled_price'],800)
            self.assertEqual(p.read_bytes(),raw)

    def test_settlement_grade_drives_comparison_not_obsolete_opening_grade(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'events.jsonl'
            market_memory.record('opening',path=p,task_id='one',sku='F1',grade='A',ask=1000)
            market_memory.record('settled',path=p,task_id='one',sku='F1',grade='C',price=500,ledger=1)
            view=market_memory.compact_context('F1','C',path=p)
            self.assertEqual(view['same_grade_attempts'],1)
            self.assertEqual(view['recent_same_grade'][0]['grade'],'A')
            self.assertEqual(view['recent_same_grade'][0]['settled_grade'],'C')

    def test_snapshot_noise_cannot_hide_relevant_settlement(self):
        history=[{'event':'opening','task_id':'one','sku':'F1','grade':'B','ask':1000},
                 {'event':'settled','task_id':'one','sku':'F1','grade':'B','price':800,'ledger':1}]
        history += [{'event':'inventory_snapshot','stock':{}} for _ in range(1000)]
        view=market_memory.recent_trade_context(history,{'F1'})
        self.assertEqual(view['confirmed_by_sku'],{'F1':1})
        self.assertEqual(view['cases'][0]['settled_price'],800)

    def test_same_sku_grade_memory_is_not_repeated_for_every_unit(self):
        async def exchange():
            app=seller_r3.build(9999)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                response=await client.post('/',json={'jsonrpc':'2.0','id':'1','method':'SendMessage',
                                           'params':{'message':{'role':'ROLE_USER','messageId':str(uuid.uuid4()),
                                                                'parts':[{'text':'I want F1'}]}}},headers={'A2A-Version':'1.0'})
                self.assertNotIn('error',response.json())
        with tempfile.TemporaryDirectory() as directory:
            previous=Path.cwd();os.chdir(directory)
            try:
                md=Path('.market');md.mkdir()
                (md/'inventory.json').write_text(json.dumps({'round':3,'units':[
                    {'unit':f'u{i}','sku':'F1','name':'Food','grade':'A','kind':'food','list_price':1000,
                     'cost':600,'expires_at':time.time()+7200} for i in range(12)]}))
                with patch.object(seller_r3,'decide_json',return_value={'action':'ask','price':1000,'unit_id':'u0','reason':'current stock'}) as model:
                    import asyncio
                    asyncio.run(exchange())
                prompt=model.call_args.args[1]
                self.assertEqual(len(prompt['unit_options']),12)
                self.assertEqual(list(prompt['memory_by_sku_grade']),['F1:A'])
                self.assertTrue(all(u['memory_key']=='F1:A' for u in prompt['unit_options']))
            finally:os.chdir(previous)


if __name__=='__main__':unittest.main()
