import copy,json,sqlite3,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from at_floor_exact_trial import AtFloorExactTrial,at_floor_exact

def deal():
 return dict(id="F1",unit="u-1",kind="food",cost=5000,reserve=6000,open=10000,ask=9700,rounds=0,history=[("you asked",9700)],expires_at=99999,reader_trial_policy_sha="baseline")

class AtFloorExactTests(unittest.TestCase):
 def configured(self,td):
  t=AtFloorExactTrial(td);t.root.mkdir()
  (t.root/"seller_concession_trial.json").write_text(json.dumps(dict(enabled=True,id="floor-trial",start=1000,end=2800,seed=13)))
  return t
 @patch("negotiate.engine_on",return_value=True)
 def test_existing_numeric_rule_and_no_guard_change(self,_):
  import fast_classify,seller
  for kind in ("food","goods"):
   for r in (1,3,9):
    d=deal();d.update(kind=kind,rounds=r)
    for v in (1,4850,7000,9699):
     before=copy.deepcopy(d);a=at_floor_exact(f"I can offer {v}.",d,9700,10,True)
     self.assertEqual(a,fast_classify.seller_turn(str(v),d,9700,None))
     self.assertEqual(seller.price_guards(d,a["action"],a["price"],v,9700),("counter",9700))
     self.assertEqual(d,before)
 @patch("negotiate.engine_on",return_value=True)
 def test_strict_exclusions_and_real_unit_or_engine_gate(self,_):
  d=deal();d["rounds"]=1
  for text in ("7000","I can offer 9700.","I can offer 9900.","I do not offer 7000.","I can offer 7000. if warranty C","How many hours of shelf life are left?"):
   self.assertIsNone(at_floor_exact(text,d,9700,10,True))
  for floor,ok in ((9500,True),(True,True),(9700,False),(9700,1)):
   self.assertIsNone(at_floor_exact("I can offer 7000.",d,floor,10,ok))
  d["rounds"]=10;self.assertIsNone(at_floor_exact("I can offer 7000.",d,9700,10,True))
  d["rounds"]=1
  with patch("negotiate.engine_on",return_value=False):self.assertIsNone(at_floor_exact("I can offer 7000.",d,9700,10,True))
 def test_off_no_database_and_unowned_no_assignment(self):
  with tempfile.TemporaryDirectory() as td:
   t=AtFloorExactTrial(td);d=deal();before=copy.deepcopy(d)
   t.quote(str(uuid.uuid4()),d,now=1100,unit_ok=True)
   self.assertEqual(d,before);self.assertFalse(t.root.exists())
   t=self.configured(td);d=deal();t.quote(str(uuid.uuid4()),d,now=1100,unit_ok=False)
   self.assertNotIn("at_floor_exact",d);self.assertFalse((t.root/"seller_concession_trial.sqlite3").exists())
 @patch("negotiate.engine_on",return_value=True)
 def test_AB_quote_identical_B_only_turn_sticky_and_disable(self,_):
  with tempfile.TemporaryDirectory() as td:
   t=self.configured(td);arms=set();b=None
   for i in range(8):
    task=str(uuid.uuid5(uuid.NAMESPACE_URL,"floor"+str(i)));d=deal();old=copy.deepcopy(d)
    t.quote(task,d,now=1100,unit_ok=True);a=d["at_floor_exact"];arms.add(a["arm"])
    self.assertEqual(d["ask"],old["ask"]);self.assertEqual(d["history"],old["history"])
    d["rounds"]=1;r=t.decide("I can offer 7000.",d,9700,10,unit_ok=True)
    self.assertEqual(r is not None,a["arm"]=="B")
    if a["arm"]=="B":b=(task,copy.deepcopy(d))
   self.assertEqual(arms,{"A","B"})
   (t.root/"disabled").touch();d=deal();t.quote(str(uuid.uuid4()),d,now=1100,unit_ok=True)
   self.assertNotIn("at_floor_exact",d)
   self.assertIsNotNone(t.decide("I can offer 7000.",b[1],9700,10,unit_ok=True))
   changed=copy.deepcopy(b[1]);changed["cost"]+=1
   self.assertIsNone(t.decide("I can offer 7000.",changed,9700,10,unit_ok=True))
 def test_other_experiment_and_uuid_and_expired_window(self):
  from seller_concession_trial import Trial
  with tempfile.TemporaryDirectory() as td:
   t=self.configured(td);other=Trial(Path(td)/"other");other.root.mkdir()
   (other.root/"seller_concession_trial.json").write_text(json.dumps(dict(enabled=True,id="other",start=1000,end=2800,seed=1)))
   for task,now,others in (("short",1100,()),(str(uuid.uuid4()),2900,()),(str(uuid.uuid4()),1100,(other,))):
    d=deal();t.quote(task,d,now=now,unit_ok=True,others=others);self.assertNotIn("at_floor_exact",d)
 def test_parallel_cap(self):
  import concurrent.futures
  with tempfile.TemporaryDirectory() as td:
   t=self.configured(td)
   def one(i):
    d=deal();t.quote(str(uuid.uuid5(uuid.NAMESPACE_URL,"fcap"+str(i))),d,now=1100,unit_ok=True)
   with concurrent.futures.ThreadPoolExecutor(max_workers=24) as p:list(p.map(one,range(240)))
   with sqlite3.connect(t.root/"seller_concession_trial.sqlite3") as db:aa=[json.loads(r[0]) for r in db.execute("SELECT data FROM assignments")]
   selected=[a for a in aa if a.get("id")=="floor-trial"]
   self.assertLessEqual(len(selected),200);self.assertLessEqual(sum(a["arm"]=="B" for a in selected),100)
