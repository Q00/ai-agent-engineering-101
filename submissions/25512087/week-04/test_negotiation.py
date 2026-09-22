import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from negotiation import (
    ParsedMessage,
    evaluate_outcome,
    parse_structured,
    parse_tagged,
    run_episode,
)
from run_experiment import load_results


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, system, messages):
        self.calls.append((system, [dict(message) for message in messages]))
        if not self.responses:
            raise AssertionError("model response queue is empty")
        response = self.responses.pop(0)
        return response() if callable(response) else response


def structured(performative, price=None):
    content = {} if price is None else {"price": price}
    return json.dumps({"performative": performative, "content": content}, separators=(",", ":"))


class ProtocolParserTests(unittest.TestCase):
    def test_structured_message_is_parsed_without_reader(self):
        parsed = parse_structured(
            '{"performative":"propose","content":{"price":85}}'
        )
        self.assertEqual(parsed, ParsedMessage("propose", 85))

    def test_tagged_propose_uses_price_reader_once(self):
        calls = []

        def reader(text):
            calls.append(text)
            return 90

        parsed = parse_tagged("(propose) I can do that for ninety", reader)
        self.assertEqual(parsed, ParsedMessage("propose", 90))
        self.assertEqual(len(calls), 1)

    def test_tagged_refuse_does_not_call_price_reader(self):
        def reader(_):
            raise AssertionError("reader is not needed for refuse")

        self.assertEqual(parse_tagged("(refuse) I will pass", reader), ParsedMessage("refuse", None))

    def test_invalid_structured_message_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_structured('{"performative":"propose","content":{}}')


class OutcomeTests(unittest.TestCase):
    def test_possible_deal_inside_private_limits_is_correct(self):
        scenario = {"reserve": 80, "budget": 100}
        self.assertEqual(evaluate_outcome(scenario, "deal", 90), (1, 0))

    def test_impossible_deal_is_incorrect_and_violation_is_reported(self):
        scenario = {"reserve": 120, "budget": 100}
        self.assertEqual(evaluate_outcome(scenario, "deal", 110), (0, 1))

    def test_no_deal_is_correct_when_limits_do_not_overlap(self):
        scenario = {"reserve": 120, "budget": 100}
        self.assertEqual(evaluate_outcome(scenario, "no_deal", None), (1, 0))


class EpisodeHarnessTests(unittest.TestCase):
    SCENARIO = {"id": "item", "item": "item", "reserve": 50, "budget": 100}

    def test_unread_message_is_forwarded_and_episode_continues(self):
        model = SequenceModel([
            "not json",
            structured("propose", 80),
            structured("accept-proposal"),
        ])
        events = []
        result = run_episode(self.SCENARIO, "structured", model, events.append, max_turns=3)
        self.assertEqual(result["outcome"], "deal")
        self.assertEqual(result["price"], 80)
        self.assertEqual(result["format_errors"], 1)
        self.assertEqual(result["turns"], 3)
        seller_messages = model.calls[1][1]
        self.assertIn("not json", seller_messages[-1]["content"])

    def test_accept_uses_other_side_last_proposal(self):
        model = SequenceModel([
            structured("propose", 70),
            structured("propose", 100),
            structured("reject-proposal"),
            structured("accept-proposal"),
        ])
        result = run_episode(self.SCENARIO, "structured", model, lambda _: None, max_turns=4)
        self.assertEqual(result["outcome"], "deal")
        self.assertEqual(result["price"], 70)

    def test_free_reader_receives_transcript_context(self):
        model = SequenceModel([
            "I can pay 80.",
            '{"performative":"propose","price":80}',
            "I accept 80.",
            '{"performative":"accept-proposal","price":80}',
        ])
        result = run_episode(self.SCENARIO, "free", model, lambda _: None, max_turns=2)
        self.assertEqual(result["outcome"], "deal")
        reader_calls = [call for call in model.calls if call[0].startswith("You are an observer")]
        self.assertEqual(len(reader_calls), 2)
        second_reader_prompt = reader_calls[1][1][-1]["content"]
        self.assertIn("I can pay 80.", second_reader_prompt)
        self.assertIn("I accept 80.", second_reader_prompt)

    def test_agents_keep_role_specific_chat_history(self):
        model = SequenceModel([
            structured("propose", 70),
            structured("accept-proposal"),
        ])
        run_episode(self.SCENARIO, "structured", model, lambda _: None, max_turns=2)
        buyer_first_messages = model.calls[0][1]
        seller_messages = model.calls[1][1]
        self.assertEqual(buyer_first_messages[0]["role"], "user")
        self.assertEqual(seller_messages[-1], {"role": "user", "content": structured("propose", 70)})


class ResumeTests(unittest.TestCase):
    def test_load_results_uses_repeat_number_for_new_results(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            path.write_text(
                "run,condition,scenario\n"
                "1,free,a\n"
                "1,tagged,a\n",
                encoding="utf-8",
            )
            pairs, count = load_results(path, scenario_count=1, requested_runs=3)
        self.assertEqual(count, 2)
        self.assertIn(("free", "a", "1"), pairs)

    def test_load_results_migrates_legacy_global_run_numbers(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            path.write_text(
                "run,condition,scenario\n"
                "1,free,a\n"
                "2,free,b\n"
                "3,tagged,a\n"
                "4,tagged,b\n"
                "5,structured,a\n"
                "6,structured,b\n",
                encoding="utf-8",
            )
            pairs, _ = load_results(path, scenario_count=2, requested_runs=1)
        self.assertIn(("free", "a", "1"), pairs)
        self.assertIn(("structured", "b", "1"), pairs)


if __name__ == "__main__":
    unittest.main()
