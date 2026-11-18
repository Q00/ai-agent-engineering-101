"""Per-hour rates of every buyer on the board over a recent window (person 11/09 ~14:20: "시간당 주문" in reports is
counted over a recent window, never over the time since the account joined - the board's orders are round-3 totals
and 'joined' can be before round 3). Snapshots of the whole buyer list: logs/board_buyers.jsonl (tools/accept_watch.py
saves one each check, every 30 min; any report may save one too).
"""
import json
import os
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(HERE, "logs", "board_buyers.jsonl")
KEEP = 400
KEYS = ("orders", "deals", "surplus", "served", "posts")
MIN_S = 1200


def save(B, now=None, path=None):
    path = path or SNAP
    row = {"ts": round(now or time.time()), "b": {b["name"]: {k: b.get(k) or 0 for k in KEYS} for b in B}}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-(KEEP - 1):]
    except OSError:
        lines = []
    lines.append(json.dumps(row) + "\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def rates(B, now=None, window_s=3 * 3600, path=None):
    """({name: {key: per hour}}, hours) from the snapshot nearest to now - window_s (at least MIN_S old), or ({}, 0)."""
    now = now or time.time()
    best = None
    try:
        with open(path or SNAP, encoding="utf-8") as f:
            for ln in f:
                r = json.loads(ln)
                if now - r["ts"] >= MIN_S and (best is None or abs(now - window_s - r["ts"]) < abs(now - window_s - best["ts"])):
                    best = r
    except (OSError, ValueError):
        pass
    if not best:
        return {}, 0
    h = (now - best["ts"]) / 3600
    out = {}
    for b in B:
        o = best["b"].get(b["name"])
        if o:
            out[b["name"]] = {k: ((b.get(k) or 0) - o[k]) / h for k in KEYS}
    return out, h
