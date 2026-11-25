"""Mixed Claude roles and operator transport; no live models or authentication."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import model_backend as backend
import shop_ops_model
from shop_ops_policy import config_at

ROOT = Path(__file__).resolve().parent


class RoleModelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ('model-profile.json', 'shop-ops-config.json', 'advisor-config.json'):
            (self.root/name).write_bytes((ROOT/name).read_bytes())

    def test_current_roles_use_haiku_shop_and_opus_buyer_and_operations(self):
        for prompt in ('seller.txt', 'seller_inventory.txt', 'seller_opening_value.txt'):
            self.assertEqual(backend.for_prompt(prompt, self.root), 'haiku')
        for prompt in ('buyer.txt', 'market_select.txt'):
            self.assertEqual(backend.for_prompt(prompt, self.root), 'opus5.5')
        self.assertEqual(config_at(self.root)['model_profile'], 'opus5.5')
        self.assertEqual(backend.required_executables(self.root), ('claude',))
        self.assertEqual(set(backend.active_profiles(self.root)), {'haiku','opus5.5'})
        with self.assertRaisesRegex(ValueError, 'disabled'):
            backend.for_prompt('sales_advisor.txt', self.root)

    def test_shop_profile_edit_does_not_change_buyer_or_ops(self):
        before = (self.root/'shop-ops-config.json').read_bytes()
        with patch.object(backend,'ROOT',self.root), patch.object(backend,'execute') as execute, redirect_stdout(io.StringIO()):
            self.assertEqual(backend.main(['--shop-profile','sol']), 0)
        execute.assert_not_called()
        self.assertEqual(backend.for_prompt('seller_inventory.txt',self.root),'sol')
        self.assertEqual(backend.for_prompt('buyer.txt',self.root),'opus5.5')
        self.assertEqual((self.root/'shop-ops-config.json').read_bytes(),before)

    def test_invalid_shop_override_fails_instead_of_using_buyer(self):
        path = self.root/'model-profile.json'
        cfg = json.loads(path.read_text())
        for invalid in ('typo', None, [], True):
            path.write_text(json.dumps({**cfg,'shop':invalid}))
            with self.assertRaises(ValueError):
                backend.for_prompt('seller_inventory.txt',self.root)

    def test_enabled_operations_model_is_part_of_preflight_even_if_trading_is_haiku(self):
        path = self.root/'model-profile.json'
        cfg = json.loads(path.read_text())
        path.write_text(json.dumps({**cfg,'trading':'haiku'}))
        with patch.object(backend,'ROOT',self.root), patch.object(backend.shutil,'which',return_value='/cli/claude'), \
             patch.object(backend,'ensure_chatgpt') as auth, \
             patch.object(backend,'call',return_value='{"ready":true,"reason":"ok"}') as call, redirect_stdout(io.StringIO()):
            self.assertEqual(backend.main(['--preflight']),0)
        auth.assert_not_called()
        self.assertEqual([c.args[0] for c in call.call_args_list],['haiku','opus5.5'])
        ops_path = self.root/'shop-ops-config.json'
        ops = json.loads(ops_path.read_text())
        ops_path.write_text(json.dumps({**ops,'model_enabled':False}))
        self.assertEqual(backend.active_profiles(self.root),('haiku',))

    def response(self, model='claude-opus-5-5'):
        text=json.dumps({'action':'hold','targets':[],'diagnosis':'thin evidence','expected_effect':'observe'})
        return subprocess.CompletedProcess([],0,json.dumps({'type':'result','subtype':'success',
            'is_error':False,'num_turns':1,'permission_denials':[],'result':text,
            'modelUsage':{model:{'outputTokens':25,'canonicalModel':model}}}), '')

    def test_ops_claude_uses_schema_in_prompt_and_no_codex_auth(self):
        with patch.object(backend,'load_profile',side_effect=AssertionError('shared config read')), \
             patch.object(backend,'ensure_chatgpt') as auth, \
             patch.object(backend,'execute',return_value=self.response()) as execute:
            result=shop_ops_model.analyse({'test':True},self.root,config_at(self.root))
        self.assertEqual(result['action'],'hold')
        auth.assert_not_called()
        execute.assert_called_once()
        command=execute.call_args.args[0]
        self.assertEqual(command[0],'claude')
        self.assertEqual(command[command.index('--model')+1],'claude-opus-5-5')
        self.assertEqual(command[command.index('--tools')+1],'')
        self.assertIn('OUTPUT SCHEMA:',execute.call_args.kwargs['prompt'])
        self.assertIn('"additionalProperties": false',execute.call_args.kwargs['prompt'])
        self.assertLessEqual(execute.call_args.kwargs['timeout'],45)

    def test_ops_wrong_claude_model_is_rejected_without_fallback(self):
        with patch.object(backend,'ensure_chatgpt') as auth, \
             patch.object(backend,'execute',return_value=self.response('claude-haiku-4-5')) as execute:
            with self.assertRaisesRegex(ValueError,'exact requested'):
                shop_ops_model.analyse({'test':True},self.root,config_at(self.root))
        auth.assert_not_called()
        execute.assert_called_once()


if __name__ == '__main__':
    unittest.main()
