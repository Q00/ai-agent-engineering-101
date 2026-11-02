"""Round 3 basic restock hook: the kit runs it every 60 s and buys what it prints.

    python restock.py --state .market
    {"buy": [{"sku": "...", "qty": 2}, ...]}            (or "bankrupt": true)

Rules only; the enhanced version replaces them:
  - stop while the stock is worth STOCK_CAP at cost or holds MAX_UNITS units
  - otherwise take categories we hold nothing of, largest first, and in each the
    SKU whose list price is nearest the category's median (the likeliest to be
    in an order's acceptable list); skip food that keeps under MIN_SHELF hours
    and anything listed above MAX_PRICE
  - plan as if every unit came in at the dearest cost (grade A, 75% of list),
    so a run never spends past the cap or the balance
  - file for bankruptcy only below the line with nothing left to sell: the kit's
    own rule, which it stops applying once this hook exists
"""
import argparse
from pathlib import Path

import common

STOCK_CAP = 1_000_000    # credits of stock at cost (a fifth of the starting capital)
MAX_UNITS = 40
LINES = 5                # SKUs per run (the kit takes at most 10)
QTY = 2                  # units per SKU (the market takes 1-20)
MIN_SHELF = 168          # hours: food that keeps less than a week is left alone
MAX_PRICE = 60_000
DEAREST = 0.75           # grade A costs at most 75% of the list price


def plan(inv: dict, catalog: dict, demand: list) -> dict:
    units = inv.get("units") or []
    if inv.get("round", 0) < 3:
        return {"buy": []}
    if inv.get("balance", 0) < inv.get("bankrupt_line", 0) and not units and inv.get("bankruptcies_left", 0) > 0:
        return {"buy": [], "bankrupt": True}
    value = sum(u.get("cost", 0) for u in units)
    if value >= STOCK_CAP or len(units) >= MAX_UNITS:
        return {"buy": []}
    room = min(STOCK_CAP - value, inv.get("balance", 0))
    held = {u.get("category") for u in units}
    buy = []
    for d in sorted(demand, key=lambda d: -d.get("skus", 0)):
        if d["category"] in held or (d.get("kind") == "food" and (d.get("shelf_hours") or 0) < MIN_SHELF):
            continue
        skus = [s for s in catalog.values() if s["category"] == d["category"] and s["list_price"] <= MAX_PRICE]
        if not skus:
            continue
        s = min(skus, key=lambda s: abs(s["list_price"] - d["median_price"]))
        dearest = s["list_price"] * DEAREST * QTY
        if dearest > room:
            continue
        room -= dearest
        buy.append({"sku": s["sku"], "qty": QTY})
        if len(buy) >= LINES:
            break
    return {"buy": buy}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(common.MARKET_DIR))
    state = Path(ap.parse_args().state)
    inv = common.read_json(state / "inventory.json", {}) or {}
    out = plan(inv, common.catalog(state), common.read_json(state / "demand.json", []) or [])
    if out["buy"] or out.get("bankrupt"):
        common.record("restock", balance=inv.get("balance"), units=len(inv.get("units") or []), **out)
    common.last_line(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
