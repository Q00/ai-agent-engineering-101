"""Read-only ledger contract tests with an injected local response."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r3_memory import Memory
from r3_observe import refresh_public


class ObserverContractTests(unittest.TestCase):
    def test_paid_amount_and_identity_are_retained_without_invention(self):
        with tempfile.TemporaryDirectory() as folder:
            md = Path(folder)
            data = dict(item='S1', amount=120, order='o1', shop='our-shop',
                        buyer='other-buyer', unit='u1', grade='B', claim_honest=True)
            def get(path):
                if path == '/api/shops':
                    return []
                return [dict(seq=10, ts=1000, kind='settle', data=data)]
            with patch.dict(os.environ, {'R3_ENABLE_LIVE': '1'}):
                self.assertEqual(refresh_public(md, get=get, now=1001)['added'], 1)
            with Memory(md) as memory:
                stored = memory.get('market_settle', '10')['data']
                for key, value in data.items():
                    self.assertEqual(stored[key], value)
                self.assertNotIn('price', stored)
                self.assertNotIn('task', stored)
                self.assertEqual(stored['source'], 'public_ledger')


if __name__ == '__main__':
    unittest.main(verbosity=2)

