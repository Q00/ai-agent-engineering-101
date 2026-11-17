"""RED test: --exit-with-parent must watch the launching PowerShell, not the venv python.exe wrapper.

Reproduced: Stop-ScheduledTask ended PowerShell, but .venv\\Scripts\\python.exe (a launcher) stayed alive and
os.getppid() pointed at it, so the supervisor, kit and tunnel survived and the next start hit singleton_held.

Run (from the repo root, repo venv):
    .venv\\Scripts\\python.exe -m unittest discover -s submissions/26512071/week-08 -p test_runtime_parent.py -v

No network, no market, no cloudflared, no credentials. Only processes this test starts are touched.
"""
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import market_runtime as rt

PUBLIC = "https://fair-example.trycloudflare.com"
SLEEPER = "import time; time.sleep(60)"


@unittest.skipUnless(os.name == "nt", "parent handle is Windows-only")
class ExplicitParentTests(unittest.TestCase):
    def test_parent_pid_is_parsed_and_watched_instead_of_wrapper(self):
        # the launching PowerShell stand-in: our own process, not our parent and not the venv wrapper
        launcher = subprocess.Popen([sys.executable, "-c", SLEEPER])
        self.addCleanup(launcher.wait)
        self.addCleanup(launcher.kill)
        self.assertNotEqual(launcher.pid, os.getppid())

        # 1. CLI: --parent-pid is an optional integer
        try:
            args = rt.parse_args(["--exit-with-parent", "--parent-pid", str(launcher.pid)])
        except SystemExit:
            self.fail("--parent-pid is not accepted")
        self.assertEqual(args.parent_pid, launcher.pid)
        self.assertIsNone(rt.parse_args(["--exit-with-parent"]).parent_pid)
        with self.assertRaises(SystemExit):
            rt.parse_args(["--parent-pid", "not-a-pid"])

        # 2. main hands exactly that pid to _open_parent and puts the handle on the supervisor
        seen = {}

        class FakeSupervisor:
            def __init__(self, a, events):
                self.a, self.parent, self.stop = a, None, threading.Event()

            def run(self):
                seen["parent"], seen["python"] = self.parent, self.a.python
                return 0

        opened = mock.Mock(return_value="launcher-handle")
        with mock.patch.object(rt, "acquire_singleton", return_value="mutex"), \
                mock.patch.object(rt, "release_singleton"), \
                mock.patch.object(rt, "preflight_conflict", return_value=None), \
                mock.patch.object(rt, "EventLog"), \
                mock.patch.object(rt, "_install_signals"), \
                mock.patch.object(rt, "Supervisor", FakeSupervisor), \
                mock.patch.object(rt, "_open_parent", opened):
            code = rt.main(["--public-url", PUBLIC, "--exit-with-parent", "--parent-pid", str(launcher.pid)])
        self.assertEqual(code, 0)
        opened.assert_called_once_with(launcher.pid)
        self.assertEqual(seen["parent"], "launcher-handle")
        self.assertEqual(seen["python"], sys.executable)        # --python default (the kit's venv) is unchanged

        # 3. real seam: the handle tracks the given pid, not os.getppid()
        h = rt._open_parent(launcher.pid)
        self.assertTrue(h)
        try:
            self.assertFalse(rt._has_exited(h))
            launcher.kill()                                      # our own child only
            launcher.wait(timeout=10)
            self.assertTrue(rt._has_exited(h))
        finally:
            rt._CloseHandle(h)


if __name__ == "__main__":
    unittest.main()
