"""Read-only: which limit holds back restocking. No network, no model. Counts and shares only: no unit cost.

Population: logs/restock plans from --since (KST file names are UTC), and [supply] lines plus supply refusals in logs/market-kit.log for its last days. The plan is replayed by nothing: every figure is what the running policy logged.
Run from week-08 with --out; prints the same JSON.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from statistics import median

ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))
STAMP = re.compile(r"^(\d{8}T\d{6})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    start = datetime.fromisoformat(args.since).replace(tzinfo=KST)
    reasons, planned_hist, budgets = Counter(), Counter(), []
    limit_at = Counter()
    surge_seen = 0
    by_hour = defaultdict(lambda: [0, 0])
    opened = 0
    files = sorted((ROOT / "logs/restock").iterdir())
    used = 0
    for path in files:
        found = STAMP.match(path.name)
        if not found:
            continue
        moment = datetime.strptime(found[1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        if moment < start:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            reasons["unreadable"] += 1
            continue
        used += 1
        info = data.get("explanation") or {}
        reasons[info.get("reason")] += 1
        if info.get("reason") != "bounded_experiment":
            continue
        opened += 1
        units = info.get("planned_units", 0)
        planned_hist[units] += 1
        budgets.append(info.get("budget", 0))
        capacity = info.get("capacity") or {}
        if units >= capacity.get("max_units_per_run", 6):
            limit_at["hit_max_units_per_run"] += 1
        elif units == 0:
            limit_at["planned_nothing"] += 1
        else:
            limit_at["under_the_run_limit"] += 1
        if info.get("estimated_max_cost", 0)+info.get("new_stock_seven_day_storage", 0) >= .9*info.get("budget", 1):
            limit_at["within_10pct_of_budget"] += 1
        hour = moment.astimezone(KST).strftime("%m-%d %H")
        by_hour[hour][0] += 1
        by_hour[hour][1] += units
    supplied, refused, kinds = 0, 0, Counter()
    samples = Counter()
    with (ROOT / "logs/market-kit.log").open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if "[supply]" not in line:
                continue
            if re.search(r"\[supply\] \d+ x .+ for \d+, balance", line):
                supplied += 1
            else:
                refused += 1
                text = re.sub(r"\d+", "#", line[9:]).strip()[:70]
                samples[text] += 1
    report = {"plans": used, "reasons": dict(reasons.most_common()), "runs_with_an_open_window": opened, "planned_units_histogram": {str(k): planned_hist[k] for k in sorted(planned_hist)}, "limit": dict(limit_at), "median_budget_share_note": "budget = min(rate x balance, balance - reserve)", "budget_median": round(median(budgets)) if budgets else None,
              "planned_units_per_open_run_by_hour_last_24": {k: round(v[1]/max(1, v[0]), 2) for k, v in sorted(by_hour.items())[-24:]}, "supply_lines_in_the_kit_log": {"supplied": supplied, "other": refused, "other_kinds": dict(samples.most_common(10))}}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
