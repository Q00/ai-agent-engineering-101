"""Executable lifecycle regression tests; no model requests or market trades."""
from contextlib import nullcontext
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness


class HarnessExecutableTests(unittest.TestCase):
    def test_resolved_binary_survives_removal_of_launcher_and_empty_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "codex"
            launcher.symlink_to(sys.executable)
            empty = root / "empty"
            empty.mkdir()
            with patch.dict(os.environ, {"PATH": directory}), patch.object(harness, "_codex_executable", None):
                with patch.dict(os.environ, {"A2A_HARNESS_COMMAND": ""}):
                    executable = harness.command_for("seller")[0]
                    self.assertEqual(executable, str(Path(sys.executable).resolve()))
                    launcher.unlink()
                    self.assertEqual(harness.command_for("seller")[0], executable)
                    completed = subprocess.run([executable, "-c", "print('launcher-recovery-ok')"],
                                               cwd=empty, capture_output=True, text=True, check=True)
                    self.assertEqual(completed.stdout.strip(), "launcher-recovery-ok")

    def test_removed_cached_binary_is_replaced_by_current_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            stale = str(Path(directory) / "removed-binary")
            with patch.object(harness, "_codex_executable", stale), patch.object(
                    harness.shutil, "which", return_value=sys.executable):
                self.assertEqual(harness.codex_executable(), str(Path(sys.executable).resolve()))

    def test_missing_installation_does_not_return_a_stale_cached_path(self):
        with patch.object(harness, "_codex_executable", "/nonexistent/test-only-codex"), patch.object(
                harness.shutil, "which", return_value=None):
            self.assertEqual(harness.codex_executable(), "codex")

    def test_explicit_custom_harness_command_keeps_its_arguments(self):
        with patch.dict(os.environ, {"A2A_HARNESS_COMMAND": "'/test only/custom-cli' --flag value"}), patch.object(
                harness, "codex_executable") as resolver:
            command = harness.command_for("buyer")
            self.assertEqual(command[:3], ["/test only/custom-cli", "--flag", "value"])
            resolver.assert_not_called()

    def test_missing_process_fails_once_with_a_configuration_error(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = SimpleNamespace(write=Mock(), directory=Path(directory), role="test-only", run_id="test")
            with patch.object(harness, "invocation_slot", return_value=nullcontext()), patch.object(
                    harness.subprocess, "Popen", side_effect=FileNotFoundError("test-only")) as process:
                with self.assertRaisesRegex(harness.HarnessConfigurationError, "No model process was started"):
                    harness.Harness("seller", trace).decide({}, attempts=3)
                self.assertEqual(process.call_count, 1)
                trace.write.assert_any_call("harness_unavailable", reason_code="executable_missing")


if __name__ == "__main__":
    unittest.main()
