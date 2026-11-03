"""Offline transport configuration checks; no real CLI, model, auth, or network."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import codex_backend as backend


class ModelConfigurationTests(unittest.TestCase):
    def test_week08_uses_luna_low(self):
        self.assertEqual(backend.MODEL, 'gpt-5.6-luna')
        self.assertEqual(backend.REASONING, 'low')

    def test_invoke_builds_luna_command_with_existing_safety_guards(self):
        reply = '{"action":"walk","price":null,"summary":"Offline fixture."}'
        usage = dict(input_tokens=12, output_tokens=5)
        events = '\n'.join(json.dumps(event) for event in (
            dict(type='item.completed', item=dict(type='agent_message', text=reply)),
            dict(type='turn.completed', usage=usage),
        ))
        calls, emitted = [], []

        def fake_run(argv, **kwargs):
            calls.append((list(argv), kwargs))
            self.assertEqual(argv[:2], ['/offline/codex', 'exec'])
            output = Path(argv[argv.index('--output-last-message') + 1])
            working = Path(argv[argv.index('--cd') + 1])
            self.assertEqual(output.parent, working)
            self.assertTrue(working.is_dir())
            output.write_text(reply, encoding='utf-8')
            return subprocess.CompletedProcess(argv, 0, stdout=events, stderr='')

        with patch.object(backend, 'executable', return_value='/offline/codex'), \
                patch.object(backend.subprocess, 'run', side_effect=fake_run) as run:
            actual, actual_usage = backend.invoke('Offline system', [{'role': 'user', 'content': 'fixture'}],
                lambda event, **data: emitted.append((event, data)), timeout=7)

        self.assertEqual(actual, reply)
        self.assertEqual(actual_usage, usage)
        self.assertEqual(run.call_count, 1)
        argv, kwargs = calls[0]
        self.assertEqual(argv[argv.index('--model') + 1], 'gpt-5.6-luna')
        self.assertEqual(argv[argv.index('--sandbox') + 1], 'read-only')
        for flag in ('--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '--json'):
            self.assertIn(flag, argv)
        configs = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == '-c']
        self.assertIn('model_reasoning_effort="low"', configs)
        self.assertIn('web_search="disabled"', configs)
        disabled = {argv[i + 1] for i, value in enumerate(argv[:-1]) if value == '--disable'}
        self.assertEqual(disabled, set(backend.DISABLED))
        self.assertTrue({'shell_tool', 'unified_exec', 'multi_agent', 'apps', 'plugins', 'hooks',
                         'browser_use', 'computer_use', 'image_generation', 'view_image', 'sleep_tool'} <= disabled)
        self.assertEqual(argv[-1], '-')
        self.assertEqual(kwargs['timeout'], 7)
        self.assertTrue(kwargs['text'])
        self.assertTrue(kwargs['capture_output'])
        self.assertEqual(json.loads(kwargs['input'])['system'], 'Offline system')
        self.assertEqual(next(data['argv'] for event, data in emitted if event == 'model_command'), argv)
        self.assertFalse(Path(argv[argv.index('--cd') + 1]).exists())

    def test_backend_info_reports_luna_without_reading_real_auth(self):
        def fake_run(argv, **kwargs):
            self.assertEqual(argv[0], '/offline/codex')
            if argv[1:] == ['--version']:
                return subprocess.CompletedProcess(argv, 0, stdout='codex-cli offline-fixture\n', stderr='')
            if argv[1:] == ['login', 'status']:
                return subprocess.CompletedProcess(argv, 0, stdout='Logged in using ChatGPT (fixture)', stderr='')
            self.fail('Unexpected subprocess invocation: %r' % argv)

        with patch.object(backend, 'executable', return_value='/offline/codex'), \
                patch.object(backend.subprocess, 'run', side_effect=fake_run) as run:
            info = backend.backend_info()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(info['model'], 'gpt-5.6-luna')
        self.assertEqual(info['reasoning_effort'], 'low')
        self.assertEqual(info['cli_version'], 'codex-cli offline-fixture')
        self.assertEqual(info['authentication'], 'ChatGPT')

    def test_native_tool_events_remain_rejected(self):
        stdout = json.dumps(dict(type='item.completed', item=dict(type='command_execution', command='fixture')))

        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr='')

        with patch.object(backend, 'executable', return_value='/offline/codex'), \
                patch.object(backend.subprocess, 'run', side_effect=fake_run) as run:
            with self.assertRaisesRegex(RuntimeError, 'native tool action detected'):
                backend.invoke('Offline fixture', [], lambda *args, **kwargs: None)
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
