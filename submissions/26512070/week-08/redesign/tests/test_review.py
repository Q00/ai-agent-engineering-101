import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from redesign import guard, review


class Review(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=guard.WORK.parent / "work"))
        (self.tmp / "proposals").mkdir()
        book = {"about": "", "rules": [{"id": "F1", "area": "food", "status": "confirmed", "rule": "old",
                                         "source": "agent.md", "harness": ""}]}
        (self.tmp / "rules.json").write_text(json.dumps(book), encoding="utf-8")
        props = {"rules": [
            {"pid": "food#1", "area": "food", "kind": "definition", "status": "confirmed", "rule": "new text",
             "harness": "h", "evidence": ["a-0"], "sources": ["agent.md"], "flags": [], "conflict": "x vs y",
             "existing": {"id": "F1", "relation": "refines"}},
            {"pid": "food#2", "area": "food", "kind": "lesson", "status": "observed", "rule": "fresh",
             "harness": "", "evidence": ["b-1"], "sources": ["git log"], "flags": [], "conflict": "",
             "existing": None}]}
        (self.tmp / "proposals" / "food.json").write_text(json.dumps(props), encoding="utf-8")
        self.patches = [mock.patch.object(guard, "RULE_BOOK", self.tmp / "rules.json"),
                        mock.patch.object(review, "PROPOSALS", self.tmp / "proposals"),
                        mock.patch.object(review, "LOG", self.tmp / "review.jsonl")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_approve_area_replaces_and_adds(self):
        out = dict(review.approve(["food"]))
        self.assertEqual(out, {"food#1": "replaces F1", "food#2": "new food-1"})
        book = json.loads((self.tmp / "rules.json").read_text(encoding="utf-8"))["rules"]
        f1 = book[0]
        self.assertEqual(f1["rule"], "new text")
        self.assertIn("was: old", f1["history"])
        self.assertEqual(f1["conflict"], "x vs y")
        self.assertEqual(book[1]["id"], "food-1")
        self.assertEqual(review.approve(["food"]), [])          # nothing left open in the area

    def test_reject_is_logged_and_hides_the_proposal(self):
        review.reject("food#2", "noise")
        self.assertNotIn("food#2", (self.tmp / "review.jsonl").read_text(encoding="utf-8").replace('"pid": "food#2"', ""))
        self.assertEqual([p for p, _ in review.approve(["food"])], ["food#1"])


if __name__ == "__main__":
    unittest.main()
