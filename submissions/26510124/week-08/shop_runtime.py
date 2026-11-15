"""The kit starts the seller and an independently supervised Shop operator.

Operations configuration reloads without restarting the seller or market kit.
The archived advisor remains optional and takes precedence if explicitly
enabled: two analysts never run together. Analyst failures do not stop trading.
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
from shop_ops_policy import config_at as operations_config_at

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


def supervise(port, *, stop=None, launch=start, cleanup=stop_children, clock=time.monotonic,
              ops_config=None):
    stop = stop or threading.Event()
    enabled = config_at()["enabled"]
    ops_config = ops_config or operations_config_at
    children = []
    seller = launch("seller_r3.py", "--port", str(port))
    children.append(seller)
    analyst, next_start = None, 0
    operator, next_operator, ops_status = None, 0, None
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
            try:
                requested = ops_config()["enabled"]
                if type(requested) is not bool:
                    raise ValueError("explicit operations enable flag required")
                wanted = requested and not enabled
                status = ("blocked_by_legacy_advisor" if requested and enabled else
                          "enabled" if wanted else "disabled")
            except Exception:
                wanted, status = False, "invalid_config"
            if status != ops_status:
                print("[shop-runtime] operations " + status, flush=True)
                ops_status = status
            if operator is not None:
                exited = operator.poll() is not None
                if not wanted or exited:
                    try:
                        cleanup([operator])
                    except (OSError, subprocess.SubprocessError):
                        # Never start a second operator until the old child has
                        # been cleaned up; trading in the seller keeps running.
                        print("[shop-runtime] operations cleanup deferred", flush=True)
                    else:
                        children.remove(operator)
                        operator = None
                        next_operator = clock() + 60 if wanted and exited else 0
                        if wanted and exited:
                            print("[shop-runtime] operations exited; retry after 60s", flush=True)
            if wanted and operator is None and clock() >= next_operator:
                try:
                    operator = launch("shop_operations.py")
                    children.append(operator)
                except OSError:
                    next_operator = clock() + 60
                    print("[shop-runtime] operations unavailable; retry after 60s", flush=True)
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
