import json,math,tempfile,time,unittest
from pathlib import Path
from seller_ledger import Program
from seller_strategy import sale_quote,negotiate,protected_floor,observed_hazard
from buyer_memory import Memory
from seller_offers import offers
from market_intel import Intel

class RolloutTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.p=Program(self.root)
  self.u=dict(unit='u',sku='sku',name='item',grade='B',kind='goods',list_price=20000,cost=8000,expires_at=None)
 def tearDown(self):self.p.close();self.tmp.cleanup()
 def test_haggle_keeps_benefits_and_warranty_margin(self):
  q=sale_quote(self.u,{},self.p)
  for offer in (1,10000,14000,20000):
   n=negotiate(self.u,q,offer,{},self.p)
   self.assertLessEqual(n['price'],q['price']);self.assertEqual(n['warranty_hours'],6)
   self.assertGreaterEqual(n['price']*.85-n['reward'],self.u['cost']+math.ceil(self.u['cost']*.09))
  self.assertLess(negotiate(self.u,q,14000,{},self.p)['price'],q['price'])
 def test_mature_public_breakage_cohort_lowers_floor_without_removing_margin(self):
  class Evidence:
   def source(self,*args,**kwargs):return {'shops':[{'name':'25512088','claims':139,'broken':11}]}
  hazard,basis=observed_hazard(Evidence())
  self.assertAlmostEqual(hazard,13/143);self.assertIn('139',basis)
  self.assertLess(protected_floor(self.u,self.p.p,hazard),protected_floor(self.u,self.p.p,.15))
 def test_full_refund_cash_reserved_atomically(self):
  q=sale_quote(self.u,{},self.p);inv=dict(start=1000000,balance=500000+q['price'])
  self.p.accept('one','u','sku',8000,q,inventory=inv)
  with self.assertRaises(ValueError):self.p.accept('two','v','sku',8000,q,inventory=inv)
  self.assertEqual(self.p.db.execute('select count(*) from warranties').fetchone()[0],1)
 def test_unaccepted_legacy_quote_holds_never_hide_offers(self):
  (self.root/'inventory.json').write_text(json.dumps(dict(round=3,units=[self.u])))
  with self.p.db:
   self.p.db.execute('CREATE TABLE quote_holds(task TEXT PRIMARY KEY,unit TEXT,until REAL)')
   self.p.db.execute('INSERT INTO quote_holds VALUES(?,?,?)',('q','u',time.time()+60))
  intel=Intel(self.root)
  try:
   self.assertEqual(offers(self.root,self.p,intel)[0]['warranty_hours'],6)
   with self.p.db:self.p.db.execute('UPDATE quote_holds SET until=0')
   self.assertEqual(offers(self.root,self.p,intel)[0]['warranty_hours'],6)
  finally:intel.close()
 def test_model_excludes_quote_only_and_uncertain_orders(self):
  m=Memory(self.root)
  try:
   for oid,kind,state in [('a','response','paid'),('b','offer','uncertain'),('c','offer','no_deal')]:
    m.begin(dict(order=oid));m.record(oid,'shop','sku','task',kind);m.state(oid,state)
   x=m.deal_estimate('shop');self.assertEqual(x['trials'],1);self.assertEqual(x['probability'],.4)
  finally:m.close()
 def test_discovery_does_not_trigger_conversion_discount(self):
  # Six physical units and repeated browsing are not six purchase quotes.
  units=[dict(self.u,unit='u'+str(i)) for i in range(6)]
  (self.root/'inventory.json').write_text(json.dumps(dict(round=3,units=units)))
  catalog={'sku':dict(category='chairs',list_price=20000)}
  (self.root/'catalog.json').write_text(json.dumps(catalog))
  intel=Intel(self.root)
  try:
   intel.save_source('shops',{'shops':[dict(name='rival',offers=[dict(sku='sku',qty=4)])]})
   for _ in range(3):
    self.assertEqual(offers(self.root,self.p,intel)[0]['price'],16000)
   self.assertEqual(intel.db.execute("SELECT COUNT(*) FROM decisions WHERE role='seller_quote'").fetchone()[0],0)
   # Genuine purchase quotes still enable the existing evidence-based discount.
   for _ in range(5):sale_quote(self.u,catalog,self.p,intel)
   self.assertEqual(offers(self.root,self.p,intel)[0]['price'],15680)
   self.assertEqual(intel.db.execute("SELECT COUNT(*) FROM decisions WHERE role='seller_quote'").fetchone()[0],5)
   q=sale_quote(self.u,catalog,self.p,intel)
   self.assertEqual(q['price'],15680)
   self.assertGreaterEqual(q['price']*.85-q['reward'],self.u['cost']+math.ceil(self.u['cost']*.09))
  finally:intel.close()
 def test_conditions_do_not_learn_unwarranted_goods(self):
  m=Memory(self.root)
  try:
   m.begin(dict(order='a'));m.state('a','paid')
   with m.db:
    m.db.execute('INSERT INTO payments VALUES(?,?,?,?,?,?,?)',('a','shop','sku',8000,1,1,None))
    m.db.execute('INSERT INTO outcomes VALUES(?,?)',('a',json.dumps(dict(grade='B',warranty_hours=0))))
   self.assertIsNone(m.condition_reference('shop','sku',dict(kind='goods')))
   with m.db:m.db.execute('UPDATE outcomes SET details=?',(json.dumps(dict(grade='B',warranty_hours=6)),))
   self.assertEqual(m.condition_reference('shop','sku',dict(kind='goods'),'B'),8000)
   self.assertIsNone(m.condition_reference('shop','sku',dict(kind='goods'),'A'))
  finally:m.close()
 def test_substitute_competition_and_conversion_feed_price_without_scarcity_markup(self):
  intel=Intel(self.root)
  try:
   catalog={'sku':dict(category='chairs',list_price=20000),
            'alt':dict(category='chairs',list_price=18000)}
   intel.save_source('shops',{'shops':[dict(name='rival',offers=[dict(sku='alt',qty=4)])]})
   for _ in range(5):intel.decision('seller_quote',{'sku':'sku'})
   q=sale_quote(self.u,catalog,self.p,intel)
   body=json.loads(intel.db.execute("SELECT body FROM decisions WHERE role='seller_quote' ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertEqual(body['substitute_competitor_units'],4)
   self.assertEqual(body['market_adjustment'],.98)
   self.assertGreaterEqual(q['price']*.85-q['reward'],self.u['cost']+math.ceil(self.u['cost']*.09))
  finally:intel.close()

if __name__=='__main__':unittest.main()
