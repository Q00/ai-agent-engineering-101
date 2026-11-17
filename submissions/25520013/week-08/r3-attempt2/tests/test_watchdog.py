"""The loop watchdog (live/watchdog.sh), one pass (ONCE=1) against a temp dir with a fake a2ctl.sh and a fake tmux:
a missing process is started, a stale pid file is removed, WATCHDOG_OFF stops all action, the restart cap holds."""

import os
import pathlib
import subprocess
import time

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "live" / "watchdog.sh"
NAMES = ["memd", "memfeed", "operator-shop", "operator-buyer", "analyst", "agents"]

# Fake a2ctl: "running" when the pid file exists (as kill -0 on a reused pid would say); start records the call.
FAKE_A2CTL = """#!/bin/bash
P="$W/a2/pids"
case "$1" in
  status) for n in memd memfeed operator operator-shop operator-buyer analyst agents; do
            [ -f "$P/$n.pid" ] && echo "$n running pid $(cat "$P/$n.pid")" || echo "$n not running"; done ;;
  start) echo "$2 gap=$A2_ANALYST_MIN_GAP_S" >> "$W/started"; echo 999 > "$P/$2.pid"; echo "$2 started pid 999" ;;
esac
"""

# Fake tmux: list-windows prints $W/windows; new-window records its arguments.
FAKE_TMUX = """#!/bin/bash
case "$1" in
  list-windows) cat "$W/windows" 2>/dev/null ;;
  new-window) echo "$*" >> "$W/tmux_calls" ;;
esac
"""


def _setup(tmp_path, windows="kit\nforum\nrunner\nboardsnap\n"):
    w = tmp_path / "w"
    (w / "a2" / "pids").mkdir(parents=True)
    (w / "windows").write_text(windows)
    proc = tmp_path / "proc"
    proc.mkdir()
    a2ctl = tmp_path / "a2ctl.sh"
    a2ctl.write_text(FAKE_A2CTL)
    tmux = tmp_path / "tmux"
    tmux.write_text(FAKE_TMUX)
    tmux.chmod(0o755)
    return w, proc, a2ctl, tmux


def _run(tmp_path, w, proc, a2ctl, tmux):
    env = dict(os.environ, W=str(w), CODE=str(tmp_path), A2CTL=str(a2ctl), TMUX_BIN=str(tmux), PROC=str(proc),
               ONCE="1", SLEEP_S="0")
    env.pop("A2_ANALYST_MIN_GAP_S", None)
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""
    return r


def _ours(w, proc, name, pid, cmd):
    """A live process of ours: pid file plus a /proc cmdline that names the module."""
    (w / "a2" / "pids" / f"{name}.pid").write_text(f"{pid}\n")
    (proc / str(pid)).mkdir()
    (proc / str(pid) / "cmdline").write_bytes(cmd.replace(" ", "\0").encode())


def _started(w):
    f = w / "started"
    return [ln.split()[0] for ln in f.read_text().splitlines()] if f.exists() else []


def test_missing_process_started(tmp_path):
    w, proc, a2ctl, tmux = _setup(tmp_path)
    for i, n in enumerate(NAMES):
        if n != "memfeed":
            _ours(w, proc, n, 100 + i, f"python3 -m kernel.{n} --root x" if n != "memd" else "python -m memd.server")
    _run(tmp_path, w, proc, a2ctl, tmux)
    assert _started(w) == ["memfeed"]
    assert "gap=600" in (w / "started").read_text()
    log = (w / "a2" / "logs" / "watchdog.log").read_text()
    assert "memfeed not running, started" in log and "heartbeat" in log
    assert not (w / "tmux_calls").exists()  # boardsnap present: no tmux action


def test_stale_pid_removed_and_started(tmp_path):
    w, proc, a2ctl, tmux = _setup(tmp_path)
    for i, n in enumerate(NAMES):
        _ours(w, proc, n, 100 + i, f"python3 -m kernel.{n}" if n != "memd" else "python -m memd.server")
    (proc / "102" / "cmdline").write_bytes(b"/usr/sbin/sshd\0-D")  # operator-shop's pid now belongs to sshd
    _run(tmp_path, w, proc, a2ctl, tmux)
    assert _started(w) == ["operator-shop"]
    assert "operator-shop removed stale pid file" in (w / "a2" / "logs" / "watchdog.log").read_text()


def test_boardsnap_recreated_only(tmp_path):
    w, proc, a2ctl, tmux = _setup(tmp_path, windows="kit\nforum\nrunner\n")
    for i, n in enumerate(NAMES):
        _ours(w, proc, n, 100 + i, f"python3 -m kernel.{n}" if n != "memd" else "python -m memd.server")
    _run(tmp_path, w, proc, a2ctl, tmux)
    calls = (w / "tmux_calls").read_text().splitlines()
    assert calls == [f"new-window -d -t market -n boardsnap bash {w}/.market/boardsnap-loop.sh"]
    assert _started(w) == []


def test_watchdog_off_and_stop_file(tmp_path):
    w, proc, a2ctl, tmux = _setup(tmp_path, windows="")
    (w / "a2" / "WATCHDOG_OFF").touch()
    _run(tmp_path, w, proc, a2ctl, tmux)
    assert _started(w) == [] and not (w / "tmux_calls").exists()
    assert not (w / "a2" / "logs" / "watchdog.log").exists()
    (w / "a2" / "WATCHDOG_OFF").unlink()
    (w / "a2" / "stop").mkdir()
    (w / "a2" / "stop" / "analyst").touch()
    _run(tmp_path, w, proc, a2ctl, tmux)
    assert "analyst" not in _started(w) and len(_started(w)) == 5


def test_restart_cap(tmp_path):
    w, proc, a2ctl, tmux = _setup(tmp_path)
    for i, n in enumerate(NAMES):
        if n != "agents":
            _ours(w, proc, n, 100 + i, f"python3 -m kernel.{n}" if n != "memd" else "python -m memd.server")
    now = int(time.time())
    (w / "a2" / "watchdog_state").write_text(
        f"{now - 7200} agents start\n" + "".join(f"{now - 60 * k} agents start\n" for k in (1, 2)))
    _run(tmp_path, w, proc, a2ctl, tmux)  # two in the hour (the old one pruned): third start allowed
    assert _started(w) == ["agents"]
    for _ in range(2):
        (w / "a2" / "pids" / "agents.pid").unlink(missing_ok=True)  # it died again
        _run(tmp_path, w, proc, a2ctl, tmux)
    assert _started(w) == ["agents"]
    log = (w / "a2" / "logs" / "watchdog.log").read_text()
    assert log.count("giving up") == 1
