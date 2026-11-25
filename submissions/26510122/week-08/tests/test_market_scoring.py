import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

import market_scoring as scoring
import market_buyer_r3 as buyer
from capital_policy import ROUND_END,operating_policy
from newsvendor_tool import quote_candidates,recommend_quantity,poisson_pmf


class BookMathTests(unittest.TestCase):
    def test_procurement_is_book_neutral_but_holding_and_spoilage_are_losses(self):
        before={'balance':10_000_000,'start':5_000_000,'injected':0,'units':[]}
        bought={**before,'balance':8_000_000,'units':[{'cost':2_000_000,'kind':'goods','grade':'A'}]}
        self.assertEqual(scoring.book_metrics(before,100)['book'],5_000_000)
        self.assertEqual(scoring.book_metrics(bought,100)['book'],5_000_000)
        held={**bought,'balance':7_980_000}
        self.assertEqual(scoring.book_metrics(held,100)['book'],4_980_000)
        spoiled={**bought,'units':[{'cost':2_000_000,'kind':'food','grade':'expired','expires_at':50}]}
        self.assertEqual(scoring.book_metrics(spoiled,100)['book'],3_000_000)

    def test_bankruptcy_injections_are_not_earnings(self):
        inv={'balance':9_000_000,'start':5_000_000,'injected':4_700_000,'units':[{'cost':1_000_000}]}
        self.assertEqual(scoring.book_metrics(inv,100)['book'],300_000)

    def test_terminal_credit_respects_actual_food_survival(self):
        self.assertEqual(scoring.terminal_credit({'kind':'goods','cost':100},1000),100)
        self.assertEqual(scoring.terminal_credit({'kind':'food','cost':100,'expires_at':999},1000),0)
        self.assertEqual(scoring.terminal_credit({'kind':'food','cost':100,'expires_at':1001},1000),100)
        self.assertEqual(scoring.terminal_credit({'kind':'food','cost':100},1000),0)

    def test_goods_continuation_keeps_book_credit_and_charges_future_holding(self):
        far=scoring.continuation({'kind':'goods'},100,0,24,240)
        self.assertEqual(far['value'],91)
        near=scoring.continuation({'kind':'goods'},100,0,1,1)
        self.assertEqual(near['value'],100)
        self.assertEqual(scoring.continuation({'kind':'food'},100,0,1,1)['value'],0)
        old=recommend_quantity(poisson_pmf(.2),110,100,max_qty=2)
        new=recommend_quantity(poisson_pmf(.2),110,100,salvage=near['value'],max_qty=2)
        self.assertEqual(old['quantity'],0);self.assertGreater(new['quantity'],0)
        self.assertEqual(recommend_quantity(poisson_pmf(0),110,100,salvage=100,max_qty=2)['quantity'],0)

    def test_quote_compares_sale_with_retained_book_not_forced_liquidation(self):
        unit={'sku':'G','unit':'u','cost':100,'list_price':200,'kind':'goods','grade':'A'}
        q=quote_candidates(unit,now=ROUND_END-86400)
        self.assertEqual(q['book']['credit_if_unsold_at_round_end'],100)
        self.assertEqual(q['book']['book_change_if_held_to_end'],-1)
        self.assertEqual(operating_policy(ROUND_END-1)['score_weights']['book'],.5)


class SavedPolicyTests(unittest.TestCase):
    def test_normalized_saving_beats_absolute_money_saved(self):
        rich=scoring.buyer_candidate(1_000_000,700_000,cost_reference={"unit_cost":500_000})
        cheap=scoring.buyer_candidate(10_000,6_000,cost_reference={"unit_cost":5_000})
        self.assertGreater(cheap['saved_if_usable_at_cap'],rich['saved_if_usable_at_cap'])
        self.assertEqual(cheap['virtual_zero_deals'],100)
        self.assertIn('NOT a seller quote',cheap['assumption'])

    def test_equal_saving_rates_do_not_force_expensive_skus_to_front(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path.cwd();os.chdir(tmp)
            try:
                catalog={'cheap':{'kind':'goods','list_price':1000,'name':'Cheap'},
                         'rich':{'kind':'goods','list_price':1_000_000,'name':'Rich'}}
                order={'kind':'goods','budget':1_000_000,'acceptable':list(catalog)}
                shops=[{'name':'other','url':'https://other','offers':[{'sku':s,'qty':1} for s in catalog]}]
                found=buyer.candidates(order,shops,catalog,'own')
                self.assertEqual([c['sku'] for c in found],['cheap','rich'])
                self.assertEqual(found[0]['score_evidence']['saved_if_usable_at_cap'],round(.25/.41,6))
                self.assertTrue(found[0]['score_evidence']['cost_reference']['estimated'])
                with patch.object(buyer,'decide_json',return_value={'candidate_id':'0','opening_offer':600,'reason':'usable rate'} ) as model:
                    chosen,price=buyer.choose(found,order)
                self.assertEqual(chosen['sku'],'cheap');self.assertLessEqual(price,chosen['cap'])
                prompt=model.call_args.args[1]
                self.assertEqual(prompt['score_contract']['weights']['saved'],.5)
                self.assertEqual(prompt['score_contract']['scales']['forum'],'raw_rating_0_to_1')
                self.assertEqual(prompt['score_contract']['scales']['saved'],'percentile')
                self.assertEqual(prompt['score_contract']['ranking_version'],'round3-patch3.6')
                self.assertIn('paid votes reduce shop cash and Book',prompt['score_contract']['all_definition'])
                self.assertIn('not absolute currency surplus',prompt['objective'])
            finally:os.chdir(old)

class MarginSavedTests(unittest.TestCase):
    def test_equal_margin_points_are_equal_across_food_and_furniture(self):
        self.assertEqual(scoring.saved_at_price(100000,76000,52000),.5)
        self.assertEqual(scoring.saved_at_price(100000,80500,61000),.5)
        self.assertEqual(scoring.saved_at_price(100000,40000,52000),1)
        self.assertIsNone(scoring.saved_at_price(100000,70000))
        for cost in (float('nan'),100000,110000,-1,True):
            self.assertIsNone(scoring.saved_at_price(100000,70000,cost))

    def test_same_discount_is_more_margin_saved_on_high_cost_product(self):
        food=scoring.saved_at_price(100000,70000,52000)
        furniture=scoring.saved_at_price(100000,70000,61000)
        self.assertGreater(furniture,food)
        self.assertEqual(scoring.buyer_cost_reference(
            {'kind':'goods','category':'가구/침구>침구','list_price':100000})['cost_share'],.61)

    def test_candidates_compare_margin_and_keep_estimate_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path.cwd();os.chdir(tmp)
            try:
                catalog={sku:{'kind':'goods','list_price':100000,'category':category}
                         for sku,category in [('other','주방용품'),('furniture','가구/침구>침대')]}
                order={'kind':'goods','budget':100000,'acceptable':list(catalog)}
                shops=[{'name':'other-shop','url':'https://other','offers':[
                    {'sku':sku,'qty':1} for sku in catalog]}]
                with patch.object(buyer,'read_events',return_value=[]):
                    found=buyer.candidates(order,shops,catalog,'own',
                                          saved_baseline={'saved':.6823,'observed_at':1000})
                self.assertEqual(found[0]['sku'],'furniture')
                reference=found[0]['price_guidance']['saved_reference']
                self.assertEqual(reference['max_price_to_improve_if_usable'],73390)
                self.assertTrue(reference['cost_reference']['estimated'])
            finally:os.chdir(old)
