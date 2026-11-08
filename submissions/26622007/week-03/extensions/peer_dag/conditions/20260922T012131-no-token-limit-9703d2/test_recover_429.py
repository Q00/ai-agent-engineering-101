"""Offline recovery selection regression against actual exhausted-429 traces."""
import json
import unittest
from recover_429 import B, selected_failure


class SelectionTests(unittest.TestCase):
    def test_all_six_recorded_provider_failures_are_selected(self):
        selected = []
        for case in ('growth-roadmap', 'launch-operations'):
            for condition in ('baseline', 'homogeneous', 'overconfident'):
                path = B / f'{B.name}-r1-{case}-{condition}.metrics.json'
                if selected_failure(json.loads(path.read_text())):
                    selected.append((case, condition))
        self.assertEqual(len(selected), 6, selected)

    def test_content_failure_and_mixed_rejections_are_not_retried(self):
        for error, rejections in [('ValueError: no valid bids', [{'error': 'HTTP 429'}, {'error': 'bad JSON'}]),
                                  ('ValueError: no valid bids', []),
                                  ('wrong facts', []), ('timeout', [])]:
            with self.subTest(error=error, rejections=rejections):
                self.assertFalse(selected_failure({'passed': False, 'error': error, 'proposal_rejections': rejections}))

    def test_success_is_never_retried(self):
        self.assertFalse(selected_failure({'passed': True, 'error': 'CallError: HTTP 429'}))

    def test_nested_actual_rate_failure_is_selected(self):
        metric = json.loads((B / f'{B.name}-r3-payment-redesign-overconfident.metrics.json').read_text())
        self.assertTrue(selected_failure(metric))

    def test_nested_mixed_cause_or_empty_cause_is_not_selected(self):
        error = 'ValueError: child failed or blocked; partial results retained'
        for failures in ([], [{'error': error}], [{'error': error}, {'error': 'CallError: HTTP 429'}, {'error': 'bad JSON'}]):
            self.assertFalse(selected_failure({'passed': False, 'error': error, 'task_failures': failures}))


if __name__ == '__main__':
    unittest.main()
