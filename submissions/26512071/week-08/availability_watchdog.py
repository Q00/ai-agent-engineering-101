"""Minutely availability watchdog for the FairFurniture-Week08-Round3 scheduled task (run by its own task).

    .venv\\Scripts\\pythonw.exe submissions/26512071/week-08/availability_watchdog.py

Starts the exact main task only when no owned supervisor exists. Ownership = the runtime's named mutex is held,
or runtime-status.json's supervisor_pid is alive with the recorded creation time. It never kills, stops or
signals anything; a live owner with a stale heartbeat is logged only. Market/API/pilot state is never read.
Silent: no alerts, no network, no model calls. Writes logs/always-on-recovery/watchdog-health.json (atomic)
and watchdog.log (UTC, one line per change or action, rotated to one .1 file).
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "logs" / "always-on-recovery"
STATUS = HERE / "logs" / "runtime-status.json"
MAIN_TASK = "FairFurniture-Week08-Round3"
RUNTIME_MUTEX = "Global\\fair-furniture-market-runtime"
WATCHDOG_MUTEX = "Local\\fair-furniture-availability-watchdog"
PAUSE_FILE = "WATCHDOG_PAUSE"

STALE_S = 180                   # runtime refreshes updated_at every 30 s
MIN_START_GAP_S = 110           # at most one start per ~2 watchdog ticks
IDENTITY_SLACK_S = 5
LOG_MAX = 256_000
CMD_TIMEOUT_S = 30
NO_WINDOW = 0x08000000

START, NONE = "start", "none"

Observation = namedtuple("Observation", "task_state mutex_held supervisor_alive heartbeat_age_s paused "
                                        "last_start_age_s")


# --- pure decision

def decide(o):
    if o.paused:
        return NONE, "paused"
    if o.task_state is None:
        return NONE, "task_unknown"
    if o.task_state == "Disabled":
        return NONE, "task_disabled"
    if o.mutex_held or o.supervisor_alive:
        if o.heartbeat_age_s is None or o.heartbeat_age_s > STALE_S:
            return NONE, "owned_live_stale_heartbeat"
        return NONE, "owned_live"
    if o.last_start_age_s is not None and o.last_start_age_s < MIN_START_GAP_S:
        return NONE, "start_backoff"
    if o.task_state == "Running":
        return START, "running_no_owner_ignore_new"
    return START, "stopped_no_owner"


# --- status file

def _parse_ts(value):
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else None


def read_status(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def heartbeat_age(status, now):
    ts = _parse_ts(status.get("updated_at"))
    return None if ts is None else int((now - ts).total_seconds())


def same_identity(created, started):
    ts = _parse_ts(started)
    if created is None or ts is None:
        return False
    return abs((created - ts).total_seconds()) <= IDENTITY_SLACK_S


# --- Win32 probes (read-only)

_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def _k32():
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenMutexW.restype, k.OpenMutexW.argtypes = wintypes.HANDLE, (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    k.OpenProcess.restype, k.OpenProcess.argtypes = wintypes.HANDLE, (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    return k


def mutex_held(name=RUNTIME_MUTEX):
    k = _k32()
    h = k.OpenMutexW(_SYNCHRONIZE, False, name)
    if not h:
        return False                # ERROR_FILE_NOT_FOUND: no process holds a handle
    k.CloseHandle(h)
    return True


def _process_created(pid):
    from ctypes import wintypes
    k = _k32()
    h = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return None
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(h, *(ctypes.byref(t) for t in times)):
            return None
        ft = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return datetime.fromtimestamp((ft - 116444736000000000) / 1e7, tz=timezone.utc)
    finally:
        k.CloseHandle(h)


def supervisor_alive(status):
    pid = status.get("supervisor_pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    return same_identity(_process_created(pid), status.get("supervisor_started"))


def task_state(name=MAIN_TASK):
    cmd = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
           f"(Get-ScheduledTask -TaskName '{name}' -ErrorAction Stop).State.ToString()"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=CMD_TIMEOUT_S, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    state = r.stdout.strip()
    return state if r.returncode == 0 and state else None


def start_task(name=MAIN_TASK):
    try:
        return subprocess.run(["schtasks.exe", "/Run", "/TN", name], capture_output=True, timeout=CMD_TIMEOUT_S,
                              creationflags=NO_WINDOW).returncode
    except (OSError, subprocess.TimeoutExpired):
        return -1


# --- outputs

def utc_iso(now):
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_log(path, line, max_bytes=LOG_MAX):
    path = Path(path)
    try:
        if path.exists() and path.stat().st_size + len(line) + 2 > max_bytes:
            os.replace(path, path.with_name(path.name + ".1"))
        with open(path, "a", encoding="ascii", errors="replace") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _write_json(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".watchdog-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def run_once(out_dir=OUT, status_path=STATUS, now=None):
    now = now or datetime.now(timezone.utc)
    out_dir = Path(out_dir)
    health_path = out_dir / "watchdog-health.json"
    prev = read_status(health_path)
    last_start = _parse_ts(prev.get("last_start_ts", "").replace("Z", "+00:00") if prev.get("last_start_ts") else None)
    status = read_status(status_path)
    o = Observation(task_state=task_state(), mutex_held=mutex_held(), supervisor_alive=supervisor_alive(status),
                    heartbeat_age_s=heartbeat_age(status, now), paused=(out_dir / PAUSE_FILE).exists(),
                    last_start_age_s=None if last_start is None else (now - last_start).total_seconds())
    action, reason = decide(o)
    rec = {"ts": utc_iso(now), "action": action, "reason": reason, "task_state": o.task_state,
           "mutex_held": o.mutex_held, "supervisor_alive": o.supervisor_alive, "heartbeat_age_s": o.heartbeat_age_s,
           "supervisor_pid": status.get("supervisor_pid"), "last_start_ts": prev.get("last_start_ts"),
           "start_rc": None}
    if action == START:
        rec["start_rc"] = start_task(MAIN_TASK)
        rec["last_start_ts"] = rec["ts"]
    changed = (prev.get("reason"), prev.get("task_state")) != (reason, o.task_state)
    if action == START or changed:
        append_log(out_dir / "watchdog.log", " ".join(f"{k}={rec[k]}" for k in rec))
    _write_json(health_path, rec)
    return rec


def main():
    if os.name != "nt":
        return 2
    k = _k32()
    h = k.CreateMutexW(None, True, WATCHDOG_MUTEX)
    if not h or ctypes.get_last_error() == 183:     # ERROR_ALREADY_EXISTS: another watchdog tick is running
        return 3
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        run_once()
        return 0
    finally:
        k.CloseHandle(h)


if __name__ == "__main__":
    sys.exit(main())
