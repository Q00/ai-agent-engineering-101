"""Read-only: repeated payment refusals for one shop and SKU, and what a recent-refusal skip would have cost.

The simulation replays negotiations in time order. It is an estimate: a skipped negotiation might have led elsewhere,
and each attempt can involve a different unit and an order with a different shelf-life requirement.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from buyer_funnel import ROOT, load_runs, stamp
from buyer_surplus import settled_deals
from quote_terms import check_quote
from replay_quote_filter import quotes
from settlement_memory import payment_outcomes

WINDOWS_HOURS = (0.25, 1, 3, 6, 24)
SHELF = re.compile(r"([\d.]+) h of shelf life left, the policy asks for ([\d.]+)")


def kind_of(reason):
    reason = str(reason)
    return "shelf_life" if "shelf life" in reason else "grade" if "grade" in reason else "other"


def attempts(root, since, until=None):
    """Negotiations that reached a payment row, oldest first."""
    payments = payment_outcomes(root / ".market/payments.jsonl")
    surplus = {d["ledger"]: d["estimated_surplus"] for d in settled_deals(root)[0]}
    rows = []
    for name, record in load_runs(root, since, until):
        payment = payments.get(record.get("order"))
        if not payment or record.get("outcome") == "research_skipped":
            continue
        at = datetime.strptime(name, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).timestamp()
        paid = payment.get("paid") is True and payment.get("ledger") is not None
        shelf = SHELF.search(str(payment.get("reason", "")))
        accept = record.get("accept_conditions")
        directory = root / "logs/buyer" / Path(str(record.get("buyer_log_dir", "")).replace("\\", "/")).name
        filtered = bool(accept) and any(check_quote(task, text, accept, at)["reason_code"] for task, text in quotes(directory))
        rows.append({"at": at, "shop": record.get("shop_name"), "sku": record.get("item"), "paid": paid,
                     "price": payment.get("price"), "kind": None if paid else kind_of(payment.get("reason")),
                     "hours": float(shelf[1]) if shelf else None, "required": float(shelf[2]) if shelf else None,
                     "min_hours": (record.get("accept_conditions") or {}).get("min_hours_left"),
                     "surplus": surplus.get(payment.get("ledger"), 0) if paid else 0, "filtered": filtered})
    return rows


def repeats(rows):
    groups = defaultdict(list)
    for row in rows:
        if not row["paid"] and row["kind"] in ("shelf_life", "grade"):
            groups[(row["shop"], row["sku"], row["kind"])].append(row)
    repeated = {key: group for key, group in groups.items() if len(group) >= 2}
    top = sorted(repeated.items(), key=lambda item: -len(item[1]))[:10]
    return {"refusals_shelf_or_grade": sum(len(g) for g in groups.values()), "pairs": len(groups),
            "pairs_repeated": len(repeated), "refusals_in_repeated_pairs": sum(len(g) for g in repeated.values()),
            "refusals_after_the_first_in_a_pair": sum(len(g) - 1 for g in repeated.values()),
            "top": [{"shop": k[0], "sku": k[1], "kind": k[2], "refusals": len(g), "prices": sorted({r["price"] for r in g})[:6],
                     "span_hours": round((g[-1]["at"] - g[0]["at"]) / 3600, 2)} for k, g in top]}


def simulate(rows, hours, *, aged_shelf=False):
    """Skip a shop and SKU for `hours` after a shelf-life or grade refusal, with no memory of skipped attempts.

    aged_shelf narrows shelf-life refusals: skip only if the refused unit, aged to now, would still miss this order's need.
    """
    last = {}
    result = Counter()
    for row in rows:
        key = (row["shop"], row["sku"])
        memory = last.get(key)
        skip = False
        if memory and row["at"] - memory["at"] <= hours * 3600:
            skip = True
            if aged_shelf and memory["kind"] == "shelf_life":
                left = memory["hours"] - (row["at"] - memory["at"]) / 3600 if memory["hours"] is not None else None
                skip = left is not None and row["min_hours"] is not None and left < row["min_hours"]
        if skip:
            if row["paid"]:
                result["paid_deals_lost"] += 1
                result["surplus_lost"] += row["surplus"]
            elif row["kind"] in ("shelf_life", "grade"):
                result["refusals_avoided"] += 1
            else:
                result["other_refusals_avoided"] += 1
        if not row["paid"] and row["kind"] in ("shelf_life", "grade"):
            last[key] = row
    return dict(result)


def report(root, since, until=None):
    rows = attempts(root, since, until)
    after_filter = [r for r in rows if not r["filtered"]]
    return {"window": {"since": since, "until": until}, "attempts_with_payment_row": len(rows),
            "paid": sum(r["paid"] for r in rows), "refused_by_kind": dict(Counter(r["kind"] for r in rows if not r["paid"]).most_common()),
            "repeats": repeats(rows),
            "skip_any_refusal": {str(h): simulate(rows, h) for h in WINDOWS_HOURS},
            "skip_aged_shelf_life": {str(h): simulate(rows, h, aged_shelf=True) for h in WINDOWS_HOURS},
            "with_the_quote_filter_already_on": {
                "attempts": len(after_filter), "paid": sum(r["paid"] for r in after_filter),
                "refused_by_kind": dict(Counter(r["kind"] for r in after_filter if not r["paid"]).most_common()),
                "repeats": repeats(after_filter)["refusals_after_the_first_in_a_pair"],
                "skip_any_refusal": {str(h): simulate(after_filter, h) for h in WINDOWS_HOURS},
                "skip_aged_shelf_life": {str(h): simulate(after_filter, h, aged_shelf=True) for h in WINDOWS_HOURS}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until")
    args = parser.parse_args()
    print(json.dumps(report(ROOT, stamp(args.since), stamp(args.until) if args.until else None), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
