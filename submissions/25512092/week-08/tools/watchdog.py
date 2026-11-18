"""Process watchdog for the unattended run (person's decision 11/10 ~15:00). Every CHECK_S:
  analyst   analyst_main.py (the analyst, the cap brake, Telegram buttons and the buyer forum poster thread) missing
            -> started again here
  seller    seller_r3.py missing on MISS_N checks in a row -> the kit (market_kit join, seller + buyer) is killed with
            its process tree; keepalive.py starts it again (it holds the claim code; this file never sees it)
  buyer     no "[buyer]" line in the newest kit console for BUYER_SILENT_S -> the same kit restart
  keepalive missing -> urgent Telegram only (it needs the claim code from the person's shell)
Every restart: an urgent Telegram. More than MAX_RESTARTS restarts of one name within an hour -> no more restarts of it,
an urgent Telegram. Log: logs/watchdog.log; state: logs/watchdog_state.json.
    python tools/watchdog.py [--once]
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
VENV_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), ".venv", "Scripts", "python.exe")
LOG = os.path.join(HERE, "logs", "watchdog.log")
STATE = os.path.join(HERE, "logs", "watchdog_state.json")
CHECK_S, MISS_N, BUYER_SILENT_S, MAX_RESTARTS = 60, 2, 600, 3


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def procs():
    """[(pid, ppid, command line)] of the python processes."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
                          "ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }"],
                         capture_output=True, timeout=60).stdout or b""
    try:                                          # the console code page (cp949) or UTF-8; never fail on a byte
        out = out.decode("utf-8")
    except UnicodeDecodeError:
        out = out.decode("cp949", "replace")
    rows = []
    for ln in out.splitlines():
        p = ln.split("\t", 2)
        if len(p) == 3 and p[0].isdigit():
            rows.append((int(p[0]), int(p[1]) if p[1].isdigit() else 0, p[2]))
    return rows


def find(rows, pat):
    return [r for r in rows if pat in r[2]]


def buyer_last(now=None):
    """Seconds since the last "[buyer]" line of the newest kit console, or None (no console)."""
    files = sorted(glob.glob(os.path.join(HERE, "logs", "kit-console-*.txt")), key=os.path.getmtime)
    if not files:
        return None
    now = now or time.time()
    with open(files[-1], "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 400_000))
        tail = f.read().decode("utf-8", "replace").splitlines()
    for ln in reversed(tail):
        m = re.match(r"(\d\d):(\d\d):(\d\d) \[buyer\]", ln)
        if m:
            t = datetime.now().replace(hour=int(m[1]), minute=int(m[2]), second=int(m[3]), microsecond=0).timestamp()
            if t > now + 60:                     # a line from before midnight
                t -= 86400
            return now - t
    return now - os.path.getmtime(files[-1])


def state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f)


def allowed(s, name, now):
    """True if `name` may restart now (<= MAX_RESTARTS in the last hour); records the restart."""
    r = [t for t in s.setdefault("restarts", {}).get(name, []) if now - t < 3600]
    if len(r) >= MAX_RESTARTS:
        s["restarts"][name] = r
        return False
    s["restarts"][name] = r + [round(now)]
    return True


def urgent(key, text):
    import alerts
    alerts.send("urgent", key, text, log=log)


def start_analyst():
    """Detached in a new process group, stdin / stdout / stderr to DEVNULL (11/12 ~23:45: also used by the rollouts)."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([VENV_PY, "-X", "utf8", "analyst_main.py"], cwd=HERE, creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def restart_kit(rows):
    """Kill the kit: the top market_kit process (its parent not a market_kit one) with its tree, never keepalive
    (11/10 15:06: killing the keepalive's child tree took keepalive down too). keepalive starts the kit again."""
    kit = find(rows, "market_kit.py join")
    kit_pids = {r[0] for r in kit}
    top = [r for r in kit if r[1] not in kit_pids and "keepalive.py" not in r[2]]
    for pid, _, _ in top:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    return bool(top)


def start_keepalive():
    """keepalive.py from the repo root with MARKET_OWNER from the Windows user environment (HKCU\\Environment), put
    only into the child's environment: never printed, logged or written. False when it is not set there."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            code = winreg.QueryValueEx(k, "MARKET_OWNER")[0]
    except OSError:
        return False
    if not code:
        return False
    repo = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([VENV_PY, "-X", "utf8", "submissions/25512092/week-08/keepalive.py"], cwd=repo,
                     env=dict(os.environ, MARKET_OWNER=code), creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    return True


def check(s, rows, now, buyer_age):
    """One round; returns the actions taken (for the log / tests)."""
    acts = []
    stopped = s.setdefault("stopped", {})
    if not find(rows, "keepalive.py"):
        acts.append("keepalive missing")
        if stopped.get("keepalive"):
            pass
        elif now - s.get("keepalive_start_at", 0) < 180:
            acts.append("keepalive start pending")
        elif allowed(s, "keepalive", now):
            if start_keepalive():
                s["keepalive_start_at"] = round(now)
                acts.append("keepalive restarted")
                urgent(f"watchdog_restart:keepalive:{int(now)}", "keepalive(가게 kit 관리) 꺼짐 -> 자동 재시작 (가게도 다시 켜짐)")
            else:
                urgent("watchdog_keepalive", "keepalive.py가 꺼져 있고 MARKET_OWNER(사용자 환경 변수)가 없어 재시작 불가: 사람이 "
                       "keepalive를 다시 켜야 함")
        else:
            stopped["keepalive"] = round(now)
            urgent("watchdog_stop:keepalive", f"keepalive가 1시간에 {MAX_RESTARTS}번 넘게 죽음: 자동 재시작 멈춤, 확인 필요")
    if not find(rows, "analyst_main.py"):
        acts.append("analyst missing")
        if stopped.get("analyst"):
            pass
        elif allowed(s, "analyst", now):
            start_analyst()
            acts.append("analyst restarted")
            urgent(f"watchdog_restart:analyst:{int(now)}", "분석관(analyst_main, poster 포함) 프로세스 꺼짐 -> 자동 재시작")
        else:
            stopped["analyst"] = round(now)
            urgent("watchdog_stop:analyst", f"분석관 프로세스가 1시간에 {MAX_RESTARTS}번 넘게 죽음: 자동 재시작 멈춤, 확인 필요")
    seller_ok = bool(find(rows, "seller_r3.py"))
    s["seller_miss"] = 0 if seller_ok else s.get("seller_miss", 0) + 1
    why = None
    if s["seller_miss"] >= MISS_N:
        why = ("seller", f"seller_r3.py 프로세스 없음 ({s['seller_miss']}회 연속)")
    elif buyer_age is not None and buyer_age > BUYER_SILENT_S:
        why = ("buyer", f"buyer 로그 {buyer_age / 60:.0f}분 동안 없음")
    if why and find(rows, "keepalive.py"):
        name, txt = why
        acts.append(f"{name} down: {txt}")
        if stopped.get(name):
            pass
        elif now - s.get("kit_restart_at", 0) < 300:
            acts.append("kit restart pending")       # give the last restart 5 min
        elif allowed(s, name, now):
            restart_kit(rows)
            s["kit_restart_at"], s["seller_miss"] = round(now), 0
            acts.append(f"kit restarted ({name})")
            urgent(f"watchdog_restart:{name}:{int(now)}", f"{name} 꺼짐 ({txt}) -> 가게 kit 재시작 (keepalive가 다시 켬)")
        else:
            stopped[name] = round(now)
            urgent(f"watchdog_stop:{name}", f"{name}이(가) 1시간에 {MAX_RESTARTS}번 넘게 죽음: 자동 재시작 멈춤, 확인 필요 ({txt})")
    kit = find(rows, "market_kit.py join")           # 11/10 ~15:15 (person): every kit (re)start, whoever did it
    kp = {r[0] for r in kit}
    top = sorted(r[0] for r in kit if r[1] not in kp)
    if top and top != s.get("kit_pids"):
        if s.get("kit_pids") is not None:
            s["kit_starts"] = [t for t in s.get("kit_starts", []) if now - t < 3600] + [round(now)]
            acts.append(f"kit started again (pid {top})")
        s["kit_pids"] = top
    ks = [t for t in s.get("kit_starts", []) if now - t < 3600]
    if len(ks) > MAX_RESTARTS and now - s.get("kit_alerted", 0) > 3600:
        s["kit_alerted"] = round(now)
        acts.append(f"kit restarted {len(ks)} times in an hour")
        urgent("watchdog_kit_flapping", f"가게 kit가 1시간에 {len(ks)}번 재시작됨 (기준 {MAX_RESTARTS}번 초과): 터널·seller·buyer 확인 필요")
    for name in list(stopped):                       # an hour later the counter starts over
        if now - stopped[name] > 3600:
            del stopped[name]
    return acts


def main():
    log("start")
    while True:
        now = time.time()
        try:
            s = state()
            acts = check(s, procs(), now, buyer_last(now))
            save(s)
            if acts:
                log("; ".join(acts))
        except Exception as e:
            log(f"check failed: {type(e).__name__}: {e}")
        if "--once" in sys.argv:
            return
        time.sleep(CHECK_S)


if __name__ == "__main__":
    main()
