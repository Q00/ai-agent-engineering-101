"""Keep the market kit running until the deadline.

    python keepalive.py --name YAHO1212

The kit exits on the first network error it does not handle: on 2026-10-22 at
23:16 a dropped connection raised httpx.ConnectTimeout, the kit died, and its
cloudflared tunnel died with it -- the shop and the buyer left the market and
nothing brought them back. This wraps `market_kit.py join` in a loop: whenever
the kit exits, wait (longer each time it dies quickly, reset after a long run),
kill any orphaned tunnel, and join again with the same name and key, which
keeps the shop's trades and reviews. Every restart is logged.

The claim code is read from MARKET_OWNER, never written to disk here.
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LOG = HERE / "logs" / "keepalive.log"


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [keepalive] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def kill_tunnels():
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"],
                       capture_output=True)


KIT_LOG = HERE / "logs" / "market-kit.log"
STUCK_AFTER = 8          # consecutive orders with no result line before a restart
NO_RESULT = "printed no result line"


def watch(proc):
    """Wait for the kit, but also restart it when it is alive and useless.

    On 2026-10-22 from 19:56 to 23:16 the kit stayed up while every one of 587
    orders ended "market_buyer printed no result line" -- the buyer produced no
    output at all, not even a traceback, within the same second. A supervisor
    that only waits for the kit to exit never notices that. This one tails
    market-kit.log and kills the kit after STUCK_AFTER of those in a row; the
    loop in main() then starts a fresh one, which is what cleared it last time.
    """
    pos = KIT_LOG.stat().st_size if KIT_LOG.exists() else 0
    streak = 0
    while proc.poll() is None:
        time.sleep(15)
        if not KIT_LOG.exists():
            continue
        with KIT_LOG.open("r", encoding="utf-8", errors="ignore") as f:
            f.seek(pos)
            new = f.read()
            pos = f.tell()
        for line in new.splitlines():
            if NO_RESULT in line:
                streak += 1
            elif "ledger #" in line:
                streak = 0
        if streak >= STUCK_AFTER:
            log(f"{streak} orders in a row with no result line; restarting the kit")
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
            proc.wait(timeout=30)
            return "stuck"
    return proc.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="YAHO1212")
    ap.add_argument("--market", default="https://market.ourolabs.ai")
    a = ap.parse_args()
    if not os.environ.get("MARKET_OWNER"):
        sys.exit("set MARKET_OWNER to the claim code first")

    cmd = [sys.executable, str(REPO / "scripts" / "market_kit.py"), "join",
           "--dir", str(HERE), "--market", a.market, "--name", a.name, "--tunnel"]
    backoff, restarts = 10, 0
    while True:
        kill_tunnels()                      # a tunnel left by a dead kit is useless
        started = time.time()
        log(f"starting kit (restart #{restarts})")
        proc = subprocess.Popen(cmd, cwd=str(REPO))
        rc = watch(proc)
        ran = time.time() - started
        log(f"kit exited with {rc} after {ran / 60:.1f} min")
        restarts += 1
        backoff = 10 if ran > 600 else min(backoff * 2, 300)
        log(f"rejoining in {backoff}s")
        time.sleep(backoff)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped by Ctrl+C")
        kill_tunnels()
