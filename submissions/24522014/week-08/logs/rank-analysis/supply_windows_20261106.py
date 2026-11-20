"""Read-only: what the market's supply window lets us buy. No network, no model. Counts only: no unit cost.

Population: [supply] lines in logs/market-kit.log dated by market.kit_lines, from --since (KST). A window = successful lines with gaps under 120 s; refusals after it are counted toward it. Stock comes from logs/restock plans is not logged, so stock is taken from nothing here.
Run from week-08 with --out; prints the same JSON.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import buyer_pace_market_20261105 as market  # noqa: E402

KST = timezone(timedelta(hours=9))
GOOD = re.compile(r"\[supply\] (\d+) x .+ for \d+, balance")
BAD = re.compile(r"\[supply\] .+ refused: (\d+) (.*)")
OPENS = re.compile(r"opens in (\d+)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    start = datetime.fromisoformat(args.since).replace(tzinfo=KST)
    good, bad, waits = [], Counter(), []
    closed_times = []
    for moment, raw in market.kit_lines():
        moment = moment.replace(tzinfo=KST)
        if moment < start or b"[supply]" not in raw:
            continue
        line = raw.decode("utf-8", errors="replace")
        found = GOOD.search(line)
        if found:
            good.append((moment, int(found[1])))
            continue
        found = BAD.search(line)
        if found:
            kind = "closed" if "closed" in found[2] else f"{found[1]}"
            bad[kind] += 1
            if kind == "closed":
                closed_times.append(moment)
            wait = OPENS.search(found[2])
            if wait:
                waits.append(int(wait[1]))
    windows = []
    for moment, qty in good:
        if not windows or (moment-windows[-1]["end"]).total_seconds() > 120:
            windows.append({"start": moment, "end": moment, "lines": 0, "units": 0})
        row = windows[-1]
        row["end"] = moment
        row["lines"] += 1
        row["units"] += qty
    gaps = [(b["start"]-a["start"]).total_seconds()/60 for a, b in zip(windows, windows[1:])]
    pace = [(b[0]-a[0]).total_seconds() for a, b in zip(good, good[1:]) if (b[0]-a[0]).total_seconds() <= 60]
    for row in windows:
        row["closed_after"] = sum(row["end"] < t <= row["end"]+timedelta(minutes=20) for t in closed_times)
    hours = (good[-1][0]-good[0][0]).total_seconds()/3600 if len(good) > 1 else 0
    tail = [dict(start=r["start"].strftime("%m-%d %H:%M:%S"), seconds=int((r["end"]-r["start"]).total_seconds()), lines=r["lines"], units=r["units"], closed_after=r["closed_after"]) for r in windows[-12:]]
    report = {"since": args.since, "hours": round(hours, 1), "lines": len(good), "units": sum(q for _, q in good), "units_per_hour": round(sum(q for _, q in good)/hours, 1) if hours else None, "lines_with_qty_over_1": sum(q > 1 for _, q in good), "refused": dict(bad), "seconds_between_good_lines_median": median(pace) if pace else None,
              "windows": len(windows), "minutes_between_window_starts_median": round(median(gaps), 1) if gaps else None, "units_per_window": {"median": median(r["units"] for r in windows), "max": max(r["units"] for r in windows), "histogram": dict(sorted(Counter(r["units"] for r in windows).items()))}, "seconds_used_per_window_median": median((r["end"]-r["start"]).total_seconds() for r in windows), "last_windows": tail,
              "largest_opens_in_seen_seconds": max(waits, default=None)}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
