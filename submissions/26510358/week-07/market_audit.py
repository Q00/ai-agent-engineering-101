"""Offline market comparison from explicitly captured public data and own trace.

No network, price changes, reviews or agent decisions. Output is create-only.
The trades endpoint is a bounded sample; local receipts are not market deals.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median


def read(path):
    return json.loads(Path(path).read_text())


def audit(args):
    board, trades = read(args.board), read(args.trades)
    shops = board["shops"]
    own = next(s for s in shops if s["name"] == args.shop)
    round_number = board["round"]
    sample = [t for t in trades if t["round"] == round_number]
    counted = [t for t in sample if t.get("counted")]
    totals = Counter(t["shop"] for t in counted)
    sample_results = Counter(t["shop"] for t in sample)
    prices = defaultdict(list)
    for trade in counted:
        prices[(trade["shop"], trade["item"])].append(trade["price"])
    above = sorted((s for s in shops if s["deals"] > own["deals"]), key=lambda s: s["deals"])
    snapshot = lambda s: {k: s[k] for k in ("name", "alive", "deals", "share", "stars", "reviews", "visits")}
    started, feasible, receipts = set(), set(), set()
    events, failures, stages = Counter(), Counter(), Counter()
    latencies, missing_times, feasible_failures = [], [], set()
    trace_rows, malformed_rows = 0, 0
    with Path(args.trace).open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                malformed_rows += 1
                continue
            if not args.since <= row.get("time", "") <= args.until:
                continue
            trace_rows += 1
            event = row.get("event")
            task = row.get("task_id")
            events[event] += 1
            if event == "trade_stage":
                stages[row["stage"]] += 1
                if row["stage"] == "negotiation_started":
                    started.add(task)
                if row["stage"] == "receipt_issued":
                    receipts.add(task)
            if event == "harness_request":
                context = row["context"]
                if (context.get("offer") is not None and not context.get("opening")
                        and context["offer"] >= context["reserve"]):
                    feasible.add(context["task_id"])
            if event == "harness_exit":
                latencies.append(row["elapsed"])
            if event == "negotiation_failed":
                detail = row["detail"]
                failures[detail] += 1
                feasible_failures.add(task)
                if "No such file or directory: 'codex'" in detail:
                    missing_times.append(row["time"])
    ledger = read(args.ledger) if args.ledger else []
    own_results = [dict(time=datetime.fromtimestamp(e["ts"], timezone.utc).isoformat(),
                        **{k: e["data"].get(k) for k in ("id", "buyer", "verified", "stars", "review", "checks")})
                   for e in ledger if e["kind"] == "result" and e["round"] == round_number
                   and e["data"].get("shop", "").rstrip("/") == own["card"]["url"].rstrip("/")]
    return {
        "sources": {k: str(getattr(args, k)) for k in ("board", "trades", "trace", "ledger")},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "round": round_number, "registered_shops": len(shops),
        "own": {**snapshot(own), "rank_by_counted_deals": 1 + len(above),
                "share_recomputed": own["deals"] / board["totals"]["deals"] if board["totals"]["deals"] else None},
        "next_rank": {**snapshot(above[0]), "additional_deals_to_pass_if_peer_stops": above[0]["deals"] - own["deals"] + 1} if above else None,
        "leaders": [snapshot(s) for s in sorted(shops, key=lambda s: s["deals"], reverse=True)[:10]],
        "recent_sample": {
            "rows": len(sample), "counted_deals": len(counted),
            "from": datetime.fromtimestamp(min(t["ts"] for t in sample), timezone.utc).isoformat() if sample else None,
            "to": datetime.fromtimestamp(max(t["ts"] for t in sample), timezone.utc).isoformat() if sample else None,
            "shops": [{"name": name, "counted_deals": totals[name], "results": sample_results[name],
                       "share_of_counted_sample": totals[name] / len(counted) if counted else None}
                      for name in sorted(set(sample_results), key=lambda name: (-totals[name], name))],
            "prices_by_shop_item": [{"shop": shop, "item": item, "n": len(values), "min": min(values),
                                     "mean": sum(values) / len(values), "max": max(values)}
                                    for (shop, item), values in sorted(prices.items())]},
        "own_trace": {"since": args.since, "until": args.until, "rows": trace_rows,
                      "malformed_rows": malformed_rows, "events": events, "stages": stages,
                      "negotiations_started": len(started), "negotiation_failures": failures,
                      "cli_missing_first": min(missing_times) if missing_times else None,
                      "cli_missing_last": max(missing_times) if missing_times else None,
                      "feasible_followup_offer_tasks": len(feasible),
                      "feasible_tasks_with_local_receipt": len(feasible & receipts),
                      "feasible_tasks_with_explicit_failure": len(feasible & feasible_failures),
                      "harness_latency_n": len(latencies),
                      "harness_latency_median_seconds": median(latencies) if latencies else None,
                      "harness_latency_p95_seconds": sorted(latencies)[int((len(latencies) - 1) * .95)] if latencies else None},
        "ledger_results_for_current_own_url": own_results,
        "caveats": ["Board rank is cumulative within the selected round; share is rounded by the API.",
                    "The recent trades sample is bounded and is not the whole market or a controlled comparison.",
                    "Board visits and counted deals are different event populations; their ratio is not a measured conversion rate.",
                    "Trace coverage is one explicitly selected seller process and a bounded observation window.",
                    "Tasks may start before the window or finish after it; absent receipts do not prove abandonment.",
                    "A feasible numeric offer is a local observation, not proof of a valid funded market order.",
                    "Local signed receipts are not counted as market-verified deals.",
                    "The next-rank gap assumes the competing shop receives no additional deals; it is not an ETA.",
                    "Results for the current registered URL do not cover historical shop URLs.",
                    "Runtime recovery started before this patch; before/after observations do not establish a causal effect."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("board", "trades", "trace", "since", "until", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--ledger")
    parser.add_argument("--shop", default="myeonggi")
    args = parser.parse_args()
    result = audit(args)
    with Path(args.output).open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"output": args.output, "rank": result["own"]["rank_by_counted_deals"],
                      "deals": result["own"]["deals"], "sample": result["recent_sample"]["rows"]}))
