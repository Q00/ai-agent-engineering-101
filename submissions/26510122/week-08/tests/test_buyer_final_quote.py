import time
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
import buyer_r3 as buyer
from buyer_policy import routing_evidence
from buyer_checkout_memory import refused_unit_ids
from buyer_memory import PreparedHistory

def task(message='',state='TASK_STATE_INPUT_REQUIRED',receipt=None):
    t={'id':'t1','status':{'state':state,'message':{'parts':[{'text':message}]}}}
    if receipt:t['artifacts']=[{'parts':[{'data':receipt}]}]
    return t

def receipt(price=200,unit='good',claim='A'):
    return {'item':'G','price':price,'task':'t1','sig_seller':'test',
            'unit':unit,'claim':claim,'cert':True}

class FinalQuoteTests(unittest.TestCase):
    def kwargs(self,memory=None):
        return dict(opening_offer=150,cap=250,list_price=500,memory=memory or {},
                    deadline=time.monotonic()+240)
    def test_explicit_final_templates_and_conflicting_prices(self):
        self.assertEqual(buyer.asking_price('Your price is 117,520.'),117520)
        self.assertEqual(buyer.asking_price('Our final price for this grade A unit is 21837.'),21837)
        self.assertEqual(buyer.asking_price('Counteroffer: 499367.'),499367)
        self.assertIsNone(buyer.asking_price('asking 200; counteroffer 300'))
        self.assertIsNone(buyer.asking_price('counteroffer 200.50'))
    def test_fourth_price_is_only_model_approved_exact_final_ask(self):
        answers=[task('asking 240'),task('asking 230'),task('asking 220'),
                 task('Our final price is 200.'),task('Done','TASK_STATE_COMPLETED',receipt())]
        def model(role,prompt,schema):
            if 'final price reviewer' in role:
                self.assertEqual(prompt['seller_final_ask'],200)
                return {'action':'accept','reason':'usable score and checkout still guarded'}
            return {'action':'offer','price':180 if prompt['last_offer']==150 else 190,'reason':'counter'}
        with patch.object(buyer,'card_endpoint',return_value='endpoint'),\
             patch.object(buyer,'send',side_effect=answers),patch.object(buyer,'decide_json',side_effect=model):
            result=buyer.negotiate('url','G','Goods',{'kind':'goods'},**self.kwargs())
        self.assertEqual(result['offers'],[150,180,190,200])
        self.assertEqual(result['receipt']['price'],200)
    def test_above_cap_bad_quality_or_little_time_never_calls_extra_model(self):
        with patch.object(buyer,'decide_json',side_effect=AssertionError('must not call')):
            for msg,deadline in [('asking 251',time.monotonic()+200),
                                 ('grade C; asking 200',time.monotonic()+200),
                                 ('asking 200',time.monotonic()+20)]:
                self.assertIsNone(buyer.final_price_review(msg,190,250,500,{}, {'kind':'goods'},deadline))
    def test_explicit_c_quote_is_corrected_before_any_price_offer(self):
        calls=[]
        answers=[task('grade C; asking 150'),task('grade A; asking 150'),
                 task('Done','TASK_STATE_COMPLETED',receipt(150))]
        def send(*args,**kwargs):
            calls.append(args[2]);return answers.pop(0)
        with patch.object(buyer,'card_endpoint',return_value='endpoint'),\
             patch.object(buyer,'send',side_effect=send):
            result=buyer.negotiate('url','G','Goods',{'kind':'goods'},**self.kwargs())
        self.assertIn('switch to a concrete eligible unit',calls[1])
        self.assertEqual(calls[2],'150')
        self.assertEqual(result['receipt']['claim'],'A')
    def test_exact_previously_refused_c_unit_cannot_be_returned_for_payment(self):
        answers=[task('asking 150'),task('Done','TASK_STATE_COMPLETED',receipt(150,'bad','A'))]
        with patch.object(buyer,'card_endpoint',return_value='endpoint'),\
             patch.object(buyer,'send',side_effect=answers):
            result=buyer.negotiate('url','G','Goods',{'kind':'goods'},
                    **self.kwargs({'known_refused_units':['bad']}))
        self.assertIsNone(result['receipt'])
    def test_kit_grade_c_failure_only_excludes_exact_unit_of_same_shop_and_attempt(self):
        now=int(time.time());at=datetime.fromtimestamp(now,timezone.utc).isoformat()
        rows=[{'event':'negotiation','at':at,'order':'o','shop':'url','shop_name':'A','sku':'G',
               'attempt_id':'a','receipt_price':200,'receipt_unit':'bad'},
              {'event':'payment','at':at,'order':'o','shop':'url','shop_name':'A','sku':'G',
               'attempt_id':'a','price':200,'paid':False,'reason':"not paying: grade C, the policy accepts ['A', 'B']"}]
        h=PreparedHistory(rows)
        self.assertEqual(refused_unit_ids({'shop':'A','url':'new','sku':'G'},{'kind':'goods'},h,now=now),['bad'])
        self.assertEqual(refused_unit_ids({'shop':'B','url':'url','sku':'G'},{'kind':'goods'},h,now=now),[])
        self.assertEqual(refused_unit_ids({'shop':'A','url':'url','sku':'G'},{'kind':'food'},h,now=now),[])
    def test_fast_failed_checkout_does_not_gain_value_from_speed(self):
        baseline=routing_evidence(None,.8)
        failed=routing_evidence({'completed_orders_6h':1,'paid_orders_6h':0,
            'unusable_at_settlement_6h':0,'smoothed_paid_probability':.5,
            'smoothed_unusable_probability':0,'observed_duration_count':1,
            'median_observed_seconds':1,'smoothed_attempt_seconds':46.2},.8)
        self.assertLess(failed['estimated_saved_per_attempt'],baseline['estimated_saved_per_attempt'])
