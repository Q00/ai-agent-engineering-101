"""The model that logs name must be the model actually called, for every R3 surface."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import harness
import r3_harness

ROOT = Path(harness.__file__).resolve().parent


class FakeProcess:
    returncode = 0
    pid = 1

    def __init__(self, command, stdout, **_):
        self.command = command
        stdout.write(b'{"action": "offer", "price": 1, "reason": "fixture"}')

    def communicate(self, _input=None, timeout=None):
        return None

    def poll(self):
        return 0


def built(models=None):
    with patch.object(harness.shutil, "which", return_value="codex.exe"), \
            patch.dict(harness.MODELS, models or {}):
        return harness.build_command("codex")


class ModelConfig(unittest.TestCase):
    def test_command_model_is_the_one_in_models(self):
        command = built()
        self.assertEqual(command[command.index("--model") + 1], harness.MODELS["codex"])

    def test_command_follows_models_rather_than_a_literal(self):
        command = built({"codex": "fixture-model"})
        self.assertEqual(command[command.index("--model") + 1], "fixture-model")

    def test_flags_are_kept(self):
        command = built()
        for flag in ("exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "project_doc_max_bytes=0"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")

    def test_run_harness_logs_the_model_it_actually_ran(self):
        with TemporaryDirectory() as directory, patch.object(harness, "ROOT", Path(directory)), \
                patch.object(harness.shutil, "which", return_value="codex.exe"), \
                patch.object(harness.subprocess, "Popen", side_effect=FakeProcess) as popen, patch("sys.stderr"):
            harness.run_harness("codex", "fixture")
            called = popen.call_args.args[0]
            metadata = json.loads(next(Path(directory).glob("logs/harness/*/metadata.json")).read_text(encoding="utf-8"))
        self.assertEqual(called[called.index("--model") + 1], metadata["model"])
        self.assertEqual(metadata["command"], called)

    def test_seller_adapter_names_the_same_model(self):
        self.assertEqual(r3_harness.CMD, "codex " + harness.MODELS["codex"])

    def test_card_and_agent_file_say_what_the_code_does(self):
        source = (ROOT / "seller_r3.py").read_text(encoding="utf-8")
        self.assertIn("Coded rules answer quote requests and numeric offers", source)
        self.assertNotIn("negotiates the price.", source)
        agent = json.loads((ROOT / "agent.json").read_text(encoding="utf-8"))
        self.assertIn("coded rules answer quotes and numeric offers", agent["harness"])

    def test_active_agent_files_name_the_same_model(self):
        for name in ("agent.json", "agent-r3.json"):
            with self.subTest(name=name):
                agent = json.loads((ROOT / name).read_text(encoding="utf-8"))
                self.assertEqual(agent["seller"]["model"], harness.MODELS["codex"])
                self.assertEqual(agent["buyer"]["model"], harness.MODELS["codex"])
                self.assertIn(harness.MODELS["codex"], agent["harness"])

    def test_round_two_history_is_not_rewritten(self):
        agent = json.loads((ROOT / "agent-r2.json").read_text(encoding="utf-8"))
        self.assertEqual(agent["seller"]["model"], "gpt-6-luna")


if __name__ == "__main__":
    unittest.main()
