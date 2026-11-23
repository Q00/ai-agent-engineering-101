import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from round3 import wait_supply_window,restock_hook


class SupplyWindowEntryTests(unittest.TestCase):
    def test_waits_for_imminent_window_and_uses_fresh_balance(self):
        with tempfile.TemporaryDirectory() as d:
            md=Path(d);now=[100.];initial=dict(balance=100,restock=dict(open=False,opens_at=105))
            (md/'inventory.json').write_text(json.dumps(initial))
            def sleep(seconds):
                now[0]+=seconds
                if now[0]>=105:(md/'inventory.json').write_text(json.dumps(dict(balance=200,restock=dict(open=True,opens_at=105))))
            fresh=wait_supply_window(md,initial,lambda:now[0],sleep)
            self.assertTrue(fresh['restock']['open']);self.assertEqual(fresh['balance'],200)

    def test_distant_window_does_not_sleep(self):
        inv=dict(restock=dict(open=False,opens_at=1000))
        with patch('round3.time.sleep') as sleep:self.assertIs(wait_supply_window('.',inv,lambda:100),inv);sleep.assert_not_called()

    def test_active_hook_does_not_replay_ledger_or_request_scoreboard(self):
        with tempfile.TemporaryDirectory() as d:
            md=Path(d);(md/'inventory.json').write_text(json.dumps({'restock':{'open':False}}))
            with patch('strategy_policy.active',return_value=True),patch('patch36.refresh_scoreboard') as refresh,patch('ledger_window.window') as ledger,patch('round3.procurement',return_value={'buy':[]}):
                self.assertEqual(restock_hook(md,now=100),{'buy':[]});refresh.assert_not_called();ledger.assert_not_called()
