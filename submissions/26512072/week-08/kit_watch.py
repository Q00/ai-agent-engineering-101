"""Watches the market kit from outside and restarts it when it hangs.

    python kit_watch.py        (runs forever; one line per action in logs/kit-watch.log)

Why (11-12): since 11-11 17:58 the kit's quick tunnel stopped answering about every 30 minutes. Each time the kit
opened a new one, and three times the kit then hung without a log line: 13 min, 21 min and 124 min (01:30-03:35 UTC on
11-12), while the market's ledger kept its usual ~15,000 rows an hour. Each new tunnel also left its old cloudflared
running (11 of them on 11-12). The kit itself is read-only (scripts/), so this script does two things every minute:
  1. no new line in logs/market-kit.log for STALL seconds (the buyer alone writes several a minute): kill the kit
     process; the tmux wrapper loop starts it again in 10 s, with a fresh tunnel and a fresh join;
  2. cloudflared processes for the kit's port other than the newest: kill them (their tunnels are dead).
"""
import os
import signal
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT_LOG = HERE / "logs" / "market-kit.log"
LOG = HERE / "logs" / "kit-watch.log"
STALL = 480          # seconds without a kit log line
GRACE = 300          # after a restart, give the kit this long to come back


def say(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def procs(pattern):
    """(pid, seconds running, args) of my processes whose command line contains pattern."""
    out = subprocess.run(["ps", "-u", str(os.getuid()), "-o", "pid=,ppid=,etimes=,args="], capture_output=True,
                         text=True).stdout
    rows = []
    for line in out.splitlines():
        pid, ppid, age, args = line.split(None, 3)
        if pattern in args and "kit_watch" not in args:
            rows.append((int(pid), int(age), args))
    return rows


def descendants(pid):
    out = subprocess.run(["ps", "-u", str(os.getuid()), "-o", "pid=,ppid="], capture_output=True, text=True).stdout
    kids = {}
    for line in out.splitlines():
        c, p = map(int, line.split())
        kids.setdefault(p, []).append(c)
    found, todo = [], [pid]
    while todo:
        for c in kids.get(todo.pop(), []):
            found.append(c)
            todo.append(c)
    return found


def kit_pids():
    return [p for p, _, a in procs("market_kit.py join") if "while true" not in a and "tmux" not in a]


def tidy_tunnels():
    # cloudflared for the kit's port only; the newest one is the live tunnel
    rows = sorted(procs("cloudflared tunnel --no-autoupdate --url http://127.0.0.1:"), key=lambda r: -r[1])
    for pid, _, args in rows[:-1]:                  # oldest first; keep the youngest
        try:
            os.kill(pid, signal.SIGTERM)
            say(f"killed old tunnel {pid}: {args.split('--url')[-1].strip()}")
        except ProcessLookupError:
            pass


def main():
    say("watching")
    last_restart = time.time()      # the kit may have just started with the log still stale (11-12 07:12)
    while True:
        try:
            idle = time.time() - KIT_LOG.stat().st_mtime
            pids = kit_pids()
            if idle > STALL and time.time() - last_restart > GRACE and pids:
                for pid in pids:                    # the kit and its seller and buyer (the tunnel is tidied below)
                    for q in [pid] + descendants(pid):
                        try:
                            os.kill(q, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                say(f"kit log idle {idle / 60:.0f} min: killed kit {pids}; the wrapper restarts it")
                last_restart = time.time()
            tidy_tunnels()
        except Exception as e:                      # never die: this is the thing that keeps the shop up
            say(f"error {type(e).__name__}: {e}")
        time.sleep(60)


if __name__ == "__main__":
    main()
