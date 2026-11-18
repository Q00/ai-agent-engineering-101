import tempfile,json,unittest
from pathlib import Path
from profit_strategy import replenish


class PublicSupplyCostTests(unittest.TestCase):
    def test_lower_price_market_demand_allows_multiple_trial_units(self):
        with tempfile.TemporaryDirectory() as directory:
            events=[dict(seq=i,kind='settle',round=3,ts=10000,data=dict(sku='x',shop=f's{i%3}',buyer=f'b{i%6}',amount=35000)) for i in range(30)]
            events.extend(dict(seq=100+i,kind='supply',round=3,ts=9500,data=dict(sku='x',shop=f's{i%2}',total=25000,qty=1)) for i in range(5))
            plan=replenish(dict(balance=6000000,units=[],restock=dict(open=True)),{'x':dict(kind='goods',list_price=50000)},events,10001,directory)
            self.assertEqual(plan['buy'][0]['sku'],'x')
            self.assertGreater(plan['buy'][0]['qty'],6)
            self.assertLessEqual(plan['buy'][0]['qty'],20)

    def test_new_premium_sku_enters_on_public_demand_without_own_sales(self):
        with tempfile.TemporaryDirectory() as directory:
            events=self.fixture(2)
            for event in events:
                if event['kind']=='settle':event['data']['amount']=1400000
                else:event['data']['total']=1000000
            plan=replenish(dict(balance=20000000,units=[],restock=dict(open=True)),
                           {'x':dict(kind='goods',list_price=2000000)},events,10001,directory)
            self.assertEqual(plan['buy'][0]['sku'],'x')
            self.assertGreater(plan['buy'][0]['qty'],2)
            self.assertLessEqual(plan['buy'][0]['qty'],20)

    def test_existing_premium_stock_does_not_block_demand_based_restock(self):
        with tempfile.TemporaryDirectory() as directory:
            events=self.fixture(2)
            for event in events:
                if event['kind']=='settle':event['data']['amount']=1400000
                else:event['data']['total']=1000000
            unit=dict(unit='held',sku='x',kind='goods',cost=1000000,supplied_at=9900)
            plan=replenish(dict(balance=20000000,units=[unit],restock=dict(open=True)),
                           {'x':dict(kind='goods',list_price=2000000)},events,10001,directory)
            self.assertEqual(plan['buy'][0]['sku'],'x')
            self.assertGreater(plan['buy'][0]['qty'],1)
            self.assertLessEqual(plan['buy'][0]['qty'],20)

    def fixture(self,suppliers):
        events=[dict(seq=i,kind='settle',round=3,ts=10000,data=dict(sku='x',shop=f's{i%3}',buyer=f'b{i%4}',amount=140000)) for i in range(10)]
        events.extend(dict(seq=100+i,kind='supply',round=3,ts=9500,data=dict(sku='x',shop=f's{i%suppliers}',total=100000,qty=1,units=[str(i)])) for i in range(5))
        return events

    def test_new_sku_uses_observed_supply_cost_but_budgets_worst_case(self):
        with tempfile.TemporaryDirectory() as directory:
            result=replenish(dict(balance=6000000,units=[],restock=dict(open=True)),{'x':dict(kind='goods',list_price=200000)},self.fixture(2),10001,directory)
            self.assertEqual(result['buy'][0]['sku'],'x')
            self.assertGreater(result['buy'][0]['qty'],2)
            self.assertLessEqual(result['buy'][0]['qty'],20)
            candidate=json.loads((Path(directory)/'procurement-preview.json').read_text())['candidates'][0]
            self.assertEqual(candidate['estimated_cost'],100000)
            self.assertEqual(candidate['worst_cost'],150000)

    def test_sparse_history_is_usable_with_uncertainty_buffer(self):
        with tempfile.TemporaryDirectory() as directory:
            result=replenish(dict(balance=6000000,units=[],restock=dict(open=True)),{'x':dict(kind='goods',list_price=200000)},self.fixture(1),10001,directory)
            self.assertEqual(result['buy'][0]['sku'],'x')
            self.assertGreater(result['buy'][0]['qty'],2)
            self.assertLessEqual(result['buy'][0]['qty'],20)
            candidate=json.loads((Path(directory)/'procurement-preview.json').read_text())['candidates'][0]
            self.assertAlmostEqual(candidate['estimated_cost'],110000)

    def test_recent_supply_prices_replace_old_expensive_stock_costs(self):
        with tempfile.TemporaryDirectory() as directory:
            unit=dict(unit='old',sku='x',kind='goods',cost=190000,supplied_at=9900)
            result=replenish(dict(balance=6000000,units=[unit],restock=dict(open=True)),
                             {'x':dict(kind='goods',list_price=200000)},self.fixture(2),10001,directory)
            self.assertEqual(result['buy'][0]['sku'],'x')
            self.assertGreater(result['buy'][0]['qty'],1)
            self.assertLessEqual(result['buy'][0]['qty'],20)
            candidate=json.loads((Path(directory)/'procurement-preview.json').read_text())['candidates'][0]
            self.assertEqual(candidate['estimated_cost'],100000)
            self.assertEqual(candidate['cost_source'],'public_supply_batch_cost_q65')

    def test_no_source_data_retains_conservative_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            events=[e for e in self.fixture(1) if e['kind']=='settle']
            result=replenish(dict(balance=6000000,units=[],restock=dict(open=True)),{'x':dict(kind='goods',list_price=200000)},events,10001,directory)
            self.assertEqual(result['buy'],[])

    def test_closing_window_does_not_enqueue_orders_that_will_retry_after_close(self):
        with tempfile.TemporaryDirectory() as directory:
            result=replenish(dict(balance=6000000,units=[],restock=dict(open=True,closes_at=10020)),
                             {'x':dict(kind='goods',list_price=200000)},self.fixture(2),10001,directory)
            self.assertEqual(result['buy'],[])
            preview=json.loads((Path(directory)/'procurement-preview.json').read_text())
            self.assertEqual(preview['limits']['window_order_slots'],0)
