"""Public price previews must match enforcement and advertise supported facts only."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import load_catalog
from rank_pricing import opening_ask
from referral_pricing import review_notice
from shopfront import public_items, public_description
from test_seller_cancel_http import actual_sdk_server
import httpx

ROOT=Path(__file__).resolve().parents[1]


class ShopfrontTests(unittest.TestCase):
    def quotes(self,phase):
        policy=json.loads((ROOT/'data/price-policy.json').read_text())
        return {'phase':phase,**policy['rank_cycle']['phases'][phase],
                'default_margin_rate':policy['default_margin_rate'],'review_referral':policy['review_referral']}

    def test_public_quotes_share_seller_caps_and_do_not_expose_reserves(self):
        catalog=load_catalog();policy=self.quotes('acquisition')
        offers=public_items(catalog,policy)
        self.assertEqual([x['price'] for x in offers],[100,180,260])
        self.assertTrue(all(x['price']==opening_ask(next(i for i in catalog if i['id']==x['id']),policy) for x in offers))
        self.assertNotIn('reserve',json.dumps(offers))
        description=public_description(offers)
        self.assertIn('lamp 100, chair 180, bookshelf 260',description[:150])
        self.assertIn('about 40 books',description)
        for unsupported in ('handmade','2-year warranty','1+1','five-star','guaranteed lowest'):
            self.assertNotIn(unsupported,description)

    def test_coupon_advertising_matches_actual_phase_and_full_ten_percent(self):
        rule=self.quotes('acquisition')['review_referral']
        acquisition=public_items(load_catalog(),self.quotes('acquisition'))
        self.assertNotIn('REVIEW10',review_notice(rule,offers=acquisition))
        recovery=public_items(load_catalog(),self.quotes('recovery'))
        self.assertEqual([x['discount']['price'] for x in recovery],[108,180,261])
        self.assertIn('REVIEW10',review_notice(rule,offers=recovery))
        self.assertLess(len(review_notice(rule,offers=acquisition)),200)
        self.assertLess(len(review_notice(rule,offers=recovery)),350)

    def test_actual_sdk_discovery_updates_prices_without_model_or_network(self):
        with actual_sdk_server(public_profile=True) as state:
            first=httpx.get(state.url+'/.well-known/agent-card.json').json()
            self.assertIn('lamp 1100',first['description'])  # TEST ONLY policy has a 1000-credit opening margin.
            self.assertEqual(first['supportedInterfaces'][0]['protocolVersion'],'1.0')
            self.assertEqual(len(state.model_contexts),0)
            previous=state.executor.rank_pricing.quote_policy
            state.executor.rank_pricing.quote_policy=lambda:{**previous(),'opening_margin':{'lamp':0}}
            second=httpx.get(state.url+'/.well-known/agent-card.json').json()
            self.assertIn('lamp 100',second['description'])
            self.assertEqual(second['skills'][0]['id'],'buy-lamp')
            self.assertEqual(len(state.model_contexts),0)
