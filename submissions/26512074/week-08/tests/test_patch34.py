import unittest
from buyer_r3 import candidates
from profit_strategy import surge_factor


class Patch34Tests(unittest.TestCase):
    def test_equal_discount_has_equal_score_across_price_bands(self):
        shops=[dict(url='a',offers=[dict(sku='small',qty=1)]),dict(url='b',offers=[dict(sku='large',qty=1)])]
        cat={'small':dict(list_price=100),'large':dict(list_price=1000000)}
        rows=candidates(shops,dict(acceptable=list(cat),budget=2000000),cat)
        self.assertAlmostEqual(rows[0][0],rows[1][0])

    def test_surge_counts_our_batch_and_caps_at_fifty_percent(self):
        self.assertAlmostEqual(surge_factor({'surge':[dict(sku='x',units=49,factor=1)]},'x',10),1.018)
        self.assertEqual(surge_factor({'surge':[dict(sku='x',units=400,factor=1.5)]},'x'),1.5)
        self.assertEqual(surge_factor({'surge':[]},'x'),1)

    def test_large_observations_do_not_block_publication(self):
        from recommendations import render_slate
        evidence={'leaders':[],'repeat_pairs':[],
                  'products':[dict(shop='shop'+str(i)+'x'*80,sku='G12345678',count=100,revenue=100000,low=900,high=1100,refs=[]) for i in range(5)]}
        exclusions=[dict(shop='seller'+str(i),reason='Expensive quote '+('detail '*100),url='http://seller',refs=[]) for i in range(3)]
        result=render_slate([],{},dict(units=[]),'market',exclusions=exclusions,evidence=evidence)
        self.assertLessEqual(len(result['post']),2000)
        self.assertIn('tomato&potato',result['post'])
