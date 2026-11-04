"""py -m unittest discover -s redesign/tests -t .     (from week-08)"""
import ast
import importlib
import unittest
from pathlib import Path

from redesign import guard

BANNED_MODULES = {"subprocess", "anthropic", "claude_agent_sdk"}


class Isolation(unittest.TestCase):
    def test_import_of_live_harness_is_blocked(self):
        for name in ("core", "agents.model", "outer", "seller"):
            with self.assertRaises(guard.IsolationError):
                importlib.import_module(name)

    def test_writes_stay_in_work(self):
        with self.assertRaises(guard.IsolationError):
            guard.write(guard.HARNESS / "HARNESS.md", "x")
        with self.assertRaises(guard.IsolationError):
            guard.write("../config.json", "x")

    def test_read_is_for_harness_files_only(self):
        self.assertIn("HARNESS", guard.read("HARNESS.md")[:200])
        with self.assertRaises(guard.IsolationError):
            guard.read("redesign/config.json")
        with self.assertRaises(guard.IsolationError):
            guard.read("../week-07/README.md")

    def test_source_never_imports_the_harness_or_claude(self):
        live = guard.harness_modules()
        for f in guard.OWN.rglob("*.py"):
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                else:
                    continue
                for n in names:
                    top = n.split(".")[0]
                    self.assertNotIn(top, live, f"{f.name} imports {n}")
                    if top == "subprocess" and f.name == "guard.py":
                        continue                       # guard.git_log only, checked below
                    self.assertNotIn(top, BANNED_MODULES, f"{f.name} imports {n}")

    def test_the_one_outside_command_is_git(self):
        tree = ast.parse((guard.OWN / "guard.py").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and getattr(n.func.value, "id", "") == "subprocess"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0].elts[0].value, "git")
        self.assertEqual(calls[0].args[0].elts[1].value, "log")

    def test_no_claude_cli(self):
        for f in guard.OWN.rglob("*.py"):
            if f.name == Path(__file__).name:
                continue
            src = f.read_text(encoding="utf-8")
            self.assertNotIn("claude -p", src, f.name)
            self.assertNotIn("claude.exe", src, f.name)


if __name__ == "__main__":
    unittest.main()
