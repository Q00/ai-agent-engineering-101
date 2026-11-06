"""Read-only: why the share of agreed deals the kit refused to pay rose after 10:09 on 11-06. No network, no model.

Population: market_buyer runs from 11-05 00:00 KST with outcome purchased, joined to payments.jsonl by order. The shop with the most refusals is shown as top_shop, unnamed. A refusal is checked against the order: a stated grade inside the order's accepted grades, or stated hours at or above its minimum, would be a wrong block.
Run from week-08 with --out; prints the same JSON.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import buyer_pace_detail_20261105 as detail  # noqa: E402

KST = timezone(timedelta(hours=9))
PERIODS = (("11-05", "2026-11-05T00:00", "2026-11-06T00:00"), ("11-06 00:00-06:00", "2026-11-06T00:00", "2026-11-06T06:00"), ("11-06 06:00-10:09", "2026-11-06T06:00", "2026-11-06T10:09:20"), ("11-06 10:09-13:00", "2026-11-06T10:09:20", "2026-11-06T13:00"))
GRADE = re.compile(r"grade (\w+), the policy accepts")
HOURS = re.compile(r"([\d.]+) h of shelf life left, the policy asks for ([\d.]+)")


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=KST)


def kind(text):
    return "grade" if GRADE.search(text) else "shelf_life" if HOURS.search(text) else "not_in_stock" if "not in stock" in text else "shop_endpoint" if "ACP endpoint" in text else "other"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out")
    args = parser.parse_args()
    rows = detail.load_records()
    pay = detail.load_payments()
    agreed = [(m, r) for m, r in rows if m >= at("2026-11-05T00:00") and r.get("outcome") == "purchased"]
    refused = Counter((pay.get(r["order"]) or {}).get("shop") for _, r in agreed if not (pay.get(r["order"]) or {}).get("paid"))
    top = refused.most_common(1)[0][0]
    names = {r.get("shop_name") for _, r in agreed if (pay.get(r["order"]) or {}).get("shop") == top}
    wrong, checked, read_how = [], 0, Counter()
    table = {}
    for name, a, b in PERIODS:
        start, end = at(a), at(b)
        hours = (end-start).total_seconds()/3600
        window = [r for m, r in rows if start <= m < end and r.get("shop_name")]
        deals = [r for r in window if r.get("outcome") == "purchased"]
        unpaid = [r for r in deals if not (pay.get(r["order"]) or {}).get("paid")]
        ours = [r for r in window if r.get("shop_name") in names]
        ours_deals = [r for r in deals if r.get("shop_name") in names]
        ours_unpaid = [r for r in unpaid if r.get("shop_name") in names]
        rest = [r for r in deals if r.get("shop_name") not in names]
        rest_unpaid = [r for r in unpaid if r.get("shop_name") not in names]
        for row in unpaid:
            text = (pay.get(row["order"]) or {}).get("reason") or ""
            accept = row.get("accept_conditions") or {}
            found_grade, found_hours = GRADE.search(text), HOURS.search(text)
            checked += bool(found_grade or found_hours)
            if (found_grade and found_grade[1] in (accept.get("grades") or [])) or (found_hours and (float(found_hours[1]) >= float(found_hours[2]) or float(found_hours[2]) != accept.get("min_hours_left"))):
                wrong.append({"order": row["order"], "reason": text[:90], "accept": accept})
            read_how[next((t["price_read"] for t in row.get("decision_trail") or [] if t.get("price_read")), "existing_parser")] += 1
        table[name] = {"hours": hours, "agreed": len(deals), "not_paid": len(unpaid), "share": round(len(unpaid)/max(1, len(deals)), 3), "top_shop": {"tries_per_hour": round(len(ours)/hours, 2), "share_of_tries": round(len(ours)/max(1, len(window)), 3), "agreed_per_hour": round(len(ours_deals)/hours, 2), "not_paid": len(ours_unpaid), "not_paid_share": round(len(ours_unpaid)/max(1, len(ours_deals)), 3), "reasons": dict(Counter(kind((pay.get(r["order"]) or {}).get("reason") or "") for r in ours_unpaid))},
                       "other_shops": {"agreed_per_hour": round(len(rest)/hours, 2), "not_paid": len(rest_unpaid), "not_paid_share": round(len(rest_unpaid)/max(1, len(rest)), 3), "reasons": dict(Counter(kind((pay.get(r["order"]) or {}).get("reason") or "") for r in rest_unpaid))}}
    report = {"periods": table, "refusals_checked_against_the_order": checked, "wrong_blocks": wrong, "not_paid_read_by": dict(read_how)}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
