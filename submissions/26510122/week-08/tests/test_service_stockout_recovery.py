import unittest
from stock_policy import replenishment_candidates

class ServiceRecoveryTests(unittest.TestCase):
    now=1794800000
    product={'name':'Cover','kind':'goods','category':'home','list_price':50000}
    def rows(self,sku='G'):
        return [{'event':'receipt_signed','sku':sku,'unit':'u','cost':15000,'supplied_at':self.now-1000,'warranty_hours':6,'at':self.now-500},
                {'event':'settled','sku':sku,'unit':'u','ledger':1,'price':40000,'at':self.now-400},
                {'event':'settled','sku':sku,'unit':'old','ledger':2,'price':40000,'at':self.now-13*3600}]+[
                {'event':'no_stock','task_id':'task'+str(i),'requested_skus':[sku],'at':self.now-100+i} for i in range(2)]
    def inventory(self):return {'units':[],'balance':10000000,'restock':{'open':False,'opens_at':self.now+600}}
    def test_repeat_sku_with_one_recent_profit_and_lost_requests_gets_one_model_option(self):
        rows=replenishment_candidates({'G':self.product},self.inventory(),self.rows(),self.now)
        self.assertEqual(len(rows),1);r=rows[0];self.assertTrue(r['service_stockout_recovery'])
        self.assertEqual([o['qty'] for o in r['target_options']],[1]);self.assertGreater(r['projected_service_contribution_per_unit'],0)
    def test_duplicate_or_stale_requests_do_not_activate_recovery(self):
        rows=self.rows();rows[-1]['task_id']=rows[-2]['task_id']
        self.assertFalse(replenishment_candidates({'G':self.product},self.inventory(),rows,self.now))
        rows=self.rows()
        for e in rows[-2:]:e['at']=self.now-4*3600
        self.assertFalse(replenishment_candidates({'G':self.product},self.inventory(),rows,self.now))
    def test_pending_or_physical_stock_and_unprofitable_current_supply_block_recovery(self):
        self.assertFalse(replenishment_candidates({'G':self.product},self.inventory(),self.rows(),self.now,pending={'G':1}))
        inv=self.inventory();inv['units']=[{'sku':'G','grade':'A','cost':15000,'category':'home'}]
        self.assertFalse(replenishment_candidates({'G':self.product},inv,self.rows(),self.now))
        inv=self.inventory();inv['surge']=[{'sku':'G','factor':4}]
        self.assertFalse(replenishment_candidates({'G':self.product},inv,self.rows(),self.now))
    def test_no_receipts_no_cost_proof_and_no_unbounded_recovery_batch(self):
        rows=[e for e in self.rows() if e['event']!='receipt_signed']
        self.assertFalse(replenishment_candidates({'G':self.product},self.inventory(),rows,self.now))
        catalog={f'G{i}':self.product for i in range(4)};history=[]
        for i,sku in enumerate(catalog):
            for e in self.rows(sku):
                e=dict(e)
                if 'ledger' in e:e['ledger']+=10*i
                if 'unit' in e:e['unit']+=str(i)
                if 'task_id' in e:e['task_id']+=str(i)
                history.append(e)
        results=replenishment_candidates(catalog,self.inventory(),history,self.now)
        self.assertEqual(len(results),2);self.assertTrue(all(r['target_options'][0]['qty']==1 for r in results))

    def test_final_three_hours_do_not_open_this_recovery_lane(self):
        from capital_policy import ROUND_END
        now=ROUND_END-3600;delta=now-self.now
        rows=self.rows()
        for e in rows:e['at']+=delta
        for e in rows:
            if 'supplied_at' in e:e['supplied_at']+=delta
        self.assertFalse(replenishment_candidates({'G':self.product},self.inventory(),rows,now))

    def test_model_approval_cannot_buy_when_supply_cost_surges_before_execution(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import inventory_portfolio as portfolio
        from capital_policy import operating_policy
        now=self.now;inv=self.inventory();inv['round']=3;inv['restock']={'open':True,'opens_at':now-5,'closes_at':now+175};inv['surge']=[{'sku':'G','factor':4}]
        desired=[{'sku':'G','qty':1,'target_stock':1,'kind':'replenishment','category':'home','cost_ceiling':56250,
                  'service_recovery':{'median_paid':40000,'lost_tasks_3h':2}}]
        with tempfile.TemporaryDirectory() as directory,patch.object(portfolio,'record'):
            state={'version':1,'windows':{},'intents':[]}
            result=portfolio._emit_approved(state,Path(directory)/'state.json',desired,{'reason':'approved'},inv['restock'],str(now),
                operating_policy(now),now,10,1000000,lambda:(inv,now,True),{'G':self.product})
            self.assertEqual(result['buy'],[]);self.assertFalse(state['intents'])
