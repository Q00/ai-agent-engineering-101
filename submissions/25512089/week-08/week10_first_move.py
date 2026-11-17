import json
from pathlib import Path
from datetime import datetime, timezone

root = Path(__file__).resolve().parent
market = root / ".market"
record = market / "week10_first_move_baseline.json"

def read(name):
    return json.loads((market / name).read_text(encoding="utf-8-sig"))

if record.exists():
    print("ALREADY QUEUED: One Week 10 action was prepared earlier.")
    raise SystemExit(0)

inventory = read("inventory.json")
catalog = read("catalog.json")
demand = read("demand.json")
forum = read("forum.json")

if not isinstance(catalog, dict):
    raise SystemExit("Catalog format not supported.")

units = inventory.get("units", [])
categories = {
    d["category"] for d in demand
    if isinstance(d, dict) and isinstance(d.get("category"), str)
} if isinstance(demand, list) else set()

forum_text = json.dumps(forum, ensure_ascii=False)

choices = []
for unit in units:
    if not isinstance(unit, dict):
        continue
    sku = unit.get("sku")
    product = catalog.get(sku)
    if sku and isinstance(product, dict):
        choices.append((sku, product))

if not choices:
    raise SystemExit("NO ACTION: No verifiable listed inventory.")

choices.sort(
    key=lambda x: (
        x[1].get("category") in categories,
        x[0] not in forum_text
    ),
    reverse=True
)

sku, product = choices[0]
qty = sum(u.get("sku") == sku for u in units)
matched = product.get("category") in categories
now = datetime.now(timezone.utc).isoformat()

post = (
    f"[Stock update | chanarun] "
    f"Product: {product.get('name', sku)}. "
    f"SKU: {sku}. "
    f"Inventory snapshot: {qty} unit(s). "
    f"Availability, product condition, price and terms "
    f"must be confirmed at checkout. "
    f"Snapshot UTC: {now}."
)

baseline = {
    "time_utc": now,
    "action": "Proactive shop inventory forum post",
    "sku": sku,
    "demand_category_match": matched,
    "units_at_snapshot": qty,
    "shop_balance_before": inventory.get("balance"),
    "likes_before": 0,
    "replies_before": 0,
    "prediction_confidence": (
        "Medium (hypothesis)" if matched else "Low (hypothesis)"
    ),
    "risk": "Post may be ignored or inventory may change."
}

with (market / "outbox.jsonl").open("a", encoding="utf-8") as f:
    f.write(json.dumps({"post": post, "as": "shop"},
                       ensure_ascii=False) + "\n")

record.write_text(
    json.dumps(baseline, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print("WEEK 10 ACTION QUEUED")
print("SKU:", sku)
print("Demand category matched:", matched)
print("Units at snapshot:", qty)
print("Post:", post)
print("Baseline saved:", record)
