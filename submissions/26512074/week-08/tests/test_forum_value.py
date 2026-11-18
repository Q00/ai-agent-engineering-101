import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from forum_value import build,measure


class ForumValueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.md=Path(self.temp.name);self.now=10000
        (self.md/'catalog.json').write_text(json.dumps({'x':dict(name='X',list_price=100)}))
        (self.md/'shops.json').write_text(json.dumps([dict(name='seller',url='other')]))
        with closing(sqlite3.connect(self.md/'strategy.sqlite3')) as c:
            c.execute('create table market_events(seq integer primary key,ts real,kind text,payload text)')
            for seq,price,grade,wh,buyer,shop in [(1,60,'A',6,'totato2203-r3','other'),(2,70,'A',6,'b','other'),(3,80,'A',6,'b','other'),(4,90,'A',6,'b','other'),(5,1,'C',0,'b','other'),(6,65,'A',6,'b','totato2203-r3')]:
                e=dict(seq=seq,ts=self.now-seq,round=3,kind='settle',data=dict(sku='x',amount=price,grade=grade,warranty_hours=wh,buyer=buyer,shop=shop,claim_honest=True))
                c.execute('insert into market_events values(?,?,?,?)',(seq,e['ts'],'settle',json.dumps(e)))
            c.commit()

    def test_comparison_matches_grade_and_warranty_and_uses_real_refs(self):
        p=build(self.md,'buyer',self.now)
        self.assertEqual(p['refs'][0],1);self.assertNotIn(5,p['refs'])
        self.assertIn('중앙값 75',p['post']);self.assertLess(len(p['post']),1000)
        self.assertNotIn('좋아요',p['post']);self.assertIn('#1',p['post'])

    def test_both_roles_use_their_actual_settlement(self):
        self.assertEqual(build(self.md,'buyer',self.now)['_seq'],1)
        self.assertEqual(build(self.md,'shop',self.now)['_seq'],6)

    def test_used_transaction_is_not_republished(self):
        (self.md/'forum-value-state.json').write_text(json.dumps({'buyer':dict(used=[1],count=1)}))
        self.assertIsNone(build(self.md,'buyer',self.now))

    def test_early_votes_do_not_choose_a_format_champion(self):
        posts=[dict(id=2,ts=self.now-600,author='totato2203-r3',role='buyer',text='[실제 구매 기록]',likes=8,dislikes=0)]
        (self.md/'forum.json').write_text(json.dumps(posts))
        self.assertEqual(measure(self.md,self.now)['formats'],{})

    def test_feedback_updates_post_instead_of_double_counting_votes(self):
        posts=[dict(id=1,ts=self.now-1800,author='totato2203-r3',role='buyer',text='[실제 구매 기록]',likes=3,dislikes=0)]
        (self.md/'forum.json').write_text(json.dumps(posts));measure(self.md,self.now)
        posts[0]['likes']=5;(self.md/'forum.json').write_text(json.dumps(posts));s=measure(self.md,self.now+60)
        self.assertEqual(s['formats']['buyer:purchase']['posts'],1)
        self.assertEqual(s['formats']['buyer:purchase']['mean_likes'],5)
