import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from a2a.server.tasks import InMemoryTaskStore
from optimizer import db
from seller_handoff import transcript,recover_receipts,restore,snapshot_tasks,snapshot_header

class HandoffTests(unittest.TestCase):
    def test_streamed_archive_preserves_large_unicode_task_and_signed_data(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'handoff.json';tasks=[{'id':'a','history':'한글'*80000},{'id':'b','artifacts':[{'parts':[{'data':{'sig_seller':'original','price':110}}]}]}]
            p.write_text(json.dumps({'at':123.5,'tasks':tasks},ensure_ascii=False),encoding='utf-8')
            self.assertEqual(snapshot_header(p)['at'],123.5)
            self.assertEqual(list(snapshot_tasks(p)),tasks)

    def messages(self):
        return [dict(messageId='m1',role='ROLE_USER',parts=[{'text':'Quote only: x'}]),
                dict(messageId='m2',role='ROLE_AGENT',parts=[{'text':'X: asking 120.'}]),
                dict(messageId='m3',role='ROLE_USER',parts=[{'text':'100'}]),
                dict(messageId='m4',role='ROLE_AGENT',parts=[{'text':'Counteroffer: 110. Includes a 6-hour warranty.'}])]

    def test_transcript_preserves_rounds_and_deduplicates_status_message(self):
        messages=self.messages();lines=transcript({'history':messages,'status':{'message':messages[-1]}})
        self.assertEqual(len(lines),4);self.assertEqual(sum(s.startswith('buyer:') for s in lines),2)
        self.assertIn('Quote only',lines[0])

    def test_verified_original_receipts_only_without_resigning(self):
        with tempfile.TemporaryDirectory() as d:
            original=dict(task='t',price=110,shop='tomato&potato',item='x',sig_seller='original-signature')
            event=dict(kind='result',round=3,data=dict(shop='totato2203-r3',verified=True,checks={'sig_seller_ok':True},receipt=original))
            Path(d,'ledger.jsonl').write_text(json.dumps(event))
            task=dict(id='t',status={'state':'TASK_STATE_COMPLETED'})
            self.assertEqual(recover_receipts([task],d),1)
            self.assertEqual(task['artifacts'][0]['parts'][0]['data'],original)
            self.assertEqual(recover_receipts([task],d),0)
            event['data']['verified']=False;Path(d,'ledger.jsonl').write_text(json.dumps(event))
            self.assertEqual(recover_receipts([dict(id='t',status={'state':'TASK_STATE_COMPLETED'})],d),0)

    def test_live_restore_preserves_quote_warranty_and_negotiation_history(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);messages=self.messages()
            raw=dict(id='t',contextId='c',history=messages,status=dict(state='TASK_STATE_INPUT_REQUIRED',message=messages[-1]))
            (p/'inventory.json').write_text(json.dumps({'units':[dict(unit='u',sku='x',cost=50)]}))
            (p/'seller-handoff.json').write_text(json.dumps({'at':time.time(),'tasks':[raw]}))
            with db(d) as c:c.execute('insert into trials(task,sku,unit,ask,last_offer) values(?,?,?,?,?)',('t','x','u',120,100))
            agent=SimpleNamespace(open={},promised=set(),promised_at={})
            self.assertEqual(restore(agent,InMemoryTaskStore(),p),1)
            deal=agent.open['t'];self.assertEqual(deal['ask'],110);self.assertEqual(deal['warranty_hours'],6)
            self.assertEqual(len(deal['lines']),4);self.assertEqual(deal['offer'],100)
