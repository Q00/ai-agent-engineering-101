"""The kit starts this supervisor: seller plus an explicitly enabled analyst.

The current policy disables the analyst, so only the seller starts. The retained
optional implementation shares the user-systemd service when enabled and stops
with it; an analyst failure never stops serving the shop.
"""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from sales_advisor import config_at

ROOT = Path(__file__).resolve().parent


def start(script, *args):
    return subprocess.Popen([sys.executable, str(ROOT / script), *args], cwd=ROOT,
                            start_new_session=True)


def stop_children(children, grace=5):
    # killpg also reaches a Claude CLI child while the analyst is evaluating.
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace
    for child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def supervise(port, *, stop=None, launch=start, cleanup=stop_children, clock=time.monotonic):
    stop = stop or threading.Event()
    enabled = config_at()["enabled"]
    children = []
    seller = launch("seller_r3.py", "--port", str(port))
    children.append(seller)
    analyst, next_start = None, 0
    print("[shop-runtime] seller started; advisor " + ("enabled" if enabled else "disabled"), flush=True)
    try:
        while not stop.is_set():
            code = seller.poll()
            if code is not None:
                return code
            if enabled and analyst is not None and analyst.poll() is not None:
                print("[shop-runtime] advisor exited; retry after 60s", flush=True)
                # Reap any descendant left by an interrupted model call.
                cleanup([analyst])
                children.remove(analyst)
                analyst, next_start = None, clock() + 60
            if enabled and analyst is None and clock() >= next_start:
                try:
                    analyst = launch("sales_advisor.py")
                    children.append(analyst)
                except OSError:
                    next_start = clock() + 60
                    print("[shop-runtime] advisor unavailable; retry after 60s", flush=True)
            stop.wait(1)
    finally:
        cleanup(children)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    return supervise(args.port, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
