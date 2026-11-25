"""Read-only: the running restock plan replayed on the saved state under wider limits, and how many planned lines the market took per window. No network, no model, nothing written outside --out. Counts only: no unit cost.

Replay: restock_policy.plan with the window forced open and capacity replaced. Stock levels are the current stock or a stock raised with copies of itself. Window pace: [supply] lines in logs/market-kit.log, grouped by gaps over 60 s.
Run from week-08 with --out; prints the same JSON.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
import time
from statistics import median
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import restock_policy as policy  # noqa: E402
from restock_capacity import config, profitable_counts  # noqa: E402
from seller_quality import fresh as fresh_quality  # noqa: E402
from strategy_policy import effective  # noqa: E402

GOOD = re.compile(r"^(\d\d):(\d\d):(\d\d) \[supply\] (\d+) x .+ for \d+, balance")
BAD = re.compile(r"^(\d\d):(\d\d):(\d\d) \[supply\] .+ refused: (\d+) (.{0,60})")


def windows():
    groups, last = [], None
    with (ROOT / "logs/market-kit.log").open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            good, bad = GOOD.match(line), BAD.match(line)
            found = good or bad
            if not found:
                continue
            at = int(found[1])*3600+int(found[2])*60+int(found[3])
            if last is None or abs(at-last) > 60:
                groups.append({"ok": 0, "units": 0, "closed": 0, "other": 0, "start": at, "end": at})
            row = groups[-1]
            row["end"] = at
            last = at
            if good:
                row["ok"] += 1
                row["units"] += int(good[4])
            elif "closed" in bad[5]:
                row["closed"] += 1
            else:
                row["other"] += 1
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out")
    args = parser.parse_args()
    md = ROOT / ".market"
    read = lambda name: json.loads((md / name).read_text(encoding="utf-8"))
    inventory, catalog, demand, snapshot, memory = read("inventory.json"), read("catalog.json"), read("demand.json"), read("market-intel.json"), read("r3-restock-memory.json")
    surge = {r["sku"]: r["factor"] for r in inventory.get("surge") or []}
    own = profitable_counts(md)
    quality = fresh_quality(md)
    runtime = effective(md)
    base = config()
    now = time.time()
    forced = dict(inventory, restock={"open": True})
    table = {}
    for name, change, stock_to in (("now_6_per_run", {}, None), ("12_per_run_total_80", {"max_units_per_run": 12, "max_total_units": 80}, None), ("12_per_run_total_80_sku_6", {"max_units_per_run": 12, "max_total_units": 80, "max_proven_units_per_sku": 6}, None),
                                    ("12_per_run_total_80_stock_60", {"max_units_per_run": 12, "max_total_units": 80}, 60), ("12_per_run_total_80_sku_6_stock_60", {"max_units_per_run": 12, "max_total_units": 80, "max_proven_units_per_sku": 6}, 60)):
        units = list(inventory.get("units") or [])
        if stock_to and units:
            units = [units[i % len(units)] for i in range(stock_to)]
        with patch.object(policy, "capacity_config", lambda: dict(base, **change)):
            buys, info = policy.plan(dict(forced, units=units), catalog, demand, memory["sales"], memory["cursor"], hints={}, now=now, intelligence=snapshot.get("skus", {}), runtime=runtime, own_profitable=own, quality=quality)
        qty = sum(b["qty"] for b in buys)
        kinds = Counter((catalog.get(b["sku"]) or {}).get("kind") for b in buys)
        table[name] = {"lines": len(buys), "units": qty, "units_on_the_surge_list": sum(b["qty"] for b in buys if b["sku"] in surge), "surge_factor_highest": max((surge[b["sku"]] for b in buys if b["sku"] in surge), default=None), "kinds": dict(kinds), "reason": info.get("reason"), "cash_reserve_binds": bool(info.get("cash_reserve", 0) >= inventory.get("balance", 0)), "budget": round(info.get("budget", 0)), "planned_share_of_budget": round((info.get("estimated_max_cost", 0)+info.get("new_stock_seven_day_storage", 0))/max(1, info.get("budget", 1)), 3)}
    groups = windows()
    recent = groups[-400:]
    share = lambda a: round(sum(g["closed"] for g in a)/max(1, sum(g["ok"]+g["closed"] for g in a)), 3)
    full = [g for g in recent if g["ok"]+g["closed"] >= 4]
    report = {"stock_now": len(inventory.get("units") or []), "surge_skus": len(surge), "replay": table, "supply_windows_seen_by_gap_over_60s": len(groups), "recent_400": {"lines_ok": sum(g["ok"] for g in recent), "units_ok": sum(g["units"] for g in recent), "refused_closed": sum(g["closed"] for g in recent), "refused_other": sum(g["other"] for g in recent), "closed_share_of_lines": share(recent), "lines_ok_per_group_median": median(g["ok"] for g in recent), "seconds_per_group_median": median(g["end"]-g["start"] for g in full) if full else None}}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
