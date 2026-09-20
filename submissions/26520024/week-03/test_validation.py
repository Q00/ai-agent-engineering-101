"""Synthetic evidence lives only in temporary directories, never results.csv."""
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import codex_backend
from contract_net import CONDITIONS, HEADER, ROOT, load_json
from run_experiment import FROZEN_FILES, execute_run
import validate_results


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in FROZEN_FILES:
            shutil.copyfile(str(ROOT / name), str(self.root / name))
        info = dict(provider="OFFLINE FIXTURE", model=codex_backend.MODEL,
                    cli_version="fixture", authentication="none", reasoning_effort="low",
                    temperature="unknown", max_output_tokens="unknown",
                    timeout_seconds=180, response_schema=None,
                    hashes={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                            for name in FROZEN_FILES})
        def fake(system, user, emit):
            raw = json.dumps(dict(bid=True, confidence=90, reason="OFFLINE FIXTURE"))
            usage = dict(input_tokens=5, output_tokens=3)
            argv = ["fixture-codex", "--model", codex_backend.MODEL, "--ignore-user-config",
                    "--ephemeral", "--skip-git-repo-check", "--json", "--sandbox", "read-only"]
            for feature in codex_backend.DISABLED:
                argv.extend(["--disable", feature])
            events = [dict(type="item.completed", item=dict(type="agent_message", text=raw)),
                      dict(type="turn.completed", usage=usage)]
            emit("model_input", payload=codex_backend.payload(system, user))
            emit("model_command", argv=argv)
            emit("model_output", stdout="\n".join(map(json.dumps, events)), stderr="", returncode=0)
            emit("model_response", raw=raw, usage=usage)
            return raw, usage
        with contextlib.redirect_stdout(io.StringIO()):
            for i in range(9):
                execute_run(self.root, "{:03d}".format(i + 1), CONDITIONS[i % 3],
                            load_json("tasks.json"), load_json("prompts.json"), info, fake)

    def validate(self):
        with patch.object(validate_results, "ROOT", self.root), contextlib.redirect_stdout(io.StringIO()):
            validate_results.validate()

    def test_consistent_fixture_passes(self):
        self.validate()

    def test_tampered_csv_rejected(self):
        path = self.root / "results.csv"
        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["correct"] = "6"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADER)
            writer.writeheader()
            writer.writerows(rows)
        with self.assertRaisesRegex(ValueError, "CSV not equal"):
            self.validate()

    def test_changed_frozen_input_rejected(self):
        (self.root / "tasks.json").write_text("[]")
        with self.assertRaisesRegex(ValueError, "changed frozen file"):
            self.validate()

    def test_tampered_raw_response_rejected(self):
        path = self.root / "logs/001-baseline.log"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        event = next(e for e in events if e["event"] == "model_response")
        event["raw"] = "tampered"
        path.write_text("\n".join(map(json.dumps, events)) + "\n")
        with self.assertRaisesRegex(ValueError, "raw reply mismatch"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
