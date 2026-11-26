import ast
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

import experiment as lab


class LabTests(unittest.TestCase):
    def episode(self, condition, replies, scenario=None):
        iterator = iter(replies)
        calls, events = [], []
        def call(role, messages, fmt):
            calls.append((role, copy.deepcopy(messages), copy.deepcopy(fmt)))
            return next(iterator)
        def emit(event, **data):
            events.append((event, data))
        scenario = scenario or {"id": 2, "item": "a desk lamp", "reserve": 30, "budget": 45}
        result = {"deal_possible": int(scenario["reserve"] <= scenario["budget"])}
        lab.negotiate(scenario, condition, call, emit, result)
        return result, calls, events

    def test_visible_html_prompts_are_preserved(self):
        source = (lab.HERE / "reference/lecture-code.txt").read_text()
        snippet = source[source.index("ROLE ="):source.index("# 에피소드 하나")]
        values = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(snippet).body if isinstance(n, ast.Assign)}
        self.assertEqual(lab.FORMAT, values["FORMAT"])
        self.assertEqual(lab.COMMON, values["COMMON"].removesuffix(" ..."))
        self.assertEqual(lab.READER_SYSTEM, values["READER_SYSTEM"].removesuffix(" ..."))
        self.assertEqual(lab.ROLE["buyer"], values["ROLE"]["buyer"].removesuffix(" ..."))

    def test_html_lamp_misread_is_not_repaired(self):
        replies = [
            "Would you be willing to start at 30?", "null",
            "I'm looking to get 50.", '{"performative":"propose","price":50}',
            "50 is above my budget, I can go up to 40.", '{"performative":"propose","price":50}',
            "I can work with 40, we have a deal.", '{"performative":"accept-proposal","price":40}',
        ]
        r, calls, _ = self.episode("free", replies)
        self.assertEqual((r["outcome"], r["price"], r["violation"], r["format_errors"]), ("deal", 50, 1, 1))
        self.assertEqual(r["reader_calls"], 4)
        self.assertEqual([m["role"] for m in calls[0][1]], ["system"])
        self.assertEqual([m["role"] for m in calls[4][1]], ["system", "assistant", "user"])
        for i, (_, messages, fmt) in enumerate(calls[1::2], 1):
            self.assertEqual(len(json.loads(messages[1]["content"])), i)
            self.assertTrue(fmt["json_schema"]["strict"])

    def test_tagged_rejection_does_not_register_counteroffer(self):
        r, calls, _ = self.episode("tagged", [
            "(reject-proposal) Let me counter at 45 dollars.",
            "(accept-proposal) Agreed at 45.",
            "(refuse) Goodbye.",
        ])
        self.assertEqual((r["outcome"], r["turns"], r["format_errors"]), ("no_deal", 3, 1))
        self.assertEqual(r["reader_calls"], 0)
        self.assertFalse(any(c[0] == "reader" for c in calls))

    def test_tagged_reader_supplies_only_price(self):
        r, _, _ = self.episode("tagged", [
            "(propose) I can offer 35.", '{"performative":"reject-proposal","price":35}',
            "(accept-proposal) Agreed.",
        ])
        self.assertEqual((r["outcome"], r["price"], r["reader_calls"]), ("deal", 35, 1))

    def test_structured_ignores_natural_language_price_and_never_calls_reader(self):
        r, calls, events = self.episode("structured", [
            '{"performative":"propose","content":{"price":null}} I can offer 35.',
            '{"performative":"accept-proposal","content":{"price":35}}',
            '{"performative":"refuse","content":{"price":null}}',
        ])
        self.assertEqual((r["format_errors"], r["reader_calls"], r["outcome"]), (2, 0, "no_deal"))
        self.assertFalse(any(c[0] == "reader" for c in calls))
        self.assertTrue(any(e[0] == "ignored_trailing_text" for e in events))

    def test_eighth_turn_acceptance_precedes_limit(self):
        prop = '{"performative":"propose","content":{"price":35}}'
        accept = '{"performative":"accept-proposal","content":{"price":999}}'
        r, _, _ = self.episode("structured", [prop] * 7 + [accept])
        self.assertEqual((r["outcome"], r["price"], r["turns"]), ("deal", 35, 8))
        r, _, _ = self.episode("structured", [prop] * 8)
        self.assertEqual((r["outcome"], r["correct"]), ("open", 0))

    def test_infeasible_refusal_and_limit_violation_are_scored(self):
        scenario = {"id": 4, "item": "keyboard", "reserve": 90, "budget": 70}
        r, _, _ = self.episode("structured", ['{"performative":"refuse","content":{"price":null}}'], scenario)
        self.assertEqual(r["correct"], 1)
        r, _, _ = self.episode("structured", ['{"performative":"propose","content":{"price":60}}',
            '{"performative":"accept-proposal","content":{"price":60}}'], scenario)
        self.assertEqual((r["outcome"], r["correct"], r["violation"]), ("deal", 0, 1))

    def test_role_prompts_differ_only_in_format_between_conditions(self):
        scenario = {"id": 1, "item": "bicycle", "reserve": 120, "budget": 150}
        for role in ("buyer", "seller"):
            prefixes = [lab.system_prompt(role, scenario, c).removesuffix(lab.FORMAT[c]) for c in lab.CONDITIONS]
            self.assertEqual(len(set(prefixes)), 1)
            self.assertNotIn("120" if role == "buyer" else "150", prefixes[0])

    def test_resume_preserves_failed_rows_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            row = dict.fromkeys(lab.HEADER, "")
            row.update(run="free-01", scenario="1", note="crashed: HTTP 429")
            with path.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=lab.HEADER)
                writer.writeheader(); writer.writerow(row)
            self.assertEqual(lab.load_completed(path), {("free-01", "1")})
            with path.open("a", newline="") as f:
                csv.DictWriter(f, fieldnames=lab.HEADER).writerow(row)
            with self.assertRaises(ValueError): lab.load_completed(path)


if __name__ == "__main__":
    unittest.main()
