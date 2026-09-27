"""Private reason must never enter opposing or reader history, even on failure."""
import json
from pathlib import Path
import unittest

from policy_experiment import episode, metadata, prompt


def output(condition, act, price, reason):
    sentence = f'I offer {price} because pickup is convenient.' if act == 'propose' else 'I accept your offer.'
    if condition == 'structured':
        return json.dumps({'performative':act, 'content':{'price':price,
                          'message':sentence,'reason':reason,'discretion':False}},ensure_ascii=False)
    return (f'({act}) ' if condition == 'tagged' else '') + sentence + f'\nReason: {reason}\nDiscretion: no'


class SpyCaller:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []
        self.reader_inputs = []

    def chat(self, system, messages):
        self.requests.append((system, messages))
        return next(self.outputs)

    def __call__(self, system, user):
        self.reader_inputs.append(user)
        last = user.split('LAST MESSAGE:\n')[-1]
        if 'I accept' in last:
            return '{"performative":"accept-proposal","price":null}'
        import re
        price = int(re.search(r'offer (\d+)',last)[1])
        return json.dumps({'price':price} if 'Extract the whole-number' in system
                          else {'performative':'propose','price':price})


class PrivateReasonTests(unittest.TestCase):
    def setUp(self):
        self.scenario = json.loads((Path(__file__).parent/'scenarios_context.json').read_text(encoding='utf-8'))[0]

    def test_private_reasons_hidden_from_opponent_and_reader_in_all_formats(self):
        for condition in ('free','tagged','structured'):
            with self.subTest(condition=condition):
                buyer_reason, seller_reason = '바이어비밀일번', '셀러비밀일번'
                outputs = [output(condition,'propose',80,buyer_reason),
                           output(condition,'propose',90,seller_reason),
                           output(condition,'propose',85,'바이어비밀이번'),
                           output(condition,'accept-proposal',None,'셀러비밀이번')]
                caller = SpyCaller(outputs)
                events = []
                result, _ = episode(self.scenario, condition, caller, {'buyer':2,'seller':2},
                                     lambda text:None, events.append, contextual=True)
                self.assertEqual((result.outcome,result.price,result.format_errors),('deal',85,0))
                seller_history = str(caller.requests[1][1])
                buyer_history = str(caller.requests[2][1])
                self.assertNotIn(buyer_reason,seller_history)
                self.assertIn(buyer_reason,buyer_history)
                self.assertNotIn(seller_reason,buyer_history)
                self.assertIn(seller_reason,str(caller.requests[3][1]))
                public = str(result.transcript) + str(caller.reader_inputs)
                for secret in (buyer_reason,seller_reason,'바이어비밀이번','셀러비밀이번'):
                    self.assertNotIn(secret,public)
                self.assertNotIn('discretion',public.lower())
                self.assertTrue(any(e.get('reason')==buyer_reason for e in events))

    def test_invalid_metadata_is_not_forwarded(self):
        for condition in ('free','tagged','structured'):
            with self.subTest(condition=condition):
                bad = output(condition,'propose',80,'비밀'*20)
                caller = SpyCaller([bad,output(condition,'refuse',None,'거래포기')])
                # Reader labels refusal; only free requires this reader call.
                def reader(system,user):
                    caller.reader_inputs.append(user)
                    return '{"performative":"refuse","price":null}'
                class RefusalCaller:
                    chat = caller.chat
                    def __call__(self,system,user):
                        return reader(system,user)
                result,_ = episode(self.scenario,condition,RefusalCaller(),{'buyer':2,'seller':2},
                                    lambda text:None,lambda data:None,contextual=True)
                self.assertEqual(result.format_errors,1)
                self.assertNotIn('비밀',str(caller.requests[1][1]))
                self.assertNotIn('비밀',str(result.transcript))

    def test_structured_message_is_required_and_private_fields_are_projected_out(self):
        raw=output('structured','propose',80,'비공개사유')
        public,reason,_=metadata(raw,'structured',private=True)
        self.assertEqual(set(json.loads(public)['content']),{'price','message'})
        self.assertEqual(reason,'비공개사유')
        payload=json.loads(raw)
        del payload['content']['message']
        with self.assertRaises(KeyError):
            metadata(json.dumps(payload),'structured',private=True)

    def test_prompt_explains_private_public_distinction(self):
        for condition in ('free','tagged','structured'):
            text=prompt('buyer','keyboard',100,condition,2,self.scenario)
            self.assertIn('the other party and the message reader never receive it',text)
            self.assertIn('it need not repeat Reason',text)
            self.assertNotIn('Reasons are visible to the other party',text)


if __name__=='__main__':
    unittest.main()
