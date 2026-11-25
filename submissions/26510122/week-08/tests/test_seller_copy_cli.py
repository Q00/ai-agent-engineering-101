"""Claude CLI failures must never be counted as buyer preferences."""
import json
import subprocess
import unittest

from experiments.seller_copy_probe import claude_response


class ClaudeCopyResponseTests(unittest.TestCase):
    def response(self,data,code=0):
        return subprocess.CompletedProcess(['claude'],code,json.dumps(data),'')

    def test_auth_failure_never_becomes_a_choice(self):
        for code in (0,1):
            with self.subTest(code=code):
                data={'is_error':True,'result':'Not logged in. Please run /login.'}
                with self.assertRaisesRegex(RuntimeError,'Not logged in'):
                    claude_response(self.response(data,code))

    def test_schema_less_result_cannot_be_promoted_to_preference(self):
        data={'result':'{"candidate_id":"H42","reason":"clear terms"}','is_error':False}
        with self.assertRaisesRegex(ValueError,'no structured_output'):
            claude_response(self.response(data))

    def test_structured_choice_retains_raw_usage_and_resolved_model(self):
        answer={'candidate_id':'H42','reason':'clear terms'}
        data={'structured_output':answer,'usage':{'input_tokens':120,'output_tokens':20,
              'cache_read_input_tokens':200,'cache_creation_input_tokens':100},
              'modelUsage':{'claude-sonnet-fixture':{}}}
        result,usage,raw=claude_response(self.response(data))
        self.assertEqual(result,answer)
        self.assertEqual(usage['input_tokens'],420)
        self.assertEqual(usage['cached_input_tokens'],200)
        self.assertEqual(usage['output_tokens'],20)
        self.assertEqual(raw,data)


if __name__=='__main__':unittest.main()
