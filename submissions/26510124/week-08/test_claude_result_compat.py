"""Regression for Nov 17 server response: dated Haiku identity + fenced JSON.

Only necessary observed protocol fields are reproduced; no real session IDs.
"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import model_backend as backend
import model_usage

ROOT = Path(__file__).resolve().parent
SNAPSHOT = 'claude-haiku-4-5-20251001'
BASE = 'claude-haiku-4-5'


def envelope():
    return {'type':'result', 'subtype':'success', 'is_error':False, 'num_turns':1,
            'permission_denials':[], 'terminal_reason':'completed',
            'usage':{'input_tokens':10, 'cache_creation_input_tokens':6429,
                     'cache_read_input_tokens':0, 'output_tokens':67},
            'modelUsage':{SNAPSHOT:{'inputTokens':10, 'outputTokens':67,
                                  'canonicalModel':BASE, 'provider':'firstParty'}},
            'result':'```json\n{"ready":true,"reason":"ok"}\n```'}


def response(value=None):
    return subprocess.CompletedProcess([],0,json.dumps(envelope() if value is None else value),'')


class ClaudeResultCompatibilityTests(unittest.TestCase):
    def test_observed_dated_snapshot_unwraps_and_preserves_usage(self):
        result=response()
        self.assertEqual(json.loads(backend.answer('haiku',result,ROOT)), {'ready':True,'reason':'ok'})
        usage=model_usage.extract('haiku',result.stdout)
        self.assertEqual(usage['cache_creation_tokens'],6429)
        self.assertEqual(usage['input_tokens'],10)
        self.assertEqual(usage['output_tokens'],67)

    def test_other_version_family_and_mixed_usage_are_rejected(self):
        for canonical in ('claude-haiku-4-6','claude-opus-5-5',None,True,[],{}):
            value=envelope();value['modelUsage'][SNAPSHOT]['canonicalModel']=canonical
            with self.subTest(canonical=canonical), self.assertRaises(ValueError):
                backend.answer('haiku',response(value),ROOT)
        value=envelope();value['modelUsage']['claude-opus-5-5']={'outputTokens':1}
        with self.assertRaises(ValueError): backend.answer('haiku',response(value),ROOT)
        with self.assertRaises(ValueError): backend.answer('opus5.5',response(),ROOT)

    def test_only_complete_json_fence_is_unwrapped(self):
        for raw in ('Explanation\n```json\n{}\n```', '```json\n{}\n```\n```json\n{}\n```'):
            value=envelope();value['result']=raw
            # No tolerant extraction of a valid object from an invalid response.
            with self.assertRaises(json.JSONDecodeError):
                json.loads(backend.answer('haiku',response(value),ROOT))

    def test_mixed_role_preflight_probes_both_and_still_requires_ready_true(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.market') as folder:
            root=Path(folder)
            (root/'model-profile.json').write_text(json.dumps({'version':1,'trading':'opus5.5','shop':'haiku','advisor':'opus5.5'}))
            (root/'advisor-config.json').write_text('{"enabled":false}')
            opus=envelope();opus['modelUsage']={'claude-opus-5-5':{'outputTokens':10,'canonicalModel':'claude-opus-5-5'}}
            with patch.object(backend,'ROOT',root), patch.object(backend.shutil,'which',return_value='/cli/claude'), \
                 patch.object(backend,'execute',side_effect=[response(),response(opus)]) as execute, redirect_stdout(io.StringIO()):
                self.assertEqual(backend.main(['--preflight']),0)
                self.assertEqual(execute.call_count,2)
            bad=envelope();bad['result']='```json\n{"ready":false,"reason":"not ready"}\n```'
            with patch.object(backend,'ROOT',root), patch.object(backend.shutil,'which',return_value='/cli/claude'), \
                 patch.object(backend,'execute',return_value=response(bad)) as execute, redirect_stdout(io.StringIO()):
                self.assertEqual(backend.main(['--preflight']),1)
                execute.assert_called_once()
