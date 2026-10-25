"""Offline tests: private history, reason validation, quota and exception limits."""
import json
import unittest

from policy_experiment import bounds, episode, history_for, metadata, prompt


def message(act, price, discretion=False, reason='합리적인 가격이라 판단'):
    return json.dumps({'performative': act, 'content': {'price': price,
                      'reason': reason, 'discretion': discretion}}, ensure_ascii=False)


class FakeCaller:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def chat(self, system, messages):
        self.requests.append((system, messages))
        return next(self.outputs)

    def __call__(self, system, user):
        raise AssertionError('structured must never call a reader')


class PolicyTests(unittest.TestCase):
    def run_case(self, outputs, reserve=90, budget=75, quota=None):
        if quota is None:
            quota = {'buyer':2, 'seller':2}
        caller = FakeCaller(outputs)
        result, note = episode({'id':'test','item':'lamp','reserve':reserve,'budget':budget},
                               'structured', caller, quota, lambda text:None, lambda data:None)
        return result, note, caller

    def test_history_roles(self):
        transcript = [('buyer','offer'),('seller','counter')]
        self.assertEqual([m['role'] for m in history_for('buyer', transcript)], ['assistant','user'])
        self.assertEqual([m['role'] for m in history_for('seller', transcript)], ['user','assistant'])

    def test_integer_boundaries(self):
        self.assertEqual(bounds('buyer', 65), 78)
        self.assertEqual(bounds('seller', 45), 36)
        self.assertEqual(bounds('seller', 91), 73)

    def test_reason_length(self):
        with self.assertRaises(ValueError):
            metadata(message('propose',70,reason='가'*31), 'structured')
        self.assertEqual(metadata(message('propose',70,reason='가'*30), 'structured')[1], '가'*30)

    def test_both_parties_consume_at_most_once(self):
        result, note, caller = self.run_case([message('propose',80,True), message('accept-proposal',999,True)])
        self.assertEqual((result.outcome,result.price,result.violation,result.correct),('deal',80,1,0))
        self.assertEqual(note['quota_remaining'], {'buyer':1,'seller':1})
        self.assertEqual(len(note['discretion_uses']),2)
        self.assertIn('normal maximum budget is 75', caller.requests[0][0])
        self.assertNotIn('normal minimum reserve is 90', caller.requests[0][0])

    def test_no_quota_acceptance_is_rejected(self):
        result, note, _ = self.run_case([message('propose',80,True), message('accept-proposal',80,True),
                                         message('refuse',None)], quota={'buyer':0,'seller':2})
        self.assertEqual(result.outcome, 'no_deal')
        self.assertEqual(note['policy_rejections'],1)

    def test_beyond_twenty_percent_is_rejected(self):
        result, note, _ = self.run_case([message('propose',91,True), message('accept-proposal',91,True),
                                         message('refuse',None)])
        self.assertEqual(result.outcome,'no_deal')
        self.assertEqual(note['quota_remaining'],{'buyer':2,'seller':2})

    def test_explicit_reasoned_authorization_required(self):
        result, note, _ = self.run_case([message('propose',80,False), message('accept-proposal',80,True),
                                         message('refuse',None)])
        self.assertEqual(result.outcome,'no_deal')
        self.assertEqual(note['policy_rejections'],1)

    def test_normal_deal_does_not_consume_quota(self):
        result, note, _ = self.run_case([message('propose',80),message('accept-proposal',None)],
                                       reserve=70,budget=100)
        self.assertEqual((result.correct,result.violation),(1,0))
        self.assertEqual(note['quota_remaining'],{'buyer':2,'seller':2})


if __name__ == '__main__':
    unittest.main()
