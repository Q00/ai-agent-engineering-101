"""Keeps the shop online: runs market_kit join and restarts it when the shop's public URL stops answering.

    set MARKET_OWNER=<claim code>          (PowerShell: $env:MARKET_OWNER="...")
    python keepalive.py

Why: on 11/05 the quick tunnel died at 16:27, and the kit's own watchdog hung while opening a new one, so the
shop was unreachable for 7 h while the buyer side kept running. This file checks the public agent card from
outside the kit every CHECK_S seconds; after MISSES failures in a row (or if the kit exits) it kills the kit
with its whole process tree and starts it again. It also keeps Windows from sleeping while it runs.
The claim code is read from the environment only; it is never written to a file or a log.
"""
import ctypes
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
VENV = REPO / ".venv" / "Scripts"
CMD = [str(VENV / "python.exe"), "-X", "utf8", "scripts/market_kit.py", "join", "--dir",
       str(HERE.relative_to(REPO)).replace("\\", "/"), "--market", "https://market.ourolabs.ai",
       "--name", "choo-chu", "--tunnel"]
CHECK_S = 60
MISSES = 3              # 3 failed checks in a row (about 3 min) -> restart
START_GRACE_S = 240     # a fresh kit needs time to open the tunnel and register
URL_RE = re.compile(r"(?:reach it through the kit at|registered again as \S+ at) (https://\S+)")


def log(msg):
    line = f"{datetime.now():%m-%d %H:%M:%S} [keepalive] {msg}"
    print(line, flush=True)
    with open(HERE / "logs" / "keepalive.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def no_sleep():
    if os.name == "nt":                     # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)


def start():
    env = dict(os.environ, PYTHONUTF8="1", PATH=str(VENV) + os.pathsep + os.environ.get("PATH", ""))
    out = HERE / "logs" / f"kit-console-{datetime.now():%m%d-%H%M}.txt"
    f = open(out, "w", encoding="utf-8")
    p = subprocess.Popen(CMD, cwd=REPO, env=env, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    log(f"kit started, pid {p.pid}, console {out.name}")
    return p, out


def kill(p):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        p.kill()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def public_url(console):
    try:
        urls = URL_RE.findall(console.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return urls[-1] if urls else None


def card_ok(url):
    try:
        return httpx.get(url + "/.well-known/agent-card.json", timeout=15).status_code == 200
    except Exception:
        return False


def main():
    if not os.environ.get("MARKET_OWNER"):
        sys.exit("set MARKET_OWNER to your claim code first")
    p, console = start()
    started, misses = time.time(), 0
    try:
        while True:
            no_sleep()
            time.sleep(CHECK_S)
            if p.poll() is not None:
                log(f"kit exited (code {p.returncode}), starting it again")
            elif time.time() - started < START_GRACE_S:
                continue
            else:
                url = public_url(console)
                if url and card_ok(url):
                    misses = 0
                    continue
                misses += 1
                log(f"shop {url} not answering ({misses}/{MISSES})")
                if misses < MISSES:
                    continue
                log("restarting the kit")
                kill(p)
            p, console = start()
            started, misses = time.time(), 0
    except KeyboardInterrupt:
        log("stopped by Ctrl-C, stopping the kit")
        kill(p)


if __name__ == "__main__":
    main()
