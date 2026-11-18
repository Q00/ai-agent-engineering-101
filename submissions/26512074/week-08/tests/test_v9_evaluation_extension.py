import importlib.util
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('v9_readonly_review',Path(__file__).resolve().parents[1]/'.market/evaluation-v9/extended_review.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class EvaluationTests(unittest.TestCase):
    def test_intention_is_not_a_receipt(self):
        q=[dict(task='a',at=10),dict(task='b',at=11)]
        t={'a':dict(first_offer=10,agreed=10),'b':dict(first_offer=5,agreed=5)}
        f=m.funnel(q,t,{'a':15},{'a'},0,20,30,dict(first=0,last=30))
        self.assertEqual(f['receipt_issued_observed'],1)
        self.assertEqual(f['agreement_intent_only'],1)
        self.assertEqual(f['quote_to_actual_settlement'],.5)

    def test_fast_accept_can_bypass_negotiation(self):
        f=m.funnel([dict(task='a',at=10)],{},dict(a=12),{'a'},0,20,30,dict(first=0,last=30))
        self.assertEqual(f['receipt_without_negotiation'],1)
        self.assertIsNone(f['negotiation_to_receipt_observed'])

    def test_missing_log_does_not_invent_a_receipt(self):
        f=m.funnel([dict(task='a',at=10)],{}, {},{'a'},0,20,30,dict(first=15,last=30))
        self.assertFalse(f['receipt_log_covers_segment'])
        self.assertEqual(f['settled_without_receipt_log'],1)

    def test_unknown_outcomes_stay_unknown(self):
        r=m.score_summary([dict(final_score=.8,final_official_reconstructable=True),dict(final_score=None)])
        self.assertIsNone(r['final_mean']);self.assertEqual(r['known_subset_mean'],.8)
        self.assertAlmostEqual(r['lower'],-.1);self.assertAlmostEqual(r['upper'],.9)

    def test_board_delta_is_actual_difference(self):
        r=m.board_delta(dict(class_shop_all=.5,seller=dict(book=100)),dict(class_shop_all=.51,seller=dict(book=160)),6)
        self.assertAlmostEqual(r['class_shop_all']['change'],.01)
        self.assertEqual(r['seller.book']['per_hour'],10)

    def test_refund_storage_expiry_and_refusal_attribution(self):
        events=[dict(kind='settle',ts=10,data=dict(shop='own',unit='u',amount=120)),dict(kind='refund',ts=25,data=dict(unit='u',amount=20)),dict(kind='storage',ts=11,data=dict(shop='own',fee=3)),dict(kind='expire',ts=12,data=dict(shop='own',cost=4))]
        audits=[dict(task='s',at=10,decision='deny',pattern='anonymous_refusal_template'),dict(task='q',at=10,decision='deny',pattern='explicit_blocked_claim')]
        r=m.segment(0,20,30,events,[],{}, {},set(),{},dict(u=dict(cost=50,supplied=-20)),[],[],audits,{}, {'own'},dict(s='sergiobuilds (anonymous refusal template)',q='q00'))
        self.assertEqual(r['seller']['partial_net_after_refund_storage_expiry'],43)
        self.assertEqual(r['refusals']['anonymous_template']['refused_tasks'],1)
        self.assertEqual(r['refusals']['explicit_identity']['refused_tasks'],0)
