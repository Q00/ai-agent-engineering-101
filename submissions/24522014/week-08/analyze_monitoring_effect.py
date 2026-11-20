"""Observational R3 before/after analysis; does not modify trading policies."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from market_state import read, write

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))


def main():
    all_snapshots = [(p, read(p)) for p in (ROOT/"logs/market-intel").glob("*.json")]
    snapshots = sorted([pair for pair in all_snapshots if pair[1].get("round") == 3], key=lambda pair: pair[1]["observed_at"])
    opening = snapshots[0]
    baseline = min(snapshots, key=lambda pair: abs(pair[1]["observed_at"]-1793717781.8516445))
    latest = snapshots[-1]
    journals = sorted([read(p) for p in (ROOT/"logs/strategy-controller").glob("*.json")], key=lambda j: j["at"])
    state = read(ROOT/".market/strategy-state.json")
    def metrics(pair):
        path, snapshot = pair
        c = snapshot["competition"]
        return {"file": path.name, "at": snapshot["observed_at"],
                "time_kst": datetime.fromtimestamp(snapshot["observed_at"], KST).isoformat(),
                "rank": c["rank"], "participants": c["player_count"], "gap_to_third": c["gap_to_third"],
                "player_total": (c.get("official_player") or {}).get("total"),
                "shop": c["own"]["shops"], "buyer": c["own"]["buyers"]}
    a, b, first = metrics(baseline), metrics(latest), metrics(opening)
    changes = {}
    for group, fields in {"shop": ("deals", "revenue", "profit", "stars", "visits", "refunds", "broken", "bankruptcies"),
                          "buyer": ("deals", "surplus", "forum", "posts", "orders")}.items():
        for field in fields:
            x, y = a[group].get(field), b[group].get(field)
            if type(x) in (int, float) and type(y) in (int, float):
                changes[f"{group}.{field}"] = {"before": x, "after": y, "delta": y-x,
                                               "percent": (y/x-1)*100 if x else None}
    def period(start, end):
        hours = (end["at"]-start["at"])/3600
        shop_deals = end["shop"]["deals"]-start["shop"]["deals"]
        revenue = end["shop"]["revenue"]-start["shop"]["revenue"]
        buyer_deals = end["buyer"]["deals"]-start["buyer"]["deals"]
        surplus = end["buyer"]["surplus"]-start["buyer"]["surplus"]
        orders = end["buyer"]["orders"]-start["buyer"]["orders"]
        return {"hours": hours, "shop_deals_per_hour": shop_deals/hours,
                "revenue_per_hour": revenue/hours, "average_sale": revenue/shop_deals if shop_deals else None,
                "buyer_deals_per_hour": buyer_deals/hours, "buyer_surplus_per_deal": surplus/buyer_deals if buyer_deals else None,
                "buyer_order_conversion": buyer_deals/orders if orders else None}
    # Exact local seller receipts supplement the bounded public sample for analysis only.
    offers = [read(p) for p in (ROOT/".market/signed-offers").glob("*.json")]
    unit_skus = {o["unit"]: o["sku"] for o in offers}
    own_units = set(unit_skus)
    text = (ROOT/"logs/market-kit.log").read_text(encoding="utf8", errors="replace")
    match = re.search(r"\d\d:\d\d:\d\d \[market\] shop registered as kcwarni, round 3", text)
    suffix = text[match.start():] if match else ""
    day = datetime.fromtimestamp(first["at"], KST).date()
    previous_seconds = None
    local = {}
    pattern = re.compile(r"\[acp\] settled (.+) at (\d+), ledger #(\d+), unit (\S+) grade (\S+)")
    for line in suffix.splitlines():
        clock = re.match(r"(\d\d):(\d\d):(\d\d) ", line)
        if not clock:
            continue
        h, m, s = map(int, clock.groups()); seconds = h*3600+m*60+s
        if previous_seconds is not None and seconds < previous_seconds-43200:
            day += timedelta(days=1)
        previous_seconds = seconds
        sale = pattern.search(line)
        if sale:
            name, price, seq, unit, grade = sale.groups()
            at = datetime.combine(day, datetime.min.time(), KST).timestamp()+seconds
            local[int(seq)] = {"at": at, "sku": unit_skus.get(unit), "unit": unit, "grade": grade, "price": int(price), "name": name}
    by_time = {round(s["observed_at"], 3): s for _, s in snapshots}
    blocked_examples = []
    for journal in journals:
        obs = journal["observation"]
        starts = [h for h in state["history"] if 7200 <= journal["at"]-h["at"] <= 10800]
        if not starts:
            continue
        start_at = max(starts, key=lambda h: h["at"])["at"]
        snapshot = by_time.get(round(obs.get("market_at", 0), 3), {})
        for event in journal["events"]:
            if event["event"] != "diagnosis":
                continue
            for candidate in event.get("candidates", []):
                sku, grade = candidate.get("sku"), candidate.get("grade")
                if not sku:
                    continue
                public = {r["seq"] for r in snapshot.get("rows", []) if r.get("kind") == "settle" and r.get("round") == 3
                          and start_at < r["ts"] <= journal["at"] and r.get("data", {}).get("unit") in own_units
                          and r["data"].get("item") == sku and r["data"].get("grade") == grade
                          and not r["data"].get("warranty_hours")}
                complete_local = [r for r in local.values() if r["sku"] == sku and r["grade"] == grade
                                  and start_at < r["at"] <= journal["at"]]
                blocked_examples.append({"time_kst": datetime.fromtimestamp(journal["at"], KST).isoformat(),
                                         "sku": sku, "grade": grade, "market_comparables": candidate.get("sample_count"),
                                         "quote_gap_percent": candidate.get("gap_ratio", 0)*100,
                                         "own_public_sample_count": len(public), "own_local_settled_count": len(complete_local)})
    event_counts = Counter(e["event"] for j in journals for e in j["events"])
    all_local = [r for r in local.values() if r["at"] <= b["at"]]
    captured_local = {r["seq"] for r in latest[1].get("rows", []) if r.get("kind") == "settle" and r.get("round") == 3
                      and r.get("data", {}).get("unit") in own_units}
    result = {"baseline": a, "latest": b, "changes": changes,
              "early_period": period(first, a), "later_period": period(a, b),
              "controller": {"journals": len(journals), "events": dict(event_counts), "phase": state["phase"],
                             "controls": state["controls"], "blocked_candidate_observations": blocked_examples},
              "evidence_coverage": {"local_seller_settlements_to_latest": len(all_local),
                                    "own_settlements_in_latest_public_sample": len(captured_local),
                                    "sample_skipped_ranges": latest[1].get("skipped_seq_ranges", [])},
              "note": "Observed before/after growth is not causal proof of monitoring or autonomous experiments; start/keep counts are separate."}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = ROOT/"logs/monitoring-effect"/(stamp+"-"+uuid4().hex[:8]+".json")
    write(path, result)
    print(json.dumps({"saved": str(path), "changes": changes, "early_period": result["early_period"],
                      "later_period": result["later_period"], "events": dict(event_counts),
                      "coverage": result["evidence_coverage"], "candidate_examples": blocked_examples[-5:]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
