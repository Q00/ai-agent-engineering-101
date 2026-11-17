"""Read-only live comparison against the saved policy-deployment baseline."""
import collections
import json
import re
import subprocess
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
NAME = "cheapest-in-town"


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


baseline = json.loads((ROOT / "logs/buyer-policy-deployment-baseline.json").read_text())
events = read_lines(ROOT / "logs/buyer-decisions.jsonl")
first_order = events[0]["order"]
console = (ROOT / "logs/buyer-console.log").read_text()
start = console.index("[order " + first_order + "]")
orders, completed, current_order = set(), set(), None
for line in console[start:].splitlines():
    match = re.search(r"\[order (o-[a-z0-9]+)\]", line)
    if match:
        current_order = match[1]
        orders.add(current_order)
    if "[market] ledger #" in line and current_order:
        completed.add(current_order)
payments = read_lines(ROOT / ".market/payments.jsonl")
new_attempts = [p for p in payments if p.get("order") in orders]
paid = [p for p in new_attempts if p.get("paid")]
catalog = json.loads((ROOT / ".market/catalog.json").read_text())
ready = {e["order"]: e for e in events if e.get("event") == "ready_for_payment"}


def discount(rows):
    values = []
    for p in rows:
        listed = catalog.get(p.get("item"), {}).get("list_price") or ready.get(p.get("order"), {}).get("list_price")
        if listed:
            values.append(1 - p["price"] / listed)
    return {"mean": sum(values) / len(values) if values else None, "count": len(values)}


response = httpx.get("https://market.ourolabs.ai/api/board?round=3", timeout=20)
response.raise_for_status()
board = response.json()
assert board["round"] == 3
buyer = next(b for b in board["buyers"] if b["name"] == NAME)
shop = next(s for s in board["shops"] if s["name"] == NAME)
fields = ["deals", "saved", "served", "surplus", "all", "forum", "posts", "orders"]
scored = [b for b in board["buyers"] if b.get("all") is not None]
scored.sort(key=lambda b: (-(b.get("all") or 0), -(b.get("saved") or 0)))
services = {}
for unit in ["ax-round3-buyer", "ax-round3-shop", "ax-round3-ollama", "market-buyer", "market-gpt-shop", "market-shop-tunnel"]:
    output = subprocess.check_output(["systemctl", "--user", "show", unit, "--property=ActiveState,UnitFileState,MainPID"], text=True)
    services[unit + ".service"] = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

report = {
    "market_http_date": response.headers.get("date"),
    "deployment_baseline": {k: baseline["buyer"].get(k) for k in fields},
    "current": {k: buyer.get(k) for k in fields},
    "buyer_rank_by_all_then_saved": {"rank": scored.index(buyer) + 1, "scored_buyers": len(scored)},
    "new_policy_orders_seen": len(orders),
    "new_policy_orders_completed": len(completed),
    "new_policy_paid": len(paid),
    "new_policy_payment_attempts": len(new_attempts),
    "new_policy_paid_fraction_of_completed": len(paid) / len(completed) if completed else None,
    "new_policy_mean_discount_at_purchase": discount(paid),
    "baseline_mean_discount_at_purchase": discount([p for p in payments[:baseline["payments_before"]] if p.get("paid")]),
    "new_policy_payments": [{k: p.get(k) for k in ["order", "shop", "item", "price", "paid", "ledger"]} for p in paid],
    "historical_failure_events_including_fixed_errors": dict(collections.Counter(e["reason"] for e in events if e.get("reason"))),
    "shop_current": {k: shop.get(k) for k in ["book", "deals", "all", "refunds", "stock_cost", "stock_units"]},
    "services": services,
    "paid_model_fallback": False,
    "forum_posts_or_votes_sent": False,
    "limits": "Small sequential observation, not controlled experiment. Purchase discount precedes later breakage/refunds. SAVED has a 100-zero-deal prior. Relative ranking and score can fall despite improving purchase performance. Rank excludes rows without an ALL score.",
}
(ROOT / "logs/buyer-evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
(ROOT / "logs/buyer-decision-sample.json").write_text(json.dumps(events[-50:], ensure_ascii=False, indent=2) + "\n")
print(json.dumps({k: v for k, v in report.items() if k not in ["new_policy_payments", "historical_failure_events_including_fixed_errors"]}, ensure_ascii=False, indent=2))
