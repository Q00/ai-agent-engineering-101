"""Who buys from my shop, and what those buyers buy anywhere in the market (Round 3, read from the public ledger).

The market says each buyer has a few favourite categories most of its orders come from, and publishes neither.
The ledger shows every settled deal with its buyer, shop and SKU, so:
  customers[buyer]        deals that buyer settled with my shop
  buys[buyer][category]   deals that buyer settled anywhere (any shop), by category
  sku_buys[category][sku] how often each SKU of a category settled (any buyer), to pick the SKU to stock
restock.py stocks the favourite categories of my customers first.

State lives in the restock memory dict under "ledger" (cursor) and "customers" (the counts above).
"""
import json
import os
import time

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
ME = "ddolcom"                  # the market name (join --name), also the card and receipt name
PAGES = 20                      # at most 20 x 500 ledger entries per run
FIRST_LOOKBACK = 5000           # first run: start this many entries before the newest
SKU_A_ALPHA = 0.1               # grade A paid/list per SKU: an average over about the last 10 deals
PRICE_ALPHA = 0.02             # paid/list per shop: an average over about the last 50 deals
RECENT_HOURS = 3                # sales the restock looks at to choose focus SKUs, in buckets of RECENT_BUCKET seconds
RECENT_BUCKET = 600
RECENT_SEED = "recent_seed.json"    # the last hours of settled deals when "recent" starts, written once from the ledger
BREAK_SEED = "logs/break-rate-1110-1928.json"  # break_rate.py output, read once when "brk" starts
SHOPS_SEED = "recent_shops_seed.json"   # the same for "recent_shops": bucket -> sku -> names of the shops that sold it


def get(c, path, **params):
    r = c.get(f"{MARKET}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def deal_of(e):
    """(order, buyer, shop url, sku) for a settled deal, else None. Round 3 `settle` entries carry the SKU as
    `item`; a `result` entry carries it in its receipt. Round 2 items (lamp, chair) are not SKUs and are dropped."""
    d = e.get("data") or {}
    if e.get("kind") not in ("settle", "result"):
        return None
    sku = d.get("item") or d.get("sku") or (d.get("receipt") or {}).get("item")
    buyer, shop = d.get("buyer"), d.get("shop")
    if not (sku and buyer and shop):
        return None
    if e["kind"] == "result" and not d.get("receipt"):
        return None
    return d.get("order") or f"seq{e.get('seq')}", buyer, shop, sku


def fold_price(cu, e, catalog):
    """Add one settled deal to cu["prices"][shop name][kind] = [average paid/list, deals]."""
    d = e.get("data") or {}
    item = catalog.get(d.get("sku") or d.get("item")) or {}
    name = cu.setdefault("url_names", {}).get(d.get("shop"))
    if e.get("kind") != "settle" or not name or not item.get("list_price") or not isinstance(d.get("amount"), (int, float)):
        return
    row = cu.setdefault("prices", {}).setdefault(name, {}).setdefault(item.get("kind") or "goods", [None, 0])
    ratio = d["amount"] / item["list_price"]
    row[1] += 1
    row[0] = ratio if row[0] is None else row[0] + (ratio - row[0]) * max(1 / row[1], PRICE_ALPHA)


def fold_a_price(cu, e, catalog):
    """ddolcom (2026-11-09 20:22): cu["sku_a"][sku] = [average paid/list of grade A settles, deals], any shop.
    restock.py buys a high-tier SKU only when grade A sells high enough over its random cost (0.55-0.75 of list)."""
    d = e.get("data") or {}
    item = catalog.get(d.get("sku") or "") or {}
    if e.get("kind") != "settle" or d.get("grade") != "A" or not item.get("list_price") \
            or not isinstance(d.get("amount"), (int, float)):
        return
    row = cu.setdefault("sku_a", {}).setdefault(d["sku"], [None, 0])
    ratio = d["amount"] / item["list_price"]
    row[1] += 1
    row[0] = ratio if row[0] is None else row[0] + (ratio - row[0]) * max(1 / row[1], SKU_A_ALPHA)


def fold_break(cu, e):
    """Claude (2026-11-10 19:22, the user's plan step 3): cu["brk"][sku] = [settles, broken] in the whole market, seeded from
    BREAK_SEED (12 h of the ledger: 34,810 settles, 1,031 broken). restock.py skips a SKU that breaks far more than the rest."""
    d = e.get("data") or {}
    if "brk" not in cu:
        try:
            with open(BREAK_SEED, encoding="utf-8") as f:
                seed = json.load(f)
            cu["brk"] = {s: [n, seed["broken"].get(s, 0)] for s, n in seed["settles"].items()}
        except (OSError, ValueError, KeyError):
            cu["brk"] = {}
    sku = d.get("sku") if e.get("kind") == "settle" else d.get("item") if e.get("kind") == "broken" else None
    if sku:
        cu["brk"].setdefault(sku, [0, 0])[0 if e["kind"] == "settle" else 1] += 1


def update(mem, catalog, client=None):
    """Read the ledger since the last run and fold the settled deals into mem["customers"]. Never raises."""
    cu = mem.setdefault("customers", {"my_urls": [], "customers": {}, "buys": {}, "sku_buys": {}, "seen": []})
    c = client or httpx.Client()
    try:
        shops = get(c, "/api/shops").get("shops", [])
        for s in shops:
            if s.get("name") == ME and s.get("url") not in cu["my_urls"]:
                cu["my_urls"].append(s["url"])
            if s.get("name") and s.get("url"):
                cu.setdefault("url_names", {})[s["url"]] = s["name"]
        if "recent" not in cu:
            try:
                with open(RECENT_SEED, encoding="utf-8") as f:
                    cu["recent"] = json.load(f)
            except (OSError, ValueError):
                cu["recent"] = {}
        if "recent_shops" not in cu:
            try:
                with open(SHOPS_SEED, encoding="utf-8") as f:
                    cu["recent_shops"] = json.load(f)
            except (OSError, ValueError):
                cu["recent_shops"] = {}
        after = mem.get("ledger", -FIRST_LOOKBACK)
        seen = set(cu["seen"])
        for _ in range(PAGES):
            rows = get(c, "/api/ledger", after=after)
            if not rows:
                break
            after = rows[-1]["seq"]
            for e in rows:
                d = e.get("data") or {}
                if e.get("kind") == "join" and d.get("name") == ME and d.get("role") == "shop" \
                        and d.get("url") not in cu["my_urls"]:
                    cu["my_urls"].append(d["url"])
                if e.get("kind") == "join" and d.get("role") == "shop" and d.get("name") and d.get("url"):
                    cu.setdefault("url_names", {})[d["url"]] = d["name"]
                fold_price(cu, e, catalog)
                fold_a_price(cu, e, catalog)
                fold_break(cu, e)
                if e.get("kind") == "settle" and d.get("sku"):
                    b = cu["recent"].setdefault(str(int(e["ts"] // RECENT_BUCKET)), {})
                    b[d["sku"]] = b.get(d["sku"], 0) + 1
                    who = cu["recent_shops"].setdefault(str(int(e["ts"] // RECENT_BUCKET)), {}).setdefault(d["sku"], [])
                    name = cu.get("url_names", {}).get(d.get("shop"), d.get("shop"))
                    if name not in who:
                        who.append(name)
                deal = deal_of(e)
                if not deal or deal[0] in seen:
                    continue
                order, buyer, shop, sku = deal
                seen.add(order)
                if shop in cu["my_urls"]:                 # Round 2 lamps count too: that buyer already trusts us
                    cu["customers"][buyer] = cu["customers"].get(buyer, 0) + 1
                if sku not in catalog:
                    continue
                cat = catalog[sku]["category"]
                b = cu["buys"].setdefault(buyer, {})
                b[cat] = b.get(cat, 0) + 1
                s = cu["sku_buys"].setdefault(cat, {})
                s[sku] = s.get(sku, 0) + 1
            if len(rows) < 500:
                break
        mem["ledger"] = after
        keep = (time.time() - RECENT_HOURS * 3600) // RECENT_BUCKET
        cu["recent"] = {k: v for k, v in cu["recent"].items() if int(k) >= keep}
        cu["recent_shops"] = {k: v for k, v in cu["recent_shops"].items() if int(k) >= keep}
        cu["seen"] = list(seen)[-5000:]                 # order ids, against a settle and a result for one deal
    except (httpx.HTTPError, ValueError, KeyError) as e:
        print(f"customers: ledger not read ({type(e).__name__})")
    finally:
        if client is None:
            c.close()
    return cu


def favourites(cu):
    """category -> weight: each customer's buys by category, weighted by how often that customer bought from me."""
    fav = {}
    for buyer, mine in cu.get("customers", {}).items():
        for cat, n in cu.get("buys", {}).get(buyer, {}).items():
            fav[cat] = fav.get(cat, 0) + n * mine
    return fav


def popular(cu):
    """category -> settled deals in the whole market since we started reading the ledger (any buyer, any shop)."""
    return {cat: sum(skus.values()) for cat, skus in cu.get("sku_buys", {}).items()}


def sales_value(cu, catalog):
    """category -> list-price value of the market's settled deals (count x list price of each SKU bought)."""
    return {cat: sum(n * catalog[sku]["list_price"] for sku, n in skus.items() if sku in catalog)
            for cat, skus in cu.get("sku_buys", {}).items()}


def recent_buys(cu, catalog):
    """category -> sku -> settled deals in the whole market over the last RECENT_HOURS."""
    out = {}
    for bucket in cu.get("recent", {}).values():
        for sku, n in bucket.items():
            if sku in catalog:
                s = out.setdefault(catalog[sku]["category"], {})
                s[sku] = s.get(sku, 0) + n
    return out


def recent_shop_counts(cu):
    """sku -> how many other shops settled it over the last RECENT_HOURS. A tunnel restart can count one shop twice if its
    new URL is not yet in url_names; my own URLs are left out by name and by my_urls."""
    shops = {}
    for bucket in cu.get("recent_shops", {}).values():
        for sku, names in bucket.items():
            shops.setdefault(sku, set()).update(n for n in names if n != ME and n not in cu.get("my_urls", []))
    return {sku: len(names) for sku, names in shops.items()}


def best_sku(cu, cat):
    """The SKU of a category that settled most often in the market, or None."""
    s = cu.get("sku_buys", {}).get(cat)
    return max(s, key=s.get) if s else None


if __name__ == "__main__":                              # python customers.py: what the ledger says right now
    from pathlib import Path
    md = Path(os.environ.get("MARKET_DIR", ".market"))
    cat = json.loads((md / "catalog.json").read_text(encoding="utf-8")) if (md / "catalog.json").exists() else {}
    m = {}
    cu = update(m, cat)
    print(json.dumps({"my_urls": cu["my_urls"], "customers": cu["customers"],
                      "favourites": sorted(favourites(cu).items(), key=lambda x: -x[1])[:10]}, ensure_ascii=False, indent=1))
