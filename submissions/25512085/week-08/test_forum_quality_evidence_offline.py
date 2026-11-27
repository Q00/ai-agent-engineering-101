"""Cohort proof follows accepted ledger identities, never production state."""
import copy
import unittest
from unittest.mock import patch

import forum_writer as writer
import seller_cash_policy as cash
import store

NOW = 1793800000
CATALOG = {'Q': {'kind':'goods', 'category':'goods', 'name':'상품', 'list_price':100},
           'X': {'kind':'goods', 'category':'other', 'list_price':200},
           'F': {'kind':'food', 'category':'food', 'list_price':100}}


def event(seq, order, age=7*3600, kind='settle', **data):
    return {'seq':seq, 'kind':kind, 'round':3, 'ts':NOW-age,
            'data':dict({'order':order, 'item':'Q', 'grade':'A', 'amount':80,
                         'buyer':'outside', 'shop':'remote'}, **data)}


def observe(hub, rows=(), now=NOW):
    with patch.object(store.time, 'time', return_value=now):
        store.apply_ledger_rows(hub, list(rows), CATALOG)


def prepared():
    hub = store.default_hub()
    observe(hub, [event(i, str(i)) for i in range(1, 53)] +
            [event(53, '1', age=6.5*3600, kind='broken')])
    return hub


def quality(hub, now=NOW):
    return [p for p in writer.analysis_candidates(hub, CATALOG, None, now)
            if p['kind'] == 'quality' and p['category'] == 'goods']


class QualityEvidenceTests(unittest.TestCase):
    def test_mature_matching_cohort_not_recent_refs(self):
        hub = prepared()
        # Both recent sales break, but neither sale nor break belongs to n/k.
        observe(hub, [event(54, 'young', age=360),
                      event(55, 'young', age=300, kind='broken'),
                      event(56, 'young2', age=1920)])
        hub['forum_intel']['quality_refs']['Q:A'] = [54, 56, 9999]
        hub['forum_intel']['quality_break_refs']['Q:A'] = [55, 9998]
        post, = quality(hub)
        self.assertIn('판매 52건 중 고장 1건(1.9%)', post['fallback'])
        self.assertEqual(post['refs'], [53, 1] + list(range(45, 53)))
        self.assertIn('전체 표본 중 대표 근거', post['fallback'])
        self.assertIn('전체 원장 아님', post['fallback'])
        self.assertEqual(len(post['refs']), 10)
        self.assertIsNone(writer.check_post(post['fallback']))

    def test_rejected_sales_other_grade_sku_round_and_unmatched(self):
        hub = prepared()
        rows = [event(60, 'other-sku', item='X'), event(61, 'other-grade', grade='B'),
                event(62, 'own-buyer', buyer=store.OWN_NAME),
                event(63, 'invalid-price', amount=0), event(64, 'invalid-grade', grade='Z'),
                event(65, 'wrong-round'), event(66, 'no-sale', kind='broken'),
                event(67, '1')]  # duplicate order, different seq
        rows[5]['round'] = 2
        observe(hub, rows)
        post, = quality(hub)
        self.assertTrue(set(post['refs']).isdisjoint(range(60, 68)))
        evidence = hub['break_observer']['forum_evidence']
        for order in ('own-buyer', 'invalid-price', 'invalid-grade', 'wrong-round', 'no-sale'):
            self.assertNotIn(order, evidence)

    def test_old_state_no_backfill_then_safe_new_observations(self):
        hub = prepared()
        del hub['break_observer']['forum_evidence']
        observe(hub, [event(i, str(i)) for i in range(1, 53)])
        self.assertFalse(quality(hub))
        observe(hub, [event(i, str(i)) for i in range(100, 104)] +
                [event(104, '100', age=6.5*3600, kind='broken')])
        self.assertTrue(quality(hub))
        self.assertEqual(set(quality(hub)[0]['refs']), {100, 101, 102, 103, 104})

    def test_insufficient_or_unmatched_break_proof_skips(self):
        hub = prepared()
        evidence = hub['break_observer']['forum_evidence']
        evidence['1']['break_seq'] = None
        self.assertFalse(quality(hub))
        # Positive k needs a break whose accepted sale is mature too.
        evidence['1']['break_seq'] = 53
        evidence['1']['mature'] = False
        self.assertFalse(quality(hub))
        hub = prepared()
        hub['market_break_stats']['Q']['A']['broken'] = 0
        hub['break_observer']['forum_evidence'] = {
            k:r for k,r in hub['break_observer']['forum_evidence'].items() if k in ('2', '3')}
        self.assertFalse(quality(hub))  # MIN_REFS, even though n >= 30

    def test_age_does_not_infer_actual_counter_membership(self):
        hub = prepared()
        observe(hub, [event(60, 'pending', age=60)])
        future = NOW+21600
        # No observer maturity pass yet: merely old enough is not proof.
        self.assertNotIn(60, quality(hub, future)[0]['refs'])
        observe(hub, now=future)
        self.assertIn(60, quality(hub, future)[0]['refs'])

    def test_multiple_breaks_cite_matching_sales_under_cap(self):
        hub = prepared()
        observe(hub, [event(60+i, str(i+2), age=6.5*3600, kind='broken')
                      for i in range(6)])
        post, = quality(hub)
        self.assertEqual(post['refs'], list(range(61, 66)) + list(range(3, 8)))
        self.assertEqual(len(post['refs']), 10)

    def test_own_sale_numeric_order_and_replay(self):
        hub = prepared()
        hub['own_shop_urls'] = ['own']
        observe(hub, [event(60, 123, shop='own'),
                      event(61, '123', age=6.5*3600, kind='broken')])
        evidence = hub['break_observer']['forum_evidence']['123']
        self.assertTrue(evidence['own'])
        self.assertTrue(evidence['mature'])
        self.assertEqual(evidence['break_seq'], 61)
        before = copy.deepcopy(quality(hub))
        observe(hub, [event(60, 123, shop='own'),
                      event(62, '123', age=6.5*3600, kind='broken')])
        self.assertEqual(quality(hub), before)
        self.assertTrue({60, 61}.issubset(quality(hub)[0]['refs']))

    def test_bounded_evidence_and_counter_eviction(self):
        hub = prepared()
        with patch.object(cash, 'MAX_BREAK_PENDING', 3):
            observe(hub)
        self.assertLessEqual(len(hub['break_observer']['forum_evidence']), 3)
        # Evict Q's market counter: old proof must not survive into a new cohort.
        with patch.object(cash, 'MAX_BREAK_SKUS', 1):
            observe(hub, [event(100, 'x', item='X', age=21600)])
        self.assertNotIn('Q', hub['market_break_stats'])
        self.assertFalse(any(r['sku'] == 'Q' for r in
                             hub['break_observer']['forum_evidence'].values()))

    def test_seed_has_no_proof_and_compaction_keeps_actual_membership(self):
        hub = store.default_hub()
        observe(hub)
        self.assertFalse(quality(hub))
        self.assertEqual(hub['break_observer']['forum_evidence'], {})
        hub = prepared()
        before = copy.deepcopy(quality(hub)[0])
        store.compact_hub(hub, NOW+9*3600)
        self.assertFalse(hub['break_observer']['market_orders'])
        self.assertEqual(quality(hub, NOW+9*3600)[0], before)

    def test_goods_and_food_mixed_conditions_disclosed_without_price_change(self):
        hub = store.default_hub()
        rows = [event(i, str(i), age=i, item='Q' if i < 21 else 'F')
                for i in range(1, 41)]
        observe(hub, rows)
        posts = [p for p in writer.analysis_candidates(hub, CATALOG, None, NOW)
                 if p['kind'] == 'price']
        self.assertEqual(len(posts), 2)
        for post in posts:
            self.assertIn('등급·보증·식품 잔여수명 미통제 혼합통계', post['fallback'])
            self.assertIn('동일 조건의 현재 구매 가능 가격이 아님', post['fallback'])
            self.assertIn('체결가 중앙값 80', post['fallback'])
            self.assertIsNone(writer.check_post(post['fallback']))
        with patch.object(writer, 'MAX_CHARS', 40):
            self.assertFalse(writer.analysis_candidates(hub, CATALOG, None, NOW))


if __name__ == '__main__':
    unittest.main()
