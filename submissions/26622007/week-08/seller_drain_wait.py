"""Request drain and wait, bounded; never sends a signal or restarts a process."""
import argparse
import json
import os
import time
from pathlib import Path

def quiet(status, pid, requested, now):
    return (status.get("pid") == pid and requested <= status.get("at", 0) <= now
            and now - status["at"] <= 10 and status.get("quiet") is True
            and status.get("flushed") is True
            and all(status.get(k) == 0 for k in ("inflight", "open", "held", "agreed", "late", "stale")))

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pid", required=True, type=int)
    ap.add_argument("--root", default=".market")
    ap.add_argument("--timeout", type=int, default=1200)
    a = ap.parse_args()
    if a.pid <= 1 or not 1 <= a.timeout <= 1800:
        ap.error("invalid pid or timeout (1..1800)")
    os.kill(a.pid, 0)
    root = Path(a.root)
    requested = time.time()
    marker = root / "seller.drain"
    if marker.exists() and marker.read_text().strip() != str(a.pid):
        ap.error("another drain marker exists")
    marker.write_text(str(a.pid))
    deadline = time.monotonic() + a.timeout
    while time.monotonic() < deadline:
        os.kill(a.pid, 0)
        try:
            status = json.loads((root / "seller_drain_status.json").read_text())
            if quiet(status, a.pid, requested, time.time()):
                print(json.dumps(status))
                return
        except (OSError, ValueError, TypeError):
            pass
        time.sleep(1)
    raise SystemExit("drain deadline reached; NO restart. Marker retained; operator may remove to reopen.")

if __name__ == "__main__":
    main()
