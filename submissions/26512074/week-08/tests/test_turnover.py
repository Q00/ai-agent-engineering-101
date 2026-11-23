import tempfile,unittest
from pathlib import Path
from optimizer import choose_quote,negotiate
from turnover import age_terms,alternative,promotion_score


class TurnoverTests(unittest.TestCase):
    def test_old_stock_new_quote_reduces_price_but_existing_quote_is_stable(self):
        now=100000;fresh=dict(unit='f',sku='x',kind='goods',grade='A',cost=40,list_price=100,supplied_at=now)
        old=dict(fresh,unit='o',supplied_at=now-24*3600)
        with tempfile.TemporaryDirectory() as directory:
            inv=dict(units=[fresh,old]);base=dict(ask=100,floor=41,market_reference=100)
            a=choose_quote(directory,'fresh',fresh,inv,base,now)
            b=choose_quote(directory,'old',old,inv,base,now)
            self.assertLess(b['ask'],a['ask'])
            self.assertEqual(choose_quote(directory,'old',old,inv,base,now+10000)['ask'],b['ask'])

    def test_improving_offer_near_ask_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            u=dict(sku='x',kind='goods',grade='A',cost=100000,list_price=300000)
            move=negotiate(directory,'t',dict(unit=u,ask=200000,offer=195000,previous_offer=170000,lines=['buyer:170000','buyer:195000']),dict(floor=101000,ask=200000),1000)
            self.assertEqual(move['action'],'accept')

    def test_alternative_excludes_wrong_category_and_short_life(self):
        u=dict(sku='x',category='sofa',list_price=1000)
        units=[dict(sku='bad',category='sofa',list_price=100,expires_at=1001),dict(sku='wrong',category='tv',list_price=100),dict(sku='good',category='sofa',list_price=700)]
        self.assertEqual(alternative(u,dict(units=units),set(),1000)['sku'],'good')

    def test_promotion_considers_demand_and_age(self):
        fresh=dict(cost=100,supplied_at=100000);old=dict(cost=100,supplied_at=100000-8*3600)
        self.assertGreater(promotion_score(old,200,10,100000),promotion_score(fresh,200,10,100000))
        self.assertGreater(promotion_score(fresh,200,10,100000),promotion_score(fresh,200,0,100000))
