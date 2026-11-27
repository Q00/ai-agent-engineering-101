"""Scheduled-task supervisor for the week-08 market kit and its cloudflared quick tunnel.

    .venv\\Scripts\\python.exe submissions/26512071/week-08/market_runtime.py [--public-url https://...] [--port 8900]
    .venv\\Scripts\\python.exe submissions/26512071/week-08/market_runtime.py --probe-only

launch_market_runtime.ps1 runs this in the foreground with the market env already in the process environment.
Secrets reach the kit only through that inherited environment: never argv, events, status or this module's output.
- Singleton: a named mutex. A second instance logs singleton_held and exits 3.
- Fail closed: if something else listens on the port at startup -> port_busy, exit 4. Nothing this process did not
  start is ever touched.
- Each child tree (tunnel, kit) lives in its own kill-on-close Job Object. The child is created suspended, assigned,
  then resumed, so no grandchild can escape. If this process dies, the OS closes the jobs and both trees end.
- Owned tunnel dies -> stop kit, new tunnel, kit restarts with the new URL. Kit dies -> only the kit restarts.
  An external --public-url tunnel is never started, stopped or restarted.
- logs/runtime-events.jsonl (append-only, whitelisted fields), logs/runtime-status.json (atomic, whitelisted),
  kit stdout/stderr appended to ~/agent-market/join.log and join.err, cloudflared output to logs/runtime-tunnel.log.
"""
import argparse
import ctypes
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DIR = "submissions/26512071/week-08"
MARKET = "https://market.ourolabs.ai"
NAME = "fair-furniture"
PORT = 8900
CLOUDFLARED = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
MUTEX_NAME = "Global\\fair-furniture-market-runtime"
EVENTS = HERE / "logs" / "runtime-events.jsonl"
STATUS = HERE / "logs" / "runtime-status.json"
TUNNEL_LOG = HERE / "logs" / "runtime-tunnel.log"
JOIN_DIR = Path.home() / "agent-market"

POLL_S = 2.0
STABLE_AFTER_S = 600                                # a child that ran this long starts the backoff over
BACKOFF_BASE_S, BACKOFF_MAX_S = 5, 300
TUNNEL_URL_TIMEOUT_S = 60
TUNNEL_CONNECT_GRACE_S = 20
REGISTER_WARN_S = 180
HEARTBEAT_S = 30
TUNNEL_LOG_MAX = 20_000_000                         # bytes; past this cloudflared output is drained but not kept
MAX_LOOP_ERRORS = 10

OK, RESTART_KIT, RESTART_TUNNEL_AND_KIT, WAIT_PORT = "ok", "restart_kit", "restart_tunnel_and_kit", "wait_port"

EVENT_FIELDS = ("role", "pid", "exit_code", "restarts", "delay_s", "url", "reason", "port", "action",
                "uptime_s", "error_type", "winerror")
STATUS_FIELDS = ("state", "reason", "supervisor_pid", "supervisor_started", "kit_pid", "kit_started", "tunnel_pid",
                 "tunnel_started", "tunnel_url", "tunnel_owned", "port", "restarts", "tunnel_restarts", "registered",
                 "updated_at")
TUNNEL_URL = re.compile(r"https://(?!api\.)[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com(?![\w.-])")
REGISTERED = ("[market] shop registered", "[market] buyer registered")


# --- pure lifecycle decisions

def plan(tunnel_owned, tunnel_alive, kit_alive, port_free):
    if tunnel_owned and not tunnel_alive:
        return RESTART_TUNNEL_AND_KIT               # the kit was joined with the old URL
    if kit_alive:
        return OK
    return RESTART_KIT if port_free else WAIT_PORT


def backoff_delay(failures):
    if failures <= 0:
        return 0
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** (failures - 1))


def next_failures(failures, uptime_s):
    return 1 if uptime_s >= STABLE_AFTER_S else failures + 1


def tunnel_args(cloudflared, port):
    return [cloudflared, "tunnel", "--url", f"http://127.0.0.1:{port}", "--protocol", "http2", "--no-autoupdate"]


def kit_args(python, public_url, port):
    return [python, "scripts/market_kit.py", "join", "--dir", DIR, "--market", MARKET, "--name", NAME,
            "--port", str(port), "--public-url", public_url]


def kit_env(python):
    env = dict(os.environ)
    env["PATH"] = str(Path(python).parent) + os.pathsep + env.get("PATH", "")   # agent.json runs bare "python"
    return env


def parse_tunnel_url(line):
    m = TUNNEL_URL.search(line)
    return m.group(0) if m else None


def registration_seen(join_log, offset):
    """Both roles registered in what the kit wrote after `offset` (history does not count)."""
    try:
        with open(join_log, "rb") as f:
            if os.fstat(f.fileno()).st_size < offset:
                offset = 0                          # replaced by a fresh file
            f.seek(offset)
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return False
    return all(marker in text for marker in REGISTERED)


def _https_url(value):
    u = urlparse(value)
    if u.scheme != "https" or not u.hostname or any(c.isspace() for c in value):
        raise argparse.ArgumentTypeError("must be an https:// URL")
    return value.rstrip("/")


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="market_runtime.py",
                                description="Supervise the week-08 market kit and (unless --public-url) its tunnel.")
    p.add_argument("--public-url", type=_https_url, default=None,
                   help="existing external tunnel URL; it is never restarted (default: own a quick tunnel)")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--cloudflared", default=CLOUDFLARED)
    p.add_argument("--python", default=sys.executable, help="python for the kit (default: this interpreter)")
    p.add_argument("--join-dir", type=Path, default=JOIN_DIR, help="where join.log / join.err are appended")
    p.add_argument("--exit-with-parent", action="store_true", help="shut down when the launching process exits")
    p.add_argument("--parent-pid", type=int, default=None,
                   help="pid watched by --exit-with-parent (default: os.getppid(), which is the venv wrapper)")
    p.add_argument("--probe-only", action="store_true", help="check singleton, port and paths; start nothing")
    return p.parse_args(argv)


# --- observability

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]


class EventLog:
    """Append-only JSONL. Only whitelisted fields are written; a failed write never stops the supervisor."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def emit(self, event, **fields):
        rec = {"event": event, "ts": now_iso(), "supervisor_pid": os.getpid()}
        rec.update((k, _safe(fields[k])) for k in EVENT_FIELDS if k in fields)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass


def write_status(path, data):
    """Whitelisted fields, written to a temp file in the same directory and swapped in with os.replace."""
    path = Path(path)
    rec = {k: _safe(data[k]) for k in STATUS_FIELDS if k in data}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".runtime-status-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:                 # a reader has the old file open; retry briefly
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _write_pid(path, pid):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(f"{int(pid)}\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_pid_files(join_dir, log_dir, kit_pid, supervisor_pid):
    """Numeric PID mirrors for the existing health monitor (kit.pid, trading-supervisor.pid); nothing else."""
    _write_pid(Path(join_dir) / "kit.pid", kit_pid)
    _write_pid(Path(log_dir) / "trading-supervisor.pid", supervisor_pid)


def open_kit_streams(join_dir):
    join_dir = Path(join_dir)
    join_dir.mkdir(parents=True, exist_ok=True)
    out = open(join_dir / "join.log", "ab")
    try:
        err = open(join_dir / "join.err", "ab")
    except OSError:
        out.close()
        raise
    return out, err


# --- singleton and port

def port_in_use(port, host="127.0.0.1"):
    # exclusive bind probe: never consumes a listener's backlog (connect_ex did, so a
    # saturated listener read as free); any bind failure fails closed as busy
    with socket.socket() as s:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((host, port))
        except OSError:
            return True
        return False


def preflight_conflict(port):
    return "port_busy" if port_in_use(port) else None


# --- Win32: named mutex, Job Objects, suspended spawn (explicit restype/argtypes: no 64-bit handle truncation)

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200

if os.name == "nt":
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)   # own instance: argtypes do not leak to other users

    def _fn(name, restype, *argtypes):
        f = getattr(_k32, name)
        f.restype, f.argtypes = restype, argtypes
        return f

    _CloseHandle = _fn("CloseHandle", wintypes.BOOL, wintypes.HANDLE)
    _CreateMutexW = _fn("CreateMutexW", wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    _ReleaseMutex = _fn("ReleaseMutex", wintypes.BOOL, wintypes.HANDLE)
    _CreateJobObjectW = _fn("CreateJobObjectW", wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCWSTR)
    _SetInformationJobObject = _fn("SetInformationJobObject", wintypes.BOOL,
                                   wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _AssignProcessToJobObject = _fn("AssignProcessToJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE)
    _TerminateJobObject = _fn("TerminateJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
    _OpenProcess = _fn("OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _OpenThread = _fn("OpenThread", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _ResumeThread = _fn("ResumeThread", wintypes.DWORD, wintypes.HANDLE)
    _WaitForSingleObject = _fn("WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
    _CreateToolhelp32Snapshot = _fn("CreateToolhelp32Snapshot", wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)

    class _THREADENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                    ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG),
                    ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]

    _Thread32First = _fn("Thread32First", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32))
    _Thread32Next = _fn("Thread32Next", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32))

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                       "OtherOperationCount", "ReadTransferCount",
                                                       "WriteTransferCount", "OtherTransferCount")]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _INVALID_HANDLE = ctypes.c_void_p(-1).value
    _ERROR_ALREADY_EXISTS = 183
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9
    _PROCESS_TERMINATE, _PROCESS_SET_QUOTA, _SYNCHRONIZE = 0x0001, 0x0100, 0x00100000
    _TH32CS_SNAPTHREAD, _THREAD_SUSPEND_RESUME = 0x00000004, 0x0002


def _winerror():
    return ctypes.WinError(ctypes.get_last_error())


def acquire_singleton(name=MUTEX_NAME):
    """Mutex handle if this process now owns `name`, else None (held elsewhere, or not openable)."""
    ctypes.set_last_error(0)
    h = _CreateMutexW(None, True, name)
    err = ctypes.get_last_error()
    if not h:
        return None
    if err == _ERROR_ALREADY_EXISTS:
        _CloseHandle(h)
        return None
    return h


def release_singleton(handle):
    if handle:
        _ReleaseMutex(handle)
        _CloseHandle(handle)


class JobObject:
    """Kill-on-close job: close() (or this process dying) ends every process in it, and nothing else."""

    def __init__(self):
        h = _CreateJobObjectW(None, None)           # not inheritable: children never hold it open
        if not h:
            raise _winerror()
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _SetInformationJobObject(h, _JobObjectExtendedLimitInformation, ctypes.byref(info),
                                        ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            _CloseHandle(h)
            raise ctypes.WinError(err)
        self.handle = h

    def assign(self, pid):
        h = _OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not h:
            raise _winerror()
        try:
            if not _AssignProcessToJobObject(self.handle, h):
                raise _winerror()
        finally:
            _CloseHandle(h)

    def close(self):
        h, self.handle = self.handle, None
        if h:
            _TerminateJobObject(h, 1)
            _CloseHandle(h)


def _resume(pid):
    """Resume the threads of our own suspended child (subprocess does not keep its thread handle)."""
    snap = _CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snap or snap == _INVALID_HANDLE:
        raise _winerror()
    resumed = 0
    try:
        te = _THREADENTRY32()
        te.dwSize = ctypes.sizeof(te)
        more = _Thread32First(snap, ctypes.byref(te))
        while more:
            if te.th32OwnerProcessID == pid:
                th = _OpenThread(_THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                if th:
                    try:
                        if _ResumeThread(th) != 0xFFFFFFFF:
                            resumed += 1
                    finally:
                        _CloseHandle(th)
            more = _Thread32Next(snap, ctypes.byref(te))
    finally:
        _CloseHandle(snap)
    if not resumed:
        raise OSError("suspended child has no resumable thread")


def spawn(argv, job, **popen_kwargs):
    """Start argv (a list, never a shell string) suspended, put it in `job`, then let it run."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a non-empty list of str")
    if "shell" in popen_kwargs:
        raise TypeError("shell is not accepted")
    flags = popen_kwargs.pop("creationflags", 0) | CREATE_SUSPENDED
    proc = subprocess.Popen(argv, creationflags=flags, **popen_kwargs)
    try:
        job.assign(proc.pid)                        # Popen holds the process handle: the pid cannot be reused
        _resume(proc.pid)
    except BaseException:
        proc.kill()                                 # our own suspended child, through its own handle
        proc.wait()
        raise
    return proc


def _open_parent(pid=None):
    h = _OpenProcess(_SYNCHRONIZE, False, os.getppid() if pid is None else pid)
    return h or None


def _has_exited(handle):
    return _WaitForSingleObject(handle, 0) == 0


# --- supervisor

class Child:
    def __init__(self, role, proc, job):
        self.role, self.proc, self.job = role, proc, job
        self.t0, self.started = time.monotonic(), now_iso()
        self.url = None
        self.url_ready = threading.Event()
        self.connected = threading.Event()

    def uptime(self):
        return round(time.monotonic() - self.t0, 1)

    def stop(self):
        self.job.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def _drain_tunnel(child, log_path):
    """Keep reading cloudflared's stderr (a full pipe would stall it); take the first quick-tunnel URL."""
    log = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not (log_path.exists() and log_path.stat().st_size > TUNNEL_LOG_MAX):
            log = open(log_path, "ab")
    except OSError:
        log = None
    try:
        for raw in child.proc.stderr:
            if log:
                try:
                    log.write(raw)
                    log.flush()
                except OSError:
                    pass
            line = raw.decode("utf-8", "replace")
            if child.url is None:
                child.url = parse_tunnel_url(line)
                if child.url:
                    child.url_ready.set()
            if "Registered tunnel connection" in line:
                child.connected.set()
    except (OSError, ValueError):
        pass
    finally:
        child.url_ready.set()                       # wake the waiter on EOF too; it checks child.url
        if log:
            log.close()


class Supervisor:
    def __init__(self, args, events, status_path=STATUS, tunnel_log=TUNNEL_LOG):
        self.a, self.ev, self.status_path, self.tunnel_log = args, events, Path(status_path), Path(tunnel_log)
        self.owned = args.public_url is None
        self.url = args.public_url
        self.tunnel = self.kit = None
        self.failures = {"tunnel": 0, "kit": 0}
        self.retry_at = {"tunnel": 0.0, "kit": 0.0}
        self.starts = {"tunnel": 0, "kit": 0}
        self.registered, self.reg_offset, self.reg_warned = False, 0, False
        self.waiting_port = False
        self.stop = threading.Event()
        self.parent = None
        self.started = now_iso()
        self._last_status, self._status_at = None, 0.0

    def run(self):
        self.ev.emit("supervisor_start", port=self.a.port, url=self.url,
                     reason="owned_tunnel" if self.owned else "external_tunnel")
        errors = 0
        try:
            while not self.stop.is_set():
                try:
                    self.step()
                    errors = 0
                except Exception as e:
                    errors += 1
                    self.ev.emit("loop_error", error_type=type(e).__name__, winerror=getattr(e, "winerror", None))
                    if errors >= MAX_LOOP_ERRORS:
                        return 1
                self.publish("running")
                if self.parent and _has_exited(self.parent):
                    self.ev.emit("parent_exited")
                    break
                self.stop.wait(POLL_S)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
        return 0

    def step(self):
        self._reap()
        kit_alive = self.kit is not None
        tunnel_alive = (self.tunnel is not None) if self.owned else None
        port_free = not kit_alive and not port_in_use(self.a.port)
        action = plan(self.owned, tunnel_alive, kit_alive, port_free)
        now = time.monotonic()
        if action == RESTART_TUNNEL_AND_KIT:
            if self.kit:
                self._stop_child("kit", "tunnel_restart")
            if now >= self.retry_at["tunnel"]:
                self._start_tunnel()
        elif action == RESTART_KIT:
            if self.waiting_port:
                self.waiting_port = False
                self.ev.emit("port_free", port=self.a.port)
            if now >= self.retry_at["kit"]:
                self._start_kit()
        elif action == WAIT_PORT:
            if not self.waiting_port:                 # log once per wait; never touch the holder
                self.waiting_port = True
                self.ev.emit("port_wait", port=self.a.port, reason="port_busy", action=WAIT_PORT)
        else:
            self._check_registration()

    def _reap(self):
        for role in ("tunnel", "kit"):
            child = getattr(self, role)
            if child is None or child.proc.poll() is None:
                continue
            up = child.uptime()
            child.stop()                              # closing the job ends anything it left behind
            setattr(self, role, None)
            if role == "tunnel":
                self.url = None
            self.ev.emit(f"{role}_exit", role=role, pid=child.proc.pid, exit_code=child.proc.returncode, uptime_s=up)
            self._schedule_retry(role, up)

    def _schedule_retry(self, role, uptime):
        self.failures[role] = next_failures(self.failures[role], uptime)
        delay = backoff_delay(self.failures[role])
        self.retry_at[role] = time.monotonic() + delay
        self.ev.emit("restart_scheduled", role=role, delay_s=delay, restarts=self.starts[role])

    def _spawn_failed(self, role, e):
        self.ev.emit("spawn_failed", role=role, error_type=type(e).__name__, winerror=getattr(e, "winerror", None))
        self._schedule_retry(role, 0.0)

    def _stop_child(self, role, reason):
        child = getattr(self, role)
        if child is None:
            return
        self.ev.emit(f"{role}_stop", role=role, pid=child.proc.pid, reason=reason, uptime_s=child.uptime())
        child.stop()
        setattr(self, role, None)
        if role == "kit":
            self.registered = False

    def _start_tunnel(self):
        self.starts["tunnel"] += 1
        job = None
        try:
            job = JobObject()
            proc = spawn(tunnel_args(self.a.cloudflared, self.a.port), job, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=CREATE_NEW_PROCESS_GROUP)
        except Exception as e:
            if job:
                job.close()
            self._spawn_failed("tunnel", e)
            return
        child = Child("tunnel", proc, job)
        threading.Thread(target=_drain_tunnel, args=(child, self.tunnel_log), daemon=True).start()
        self.ev.emit("tunnel_start", role="tunnel", pid=proc.pid, restarts=self.starts["tunnel"] - 1)
        deadline = time.monotonic() + TUNNEL_URL_TIMEOUT_S
        while not child.url_ready.wait(0.5) and not self.stop.is_set() and time.monotonic() < deadline:
            pass
        if child.url is None:
            reason = ("stopping" if self.stop.is_set() else
                      "exited" if proc.poll() is not None else "no_url_timeout")
            self.ev.emit("tunnel_no_url", role="tunnel", pid=proc.pid, reason=reason, exit_code=proc.poll())
            child.stop()
            if not self.stop.is_set():
                self._schedule_retry("tunnel", child.uptime())
            return
        deadline = time.monotonic() + TUNNEL_CONNECT_GRACE_S
        while not child.connected.wait(0.5) and not self.stop.is_set() and time.monotonic() < deadline:
            pass
        self.tunnel, self.url = child, child.url
        self.retry_at["kit"] = 0.0                    # a new URL: the kit should rejoin now
        self.ev.emit("tunnel_url", role="tunnel", pid=proc.pid, url=child.url,
                     reason="connected" if child.connected.is_set() else "connect_unconfirmed")

    def _start_kit(self):
        if not self.url:
            return
        self.starts["kit"] += 1
        job = None
        try:
            out, err = open_kit_streams(self.a.join_dir)
        except OSError as e:
            self._spawn_failed("kit", e)
            return
        try:
            offset = os.fstat(out.fileno()).st_size
            job = JobObject()
            proc = spawn(kit_args(self.a.python, self.url, self.a.port), job, cwd=str(REPO),
                         env=kit_env(self.a.python), stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                         creationflags=CREATE_NEW_PROCESS_GROUP)
        except Exception as e:
            if job:
                job.close()
            self._spawn_failed("kit", e)
            return
        finally:
            out.close()                               # the kit has its own handles
            err.close()
        self.kit = Child("kit", proc, job)
        try:
            write_pid_files(self.a.join_dir, HERE / "logs", proc.pid, os.getpid())
        except Exception as e:                        # the kit is ours and running: never spawn a duplicate
            self.ev.emit("pidfile_failed", role="kit", pid=proc.pid, error_type=type(e).__name__,
                         winerror=getattr(e, "winerror", None))
        self.registered, self.reg_offset, self.reg_warned = False, offset, False
        self.ev.emit("kit_start", role="kit", pid=proc.pid, url=self.url, port=self.a.port,
                     restarts=self.starts["kit"] - 1)

    def _check_registration(self):
        if self.kit is None or self.registered:
            return
        if registration_seen(Path(self.a.join_dir) / "join.log", self.reg_offset):
            self.registered = True
            self.ev.emit("registered", role="kit", pid=self.kit.proc.pid, url=self.url)
        elif not self.reg_warned and self.kit.uptime() > REGISTER_WARN_S:
            self.reg_warned = True
            self.ev.emit("registration_pending", role="kit", pid=self.kit.proc.pid, uptime_s=self.kit.uptime())

    def publish(self, state, force=False):
        k, t = self.kit, self.tunnel
        snap = {"state": state, "reason": "port_busy" if self.waiting_port else None,
                "supervisor_pid": os.getpid(), "supervisor_started": self.started,
                "kit_pid": k.proc.pid if k else None, "kit_started": k.started if k else None,
                "tunnel_pid": t.proc.pid if t else None, "tunnel_started": t.started if t else None,
                "tunnel_url": self.url, "tunnel_owned": self.owned, "port": self.a.port,
                "restarts": max(0, self.starts["kit"] - 1), "tunnel_restarts": max(0, self.starts["tunnel"] - 1),
                "registered": self.registered}
        now = time.monotonic()
        if not force and snap == self._last_status and now - self._status_at < HEARTBEAT_S:
            return
        self._last_status, self._status_at = dict(snap), now
        snap["updated_at"] = now_iso()
        try:
            write_status(self.status_path, snap)
        except OSError:
            pass

    def shutdown(self):
        self.ev.emit("stopping")
        self._stop_child("kit", "shutdown")
        if self.tunnel:
            self._stop_child("tunnel", "shutdown")
        self.publish("stopped", force=True)
        self.ev.emit("stopped")


def _install_signals(stop):
    def handler(signum, frame):
        stop.set()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, handler)
            except (OSError, ValueError):
                pass


def main(argv=None):
    args = parse_args(argv)
    if os.name != "nt":
        print("market_runtime.py: Windows only", file=sys.stderr)
        return 2
    events = EventLog(EVENTS)
    mutex = acquire_singleton(MUTEX_NAME)
    if mutex is None:
        if args.probe_only:
            print(json.dumps({"singleton": "held"}))
        else:
            events.emit("singleton_held")
        return 3
    try:
        conflict = preflight_conflict(args.port)
        missing = [label for label, path in (("python_missing", args.python),
                                             ("kit_missing", REPO / "scripts" / "market_kit.py"),
                                             ("cloudflared_missing", args.cloudflared if args.public_url is None
                                              else None))
                   if path is not None and not Path(path).exists()]
        if args.probe_only:
            print(json.dumps({"singleton": "free", "port": args.port, "conflict": conflict, "missing": missing,
                              "tunnel": "owned" if args.public_url is None else "external"}))
            return 0 if not conflict and not missing else 4
        if conflict:
            events.emit(conflict, port=args.port, action="exit")
            return 4
        if missing:
            events.emit("config_error", reason=",".join(missing))
            return 2
        sup = Supervisor(args, events)
        if args.exit_with_parent:
            sup.parent = _open_parent(args.parent_pid) if args.parent_pid is not None else _open_parent()
        _install_signals(sup.stop)
        try:
            return sup.run()
        except Exception as e:
            events.emit("crashed", error_type=type(e).__name__, winerror=getattr(e, "winerror", None))
            return 1
    finally:
        release_singleton(mutex)


if __name__ == "__main__":
    sys.exit(main())
