"""Public price previews must match enforcement and advertise supported facts only."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import load_catalog
from pricing import opening_ask
from shopfront import public_items, public_description, public_skills
from test_seller_cancel_http import actual_sdk_server
import httpx

ROOT=Path(__file__).resolve().parents[1]


class ShopfrontTests(unittest.TestCase):
    def quotes(self, phase="normal"):
        policy=json.loads((ROOT/'data/price-policy.json').read_text())
        return {'phase':phase, **policy}

    def test_public_quotes_share_seller_caps_and_do_not_expose_reserves(self):
        catalog=load_catalog();policy=self.quotes('acquisition')
        offers=public_items(catalog,policy)
        self.assertEqual([x['price'] for x in offers],[100,180,260])
        self.assertTrue(all(x['price']==opening_ask(next(i for i in catalog if i['id']==x['id']),policy) for x in offers))
        self.assertNotIn('reserve',json.dumps(offers))
        description=public_description(offers)
        self.assertIn('lamp 100, chair 180, bookshelf 260',description[:150])
        self.assertIn('about 40 books',json.dumps(public_skills(offers)))
        for unsupported in ('handmade','2-year warranty','1+1','five-star','guaranteed lowest'):
            self.assertNotIn(unsupported,description)

    def test_exact_owner_profile_has_no_inactive_promotion(self):
        description=public_description(public_items(load_catalog(), self.quotes()))
        self.assertEqual(description, "Ssadassa Shop: lamp 100, chair 180, bookshelf 260 credits each. Clear quotes, prompt acceptance and Ed25519-signed receipts matching the agreed item and price. Request a current quote, then offer that total on the same task. (Catalog search and price negotiation)")
        self.assertNotIn('REVIEW10', description)
        self.assertLessEqual(len(description), 300)

    def test_actual_sdk_discovery_updates_prices_without_model_or_network(self):
        with actual_sdk_server(public_profile=True) as state:
            first=httpx.get(state.url+'/.well-known/agent-card.json').json()
            self.assertIn('lamp 1100',first['description'])  # TEST ONLY policy has a 1000-credit opening margin.
            self.assertEqual(first['supportedInterfaces'][0]['protocolVersion'],'1.0')
            self.assertEqual(len(state.model_contexts),0)
            previous=state.executor.pricing.quote_policy
            state.executor.pricing.quote_policy=lambda:{**previous(),'opening_margin':{'lamp':0}}
            second=httpx.get(state.url+'/.well-known/agent-card.json').json()
            self.assertIn('lamp 100',second['description'])
            self.assertEqual(second['skills'][0]['id'],'negotiate')
            self.assertEqual(len(state.model_contexts),0)
