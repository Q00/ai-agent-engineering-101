import copy
import unittest
from unittest.mock import patch

from deep_lowball_trial import deep_lowball


class DeepLowballRuleTests(unittest.TestCase):
    @patch("negotiate.engine_on", return_value=True)
    def test_template_reuses_numeric_counter_without_changing_deal(self, _):
        import seller
        for kind in ("food", "goods"):
            for rounds in (1, 3, 9):
                deal = dict(unit="unit", kind=kind, ask=10000, reserve=6000,
                            rounds=rounds, history=[("you asked", 10000)])
                before = copy.deepcopy(deal)
                result = deep_lowball("I can offer 2000.", deal, 8000,
                                      unit_ok=True)
                self.assertEqual((result["action"], result["price"]), ("counter", 10000))
                self.assertEqual(seller.price_guards(deal, "counter", 10000, 2000, 8000),
                                 ("counter", 10000))
                self.assertEqual(deal, before)

class DeepLowballControllerTests(unittest.TestCase):
    def test_default_off_and_paired_quote_unchanged(self):
        import tempfile, json, uuid
        from deep_lowball_trial import DeepLowballTrial
        with tempfile.TemporaryDirectory() as td:
            t = DeepLowballTrial(td)
            def fresh():
                return dict(id='F1', unit='u1', kind='food', reserve=6000,
                            open=10000, ask=9700, rounds=0,
                            history=[('you asked',9700)], cost=None,
                            expires_at=99999, reader_trial_policy_sha='base')
            d=fresh(); old=copy.deepcopy(d)
            t.quote(str(uuid.uuid4()),d,now=1100,unit_ok=True)
            self.assertEqual(d,old); self.assertFalse(t.root.exists())
            t.root.mkdir()
            (t.root/'seller_concession_trial.json').write_text(json.dumps(
                dict(enabled=True,id='deep',start=1000,end=2800,seed=13)))
            arms=[]
            for _ in range(2):
                d=fresh(); old=copy.deepcopy(d)
                t.quote(str(uuid.uuid4()),d,now=1100,unit_ok=True)
                a=d.pop('deep_lowball'); arms.append(a['arm'])
                self.assertEqual(d,old); self.assertIsNone(a['cost'])
            self.assertEqual(sorted(arms),['A','B'])

    @patch('negotiate.engine_on', return_value=True)
    def test_treatment_sticky_600_disable_snapshot_and_trace(self, _):
        import tempfile,json,uuid
        from deep_lowball_trial import DeepLowballTrial
        with tempfile.TemporaryDirectory() as td:
            t=DeepLowballTrial(td); t.root.mkdir()
            (t.root/'seller_concession_trial.json').write_text(json.dumps(
                dict(enabled=True,id='deep',start=1000,end=2800,seed=13)))
            for i in range(2):
                d=dict(id='F1',unit='u1',kind='food',reserve=6000,open=10000,
                       ask=9700,rounds=0,cost=None,expires_at=99999,
                       history=[('you asked',9700)],reader_trial_policy_sha='base')
                task=str(uuid.uuid4());t.quote(task,d,now=2700,unit_ok=True)
                d['rounds']=1
                with patch('deep_lowball_trial.time.time',return_value=2801):
                    r=t.decide('I can offer 2000.',d,8000,unit_ok=True)
                self.assertEqual(r is not None,d['deep_lowball']['arm']=='B')
                if r:b=copy.deepcopy(d); btask=task
            (t.root/'disabled').touch()
            with patch('deep_lowball_trial.time.time',return_value=3299):
                self.assertIsNotNone(t.decide('I can offer 2000.',b,8000,unit_ok=True))
                t.event(btask,b,'deal',9700)
                altered=copy.deepcopy(b);altered['expires_at']=88888
                self.assertIsNone(t.decide('I can offer 2000.',altered,8000,unit_ok=True))
            with patch('deep_lowball_trial.time.time',return_value=3300):
                self.assertIsNone(t.decide('I can offer 2000.',b,8000,unit_ok=True))
            rows=[json.loads(x) for x in (t.root/'seller_concession_trial.jsonl').read_text().splitlines()]
            self.assertEqual(sum(x['event']=='quote' for x in rows),2)
            self.assertIsNone(rows[-1]['cost'])
            self.assertTrue(rows[-1]['deep_lowball_used'])

    def test_pre_pick_conflict_and_fixed_window(self):
        import tempfile,json
        from pathlib import Path
        from deep_lowball_trial import DeepLowballTrial
        from seller_concession_trial import Trial
        with tempfile.TemporaryDirectory() as td:
            t=DeepLowballTrial(td);t.root.mkdir()
            p=t.root/'seller_concession_trial.json'
            cfg=dict(enabled=True,id='deep',start=1000,end=2800,seed=13)
            p.write_text(json.dumps(cfg))
            o=Trial(Path(td)/'other');o.root.mkdir()
            (o.root/'seller_concession_trial.json').write_text(json.dumps(dict(cfg,id='other')))
            self.assertFalse(t.pre_pick_ok((o,),now=1100))
            (o.root/'adopted').touch()
            self.assertTrue(t.pre_pick_ok((o,),now=1100))
            p.write_text(json.dumps(dict(cfg,end=2799)))
            self.assertIsNone(t.config())

class DeepLowballSellerTests(unittest.TestCase):
    def test_real_stock_quote_turn_paid_and_pre_pick_gate(self):
        import os,sys,tempfile,subprocess
        from pathlib import Path
        script = r'''
import asyncio,json,time,uuid
from types import SimpleNamespace
from unittest.mock import patch
import seller
from shop_stock import StockBook
root=seller.DEEP_LOWBALL.root
root.mkdir(parents=True)
(root/'seller_concession_trial.json').write_text(json.dumps(dict(enabled=True,id='deep',start=time.time()-1,end=time.time()+1799,seed=13)))
# Set exact fixed duration independently of clock calls.
c=json.loads((root/'seller_concession_trial.json').read_text());c['end']=c['start']+1800
(root/'seller_concession_trial.json').write_text(json.dumps(c))
for ctrl in (seller.LATE_CONCESSION,seller.AT_FLOOR_EXACT,seller.EXACT_ACCEPT,seller.EXACT_COUNTER):
 ctrl.root.mkdir(parents=True,exist_ok=True);(ctrl.root/'adopted').touch()
u=dict(unit='u',sku='G1',name='fixture',category='food',kind='food',cost=6000,list_price=10000,grade='A',supplied_at=time.time()-3600,expires_at=time.time()+72*3600)
book=StockBook(lambda:dict(round=3,units=[u]))
shop=seller.Seller(None,'fixture',stock=book)
class Capture:
 def new_agent_message(self,**kw):return kw
 async def requires_input(self,message):self.message=message
 async def reject(self,message):self.rejected=True
 async def failed(self,message):raise AssertionError('failed')
cap=Capture();say=lambda text,ask=None:dict(text=text,ask=ask)
async def run():
 arms=set()
 for i in range(2):
  task=SimpleNamespace(id=str(uuid.uuid4()),context_id='ctx')
  await shop.open_from_stock(task,'G1',cap,say)
  d=shop.open[task.id];a=d['deep_lowball'];arms.add(a['arm'])
  quoted=d['ask'];assert cap.message['ask']==quoted
  context=SimpleNamespace(current_task=task,message=None)
  with patch.object(seller,'TaskUpdater',return_value=cap),patch.object(seller,'get_message_text',return_value='I can offer 1.'),patch.object(seller,'decide_turn',return_value=dict(action='counter',price=quoted,buyer_offer=1,message='baseline')),patch.object(seller.fast_classify,'JEV_ON',False):
   await shop._execute(context,None)
  assert d['deep_lowball_used']==(a['arm']=='B')
  assert d['ask']==quoted
  shop.paid(dict(task=task.id,deal=d,price=quoted))
  book.release('u')
 assert arms=={'A','B'}
 # Actual StockBook recheck at turn time must reject missing, expired and other-owner units.
 for reason in ('gone','expired','otherowner'):
  task=SimpleNamespace(id=str(uuid.uuid4()),context_id='ctx')
  cap.rejected=False
  await shop.open_from_stock(task,'G1',cap,say)
  d=shop.open[task.id]
  if reason=='gone':book.sold.add('u')
  elif reason=='expired':u['expires_at']=time.time()-1
  else:book.release('u');book.hold('u','different-task')
  with patch.object(seller,'TaskUpdater',return_value=cap),patch.object(seller,'get_message_text',return_value='I can offer 1.'):
   await shop._execute(SimpleNamespace(current_task=task,message=None),None)
  assert cap.rejected and task.id not in shop.open
  assert not d.get('deep_lowball_used')
  book.sold.discard('u');book.release('u');u['expires_at']=time.time()+72*3600
 # Conflict must reject before stock pick: a sold/unavailable book still rejects cleanly.
 other=seller.GRADE_PREFERENCE;other.root.mkdir(parents=True,exist_ok=True)
 (other.root/'seller_concession_trial.json').write_text(json.dumps(dict(c,id='other')))
 cap.rejected=False
 with patch.object(book,'pick',side_effect=AssertionError('picked before conflict gate')):
  await shop.open_from_stock(SimpleNamespace(id=str(uuid.uuid4())),'G1',cap,say)
 assert cap.rejected
with patch.object(seller,'ask',side_effect=AssertionError('unexpected external harness')):
 asyncio.run(run())
seller.MEMORY_WRITER.shutdown(wait=True)
rows=[json.loads(x) for x in (root/'seller_concession_trial.jsonl').read_text().splitlines()]
assert sum(r['event']=='turn' for r in rows)==2
assert sum(r['event']=='deal' for r in rows)==2
print('SELLER_SEAM_OK')
'''
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'pricing.json').write_text('{}');(p/'memory.json').write_text('{"negotiations":[]}')
            env=dict(os.environ,MARKET_DIR=td,SHOP_PRICING=str(p/'pricing.json'),SHOP_MEMORY=str(p/'memory.json'),NEGOTIATE_ENGINE='jevrule')
            r=subprocess.run([sys.executable,'-B','-c',script],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            self.assertIn('SELLER_SEAM_OK',r.stdout)

class DeepLowballBoundaryTests(unittest.TestCase):
    @patch('negotiate.engine_on',return_value=True)
    def test_rule_exclusions(self,_):
        d=dict(unit='u',kind='goods',ask=10000,reserve=6000,rounds=1)
        for text in ('2000','I can offer 0.','I can offer 5000.',
                     'I can offer 8000.','I do not offer 2000.',
                     'I can offer 2000. if warranty C',
                     'OK, 2000 it is. Deal at 3000.'):
            self.assertIsNone(deep_lowball(text,d,8000,unit_ok=True),text)
        for changes,floor,ok in (({},10000,True),({},8000,False),({},8000,1),
                                  ({'rounds':10},8000,True),({'rounds':0},8000,True),
                                  ({'kind':'other'},8000,True),({'unit':None},8000,True)):
            self.assertIsNone(deep_lowball('I can offer 2000.',dict(d,**changes),floor,unit_ok=ok))
        with patch('negotiate.engine_on',return_value=False):
            self.assertIsNone(deep_lowball('I can offer 2000.',d,8000,unit_ok=True))

    def test_concurrent_caps_sticky_identity_snapshot_and_no_adoption(self):
        import tempfile,json,uuid,concurrent.futures
        from deep_lowball_trial import DeepLowballTrial
        with tempfile.TemporaryDirectory() as td:
            t=DeepLowballTrial(td);t.root.mkdir()
            cfg=dict(enabled=True,id='deep',start=1000,end=2800,seed=13)
            (t.root/'seller_concession_trial.json').write_text(json.dumps(cfg))
            def fresh():
                return dict(id='F1',unit='u',kind='food',reserve=6000,open=10000,
                            ask=9700,rounds=0,history=[('you asked',9700)],cost=None,
                            expires_at=99999,reader_trial_policy_sha='base')
            def one(i):
                d=fresh();task=str(uuid.uuid5(uuid.NAMESPACE_URL,'deep-cap-'+str(i)))
                t.quote(task,d,now=1100,unit_ok=True)
                return task,d
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                results=list(pool.map(one,range(240)))
            enrolled=[(task,d) for task,d in results if d['deep_lowball'].get('id')]
            self.assertLessEqual(len(enrolled),200)
            self.assertLessEqual(sum(d['deep_lowball']['arm']=='B' for _,d in enrolled),100)
            self.assertGreaterEqual(len(enrolled),199)
            task,d=enrolled[0];old=copy.deepcopy(d['deep_lowball'])
            (t.root/'disabled').touch()
            repeat=fresh();t.quote(task,repeat,now=1200,unit_ok=True)
            self.assertEqual(repeat['deep_lowball'],old)
            for key,value in (('cost',1),('expires_at',88888),('reader_trial_policy_sha','changed'),('unit','different')):
                changed=fresh();changed[key]=value
                t.quote(task,changed,now=1200,unit_ok=True)
                self.assertTrue(changed['deep_lowball']['contaminated'])
            for task in ('short',str(uuid.uuid4())):
                d=fresh();t.quote(task,d,now=1200,unit_ok=False)
                self.assertNotIn('deep_lowball',d)
            (t.root/'disabled').unlink();(t.root/'adopted').touch()
            d=fresh();t.quote(str(uuid.uuid4()),d,now=1200,unit_ok=True)
            self.assertNotIn('deep_lowball',d)

    def test_uuid_window_strata_and_existing_trial_slot(self):
        import tempfile,json,uuid
        from deep_lowball_trial import DeepLowballTrial
        with tempfile.TemporaryDirectory() as td:
            t=DeepLowballTrial(td);t.root.mkdir()
            (t.root/'seller_concession_trial.json').write_text(json.dumps(
                dict(enabled=True,id='deep',start=1000,end=2800,seed=13)))
            def fresh(kind='food',price=10000):
                return dict(id='F1',unit='u',kind=kind,reserve=6000,open=price,
                            ask=price,rounds=0,cost=None,expires_at=99999,
                            history=[('you asked',price)],reader_trial_policy_sha='base')
            for task,now in (('short',1100),(str(uuid.uuid4()).upper(),1100),
                             (str(uuid.uuid4()),999),(str(uuid.uuid4()),2800)):
                d=fresh();old=copy.deepcopy(d)
                t.quote(task,d,now=now,unit_ok=True)
                self.assertEqual(d,old)
            for kind,price,now,stratum in (('food',10000,1100,'food|low|0'),
                                          ('goods',300000,1400,'goods|high|1')):
                arms=[]
                for _ in range(2):
                    d=fresh(kind,price);t.quote(str(uuid.uuid4()),d,now=now,unit_ok=True)
                    arms.append(d['deep_lowball']['arm'])
                    self.assertEqual(d['deep_lowball']['stratum'],stratum)
                self.assertEqual(sorted(arms),['A','B'])
            d=fresh();d['at_floor_exact']={'id':'other-trial','arm':'A'}
            t.quote(str(uuid.uuid4()),d,now=1100,unit_ok=True)
            self.assertNotIn('deep_lowball',d)
