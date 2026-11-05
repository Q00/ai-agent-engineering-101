"""Read-only, saved rows only: what shipped under category receipts, against SKU receipts, and what the old mismatches would have risked."""
import collections as C
import glob
import json
import os
import re
import statistics as st

root = "logs/star-audit/20261105/"
seen = {}
for name in ("rows.jsonl", "rows-tail.jsonl"):
    for line in open(root + name, encoding="utf-8"):
        r = json.loads(line)
        seen[r["seq"]] = r
rows = [seen[k] for k in sorted(seen)]
cat = json.load(open(".market/catalog.json", encoding="utf-8"))
settle = {r["data"].get("order"): r["data"] for r in rows if r["kind"] == "settle"}
by_unit = {r["data"].get("unit"): r["data"] for r in rows if r["kind"] == "settle" and r["data"].get("unit")}
sku = re.compile(r"^G[0-9a-f]{8}$")
groups = C.defaultdict(list)
for r in rows:
    if r["kind"] != "result" or not r["data"].get("verified"):
        continue
    item = (r["data"].get("receipt") or {}).get("item")
    s = settle.get(r["data"].get("order"))
    if item is None or not s:
        continue
    listed = (cat.get(s.get("item")) or {}).get("list_price")
    if not listed:
        continue
    fmt = "sku" if sku.match(str(item)) else "category"
    groups[fmt].append({"buyer": r["data"].get("buyer"), "receipt": item, "shipped": s.get("item"), "price": s.get("amount"), "value": listed, "shop": s.get("shop")})
print("== paid deals by receipt format (public sample)")
for fmt, group in groups.items():
    shares = [g["price"] / g["value"] for g in group]
    print(fmt, "n", len(group), "share p50", round(st.median(shares), 3), "max", round(max(shares), 3), "price>value", sum(s > 1 for s in shares), "surplus<10% of list", sum(s > .9 for s in shares),
          "receipt sku != shipped", sum(g["receipt"] != g["shipped"] for g in group) if fmt == "sku" else None, "shops", len({g["shop"] for g in group}), "buyers", C.Counter(g["buyer"] for g in group).most_common(3))
print("\n== old receipt mismatches")
for path in sorted(glob.glob("logs/market-buyer/*.json")):
    if os.path.basename(path) < "20261103":
        continue
    try:
        data = json.load(open(path, encoding="utf-8"))
    except ValueError:
        continue
    record = data["record"]
    if record.get("outcome") != "receipt_mismatch":
        continue
    receipt = record.get("receipt") or {}
    order_item = (record.get("category") or "").split(">")[-1]
    passes = receipt.get("item") == record.get("item") or receipt.get("item") == order_item
    unit = receipt.get("unit")
    sold = by_unit.get(unit) or {}
    listed = (cat.get(record.get("item")) or {}).get("list_price")
    candidates = [e for e in data.get("selection_evidence") or [] if e.get("sku")]
    values = sorted({e.get("catalogue_value") for e in candidates if e.get("catalogue_value")})
    print(os.path.basename(path)[:15], record.get("shop_name"), "asked", record.get("item"), "list", listed, "price", receipt.get("price"), "agreed", record.get("agreed_price"), "cap", record.get("budget"),
          "item_rule_passes", passes and receipt.get("item") == order_item, "unit", unit, "later_sold_sku", sold.get("item"), "later_list", (cat.get(sold.get("item")) or {}).get("list_price"), "min_known_value", values[0] if values else None)
print("\n== list price spread inside trial-relevant categories")
by_category = C.defaultdict(list)
for key, product in cat.items():
    if product.get("list_price"):
        by_category[product.get("category")].append(product["list_price"])
spreads = [max(v) / min(v) for v in by_category.values() if len(v) >= 2]
print("categories", len(spreads), "max/min p50", round(st.median(spreads), 2), "p90", round(sorted(spreads)[int(len(spreads) * .9)], 2), "max", round(max(spreads), 2))
kim = [v for k, v in by_category.items() if k and k.endswith(">김치")][0]
print("kimchi skus", len(kim), "min", min(kim), "median", st.median(kim), "max", max(kim))
