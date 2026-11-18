"""11/12 ~23:45 (person): the rollout launches the analyst exactly like the watchdog (tools/watchdog.start_analyst):
DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, stdin / stdout / stderr DEVNULL. Popen and PowerShell are mocked.
    python tests/test_analyst_launch.py"""
import os
import subprocess
import sys
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
import db_safe_rollout as R  # noqa: E402
import watchdog as W  # noqa: E402


def test_same_launch():
    calls = []
    fake = lambda *a, **k: calls.append((a, k)) or mock.Mock(pid=4242)
    with mock.patch.object(subprocess, "Popen", fake), \
            mock.patch.object(subprocess, "run", lambda *a, **k: mock.Mock(stdout="111\n")), \
            mock.patch.object(R.time, "sleep", lambda s: None):
        old, pid = R.restart_analyst()
        W.start_analyst()
    assert old == ["111"] and pid == 4242
    (a1, k1), (a2, k2) = calls
    assert a1 == a2 and k1 == k2, (calls,)
    assert k1["creationflags"] == subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    assert k1["stdin"] == k1["stdout"] == k1["stderr"] == subprocess.DEVNULL and a1[0][-1] == "analyst_main.py"


if __name__ == "__main__":
    test_same_launch()
    print("ok")
