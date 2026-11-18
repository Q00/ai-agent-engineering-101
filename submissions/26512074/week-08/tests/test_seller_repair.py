import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,AsyncMock
import seller_r3

class SellerRepairTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_sku_never_substituted_and_lifetime_checked(self):
        with tempfile.TemporaryDirectory() as d:
            md=Path(d)
            base={'name':'Food','category':'food','cost':10,'list_price':20,'grade':'B','kind':'food','supplied_at':1}
            units=[dict(base,sku='G11111111',unit='a',expires_at=1100),
                   dict(base,sku='G11111111',unit='b',expires_at=100000),
                   dict(base,sku='G22222222',unit='c',expires_at=1200)]
            (md/'inventory.json').write_text(json.dumps({'round':3,'units':units}))
            with patch.object(seller_r3,'MARKET_DIR',md),patch.object(seller_r3.time,'time',return_value=1000):
                self.assertIsNone(seller_r3.find_unit('I want food G33333333'))
                self.assertEqual(seller_r3.find_unit('food G11111111 must last at least 12 hours')['unit'],'b')
                self.assertEqual(seller_r3.find_unit('food G22222222')['unit'],'c')
    def test_abandoned_reservations_expire(self):
        seller=seller_r3.Seller()
        seller.open={'old':{'seen_at':1},'live':{'seen_at':999}}
        seller.promised={'old','live'}
        seller.promised_at={'old':1,'live':999}
        seller.release_stale(1000)
        self.assertEqual(set(seller.open),{'live'})
        self.assertEqual(seller.promised,{'live'})
    async def test_acceptance_parses_without_semantic_quota(self):
        with patch.object(seller_r3,'read_price',new=AsyncMock(side_effect=AssertionError('unexpected model parse'))):
            self.assertEqual(await seller_r3.offer_price('I accept your offer',80),80)
            self.assertEqual(await seller_r3.offer_price('I accept at 75',80),75)

if __name__=='__main__':
    unittest.main()
