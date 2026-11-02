"""ggari-shop, Round 3 restock hook (week 08). The kit runs it every 60 s:

    python restock.py --state .market        -> prints {"buy": [{"sku": ..., "qty": ...}, ...]}

Phase R3-0 (opening): coverage first. Buyers only see shops that stock the ordered category, so the first job is to
be in many categories with a few units each, cheap goods first (no expiry; storage is 1 %/day). Then reorder what
sold (a unit gone from inventory without expiring = sold). Position limits, as risk control:
- never spend below CASH_FLOOR of the starting capital (no bankruptcy: its money counts against profit);
- at most PER_SKU_CAP of the starting capital in one SKU, and OPENING_BUDGET in total during the opening;
- no new stock in the last LAST_BUY_HOURS before the round ends (what is left at the end is a loss).
Fix 3 (11/02): what sells. demand.json has no weights, so the market's own ledger is the demand signal: every
`settle` entry names the SKU that sold. Each run reads the new ledger entries, adds them to a decaying count per
SKU and per category (half-life HOT_HALF_LIFE_H), then
- buys the market's hot SKUs we do not hold (momentum), and
- fills uncovered categories hottest first (cheap goods first only among categories with no sales seen yet).
"""
from __future__ import annotations

import argparse
import json
import random
import time
import urllib.request
from pathlib import Path

CASH_FLOOR = 0.30          # keep 30 % of the starting capital in cash
PER_SKU_CAP = 0.05         # at most 5 % of the starting capital in one SKU (at its estimated cost)
OPENING_BUDGET = 0.20      # coverage phase: at most 20 % of the starting capital in stock
TARGET_CATEGORIES = 60     # coverage phase ends when this many categories are in stock
EST_COST = 0.65            # estimated cost / list price before we know (grade A 55-75 %)
LINES_PER_RUN = 10         # the kit buys at most 10 lines per run
ROUND_END = 1794927599     # 2026-11-17 23:59:59 KST
LAST_BUY_HOURS = 36
HOT_HALF_LIFE_H = 6.0      # fix 3: a sale seen 6 h ago counts half
HOT_LINES = 3              # fix 3: market hot SKUs bought per run
MARKET = "https://market.ourolabs.ai"


def fetch_settles(after: int | None, timeout: float = 5.0) -> tuple[list[str], int | None]:
    """SKUs of the `settle` entries after ledger seq `after` (the newest 500 on the first run), and the last seq."""
    q = f"after={after}" if after is not None else "after=-500"
    try:
        with urllib.request.urlopen(f"{MARKET}/api/ledger?{q}", timeout=timeout) as r:
            rows = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return [], after
    skus, last = [], after
    for e in rows if isinstance(rows, list) else []:
        last = max(last or 0, int(e.get("seq") or 0))
        if e.get("kind") == "settle":
            d = e.get("data") or {}
            sku = d.get("item") or d.get("sku") or e.get("item")
            if isinstance(sku, str):
                skus.append(sku)
    return skus, last


def update_hot(memory: dict, settles: list[str], catalog: dict, now: float) -> dict:
    """Decaying sale counts: {"sku": {sku: n}, "cat": {category: n}, "ts": now}."""
    hot = memory.get("hot") or {"sku": {}, "cat": {}, "ts": now}
    k = 0.5 ** (max(0.0, now - float(hot.get("ts") or now)) / 3600 / HOT_HALF_LIFE_H)
    out = {"sku": {s: n * k for s, n in hot.get("sku", {}).items() if n * k >= 0.05},
           "cat": {c: n * k for c, n in hot.get("cat", {}).items() if n * k >= 0.05}, "ts": now}
    for sku in settles:
        out["sku"][sku] = out["sku"].get(sku, 0) + 1
        cat = (catalog.get(sku) or {}).get("category")
        if cat:
            out["cat"][cat] = out["cat"].get(cat, 0) + 1
    return out


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def plan(inv: dict, demand: list, catalog: dict, memory: dict, now: float | None = None,
         rng: random.Random | None = None, settles: list[str] | None = None) -> tuple[list[dict], dict]:
    """Returns (buy lines, updated memory). memory: {"seen": {unit: sku}, "sold": {sku: n}, "hot": ...,
    "ledger_seq": n}; settles: SKUs sold on the market since the last run (fix 3)."""
    now = time.time() if now is None else now
    rng = rng or random.Random(int(now // 3600))
    units = inv.get("units", [])
    start = float(inv.get("start") or 5_000_000)
    balance = float(inv.get("balance") or 0)
    # what sold since the last run: units that left the inventory before expiring
    present = {u["unit"]: u["sku"] for u in units}
    sold = dict(memory.get("sold", {}))
    for unit, sku in memory.get("seen", {}).items():
        if unit not in present:
            gone = next((u for u in memory.get("expiring", []) if u == unit), None)
            if gone is None:
                sold[sku] = sold.get(sku, 0) + 1
    expiring = [u["unit"] for u in units if u.get("expires_at") and u["expires_at"] - now < 600]
    hot = update_hot(memory, settles or [], catalog, now)
    memory = {"seen": present, "sold": sold, "expiring": expiring, "hot": hot,
              "ledger_seq": memory.get("ledger_seq")}
    if inv.get("round", 0) < 3 or now > ROUND_END - LAST_BUY_HOURS * 3600:
        return [], memory
    spendable = balance - CASH_FLOOR * start
    if spendable <= 0:
        return [], memory
    held_value = sum(float(u.get("cost") or 0) for u in units)
    by_sku: dict[str, float] = {}
    for u in units:
        by_sku[u["sku"]] = by_sku.get(u["sku"], 0) + float(u.get("cost") or 0)
    in_stock_cats = {u.get("category") for u in units}
    buy: list[dict] = []

    def add(sku: str, qty: int) -> None:
        nonlocal spendable
        price = float(catalog.get(sku, {}).get("list_price") or 0)
        if price <= 0 or qty < 1:
            return
        est = price * EST_COST
        room = PER_SKU_CAP * start - by_sku.get(sku, 0)
        qty = min(qty, int(room // est), int(spendable // est), 20)
        if qty >= 1:
            buy.append({"sku": sku, "qty": qty})
            spendable -= qty * est
            by_sku[sku] = by_sku.get(sku, 0) + qty * est

    # 1. reorder what sold (momentum, simplest form): one more unit per sale, sold-out first
    for sku, n in sorted(sold.items(), key=lambda kv: -kv[1]):
        if len(buy) >= LINES_PER_RUN // 2:
            break
        if not any(u["sku"] == sku for u in units):
            add(sku, min(3, n))
    # 1b. momentum (fix 3): the market's hot SKUs we do not hold, hottest first
    held = {u["sku"] for u in units}
    n0 = len(buy)
    for sku, n in sorted(hot["sku"].items(), key=lambda kv: (-kv[1], kv[0])):
        if len(buy) - n0 >= HOT_LINES or len(buy) >= LINES_PER_RUN:
            break
        if n >= 1 and sku not in held and sku not in {b["sku"] for b in buy}:
            add(sku, 1 if (catalog.get(sku) or {}).get("kind") == "food" else 2)
    # 2. coverage: categories not in stock, hottest first (fix 3), then cheap goods first, while the opening budget lasts
    if len(in_stock_cats) < TARGET_CATEGORIES and held_value < OPENING_BUDGET * start:
        cats = sorted((d for d in demand if d.get("category") not in in_stock_cats),
                      key=lambda d: (-hot["cat"].get(d.get("category"), 0), d.get("kind") == "food",
                                     float(d.get("median_price") or 0)))
        for d in cats:
            if len(buy) >= LINES_PER_RUN:
                break
            skus = [s for s, x in catalog.items() if x.get("category") == d.get("category")]
            if not skus:
                continue
            hot_skus = [s for s in skus if hot["sku"].get(s, 0) >= 1]
            if hot_skus:                                   # a SKU of this category that sold on the market
                pick = max(hot_skus, key=lambda s: (hot["sku"][s], s))
            else:
                skus.sort(key=lambda s: float(catalog[s].get("list_price") or 0))
                pick = rng.choice(skus[: max(1, len(skus) // 2)])
            if pick in {b["sku"] for b in buy}:
                continue
            add(pick, 2 if d.get("kind") != "food" else 1)
    return buy[:LINES_PER_RUN], memory


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    md = Path(ap.parse_args().state)
    mem_path = md / "memory" / "restock.json"
    inv = load(md / "inventory.json", {})
    mem = load(mem_path, {})
    settles, seq = fetch_settles(mem.get("ledger_seq")) if inv.get("round", 0) >= 3 else ([], mem.get("ledger_seq"))
    buy, memory = plan(inv, load(md / "demand.json", []), load(md / "catalog.json", {}), mem, settles=settles)
    memory["ledger_seq"] = seq
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    mem_path.write_text(json.dumps(memory, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"buy": buy}, ensure_ascii=False))


if __name__ == "__main__":
    main()
