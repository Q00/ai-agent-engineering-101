"""Offline tests for experiment design and preservation of failures."""
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import suite_study as suite


class SuiteTests(unittest.TestCase):
    def test_schedule_has_exactly_three_per_case_condition_and_rotates_order(self):
        schedule = suite.schedule('test-suite')
        self.assertEqual(len(schedule), 45)
        self.assertEqual(len({item['run_id'] for item in schedule}), 45)
        self.assertEqual(set(Counter((item['case_id'], item['condition']) for item in schedule).values()), {3})
        for case in suite.catalog():
            slots = [item['condition'] for item in schedule if item['case_id'] == case['id']]
            self.assertEqual(slots, ['baseline', 'homogeneous', 'overconfident', 'homogeneous', 'overconfident', 'baseline', 'overconfident', 'baseline', 'homogeneous'])
        self.assertEqual(sum(len(suite.load_case(i['case_id'])[1]) for i in schedule), 486)

    def test_command_passes_selected_case_condition_and_run_id(self):
        item = suite.schedule('test-suite')[4]
        cmd = suite.command(item)
        for flag, key in (('--case', 'case_id'), ('--condition', 'condition'), ('--run-id', 'run_id')):
            self.assertEqual(cmd[cmd.index(flag) + 1], item[key])

    def test_timeout_and_missing_results_remain_failures_in_denominator(self):
        item = dict(suite.schedule('test-suite')[3], timed_out=True, exit_code=-15, wall_seconds=600)
        with tempfile.TemporaryDirectory() as tmp, patch.object(suite, 'ROOT', Path(tmp)):
            metric = suite.inspect_run(item)
        self.assertEqual(metric['status'], 'timeout')
        self.assertFalse(metric['passed'])
        self.assertEqual(metric['checks_passed'], 0)
        self.assertEqual(metric['checks_total'], 10)
        self.assertFalse(metric['request_checks_passed'])
        group = suite.group_metrics([metric])
        self.assertEqual((group['attempts'], group['passed'], group['checks_total']), (1, 0, 10))

    def test_truncated_trace_is_reported_without_replacing_original(self):
        item = dict(suite.schedule('test-suite')[0], timed_out=True, exit_code=-15, wall_seconds=600)
        with tempfile.TemporaryDirectory() as tmp, patch.object(suite, 'ROOT', Path(tmp)):
            log = Path(tmp) / 'logs' / (item['run_id'] + '.jsonl')
            log.parent.mkdir()
            log.write_text('{"event":')
            metric = suite.inspect_run(item)
            self.assertEqual(log.read_text(), '{"event":')
        self.assertFalse(metric['trace']['passed'])
        self.assertEqual(metric['verification_errors'][0]['kind'], 'trace_decode')

    def test_exclusive_evidence_writes_never_replace_existing_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'attempt.json'
            suite.save(path, {'status': 'failed'})
            with self.assertRaises(FileExistsError):
                suite.save(path, {'status': 'succeeded'})
            self.assertEqual(json.loads(path.read_text()), {'status': 'failed'})

    def test_malformed_model_json_is_response_failure_not_request_failure(self):
        item = dict(suite.schedule('test-suite')[0], timed_out=False, exit_code=1, wall_seconds=1)
        config = suite.load_config()[0]
        state = dict(config, case_id=item['case_id'], condition=item['condition'],
                     team_roster=suite.team_roster(item['condition']), limits=config)
        request = {key: config['transport'][key] for key in ('model', 'temperature', 'max_tokens', 'reasoning', 'provider')
                   if key in config['transport']}
        request.update(messages=suite.messages('A', 'execute', {}, item['condition']),
                       response_format=suite.response_format('execute', {}))
        tags = dict(task_id=item['case_id'], contractor='A', phase='execute', attempt=1)
        rows = [dict(event='run_start', settings=state),
                dict(event='http_request', payload=request, **tags),
                dict(event='http_response', raw_response=json.dumps({'choices': [{'message': {'content': '{"summary":'}}]}), **tags),
                dict(event='run_end', result={'evaluation': {}})]
        with tempfile.TemporaryDirectory() as tmp, patch.object(suite, 'ROOT', Path(tmp)):
            log = Path(tmp) / 'logs' / (item['run_id'] + '.jsonl')
            log.parent.mkdir()
            log.write_text('\n'.join(json.dumps(dict(row, seq=i, at='2026-09-21T00:00:00+00:00')) for i, row in enumerate(rows, 1)))
            metric = suite.inspect_run(item)
        self.assertTrue(metric['request_checks_passed'])
        self.assertFalse(metric['response_checks_passed'])
        self.assertEqual(metric['verification_errors'][0]['kind'], 'response_decode')


if __name__ == '__main__':
    unittest.main()
