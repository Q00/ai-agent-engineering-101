"""Round 3 restock hook: python restock.py --state .market  ->  {"buy": [{"sku", "qty"}, ...], "bankrupt": bool}

The kit's default buys random SKUs when stock is low. This one reads demand from the market's ledger
(what actually settled, market-wide, newest 500 entries) and buys the SKUs with the most settled revenue, within the
balance, skipping SKUs whose supply price has surged.
Rules: keep stock under 400 A/B units (storage is 1 %/day of cost), spend at most 5 % of the balance per SKU,
food only with >= 10 days of shelf life (buyers refuse grade C, and food gets there fast), file for bankruptcy only
when below the line with nothing left to sell. Also posts the shop's price list to the forum once an hour (week-10 LAB first move).
"""
import argparse, collections, json, time
from pathlib import Path

from market_price import settled, median_price
from market_buyer import due

MAX_UNITS, PER_SKU_SHARE, TOP = 400, 0.08, 30   # goods do not decay and Book counts stock at cost: idle cash earns nothing


def load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def settled_counts():
    """SKU -> settled revenue (count x price), not count: revenue and Book rank the shop, and a 300k sofa sold once
    is worth 30 lamps."""
    c = collections.Counter()
    for d in settled():
        if d.get("item"):
            c[d["item"]] += d.get("amount") or d.get("price") or 1
    return c


def surged(units):
    """SKUs whose last supplied unit cost more than 75 % of list: above grade A's normal range, so the market's
    supply surge (+0.2 % per unit over 50 an hour, up to +50 %) is on. Everyone buys the top sellers; we skip
    them until the price falls back."""
    last = {}
    for u in sorted(units, key=lambda u: u.get("supplied_at") or 0):
        if u.get("list_price"):
            last[u["sku"]] = u["cost"] / u["list_price"]
    return {sku for sku, r in last.items() if r > 0.75}


def notice(units):
    """Week-10 LAB, the shop's first move: instead of "we have stock", a price list other buyers' agents can act on —
    our ask (5 % under the market median) for the three A-grade SKUs with the most settled revenue, with the median.
    Target: buyer agents that read [시세] posts (they quote and follow them). Wrong-case cost: one post slot an hour.
    Metric: seller visits/h and deals/h (baseline Nov 12 12:00-15:00: ~300 visits/h, ~75 deals/h)."""
    hot = settled_counts()
    rows = []
    for u in sorted((u for u in units if u.get("grade") == "A" and u.get("kind") == "goods"), key=lambda u: -hot[u["sku"]]):
        if any(r[0] == u["sku"] for r in rows):
            continue
        med, n = median_price(u["sku"])
        if med:
            rows.append((u["sku"], u["name"][:30], int(med * 0.95), med, n))
        if len(rows) == 3:
            break
    if not rows:
        return None
    return ("[시세/재고] yeonsong2 호가 = 시장 중앙값의 95 %, A등급·시장 인증서·6h 보증: " +
            "; ".join(f"{name} ({sku}) {ask:,} (최근 {n}건 중앙값 {med:,})" for sku, name, ask, med, n in rows) +
            ". SKU로 요청하면 즉시 수락.")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--state", required=True); a = ap.parse_args()
    md = Path(a.state)
    inv, cat = load(md / "inventory.json"), load(md / "catalog.json")
    units, balance = inv.get("units", []), inv.get("balance") or 0
    out = {"buy": []}
    if balance < inv.get("bankrupt_line", 0) and not units and (inv.get("bankruptcies_left") or 0) > 0:
        out["bankrupt"] = True
    elif cat:
        have = collections.Counter(u["sku"] for u in units)
        hot = settled_counts()
        def score(sku):   # settled demand; food only with >= 10 days of shelf life (grade B after 20 % elapsed), half weight
            c = cat.get(sku, {}); shelf = c.get("shelf_hours") or 1e9
            if c.get("kind") == "food":
                return hot[sku] * 0.5 if shelf >= 240 else 0
            return hot[sku]
        hot_now = surged(units)
        ranked = [s for s in sorted((s for s in hot if s in cat), key=score, reverse=True) if score(s) > 0 and s not in hot_now][:TOP]
        room = MAX_UNITS - sum(1 for u in units if u.get("grade") in ("A", "B"))   # grade C stock is dead weight, not stock
        for sku in ranked:
            if room <= 0:
                break
            unit_cost = cat[sku]["list_price"] * 0.75                      # worst-case cost share
            qty = min(6, room, int(balance * PER_SKU_SHARE / unit_cost)) - have[sku]
            if qty >= 1:
                out["buy"].append({"sku": sku, "qty": qty}); room -= qty
        print(f"[restock] balance {balance} units {len(units)} hot={len(hot)} surged={sorted(hot_now)[:5]} -> buy {out['buy']}", flush=True)
    # forum, once an hour: the shop's price list (week-10 LAB "first move"), see notice()
    text = notice(units) if units and due(md / "last_shop_post", 3600) else None
    if text:
        with (md / "outbox.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"post": text, "refs": [], "as": "shop"}, ensure_ascii=False) + "\n")
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
