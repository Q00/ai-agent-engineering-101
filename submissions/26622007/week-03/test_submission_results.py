"""Recorded-data regression checks for the course export, without API calls."""
import copy
import csv
import io
import json
import unittest

import submission_results as export


class SubmissionExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metrics = json.loads((export.BATCH / "summary.json").read_text())["runs"]

    def example(self, suffix):
        metric = next(m for m in self.metrics if m["run_id"].endswith(suffix))
        path = export.PEER / "logs" / (metric["run_id"] + ".jsonl")
        rows = [(n, json.loads(line)) for n, line in enumerate(path.read_text().splitlines(), 1)]
        return metric, rows

    def test_recursive_cost_does_not_inflate_root_tasks_or_correct(self):
        metric, trace = self.example("r1-release-review-baseline")
        row, _ = export.project(metric, trace, "B", "primary")
        # A won the root, B handled a child. Four tasks negotiated, one root graded.
        self.assertEqual([row[k] for k in export.COUNTS], [1, 0, 28, 0, 1])
        note = json.loads(row["note"])
        self.assertEqual(note["message_parts"], {"announcements": 12, "bids": 12, "awards": 4})
        self.assertEqual(note["calls"], 20)
        self.assertEqual(note["root_messages"], 7)

    def test_post_award_transport_failure_keeps_partial_award_out_of_counts(self):
        metric, trace = self.example("r3-payment-redesign-overconfident")
        row, _ = export.project(metric, trace, "A", "primary")
        self.assertEqual([row[k] for k in export.COUNTS], [""] * 5)
        note = json.loads(row["note"])
        self.assertEqual(note["root_award"], "C")
        self.assertEqual(note["observed_partial"]["misawards"], 1)
        self.assertEqual(note["observed_partial"]["root_unawarded"], 0)
        self.assertNotIn("unassigned", note["observed_partial"])
        self.assertTrue(note["error"])

    def test_refusal_and_http_retry_are_not_bid_messages(self):
        metric, trace = self.example("r1-release-review-baseline")
        modified = copy.deepcopy(trace)
        next(row for _, row in modified if row["event"] == "proposal")["proposal"]["bid"] = False
        modified.append((999, {"event": "http_request", "attempt": 2}))
        row, _ = export.project(metric, modified, "B", "primary")
        self.assertEqual(row["messages"], 27)
        self.assertEqual(json.loads(row["note"])["calls"], 20)

    def test_summary_disagreement_is_rejected(self):
        metric, trace = self.example("r1-release-review-baseline")
        with self.assertRaisesRegex(ValueError, "root award differs"):
            export.project(dict(metric, root_award="B"), trace, "B", "primary")

    def test_final_slots_include_recovery_once_and_preserve_original_failures(self):
        outputs, copies, _ = export.artifacts()
        combined = list(csv.DictReader(io.StringIO(outputs[export.ROOT / "results.csv"].decode())))
        primary = list(csv.DictReader(io.StringIO(outputs[export.OUTPUT / "results-primary.csv"].decode())))
        recovery = list(csv.DictReader(io.StringIO(outputs[export.OUTPUT / "results-recovery.csv"].decode())))
        self.assertEqual(list(primary[0]), export.HEADER)
        self.assertEqual((len(primary), len(recovery), len(copies)), (45, 16, 122))
        self.assertEqual(sum(r["tasks"] == "" for r in primary), 16)
        self.assertEqual({json.loads(r["note"])["cohort"] for r in primary}, {"primary"})
        self.assertTrue(all(r["tasks"] == "1" for r in recovery))
        self.assertEqual(sum(not json.loads(r["note"])["facts_passed"] for r in recovery), 1)
        self.assertFalse({r["run"] for r in primary} & {r["run"] for r in recovery})
        self.assertEqual(len(combined), 61)
        self.assertEqual(sum(r["tasks"] == "1" for r in combined), 45)
        self.assertEqual(sum(r["tasks"] == "" for r in combined), 16)
        completed = [r for r in combined if r["tasks"] == "1"]
        slots = {(json.loads(r["note"])["block"], r["condition"], json.loads(r["note"])["case"])
                 for r in completed}
        self.assertEqual(len(slots), 45)
        self.assertEqual(sum(int(r["correct"]) for r in completed), 17)
        self.assertEqual(sum(int(r["misawards"]) for r in completed), 28)
        self.assertEqual(sum(json.loads(r["note"])["facts_passed"] for r in completed), 44)

    def test_every_run_exports_full_protocol_trace_alongside_console(self):
        _, copies, rows = export.artifacts()
        contents = dict(copies)
        for row in rows:
            note = json.loads(row["note"])
            console = export.ROOT / note["console"]
            trace = export.ROOT / note["trace"]
            self.assertEqual(console.parent, export.ROOT / "logs")
            self.assertEqual(trace.parent, export.ROOT / "logs")
            self.assertIn(console, contents)
            self.assertEqual(contents[trace], (export.PEER / "logs" / trace.name).read_bytes())
            events = [json.loads(line) for line in contents[trace].decode().splitlines()]
            announcements = [e for e in events if e["event"] == "http_request" and e["phase"] == "propose"]
            self.assertTrue(announcements)
            self.assertTrue(all(e["payload"]["messages"] for e in announcements))
            for event in events:
                if event["event"] == "proposal":
                    self.assertIn("confidence", event["proposal"])
                    self.assertTrue(event["proposal"]["reason"])
            if row["tasks"]:
                self.assertTrue(any(e["event"] == "award" for e in events))


if __name__ == "__main__":
    unittest.main()
