import json
import unittest

from acl import (
    COMMON_RULES,
    FORMAT_INSTRUCTIONS,
    READER_SYSTEM,
    read_free,
    read_structured,
    read_tagged,
    system_prompt,
)


class PromptControlTests(unittest.TestCase):
    def test_only_final_format_paragraph_changes_by_condition(self):
        prompts = {
            condition: system_prompt("buyer", "desk lamp", 55, condition)
            for condition in FORMAT_INSTRUCTIONS
        }
        bases = {
            condition: prompt.removesuffix(FORMAT_INSTRUCTIONS[condition])
            for condition, prompt in prompts.items()
        }
        self.assertEqual(len(set(bases.values())), 1)
        self.assertIn(COMMON_RULES, next(iter(bases.values())))


class FreeReaderTests(unittest.TestCase):
    def test_reader_receives_full_transcript_once(self):
        transcript = [
            {"speaker": "buyer", "content": "I offer 45."},
            {"speaker": "seller", "content": "I accept your offer."},
        ]
        received = []

        def reader(value):
            received.append(value)
            return '{"performative":"accept-proposal","price":null}'

        result = read_free(transcript, reader)
        self.assertTrue(result.ok)
        self.assertEqual(result.performative, "accept-proposal")
        self.assertEqual(result.reader_calls, 1)
        self.assertEqual(received, [transcript])
        self.assertIn("LAST message only", READER_SYSTEM)


class TaggedParserTests(unittest.TestCase):
    def test_leading_tag_and_proposal_price(self):
        calls = []

        def reader(transcript):
            calls.append(transcript)
            return '{"performative":"propose","price":50}'

        result = read_tagged(
            "(propose) I offer 50.", [{"speaker": "buyer", "content": "x"}], reader
        )
        self.assertTrue(result.ok)
        self.assertEqual((result.performative, result.price), ("propose", 50))
        self.assertEqual(result.reader_calls, 1)
        self.assertEqual(len(calls), 1)

    def test_tag_not_at_start_fails_without_reader(self):
        def reader(_transcript):
            self.fail("reader must not run")

        result = read_tagged(
            "Sure. (propose) 50", [{"speaker": "buyer", "content": "x"}], reader
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reader_calls, 0)

    def test_non_propose_tag_does_not_call_reader(self):
        def reader(_transcript):
            self.fail("reader must not run")

        result = read_tagged(
            "(refuse) Goodbye.", [{"speaker": "buyer", "content": "x"}], reader
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.performative, "refuse")


class StructuredParserTests(unittest.TestCase):
    def test_valid_json(self):
        result = read_structured(
            json.dumps({"performative": "propose", "content": {"price": 50}})
        )
        self.assertTrue(result.ok)
        self.assertEqual((result.performative, result.price), ("propose", 50))
        self.assertEqual(result.reader_calls, 0)

    def test_malformed_json(self):
        self.assertFalse(read_structured('{"performative":').ok)

    def test_propose_without_price(self):
        result = read_structured(
            json.dumps({"performative": "propose", "content": {"price": None}})
        )
        self.assertFalse(result.ok)

    def test_unsupported_performative(self):
        result = read_structured(
            json.dumps({"performative": "inform", "content": {"price": None}})
        )
        self.assertFalse(result.ok)

    def test_trailing_natural_language_is_not_repaired(self):
        raw = '{"performative":"propose","content":{"price":50}} I offer 50.'
        self.assertFalse(read_structured(raw).ok)


if __name__ == "__main__":
    unittest.main()
