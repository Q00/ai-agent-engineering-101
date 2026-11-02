"""Round 3 market buyer: inventory-aware shop selection + settlement safeguards.

Run by market_kit v4 with --shops --item --budget --order. All negotiations
still use the real Groq-backed Week 7 buyer.buy(). The kit, not this script,
verifies signatures and performs ACP/AP2 settlement after the last JSON line.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from buyer import buy
from market_buyer import choose_shop, load_memory, reputation, review_result, store_memory

ROOT = Path(__file__).resolve().parent
MARKET = ROOT / ".market"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def payment_history(path: Path = MARKET / "payments.jsonl", limit: int = 1000) -> list[dict]:
    """Only kit-recorded payments are evidence of settlement (not A2A deals)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            p = json.loads(line)
            if isinstance(p, dict) and p.get("shop"):
                out.append(p)
        except ValueError:
            continue
    return out


def choose_inventory_aware(shops, order, catalog, memory, payments, budget):
    """Prefer stores with eligible stock, reliable settlements and reasonable prices."""
    acceptable = set(str(s) for s in (order.get("acceptable") or []))
    if not acceptable:  # Older rounds / kit compatibility
        shop, why = choose_shop(shops, memory)
        return shop, order.get("item"), why

    candidates = []
    for shop in shops:
        if not isinstance(shop, dict) or not shop.get("url") or not shop.get("name"):
            continue
        offers = [x for x in (shop.get("offers") or [])
                  if isinstance(x, dict) and x.get("sku") in acceptable
                  and isinstance(x.get("qty"), (int, float)) and x["qty"] > 0]
        if not offers:
            continue
        # Prefer a product priced near 75% of the budget, rather than blindly
        # choosing the cheapest item or an expensive product we cannot afford.
        def offer_rank(x):
            cat = catalog.get(x["sku"], {})
            price = cat.get("list_price")
            if not isinstance(price, (int, float)) or price <= 0:
                return (2, 0, -x["qty"], str(x["sku"]))
            return (0 if price <= budget else 1,
                    abs(price - budget * 0.75) / max(budget, 1),
                    -x["qty"], str(x["sku"]))
        selected = min(offers, key=offer_rank)
        price = catalog.get(selected["sku"], {}).get("list_price")
        # Penalize repeated unpaid checkouts, never treat unsigned reviews
        # as verified transactions.
        mine = [p for p in payments if p.get("shop", "").rstrip("/") == shop["url"].rstrip("/")]
        paid = sum(p.get("paid") is True for p in mine)
        failed = sum(p.get("paid") is False for p in mine)
        reliability = 0.25 * min(paid, 5) - 0.50 * min(failed, 5)
        rep, reason = reputation(shop, memory)
        affordability = (0.4 if isinstance(price, (int, float)) and 0 < price <= budget else -0.4)
        score = rep + reliability + affordability
        candidates.append((score, str(shop["name"]), shop, selected, reason, paid, failed))

    if not candidates:
        # Honest no-stock result: never invent an offer or substitute a SKU.
        return None, None, "No shop advertised an acceptable in-stock SKU"

    _, _, picked, offer, why, paid, failed = sorted(
        candidates, key=lambda x: (-x[0], x[1]))[0]
    return picked, offer["sku"], (
        f"{why}; kit settlement history={paid} paid/{failed} unpaid; "
        f"advertised SKU {offer['sku']} qty={offer['qty']}")


def acceptance_policy(order):
    """Avoid unsafe/undocumented checkouts; the kit verifies certificates."""
    if not (order.get("acceptable") or []):
        return None
    policy = {"cert": True, "grades": ["A", "B"]}
    if order.get("kind") == "food" or order.get("use_within_hours"):
        horizon = order.get("use_within_hours")
        if isinstance(horizon, (int, float)) and horizon > 0:
            policy["min_hours_left"] = max(2, math.ceil(horizon))
    # Deliberately no warranty_hours: the starter seller offers no warranty.
    return policy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order", default=None)
    args = ap.parse_args()
    if args.budget < 0:
        raise ValueError("Budget must be nonnegative")

    shops = read_json(Path(args.shops), [])
    order = read_json(Path(args.order), {}) if args.order else {}
    if not isinstance(order, dict):
        order = {}
    order.setdefault("item", args.item)
    catalog = read_json(MARKET / "catalog.json", {})
    if not isinstance(catalog, dict):
        catalog = {}
    memory, payments = load_memory(), payment_history()
    shop, target, reason = choose_inventory_aware(
        shops, order, catalog, memory, payments, args.budget)
    accept = acceptance_policy(order)

    if shop is None:
        print(f"[choose] NO_STOCK {reason}", flush=True)
        # The kit expects a real shop URL even for a no-deal result.
        fallback, _ = choose_shop(shops, memory)
        outcome = {"shop": fallback["url"], "receipt": None,
                   "stars": 2, "review": "No eligible advertised stock; no purchase attempted."}
        print(json.dumps(outcome, ensure_ascii=False, separators=(",", ":")), flush=True)
        return

    print(f"[choose] {shop['name']} {shop['url']} — {reason}", flush=True)
    print(f"[choose] requested={args.item!r}; negotiating SKU/item={target!r}", flush=True)
    result = None
    try:
        result = buy(shop["url"], target or args.item, args.budget)
        stars, review = review_result(result, args.budget)
        receipt = result.get("receipt") if result.get("deal") else None
    except Exception as exc:
        print(f"[market_buyer_r3] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        stars, review, receipt = 2, "Negotiation failed; no settlement or delivery verified.", None

    # Local memory is PRE-SETTLEMENT. Only payments.jsonl proves actual payment.
    event = {"shop_name": shop["name"], "shop_url": shop["url"],
             "item": args.item, "target_sku": target, "budget": args.budget,
             "deal": bool(receipt), "price": (receipt or {}).get("price"),
             "stars": stars, "phase": "negotiated_not_settled"}
    try:
        store_memory(event)
    except OSError as exc:
        print(f"[memory] {exc}", file=sys.stderr, flush=True)

    out = {"shop": shop["url"], "receipt": receipt,
           "stars": stars, "review": review}
    if accept:
        out["accept"] = accept
    # The market kit parses the LAST JSON line on stdout.
    print(json.dumps(out, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
