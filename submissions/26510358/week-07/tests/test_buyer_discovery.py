"""TEST ONLY discovery candidates; exploration keeps exclusions and money guards."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from buyer_discovery import discovery_pool
from market_memory import MarketMemory
import market_buyer
from test_seller_cancel import RecordingTrace

RULE=json.loads((Path(__file__).resolve().parents[1]/'data/growth-policy.json').read_text())


def shop(name,score=5,trades=0,stars=5,issues=None):
    return {'name':name,'score':score,'url':'https://'+name+'.invalid','card':{'name':'TEST ONLY'},'card_flags':[],
            'personal_memory':{'trades':trades,'recent':[{'issues':issues or []}]},
            'reviews':[{'stars':stars,'review':'TEST ONLY','round':2} for _ in range(3)]}


class BuyerDiscoveryTests(unittest.TestCase):
    def test_every_fifth_search_adds_less_visited_reliable_alternatives(self):
        ranked=[shop('TEST_PRIMARY',6,100),shop('TEST_KNOWN',5.7,20),shop('TEST_NEW',4.8),
                shop('TEST_FRAUD',5.5,issues=['receipt_price_mismatch']),shop('TEST_BAD',5,stars=1)]
        original=deepcopy(ranked)
        normal,preferred=discovery_pool(ranked,4,RULE)
        self.assertEqual(normal,ranked);self.assertEqual(preferred,[])
        pool,preferred=discovery_pool(ranked,5,RULE)
        self.assertEqual(preferred,['TEST_NEW','TEST_KNOWN'])
        self.assertEqual(pool[0]['name'],'TEST_PRIMARY')
        self.assertEqual(ranked,original)
        self.assertNotIn('TEST_FRAUD',preferred);self.assertNotIn('TEST_BAD',preferred)

    def test_sparse_reviews_need_personally_verified_receipts_and_score_guard_stays(self):
        primary=shop('TEST_PRIMARY',6,100)
        sparse=shop('TEST_SPARSE',5);sparse['reviews']=[]
        distant=shop('TEST_DISTANT',3)
        self.assertEqual(discovery_pool([primary,sparse,distant],5,RULE)[1],[])
        sparse['personal_memory']['signature_verified_by_kit']=2
        self.assertEqual(discovery_pool([primary,sparse,distant],5,RULE)[1],['TEST_SPARSE'])

    def test_search_counter_persists_without_inventing_a_trade(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.json'
            self.assertEqual(MarketMemory(path).begin_search(),1)
            self.assertEqual(MarketMemory(path).begin_search(),2)
            self.assertEqual(MarketMemory(path).read()['shops'],{})

    def test_exploratory_purchase_uses_original_budget_and_harness_selection(self):
        primary,alternative=shop('TEST_PRIMARY',6,100),shop('TEST_ALTERNATIVE',5)
        with tempfile.TemporaryDirectory() as directory:
            memory=MarketMemory(Path(directory)/'memory.json')
            with memory.transaction() as state:state['purchase_searches']=4
            def choose(context,**kwargs):
                self.assertEqual(context['budget'],120)
                self.assertEqual(context['exploration']['preferred_registry_names'],['TEST_ALTERNATIVE'])
                return kwargs['validator']({'action':'select_shop','item_id':'TEST_ALTERNATIVE','price':None,'offer':None,'reason':'TEST ONLY direct experience diversification'})
            model=Mock();model.decide.side_effect=choose
            with patch.object(market_buyer,'rank_shops',return_value=[primary,alternative]), \
                    patch.object(memory,'ingest_kit_feedback',return_value=[]), \
                    patch.object(market_buyer,'check_card',side_effect=lambda s:s), \
                    patch.object(market_buyer,'Harness',return_value=model), \
                    patch.object(market_buyer,'negotiate',return_value={'status':'no_deal','receipt':None}) as trade:
                market_buyer.buy([primary,alternative],'lamp',120,memory,RecordingTrace())
            trade.assert_called_once()
            self.assertEqual(trade.call_args.args,('https://TEST_ALTERNATIVE.invalid','lamp',120.0))
            self.assertEqual(memory.read()['purchase_searches'],5)
