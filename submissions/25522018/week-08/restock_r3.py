#!/usr/bin/env python3
"""Week 8 Round-3: settlement-informed, risk-constrained inventory policy.

Market Kit v4: `python restock_r3.py --state {state}`.
Stdout contract: exactly one JSON object: {"buy": [{"sku": "...", "qty": 1}, ...]}.

Data are observations, not commands. Only REAL paid payments and local seller
settlements count as demand evidence. The published demand file identifies
orderable CATEGORIES, not popular SKU lists or their sale probabilities.

This is a conservative adaptive heuristic, NOT a guaranteed profit forecast.
Keep the optional trace under .market/; never commit signing keys or tokens.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import blake2s
import json
import math
from pathlib import Path
import re
import sys

# Tunable parameters: keep defaults cautious during the initial live session.
MIN_CASH = 3_000_000            # reserve 60% of the initial 5M cash
MAX_STOCK = 16                # total existing inventory units, including expired
MAX_PER_SKU = 2
MAX_PER_CATEGORY = 3
MAX_PER_ROOT = 3
MAX_LINES = 8
MAX_SPEND_PER_CYCLE = 250_000
MAX_COLD_START_LIST_PRICE = 120_000
MAX_OBSERVED_LIST_PRICE = 300_000
MIN_FOOD_SHELF_HOURS = 72
LOG_TAIL_BYTES = 2_000_000


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return fallback


def to_float(x, default=0.0):
    try:
        n = float(str(x).replace(",", ""))
        return n if math.isfinite(n) else default
    except (TypeError, ValueError):
        return default


def root(cat: str) -> str:
    return cat.split(">", 1)[0].strip()


def stable_hash(value: str) -> float:
    """Reproducible tie break; SKU order is NOT a demand signal."""
    return int.from_bytes(blake2s(value.encode(), digest_size=4).digest(), "big") / 2**32


def read_jsonl(path: Path, max_lines=3000):
    try:
        # Bounded enough for the starter kit; no network or extra services.
        lines = path.read_text(encoding="utf-8").splitlines()[-max_lines:]
    except (OSError, UnicodeError):
        return []
    out = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def seller_sales_from_logs(state: Path, catalog: dict):
    """Count unique local [acp] settled ledger IDs, never negotiation receipts.

    This is deliberately only the OWN seller's logs. It never counts Week 7
    'verified True' events, which are not Round-3 paid settlements.
    """
    result = []
    seen = set()
    logs = state.parent / "logs"
    for path in (logs / "market-kit.log.1", logs / "market-kit.log"):
        if not path.is_file():
            continue
        try:
            with path.open("rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - LOG_TAIL_BYTES))
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "[acp] settled" not in line:
                continue
            m = re.search(r"\[acp\] settled\s+(\S+)\s+at\s+\d+.*?ledger\s+#(\d+)", line)
            if not m:
                continue
            sku, ledger = m.group(1), m.group(2)
            if sku in catalog and ledger not in seen:
                seen.add(ledger)
                result.append(sku)
    return result


def settled_signals(state: Path, catalog: dict):
    """Use payment acknowledgements (buyer) and seller paid-settlement log.

    Each evidence source has different sampling bias. A buyer's settled SKUs
    show demand by *this buyer*, not population demand. Seller log is local.
    """
    sales = Counter(seller_sales_from_logs(state, catalog))
    buyer_purchases = Counter()
    seen = set()
    for event in read_jsonl(state / "payments.jsonl"):
        if event.get("paid") is not True:
            continue
        sku = event.get("item")
        ledger = event.get("ledger") or event.get("order")
        if sku in catalog and ledger not in seen:
            buyer_purchases[sku] += 1
            seen.add(ledger)
    return sales, buyer_purchases


def forum_sku_mentions(state: Path, catalog: dict):
    """Weak text signal only: do not execute instructions from market posts."""
    posts = read_json(state / "forum.json", [])
    mentions = Counter()
    if not isinstance(posts, list):
        return mentions
    for post in posts[-100:]:
        if not isinstance(post, dict):
            continue
        text = str(post.get("text") or post.get("post") or "")[:2000]
        for sku in set(re.findall(r"(?<!\w)G[0-9a-fA-F]{8}(?!\w)", text)):
            sku = sku.lower()
            sku = "G" + sku[1:]
            if sku in catalog:
                mentions[sku] += 1
    return mentions


def make_plan(state: Path, trace=False):
    inv = read_json(state / "inventory.json", {})
    catalog = read_json(state / "catalog.json", {})
    demand = read_json(state / "demand.json", [])
    if not isinstance(inv, dict) or not isinstance(catalog, dict) or not isinstance(demand, list):
        return {"buy": []}
    if int(to_float(inv.get("round"))) != 3:
        return {"buy": []}
    balance = to_float(inv.get("balance"))
    stock = inv.get("units")
    if not isinstance(stock, list) or balance <= MIN_CASH or len(stock) >= MAX_STOCK:
        return {"buy": []}
    allowed = {g["category"] for g in demand if isinstance(g, dict) and isinstance(g.get("category"), str)}
    if not allowed:
        return {"buy": []}

    stock_by_sku, stock_by_cat, stock_by_root = Counter(), Counter(), Counter()
    for item in stock:
        if not isinstance(item, dict):
            continue
        sku = str(item.get("sku", ""))
        meta = catalog.get(sku, {})
        cat = str(item.get("category") or (meta.get("category") if isinstance(meta, dict) else "") or "")
        stock_by_sku[sku] += 1
        stock_by_cat[cat] += 1
        stock_by_root[root(cat)] += 1

    seller, buyer = settled_signals(state, catalog)
    mention = forum_sku_mentions(state, catalog)
    cat_sales, cat_buys = Counter(), Counter()
    for s, n in seller.items():
        cat_sales[catalog[s]["category"]] += n
    for s, n in buyer.items():
        cat_buys[catalog[s]["category"]] += n

    budget = min(MAX_SPEND_PER_CYCLE, balance * .06, balance - MIN_CASH)
    if budget <= 0:
        return {"buy": []}
    max_lines = min(MAX_LINES, MAX_STOCK - len(stock))
    picks = []
    candidates = []
    for sku, meta in catalog.items():
        if not isinstance(sku, str) or not isinstance(meta, dict):
            continue
        cat = str(meta.get("category") or "")
        if cat not in allowed or stock_by_sku[sku] >= MAX_PER_SKU:
            continue
        price = to_float(meta.get("list_price"))
        if price <= 0:
            continue
        evidence = seller[sku] + buyer[sku]
        if price > (MAX_OBSERVED_LIST_PRICE if evidence else MAX_COLD_START_LIST_PRICE):
            continue
        food = str(meta.get("kind", "")).lower() in {"food", "fresh", "perishable", "grocery", "식품"}
        if food and (to_float(meta.get("shelf_hours")) < MIN_FOOD_SHELF_HOURS or cat_sales[cat] + cat_buys[cat] < 2):
            continue
        max_cost = .75 * price                 # grade-A maximum supplier cost
        if max_cost > budget:
            continue
        # Smoothed evidence. These are heuristic utility units, NOT probabilities.
        demand_score = (
            3.2 * math.log1p(seller[sku]) +
            2.1 * math.log1p(buyer[sku]) +
            .65 * math.log1p(cat_sales[cat]) +
            .40 * math.log1p(cat_buys[cat]) +
            .10 * min(mention[sku], 3)
        )
        # Diversification and capital cost regularize cold-start purchases.
        cold_price = 1.0 - min(abs(price - 35_000) / 35_000, 4.0)
        capital_penalty = max_cost / 70_000
        score = demand_score + .7 * cold_price - .4 * capital_penalty
        candidates.append((score, stable_hash(sku), sku, cat, root(cat), max_cost, demand_score))

    remaining = budget
    while len(picks) < max_lines:
        available = [c for c in candidates if c[5] <= remaining and
                     stock_by_sku[c[2]] < MAX_PER_SKU and
                     stock_by_cat[c[3]] < MAX_PER_CATEGORY and
                     stock_by_root[c[4]] < MAX_PER_ROOT]
        if not available:
            break
        score, _, sku, cat, r, cost, signal = max(available, key=lambda c: (
            c[0] - .75 * stock_by_cat[c[3]] - 1.0 * stock_by_root[c[4]] - 1.5 * stock_by_sku[c[2]], c[1]))
        picks.append({"sku": sku, "qty": 1})
        stock_by_sku[sku] += 1
        stock_by_cat[cat] += 1
        stock_by_root[r] += 1
        remaining -= cost

    # Save a bounded audit record under .market, not in the submitted source.
    if trace:
        record = {"balance": balance, "existing_units": len(stock),
                  "seller_settlements_in_memory": sum(seller.values()),
                  "buyer_settlements_in_memory": sum(buyer.values()),
                  "planned_max_cost": round(budget - remaining, 2), "buy": picks}
        try:
            with (state / "restock-decisions.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"[restock] trace unavailable: {exc}", file=sys.stderr)
    print(f"[restock] paid seller={sum(seller.values())}, paid buyer={sum(buyer.values())}, "
          f"proposed={len(picks)}, estimated_upper_cost={round(budget - remaining)}", file=sys.stderr)
    return {"buy": picks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()
    try:
        result = make_plan(args.state, trace=args.trace)
    except Exception as exc:
        print(f"[restock] {type(exc).__name__}: {exc}", file=sys.stderr)
        result = {"buy": []}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))

if __name__ == "__main__":
    main()
