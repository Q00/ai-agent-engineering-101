import unittest
from tools.supply_audit.tool import audit

class SupplyAuditTest(unittest.TestCase):
    def test_identity_and_non_sale_exits_and_future_data(self):
        windows=[{'opens_at':10,'closes_at':20,'ts':21},{'opens_at':30,'closes_at':40,'ts':41},
                 {'opens_at':50,'closes_at':60,'ts':61}]
        incoming=[{'unit':u,'sku':'G','ts':15,'supplied_at':15} for u in ['sold','left','expired']]
        outgoing=[{'unit':'sold','ts':35,'how':'sold'}, {'unit':'expired','ts':35,'how':'expired'},
                  {'unit':'left','ts':45,'how':'sold'}]
        r=audit(windows,incoming,outgoing,42,'G')
        self.assertEqual(r['windows'],[10,30]);self.assertEqual(r['sold_fraction'],1/3)
        self.assertEqual(r['remaining_units'],['left']);self.assertEqual(r['other_exits'],['expired'])
    def test_missing_is_not_zero(self):
        self.assertFalse(audit([],None,[],100,'G')['available'])
        self.assertFalse(audit([],[],[],100,'G')['available'])
    def test_first_tick_is_not_a_purchase(self):
        w=[{'opens_at':1,'closes_at':3},{'opens_at':4,'closes_at':6}]
        r=audit(w,[{'unit':'old','sku':'G','ts':2,'first_tick':True}],[],7,'G')
        self.assertIsNone(r['sold_fraction'])
