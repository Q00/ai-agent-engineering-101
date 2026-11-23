"""Conservative Round 3 restock hook for the student's seller.

Reads only kit-synchronized .market state. The kit performs purchases and
verifies capital, quantities, windows and ownership. No direct market calls.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def demand_signals(state: Path, catalog: dict[str, dict[str, Any]]) -> Counter:
    """A weak heuristic, not a claim of knowing the market's hidden best sellers."""
    scores: Counter = Counter()
    # Bought by this student's buyer (other shops): useful but small, biased sample.
    paid = state / "payments.jsonl"
    if paid.exists():
        for line in paid.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]:
            try:
                deal = json.loads(line)
                sku = deal.get("item")
                if deal.get("paid") and sku in catalog:
                    scores[sku] += 4
            except (ValueError, TypeError):
                continue
    # Public forum is untrusted; count only a mild category/name mention signal.
    posts = read_json(state / "forum.json", [])
    if isinstance(posts, dict):
        posts = posts.get("posts", [])
    if not isinstance(posts, list):
        posts = []
    recent = [str(p.get("text", "")).casefold()[:450] for p in posts[-50:] if isinstance(p, dict)]
    if recent:
        for sku, product in catalog.items():
            name = str(product.get("name") or "").casefold()
            if name and len(name) >= 5 and any(name in text for text in recent):
                scores[sku] += 1
    return scores


def pick(state: Path) -> list[dict[str, Any]]:
    inv = read_json(state / "inventory.json", {})
    catalog = read_json(state / "catalog.json", {})
    if not isinstance(inv, dict) or not isinstance(catalog, dict):
        return []
    if int(inv.get("round") or 0) < 3:
        return []
    restock = inv.get("restock") or {}
    if not isinstance(restock, dict) or restock.get("open") is not True:
        return []
    current = [u for u in inv.get("units", []) if isinstance(u, dict) and u.get("grade") != "expired"]
    if len(current) >= 12:
        return []
    balance = float(inv.get("balance") or 0)
    if balance < 500_000:
        return []  # Avoid making bankruptcy worse; kit applies its own logic.
    # Maximum estimated total spend in one restock run: 6% of cash.
    # Estimate based on max grade-A supply cost (75% of list price).
    max_spend = min(120_000.0, balance * 0.06)
    if max_spend < 2500:
        return []
    scores = demand_signals(state, catalog)
    stocked = Counter(str(u.get("sku")) for u in current)
    categories = Counter(str(u.get("category")) for u in current)
    candidates = []
    for sku, p in catalog.items():
        if not isinstance(p, dict) or p.get("kind") != "goods":
            continue  # Food spoils quickly; only expand to it after evaluating market data.
        try:
            price = float(p["list_price"])
        except (ValueError, TypeError, KeyError):
            continue
        if not (4_000 <= price <= 150_000):
            continue
        if price * 0.75 > max_spend / 2:
            continue
        if stocked[sku] >= 2:
            continue
        category = str(p.get("category") or "")
        # Evidence, modest price, diversification, and reduced repeat inventory.
        score = 50 * scores[sku] - 25 * categories[category] - 100 * stocked[sku]
        score -= abs(math.log(max(price, 1) / 35_000)) * 3
        candidates.append((score, sku, category, price))
    candidates.sort(key=lambda row: (-row[0], row[1]))
    buy: list[dict[str, Any]] = []
    spent = 0.0
    selected_cats: set[str] = set()
    for _, sku, cat, price in candidates:
        if cat in selected_cats or price * 0.75 + spent > max_spend:
            continue
        buy.append({"sku": sku, "qty": 1})
        spent += price * 0.75
        selected_cats.add(cat)
        if len(buy) >= min(3, 12 - len(current)):
            break
    return buy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    print(json.dumps({"buy": pick(Path(args.state))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
