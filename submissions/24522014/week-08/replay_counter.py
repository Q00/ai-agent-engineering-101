"""Read-only replay: which past over-budget quotes the counteroffer rule would have targeted, if it were enabled.

A seller's answer to an offer it never received is unknown, so this counts eligibility and estimates no acceptance.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median

from buyer_counter import evaluate
from buyer_fast import classify
from buyer_funnel import ROOT, load_runs, read_json, stamp
from quote_terms import check_quote
from replay_quote_filter import quotes

PROPOSED = {"enabled": True, "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.10}


def was_over_budget_refusal(root, record, directory):
    """model_quota_hour + over_budget from the trail, or an unlogged walk-away on an over-budget quote."""
    trail = record.get("decision_trail")
    if trail is not None:
        return any(e.get("reason_code") == "model_quota_hour" and e.get("parser_reason") == "over_budget" for e in trail)
    summary = read_json(directory / "outcome.json") or {}
    if summary.get("outcome") != "walked_away":
        return False
    return read_json(directory / f"decision-{summary.get('turns', 0):02d}.json") is None


def replay(root, since, until=None, config=PROPOSED):
    root = Path(root)
    catalog = read_json(root / ".market/catalog.json") or {}
    tally, reasons, filtered, ratios, offers_per_ask, surplus = Counter(), Counter(), Counter(), [], [], []
    for name, record in load_runs(root, since, until):
        if record.get("outcome") == "research_skipped" or not record.get("buyer_log_dir"):
            continue
        directory = root / "logs/buyer" / Path(str(record["buyer_log_dir"]).replace("\\", "/")).name
        if not was_over_budget_refusal(root, record, directory):
            continue
        replies = quotes(directory)
        if not replies:
            continue
        task, text = replies[-1]
        cap = record.get("budget")
        _, code, ask = classify(text, cap)
        if code != "over_budget":
            tally[f"not_over_budget:{code}"] += 1
            continue
        tally["over_budget_refusals"] += 1
        now = datetime.strptime(name, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).timestamp()
        accept = record.get("accept_conditions")
        check = check_quote(task, text, accept, now) if accept else None
        if check and check["reason_code"]:
            filtered[check["reason_code"]] += 1
            continue
        list_price = (catalog.get(record.get("item")) or {}).get("list_price")
        decision, result = evaluate(ask, cap, list_price, config=config)
        reasons[result["reason_code"]] += 1
        if decision:
            ratios.append(result["ask_per_cap"])
            offers_per_ask.append(cap / ask)
            surplus.append(result["surplus_per_list"])
    def spread(values):
        return {"min": round(min(values), 4), "median": round(median(values), 4), "max": round(max(values), 4)} if values else None
    return {"window": {"since": since, "until": until}, "config_replayed": config, "tally": dict(tally),
            "filter_first": dict(filtered.most_common()), "counter_outcome": dict(reasons.most_common()),
            "eligible_ask_per_cap": spread(ratios), "eligible_offer_per_ask": spread(offers_per_ask),
            "eligible_surplus_per_list": spread(surplus),
            "note": "Eligibility only. Whether a seller would accept is unknown and not estimated."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until")
    args = parser.parse_args()
    print(json.dumps(replay(ROOT, stamp(args.since), stamp(args.until) if args.until else None), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
