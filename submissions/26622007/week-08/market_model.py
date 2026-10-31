"""Round 3: what sells and who wants it, estimated from the public ledger. The market publishes neither.

    sync(md)                     appends new Round 3 ledger entries to .market/ledger_rows.jsonl
    estimate(rows, catalog, me)  turns the rows into demand per SKU and category, and each buyer's favourites

The ledger is the source of truth and the file is only a cache: .market/ is empty after a Render deploy, and sync
finds the start of Round 3 by binary search and reads it again. Everything here is arithmetic, with no harness.

Three things keep the estimate honest:
- Newer deals weigh more (half_life_hours), because favourites and competitors change during the round.
- A SKU with few sales leans on its category's average (shrink), so one lucky sale does not fill the shelf.
- Demand I cannot win is left out: my own buyer (same owner cannot settle), and a buyer who buys nearly everything
  from one other shop (week 7: 86-91% of two colluding pairs' purchases went to each other).
"""
import collections
import json
import math
import os
import statistics
import time

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
ECON_ROUND = 3
ROWS = "ledger_rows.jsonl"
CURSOR = "ledger_cursor.json"
PAGE = 500
DEFAULTS = {"half_life_hours": 24, "shrink": 1.0, "price_ratio": 0.8, "min_prices": 3, "min_settles": 200,
            "captive_min_deals": 20, "captive_share": 0.6, "max_pages": 40}


def ledger_get(after):
    r = httpx.get(f"{MARKET}/api/ledger", params={"after": after}, timeout=20)
    r.raise_for_status()
    return r.json()


def first_seq_of_round(get, rnd=ECON_ROUND):
    """The cursor just before the round's first entry, or None if the round has not begun. Seq only grows."""
    newest = get(-1)
    if not newest or newest[-1].get("round", 0) < rnd:
        return None
    lo, hi = 0, newest[-1]["seq"]             # invariant: the entry after hi is in the round
    hi -= 1
    while lo < hi:
        mid = (lo + hi) // 2
        page = get(mid)
        if page and page[0].get("round", 0) >= rnd:
            hi = mid
        else:
            lo = mid + 1
    return lo


def compact(e):
    """One ledger entry as the row we keep, or None. Field names come from week-08's sample and the kit; anything
    else the market adds is listed in the cursor file ("fields") to check once Round 3 is open."""
    d = e.get("data") or {}
    base = {"k": e["kind"], "seq": e["seq"], "ts": e.get("ts"), "buyer": d.get("buyer"), "order": d.get("order")}
    if e["kind"] == "settle":
        return {**base, "sku": d.get("item"), "amount": d.get("amount"), "grade": d.get("grade"),
                "honest": d.get("claim_honest"), "shop": d.get("shop") or d.get("payee"),
                "warranty": d.get("warranty_hours")}
    if e["kind"] == "broken":
        return {**base, "sku": d.get("item")}
    if e["kind"] == "result" and d.get("shop"):       # names the shop by order, in case a settle entry does not
        return {**base, "shop": d["shop"], "seller": (d.get("receipt") or {}).get("shop")}
    return None


def sync(md, get=ledger_get, max_pages=DEFAULTS["max_pages"]):
    """Reads at most max_pages of new entries. Returns how many rows it added; a slow market only delays it."""
    path, cur = os.path.join(md, ROWS), os.path.join(md, CURSOR)
    try:
        with open(cur, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        after = first_seq_of_round(get)
        if after is None:
            return 0
        state = {"after": after, "fields": {}}
        if os.path.exists(path):              # a cursor lost but rows kept would count every deal twice
            os.remove(path)
    added = 0
    os.makedirs(md, exist_ok=True)
    with open(path, "a", encoding="utf-8") as out:
        for _ in range(max_pages):
            page = get(state["after"])
            for e in page:
                seen = state["fields"].setdefault(e["kind"], [])
                state["fields"][e["kind"]] = sorted(set(seen) | set(e.get("data") or {}))
                row = compact(e) if e.get("round", 0) >= ECON_ROUND else None
                if row:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    added += 1
            if page:
                state["after"] = page[-1]["seq"]
            if len(page) < PAGE:
                break
    tmp = cur + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:   # after the rows: a crash repeats nothing it did not write ...
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, cur)                          # ... and at worst re-adds one run, which load_rows drops
    return added


def load_rows(md):
    seen, rows = set(), []
    try:
        with open(os.path.join(md, ROWS), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("seq") not in seen:
                    seen.add(r.get("seq"))
                    rows.append(r)
    except OSError:
        pass
    return rows


def my_shops():
    """How the ledger can name my shop: the name in my receipts stays when a quick tunnel's URL changes."""
    return {os.environ.get("SHOP_CARD_NAME", "Nocha Furniture"), *(os.environ[k].rstrip("/") for k in ("SHOP_PUBLIC_URL",)
                                                                  if os.environ.get(k))}


def captive_buyers(settles, me_shops, cfg):
    """Buyers who buy nearly everything from one other shop: their orders are not there for me to win."""
    by_buyer = collections.defaultdict(collections.Counter)
    for r in settles:
        if r.get("shop"):
            by_buyer[r["buyer"]][r["shop"]] += 1
    out = {}
    for buyer, shops in by_buyer.items():
        total = sum(shops.values())
        top, n = shops.most_common(1)[0]
        if total >= cfg["captive_min_deals"] and n / total >= cfg["captive_share"] and top not in me_shops:
            out[buyer] = top
    return out


BREAK_WINDOW = 7 * 3600       # goods break up to 6 h after the sale; an hour more for late ledger entries


def link_broken(rows):
    """[(broken row, the settle it belongs to)]. A broken entry names only the buyer and the SKU, so it belongs to that
    buyer's latest purchase of that SKU within the window, each purchase once. An unmatched entry is left out."""
    by_key = {}
    for r in rows:
        if r["k"] == "settle" and r.get("ts"):
            by_key.setdefault((r.get("buyer"), r.get("sku")), []).append(r)
    used, out = set(), []
    for b in (r for r in rows if r["k"] == "broken" and r.get("ts")):
        fit = [s for s in by_key.get((b.get("buyer"), b.get("sku")), []) if 0 <= b["ts"] - s["ts"] <= BREAK_WINDOW and s["seq"] not in used]
        if fit:
            s = max(fit, key=lambda x: x["ts"])
            used.add(s["seq"])
            out.append((b, s))
    return out


def break_stats(rows, catalog):
    """How often goods break, measured from the ledger: overall and by SKU, grade and shop. The market does not publish it."""
    sold = [r for r in rows if r["k"] == "settle" and (catalog.get(r.get("sku")) or {}).get("kind") == "goods"]
    broke = [s for _, s in link_broken(rows) if (catalog.get(s.get("sku")) or {}).get("kind") == "goods"]
    def table(field):
        t = {}
        for r in sold:
            t.setdefault(str(r.get(field)), {"sold": 0, "broke": 0})["sold"] += 1
        for r in broke:
            t.setdefault(str(r.get(field)), {"sold": 0, "broke": 0})["broke"] += 1
        return t
    return {"sold": len(sold), "broke": len(broke), "rate": len(broke) / len(sold) if sold else None,
            "by_sku": table("sku"), "by_grade": table("grade"), "by_shop": table("shop")}


def break_chance(stats, sku, min_sold=50, strength=20):
    """This SKU's chance of breaking, leaning on the overall rate. None until enough goods have sold to say anything."""
    if stats["sold"] < min_sold:
        return None
    s = stats["by_sku"].get(sku) or {"sold": 0, "broke": 0}
    return (s["broke"] + strength * stats["rate"]) / (s["sold"] + strength)


def estimate(rows, catalog, me_buyers=(), me_shops=(), now=None, cfg=None):
    """{"active", "n", "skus": {sku: {rate, my_share, price_ratio, deals, buyers, sellers}}, "cats": {category: {...}},
    "buyers": {buyer: [favourite categories]}, "captive": {buyer: shop}}. rate is reachable deals per hour."""
    cfg = {**DEFAULTS, **(cfg or {})}
    now = now or time.time()
    me_buyers, me_shops = set(me_buyers), set(me_shops)
    shop_of = {r["order"]: r["shop"] for r in rows if r["k"] == "result" and r.get("order")}
    mine = {r["order"]: r["seller"] for r in rows if r["k"] == "result" and r.get("seller") in me_shops}
    settles = [dict(r, shop=mine.get(r.get("order")) or r.get("shop") or shop_of.get(r.get("order"))) for r in rows
               if r["k"] == "settle" and r.get("sku") in catalog and r.get("ts")]
    captive = captive_buyers(settles, me_shops, cfg)
    hl = cfg["half_life_hours"] * 3600
    weight = lambda ts: 0.5 ** (max(0, now - ts) / hl)

    sku = collections.defaultdict(lambda: {"c": 0.0, "mine": 0.0, "ratios": [], "buyers": set(), "sellers": set(), "deals": 0})
    cat = collections.defaultdict(float)
    prefs = collections.defaultdict(lambda: collections.defaultdict(float))
    used = 0
    for r in settles:
        x, w = catalog[r["sku"]], weight(r["ts"])
        if isinstance(r.get("amount"), (int, float)) and x.get("list_price"):
            sku[r["sku"]]["ratios"].append(r["amount"] / x["list_price"])
        prefs[r["buyer"]][x["category"]] += w        # favourites are a fact about the buyer, reachable or not
        if r["buyer"] in me_buyers or r["buyer"] in captive:
            continue
        used += 1
        s = sku[r["sku"]]
        s["c"] += w
        s["deals"] += 1
        s["buyers"].add(r["buyer"])
        cat[x["category"]] += w
        if r.get("shop") in me_shops:
            s["mine"] += w
        elif r.get("shop"):
            s["sellers"].add(r["shop"])

    first = min((r["ts"] for r in settles), default=now)
    hours = max(1.0, min((now - first) / 3600, cfg["half_life_hours"] / math.log(2)))   # the window the weights cover
    in_cat = collections.Counter(x.get("category") for x in catalog.values())
    ratios = [q for s in sku.values() for q in s["ratios"]]
    typical = statistics.median(ratios) if ratios else cfg["price_ratio"]

    skus = {}
    for k, s in sku.items():
        if not s["c"]:
            continue
        c = catalog[k]["category"]
        rate = (s["c"] + cfg["shrink"] * cat[c] / max(1, in_cat[c])) / hours
        # at least an even split among its sellers: not selling a SKU I never stocked says nothing about my share,
        # and stock that really does not sell blocks its own rebuying (restock counts what I have)
        prior = 1 / (1 + len(s["sellers"]))
        skus[k] = {"rate": rate, "my_share": max(prior, s["mine"] / s["c"]),
                   "price_ratio": statistics.median(s["ratios"]) if len(s["ratios"]) >= cfg["min_prices"] else typical,
                   "deals": s["deals"], "buyers": len(s["buyers"]), "sellers": len(s["sellers"])}
    return {"active": used >= cfg["min_settles"], "n": used, "skus": skus,
            "cats": {c: {"rate": w / hours} for c, w in cat.items()},
            "buyers": {b: [c for c, _ in sorted(p.items(), key=lambda kv: -kv[1])[:5]] for b, p in prefs.items()},
            "captive": captive}
