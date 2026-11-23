import tempfile,unittest,json,time,os
from pathlib import Path
from forum_votes import tick

class ForumVoteTests(unittest.TestCase):
    def test_missing_fee_information_does_not_vote(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(tick(d)['queued'],0)
            self.assertEqual(tick(d,posts=[dict(id=1,author="other")])['queued'],0)
            self.assertFalse((Path(d)/"outbox.jsonl").exists())

    def test_paid_fee_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'inventory.json').write_text(json.dumps({'next_dislike_fee':20000}))
            self.assertEqual(tick(d)['status'],'fee_not_zero')

    def test_three_per_rolling_hour_excluding_self_and_duplicate_owners(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);now=time.time()
            posts=[dict(id=i,owner=str(i),author=str(i),role='buyer',likes=i) for i in range(1,6)]
            posts.append(dict(id=99,owner='totato2203',author='ours',role='buyer',likes=999))
            for i in range(3):
                (p/'inventory.json').write_text(json.dumps({'next_dislike_fee':0}))
                os.utime(p/'inventory.json',(now+i*2,now+i*2))
                r=tick(d,posts=posts,now=now+i*2)
                self.assertEqual(r['queued'],1);self.assertNotEqual(r['owner'],'totato2203')
                sent=json.loads((p/'outbox.jsonl').read_text());(p/'outbox.jsonl').unlink()
                with (p/'outbox.sent.jsonl').open('a') as f:f.write(json.dumps({'sent':sent,'status':200,'answer':'{"fee":0,"next_dislike_fee":0}'})+'\n')
            self.assertEqual(tick(d,posts=posts,now=now+5)['status'],'hourly_limit')

    def test_role_stagger_and_restart_persistence(self):
        from forum_schedule import due,queued
        with tempfile.TemporaryDirectory() as d:
            queued(d,'buyer',1000)
            self.assertFalse(due(d,'shop',1149))
            self.assertTrue(due(d,'shop',1150))
            queued(d,'shop',1150)
            self.assertFalse(due(d,'buyer',1299))
            self.assertTrue(due(d,'buyer',1300))
