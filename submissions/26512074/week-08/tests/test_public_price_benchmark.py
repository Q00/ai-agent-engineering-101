import json
import tempfile
import time
import unittest
from pathlib import Path
from patch36 import comparison_price


class PublicPriceBenchmarkTests(unittest.TestCase):
    def test_public_paid_price_is_shrunk_not_assumed_guaranteed(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'patch36-market.json').write_text(json.dumps(dict(at=time.time(),public_prices={'s|x|A|6':dict(n=1,median=60)})))
            self.assertEqual(comparison_price('s','x','A',90,6,d),80)
            self.assertEqual(comparison_price('s','x','A',90,0,d),90)
            self.assertEqual(comparison_price('s','x','B',90,6,d),90)

    def test_own_verified_history_remains_preferred(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'patch36-market.json').write_text(json.dumps(dict(at=time.time(),prices={'s|x|A':dict(n=2,median=70)},public_prices={'s|x|A|6':dict(n=20,median=50)})))
            self.assertEqual(comparison_price('s','x','A',90,6,d),70)

    def test_benchmark_never_increases_a_better_actual_quote(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'patch36-market.json').write_text(json.dumps(dict(at=time.time(),public_prices={'s|x|A|6':dict(n=10,median=100)})))
            self.assertEqual(comparison_price('s','x','A',80,6,d),80)
