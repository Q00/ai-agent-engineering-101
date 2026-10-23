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
        rc = subprocess.call(cmd, cwd=str(REPO))
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
