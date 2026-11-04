"""One-time fill of ledger/our_results.json (11/4, when ledger.count_ours was added).

The collector counts our shop's result entries from where it stopped, so the file starts empty and
the seller analysis would see an hour of it. This reads the ledger back HOURS hours, up to the seq
the collector last read (so nothing is counted twice), and counts those entries into the file.

    cd ~/market/round3 && MARKET_DIR=$PWD/.market ~/market/.venv/bin/python deploy/backfill_our_results.py [--hours 24]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common   # noqa: E402
import ledger   # noqa: E402

STEP = 20000     # seqs per step back while looking for the start


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    since = time.time() - ap.parse_args().hours * 3600
    f = ledger.folder()
    stop = (common.read_json(f / "state.json", {}) or {}).get("seq")
    start = stop
    while True:
        rows = ledger.fetch(start - STEP)
        if not rows or (rows[0].get("ts") or 0) < since or start - STEP <= 0:
            start = max(start - STEP, 0)
            break
        start -= STEP
    ours, names, after, seen = {}, ledger.names(), start, 0
    while after < stop:
        rows = [e for e in ledger.fetch(after) if e["seq"] <= stop]
        if not rows:
            break
        for e in rows:
            if e.get("kind") == "result" and (e.get("ts") or 0) >= since and e.get("round", common.ROUND) == common.ROUND:
                ledger.count_ours(ours, e, names)
                seen += 1
        after = rows[-1]["seq"]
    old = common.read_json(f / "our_results.json", {}) or {}
    for h, v in old.items():                     # hours the collector counted after `stop` stay as they are
        ours.setdefault(h, v)
    common.write_json(f / "our_results.json", ours)
    print(f"read seq {start}..{stop}: {seen} results, ours agreed {sum(v['agreed'] for v in ours.values())}, "
          f"paid {sum(v['paid'] for v in ours.values())}, hours {len(ours)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
