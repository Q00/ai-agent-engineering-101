import inspect
import json
import unittest
import korean as ko
import experiment as english


class KoreanTests(unittest.TestCase):
    def test_english_is_not_mutated_and_algorithms_are_identical(self):
        self.assertIn("You are", english.ROLE["buyer"])
        self.assertIn("구매자", ko.lab.ROLE["buyer"])
        for fn in ("negotiate", "read_message", "validate_object", "schema_format", "system_prompt"):
            self.assertEqual(inspect.getsource(getattr(english, fn)), inspect.getsource(getattr(ko.lab, fn)))
        self.assertIs(ko.unlimited.lab, ko.lab)
        self.assertEqual(ko.lab.schema_format(True), english.schema_format(True))

    def test_korean_context_and_reader_reach_calls(self):
        replies = iter(['35에 구매하겠습니다.', '{"performative":"propose","price":35}',
                        '35에 판매하겠습니다.', '{"performative":"accept-proposal","price":35}'])
        calls = []
        def call(role, messages, fmt):
            calls.append((role, messages, fmt)); return next(replies)
        result = {"deal_possible": 1}
        ko.lab.negotiate({"item": "탁상등", "reserve": 30, "budget": 45}, "free", call, lambda *a, **k: None, result)
        self.assertEqual((result["outcome"], result["price"]), ("deal", 35))
        self.assertIn("최대 45", calls[0][1][0]["content"])
        self.assertIn("마지막 메시지만", calls[1][1][0]["content"])
        self.assertEqual(json.loads(calls[3][1][1]["content"])[0]["text"], '35에 구매하겠습니다.')
        self.assertTrue(calls[1][2]["json_schema"]["strict"])

    def test_both_turn_policies_keep_same_protocol(self):
        scenario = {"item": "탁상등", "reserve": 30, "budget": 45}
        def run(fn):
            counter = [0]
            def call(*args):
                counter[0] += 1
                return json.dumps({"performative": "accept-proposal" if counter[0] == 9 else "propose", "content": {"price": 35}})
            r = {"deal_possible": 1}
            fn(scenario, "structured", call, lambda *a, **k: None, r)
            return r
        a = run(ko.lab.negotiate)
        b = run(lambda *args: ko.unlimited.negotiate(*args, seconds=180, clock=lambda: 0))
        self.assertEqual((a["outcome"], a["turns"]), ("open", 8))
        self.assertEqual((b["outcome"], b["turns"]), ("deal", 9))


if __name__ == "__main__": unittest.main()
