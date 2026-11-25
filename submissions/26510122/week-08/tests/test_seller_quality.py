import json
from pathlib import Path
import tempfile
import unittest
from seller_quality import quality, description


class QualityTests(unittest.TestCase):
    def test_fixed_goods_and_food_boundaries(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'catalog.json').write_text(json.dumps({'food':{'shelf_hours':100}}))
            for grade in ('A','B','C'):
                self.assertEqual(quality({'kind':'goods','grade':grade},d,1000)['claim'],grade)
            unit={'sku':'food','kind':'food','grade':'A','expires_at':1000+50*3600}
            self.assertEqual(quality(unit,d,1000)['claim'],'B')  # Recompute stale grade.
            self.assertIn('grade B',description(unit,quality(unit,d,1000)))
            for hours in (80.01,45.01,.1):
                unit['expires_at']=1000+hours*3600
                self.assertIsNone(quality(unit,d,1000)['claim'])
            unit['expires_at']=1000+44*3600
            self.assertEqual(quality(unit,d,1000)['claim'],'C')
            self.assertIsNone(quality(unit,Path(d)/'missing',1000)['claim'])
