"""Offline coverage of the cheaper GPT advisor, with no model/auth/network calls."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import harness
import model_backend as backend
import sales_advisor as advisor

ROOT = Path(__file__).resolve().parent


class LunaAdvisorTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.write_profile()
        (self.root / "prompts").mkdir()
        (self.root / "prompts/sales_advisor.txt").write_text("Return evidence-grounded advice.")
        self.context = {"categories": ["가구>의자"], "evidence": [{"id": "review:verified", "kind": "review"}]}
        self.advice = {
            "reason": "Recent price complaints support a small bounded concession.",
            "default": {"price_bias_percent": -3, "pace": "quick", "focus": "value",
                        "evidence_ids": ["review:verified"]},
            "categories": [{"category": "가구>의자", "price_bias_percent": 0, "pace": "balanced",
                            "focus": "quality", "evidence_ids": ["review:verified"]}],
        }

    def write_profile(self, trading="astra", advisor_profile="luna"):
        (self.root / "model-profile.json").write_text(json.dumps(
            {"version": 1, "trading": trading, "advisor": advisor_profile}))

    def test_profiles_require_only_the_clis_actually_selected(self):
        for trading, analyst, required in (
                ("astra", "luna", ("codex",)), ("haiku", "luna", ("claude", "codex")),
                ("astra", "haiku", ("claude", "codex")), ("haiku", "haiku", ("claude",))):
            with self.subTest(trading=trading, advisor=analyst):
                self.write_profile(trading, analyst)
                self.assertEqual(backend.required_executables(self.root), required)
                self.assertEqual(backend.for_prompt("sales_advisor.txt", self.root), analyst)
                for prompt in ("buyer.txt", "seller_inventory.txt", "market_select.txt"):
                    self.assertEqual(backend.for_prompt(prompt, self.root), trading)

    def test_manual_advisor_rollback_does_not_change_trading_or_start_a_cli(self):
        with patch.object(backend, "ROOT", self.root), patch.object(backend, "execute") as run, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(backend.main(["--advisor-profile", "haiku"]), 0)
            self.assertEqual(backend.load_profile(self.root),
                             {"version": 1, "trading": "astra", "advisor": "haiku"})
            self.assertEqual(backend.main(["--advisor-profile", "luna"]), 0)
            self.assertEqual(backend.main(["--profile", "haiku"]), 0)
            run.assert_not_called()
        self.assertEqual(backend.load_profile(self.root),
                         {"version": 1, "trading": "haiku", "advisor": "luna"})

    def test_luna_output_schema_covers_nested_advice_instead_of_trade_actions(self):
        cmd = backend.command("luna", self.root, "sales_advisor.txt")
        self.assertEqual(cmd[cmd.index("--model") + 1], "gpt-6-luna")
        self.assertIn('model_reasoning_effort="low"', cmd)
        self.assertIn('forced_login_method="chatgpt"', cmd)
        self.assertIn("--ephemeral", cmd)
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "read-only")
        schema = json.loads((self.root / "schema.json").read_text())
        self.assertEqual(set(schema["required"]), {"reason", "default", "categories"})
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["reason"]["maxLength"], 600)
        default = schema["properties"]["default"]
        rows = schema["properties"]["categories"]
        self.assertEqual(rows["maxItems"], 4)
        for node, expected in ((default, set(self.advice["default"])),
                               (rows["items"], set(self.advice["categories"][0]))):
            self.assertEqual(set(node["required"]), expected)
            self.assertFalse(node["additionalProperties"])
            fields = node["properties"]
            self.assertEqual(fields["price_bias_percent"]["type"], "integer")
            self.assertEqual((fields["price_bias_percent"]["minimum"],
                              fields["price_bias_percent"]["maximum"]), (-5, 5))
            self.assertEqual(set(fields["pace"]["enum"]), advisor.PACE)
            self.assertEqual(set(fields["focus"]["enum"]), advisor.FOCUS)
            self.assertEqual((fields["evidence_ids"]["minItems"], fields["evidence_ids"]["maxItems"]), (1, 4))

    def test_luna_call_uses_existing_login_and_final_file_without_api_environment(self):
        raw = json.dumps(self.advice)
        log = io.StringIO()
        def execute(cmd, **kwargs):
            self.assertEqual(cmd[cmd.index("--model") + 1], "gpt-6-luna")
            self.assertEqual(kwargs["env"]["CODEX_HOME"], "/existing/auth")
            for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
                self.assertNotIn(key, kwargs["env"])
            self.assertLessEqual(kwargs["timeout"], 60)
            (Path(kwargs["cwd"]) / "answer.json").write_text(raw)
            return subprocess.CompletedProcess(cmd, 0, '{"type":"turn.completed"}', "")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unused", "CODEX_API_KEY": "unused",
                                     "OPENAI_BASE_URL": "https://unused.invalid", "CODEX_HOME": "/existing/auth"}), \
                patch.object(backend, "ensure_chatgpt") as auth, patch.object(backend, "execute", side_effect=execute) as run:
            result = backend.call("luna", "sales_advisor.txt", "Evidence only", timeout=60, log=log)
        auth.assert_called_once()
        run.assert_called_once()
        self.assertEqual(advisor.validate_advice(result, self.context), self.advice)
        self.assertIn("FINAL ANSWER:", log.getvalue())
        self.assertIn("PROFILE: luna", log.getvalue())

    def test_analyse_routes_one_luna_call_and_validates_evidence_without_retry(self):
        invalid = deepcopy(self.advice)
        invalid["default"]["evidence_ids"] = ["invented:citation"]
        for output, valid in ((self.advice, True), (invalid, False)):
            with self.subTest(valid=valid), patch.object(harness, "ROOT", self.root), \
                    patch.dict(os.environ, {"WEEK06_LOG_DIR": str(self.root / "logs")}), \
                    patch.object(harness, "call_model", return_value=json.dumps(output)) as model:
                if valid:
                    self.assertEqual(advisor.analyse(self.context), self.advice)
                else:
                    with self.assertRaises(harness.HarnessError):
                        advisor.analyse(self.context)
                model.assert_called_once()
                self.assertEqual(model.call_args.args[:2], ("luna", "sales_advisor.txt"))
                self.assertLessEqual(model.call_args.kwargs["timeout"], 60)

    def test_check_only_authenticates_and_does_not_require_claude_for_all_gpt(self):
        def which(name):
            return "/bin/codex" if name == "codex" else None
        with patch.object(backend, "ROOT", self.root), patch.object(backend.shutil, "which", side_effect=which), \
                patch.object(backend, "ensure_chatgpt") as auth, patch.object(backend, "call") as model, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(backend.main(["--check"]), 0)
        model.assert_not_called()
        auth.assert_called_once()

    def test_preflight_probes_each_selected_codex_model(self):
        for trading, expected in (("astra", ["astra", "luna"]), ("haiku", ["luna"])):
            self.write_profile(trading)
            with self.subTest(trading=trading), patch.object(backend, "ROOT", self.root), \
                    patch.object(backend.shutil, "which", return_value="/cli"), \
                    patch.object(backend, "call", return_value='{"ready":true,"reason":"ok"}') as model, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(backend.main(["--preflight"]), 0)
            self.assertEqual([c.args[0] for c in model.call_args_list], expected)
            self.assertTrue(all(c.args[1] == "preflight" for c in model.call_args_list))

    def test_unavailable_luna_fails_preflight_without_fallback_or_config_change(self):
        profile = (self.root / "model-profile.json").read_bytes()
        def probe(selected, *args, **kwargs):
            if selected == "luna":
                raise ValueError("Selected model is unavailable")
            return '{"ready":true,"reason":"ok"}'
        with patch.object(backend, "ROOT", self.root), patch.object(backend.shutil, "which", return_value="/cli"), \
                patch.object(backend, "call", side_effect=probe) as model, redirect_stdout(io.StringIO()):
            self.assertEqual(backend.main(["--preflight"]), 1)
        self.assertEqual([c.args[0] for c in model.call_args_list], ["astra", "luna"])
        self.assertEqual((self.root / "model-profile.json").read_bytes(), profile)


if __name__ == "__main__":
    unittest.main()
