"""Read-only derivation from the adjacent public market snapshot; no requests."""
import collections
import datetime
import json
import pathlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parent
BUYERS = ("dyishappy", "dragonash", "kimauve", "lee-sungsoo")
catalog = {row["sku"]: row for row in json.loads((ROOT / "catalog.json").read_text())}
board = json.loads((ROOT / "board.json").read_text())
sources = sorted(ROOT.glob("ledger-*.json"))
records = [row for path in sources for row in json.loads(path.read_text())]
by_seq = {row["seq"]: row for row in records}
rows = sorted(by_seq.values(), key=lambda row: row["seq"])
assert all(row.get("round") == 3 for row in rows), "Unexpected round in snapshot"
shop_names = {row.get("card", {}).get("url"): row["name"] for row in board["shops"]}
for row in rows:
    data = row["data"]
    receipt = data.get("receipt")
    if row["kind"] == "result" and isinstance(receipt, dict) and receipt.get("shop"):
        shop_names.setdefault(data.get("shop"), receipt["shop"])


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=9))).isoformat()


def stats(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    q = statistics.quantiles(values, n=4, method="inclusive") if len(values) > 1 else values * 3
    return {"n": len(values), "mean": statistics.mean(values), "median": statistics.median(values),
            "q1": q[0], "q3": q[2], "min": values[0], "max": values[-1]}


def annotate(row):
    data = row["data"]
    item = catalog.get(data.get("item"), {})
    price = data.get("amount")
    list_price = item.get("list_price")
    return {"seq": row["seq"], "ts": row["ts"], "order": data.get("order"),
            "sku": data.get("item"), "name": item.get("name"), "kind": item.get("kind"),
            "grade": data.get("grade"), "warranty_hours": data.get("warranty_hours"),
            "expires_at": data.get("expires_at"),
            "remaining_shelf_hours": (data["expires_at"] - row["ts"]) / 3600 if isinstance(data.get("expires_at"), (int, float)) else None,
            "shop": shop_names.get(data.get("shop"), data.get("shop")),
            "shop_url": data.get("shop"), "amount": price, "list_price": list_price,
            "paid_to_list": price / list_price if list_price and isinstance(price, (int, float)) else None}


settled = {buyer: [annotate(row) for row in rows if row["kind"] == "settle" and row["data"].get("buyer") == buyer] for buyer in BUYERS}
out = {"source_files": [path.name for path in sources], "method": {
    "deduplicate": "ledger seq", "row_count": len(rows), "duplicate_rows": len(records) - len(rows),
    "first_seq": rows[0]["seq"], "last_seq": rows[-1]["seq"],
    "contiguous": len(rows) == rows[-1]["seq"] - rows[0]["seq"] + 1,
    "first_kst": iso(min(row["ts"] for row in rows)), "last_kst": iso(max(row["ts"] for row in rows)),
    "duration_minutes": (max(row["ts"] for row in rows) - min(row["ts"] for row in rows)) / 60,
    "quantiles": "statistics.quantiles(method='inclusive')", "filter": "data.buyer exact match, round 3"},
    "buyers": {}, "matched_comparisons": {}}

for buyer in BUYERS:
    trades = settled[buyer]
    orders = [row for row in rows if row["kind"] == "order" and row["data"].get("buyer") == buyer]
    results = [row for row in rows if row["kind"] == "result" and row["data"].get("buyer") == buyer]
    verified = [row for row in results if row["data"].get("verified") is True]
    unverified = [row for row in results if row["data"].get("verified") is not True]
    prices = [row["paid_to_list"] for row in trades if row["paid_to_list"] is not None]
    settled_orders = {row["order"] for row in trades}
    issued_orders = {row["data"].get("order") for row in orders}
    data = {"orders_issued_in_window": len(orders), "settlements": len(trades),
            "distinct_settled_orders": len(settled_orders), "results": len(results),
            "verified_results": len(verified), "settled_over_result_rows_descriptive": len(trades) / len(results) if results else None,
            "issued_orders_settled_within_window": len(issued_orders & settled_orders),
            "settlements_from_orders_outside_window": len(settled_orders - issued_orders),
            "mix_kind": dict(collections.Counter(row["kind"] for row in trades)),
            "mix_grade": dict(collections.Counter(row["grade"] for row in trades)),
            "mix_kind_grade": dict(collections.Counter(f'{row["kind"]}:{row["grade"]}' for row in trades)),
            "warranty_hours": dict(collections.Counter(str(row["warranty_hours"]) for row in trades)),
            "price_to_list": stats(prices),
            "price_to_list_by_kind": {kind: stats([row["paid_to_list"] for row in trades if row["kind"] == kind and row["paid_to_list"] is not None]) for kind in ("food", "goods")},
            "prices_84_to_85_percent_of_list": sum(.84 <= value <= .85 for value in prices),
            "prices_above_85_percent_of_list": sum(value > .85 for value in prices),
            "prices_above_list": sum(value > 1 for value in prices),
            "goods_price_near_76_5_percent_list": sum(row["kind"] == "goods" and row["paid_to_list"] is not None and abs(row["paid_to_list"] - .765) < .00001 for row in trades),
            "distinct_settled_shops": len({row["shop_url"] for row in trades}),
            "settled_shops": dict(collections.Counter(row["shop"] for row in trades).most_common()),
            "result_shops": dict(collections.Counter(shop_names.get(row["data"].get("shop"), row["data"].get("shop")) for row in results).most_common()),
            "verified_review_stars": dict(collections.Counter(str(row["data"].get("stars")) for row in verified)),
            "unverified_result_evidence": {"no_receipt": sum(not row["data"].get("receipt") for row in unverified),
                "receipt_present": sum(bool(row["data"].get("receipt")) for row in unverified),
                "false_check_fields": dict(collections.Counter(key for row in unverified for key, value in row["data"].get("checks", {}).items() if value is False)),
                "structured_reason_fields_present": dict(collections.Counter(key for row in unverified for key in ("reason", "error", "status") if key in row["data"]))},
            "verified_review_examples": [{"seq": row["seq"], "stars": row["data"].get("stars"), "review": row["data"].get("review")} for row in verified[:3]],
            "settled_trade_details": trades}
    out["buyers"][buyer] = data

for other in BUYERS[1:]:
    out["matched_comparisons"][other] = {}
    for same_shop in (False, True):
        keys = ("sku", "grade", "warranty_hours") + (("shop_url",) if same_shop else ())
        groups = []
        own_group = collections.defaultdict(list)
        other_group = collections.defaultdict(list)
        for trade in settled["dyishappy"]:
            own_group[tuple(trade[key] for key in keys)].append(trade)
        for trade in settled[other]:
            other_group[tuple(trade[key] for key in keys)].append(trade)
        for key in sorted(own_group.keys() & other_group.keys(), key=str):
            own = own_group[key]
            theirs = other_group[key]
            ours_median = statistics.median(row["amount"] for row in own)
            theirs_median = statistics.median(row["amount"] for row in theirs)
            groups.append({**dict(zip(keys, key)), "name": own[0]["name"], "kind": own[0]["kind"],
                           "own_n": len(own), "other_n": len(theirs), "own_median_price": ours_median,
                           "other_median_price": theirs_median, "own_over_other_median_price": ours_median / theirs_median,
                           "own_seq": [row["seq"] for row in own], "other_seq": [row["seq"] for row in theirs]})
        out["matched_comparisons"][other]["same_shop" if same_shop else "all_shops"] = {
            "keys": keys, "group_count": len(groups), "own_trades": sum(row["own_n"] for row in groups),
            "other_trades": sum(row["other_n"] for row in groups),
            "group_median_price_ratios": stats([row["own_over_other_median_price"] for row in groups]),
            "own_cheaper_groups": sum(row["own_over_other_median_price"] < 1 for row in groups),
            "own_dearer_groups": sum(row["own_over_other_median_price"] > 1 for row in groups), "groups": groups}

out["limits"] = [
    "37.7-minute contiguous public ledger sample, not all historical orders or a controlled model comparison.",
    "Orders/results/settlements can cross the window boundary. Aggregate settlement/result ratio is descriptive, not eventual success probability.",
    "Paid/list is an immediate catalog ratio, not the official Saved score; budgets, buyer use horizons, future breakage/refunds and failure costs are not observed here.",
    "Matched groups control SKU, recorded grade and recorded warranty, optionally shop URL; food remaining life, assigned budgets, condition and negotiation context may still differ.",
    "No receipt or false settled check is observed evidence, not proof of a permanent failed payment or a particular failure cause.",
    "Public review text is an agent-authored claim. Verified means market checks passed, not that every qualitative opinion is a measured fact.",
    "Shop name lookup uses current board card URLs and public result receipt names; URL/name lookup is not independent identity proof.",
    "Competitor private policies and prompts were not accessed; descriptive patterns do not establish their algorithm."
]
(ROOT / "recent-trades.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"method": out["method"], "matched": {buyer: {kind: {key: value for key, value in result.items() if key != "groups"} for kind, result in kinds.items()} for buyer, kinds in out["matched_comparisons"].items()}}, ensure_ascii=False, indent=2))
