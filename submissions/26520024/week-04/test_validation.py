"""Synthetic evidence lives only in temporary directories, never submission logs."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import codex_backend as backend
from negotiation import CONDITIONS, MAX_TURNS, ROOT, load_json
from run_experiment import FROZEN, run_group
from test_negotiation import label, structured
from validate_results import validate


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in FROZEN:
            shutil.copyfile(ROOT / name, self.root / name)
        self.info = dict(provider="offline fixture", model=backend.MODEL,
            reasoning_effort=backend.REASONING, cli_version="test", authentication="none",
            temperature="unknown", max_output_tokens="unknown", timeout_seconds=backend.TIMEOUT,
            retry_delays=list(backend.RETRY_DELAYS), response_schema=None,
            max_turns=MAX_TURNS, conda_env="base", executable="offline fixture",
            hashes={name: hashlib.sha256((self.root / name).read_bytes()).hexdigest() for name in FROZEN})
        with contextlib.redirect_stdout(io.StringIO()):
            for condition in CONDITIONS:
                for repeat in range(1, 4):
                    run_group(self.root, "{}-{:02d}".format(condition, repeat), condition,
                              load_json("scenarios.json"), load_json("prompts.json"), self.info,
                              session_factory=self.factory(condition))

    def factory(self, condition):
        def factory(emit):
            replies = iter({
                "free": ["I offer 80.", label("propose", 80), "Accepted.", label("accept-proposal")],
                "tagged": ["(propose) I offer 80.", label("propose", 80), "(accept-proposal) Accepted."],
                "structured": [structured("propose", 80), structured("accept-proposal")],
            }[condition])
            def transport(system, history, emit):
                raw = next(replies)
                usage = dict(input_tokens=10, output_tokens=2)
                argv = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--sandbox",
                        "read-only", "--skip-git-repo-check", "--cd", "/tmp/offline-fixture",
                        "--model", backend.MODEL, "--output-last-message", "/tmp/offline-fixture/reply.txt",
                        "--json", "--color", "never", "-c", 'web_search="disabled"',
                        "-c", 'model_reasoning_effort="{}"'.format(backend.REASONING)]
                for feature in backend.DISABLED:
                    argv.extend(["--disable", feature])
                argv.append("-")
                output = [dict(type="item.completed", item=dict(type="agent_message", text=raw)),
                          dict(type="turn.completed", usage=usage)]
                emit("model_input", payload=backend.payload(system, history))
                emit("model_command", argv=argv)
                emit("model_output", stdout="\n".join(map(json.dumps, output)), stderr="", returncode=0)
                emit("model_response", raw=raw, usage=usage)
                return raw, usage
            return backend.ModelSession(emit, transport=transport)
        return factory

    def validate(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return validate(self.root)

    def mutate(self, kind, edit):
        path = self.root / "logs/free-01.log"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        edit(next(e for e in events if e["event"] == kind))
        path.write_text("\n".join(map(json.dumps, events)) + "\n")

    def test_complete_replay(self):
        totals = self.validate()
        self.assertEqual((totals["completed"], totals["actor_calls"], totals["reader_calls"]), (36, 72, 36))

    def test_csv_tampering_detected(self):
        path = self.root / "results.csv"
        path.write_text(path.read_text().replace("free-01,free,S1,1,deal,80,1", "free-01,free,S1,1,deal,80,0", 1))
        with self.assertRaisesRegex(ValueError, "CSV differs"):
            self.validate()

    def test_raw_response_tampering_detected(self):
        self.mutate("model_response", lambda e: e.update(raw="different reply"))
        with self.assertRaisesRegex(ValueError, "final reply differs"):
            self.validate()

    def test_private_input_tampering_detected(self):
        self.mutate("model_input", lambda e: e["payload"].update(system="leaked private limits"))
        with self.assertRaisesRegex(ValueError, "model input differs"):
            self.validate()

    def test_transport_flag_tampering_detected(self):
        self.mutate("model_command", lambda e: e["argv"].remove("--ignore-user-config"))
        with self.assertRaisesRegex(ValueError, "transport flags changed"):
            self.validate()

    def test_frozen_input_tampering_detected(self):
        path = self.root / "prompts.json"
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "frozen file changed"):
            self.validate()

    def test_usage_tampering_detected(self):
        self.mutate("model_response", lambda e: e["usage"].update(input_tokens=99))
        with self.assertRaisesRegex(ValueError, "usage differs"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
