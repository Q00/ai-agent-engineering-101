"""Regression: the monitor may not use counterpart URLs found in trading logs."""
import importlib.util,json,tempfile,unittest
from pathlib import Path
SCRIPT=Path('C:/Users/qkrgk/AppData/Local/hermes/scripts/course_market_health_20m.py')
spec=importlib.util.spec_from_file_location('health',SCRIPT)
health=importlib.util.module_from_spec(spec);spec.loader.exec_module(health)
class HealthUrlTests(unittest.TestCase):
    def test_only_own_running_status_url_is_used(self):
        self.assertTrue(hasattr(health, 'own_public_url'), 'Monitor still selects URLs from counterparty-contaminated log tails')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'status.json'
            path.write_text(json.dumps({'state':'running','tunnel_url':'https://our-shop.trycloudflare.com'}))
            self.assertEqual(health.own_public_url(path),'https://our-shop.trycloudflare.com')
            path.write_text(json.dumps({'state':'stopped','tunnel_url':'https://old-shop.trycloudflare.com'}))
            self.assertIsNone(health.own_public_url(path))
            path.write_text('{}');self.assertIsNone(health.own_public_url(path))
            path.unlink();self.assertIsNone(health.own_public_url(path))
