"""Round 3 restock hook: python restock.py --state .market  ->  {"buy": [{"sku", "qty"}, ...], "bankrupt": bool}

The kit's default buys random SKUs when stock is low. This one reads demand from the market's ledger
(what actually settled, market-wide, newest 500 entries) and buys the SKUs that sell, within the balance.
Rules: keep stock under 40 units (storage is 1 %/day of cost), spend at most 5 % of the balance per SKU,
prefer goods and long-shelf food (short shelf life rots before a buyer asks), file for bankruptcy only
when below the line with nothing left to sell. Also posts a shop notice to the forum at most every 10 min.
"""
import argparse, collections, json, time, urllib.request
from pathlib import Path

MARKET = "https://market.ourolabs.ai"
MAX_UNITS, PER_SKU_SHARE, TOP = 40, 0.05, 10


def load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def settled_counts():
    try:
        led = json.load(urllib.request.urlopen(f"{MARKET}/api/ledger?after=-500", timeout=15))
    except Exception:
        return collections.Counter()
    entries = led if isinstance(led, list) else led.get("entries", [])
    return collections.Counter(e["data"]["item"] for e in entries
                               if e.get("kind") == "settle" and isinstance(e.get("data"), dict) and e["data"].get("item"))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--state", required=True); a = ap.parse_args()
    md = Path(a.state)
    inv, cat = load(md / "inventory.json"), load(md / "catalog.json")
    units, balance = inv.get("units", []), inv.get("balance") or 0
    out = {"buy": []}
    if balance < inv.get("bankrupt_line", 0) and not units and (inv.get("bankruptcies_left") or 0) > 0:
        out["bankrupt"] = True
    elif len(units) < MAX_UNITS and cat:
        have = collections.Counter(u["sku"] for u in units)
        hot = settled_counts()
        def score(sku):   # settled demand, discounted for food that rots fast
            c = cat.get(sku, {}); shelf = c.get("shelf_hours") or 1e9
            return hot[sku] * (0.3 if c.get("kind") == "food" and shelf < 48 else 1.0)
        ranked = sorted((s for s in hot if s in cat), key=score, reverse=True)[:TOP]
        room = MAX_UNITS - len(units)
        for sku in ranked:
            if room <= 0:
                break
            unit_cost = cat[sku]["list_price"] * 0.75                      # worst-case cost share
            qty = min(5, room, int(balance * PER_SKU_SHARE / unit_cost)) - have[sku]
            if qty >= 1:
                out["buy"].append({"sku": sku, "qty": qty}); room -= qty
        print(f"[restock] balance {balance} units {len(units)} hot={len(hot)} -> buy {out['buy']}", flush=True)
    # forum: one shop notice per 10 minutes about what we stock
    stamp = md / "last_shop_post"
    if units and time.time() - (stamp.stat().st_mtime if stamp.exists() else 0) > 600:
        cats = collections.Counter(u.get("category", "").split(">")[-1] for u in units).most_common(3)
        line = {"post": "yeonsong2 재고 있음: " + ", ".join(f"{c} {n}개" for c, n in cats) + " — 등급 정직 표기, 비식품 보증 A 6h/B 3h.",
                "refs": [], "as": "shop"}
        with (md / "outbox.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        stamp.touch()
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
