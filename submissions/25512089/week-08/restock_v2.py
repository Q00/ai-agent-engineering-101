"""Evidence-based, conservative Round 3 restock hook.

Interface: python restock_v2.py --state PATH
Writes precisely one JSON object to stdout: {"buy": [...]}
Reads only local kit-synchronized state and local settlement/supply logs.
No network requests and no trades. The market kit decides whether to buy.

This is deliberately a conservative candidate, not a guarantee of greater profit.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

# Tunables: cautious while the shop has negative cash-based board profit.
MAX_STOCK_UNITS = 8
MAX_UNSOLD_COST = 160_000
MAX_SINGLE_RESTOCK_SPEND = 30_000
MIN_BALANCE = 4_600_000
MAX_SAME_SKU = 2
MIN_RECENT_GRADE_A_SALES = 2
MIN_EXPECTED_MARGIN = 0.08  # Grade-A sale price versus *highest* recent observed supply cost.
MAX_LOG_SALES = 220
MAX_LOG_BUYS = 240

SALE_RE = re.compile(r"\[acp\] settled (.+?) at (\d+), ledger #(\d+)(?:, unit \S+ grade ([A-Z]))?")
SUPPLY_RE = re.compile(r"\[supply\] (\d+) x (.+?) for (\d+), balance")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_log_lines(state: Path) -> list[str]:
    logdir = state.parent / "logs"
    paths = [logdir / "market-kit.log.1", logdir / "market-kit.log"]
    records: list[str] = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                records.extend(f.read().splitlines())
        except OSError:
            continue
    return records


def name_lookup(catalog: dict) -> dict[str, str]:
    # Only unambiguous exact product-name matches: never guess a SKU from a name.
    names = defaultdict(set)
    for sku, product in catalog.items():
        if isinstance(product, dict) and product.get("name"):
            names[str(product["name"]).strip()].add(str(sku))
    return {name: next(iter(skus)) for name, skus in names.items() if len(skus) == 1}


def historical_metrics(logs: list[str], catalog: dict) -> tuple[dict, dict]:
    lookup = name_lookup(catalog)
    sales_seen: set[str] = set()
    sales: list[tuple[str, int, str]] = []
    purchases: list[tuple[str, float]] = []
    for line in logs:
        match = SALE_RE.search(line)
        if match:
            name, price, ledger, grade = match.groups()
            sku = lookup.get(name.strip())
            if sku and ledger not in sales_seen:
                sales_seen.add(ledger)
                sales.append((sku, int(price), grade or "?"))
            continue
        match = SUPPLY_RE.search(line)
        if match:
            qty, name, amount = match.groups()
            sku = lookup.get(name.strip())
            n = int(qty)
            if sku and n > 0:
                purchases.append((sku, int(amount) / n))

    sale_prices: dict[str, list[int]] = defaultdict(list)
    sale_counts: Counter = Counter()
    for sku, price, grade in sales[-MAX_LOG_SALES:]:
        sale_counts[sku] += 1
        if grade == "A":
            sale_prices[sku].append(price)
    buy_costs: dict[str, list[float]] = defaultdict(list)
    for sku, cost in purchases[-MAX_LOG_BUYS:]:
        buy_costs[sku].append(cost)
    return {"a_prices": sale_prices, "counts": sale_counts}, buy_costs


def evaluate(state: Path, *, ignore_window: bool = False) -> tuple[list[dict], dict]:
    inv = read_json(state / "inventory.json", {})
    catalog = read_json(state / "catalog.json", {})
    demand = read_json(state / "demand.json", [])
    if not isinstance(inv, dict) or not isinstance(catalog, dict):
        return [], {"reason": "inventory or catalog missing/invalid"}
    if int(inv.get("round") or 0) < 3:
        return [], {"reason": "not Round 3"}
    restock = inv.get("restock") or {}
    if not ignore_window and (not isinstance(restock, dict) or restock.get("open") is not True):
        return [], {"reason": "supply window closed"}

    units = [u for u in inv.get("units", []) if isinstance(u, dict) and u.get("grade") != "expired"]
    cash = float(inv.get("balance") or 0)
    unsold_cost = sum(max(0, float(u.get("cost") or 0)) for u in units)
    info = {"cash": int(cash), "stock_units": len(units), "unsold_cost": int(unsold_cost)}
    if cash < MIN_BALANCE:
        return [], dict(info, reason="below minimum cash guard")
    if len(units) >= MAX_STOCK_UNITS:
        return [], dict(info, reason="stock unit cap reached")
    if unsold_cost >= MAX_UNSOLD_COST:
        return [], dict(info, reason="unsold inventory cost cap reached")

    stocked = Counter(str(u.get("sku")) for u in units)
    stats, buying = historical_metrics(read_log_lines(state), catalog)
    # demand.json is a *category listing*, not a ranking by order frequency.
    known_categories = {str(row.get("category")) for row in demand if isinstance(row, dict)} if isinstance(demand, list) else set()
    candidates = []
    for sku, product in catalog.items():
        if not isinstance(product, dict) or product.get("kind") != "goods":
            continue
        if stocked[str(sku)] >= MAX_SAME_SKU:
            continue
        cat = str(product.get("category") or "")
        if known_categories and cat not in known_categories:
            continue
        grades_a = stats["a_prices"].get(str(sku), [])
        observed_buy_cost = buying.get(str(sku), [])
        if len(grades_a) < MIN_RECENT_GRADE_A_SALES or not observed_buy_cost:
            continue
        # A conservative comparison. Grade-C exposure/fees still make this an estimate.
        sale_avg = mean(grades_a[-15:])
        upper_buy_cost = max(observed_buy_cost[-15:])
        if upper_buy_cost <= 0:
            continue
        margin = sale_avg / upper_buy_cost - 1
        if margin < MIN_EXPECTED_MARGIN:
            continue
        if upper_buy_cost * 1.15 > MAX_SINGLE_RESTOCK_SPEND:
            continue
        if unsold_cost + upper_buy_cost * 1.15 > MAX_UNSOLD_COST:
            continue
        # Prefer repeatable actual sales, then observed margin, then low stock.
        score = min(stats["counts"].get(str(sku), 0), 30) * 3 + min(margin, 0.5) * 40 - stocked[str(sku)] * 9
        candidates.append((score, str(sku), sale_avg, upper_buy_cost, margin))

    candidates.sort(key=lambda x: (-x[0], x[1]))
    if not candidates:
        return [], dict(info, reason="no profitable, sufficiently evidenced SKU under risk caps")
    best = candidates[0]
    return [{"sku": best[1], "qty": 1}], dict(info, reason="candidate selected", sku=best[1], grade_a_avg_sale=round(best[2]), highest_recent_supply_cost=round(best[3]), historical_margin_est_pct=round(best[4] * 100, 1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--diagnose", action="store_true", help="Print human-readable reasoning to stderr")
    parser.add_argument("--preview", action="store_true", help="Ignore current supply-window state for a non-buying local preview")
    args = parser.parse_args()
    buy, info = evaluate(args.state, ignore_window=args.preview)
    if args.diagnose:
        print(json.dumps(info, ensure_ascii=False), file=sys.stderr)
    # A preview is only for the operator; it NEVER emits purchase instructions.
    if args.preview:
        print(json.dumps({"buy": [], "preview_would_buy": buy}, ensure_ascii=False))
    else:
        print(json.dumps({"buy": buy}, ensure_ascii=False))


if __name__ == "__main__":
    main()
