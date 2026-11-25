"""Public outgoing render seam approved in the task; isolated controller state."""
import ast
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
import negotiate
import seller_price_first as pf

def trace_leaks(rows):
 needles=("Opening","task-249","warranty","9000","10000")
 hashes={"event":32,"task_sha":64,"config_sha":64,"baseline_sha":64,"candidate_sha":64,"render_sha":64}
 found=[]
 def walk(value,path):
  if isinstance(value,dict):
   for key,item in value.items():
    if any(needle in key for needle in needles):found.append((path+(key,),key))
    walk(item,path+(key,))
  elif isinstance(value,list):
   for i,item in enumerate(value):walk(item,path+(i,))
  elif isinstance(value,str):
   field=path[1] if len(path)==2 else None
   size=hashes.get(field)
   if not (size and re.fullmatch(r"[0-9a-f]{"+str(size)+r"}",value)) and any(needle in value for needle in needles):
    found.append((path,value))
  elif type(value) in (int,float) and value in (9000,10000) and not (
    value==10000 and type(value) is int and path==(path[0],"price") and isinstance(rows[path[0]],dict) and rows[path[0]].get("kind")=="intent"):
   found.append((path,value))
 for i,row in enumerate(rows):walk(row,(i,))
 return found

class PriceFirstTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.r=pf.PriceFirst(self.tmp.name);self.r.root.mkdir()
  self.cfg=pf.trial_config("price-first-test",100,1900,"independent-seed")
  self.save(self.cfg)
  self.a="Thanks for your offer of 9000. I can come down a little. This is our final price. My price: 10000."
  self.b="My price: 10000. Thanks for your offer of 9000. I can come down a little. This is our final price."
 def save(self,c): (self.r.root/"config.json").write_text(json.dumps(c))
 def task(self,arm):
  return next(str(i) for i in range(100) if pf.task_arm(str(i),self.cfg)==arm)
 def render(self,text=None,price=10000,now=110,eligible=True,task=None):
  return self.r.render(task or self.task("B"),self.a if text is None else text,price,now=now,eligible=eligible)
 def rows(self):
  return [json.loads(l) for l in (self.r.root/"trace.jsonl").read_text().splitlines() if l.strip()]
 def test_exact_order_only_preserves_offer_final_and_tokens(self):
  self.assertEqual(self.render(),self.b)
  self.assertEqual(sorted(self.a.split()),sorted(self.b.split()))
  self.assertEqual(self.render(task=self.task("A")),self.a)
 def test_all_source_templates_generated_decisions_and_observed_number_fixture(self):
  # Actual branch/price/offer from metadata-only existing log; original body unknown.
  for source in (negotiate.LINES,negotiate.HOLD,negotiate.FINAL):
   for line in source:
    for final in (""," This is our final price."):
     a=line.format(offer=9845)+final+" My price: 10234."
     b="My price: 10234. "+line.format(offer=9845)+final
     self.assertEqual(self.render(a,10234),b)
     self.assertEqual(sorted(a.split()),sorted(b.split()))
     self.assertEqual(sorted(re.findall(r"\d+",a)),sorted(re.findall(r"\d+",b)))
  deal=dict(reserve=10000,ask=15000,rounds=1,history=[("you asked",15000)])
  original=copy.deepcopy(deal)
  d=negotiate.seller_turn("9000",deal,10000)
  dcopy=copy.deepcopy(d)
  self.render(d["message"]+f" My price: {d['price']}.",d["price"])
  self.assertEqual(deal,original);self.assertEqual(d,dcopy)
 def test_fullmatch_rejects_extra_facts_fair_harness_and_noncanonical(self):
  for text in (self.a+" Extra 42.", "Warranty: 6 hours. "+self.a,
    self.a.replace(" My price:"," Certificate attached. My price:"),
    self.a.replace("10000.","10001."),self.a+"\n",
    "Please tell me the price you offer. My price: 10000.",
    "You offered more than our price. Our price is only 10000; send it to close the deal. My price: 10000."):
   self.assertEqual(self.render(text),text)
  self.assertEqual(self.render(eligible=False),self.a) # harness provenance, even matching words
  self.assertEqual(self.render(eligible=1),self.a)
  for p in (None,True,-1,10000.0): self.assertEqual(self.render(price=p),self.a)
 def test_finite_missing_invalid_default_off_disabled(self):
  for now in (99,1900,2000,float("nan"),float("inf"),True):self.assertEqual(self.render(now=now),self.a)
  self.assertEqual(self.render(now=100),self.b)
  self.assertEqual(self.render(now=1899.9),self.b)
  for c in ({},dict(self.cfg,enabled=False),dict(self.cfg,end=1901),dict(self.cfg,start=True),dict(self.cfg,salt="changed")):
   self.save(c);self.assertEqual(self.render(),self.a)
  self.save(self.cfg)
  (self.r.root/"disabled").touch();self.assertEqual(self.render(),self.a)
  (self.r.root/"config.json").unlink();self.assertEqual(self.render(),self.a)
 def test_legacy_axis_overlap_and_malformed_fail_closed_but_expired_allowed(self):
  root=self.r.root.parent/"single_price";root.mkdir()
  p=root/"config.json"
  for c in (dict(enabled=True,start=100,end=1900),dict(enabled=True,start=1800,end=3600),dict(enabled=True,start=None,end=1900)):
   p.write_text(json.dumps(c));self.assertEqual(self.render(),self.a)
  p.write_text("{");self.assertEqual(self.render(),self.a)
  p.write_text(json.dumps(dict(enabled=True,start=1,end=100)))
  self.assertEqual(self.render(),self.b)
  self.assertFalse((root/"assignments.sqlite3").exists())
 def test_itt_all_tasks_no_cap_immutable_and_hash_only(self):
  text="Opening warranty 6 hours. My price: 10000."
  for i in range(250):self.assertEqual(self.render(text,task=f"task-{i}",eligible=False),text)
  intents=[r for r in self.rows() if r["kind"]=="intent"]
  self.assertEqual(len(intents),250)
  self.assertTrue(all(r["itt_arm"] in ("A","B") for r in intents))
  self.render(task="task-249")
  self.assertEqual([r for r in self.rows() if r["kind"]=="intent"][-1]["itt_arm"],intents[-1]["itt_arm"])
  self.assertEqual(trace_leaks(self.rows()),[])
  self.save(dict(self.cfg,start=200,end=2000))
  self.assertEqual(self.render(now=300,task="new-task"),self.a)
 def test_trace_leak_classifier_counterexamples(self):
  self.assertEqual(trace_leaks([{"event":"a"*28+"9000","task_sha":"b"*60+"9000"}]),[])
  for row in ({"text":"Opening warranty 6 hours"},{"task_sha":"task-249"},
    {"warranty":"redacted"},{"count":9000},{"text":"price 9000"},
    {"event":"a"*27+"9000g"},{"nested":[{"text":"task-249"}]},
    {"nested":{"price":10000}},{"price":"10000"},
    {"kind":"selection_observed","price":10000},{"kind":"intent","price":10000.0}):
   self.assertTrue(trace_leaks([row]),row)
  self.assertTrue(trace_leaks([{"kind":"intent","price":9000}]))   # the buyer's offer is never the recorded ask
  self.assertEqual(trace_leaks([{"kind":"intent","price":10000}]),[])
 def test_io_failures_cleanup_and_final_clock_never_leak_b(self):
  for target in ("record","config"):
   with mock.patch.object(self.r,target,side_effect=OSError("failure")):self.assertEqual(self.render(),self.a)
  with mock.patch.object(pf.sqlite3,"connect",side_effect=OSError("db")):self.assertEqual(self.render(),self.a)
  real=pf.sqlite3.connect
  class BadClose:
   def __init__(self,*a,**k):self.db=real(*a,**k)
   def __enter__(self):return self
   def __exit__(self,*args):return self.db.__exit__(*args)
   def __getattr__(self,k):return getattr(self.db,k)
   def close(self):self.db.close();raise OSError("close")
  with mock.patch.object(pf.sqlite3,"connect",side_effect=BadClose):self.assertEqual(self.render(),self.a)
  with mock.patch.object(pf.time,"time",side_effect=[1899,1900]):
   self.assertEqual(self.r.render(self.task("B"),self.a,10000,eligible=True),self.a)
 def test_final_selection_after_trace_config_and_pending_flush_io(self):
  for boundary in ("record","config"):
   for change in ("end","off","missing","edited"):
    self.save(self.cfg);(self.r.root/"disabled").unlink(missing_ok=True)
    clock=[1899];real=getattr(self.r,boundary);calls=[0]
    def delay(*args):
     value=real(*args);calls[0]+=1
     if boundary=="record" or calls[0]==2:
      if change=="end":clock[0]=1900
      elif change=="off":(self.r.root/"disabled").touch()
      elif change=="missing":(self.r.root/"config.json").unlink(missing_ok=True)
      else:self.save(dict(self.cfg,salt="edited"))
     return value
    with mock.patch.object(self.r,boundary,side_effect=delay),mock.patch.object(pf.time,"time",side_effect=lambda:clock[0]):
     self.assertEqual(self.r.render(self.task("B"),self.a,10000,eligible=True),self.a)
 def test_intent_not_selection_return_or_ack(self):
  self.assertEqual(self.render(),self.b)
  first=[r for r in self.rows() if r["kind"]=="intent"][0]
  self.assertIsNone(first["render_sha"]);self.assertEqual(first["effective_arm"],"unknown")
  self.assertEqual(self.render(now=1900),self.a)
  observed=[r for r in self.rows() if r["kind"]=="selection_observed"]
  self.assertEqual(len(observed),1)
  self.assertEqual(observed[0]["event"],first["event"])
  self.assertEqual(observed[0]["render_sha"],pf.sha(self.b))
  self.assertNotIn("ACK",observed[0]["scope"])
 def test_append_close_failure_has_no_false_observation(self):
  close=pf.os.close
  def bad_close(fd):close(fd);raise OSError("close")
  with mock.patch.object(pf.os,"close",side_effect=bad_close):self.assertEqual(self.render(),self.a)
  self.assertTrue(all(r["kind"]=="intent" for r in self.rows()))
 def test_invalid_window_builder(self):
  for args in (("bad/id",100,1900,"s"),("ok",100,1901,"s"),("ok",float("nan"),1900,"s"),("ok",100,1900,"")):
   with self.assertRaises(ValueError):pf.trial_config(*args)
