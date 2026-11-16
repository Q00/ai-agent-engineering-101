import json,os,time,unittest
from pathlib import Path
import test_buyer_forum as fixture
import buyer_forum
import buyer_breakage

class ForumPacingTests(unittest.TestCase):
 setUp=fixture.BuyerForumTests.setUp
 model=fixture.BuyerForumTests.model
 def test_old_daily_cap_can_publish_more_verified_new_evidence(self):
  posts=[{'id':str(i),'at':self.now-1000-i*20,'status':'published','refs':[200+i],'skus':['old'+str(i)]} for i in range(96)]
  (self.root/'state/buyer-forum.json').write_text(json.dumps({'posts':posts,'last_attempt':0}))
  self.assertEqual(buyer_forum.run_once(self.root,now=self.now,model=self.model)['status'],'queued')
  self.assertGreaterEqual(buyer_forum.INTERVAL,300)
 def test_new_ack_restarts_safe_interval_even_if_old_queue_time_is_stale(self):
  posts=[{'id':'old','at':self.now-1000,'status':'queued','refs':[101],'skus':['tea']}]
  (self.root/'state/buyer-forum.json').write_text(json.dumps({'posts':posts,'last_attempt':0}))
  ack=self.root/'.market/outbox.sent.jsonl';ack.write_text(json.dumps({'sent':{'client_id':'old'},'status':200,'answer':'{"post":42}'})+'\n');os.utime(ack,(self.now,self.now))
  self.assertEqual(buyer_forum.run_once(self.root,now=self.now+100,model=self.model)['status'],'cooldown')
  self.assertEqual(buyer_forum.run_once(self.root,now=self.now+301,model=self.model)['status'],'no_evidence')
 def test_known_broken_paid_trade_is_not_promoted_as_an_ordinary_record(self):
  path=self.root/'logs/buyer-events.jsonl';sale=json.loads(path.read_text());sale['order']='o';path.write_text(json.dumps(sale)+'\n')
  proof={'seq':999,'ts':self.now-10,'kind':'broken','round':3,'data':{'buyer':buyer_breakage.OWN_NAME,'order':'o','item':'tea','unit':'u','refunded':0}}
  (self.root/'state/buyer-breakage-backfill.json').write_text(json.dumps({'rows':[proof]}))
  self.assertEqual(buyer_forum.run_once(self.root,now=self.now,model=self.model)['status'],'no_evidence')

 def test_legacy_published_record_uses_ack_anchor_before_faster_schedule(self):
  posts=[{'id':'old','at':self.now-302,'status':'published','refs':[101],'skus':['tea']}]
  (self.root/'state/buyer-forum.json').write_text(json.dumps({'posts':posts,'last_attempt':0}))
  ack=self.root/'.market/outbox.sent.jsonl';ack.write_text(json.dumps({'sent':{'client_id':'old'},'status':200,'answer':'{"post":42}'})+'\n');os.utime(ack,(self.now-1,self.now-1))
  self.assertEqual(buyer_forum.run_once(self.root,now=self.now,model=self.model)['status'],'cooldown')
