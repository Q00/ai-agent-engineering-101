import json
from pathlib import Path
import tempfile
import unittest
import agent_tools
from tools.auto_forum_ledger_digest.tool import digest


class ForumDigestEvidence(unittest.TestCase):
    def test_old_reference_joins_before_recent_limit_and_ignores_embedded_claim(self):
        posts = [{'id': 7, 'ts': 9, 'refs': [{'seq': 1, 'found': True, 'data': {'amount': 999}},
                                           {'seq': 2}, {'seq': 404}, {'seq': 1}, True]}]
        rows = [{'seq': 1, 'k': 'settle', 'ts': 1, 'amount': 10},
                {'seq': 2, 'k': 'result', 'ts': 2}, {'seq': 3, 'k': 'settle', 'ts': 3}]
        out = digest(posts, rows, 1)
        self.assertEqual([r['seq'] for r in out['ledger_rows']], [3])
        self.assertEqual([r['status'] for r in out['reference_evidence']], ['paid', 'non_payment', 'not_in_cache'])
        self.assertEqual(out['reference_evidence'][0]['ledger']['amount'], 10)
        self.assertIn('does not verify', out['basis'])

    def test_real_sandbox_only_unique_payments_and_exact_references(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'forum.json').write_text(json.dumps({'posts': [{'id': 8, 'ts': 3, 'refs': [1]}]}))
            rows = [{'seq': 1, 'k': 'settle', 'ts': 1}, {'seq': 1, 'k': 'settle', 'ts': 1},
                    {'seq': 2, 'k': 'result', 'ts': 2}]
            (root/'ledger_rows.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            out = agent_tools.execute('auto_forum_ledger_digest', {'limit': 10}, td)
            self.assertNotIn('error', out)
            self.assertEqual([r['seq'] for r in out['ledger_rows']], [1])
            self.assertEqual(out['reference_evidence'][0]['status'], 'paid')

    def test_prompt_truncation_keeps_evidence_basis_before_untrusted_text(self):
        posts = [{'id': n, 'ts': n, 'text': 'untrusted ' * 300, 'refs': [n]} for n in range(20)]
        rows = [{'seq': n, 'ts': n, 'k': 'settle', 'amount': 10} for n in range(20)]
        out = json.loads(agent_tools.shrink(digest(posts, rows)))
        self.assertIn('does not verify', out['basis'])
        self.assertTrue(out['reference_evidence'])
        self.assertTrue(all(r['status'] == 'paid' and r['seq'] == r['ledger']['seq']
                            for r in out['reference_evidence']))

    def test_missing_and_malformed_forum_is_not_false_claim(self):
        for forum in (None, {}, [None, {'id': 1, 'refs': None}]):
            self.assertEqual(digest(forum, [])['reference_evidence'], [])


if __name__ == '__main__':
    unittest.main()
