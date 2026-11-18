import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import harness
import optimizer
import policy
from credit_audit import audit


class CreditControlTests(unittest.TestCase):
    def test_audit_deduplicates_calls_and_preserves_unknown_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'logs').mkdir()
            rows=[{'event':'harness_start','call_id':'a','role':'semantic_price_only'},
                  {'event':'call_started','call_id':'a','role':'semantic_price_only'}]
            (root/'logs/a.log').write_text('\n'.join(json.dumps(r) for r in rows+rows))
            report=audit(root)
            self.assertEqual(report['unique_call_attempts'],1)
            self.assertEqual(report['attempts_without_usage'],1)
            self.assertIsNone(report['reported_tokens'])
            self.assertIsNone(report['actual_credits'])

    def test_environment_cannot_override_persistent_deny(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'credit-policy.json').write_text(json.dumps({'automated_model_calls_enabled': False}))
            with patch.object(optimizer, 'ROOT', root), patch.dict('os.environ', {'ROUND3_ALLOW_MODEL_CALLS': '1'}), patch.object(harness.asyncio, 'create_subprocess_exec', new_callable=AsyncMock) as spawn:
                with self.assertRaisesRegex(RuntimeError, 'credit-policy'):
                    asyncio.run(harness.decide('semantic_price_only', {}, {}))
                spawn.assert_not_called()
            record=json.loads((root/'.market/optimization/model-usage.jsonl').read_text())
            self.assertEqual(record['event'], 'call_blocked')
            self.assertEqual(record['calls'], 0)

    def test_price_parser_does_not_call_model_even_if_environment_enables_it(self):
        with patch.dict('os.environ', {'ROUND3_SEMANTIC_MODEL': '1'}), patch.object(policy, 'decide', new_callable=AsyncMock) as model:
            self.assertIsNone(asyncio.run(policy.read_price('No clear price; warranty 24 hours', role='ask')))
            self.assertEqual(asyncio.run(policy.read_price('Current asking price: 339048', role='ask')), 339048)
            self.assertEqual(asyncio.run(policy.read_price('12345', role='offer')), 12345)
            model.assert_not_called()
