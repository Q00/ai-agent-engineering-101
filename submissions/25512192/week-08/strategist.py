"""The strategist: a meta-harness. A stronger model reads what happened and rewrites the policy.

    python strategist.py                 # run once now
    python strategist.py --if-older 1800 # run only when the policy is older than 30 minutes (restock calls this)

Every deal is negotiated by haiku with the numbers in state/policy.json. Every STRATEGY_EVERY seconds this
script hands sonnet one page of evidence (the ledger stats, our stock, our sales, our buyer's payments and
breakages) and the current policy, and asks for a new policy with a reason per change. policy.clamp() keeps
the answer inside safe bounds; every run is appended to state/policy_history.jsonl, so the history of the
strategy is itself a log.
"""
import argparse
import collections
import json
import statistics
import sys
import time
from pathlib import Path

import harness
import market_memory as mm
import policy as pol

HERE = Path(__file__).resolve().parent
MD = HERE / ".market"
HISTORY = mm.STATE / "policy_history.jsonl"
BUYER_MEMORY = HERE / "buyer_memory.json"
STRATEGY_EVERY = 1800

SYSTEM = ("You are the strategist of a shop and a buyer agent in a simulated market. You never trade yourself: "
          "you set the numbers the trading agents use. Answer with exactly one JSON object and nothing else.")

PROMPT = """The market: shops buy stock from the market (cost: grade A 55-75%, B 40-60%, C 25-45% of list price),
then sell it to buyer agents who negotiate. Food loses grade while it waits and is thrown away at its date.
Goods in stock cost 1% of cost per day; some goods break within 6 h of the sale, and a warranty then refunds the
buyer from the shop. Our shop is ranked by 0.5 book (cash + stock at cost) + 0.3 revenue + 0.2 stars; our buyer by
0.5 saved + 0.3 forum votes + 0.1 number of deals + 0.1 number of posts. Saved (patch 3.6) is the mean over deals of
(list - price) / (list - unit cost), at most 1, -1 for food that did not last or goods that broke unrefunded; the
buyer's code already refuses a deal whose claimed grade puts it below MIN_SHARE. Both need 20+ deals.

EVIDENCE
{evidence}

CURRENT POLICY
{policy}

What each field does:
shop.open_ratio: opening ask / list price. shop.floor_margin: lowest price = cost x (1 + this).
shop.clearance_hours / clearance_floor: food with fewer hours left may go down to cost x clearance_floor.
shop.warranty_hours: warranty offered per goods grade (0-6). shop.stock_categories: up to 8 categories to stock
(exact names from the evidence; empty = the ledger's best sellers). shop.units_per_sku, shop.max_stock_share,
shop.food_min_shelf_hours: how much and what to stock.
buyer.offer_open_ratio: first offer / list. buyer.max_paid_ratio: the most the buyer pays / list.
buyer.goods_grades, buyer.require_warranty_goods, buyer.food_hours_margin: what the buyer accepts before paying.
The kit checks these only at payment, after the deal is agreed; the buyer cannot see shelf life while negotiating.
Food counts if it lasts use_within_hours at settle, which is right after payment, so a larger food_hours_margin
does not avoid bad food: it only refuses more agreed deals.

Change only what the evidence supports; small steps. Answer with the full new policy:
{{"shop": {{...}}, "buyer": {{...}}, "notes": "<one paragraph: what you changed and the evidence for it>"}}"""


def log(line):
    print(f"{time.strftime('%H:%M:%S')} [strategist] {line}", flush=True)


def jsonl(path, last=200):
    try:
        return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()[-last:] if l.strip()]
    except (OSError, ValueError):
        return []


def stock_lines():
    inv = mm.read_json(MD / "inventory.json", {})
    units = inv.get("units") or []
    if not inv:
        return ["Our shop: no inventory file yet."]
    now = time.time()
    by_cat = collections.Counter((u.get("category") or "?").split(">")[-1] for u in units)
    soon = sum(1 for u in units if u.get("expires_at") and u["expires_at"] - now < 12 * 3600)
    value = sum(u.get("cost") or 0 for u in units)
    return [f"Our shop: balance {inv.get('balance')}, start {inv.get('start')}, bankruptcy money {inv.get('injected')}, "
            f"{len(units)} units in stock at cost {value}, {soon} food units expire within 12 h",
            "Stock by category: " + ", ".join(f"{k} {v}" for k, v in by_cat.most_common(10))]


def sales_lines():
    sales = mm.our_sales(time.time() - 6 * 3600)
    if not sales:
        return ["Our sales in the last 6 h: none."]
    ratios = [s["price"] / s["list_price"] for s in sales if s.get("list_price")]
    margins = [s["price"] / s["cost"] - 1 for s in sales if s.get("cost")]
    return [f"Our sales in the last 6 h: {len(sales)}, median price/list {statistics.median(ratios):.2f}, "
            f"median margin over cost {statistics.median(margins):.2f}, total {sum(s['price'] for s in sales)}"]


def buyer_lines():
    pays = jsonl(MD / "payments.jsonl")
    broken = jsonl(MD / "broken.jsonl")
    mem = mm.read_json(BUYER_MEMORY, {"trades": []})
    r3 = [t for t in mem.get("trades", []) if t.get("round") == 3]
    paid = [p for p in pays if p.get("paid")]
    reasons = collections.Counter(p.get("reason", "?")[:60] for p in pays if not p.get("paid"))
    surplus = [t["list_price"] - t["price"] for t in r3 if t.get("paid") and t.get("list_price") and t.get("price")]
    walks = collections.Counter(t.get("result") for t in r3[-100:])
    return [f"Our buyer: {len(paid)} paid of the last {len(pays)} agreed deals; refused payments: "
            + (", ".join(f"{k} x{v}" for k, v in reasons.most_common(5)) or "none"),
            f"Our buyer's last 100 orders by result: {dict(walks)}; estimated surplus so far {sum(surplus)}; "
            f"broken goods reported: {len(broken)}"]


def evidence():
    stats = mm.read_json(mm.STATS, None) or mm.empty()
    return "\n".join([mm.summary(stats)] + stock_lines() + sales_lines() + buyer_lines())


def run(if_older=0):
    current = pol.load()
    if if_older and pol.age(current) < if_older:
        return current
    ev = evidence()
    out = harness.ask(SYSTEM, PROMPT.format(evidence=ev, policy=json.dumps(
        {k: current[k] for k in ("shop", "buyer")}, ensure_ascii=False)), log, "strategist",
        retries=1, timeout=180, model=harness.STRATEGY_MODEL)
    if not isinstance(out, dict) or not isinstance(out.get("shop"), dict):
        log("no usable answer; keeping the policy (and trying again at the next run)")
        return current
    new = pol.save(out)
    changed = {f"{side}.{k}": [current[side][k], new[side][k]] for side in ("shop", "buyer")
               for k in new[side] if new[side][k] != current[side][k]}
    mm.STATE.mkdir(exist_ok=True)
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "evidence": ev, "changed": changed, "notes": new["notes"]},
                           ensure_ascii=False) + "\n")
    log(f"policy updated: {json.dumps(changed, ensure_ascii=False)}; {new['notes']}")
    return new


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--if-older", type=float, default=0)
    run(ap.parse_args().if_older)
