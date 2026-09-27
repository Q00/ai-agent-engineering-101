"""Offline checks for grounded prompts and private circumstance isolation."""
import json
from pathlib import Path
import unittest

from context_prompt import context_paragraph, NEGOTIATION_GUIDANCE
from policy_experiment import episode, prompt
from test_policy_experiment import FakeCaller, message


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.scenario = json.loads((Path(__file__).parent / 'scenarios_context.json').read_text(encoding='utf-8'))[0]

    def test_each_role_receives_only_own_private_circumstances(self):
        for role, other in (('buyer', 'seller'), ('seller', 'buyer')):
            text = context_paragraph(role, self.scenario)
            for fact in self.scenario['public_facts'] + self.scenario[role + '_context']:
                self.assertIn(fact, text)
            for fact in self.scenario[other + '_context']:
                self.assertNotIn(fact, text)

    def test_context_and_guidance_identical_across_formats(self):
        common = context_paragraph('buyer', self.scenario)
        for condition in ('free', 'tagged', 'structured'):
            self.assertIn(common, prompt('buyer', 'keyboard', 100, condition, 2, self.scenario))
            self.assertIn(NEGOTIATION_GUIDANCE, common)

    def test_baseline_prompt_remains_context_free(self):
        text = prompt('buyer', 'keyboard', 100, 'free', 2)
        self.assertNotIn('Shared item facts', text)
        self.assertNotIn('Bargain like a person', text)

    def test_missing_context_is_rejected(self):
        invalid = dict(self.scenario)
        invalid.pop('seller_context')
        with self.assertRaises(ValueError):
            context_paragraph('buyer', invalid)

    def test_limits_are_private_and_disclosure_is_forbidden_in_all_formats(self):
        for condition in ('free', 'tagged', 'structured'):
            buyer = prompt('buyer', 'keyboard', 113, condition, 2, self.scenario)
            seller = prompt('seller', 'keyboard', 67, condition, 1, self.scenario)
            self.assertIn('normal maximum budget is 113', buyer)
            self.assertNotIn('normal minimum reserve is 67', buyer)
            self.assertIn('normal minimum reserve is 67', seller)
            self.assertNotIn('normal maximum budget is 113', seller)
            for text in (buyer, seller):
                self.assertIn('strictly private', text)
                self.assertIn('in your public English message', text)
                self.assertIn('never identify it as that boundary', text)
                self.assertNotIn('unless needed', text)

    def test_contextual_episode_uses_private_prompts_and_existing_policy(self):
        outputs = []
        for act, price in [('propose',80), ('accept-proposal',None)]:
            payload = json.loads(message(act,price))
            payload['content']['message'] = 'I offer 80.' if act == 'propose' else 'I accept your offer.'
            outputs.append(json.dumps(payload, ensure_ascii=False))
        caller = FakeCaller(outputs)
        result, note = episode(self.scenario, 'structured', caller, {'buyer':2,'seller':2},
                               lambda text:None, lambda data:None, contextual=True)
        self.assertEqual((result.outcome,result.price,result.correct),('deal',80,1))
        self.assertEqual(note['experiment'],'context20-private-v2')
        self.assertEqual(note['quota_remaining'],{'buyer':2,'seller':2})
        self.assertIn(self.scenario['buyer_context'][0],caller.requests[0][0])
        self.assertNotIn(self.scenario['seller_context'][0],caller.requests[0][0])
        self.assertEqual(caller.requests[1][1][0]['role'],'user')


if __name__ == '__main__':
    unittest.main()
