"""Exercise real dispatch with fake transport, not a stubbed decide function."""
import time
import unittest
from unittest.mock import patch

from codex_backend import ModelSession
from core import decide


class PurposeTests(unittest.TestCase):
    def test_round3_roles_reach_transport_and_validator(self):
        for purpose in ('r3-discovery', 'r3-restock', 'round3_reflection'):
            calls = []
            def transport(system, history, emit):
                calls.append(purpose)
                return '{"ok":true}', {'input_tokens': 3, 'output_tokens': 2}
            with patch('core.emit'):
                result = decide('fixture', {}, lambda x: x, purpose,
                                time.monotonic() + 30, transport=transport)
            self.assertEqual(result, {'ok': True})
            self.assertEqual(calls, [purpose])

    def test_unknown_role_still_rejected_before_transport(self):
        def forbidden(*args):
            self.fail('unknown role reached transport')
        session = ModelSession(lambda *args, **kwargs: None, transport=forbidden)
        with self.assertRaises(ValueError):
            session('', [], 'execute_arbitrary_code')
