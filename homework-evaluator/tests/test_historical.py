"""Historical reviews must not include corrected PR bodies or unrelated commits."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ax_evaluator.snapshot import collect_pr, verify_snapshot


class HistoricalTest(unittest.TestCase):
    def collect(self, out, reject_ancestor=False):
        current, old, spec = "a" * 40, "b" * 40, "c" * 40
        pr = dict(
            head={"sha": current},
            base={"sha": spec},
            changed_files=1,
            title="week 2",
            body="Corrected after professor feedback",
            html_url="https://example.invalid/1",
            user={"login": "student"},
            created_at="2026-09-01T00:00:00Z",
        )
        changes = [{"filename": "submissions/123/week-02/main.py"}]

        def command(args, cwd=None):
            if "merge-base" in args:
                self.assertEqual(args[-2:], [old, current])
                if reject_ancestor:
                    raise ValueError("not an ancestor")
            if "ls-tree" in args and "-r" in args:
                self.assertIn(old, args)
                return b"100644 blob abc\tsubmissions/123/week-02/main.py\0"
            if "ls-tree" in args:
                return b""
            if "cat-file" in args and "-s" in args:
                return b"4"
            if "cat-file" in args and "blob" in args:
                return b"x=1\n"
            if "show" in args:
                return b"# Old assignment\n"
            if "log" in args:
                return f"commit {old}\n".encode()
            return b""

        with (
            patch("ax_evaluator.snapshot.gh", side_effect=[pr, changes, {"sha": spec}]),
            patch("ax_evaluator.snapshot.command", side_effect=command),
        ):
            return collect_pr("example/course", 1, out, spec, "week-02", old)

    def test_old_head_omits_current_body_and_marks_unavailable_roster(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = self.collect(Path(temp) / "snapshot.json")
            verify_snapshot(snapshot)
            self.assertNotIn("@pr.md", snapshot["files"])
            self.assertTrue(snapshot["metadata"]["historical"])
            self.assertFalse(snapshot["metadata"]["roster_included"])
            self.assertEqual(snapshot["metadata"]["head_sha"], "b" * 40)

    def test_unrelated_commit_is_rejected_before_snapshot_is_written(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "snapshot.json"
            with self.assertRaisesRegex(ValueError, "ancestor"):
                self.collect(out, reject_ancestor=True)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
