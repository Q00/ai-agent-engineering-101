"""TEST ONLY: selector receives explicit registry identities; URLs cannot become IDs."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import market_buyer
from harness import system_prompt
from test_seller_cancel import RecordingTrace


class SelectorIdentityTests(unittest.TestCase):
    def test_validated_selection_uses_registry_name_and_rejects_url_and_price(self):
        candidate={'name':'test-only-registry','url':'https://test-only.invalid',
                   'card':{'name':'Different TEST ONLY display name'},'reviews':[],
                   'personal_memory':{},'card_flags':[], 'score':5}
        memory=Mock();memory.ingest_kit_feedback.return_value=[];memory.read.return_value={}
        def select(context, **kwargs):
            self.assertEqual(context['selection_ids'],['test-only-registry'])
            validate=kwargs['validator']
            valid={'action':'select_shop','item_id':'test-only-registry','price':None,'offer':None,'reason':'TEST ONLY'}
            for invalid in ({**valid,'item_id':candidate['url']},
                            {**valid,'item_id':candidate['card']['name']},
                            {**valid,'price':100}):
                with self.assertRaises(ValueError):validate(invalid)
            return validate(valid)
        model=Mock();model.decide.side_effect=select
        with patch.object(market_buyer,'rank_shops',return_value=[candidate]), \
                patch.object(market_buyer,'check_card',return_value=candidate), \
                patch.object(market_buyer,'Harness',return_value=model), \
                patch.object(market_buyer,'negotiate',return_value={'status':'no_deal','receipt':None}) as negotiate:
            market_buyer.buy([candidate],'lamp',120,memory,RecordingTrace())
        self.assertEqual(negotiate.call_args.args[0],candidate['url'])
        self.assertIn('registry shop names, NOT URLs',system_prompt('shop_selector'))

    def test_context_retains_older_receipt_issues_and_item_experience_without_mutating_memory(self):
        from copy import deepcopy
        history=[{'time':str(i),'item':'chair','status':'completed','price':180,'reason':'TEST ONLY '*100,'issues':[]}
                 for i in range(30)]
        history[0].update(item='lamp',status='disputed',issues=['receipt_price_mismatch'],reason='TEST ONLY receipt mismatch')
        history[1].update(item='lamp')
        shop={'name':'TEST ONLY','personal_memory':{'trades':900,'completed':890,'signature_verified_by_kit':880,'recent':history}}
        original=deepcopy(shop)
        view=market_buyer.selection_view(shop,'lamp')
        self.assertEqual(shop,original)
        memory=view['personal_memory']
        self.assertEqual(memory['trades'],900)
        self.assertEqual(memory['signature_verified_by_kit'],880)
        self.assertLessEqual(len(memory['recent']),7)
        self.assertEqual({r['time'] for r in memory['recent']},{'0','1','27','28','29'})
        self.assertEqual(memory['recent'][0]['issues'],['receipt_price_mismatch'])
        self.assertNotIn('reason',memory['recent'][-1])
