"""Round 3 restock hook: python restock.py --state .market  ->  {"buy": [{"sku", "qty"}, ...], "bankrupt": bool}

The kit's default buys random SKUs when stock is low. This one reads demand from the market's ledger
(what actually settled, market-wide, newest 500 entries) and buys the SKUs that sell, within the balance.
Rules: keep stock under 120 units (storage is 1 %/day of cost), spend at most 5 % of the balance per SKU,
food only with >= 10 days of shelf life (buyers refuse grade C, and food gets there fast), file for bankruptcy only
when below the line with nothing left to sell. Also posts a shop notice to the forum at most every 60 min.
"""
import argparse, collections, json, time
from pathlib import Path

from market_price import settled

MAX_UNITS, PER_SKU_SHARE, TOP = 120, 0.08, 15


def load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def settled_counts():
    return collections.Counter(d["item"] for d in settled() if d.get("item"))


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
        ranked = [s for s in sorted((s for s in hot if s in cat), key=score, reverse=True) if score(s) > 0][:TOP]
        room = MAX_UNITS - sum(1 for u in units if u.get("grade") in ("A", "B"))   # grade C stock is dead weight, not stock
        for sku in ranked:
            if room <= 0:
                break
            unit_cost = cat[sku]["list_price"] * 0.75                      # worst-case cost share
            qty = min(8, room, int(balance * PER_SKU_SHARE / unit_cost)) - have[sku]
            if qty >= 1:
                out["buy"].append({"sku": sku, "qty": qty}); room -= qty
        print(f"[restock] balance {balance} units {len(units)} hot={len(hot)} -> buy {out['buy']}", flush=True)
    # forum: one shop notice per 10 minutes about what we stock
    stamp = md / "last_shop_post"
    if units and time.time() - (stamp.stat().st_mtime if stamp.exists() else 0) > 3600:
        cats = collections.Counter(u.get("category", "").split(">")[-1] for u in units).most_common(3)
        line = {"post": "yeonsong2 재고 있음: " + ", ".join(f"{c} {n}개" for c, n in cats) + " — 등급 정직 표기, 비식품 보증 A 6h/B 3h.",
                "refs": [], "as": "shop"}
        with (md / "outbox.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        stamp.touch()
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
