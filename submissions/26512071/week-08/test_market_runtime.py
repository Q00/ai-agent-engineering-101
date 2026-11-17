"""RED tests for market_runtime.py, the scheduled-task supervisor for the week-08 kit and its tunnel.

Run (from the repo root, repo venv):
    .venv\\Scripts\\python.exe -m unittest discover -s submissions/26512071/week-08 -p test_market_runtime.py -v

No network, no market, no cloudflared, no credentials. The process tests only touch processes they start.
"""
import ctypes
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import market_runtime as rt
except ImportError:                                 # RED stage: every test fails with one clear reason
    rt = None

WINDOWS = os.name == "nt"
LAUNCHER = HERE / "launch_market_runtime.ps1"
RUNTIME = HERE / "market_runtime.py"
PUBLIC = "https://fair-example.trycloudflare.com"


class Base(unittest.TestCase):
    def setUp(self):
        if rt is None:
            self.fail("market_runtime.py is not implemented yet")


# --- helpers for the process tests: ctypes only, and only on pids these tests created

if WINDOWS:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)


def _open(pid):
    return _k32.OpenProcess(0x00100000 | 0x0001, False, pid)      # SYNCHRONIZE | PROCESS_TERMINATE


def exited_within(pid, seconds):
    h = _open(pid)
    if not h:
        return True                                 # already gone
    try:
        return _k32.WaitForSingleObject(h, int(seconds * 1000)) == 0
    finally:
        _k32.CloseHandle(h)


def kill_own(pid):
    """Test cleanup if the implementation under test left our own child behind."""
    h = _open(pid)
    if h:
        _k32.TerminateProcess(h, 1)
        _k32.CloseHandle(h)


PARENT_WITH_GRANDCHILD = (
    "import subprocess, sys, time\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(g.pid, flush=True)\n"
    "time.sleep(60)\n"
)
SLEEPER = "import time; time.sleep(60)"


# --- pure lifecycle decisions

class PlanTests(Base):
    def test_all_alive_is_ok(self):
        self.assertEqual(rt.plan(tunnel_owned=True, tunnel_alive=True, kit_alive=True, port_free=False), rt.OK)

    def test_kit_died_keeps_healthy_tunnel(self):
        self.assertEqual(rt.plan(tunnel_owned=True, tunnel_alive=True, kit_alive=False, port_free=True),
                         rt.RESTART_KIT)

    def test_owned_tunnel_died_restarts_both(self):
        # the kit was joined with the old URL (--public-url), so it must restart with the new one
        for kit_alive in (True, False):
            self.assertEqual(rt.plan(tunnel_owned=True, tunnel_alive=False, kit_alive=kit_alive, port_free=True),
                             rt.RESTART_TUNNEL_AND_KIT)

    def test_port_held_by_someone_else_waits_never_kills(self):
        self.assertEqual(rt.plan(tunnel_owned=True, tunnel_alive=True, kit_alive=False, port_free=False),
                         rt.WAIT_PORT)
        self.assertEqual(rt.plan(tunnel_owned=False, tunnel_alive=None, kit_alive=False, port_free=False),
                         rt.WAIT_PORT)

    def test_external_tunnel_is_never_restarted(self):
        self.assertEqual(rt.plan(tunnel_owned=False, tunnel_alive=None, kit_alive=True, port_free=False), rt.OK)
        self.assertEqual(rt.plan(tunnel_owned=False, tunnel_alive=None, kit_alive=False, port_free=True),
                         rt.RESTART_KIT)


class BackoffTests(Base):
    def test_bounded_doubling(self):
        self.assertEqual([rt.backoff_delay(n) for n in range(5)], [0, 5, 10, 20, 40])
        self.assertEqual(rt.backoff_delay(50), 300)

    def test_nondecreasing(self):
        delays = [rt.backoff_delay(n) for n in range(30)]
        self.assertEqual(delays, sorted(delays))
        self.assertLessEqual(max(delays), 300)

    def test_stable_run_resets_failures(self):
        self.assertEqual(rt.next_failures(5, uptime_s=rt.STABLE_AFTER_S), 1)
        self.assertEqual(rt.next_failures(2, uptime_s=1.0), 3)


# --- exact children, no shell, no secrets on argv

class ArgvTests(Base):
    def test_tunnel_argv(self):
        self.assertEqual(rt.tunnel_args("C:/cf/cloudflared.exe", 8900),
                         ["C:/cf/cloudflared.exe", "tunnel", "--url", "http://127.0.0.1:8900",
                          "--protocol", "http2", "--no-autoupdate"])

    def test_kit_argv_exact_and_secret_free(self):
        os.environ["MARKET_OWNER"] = sentinel = "owner-sentinel-" + uuid.uuid4().hex
        self.addCleanup(os.environ.pop, "MARKET_OWNER", None)
        argv = rt.kit_args("py.exe", PUBLIC, 8900)
        self.assertEqual(argv, ["py.exe", "scripts/market_kit.py", "join", "--dir", "submissions/26512071/week-08",
                                "--market", "https://market.ourolabs.ai", "--name", "fair-furniture",
                                "--port", "8900", "--public-url", PUBLIC])
        self.assertNotIn("--owner", argv)
        self.assertFalse(any(sentinel in a for a in argv))

    def test_cli_modes(self):
        self.assertIsNone(rt.parse_args([]).public_url)              # owned tunnel
        self.assertEqual(rt.parse_args([]).port, 8900)
        self.assertEqual(rt.parse_args(["--public-url", PUBLIC]).public_url, PUBLIC)
        with self.assertRaises(SystemExit):
            rt.parse_args(["--public-url", "http://plain.example"])


class TunnelUrlTests(Base):
    def test_parses_quick_tunnel_banner(self):
        line = "2026-11-04T00:35:01Z INF |  https://brave-oak-lamp.trycloudflare.com                    |"
        self.assertEqual(rt.parse_tunnel_url(line), "https://brave-oak-lamp.trycloudflare.com")

    def test_ignores_api_host_and_noise(self):
        err = "ERR failed to request quick Tunnel: Post \"https://api.trycloudflare.com/tunnel\": EOF"
        self.assertIsNone(rt.parse_tunnel_url(err))
        self.assertIsNone(rt.parse_tunnel_url("INF Registered tunnel connection connIndex=0"))
        self.assertIsNone(rt.parse_tunnel_url("https://evil.example/?x=a.trycloudflare.com"))


class RegistrationTests(Base):
    def test_needs_both_roles_after_offset(self):
        with tempfile.TemporaryDirectory() as t:
            log = Path(t) / "join.log"
            old = ("09:00:00 [market] shop registered as fair-furniture, round 3\n"
                   "09:00:01 [market] buyer registered as fair-furniture, round 3\n")
            log.write_text(old, encoding="utf-8")
            offset = log.stat().st_size
            self.assertFalse(rt.registration_seen(log, offset))       # history must not count
            with open(log, "a", encoding="utf-8") as f:
                f.write("10:00:00 [market] shop registered as fair-furniture, round 3\n")
            self.assertFalse(rt.registration_seen(log, offset))
            with open(log, "a", encoding="utf-8") as f:
                f.write("10:00:02 [market] buyer registered as fair-furniture, round 3\n")
            self.assertTrue(rt.registration_seen(log, offset))


# --- observability: append-only events, atomic status, append-only kit logs

class ObservabilityTests(Base):
    def test_events_append_only_whitelisted(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "runtime-events.jsonl"
            path.write_text('{"event":"earlier"}\n', encoding="utf-8")
            ev = rt.EventLog(path)
            ev.emit("kit_start", pid=123, url=PUBLIC)
            ev.emit("kit_exit", pid=123, exit_code=1, owner="SECRET-CLAIM", env={"K": "SECRET-ENV"})
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], '{"event":"earlier"}')
            recs = [json.loads(x) for x in lines[1:]]
            self.assertEqual([r["event"] for r in recs], ["kit_start", "kit_exit"])
            for r in recs:
                self.assertIn("ts", r)
                self.assertEqual(r["supervisor_pid"], os.getpid())
            self.assertEqual(recs[1]["exit_code"], 1)
            self.assertNotIn("owner", recs[1])
            self.assertNotIn("env", recs[1])
            self.assertNotIn("SECRET", path.read_text(encoding="utf-8"))

    def test_status_atomic_and_whitelisted(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "runtime-status.json"
            rt.write_status(path, {"state": "running", "kit_pid": 1, "tunnel_url": PUBLIC})
            rt.write_status(path, {"state": "restarting", "kit_pid": 2, "tunnel_url": PUBLIC,
                                   "env": {"MARKET_OWNER": "SECRET"}, "owner": "SECRET"})
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["state"], "restarting")
            self.assertEqual(data["kit_pid"], 2)
            self.assertNotIn("env", data)
            self.assertNotIn("owner", data)
            self.assertEqual(sorted(p.name for p in Path(t).iterdir()), ["runtime-status.json"])

    def test_kit_streams_append_to_history(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "join.log").write_bytes(b"old out\n")
            (d / "join.err").write_bytes(b"old err\n")
            out, err = rt.open_kit_streams(d)
            try:
                out.write(b"new out\n")
                err.write(b"new err\n")
            finally:
                out.close()
                err.close()
            self.assertEqual((d / "join.log").read_bytes(), b"old out\nnew out\n")
            self.assertEqual((d / "join.err").read_bytes(), b"old err\nnew err\n")


# --- fail closed: singleton and port conflict

@unittest.skipUnless(WINDOWS, "named mutex is Windows-only")
class SingletonTests(Base):
    def test_production_name_is_global(self):
        self.assertTrue(rt.MUTEX_NAME.startswith("Global\\"))

    def test_second_acquire_refused_then_free_after_release(self):
        name = "Local\\fair-furniture-test-" + uuid.uuid4().hex
        first = rt.acquire_singleton(name)
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(rt.acquire_singleton(name))
            probe = ("import sys; sys.path.insert(0, sys.argv[1]); import market_runtime as rt; "
                     "sys.exit(0 if rt.acquire_singleton(sys.argv[2]) is None else 1)")
            r = subprocess.run([sys.executable, "-c", probe, str(HERE), name], timeout=60)
            self.assertEqual(r.returncode, 0, "another process must not get the mutex")
        finally:
            rt.release_singleton(first)
        again = rt.acquire_singleton(name)
        self.assertIsNotNone(again)
        rt.release_singleton(again)


class PortTests(Base):
    def test_listener_detected_and_preflight_fails_closed(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            for _ in range(3):   # repeated probes must not drain backlog=1 and flip to free
                self.assertTrue(rt.port_in_use(port))
                self.assertEqual(rt.preflight_conflict(port), "port_busy")
        finally:
            s.close()
        for _ in range(2):
            self.assertFalse(rt.port_in_use(port))
            self.assertIsNone(rt.preflight_conflict(port))


# --- ownership: Job Objects kill exactly the trees we started

@unittest.skipUnless(WINDOWS, "Job Objects are Windows-only")
class JobTests(Base):
    def _spawn(self, job, code):
        p = rt.spawn([sys.executable, "-c", code], job, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.addCleanup(kill_own, p.pid)
        return p

    def test_spawn_refuses_shell_strings(self):
        job = rt.JobObject()
        self.addCleanup(job.close)
        with self.assertRaises(TypeError):
            rt.spawn(f'"{sys.executable}" -c "pass"', job)

    def test_close_kills_child_and_grandchild_race_free(self):
        job = rt.JobObject()
        p = self._spawn(job, PARENT_WITH_GRANDCHILD)          # grandchild starts immediately
        grandchild = int(p.stdout.readline())
        self.addCleanup(kill_own, grandchild)
        self.assertFalse(exited_within(grandchild, 0.2))
        job.close()
        self.assertTrue(exited_within(p.pid, 10))
        self.assertTrue(exited_within(grandchild, 10))
        job.close()                                           # idempotent

    def test_closing_kit_job_keeps_tunnel_job(self):
        kit_job, tunnel_job = rt.JobObject(), rt.JobObject()
        self.addCleanup(tunnel_job.close)
        kit = self._spawn(kit_job, SLEEPER)
        tunnel = self._spawn(tunnel_job, SLEEPER)
        kit_job.close()
        self.assertTrue(exited_within(kit.pid, 10))
        self.assertFalse(exited_within(tunnel.pid, 0.5))
        self.assertIsNone(tunnel.poll())

    def test_unrelated_process_untouched(self):
        stranger = subprocess.Popen([sys.executable, "-c", SLEEPER])  # not in any runtime job
        self.addCleanup(kill_own, stranger.pid)
        job = rt.JobObject()
        self._spawn(job, SLEEPER)
        job.close()
        self.assertFalse(exited_within(stranger.pid, 0.5))


# --- static ownership/secret invariants of the sources

class SourceInvariantTests(unittest.TestCase):
    FORBIDDEN_KILL = ("taskkill", "Stop-Process", "pkill", "killall", "/IM ")

    def _text(self, path):
        self.assertTrue(path.exists(), f"{path.name} is not written yet")
        return path.read_text(encoding="utf-8")

    def test_runtime_has_no_global_kill_or_shell(self):
        src = self._text(RUNTIME)
        for bad in self.FORBIDDEN_KILL + ("shell=True", "os.system("):
            self.assertNotIn(bad, src)

    def test_launcher_imports_env_silently_and_runs_venv_python_in_foreground(self):
        src = self._text(LAUNCHER)
        self.assertIn(r".venv\Scripts\python.exe", src)
        self.assertIn("market_runtime.py", src)
        self.assertIn(r"'^\$env:", src)                       # only $env: assignment lines are imported
        self.assertIn("Out-Null", src)
        for bad in self.FORBIDDEN_KILL + ("Write-Host", "Write-Output", "Start-Transcript", "Set-PSDebug",
                                          "Start-Process", "--owner"):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()
