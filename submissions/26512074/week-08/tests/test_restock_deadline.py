import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from round3 import restock_hook


class RestockDeadlineTests(unittest.TestCase):
    def test_live_hook_accounts_for_time_spent_computing(self):
        with tempfile.TemporaryDirectory() as d:
            md=Path(d)
            (md/'inventory.json').write_text(json.dumps(dict(restock=dict(open=True,closes_at=190))))
            orders=[dict(sku=str(i),qty=1) for i in range(3)]
            with patch('patch36.refresh_scoreboard'),patch('high_value_growth.update'), \
                 patch('round3.procurement',return_value=dict(buy=orders)), \
                 patch('round3.time.time',side_effect=[100,160]):
                result=restock_hook(md)
            self.assertEqual(result['buy'],orders[:1])
