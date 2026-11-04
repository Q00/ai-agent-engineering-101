"""Read-only buyer surplus by decision path, over settled deals only.

Definition (market contract, logs/agent-contract-20261102.md:216-218): a settled deal is worth the SKU's list price
to the order, or 0 if the food did not last use_within_hours at settle or the goods broke; surplus = value - price +
refund. Results are reconstructed from local records, so they are labeled estimates; `reconciliation` compares them
with the official figure, which remains the only official value. A receipt without paid:true and a ledger is not here.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median

from buyer_fast import parse_ask
from buyer_funnel import ROOT, load_runs, read_json, stamp
from replay_quote_filter import quotes
from settlement_memory import payment_outcomes

UNKNOWN_PATH = "path_unknown_no_decision_record"
DAY_ZERO = "20261103T000000000000Z"
OFFER_BUCKETS = ((0.85, "below_0.85"), (0.90, "0.85-0.90"), (0.95, "0.90-0.95"), (1.0, "0.95-1.00"))


def offer_path(root, record):
    """The decision that produced the last offer; old logs are kept apart from the decision trail."""
    offers = [e for e in record.get("decision_trail") or () if e.get("action") == "offer"]
    if offers:
        path = f"{offers[-1]['source']}:{offers[-1]['reason_code']}"
        countered = any(e["reason_code"] == "rule_counter_offer" for e in offers[:-1])
        return path + "_after_counter" if countered else path
    if record.get("decision_trail") is not None:
        return UNKNOWN_PATH
    directory = root / "logs/buyer" / Path(str(record.get("buyer_log_dir", "")).replace("\\", "/")).name
    latest = None
    for path in sorted(directory.glob("decision-*.json")):
        saved = read_json(path) or {}
        if saved.get("source") == "deterministic" and (saved.get("decision") or {}).get("action") == "offer":
            latest = "legacy_file:deterministic"
        elif saved.get("status") == "ok" and ((saved.get("attempts") or [{}])[-1].get("response") or {}).get("action") == "offer":
            latest = "legacy_file:model"
    return latest or UNKNOWN_PATH


def broken_units(root, deals):
    """Tie each broken.jsonl line to one deal: goods break within 6 hours of sale, so name and time identify it."""
    matched, unmatched = {}, []
    path = root / ".market/broken.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    def settle(deal, event):
        hours = (event["broke_at"] - deal["purchased_at"]) / 3600
        matched[deal["ledger"]] = deal["price"] if (deal.get("warranty_hours") or 0) >= hours else 0
    events = [json.loads(line) for line in lines]
    open_events = []
    for event in events:
        # A fault already tied to the deal by unit is stronger evidence than a name and a time.
        known = [d for d in deals if d.get("fault") and d.get("name") == event.get("name")
                 and abs(d["fault"].get("at", 0) - event["broke_at"]) <= 60]
        if len(known) == 1:
            settle(known[0], event)
        else:
            open_events.append(event)
    for event in open_events:
        candidates = [d for d in deals if d.get("name") == event.get("name") and d["ledger"] not in matched
                      and 0 <= event["broke_at"] - d["purchased_at"] <= 6 * 3600 + 60]
        if len(candidates) == 1:
            settle(candidates[0], event)
        else:
            unmatched.append({"name": event.get("name"), "broke_at": event.get("broke_at"), "candidates": len(candidates),
                              "max_overstatement_if_covered": max((d["value_at_purchase"] - d["price"] for d in candidates), default=None)})
    return matched, unmatched


def settled_deals(root):
    """Deals with paid:true and a ledger, with local value and any matched breakage."""
    deals = []
    for order, payment in payment_outcomes(root / ".market/payments.jsonl").items():
        if payment.get("paid") is not True or payment.get("ledger") is None:
            continue
        local = read_json(root / ".market/buyer-deals" / f"{payment['ledger']}.json") or {}
        if local.get("value_at_purchase") is None or local.get("purchased_at") is None:
            continue
        deals.append({**local, "order": order, "ledger": payment["ledger"], "price": payment["price"]})
    refunds, unmatched = broken_units(root, deals)
    for deal in deals:
        broken = deal["ledger"] in refunds
        deal["broken"] = broken
        deal["estimated_surplus"] = (0 if broken else deal["value_at_purchase"]) - deal["price"] + refunds.get(deal["ledger"], 0)
    return deals, unmatched


def negotiation_index(root):
    index = {}
    for name, record in load_runs(root, DAY_ZERO):
        if record.get("order") and record.get("outcome") != "research_skipped":
            index[record["order"]] = record
    return index


def first_ask(root, record):
    replies = quotes(root / "logs/buyer" / Path(str(record.get("buyer_log_dir", "")).replace("\\", "/")).name)
    return parse_ask(replies[0][1])[0] if replies else None


def summarize(rows):
    def med(key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return round(median(values), 4) if values else None
    return {"deals": len(rows), "broken": sum(r["broken"] for r in rows),
            "surplus_sum": sum(r["estimated_surplus"] for r in rows),
            "surplus_median": median(r["estimated_surplus"] for r in rows),
            "surplus_per_list_median": med("surplus_per_list"), "price_per_cap_median": med("price_per_cap"),
            "price_per_order_budget_median": med("price_per_order_budget"),
            "price_per_first_ask_median": med("price_per_first_ask"),
            "cap_per_list_median": med("cap_per_list"),
            "with_cap": sum(r.get("price_per_cap") is not None for r in rows),
            "with_labeled_first_ask": sum(r.get("price_per_first_ask") is not None for r in rows)}


def official_snapshots(root):
    rows = []
    for path in sorted((root / "logs/market-intel").glob("*.json")):
        data = read_json(path) or {}
        buyer = ((data.get("competition") or {}).get("own") or {}).get("buyers") or {}
        if type(buyer.get("surplus")) in (int, float) and data.get("observed_at"):
            rows.append({"observed_at": data["observed_at"], "deals": buyer.get("deals"), "served": buyer.get("served"),
                         "surplus": buyer["surplus"], "all": buyer.get("all")})
    return rows


def official_delta(snapshots, since, until, deals):
    """Official change between the snapshots nearest the window, beside local deals in that same interval."""
    before = [s for s in snapshots if s["observed_at"] <= since]
    inside = [s for s in snapshots if s["observed_at"] > since and (until is None or s["observed_at"] <= until)]
    if not inside or not (before or len(inside) > 1):
        return {"note": "fewer than two official snapshots around the window", "snapshots_inside": len(inside)}
    first, last = before[-1] if before else inside[0], inside[-1]
    between = [d for d in deals if first["observed_at"] < d["purchased_at"] <= last["observed_at"]]
    return {"from_observed_at": first["observed_at"], "to_observed_at": last["observed_at"],
            "official_surplus_delta": last["surplus"] - first["surplus"], "official_deals_delta": last["deals"] - first["deals"],
            "buyer_all_from": first["all"], "buyer_all_to": last["all"],
            "local_estimate_same_interval": sum(d["estimated_surplus"] for d in between), "local_deals_same_interval": len(between),
            "note": "Side by side only; a before/after difference is not a causal effect."}


def maturing_goods(root, deals, now):
    """Goods can break for 6 hours after sale, so a recent goods deal's surplus can still fall."""
    catalog = read_json(root / ".market/catalog.json") or {}
    goods = [d for d in deals if (catalog.get(d.get("sku")) or {}).get("kind") == "goods" and not d["broken"]
             and now - d["purchased_at"] < 6 * 3600]
    return {"deals": len(goods), "estimated_surplus_at_risk": sum(d["estimated_surplus"] for d in goods),
            "ledgers": [d["ledger"] for d in goods][:20]}


def offer_acceptance(root, index, payments, since, until):
    """Last offer against the first labeled ask, for negotiations started in the window. Descriptive only."""
    buckets = defaultdict(lambda: {"offers": 0, "receipt": 0, "paid": 0})
    for record in index.values():
        directory = Path(str(record.get("buyer_log_dir", "")).replace("\\", "/")).name
        started = directory.split("-")[0]
        offer = record.get("agreed_price")
        if offer is None or started < since or (until and started >= until):
            continue
        ask = first_ask(root, record)
        if not ask:
            continue
        ratio = offer / ask
        label = "at_or_above_ask" if ratio >= 1 else next(name for edge, name in OFFER_BUCKETS if ratio < edge)
        row = buckets[label]
        row["offers"] += 1
        row["receipt"] += isinstance(record.get("receipt"), dict)
        payment = payments.get(record["order"]) or {}
        row["paid"] += payment.get("paid") is True and payment.get("ledger") is not None
    return dict(sorted(buckets.items()))


def to_epoch(value):
    return datetime.strptime(value, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).timestamp()


def report(root, since=DAY_ZERO, until=None, now=None):
    root = Path(root)
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    deals, unmatched = settled_deals(root)
    index = negotiation_index(root)
    start, stop = to_epoch(since), to_epoch(until) if until else None
    rows, groups = [], defaultdict(list)
    for deal in deals:
        if deal["purchased_at"] < start or (stop and deal["purchased_at"] >= stop):
            continue
        record = index.get(deal["order"])
        row = {**deal, "surplus_per_list": deal["estimated_surplus"] / deal["value_at_purchase"]}
        if record:
            cap, budget = record.get("budget"), record.get("order_budget")
            ask = first_ask(root, record)
            row.update(price_per_cap=deal["price"] / cap if cap else None,
                       cap_per_list=cap / deal["value_at_purchase"] if cap else None,
                       price_per_order_budget=deal["price"] / budget if budget else None,
                       price_per_first_ask=deal["price"] / ask if ask else None)
        path = offer_path(root, record) if record else UNKNOWN_PATH
        groups[path].append(row)
        rows.append(row)
    snapshots = official_snapshots(root)
    reconciliation = None
    if snapshots:
        latest = snapshots[-1]
        counted = [d for d in deals if d["purchased_at"] <= latest["observed_at"]]
        total = sum(d["estimated_surplus"] for d in counted)
        reconciliation = {"official_surplus": latest["surplus"], "official_deals": latest["deals"],
                          "official_served": latest["served"], "official_observed_at": latest["observed_at"],
                          "estimated_surplus": total, "local_deals": len(counted),
                          "difference": total - latest["surplus"], "broken_events_unmatched": unmatched}
    return {"scope": "paid:true with ledger only", "status": "estimate; official value only in official_* fields",
            "window": {"since": since, "until": until, "deals": len(rows)},
            "by_path": {path: summarize(group) for path, group in sorted(groups.items())},
            "goods_still_within_break_window": maturing_goods(root, rows, now),
            "official_same_window": official_delta(snapshots, start, stop, deals),
            "reconciliation": reconciliation,
            "last_offer_per_first_ask": offer_acceptance(root, index, payment_outcomes(root / ".market/payments.jsonl"), since, until)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2026-11-03T00:00:00Z")
    parser.add_argument("--until")
    args = parser.parse_args()
    print(json.dumps(report(ROOT, stamp(args.since), stamp(args.until) if args.until else None),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
