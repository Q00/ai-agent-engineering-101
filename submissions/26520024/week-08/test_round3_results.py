"""Synthetic analysis regressions; no model, market calls, or real credentials."""
import json
from pathlib import Path
import tempfile
import unittest

from summarize_round3 import CUTOFF, baseline, class_result, merge_events, reason_category, summarize


def event(kind, key, data, stamp=CUTOFF-100, source='K'):
    return dict(kind=kind, key=key, data=data, ts=stamp, source=source)


class ResultAnalysisTests(unittest.TestCase):
    def test_original_record_replaces_projection_and_same_order_is_counted_once(self):
        partial=event('payment','o',{'paid':True,'recovered_from':'backup'},source='J')
        original=event('payment','o',{'paid':True,'ledger':1})
        merged,duplicates,changed=merge_events([('J',[partial]),('K',[original])])
        self.assertEqual(len(merged),1)
        self.assertEqual(merged[0]['source'],'K')
        self.assertEqual(duplicates,{'payment':1})
        self.assertEqual(changed,{'payment':1})

    def test_paid_result_is_not_inferred_from_a_signed_receipt(self):
        events=[event('negotiation','pending',{'outcome':'receipt_pending'}),
                event('negotiation','paid',{'outcome':'receipt_pending'}),
                event('payment','paid',{'paid':True}),
                event('negotiation','rejected',{'outcome':'receipt_pending'}),
                event('payment','rejected',{'paid':False,'reason':'unit is not in stock'}),
                event('policy_fallback','f',{'reason':'circuit_open'})]
        result,audit=summarize(events)
        self.assertEqual(result['local_paid_purchases'],1)
        self.assertEqual(result['buyer_outcomes_by_unique_order'],
                         {'receipt_without_payment_evidence':1,'paid':1,'payment_rejected':1})
        self.assertEqual(result['model_fallback_diagnostics_not_refusals'],{'circuit_open':1})
        self.assertEqual(len(audit),3)

    def test_close_time_is_exclusive_and_late_settlement_is_not_counted(self):
        events=[event('payment','before',{'paid':True},CUTOFF-.1),
                event('payment','at-close',{'paid':True},CUTOFF),
                event('payment','after',{'paid':True},CUTOFF+1)]
        result,_=summarize(events)
        self.assertEqual(result['local_paid_purchases'],1)

    def test_positive_server_settlement_is_counted_when_local_acknowledgment_failed(self):
        rows=[event('negotiation','o',{'outcome':'receipt_pending'}),
              event('payment','o',{'paid':False,'reason':'timeout'})]
        public=[{'seq':123,'ts':CUTOFF-10,'source':'J','data':{'order':'o'}}]
        result,_=summarize(rows,public_settlements=public)
        self.assertEqual(result['local_paid_purchases'],0)
        self.assertEqual(result['confirmed_purchase_ledgers_union'],1)
        self.assertEqual(result['buyer_outcomes_by_unique_order'],{'server_settled_unconfirmed_locally':1})
        self.assertEqual(result['buyer_nonpaid_reasons_by_unique_order'],{})
        self.assertEqual(result['public_settlements_without_local_paid_record'][0]['seq'],123)

    def test_local_policy_and_remote_faults_are_distinct(self):
        self.assertEqual(reason_category('walk'),'own_policy_walk')
        self.assertEqual(reason_category('Price violates the local inventory risk policy'),'local_price_floor_guard')
        self.assertEqual(reason_category('RuntimeError: A2A database is locked'),'remote_database_locked')
        self.assertEqual(reason_category('ReadTimeout: timed out'),'timeout')

    def test_class_rank_uses_student_total_not_everyone_total(self):
        rows=[dict(player='outside',student=False,total=2),
              dict(player='LeeUichann',student=True,total=1.8,student_total=.626,
                   student_shop=.189,student_buyer=.437,student_pct=.133),
              dict(player='higher-class',student=True,total=.5,student_total=1)]
        result=class_result(rows)
        self.assertEqual((result['rank'],result['cohort_size']),(2,2))
        self.assertEqual(result['percentile'],.133)

    def test_rate_projection_cannot_be_reported_as_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary)/'watch.json'
            p.write_text(json.dumps({'score_evaluation':{'next_prediction':{'method':'recent_metric_rates'}}}))
            with self.assertRaises(ValueError):baseline(p,[])

    def test_cold_start_baseline_is_checked_against_original_snapshot(self):
        own={'shop':{'deals':221},'buyer':{'deals':1286}}
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary)/'watch.json'
            p.write_text(json.dumps({'score_evaluation':{'next_prediction':{
                'method':'cold_start_persistence','made_at':1000,'projected_own':own}}}))
            result=baseline(p,[event('score_snapshot','original',{'own':own},stamp=999)])
            self.assertEqual(result['source'],'original_K_score_snapshot')
            self.assertFalse(result['class_rank_recorded'])
