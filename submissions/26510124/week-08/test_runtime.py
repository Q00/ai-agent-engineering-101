from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from run_round3 import identity, verify_kit
import run_round3 as runtime

ROOT = Path(__file__).resolve().parent


def write_key(path):
    path.write_bytes(Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


class RuntimeTests(unittest.TestCase):
    def check_launcher(self, profile=None, *, join=False, missing=(), advisor_enabled=True):
        (ROOT / ".market").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as d:
            folder = Path(d)
            (folder / "advisor-config.json").write_text(json.dumps({"enabled": advisor_enabled}))
            if profile is not None:
                (folder / "model-profile.json").write_text(json.dumps(profile))
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(runtime, "ROOT", folder), \
                 patch.object(runtime, "verify_kit", return_value=folder / "kit.py"), \
                 patch.object(runtime, "identity", return_value=[]), \
                 patch.object(runtime.shutil, "which", side_effect=lambda name: None if name in missing else "/cli/" + name), \
                 patch.object(runtime.sys, "argv", ["run_round3.py", *(["--join"] if join else [])]), \
                 patch.dict(os.environ, {"MARKET_OWNER": "synthetic-test-owner"}), \
                 patch("subprocess.run", side_effect=AssertionError("Launcher check must never invoke models")), \
                 patch.object(runtime.os, "execv", side_effect=AssertionError("No join is expected")), \
                 redirect_stdout(output), redirect_stderr(errors):
                result = runtime.main()
            self.assertFalse((folder / ".market").exists(), "Failed/check-only launches must not touch runtime state")
            return result, output.getvalue(), errors.getvalue()

    def test_check_reports_astra_and_advisor_cli_without_model_calls(self):
        result, output, errors = self.check_launcher({"version": 1, "trading": "astra", "advisor": "haiku"})
        self.assertEqual(result, 0, errors)
        self.assertIn("shop/buyer=astra; advisor=haiku", output)
        self.assertIn("codex CLI available: True", output)
        self.assertIn("claude CLI available: True", output)
        self.assertIn("No model calls", output)

    def test_missing_codex_refuses_join_before_starting_kit(self):
        result, output, errors = self.check_launcher(
            {"version": 1, "trading": "astra", "advisor": "haiku"}, join=True, missing=("codex",))
        self.assertEqual(result, 1)
        self.assertIn("codex CLI available: False", output)
        self.assertIn("Required model CLI not found: codex", errors)

    def test_astra_luna_runtime_requires_only_codex(self):
        result, output, errors = self.check_launcher(
            {"version": 1, "trading": "astra", "advisor": "luna"}, missing=("claude",))
        self.assertEqual(result, 0, errors)
        self.assertIn("shop/buyer=astra; advisor=luna", output)
        self.assertIn("codex CLI available: True", output)
        self.assertNotIn("claude CLI", output)

    def test_luna_requires_codex_even_when_trading_uses_haiku(self):
        result, output, errors = self.check_launcher(
            {"version": 1, "trading": "haiku", "advisor": "luna"}, join=True, missing=("codex",))
        self.assertEqual(result, 1)
        self.assertIn("Required model CLI not found: codex", errors)

    def test_disabled_advisor_reports_disabled_and_ignores_its_unavailable_cli(self):
        result, output, errors = self.check_launcher(
            {"version": 1, "trading": "haiku", "advisor": "luna"},
            advisor_enabled=False, missing=("codex",))
        self.assertEqual(result, 0, errors)
        self.assertIn("shop/buyer=haiku; advisor=disabled", output)
        self.assertIn("claude CLI available: True", output)
        self.assertNotIn("codex CLI", output)

    def test_missing_profile_keeps_legacy_haiku_launcher(self):
        result, output, errors = self.check_launcher(missing=("codex",))
        self.assertEqual(result, 0, errors)
        self.assertIn("shop/buyer=haiku; advisor=haiku", output)
        self.assertNotIn("codex CLI", output)

    def test_pinned_kit_exactly_matches_upstream_blob(self):
        self.assertTrue(verify_kit().is_file())

    def test_identity_copy_preserves_existing_keys_and_never_overwrites_mismatch(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as d:
            folder = Path(d)
            old, new = folder / "week07", folder / "week08"
            old.mkdir()
            new.mkdir()
            for name in ("shop_key.pem", "buyer_key.pem"):
                write_key(old / name)
            self.assertEqual(len(identity(old, new)), 2)
            self.assertFalse(list(new.iterdir()))
            identity(old, new, copy=True)
            for name in ("shop_key.pem", "buyer_key.pem"):
                self.assertEqual((old / name).read_bytes(), (new / name).read_bytes())
                self.assertEqual((new / name).stat().st_mode & 0o777, 0o600)
            (new / "shop_key.pem").unlink()
            write_key(new / "buyer_key.pem")
            with self.assertRaises(ValueError):
                identity(old, new, copy=True)
            self.assertFalse((new / "shop_key.pem").exists())

    def test_missing_source_key_never_generates_an_identity(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as d:
            folder = Path(d)
            old, new = folder / "old", folder / "new"
            old.mkdir()
            new.mkdir()
            write_key(old / "shop_key.pem")
            with self.assertRaises(FileNotFoundError):
                identity(old, new, copy=True)
            self.assertFalse(list(new.iterdir()))
