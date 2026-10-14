"""Regression tests for grading errors, not wording or implementation details."""

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from ax_evaluator.checks import HEADER, check
from ax_evaluator.snapshot import collect_local, document, verify_snapshot
from ax_evaluator.storage import read_json

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    scenarios = [
        {"id": "a", "item": "a", "reserve": 10, "budget": 20},
        {"id": "b", "item": "b", "reserve": 20, "budget": 20},
        {"id": "c", "item": "c", "reserve": 30, "budget": 20},
        {"id": "d", "item": "d", "reserve": 40, "budget": 20},
    ]
    rows = []
    for condition in ("prompt_inject", "server_inject"):
        for run in range(1, 4):
            for scenario in scenarios:
                possible = scenario["reserve"] <= scenario["budget"]
                rows.append(
                    dict(
                        zip(
                            HEADER,
                            [
                                str(run),
                                condition,
                                scenario["id"],
                                str(int(possible)),
                                "deal" if possible else "no_deal",
                                "20" if possible else "",
                                "1",
                                "0",
                                "0",
                                "0",
                                "2",
                                "4",
                                "host=test; model=test",
                            ],
                        )
                    )
                )
    files = {
        "server.py": "raise RuntimeError('STUDENT CODE MUST NEVER RUN')\n",
        "scenarios.json": json.dumps(scenarios),
        "REPORT.md": "# Report\nObserved results only.\n",
        "@spec.md": "# Assignment\nSource of truth.\n",
    }
    for condition in ("prompt_inject", "server_inject"):
        for run in range(1, 4):
            files[f"logs/{condition}-{run}.txt"] = "\n".join(
                "[result] " + json.dumps(row)
                for row in rows
                if row["condition"] == condition and row["run"] == str(run)
            )
    snapshot = {
        "metadata": {"source": "local"},
        "files": {path: document(text.encode()) for path, text in files.items()},
    }
    set_rows(snapshot, rows)
    return snapshot, rows, read_json(ROOT / "rubrics/week05.json")


def set_rows(snapshot, rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=HEADER)
    writer.writeheader()
    writer.writerows(rows)
    snapshot["files"]["results.csv"] = document(stream.getvalue().encode())


class ChecksTest(unittest.TestCase):
    def test_valid_records_and_explicit_logs_match(self):
        snapshot, _, rubric = fixture()
        result = check(snapshot, rubric)
        for item in rubric["items"]:
            if item["mode"] == "auto":
                self.assertEqual(result["items"][item["id"]]["status"], "pass")
        self.assertEqual(len(result["log_comparison"]["matched_csv_lines"]), 24)

    def test_server_violation_cannot_hide_behind_reported_zero(self):
        snapshot, rows, rubric = fixture()
        rows[12]["price"] = "21"
        set_rows(snapshot, rows)
        result = check(snapshot, rubric)
        self.assertEqual(result["items"]["C01"]["status"], "fail")
        self.assertEqual(result["items"]["C02"]["status"], "fail")
        self.assertEqual(len(result["log_comparison"]["mismatches"]), 1)

    def test_duplicate_rows_do_not_create_repeats(self):
        snapshot, rows, rubric = fixture()
        for row in rows:
            row["run"] = "1"
        set_rows(snapshot, rows)
        result = check(snapshot, rubric)
        self.assertEqual(result["items"]["F04"]["status"], "fail")
        self.assertIn("duplicate_keys", result["aggregates"])

    def test_open_policy_is_explicit(self):
        snapshot, rows, rubric = fixture()
        rows[2]["outcome"] = "open"
        set_rows(snapshot, rows)
        self.assertEqual(check(snapshot, rubric)["items"]["C02"]["status"], "pending")
        rubric["open_non_deal"] = "correct"
        self.assertEqual(check(snapshot, rubric)["items"]["C02"]["status"], "pass")
        rubric["open_non_deal"] = "incorrect"
        self.assertEqual(check(snapshot, rubric)["items"]["C02"]["status"], "fail")

    def test_crashes_remain_in_attempts_and_out_of_success_aggregate(self):
        snapshot, rows, rubric = fixture()
        for key in HEADER[4:-1]:
            rows[0][key] = ""
        rows[0]["note"] = "connection failed"
        set_rows(snapshot, rows)
        result = check(snapshot, rubric)
        self.assertEqual(result["items"]["F04"]["status"], "pass")
        self.assertEqual(result["aggregates"]["prompt_inject"]["crashed"], 1)
        self.assertEqual(result["aggregates"]["prompt_inject"]["completed"], 11)

    def test_unparsed_logs_are_not_an_automatic_failure(self):
        snapshot, _, rubric = fixture()
        for path in snapshot["files"]:
            if path.startswith("logs/"):
                snapshot["files"][path] = document(b"provider-specific transcript")
        result = check(snapshot, rubric)
        self.assertEqual(result["items"]["C03"]["status"], "pending")
        self.assertEqual(len(result["log_comparison"]["unparsed_logs"]), 6)

    def test_boolean_prices_and_duplicate_scenario_ids_rejected(self):
        for mutation in ("boolean", "duplicate"):
            snapshot, _, rubric = fixture()
            scenarios = json.loads(snapshot["files"]["scenarios.json"]["text"])
            if mutation == "boolean":
                scenarios[0]["budget"] = True
            else:
                scenarios[1]["id"] = scenarios[0]["id"]
            snapshot["files"]["scenarios.json"] = document(
                json.dumps(scenarios).encode()
            )
            self.assertEqual(check(snapshot, rubric)["items"]["F02"]["status"], "fail")

    def test_optional_condition_requires_complete_repeats(self):
        snapshot, rows, rubric = fixture()
        rows.append({**rows[0], "condition": "prompt"})
        set_rows(snapshot, rows)
        self.assertEqual(check(snapshot, rubric)["items"]["F04"]["status"], "fail")

    def test_more_than_eight_moves_fails(self):
        snapshot, rows, rubric = fixture()
        rows[0]["turns"] = "9"
        set_rows(snapshot, rows)
        self.assertEqual(check(snapshot, rubric)["items"]["C01"]["status"], "fail")

    def test_all_crashes_do_not_earn_metric_points(self):
        snapshot, rows, rubric = fixture()
        for row in rows:
            for key in HEADER[4:-1]:
                row[key] = ""
            row["note"] = "failed request"
        set_rows(snapshot, rows)
        findings = check(snapshot, rubric)["items"]
        self.assertEqual(findings["C01"]["status"], "pending")
        self.assertEqual(findings["C02"]["status"], "pending")

    def test_explicit_header_key_value_log_adapter(self):
        snapshot, rows, rubric = fixture()
        for row in rows:
            row["run"] = row["condition"] + "-" + row["run"]
        for path in snapshot["files"]:
            if not path.startswith("logs/"):
                continue
            run = Path(path).stem
            condition, repeat = run.rsplit("-", 1)
            lines = [
                f"# version | condition: {condition} | repeat: {repeat} | host: test"
            ]
            for row in rows:
                if row["run"] == run:
                    lines.append(f"=== scenario {row['scenario']}: item")
                    lines.append(
                        "[result] "
                        + " ".join(f"{key}={row[key]}" for key in HEADER[3:])
                    )
            snapshot["files"][path] = document("\n".join(lines).encode())
        set_rows(snapshot, rows)
        compared = check(snapshot, rubric)["log_comparison"]
        self.assertEqual(len(compared["matched_csv_lines"]), 24)
        # An explicit contradictory header must not get repaired using CSV data.
        path = "logs/prompt_inject-1.txt"
        changed = snapshot["files"][path]["text"].replace("repeat: 1", "repeat: 99")
        snapshot["files"][path] = document(changed.encode())
        compared = check(snapshot, rubric)["log_comparison"]
        self.assertEqual(len(compared["unmatched_csv_lines"]), 4)

    def test_duplicate_log_results_remain_ambiguous(self):
        snapshot, _, rubric = fixture()
        path = "logs/prompt_inject-1.txt"
        text = snapshot["files"][path]["text"]
        snapshot["files"][path] = document((text + "\n" + text).encode())
        self.assertEqual(
            len(check(snapshot, rubric)["log_comparison"]["ambiguous_csv_lines"]), 4
        )

    def test_unreadable_python_is_pending_not_pass(self):
        snapshot, _, rubric = fixture()
        snapshot["files"]["server.py"] = document(b"\xff\xfe")
        self.assertEqual(check(snapshot, rubric)["items"]["F01"]["status"], "pending")

    def test_optional_broken_source_does_not_fail_minimum_source_requirement(self):
        snapshot, _, rubric = fixture()
        snapshot["files"]["extra/broken.py"] = document(b"def broken(:")
        result = check(snapshot, rubric)["items"]["F01"]
        self.assertEqual(result["status"], "pass")
        self.assertIn("extra/broken.py", result["reason"])
        snapshot["files"]["server.py"] = document(b"def broken(:")
        self.assertEqual(check(snapshot, rubric)["items"]["F01"]["status"], "fail")


class SnapshotTest(unittest.TestCase):
    def test_local_snapshot_does_not_execute_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "submission"
            source.mkdir()
            (source / "server.py").write_text("raise RuntimeError('do not run')")
            spec = root / "spec.md"
            spec.write_text("spec")
            out = root / "snapshot.json"
            snapshot = collect_local(source, spec, out)
            verify_snapshot(snapshot)
            snapshot["files"]["server.py"]["text"] = "changed"
            with self.assertRaises(ValueError):
                verify_snapshot(snapshot)
            with self.assertRaises(FileExistsError):
                collect_local(source, spec, out)

    def test_symlink_cannot_escape_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "submission"
            source.mkdir()
            spec = root / "spec.md"
            spec.write_text("spec")
            (source / "outside").symlink_to(spec)
            with self.assertRaises(ValueError):
                collect_local(source, spec, root / "snapshot.json")


if __name__ == "__main__":
    unittest.main()
