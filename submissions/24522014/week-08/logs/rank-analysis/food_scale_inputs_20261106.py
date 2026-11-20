"""Read-only: inputs for sizing a larger food trial. No network, no model. Only market figures and shares of our balance: no unit cost.

Population: settle rows in the saved market-intel sample (bounded, not the whole ledger), joined to the catalog; our own units left out of market counts. Eligible = the trial's own test: 3+ settles and 3+ buyers in 6 h.
Worst cost = list x .75 x surge factor, as food_trial.estimated_cost. Run from week-08 with --out; prints the same JSON.
"""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from purchase_monitor import settlement_price  # noqa: E402

HOUR = 3600


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out")
    args = parser.parse_args()
    md = ROOT / ".market"
    now = time.time()
    catalog = json.loads((md / "catalog.json").read_text(encoding="utf-8"))
    intel = json.loads((md / "market-intel.json").read_text(encoding="utf-8"))
    inventory = json.loads((md / "inventory.json").read_text(encoding="utf-8"))
    local = json.loads((md / "local-settlements.json").read_text(encoding="utf-8")).get("records") or {}
    own = {r.get("unit") for r in (local.values() if isinstance(local, dict) else local)}
    surge = {r["sku"]: r["factor"] for r in inventory.get("surge") or []}
    rows = [r for r in intel["rows"] if r["kind"] == "settle" and r["data"].get("unit") not in own and (catalog.get(r["data"].get("item")) or {}).get("kind") == "food"]
    span = (max(r["ts"] for r in rows)-min(r["ts"] for r in rows))/HOUR
    by = defaultdict(list)
    for row in rows:
        by[catalog[row["data"]["item"]].get("category") or ""].append(row)
    table = []
    for category, group in by.items():
        recent = [r for r in group if now-r["ts"] <= 6*HOUR]
        skus = defaultdict(list)
        for row in recent:
            skus[row["data"]["item"]].append(row["data"].get("buyer"))
        eligible = [s for s, buyers in skus.items() if len(buyers) >= 3 and len(set(buyers)) >= 3]
        shares = [settlement_price(r["data"])/catalog[r["data"]["item"]]["list_price"] for r in group if settlement_price(r["data"]) is not None]
        lists = [catalog[s]["list_price"] for s in eligible]
        worst = [catalog[s]["list_price"]*.75*max(1, surge.get(s, 1)) for s in eligible]
        grades = Counter(r["data"].get("grade") for r in group)
        table.append({"category": category.split(">")[-1], "settles_per_hour_in_sample": round(len(group)/span, 1), "shops": len({r["data"].get("shop") for r in group}), "paid_share_of_list_median": round(median(shares), 3) if shares else None,
                      "eligible_skus_now": len(eligible), "list_price_median_eligible": round(median(lists)) if lists else None, "worst_cost_median_eligible": round(median(worst)) if worst else None, "grade_share": {g: round(grades[g]/len(group), 3) for g in "ABC"}})
    table.sort(key=lambda r: -r["settles_per_hour_in_sample"])
    balance = inventory.get("balance")
    report = {"sample_hours": round(span, 1), "market_food_settles_per_hour_in_sample_without_ours": round(len(rows)/span, 1), "top_categories": table[:12], "eligible_categories": sum(r["eligible_skus_now"] > 0 for r in table),
              "budget_as_share_of_balance": {str(b): round(b/balance, 4) for b in (150000, 300000, 500000, 1000000)} if balance else None, "stock_now": dict(Counter(u.get("kind") for u in inventory.get("units") or [])), "storage_per_day": inventory.get("storage_per_day")}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
