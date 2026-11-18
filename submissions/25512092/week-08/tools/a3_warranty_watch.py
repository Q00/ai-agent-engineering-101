"""Recompute the high-price A-grade low-break warranty list (depts/a3_warranty.py) every hour, to the round end
(person 11/13 ~10:40). A failed run keeps the last list. Log: logs/a3_warranty.log (count, added, removed).
    python tools/a3_warranty_watch.py [--once]
"""
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from depts import a3_warranty  # noqa: E402

LOG = os.path.join(HERE, "logs", "a3_warranty.log")
EVERY = 3600
END = datetime(2026, 11, 17, 23, 59).timestamp()


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def step(path=None, **kw):
    old = set((a3_warranty.rules(path).get("skus") or {}))
    try:
        new = a3_warranty.refresh(path=path, **kw)
    except Exception as e:
        log(f"refresh failed, last list kept ({len(old)}): {type(e).__name__}: {e}")
        return None
    log(f"{len(new)} SKUs; added {sorted(set(new) - old)}; removed {sorted(old - set(new))}")
    return new


def main():
    while time.time() < END + 3600:
        step()
        if "--once" in sys.argv:
            return
        time.sleep(EVERY)


if __name__ == "__main__":
    main()
