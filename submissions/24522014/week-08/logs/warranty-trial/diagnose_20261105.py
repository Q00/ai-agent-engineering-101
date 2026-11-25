"""Read-only, no network, no model: the warranty trial funnel by group, and evaluate() replayed at chosen moments without writing anything.

Uses the running trial's own files plus the saved public ledger sample. Imports nothing that writes; the evaluate rules are repeated here on purpose.
"""
import glob
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[2]
md = ROOT / ".market"
KST = timezone(timedelta(hours=9))


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def kst(t):
    return datetime.fromtimestamp(t, KST).strftime("%m-%d %H:%M:%S")


now = time.time()
print("generated", kst(now))
trial = read(md / "warranty-trial.json", {})
entries = trial.get("assignments", {})
offers_by_task = {}
offers_by_unit = defaultdict(list)
all_offers = []
for path in glob.glob(str(md / "signed-offers/*.json")):
    offer = read(path, {})
    all_offers.append(offer)
    offers_by_task[offer.get("task")] = offer
    entry = entries.get(offer.get("task"), {})
    if entry.get("enrolled"):
        offers_by_unit[offer["unit"]].append((offer, entry))
local = read(md / "local-settlements.json", {})
records = list(local.get("records", {}).values())
sales_by_unit = defaultdict(list)
for record in records:
    sales_by_unit[record.get("unit")].append(record)

print("\n== funnel by group (task level)")
table = {g: Counter() for g in ("warranty", "control")}
for task, entry in entries.items():
    row = table[entry["group"]]
    row["assigned"] += 1
    offer = offers_by_task.get(task)
    if offer:
        row["signed"] += 1
        sale = [s for s in sales_by_unit.get(offer["unit"], []) if s.get("amount") == offer.get("price")]
        if sale:
            row["settled_same_price"] += 1
            age = now - sale[0]["ts"]
            row["past_6h"] += age >= 21600
            row["inside_6_to_24h_now"] += 21600 <= age <= 86400
    else:
        other = sales_by_unit.get(entry["unit"], [])
        row["no_signed_offer_unit_sold_elsewhere"] += bool(other)
for group, row in table.items():
    print(" ", group, dict(row))
for task, entry in sorted(entries.items(), key=lambda kv: kv[1]["at"]):
    offer = offers_by_task.get(task) or {}
    sale = [s for s in sales_by_unit.get(entry["unit"], []) if s.get("amount") == offer.get("price")]
    print(" ", kst(entry["at"]), entry["group"], entry["sku"], "offer", offer.get("price"), "warranty", offer.get("warranty_hours"), "settled", kst(sale[0]["ts"]) if sale else None)


def replay(moment):
    groups = {"warranty": {}, "control": {}}
    for sale in records:
        matches = [(o, a) for o, a in offers_by_unit.get(sale.get("unit"), []) if o.get("price") == sale.get("amount")]
        found = {a["group"] for _, a in matches}
        if len(found) != 1 or sale.get("source") != "local_acp_settled" or sale.get("round") != 3 or not 21600 <= moment - sale["ts"] <= 86400:
            continue
        groups[matches[0][1]["group"]].setdefault(matches[0][1]["sku"], []).append(sale["amount"] - matches[0][1]["cost"])
    shared = set(groups["warranty"]) & set(groups["control"])
    return {"warranty_skus": sorted(groups["warranty"]), "control_skus": sorted(groups["control"]), "shared": sorted(shared),
            "warranty_in_window": sum(map(len, groups["warranty"].values())), "control_in_window": sum(map(len, groups["control"].values())),
            "reported_warranty": sum(len(groups["warranty"][s]) for s in shared), "reported_control": sum(len(groups["control"][s]) for s in shared)}


print("\n== evaluate rules replayed, nothing written")
for label, moment in (("now", now), ("11-05 12:00 KST", datetime(2026, 11, 5, 12, tzinfo=KST).timestamp()), ("11-05 18:40 KST", datetime(2026, 11, 5, 18, 40, tzinfo=KST).timestamp()), ("11-05 19:15 KST", datetime(2026, 11, 5, 19, 15, tzinfo=KST).timestamp())):
    print(" ", label, replay(moment))

print("\n== reads of evaluate against files as they stand")
intel = read(md / "market-intel.json", {})
print("  market-intel kinds", Counter(r.get("kind") for r in intel.get("rows", [])).most_common(), "first", kst(min(r["ts"] for r in intel["rows"])), "last", kst(max(r["ts"] for r in intel["rows"])))
print("  sale record keys", sorted(records[-1].keys()), "(no warranty field; the group comes from the signed offer)")
print("  config maturity_seconds used by evaluate:", "maturity_seconds" in (ROOT / "warranty_trial.py").read_text(encoding="utf-8").split("def evaluate")[1])

print("\n== saved public ledger sample")
seen = set()
kinds = Counter()
own_units = set(sales_by_unit)
warranted = []
broken_refunded = Counter()
for path in ("logs/star-audit/20261105/rows.jsonl", "logs/star-audit/20261105/rows-tail.jsonl"):
    with open(ROOT / path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("seq") in seen:
                continue
            seen.add(row.get("seq"))
            kinds[row.get("kind")] += 1
            data = row.get("data") or {}
            if row.get("kind") == "settle" and data.get("unit") in own_units and data.get("warranty_hours"):
                warranted.append((row["seq"], kst(row["ts"]), data.get("item"), data.get("amount"), data.get("warranty_hours")))
            if row.get("kind") == "broken":
                broken_refunded[(bool(data.get("warranty_hours")), bool(data.get("refunded")))] += 1
print("  rows", len(seen), "kinds", kinds.most_common())
print("  our warranted settles", warranted)
print("  broken rows by (had warranty, refunded):", dict(broken_refunded))

print("\n== target stock and quotes per UTC day: first pricing record of each task")
days = defaultdict(Counter)
seen_tasks = set()
items = []
for path in glob.glob(str(ROOT / "logs/pricing/*.json")):
    row = read(path, {})
    if row.get("at"):
        items.append((datetime.fromisoformat(row["at"]).astimezone(timezone.utc), row))
items.sort(key=lambda x: x[0])
for moment, row in items:
    if moment < datetime(2026, 11, 4, 9, 39, tzinfo=timezone.utc) or row.get("task") in seen_tasks:
        continue
    seen_tasks.add(row.get("task"))
    bucket = days[moment.date().isoformat()]
    bucket["new_tasks"] += 1
    if row.get("grade_observed") == "A" and not row.get("expires_at") and not row.get("below_cost_allowed"):
        bucket["A_goods"] += 1
        if row["quote"] >= row["cost"] / .8 + 1:
            bucket["A_goods_passing_stress_floor_without_storage"] += 1
    bucket["warranty_2h"] += row.get("warranty_hours") == 2
for day, bucket in sorted(days.items()):
    print(" ", day, dict(bucket))
inventory = read(md / "inventory.json", {})
print("  stock now", Counter((u.get("kind"), u.get("grade")) for u in inventory.get("units", [])).most_common())

print("\n== blocked windows: strategy experiment active, from controller logs")
rows = []
for path in sorted(glob.glob(str(ROOT / "logs/strategy-controller/*.json"))):
    data = read(path, {})
    at = data.get("at")
    if isinstance(at, str):
        at = datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
    if at and at >= datetime(2026, 11, 4, 18, 39, tzinfo=KST).timestamp():
        rows.append((at, bool(data.get("active_experiment"))))
free = [row for row in rows if not row[1]]
print("  logs", len(rows), "with no active experiment", len(free), "share", round(len(free) / len(rows), 3))
utc_day2 = datetime(2026, 11, 5, tzinfo=timezone.utc).timestamp()
print("  since UTC 11-05 00:00:", sum(1 for r in rows if r[0] >= utc_day2), "logs, free", sum(1 for r in rows if r[0] >= utc_day2 and not r[1]))
state = read(md / "strategy-state.json", {})
print("  active started", kst(state["active"]["started_at"]) if state.get("active") else None, "phase", state.get("phase"))
