import unittest
from capital_policy import terminal_purchase_eligible,ROUND_END,marginal_curve


class TerminalSupplyTests(unittest.TestCase):
    def candidate(self):
        return dict(kind='goods',estimated_cost=100000,expected_sale=150000,
            actual_recovery=dict(n=4,median_hours=.05),acquisition_weights={'A':1.},
            demand_posterior={'A':dict(shape=10,exposure_hours=1)},held_by_grade={'A':0},next_supply_hours=1/3)

    def test_profitable_immediate_supply_remains_allowed_at_last_ten_minutes(self):
        v=self.candidate();now=ROUND_END-600
        item=marginal_curve(v,dict(storage_per_day=.01),now,1)[0]
        self.assertTrue(terminal_purchase_eligible(v,item,now))

    def test_same_product_is_rejected_when_existing_queue_moves_median_past_close(self):
        v=self.candidate();v['held_by_grade']={'A':30};now=ROUND_END-600
        item=marginal_curve(v,dict(storage_per_day=.01),now,1)[0]
        self.assertFalse(terminal_purchase_eligible(v,item,now))

    def test_positive_book_alone_does_not_prove_timely_cash_recovery(self):
        v=self.candidate();v['actual_recovery']={}
        self.assertFalse(terminal_purchase_eligible(v,dict(book=100,sale_probability=.99),ROUND_END-3600))

    def test_past_recovery_slower_than_remaining_time_is_not_an_endgame_opportunity(self):
        v=self.candidate();v['actual_recovery']['median_hours']=5
        self.assertFalse(terminal_purchase_eligible(v,dict(book=100,sale_probability=.99),ROUND_END-3600))

    def test_does_not_impose_new_amount_or_cash_ceiling(self):
        v=self.candidate();v.update(estimated_cost=1000000000,expected_sale=1500000000)
        self.assertTrue(terminal_purchase_eligible(v,dict(book=100000000,sale_probability=.9),ROUND_END-3600))

    def test_preserves_normal_policy_outside_final_three_hours(self):
        self.assertTrue(terminal_purchase_eligible({}, {},ROUND_END-4*3600))


class OwnershipTests(unittest.TestCase):
    def test_rotated_owned_url_is_added_without_forgetting_old_contracts_or_trusting_other_owner(self):
        import tempfile,json
        from pathlib import Path
        from demand_strategy import sync_owned_shop_urls
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'shop-identity.json';p.write_text(json.dumps(dict(urls=['https://old'],keep='original')))
            board=dict(shops=[dict(owner=dict(login='totato2203'),card=dict(url='https://new')),
                dict(owner=dict(login='other'),card=dict(url='https://other'))])
            sync_owned_shop_urls(directory,board,100)
            updated=json.loads(p.read_text())
            self.assertEqual(updated['urls'],['https://new','https://old']);self.assertEqual(updated['keep'],'original')
