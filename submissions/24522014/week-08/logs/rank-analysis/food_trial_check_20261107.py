"""Read-only check of a food trial: arrival, grade, sale within 24 h, discards, profit against the goods median, paid/list, stars, and what the trial took from restock.

Stop signals are only reported: nothing acts on them, and no refill follows. Unit costs and per-unit profit stay out of the output; the mean profit is printed once with --show-mean and never written to --out.
Run from week-08. Reads at most two public ledger pages per sold unit (no key).
Usage: food_trial_check_20261107.py --trial v4 [--out PATH] [--windows N] [--no-stars] [--show-mean]
"""

import argparse
import datetime as d
import glob
import json
from pathlib import Path
import re
import ssl
import statistics as st
import sys
import time

sys.path.insert(0, ".")
import analyze_food_trial as aft  # noqa: E402

KST = d.timezone(d.timedelta(hours=9))
hm = lambda t: d.datetime.fromtimestamp(t, KST).strftime("%m-%d %H:%M:%S") if t else None


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def stars_of(sales):
    """{ledger: (stars, rater)} for verified results, from the public ledger."""
    import httpx
    found = {}
    with httpx.Client(verify=ssl.create_default_context(), timeout=20, headers={"User-Agent": "market-review-sample"}) as client:
        for seq in sorted(sales):
            after, orders = seq-1, {}
            for _ in range(2):
                response = client.get("https://market.ourolabs.ai/api/ledger", params={"after": after})
                if response.status_code != 200:
                    print("ledger status", response.status_code)
                    return found
                body = response.json()
                page = body if isinstance(body, list) else body.get("rows") or body.get("ledger") or []
                for row in page:
                    data = row.get("data") or {}
                    if row.get("kind") == "settle" and row.get("seq") == seq and data.get("order"):
                        orders[data["order"]] = seq
                    elif row.get("kind") == "result" and data.get("verified") and data.get("order") in orders and type(data.get("stars")) in (int, float):
                        found[orders[data["order"]]] = data["stars"]
                if seq in found or not page:
                    break
                after = max(row.get("seq", after) for row in page)
                time.sleep(.4)
            time.sleep(.4)
    return found


def supply_lines(catalog):
    """(time, sku, qty) from the last two days of the kit log, whose lines carry no date."""
    log = open("logs/market-kit.log", "rb").read().decode("utf-8", "replace").split("\n")
    midnights = [i for i, line in enumerate(log[:-1]) if line[:2] == "23" and log[i+1][:2] == "00"]
    by_name = {str(p.get("name")): s for s, p in catalog.items()}
    today = d.datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for back, (start, end) in enumerate(((midnights[-1]+1, len(log)), (midnights[-2]+1, midnights[-1]+1))):
        day = today-d.timedelta(days=back)
        for line in log[start:end]:
            m = re.match(r"(\d\d):(\d\d):(\d\d) \[supply\] (\d+) x (.+) for \d+, balance", line)
            if m and m[5] in by_name:
                rows.append((day.replace(hour=int(m[1]), minute=int(m[2]), second=int(m[3])).timestamp(), by_name[m[5]], int(m[4])))
    return sorted(rows)


def windows_of(rows):
    grouped = []
    for row in rows:
        if grouped and row[0]-grouped[-1][-1][0] < 300:
            grouped[-1].append(row)
        else:
            grouped.append([row])
    return grouped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", required=True)
    parser.add_argument("--out")
    parser.add_argument("--windows", type=int, default=12, help="ordinary windows before the trial to compare with")
    parser.add_argument("--no-stars", action="store_true")
    parser.add_argument("--show-mean", action="store_true")
    args = parser.parse_args()
    now = time.time()
    report = aft.report(aft.ROOT, now, args.trial)
    catalog = read(".market/catalog.json")
    local = read(".market/local-settlements.json").get("records") or {}
    by_unit = {r.get("unit"): r for r in (local.values() if isinstance(local, dict) else local) if r.get("unit")}
    sold = {int(by_unit[r["unit"]]["ledger"]): r["unit"] for r in report["units"] if r["unit"] in by_unit and by_unit[r["unit"]].get("ledger")}
    stars = {} if args.no_stars else stars_of(sold)
    planned = report["planned"]
    units = []
    for row in report["units"]:
        listed = (catalog.get(row["sku"]) or {}).get("list_price")
        sale = by_unit.get(row["unit"]) or {}
        ledger = int(sale["ledger"]) if sale.get("ledger") else None
        units.append({"sku": row["sku"], "category": (planned.get(row["sku"]) or {}).get("category", "").split(">")[-1], "grade": row["grade_on_arrival"], "arrived": hm(row["arrived_at"]), "sold": hm(row["sold_at"]), "hours_to_sale": row["hours_to_sale"],
                      "price": row["price"], "paid_per_list": round(row["price"]/listed, 3) if row["price"] and listed else None, "discarded": row["discarded"], "still_held": row["still_held"], "ledger": ledger, "stars": stars.get(ledger)})
    profit, goods = report["profit_per_deal"], report["goods_profit_median"]
    paid = [u["paid_per_list"] for u in units if u["paid_per_list"]]
    star_values = [u["stars"] for u in units if u["stars"] is not None]
    plans = []
    for path in sorted(glob.glob("logs/restock/*.json")):
        if Path(path).stat().st_mtime < min((p.get("planned_at", now) for p in planned.values()), default=now)-120:
            continue
        try:
            record = read(path)
        except (OSError, ValueError):
            continue
        why = record.get("explanation") or {}
        if why.get("food_trial") and all(s in planned for s in why["food_trial"]):
            food = [planned[s].get("estimated_cost", 0) for s in why["food_trial"]]
            lines = record.get("buy") or []
            plans.append({"file": Path(path).name, "food_lines": len(why["food_trial"]), "all_lines": len(lines), "all_units": sum(b.get("qty", 0) for b in lines), "food_worst_estimate": round(sum(food)), "run_budget": round(why.get("budget") or 0), "share_of_run_budget": round(sum(food)/why["budget"], 4) if why.get("budget") else None})
    rows = supply_lines(catalog)
    windows = windows_of(rows)
    # Only windows from this trial's first plan on: an earlier trial may have used the same SKUs.
    start = min((p.get("planned_at", now) for p in planned.values()), default=now) - 120
    first = [i for i, w in enumerate(windows) if w[0][0] >= start and any(s in planned and (catalog.get(s) or {}).get("kind") == "food" for _, s, _ in w)]
    effect = None
    if first:
        kind = lambda s: (catalog.get(s) or {}).get("kind")
        shape = lambda w: {"at": hm(w[0][0]), "goods_lines": sum(kind(s) != "food" for _, s, _ in w), "goods_units": sum(q for _, s, q in w if kind(s) != "food"), "food_lines": sum(kind(s) == "food" for _, s, _ in w), "food_units": sum(q for _, s, q in w if kind(s) == "food")}
        before = [shape(w) for w in windows[max(0, first[0]-args.windows):first[0]]]
        effect = {"trial_windows": [shape(windows[i]) for i in first], "next_windows": [shape(w) for w in windows[first[-1]+1:first[-1]+4]], "before_windows": len(before), "before_goods_lines_median": st.median(b["goods_lines"] for b in before) if before else None, "before_goods_units_median": st.median(b["goods_units"] for b in before) if before else None}
    # The trial records a unit only when a restock run sees it in stock; one sold before that is found here by SKU and time.
    seen_units = {r["unit"] for r in report["units"]}
    names = {str(p.get("name")): s for s, p in catalog.items()}
    unrecorded = []
    for r in (local.values() if isinstance(local, dict) else local):
        sku = names.get(str(r.get("name")))
        if sku in planned and r.get("unit") not in seen_units and (r.get("ts") or 0) >= start:
            listed = catalog[sku]["list_price"]
            unrecorded.append({"sku": sku, "category": (planned[sku].get("category") or "").split(">")[-1], "ledger": r.get("ledger"), "sold": hm(r.get("ts")), "grade": r.get("grade"),
                               "price": r.get("amount"), "paid_per_list": round(r["amount"]/listed, 3) if r.get("amount") else None})
    result = {"trial": args.trial, "checked_kst": hm(now), "stop_rules_report_only": True, "planned": len(planned), "arrived": report["units_arrived"], "sold": report["units_sold"], "mature_units": report["mature_units"], "sold_within_24h": report["sold_within_24h"], "discards": report["discards"],
              "profit_to_goods_median": round(profit/goods, 3) if profit is not None and goods else None, "paid_per_list_median": st.median(paid) if paid else None, "stars_found": len(star_values), "stars_mean": round(st.mean(star_values), 3) if star_values else None, "stop_signals": report["stop_signals"], "units": units, "sold_before_recorded": unrecorded, "plans": plans, "supply": effect}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+"\n", encoding="utf-8")
    print(text)
    if args.show_mean:
        print("mean profit per deal, not saved:", profit, "goods median:", goods)


if __name__ == "__main__":
    main()
