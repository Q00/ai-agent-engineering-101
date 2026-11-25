"""Read-only: our buying since the discount floor went live. No network, no model. A shop's paid price is public anyway; this holds no costs.

Population: every market_buyer run file with a timestamp in the window, and payments.jsonl by order. Discount = 1 - paid/catalog list. Run from week-08:
--since 2026-11-06T10:09:20 [--until ...] --out file. All times KST. Prints the same JSON. Board figures come from the saved snapshot .market/market-intel.json.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import buyer_pace_detail_20261105 as detail  # noqa: E402
import discount_floor as floor  # noqa: E402

KST = timezone(timedelta(hours=9))
ROOT = detail.ROOT


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=KST)


def stop(row):
    trail = row.get("decision_trail") or []
    return trail[-1].get("reason_code") if trail else (row.get("failure_reason") or row.get("outcome"))


def measure(start, end):
    rows = [(moment, r) for moment, r in detail.load_records() if start <= moment < end]
    payments = detail.load_payments()
    catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
    settings = floor.load()
    hours = (end-start).total_seconds()/3600
    paid, reasons, refused, by_kind = [], Counter(), 0, Counter()
    how = Counter()
    tries = refused_open = with_skip = 0
    skipped = set()
    for _, row in rows:
        trail = row.get("decision_trail") or []
        tries += bool(row.get("shop_name"))
        refused_open += bool(row.get("refused_at_open"))
        names = (row.get("buyer_support") or {}).get("refusal_skipped_shops") or []
        with_skip += bool(names)
        skipped.update(names)
        reasons[stop(row)] += 1
        refused += any((t.get("floor_check") or {}).get("refused_by_floor") for t in trail)
        for t in trail:
            if t.get("price_read"):
                how[t["price_read"]] += 1
        payment = payments.get(row.get("order")) or {}
        if payment.get("paid"):
            product = catalog.get(payment.get("item")) or {}
            value = product.get("list_price")
            if value:
                deal = {}
                ledger = payment.get("ledger")
                path = ROOT / ".market/buyer-deals" / f"{ledger}.json"
                if path.exists():
                    deal = json.loads(path.read_text(encoding="utf-8"))
                covered = bool((row.get("accept_conditions") or {}).get("warranty_hours"))
                need = floor.required(product.get("kind"), deal.get("grade"), covered, settings, ["A", "B"])
                paid.append({"discount": 1-payment["price"]/value, "kind": product.get("kind"), "grade": deal.get("grade"), "covered": covered, "price": payment["price"], "under_floor": payment["price"] > value*(1-settings["floor"]),
                             "under_required": deal.get("grade") is not None and payment["price"] > floor.ceiling(value, need), "shop": row.get("shop_name")})
                by_kind[product.get("kind")] += 1
    discounts = [p["discount"] for p in paid]
    return {"kst": [start.isoformat(), end.isoformat()], "hours": round(hours, 3), "orders": len(rows), "paid": len(paid), "paid_per_hour": round(len(paid)/hours, 2) if hours else None, "paid_by_kind": dict(by_kind),
            "mean_discount_new_deals": round(sum(discounts)/len(discounts), 4) if discounts else None, "lowest_discount": round(min(discounts), 4) if discounts else None,
            "paid_under_the_floor": sum(p["under_floor"] for p in paid), "paid_under_the_grade_required_discount": sum(p["under_required"] for p in paid),
            "deals_refused_by_floor_at_an_ask": refused, "tries_with_a_shop": tries, "seller_rejected_at_open": refused_open, "seller_rejected_at_open_share": round(refused_open/tries, 4) if tries else None,
            "orders_where_refusal_skip_left_out_a_shop": with_skip, "share_of_orders": round(with_skip/len(rows), 4) if rows else None, "distinct_shops_left_out": len(skipped), "stops": dict(reasons.most_common(14)), "price_read_turns": dict(how), "paid_rows": [{k: (round(v, 4) if k == "discount" else v) for k, v in p.items() if k != "shop"} for p in paid][:40]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until")
    parser.add_argument("--before-since")
    parser.add_argument("--before-until")
    parser.add_argument("--out")
    args = parser.parse_args()
    end = at(args.until) if args.until else datetime.now(KST)
    report = measure(at(args.since), end)
    if args.before_since:
        keep = ("kst", "hours", "orders", "paid", "paid_per_hour", "mean_discount_new_deals", "tries_with_a_shop", "seller_rejected_at_open", "seller_rejected_at_open_share", "orders_where_refusal_skip_left_out_a_shop", "share_of_orders", "distinct_shops_left_out")
        before = measure(at(args.before_since), at(args.before_until))
        report["before"] = {k: before[k] for k in keep}
    intel = json.loads((ROOT / ".market/market-intel.json").read_text(encoding="utf-8"))
    own = intel["competition"]["own"]["buyers"]
    report["board_snapshot"] = {"observed_kst": datetime.fromtimestamp(intel["observed_at"], KST).isoformat(timespec="seconds"), **{k: own.get(k) for k in ("saved", "forum", "posts", "deals", "all")}}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
