"""Patch 3.5 watch (read-only, not part of the shop): one GET /api/board?round=3 per check.

    python tools/patch35_watch.py          (runs until the round ends; started in the background)

1. Is buyer ALL computed the 3.4 way (forum percentile) or the 3.5 way (forum rating x 0.3)? The first board
   that fits 3.5 better -> one alert with our Class rank, total and the 3 players above and below.
2. Our buyer's forum rating (Wilson lower bound) dropped 0.01 or more since the last check -> alert (someone
   disliked us).
3. inventory.json gets dislike_power_unit -> one alert.
Checks every 10 minutes from 17:45 to 19:00 on 11/06 (3.5 is announced for 18:00), else every hour.
State: logs/patch35_watch.json; log: logs/patch35_watch.log. Alerts go through alerts.py (file, console,
Telegram when configured).
"""
import json
import os
import sys
import time
from datetime import datetime

import httpx

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import alerts  # noqa: E402

STATE = os.path.join(HERE, "logs", "patch35_watch.json")
LOG = os.path.join(HERE, "logs", "patch35_watch.log")
END = datetime(2026, 11, 17, 23, 59).timestamp()
DENSE = (datetime(2026, 11, 6, 17, 45).timestamp(), datetime(2026, 11, 6, 19, 0).timestamp())
ME, PLAYER = "choo-chu", "Choo-chu"
DROP = 0.01


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {msg}\n")


def pct(vals, v):
    lo = sum(x < v for x in vals)
    eq = sum(x == v for x in vals)
    return (lo + 0.5 * (eq - 1)) / (len(vals) - 1)


def fit(b):
    """Mean |error| of buyer ALL under 3.4 and 3.5, and our row."""
    B = [x for x in b["buyers"] if (x.get("deals") or 0) > 0]
    col = {k: [x.get(k) or 0 for x in B] for k in ("saved", "forum", "deals", "posts")}
    e34 = e35 = 0.0
    for x in B:
        p = {k: pct(col[k], x.get(k) or 0) for k in col}
        rest = .5 * p["saved"] + .1 * p["deals"] + .1 * p["posts"]
        e34 += abs(rest + .3 * p["forum"] - x["all"])
        e35 += abs(rest + .3 * (x.get("forum") or 0) - x["all"])
    return e34 / len(B), e35 / len(B)


def class_view(b):
    P = sorted([p for p in b["players"] if p.get("student")], key=lambda p: -(p.get("student_total") or 0))
    i = next(k for k, p in enumerate(P) if p["player"] == PLAYER)
    near = [f"{k + 1}. {P[k]['player']} {P[k]['student_total']:.3f}" for k in range(max(0, i - 3), min(len(P), i + 4))]
    return i + 1, len(P), P[i]["student_total"], near


def main():
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {"switched": None, "forum": None, "unit_seen": False}
    while time.time() < END:
        try:
            b = httpx.get("https://market.ourolabs.ai/api/board", params={"round": 3}, timeout=30).json()
            e34, e35 = fit(b)
            me = next(x for x in b["buyers"] if x["name"] == ME)
            rank, n, total, near = class_view(b)
            log(f"err 3.4 {e34:.4f} / 3.5 {e35:.4f}; buyer ALL {me['all']}, forum {me['forum']}; class {rank}/{n}, "
                f"total {total:.3f}")
            if not st["switched"] and e35 < e34:
                st["switched"] = time.time()
                alerts.send("urgent", "patch35_live",
                            f"Patch 3.5 is on the board (err 3.5 {e35:.4f} vs 3.4 {e34:.4f}). Choo-chu class rank "
                            f"{rank}/{n}, total {total:.3f}, buyer ALL {me['all']}.\n" + "\n".join(near), log=log)
            f = me.get("forum")
            if f is not None and st["forum"] is not None and st["forum"] - f >= DROP:
                alerts.send("urgent", f"forum_drop:{round(f, 3)}",
                            f"Our buyer forum rating fell {st['forum']:.3f} -> {f:.3f} (a dislike?). Class rank "
                            f"{rank}/{n}, total {total:.3f}.", log=log)
            if f is not None:
                st["forum"] = f
        except Exception as e:
            log(f"check failed: {type(e).__name__}")
        try:
            inv = json.load(open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8"))
            if not st["unit_seen"] and inv.get("dislike_power_unit") is not None:
                st["unit_seen"] = True
                alerts.send("info", "dislike_power_unit",
                            f"inventory.json dislike_power_unit = {inv['dislike_power_unit']:,}", log=log)
        except (OSError, ValueError):
            pass
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        now = time.time()
        nxt = now + (600 if DENSE[0] <= now < DENSE[1] else 3600)
        if now < DENSE[0] < nxt:
            nxt = DENSE[0]                             # be on time for the 18:00 switch
        time.sleep(max(60, nxt - now))
    log("stopped: round over")


if __name__ == "__main__":
    main()
