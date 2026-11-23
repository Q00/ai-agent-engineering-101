"""Recompute report numbers from saved evidence; no network/model/state changes."""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "logs/report-evidence-20261123"


def board_metrics(path):
    board = json.loads(path.read_text())
    assert board["round"] == 3
    # Match board.html econPlayers: stable descending student_total, display i + 1.
    students = sorted((p for p in board["players"] if p.get("student")),
                      key=lambda p: -p["student_total"])
    rank, player = next((i + 1, p) for i, p in enumerate(students)
                        if p["player"] == "dyishappy")
    shop = next(p for p in board["shops"] if p["name"] == "dyishappy")
    buyer = next(p for p in board["buyers"] if p["name"] == "dyishappy")
    return {
        "class_rank_display": rank, "class_size": len(students),
        "class_equal_score_count": sum(p["student_total"] == player["student_total"] for p in students),
        "class_total": player["student_total"], "class_percentile": player["student_pct"],
        "shop": {k: shop[k] for k in ("deals", "all", "book", "revenue", "stars", "refunds")},
        "buyer": {k: buyer[k] for k in ("deals", "all", "saved", "orders", "posts")},
    }


def runtime_metrics():
    path = ROOT / "logs/market-kit.log"
    raw = path.read_bytes()
    provenance = json.loads((EVIDENCE / "provenance.json").read_text())
    assert hashlib.sha256(raw).hexdigest() == provenance["market_kit_log_sha256"]
    counts = Counter()
    reasons = Counter()
    fast_skips = Counter()
    quotes = {}
    paid_ids = set()
    orders = set()
    lines = raw.decode().splitlines()
    for number, line in enumerate(lines, 1):
        paid = re.search(r"\[acp\] paid .* settled at ledger #(\d+):", line)
        if paid:
            paid_ids.add(paid[1])
            quotes.setdefault("settlement", {"line": number, "text": line})
        order = re.search(r"\[order ([^\]]+)\].*round 3", line)
        if order:
            orders.add(order[1])
        if "[market] ledger #" in line:
            counts["market_result_lines"] += 1
            if "no deal" in line:
                counts["market_no_deal_lines"] += 1
        if "[acp] not paying:" in line:
            reason = ("warranty_below_policy" if "warranty" in line else
                      "shelf_life_below_policy" if "shelf life" in line else
                      "grade_outside_policy" if "grade" in line else "other")
            reasons[reason] += 1
            quotes.setdefault(reason, {"line": number, "text": line})
        if "FAST ACCEPT SKIPPED: " in line:
            detail = json.loads(line.split("FAST ACCEPT SKIPPED: ", 1)[1])
            fast_skips[detail["reason"]] += 1
        if "[market] order failed: 503" in line and "Round 3 ended" in line:
            counts["market_closed_order_polls"] += 1
            quotes.setdefault("market_closed", {"line": number, "text": line})
    return {
        "scope": "Saved log fragment, not full-round counts; clock date/offset not reconstructed.",
        "bytes": len(raw), "lines": len(lines), "round3_order_ids": len(orders),
        "unique_acp_paid_ledger_ids": len(paid_ids),
        "counts": dict(counts), "payment_policy_refusals": dict(reasons),
        "fast_accept_skip_decisions": dict(fast_skips), "quotes": quotes,
        "note": "ACP settlement and later market review ledger lines are not two purchases. Closed-market polls are not trading failures.",
    }


def main():
    provenance = json.loads((EVIDENCE / "provenance.json").read_text())
    for name, digest in provenance["sha256"].items():
        assert hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest() == digest
    baseline = board_metrics(ROOT / "logs/food-strategy-baseline-20261110T1030Z/board.json")
    current = board_metrics(EVIDENCE / "board.json")
    result = {
        "baseline_market_time_kst": "2026-11-10T19:30:03+09:00",
        "submission_board_http_date": next(line for line in (EVIDENCE / "board.headers").read_text().splitlines()
                                           if line.lower().startswith("date:")),
        "baseline": baseline, "submission_board": current,
        "delta": {"shop_deals": current["shop"]["deals"] - baseline["shop"]["deals"],
                  "buyer_deals": current["buyer"]["deals"] - baseline["buyer"]["deals"],
                  "shop_book": current["shop"]["book"] - baseline["shop"]["book"],
                  "shop_revenue": current["shop"]["revenue"] - baseline["shop"]["revenue"]},
        "runtime_fragment": runtime_metrics(),
        "limitations": [
            "Post-close queried board has no explicit freeze timestamp; exact closing snapshot not independently established.",
            "Buyer Saved rule changed on November 12; before/after values do not isolate strategy performance.",
            "Cumulative board totals and bounded diagnostic windows have different denominators.",
            "Baseline Class displayed rank includes a tie; match original page order, not a new tie-breaking rule.",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
