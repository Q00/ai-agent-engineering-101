"""Reproduce a bounded, public-only buyer comparison from saved snapshots.

No HTTP/model calls or market writes. Output is a new immutable JSON report.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
from pathlib import Path
import re
import statistics
import sys

BUYERS = ("lee-sungsoo", "dyishappy")


def count(values):
    return dict(collections.Counter(values).most_common())


def quantile(values, q):
    values = sorted(values)
    if not values:
        return None
    p = (len(values) - 1) * q
    lo = int(p)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (p - lo)


def distribution(values):
    values = list(values)
    return {"n": len(values), "median": quantile(values, .5),
            "p25": quantile(values, .25), "p75": quantile(values, .75)}


def times(ts):
    t = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    return {"utc": t.isoformat(), "kst": t.astimezone(dt.timezone(dt.timedelta(hours=9))).isoformat()}


def review_excerpt(text):
    # Budget / authorization details are unnecessary for public performance analysis.
    if re.search(r"budget|예산|mandate|signature|서명", text, re.I):
        return None
    return text[:180]


def main():
    source, destination = map(Path, sys.argv[1:3])
    pages = [json.loads((source / f"ledger-{i}.json").read_text()) for i in range(20)]
    flattened = [event for page in pages for event in page]
    indexed = {}
    conflicts = []
    for event in flattened:
        seq = event["seq"]
        if seq in indexed and indexed[seq] != event:
            conflicts.append(seq)
        indexed[seq] = event
    events = sorted(indexed.values(), key=lambda row: row["seq"])
    if conflicts:
        raise ValueError("conflicting duplicated ledger sequence")
    contiguous = all(b["seq"] == a["seq"] + 1 for a, b in zip(events, events[1:]))
    if not contiguous:
        raise ValueError("noncontiguous ledger snapshot")
    catalog = {p["sku"]: p for p in json.loads((source / "catalog.json").read_text())}
    shop_rows = json.loads((source / "shops.json").read_text())["shops"]
    names = {r["url"].rstrip("/"): r["name"] for r in shop_rows}
    board = json.loads((source / "board.json").read_text())
    ranked_buyers = sorted((r for r in board["buyers"] if isinstance(r.get("all"), (int, float))),
                           key=lambda r: r["all"], reverse=True)
    settlements = [e for e in events if e["kind"] == "settle"]
    results = [e for e in events if e["kind"] == "result"]

    def item(e):
        return catalog.get(e["data"].get("sku"), {})

    def ratio(e):
        price = item(e).get("list_price")
        amount = e["data"].get("amount")
        return amount / price if isinstance(price, (float, int)) and price > 0 and isinstance(amount, (float, int)) else None

    def shop_name(url):
        return names.get((url or "").rstrip("/"), "unmapped_current_url")

    def match_key(e):
        d = e["data"]
        return (d.get("order"), d.get("buyer"), d.get("shop"), d.get("sku"), d.get("amount"))

    settled_index = collections.defaultdict(list)
    for e in settlements:
        settled_index[match_key(e)].append(e)
    buyer_stats = []
    for buyer in BUYERS:
        purchased = [e for e in settlements if e["data"].get("buyer") == buyer]
        own_results = [e for e in results if e["data"].get("buyer") == buyer]
        groups = collections.defaultdict(list)
        for e in purchased:
            kind, grade = item(e).get("kind", "unknown"), e["data"].get("grade", "unknown")
            for group in ("all", f"kind:{kind}", f"kind:{kind}|grade:{grade}"):
                if ratio(e) is not None:
                    groups[group].append(ratio(e))
        sku_counts = collections.Counter(e["data"].get("sku") for e in purchased)
        shop_counts = collections.Counter(e["data"].get("shop") for e in purchased)
        sku_stats = []
        for sku, n in sku_counts.most_common():
            same = [e for e in purchased if e["data"].get("sku") == sku]
            sku_stats.append({"sku": sku, **catalog.get(sku, {}), "settlements": n,
                              "paid": distribution(e["data"]["amount"] for e in same),
                              "paid_to_list": distribution(ratio(e) for e in same if ratio(e) is not None),
                              "grade": count(e["data"].get("grade") for e in same),
                              "recorded_warranty_hours": count(str(e["data"].get("warranty_hours")) for e in same)})
        matched_reviews = []
        for e in own_results:
            d = e["data"]
            receipt = d.get("receipt")
            if not isinstance(receipt, dict):
                continue
            key = (d.get("order"), d.get("buyer"), d.get("shop"), receipt.get("item"), receipt.get("price"))
            candidates = settled_index.get(key, [])
            if len(candidates) != 1 or d.get("verified") is not True or d.get("checks", {}).get("settled") is not True:
                continue
            sale = candidates[0]
            if receipt.get("shop") not in (None, sale["data"].get("shop")):
                continue
            if receipt.get("unit") not in (None, sale["data"].get("unit")):
                continue
            text = str(d.get("review") or "")
            matched_reviews.append({"result_seq": e["seq"], "settle_seq": sale["seq"],
                                    "order": d.get("order"), "sku": receipt.get("item"),
                                    "kind": item(sale).get("kind"), "grade": sale["data"].get("grade"),
                                    "paid": sale["data"]["amount"], "stars": d.get("stars"),
                                    "recorded_warranty_hours": sale["data"].get("warranty_hours"),
                                    "excerpt": review_excerpt(text),
                                    "mentions": [name for name, pattern in {
                                        "price": r"price|discount|정가|가격|할인|저렴|비싸",
                                        "warranty": r"warranty|guarantee|보증",
                                        "grade": r"grade|등급|quality|품질",
                                        "freshness": r"fresh|expir|shelf|유통|신선|기한",
                                        "honesty": r"honest|claim|정직|허위|주장",
                                    }.items() if re.search(pattern, text, re.I)]})
        matched_reviews = list({r["settle_seq"]: r for r in matched_reviews}.values())
        board_row = next(r for r in ranked_buyers if r["name"] == buyer)
        buyer_stats.append({
            "buyer": buyer,
            "board_rank": 1 + sum(r["all"] > board_row["all"] for r in ranked_buyers),
            "board": {key: board_row.get(key) for key in ("all", "saved", "forum", "deals", "posts", "orders", "served", "surplus", "paid", "alive")},
            "settlements": len(purchased), "distinct_orders_settled": len({e["data"].get("order") for e in purchased}),
            "distinct_skus": len(sku_counts), "distinct_shop_urls": len(shop_counts),
            "kind": count(item(e).get("kind", "unknown") for e in purchased),
            "kind_grade": count(f"{item(e).get('kind', 'unknown')}|{e['data'].get('grade')}" for e in purchased),
            "goods_recorded_warranty_hours": count(str(e["data"].get("warranty_hours")) for e in purchased if item(e).get("kind") == "goods"),
            "paid_to_list": {key: distribution(values) for key, values in groups.items()},
            "categories": count(item(e).get("category", "unknown") for e in purchased),
            "top3_sku_share": sum(n for _, n in sku_counts.most_common(3)) / len(purchased) if purchased else None,
            "shops": [{"current_name": shop_name(url), "url": url, "settlements": n} for url, n in shop_counts.most_common()],
            "sku_breakdown": sku_stats,
            "observed_orders_issued": len({e["data"].get("order") for e in events if e["kind"] == "order" and e["data"].get("buyer") == buyer}),
            "observed_results": {"rows": len(own_results), "unique_orders": len({e["data"].get("order") for e in own_results}),
                                 "verified": count(str(e["data"].get("verified")) for e in own_results),
                                 "receipt_present": sum(isinstance(e["data"].get("receipt"), dict) for e in own_results),
                                 "false_checks": count(key for e in own_results for key, value in e["data"].get("checks", {}).items() if value is False)},
            "matched_success_reviews": {"count": len(matched_reviews), "stars": count(str(r["stars"]) for r in matched_reviews),
                                        "mentions": count(tag for r in matched_reviews for tag in r["mentions"]), "samples": matched_reviews},
        })
    comparison_groups = collections.defaultdict(lambda: collections.defaultdict(list))
    for e in settlements:
        d = e["data"]
        if d.get("buyer") in BUYERS:
            comparison_groups[(d.get("sku"), d.get("grade"), d.get("warranty_hours"))][d["buyer"]].append(e)
    comparisons = []
    for (sku, grade, warranty), group in comparison_groups.items():
        if not all(group.get(buyer) for buyer in BUYERS):
            continue
        comparisons.append({"sku": sku, "name": catalog.get(sku, {}).get("name"),
                            "kind": catalog.get(sku, {}).get("kind"), "grade": grade, "recorded_warranty_hours": warranty,
                            "buyers": {buyer: {"paid": distribution(e["data"]["amount"] for e in group[buyer]),
                                               "paid_to_list": distribution(ratio(e) for e in group[buyer] if ratio(e) is not None),
                                               "settle_sequences": [e["seq"] for e in group[buyer]]} for buyer in BUYERS}})
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "source": str(source),
        "read_only_public_evidence": True,
        "window": {"raw_rows": len(flattened), "unique_events": len(events), "duplicate_rows": len(flattened) - len(events),
                   "contiguous": contiguous, "first_seq": events[0]["seq"], "last_seq": events[-1]["seq"],
                   "first": times(min(e["ts"] for e in events)), "last": times(max(e["ts"] for e in events)),
                   "seconds": max(e["ts"] for e in events) - min(e["ts"] for e in events),
                   "event_kinds": count(e["kind"] for e in events)},
        "buyers": buyer_stats, "same_sku_grade_recorded_warranty": comparisons,
        "limits": [
            "Bounded contiguous public ledger window, not full order lifetimes or all round history.",
            "Board totals are cumulative; sample events are a separate window. No outcome rate is inferred from raw result/order counts.",
            "Different category, SKU and grade mixes confound overall price ratios. Actual order preferences and budgets are not collected.",
            "Null warranty is unspecified, not automatically zero. Matching recorded nulls does not prove equal contractual protection.",
            "Food freshness and required use horizon can differ even for the same SKU/grade.",
            "Immediate list discount is not final official saved: future breakage, food usefulness and refunds change realized value.",
            "Shop name mapping uses current public URLs only; historical aliases may be unmapped or overcounted.",
            "Review text is buyer opinion, not independent proof of its claims. Only verified results matched to exact sampled settlements are sampled.",
            "No access to private competitor code, model, budget, negotiation turns, tool calls or response latency.",
        ],
    }
    with destination.open("x") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(destination)
    print(json.dumps(report["window"], ensure_ascii=False))
    for buyer in buyer_stats:
        print(json.dumps({key: buyer[key] for key in ("buyer", "settlements", "distinct_skus", "distinct_shop_urls", "kind", "kind_grade", "goods_recorded_warranty_hours", "paid_to_list", "observed_results")}, ensure_ascii=False))
    print("comparable_groups", len(comparisons))


if __name__ == "__main__":
    main()
