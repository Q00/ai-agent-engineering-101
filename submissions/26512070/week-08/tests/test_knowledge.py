"""harness.knowledge: the grade comes from the source and the sample (R14); harness.participant reads it back."""
import os
import tempfile
import unittest
from pathlib import Path

from harness import knowledge, participant


class KnowledgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("HARNESS_OUT")
        os.environ["HARNESS_OUT"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("HARNESS_OUT") if self.old is None else os.environ.__setitem__("HARNESS_OUT", self.old)
        self.tmp.cleanup()

    def test_grades(self):
        base = {"about": "x", "claim": "c", "method": "m"}
        self.assertEqual(knowledge.grade({**base, "source": "market:rules 3.3"}), "stated")
        self.assertEqual(knowledge.grade({**base, "source": "data:trajectory", "sample": {"n": 5}}), "observed")
        self.assertEqual(knowledge.grade({**base, "source": "data:trajectory", "sample": {"n": 0}}), "hypothesis")
        self.assertEqual(knowledge.grade({**base, "source": "data:x", "sample": {"n": 9}, "causal": True}),
                         "hypothesis")
        self.assertEqual(knowledge.grade({**base, "source": "analysis by sonnet"}), "hypothesis")

    def test_write_get_expire_review(self):
        with self.assertRaises(ValueError):
            knowledge.write({"id": "a", "about": "x", "claim": "c"})
        with self.assertRaises(ValueError):
            knowledge.write({"id": "w", "about": "x", "claim": "c", "source": "user:", "method": "m",
                             "layer": "wisdom"})
        knowledge.write({"id": "a", "about": "x", "claim": "old", "source": "data:t", "method": "m",
                         "sample": {"n": 1}}, now=100)
        knowledge.write({"id": "a", "about": "x", "claim": "new", "source": "data:t", "method": "m",
                         "sample": {"n": 2}}, now=200)
        knowledge.write({"id": "r", "about": "rule", "claim": "book counts stock", "source": "market:rules"}
                        | {"method": "read"}, now=200)
        self.assertEqual([i["claim"] for i in knowledge.get(about="x", now=300)], ["new"])
        self.assertEqual(knowledge.get(about="x", now=200 + 25 * 3600), [])          # observed: 24 h
        self.assertEqual(knowledge.flag_review("market:", "patch 3.4", now=400), ["r"])
        self.assertEqual(knowledge.get(about="rule", now=500)[0]["review"]["why"], "patch 3.4")

    def test_participant_get(self):
        parts = {"bob": {"as_shop": {"talks": 3}, "as_buyer": {}, "student": True, "names": ["bob-shop"]}}
        participant.to_knowledge(parts, (0, 1))
        self.assertEqual(participant.get("bob")["as_shop"]["n"], 3)
        self.assertEqual(participant.get("bob-shop")["as_shop"]["login"], "bob")    # by a name we met
        self.assertEqual(participant.get("nobody"), {})


if __name__ == "__main__":
    unittest.main()
