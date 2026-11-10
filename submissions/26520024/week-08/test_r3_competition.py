"""Offline public-evidence, pricing and outreach regression cases."""
import copy
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from r3_common import atomic_json
from r3_competition import capture, build_report, fresh_report, enrich, ceiling, promotion
from r3_memory import Memory
from r3_observe import refresh_public

NOW = 1000000
CAT = {'S': dict(name='fixture',category='goods',kind='goods',list_price=200,shelf_hours=None)}


def board():
    shops = [dict(name=n,owner={'login':owner},card={'url':'https://'+n+'.example'},
                  profit=cash,book=book,deals=100,house=False,alive=True)
             for n,owner,cash,book in [('own','me',20,20),('alpha','a',100,200),
                                      ('beta','b',90,1000),('gamma','c',80,2000)]]
    return dict(round=3,shops=shops,buyers=[{'name':n,'owner':{'login':owner}}
                                          for n,owner in [('buyer1','x'),('buyer2','y'),('own','me')]])


def sales():
    return [dict(kind='market_settle',key=str(i),ts=NOW-100+i,data=dict(source='public_ledger',
        seq=i,order='o'+str(i),unit='u'+str(i),buyer='buyer1' if i<=3 else 'buyer2',
        shop='https://alpha.example',item='S',amount=100,grade='A',claim_honest=True,warranty_hours=0))
        for i in range(1,7)]


def report(events=None):
    current=capture(board(),[], 'own', NOW)
    return build_report(current,[],sales() if events is None else events,CAT,NOW)


class CompetitionTests(unittest.TestCase):
    def test_rank_cash_not_revenue_or_book_and_exclude_self(self):
        b=board()
        b['shops'].append(dict(b['shops'][0],name='own-alias',profit=10000))
        b['shops'].append(dict(b['shops'][1],name='house',house=True,profit=100000))
        s=capture(b,[], 'own', NOW)
        self.assertEqual([r['name'] for r in s['leaders']],['alpha','beta'])

    def test_repetition_is_not_asserted_partnership(self):
        r=report()
        self.assertEqual(len(r['relationships']),2)
        self.assertEqual(r['relationships'][0]['count'],3)
        self.assertIn('not_proof_of_partnership',r['relationships'][0]['evidence'])
        self.assertEqual(r['opportunities'][0]['distinct_buyers'],2)

    def test_duplicates_old_self_deals_and_forum_are_not_evidence(self):
        rows=sales()
        rows.append(copy.deepcopy(rows[0]))
        rows.append(dict(rows[0],kind='forum_claim'))
        rows.append(dict(rows[0],ts=NOW-99999))
        self.assertEqual(report(rows)['coverage']['sample_count'],6)
        b=board()
        b['buyers'][0]['owner']['login']='a'
        r=build_report(capture(b,[],'own',NOW),[],sales(),CAT,NOW)
        self.assertEqual(r['coverage']['sample_count'],3)

    def test_ambiguous_url_does_not_create_relationship(self):
        current=capture(board(),[],'own',NOW)
        old=copy.deepcopy(current)
        old['identities'][1]['owner']='different'
        r=build_report(current,[old],sales(),CAT,NOW)
        self.assertEqual(r['coverage']['sample_count'],0)
        self.assertEqual(r['coverage']['unresolved_urls'],6)

    def test_public_shop_directory_links_rotated_url_with_same_identity(self):
        current=capture(board(),[{'name':'alpha','url':'https://rotated.example'}],'own',NOW)
        rows=sales()
        for row in rows: row['data']['shop']='https://rotated.example'
        r=build_report(current,[],rows,CAT,NOW)
        self.assertEqual(r['coverage']['sample_count'],6)
        self.assertEqual(r['relationships'][0]['shop'],'alpha')

    def test_warranty_or_false_claim_cannot_set_price(self):
        rows=sales()
        for r in rows: r['data']['warranty_hours']=72
        self.assertEqual(report(rows)['opportunities'][0]['grade_price'],{})
        for r in rows:
            r['data']['warranty_hours']=0
            r['data']['claim_honest']=False
        self.assertEqual(report(rows)['opportunities'][0]['grade_price'],{})

    def test_new_customer_arrival_requires_prior_repeated_rival_trades(self):
        rows=sales()
        rows.append(dict(kind='market_settle',key='8',ts=NOW-1,data=dict(rows[0]['data'],
            seq=8,order='o8',unit='u8',shop='https://own.example')))
        r=report(rows)
        self.assertEqual(r['leader_then_our_shop'][0]['own_settlements'],1)
        self.assertIn('not a partnership',r['limits'][0])

    def test_same_grade_price_cut_preserves_bounds(self):
        bounds=dict(minimum=80,ceiling=102,liquidation=False)
        out=ceiling({'sku':'S','cost':80},bounds,report(),NOW,'A')
        self.assertEqual(out['ceiling'],98)
        self.assertEqual(out['minimum'],80)
        self.assertEqual(ceiling({'sku':'S','cost':99},bounds,report(),NOW,'A'),bounds)
        self.assertEqual(ceiling({'sku':'S','cost':80},bounds,report(),NOW,'B'),bounds)
        self.assertEqual(ceiling({'sku':'S','cost':80},dict(bounds,liquidation=True),report(),NOW,'A')['ceiling'],102)

    def test_boost_requires_diversity_positive_margin_and_stays_bounded(self):
        candidate=dict(sku='S',score=10,portfolio_score=10,portfolio={'estimated_margin':1})
        inp={'candidates':[candidate]}
        enrich(inp,report())
        self.assertEqual(candidate['portfolio_score'],12)
        candidate['portfolio_score']=10
        candidate['portfolio']['estimated_margin']=-1
        enrich(inp,report())
        self.assertEqual(candidate['portfolio_score'],10)

    def test_expired_or_disabled_report_cannot_affect_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            md=Path(folder)
            atomic_json(md/'competition-latest.json',report())
            self.assertTrue(fresh_report(md,NOW))
            self.assertFalse(fresh_report(md,NOW+1801))
            atomic_json(md/'policy.json',{'competitive_tracking_enabled':False})
            self.assertFalse(fresh_report(md,NOW))

    def test_backfill_cursor_is_not_fast_forwarded_by_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            md=Path(folder)
            atomic_json(md/'public-cursor.json',{'after':10,'polled_at':0})
            def get(path):
                if path=='/api/shops': return {'shops':[]}
                if path=='/api/board?round=3': return {}
                seq=10000 if path.endswith('=-500') else 11
                return [dict(seq=seq,ts=NOW-1,round=3,kind='settle',data={'item':'S','amount':100})]
            with patch.dict(os.environ,{'R3_ENABLE_LIVE':'1'}):
                self.assertEqual(refresh_public(md,get=get,now=NOW)['after'],11)
            with Memory(md) as memory:
                self.assertTrue(memory.get('market_settle',10000))
                self.assertTrue(memory.get('market_settle',11))

    def test_promotion_requires_unreserved_real_stock_and_hour_cooldown(self):
        with tempfile.TemporaryDirectory() as folder:
            md=Path(folder)
            atomic_json(md/'catalog.json',CAT)
            atomic_json(md/'competition-latest.json',report())
            u=dict(unit='real',sku='S',name='fixture',category='goods',list_price=200,kind='goods',
                   cost=70,grade='A',supplied_at=NOW-100,expires_at=None,cert={'sig':'offline'})
            atomic_json(md/'inventory.json',dict(round=3,balance=10000,units=[u]))
            os.utime(md/'inventory.json',(NOW,NOW))
            with Memory(md) as memory:
                self.assertEqual(promotion(memory,md,NOW),[])
                with sqlite3.connect(memory.dbdir/'r3_seller.sqlite3') as db:
                    db.execute('CREATE TABLE deals(unit TEXT)')
                    db.execute("INSERT INTO deals VALUES ('real')")
                self.assertEqual(promotion(memory,md,NOW),[])
                with sqlite3.connect(memory.dbdir/'r3_seller.sqlite3') as db:
                    db.execute('DELETE FROM deals')
                ads=promotion(memory,md,NOW)
                self.assertEqual(len(ads),1)
                self.assertEqual(ads[0]['as'],'shop')
                self.assertIn('not endorsements',ads[0]['post'])
                memory.record('forum_queued',ads[0]['id'],ads[0],ts=NOW)
                self.assertEqual(promotion(memory,md,NOW+1),[])


if __name__=='__main__':
    unittest.main(verbosity=2)
