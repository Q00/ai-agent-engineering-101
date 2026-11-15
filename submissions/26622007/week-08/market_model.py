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
import sys
import time

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
ECON_ROUND = 3
ROWS = "ledger_rows.jsonl"
CURSOR = "ledger_cursor.json"
REVIEWS = "ledger_reviews.jsonl"   # result reviews: stars and verified by account (review_row)
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
    if e["kind"] == "supply" and d.get("shop") == os.environ.get("SHOP_MARKET_NAME", "nocha12"):
        # Keep our delivery proof even when every unit sells between inventory snapshots.
        return {**base, "shop": d["shop"], "sku": d.get("sku"), "units": d.get("units")}
    if e["kind"] == "settle":
        return {**base, "sku": d.get("item"), "amount": d.get("amount"), "grade": d.get("grade"),
                "honest": d.get("claim_honest"), "shop": d.get("shop") or d.get("payee"),
                "warranty": d.get("warranty_hours"), "unit": d.get("unit")}
    if e["kind"] == "broken":
        return {**base, "sku": d.get("item")}
    if e["kind"] == "result" and d.get("shop"):       # names the shop by order, in case a settle entry does not
        return {**base, "shop": d["shop"], "seller": (d.get("receipt") or {}).get("shop")}
    return None


def review_row(e):
    """A Round 3 result's review as kept in REVIEWS, or None. The shop's stars part is one mean per reviewing account
    (rules, Scoring); the rows file never kept stars, and stays as it was: it is read by byte-sized tails."""
    d = e.get("data") or {}
    if e.get("kind") != "result" or e.get("round", 0) < ECON_ROUND or d.get("stars") is None:
        return None
    return {"seq": e["seq"], "ts": e.get("ts"), "buyer": d.get("buyer"), "order": d.get("order"), "shop": d.get("shop"),
            "seller": (d.get("receipt") or {}).get("shop"), "stars": d.get("stars"), "verified": d.get("verified")}


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
        for p in (path, os.path.join(md, REVIEWS)):   # a cursor lost but rows kept would count every deal twice
            if os.path.exists(p):
                os.remove(p)
    added = 0
    os.makedirs(md, exist_ok=True)
    with open(path, "a", encoding="utf-8") as out, open(os.path.join(md, REVIEWS), "a", encoding="utf-8") as rev:
        for _ in range(max_pages):
            page = get(state["after"])
            for e in page:
                seen = state["fields"].setdefault(e["kind"], [])
                state["fields"][e["kind"]] = sorted(set(seen) | set(e.get("data") or {}))
                row = compact(e) if e.get("round", 0) >= ECON_ROUND else None
                if row:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    added += 1
                review = review_row(e)
                if review:
                    rev.write(json.dumps(review, ensure_ascii=False) + "\n")
            if page:
                state["after"] = page[-1]["seq"]
            if len(page) < PAGE:
                break
    tmp = cur + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:   # after the rows: a crash repeats nothing it did not write ...
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, cur)                          # ... and at worst re-adds one run, which load_rows drops
    return added


SHARED = ("k", "buyer", "shop", "seller", "sku", "grade")   # values repeated across rows: one string each (_shared)


def _shared(r):
    """The same row with its keys and its repeated values (SHARED) interned. json.loads makes new key strings for every
    line: the 2.4 M rows of the 526 MB file took ~1.0 kB each (2.4 GB) in seller.py and restock.py on 11/15, ~450 B shared."""
    it = sys.intern
    return {it(k): it(v) if k in SHARED and type(v) is str else v for k, v in r.items()}


def load_rows(md, name=ROWS):
    seen, rows = set(), []
    try:
        with open(os.path.join(md, name), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("seq") not in seen:
                    seen.add(r.get("seq"))
                    rows.append(_shared(r))
    except OSError:
        pass
    return rows


def load_break_rows(md):
    """What break_stats reads, without the result rows (two thirds of the ledger): the settle and broken rows, each seq
    once, and one {"k": "last", "ts"} row with the latest time of any row, which mature_only measures from. seller.py
    held the whole ledger every 10 min for this (11/15: 4-6.7 GB beside its task store)."""
    seen, rows, last = set(), [], None
    try:
        with open(os.path.join(md, ROWS), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or r.get("seq") in seen:
                    continue
                seen.add(r.get("seq"))
                ts = r.get("ts")
                if isinstance(ts, (int, float)) and (last is None or ts > last):
                    last = ts
                if r.get("k") in ("settle", "broken"):
                    rows.append(_shared(r))
    except OSError:
        pass
    if last is not None:
        rows.append({"k": "last", "ts": last})
    return rows


def load_reviews(md):
    """REVIEWS, each seq once (a run repeated after a crash re-adds its rows, as in ROWS)."""
    return load_rows(md, REVIEWS)


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
    """[(broken row, the settle it belongs to)]. A broken entry names its order: it belongs to the settle of that order.
    Without an order it belongs to that buyer's latest purchase of that SKU within the window, each purchase once. An
    unmatched entry is left out. (By buyer and SKU alone 751 of 969 Round 3 breaks went to another order of the same
    buyer, which mixed up the break rates by grade and shop.)"""
    by_key, by_order = {}, {}
    for r in rows:
        if r["k"] == "settle" and r.get("ts"):
            by_key.setdefault((r.get("buyer"), r.get("sku")), []).append(r)
            if r.get("order"):
                by_order[r["order"]] = r
    used, out = set(), []
    breaks = [r for r in rows if r["k"] == "broken" and r.get("ts")]
    for b in breaks:
        s = by_order.get(b.get("order"))
        if s is not None and s.get("sku") == b.get("sku") and s["seq"] not in used:
            used.add(s["seq"])
            out.append((b, s))
    for b in (r for r in breaks if r.get("order") not in by_order):
        fit = [s for s in by_key.get((b.get("buyer"), b.get("sku")), []) if 0 <= b["ts"] - s["ts"] <= BREAK_WINDOW and s["seq"] not in used]
        if fit:
            s = max(fit, key=lambda x: x["ts"])
            used.add(s["seq"])
            out.append((b, s))
    return out


def break_stats(rows, catalog, mature_only=False):
    """How often goods break, measured from the ledger: overall and by SKU, grade and shop. The market does not publish it.
    mature_only (seller and restock, GPT Pro 11/06 23:40): a sale of the last BREAK_WINDOW that has not broken yet is
    left out, it may still break; one that already broke counts. Never under the rate with every sale (the same breaks
    over fewer sales; it leans high, the safe side for a floor). The buyer keeps every sale (its own task's call)."""
    goods = lambda r: (catalog.get(r.get("sku")) or {}).get("kind") == "goods"
    broke = [s for _, s in link_broken(rows) if goods(s)]
    sold = [r for r in rows if r["k"] == "settle" and goods(r)]
    if mature_only:
        stamps = [r["ts"] for r in rows if isinstance(r.get("ts"), (int, float))]
        mature = (max(stamps) if stamps else 0) - BREAK_WINDOW
        gone = {id(s) for s in broke}
        sold = [r for r in sold if id(r) in gone or not isinstance(r.get("ts"), (int, float)) or r["ts"] <= mature]
    def table(field):
        t = {}
        for r in sold:
            t.setdefault(str(r.get(field)), {"sold": 0, "broke": 0})["sold"] += 1
        for r in broke:
            t.setdefault(str(r.get(field)), {"sold": 0, "broke": 0})["broke"] += 1
        return t
    sku_grade = {}
    for r in sold:
        sku_grade.setdefault(f"{r.get('sku')}|{r.get('grade')}", {"sold": 0, "broke": 0})["sold"] += 1
    for r in broke:
        sku_grade.setdefault(f"{r.get('sku')}|{r.get('grade')}", {"sold": 0, "broke": 0})["broke"] += 1
    return {"sold": len(sold), "broke": len(broke), "rate": len(broke) / len(sold) if sold else None,
            "by_sku": table("sku"), "by_grade": table("grade"), "by_shop": table("shop"), "by_sku_grade": sku_grade}


def break_chance(stats, sku, min_sold=50, strength=20):
    """This SKU's chance of breaking, leaning on the overall rate. None until enough goods have sold to say anything."""
    if stats["sold"] < min_sold:
        return None
    s = stats["by_sku"].get(sku) or {"sold": 0, "broke": 0}
    return (s["broke"] + strength * stats["rate"]) / (s["sold"] + strength)


def unit_break_chance(stats, sku, grade, min_sold=50, strength=20):
    """This unit's chance of breaking: its SKU and grade, leaning on the whole market's rate for that grade. The grade is
    most of the risk: goods sold over 6 h before 11/06 16:40 broke A 3.0% (1,515 of 50,935), B 12.2% (1,774 of 14,497),
    C 34.4% (2,366 of 6,882), ours A 2.5%, B 13.3%, C 31.4% (GPT Pro 11/06 16:45 §3.5-3), while one rate per SKU priced a C
    unit as if it broke like an A. Falls back to break_chance when the grade is unknown or has too few sales."""
    if stats["sold"] < min_sold:
        return None
    g = (stats.get("by_grade") or {}).get(str(grade)) if grade else None
    if not g or g["sold"] < min_sold:
        return break_chance(stats, sku, min_sold, strength)
    s = (stats.get("by_sku_grade") or {}).get(f"{sku}|{grade}") or {"sold": 0, "broke": 0}
    return (s["broke"] + strength * g["broke"] / g["sold"]) / (s["sold"] + strength)


GRADE_MIN_PRICES = 5        # a grade's own median needs this many deals (independent review: 3 swing too much)


def grade_ratios(s, recent, min_prices):
    """{grade: median price / list} for one SKU's deals (estimate()). The last price_hours' deals of that grade when there
    are enough; else the SKU's recent median moved by the grade's whole-log premium (its median over the SKU's), so an old
    price never outranks a recent one (independent review); a grade with too few deals either way is left out."""
    need = max(min_prices, GRADE_MIN_PRICES)
    everything = statistics.median(s["ratios"]) if s["ratios"] else None
    now_all = statistics.median(s["recent"]) if recent and len(s["recent"]) >= min_prices else everything
    out = {}
    for g, (every, new) in sorted(s["grades"].items()):
        if recent and len(new) >= need:
            out[g] = statistics.median(new)
        elif len(every) >= need and everything and now_all:
            out[g] = now_all * statistics.median(every) / everything
    return out


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

    sku = collections.defaultdict(lambda: {"c": 0.0, "mine": 0.0, "ratios": [], "recent": [], "buyers": set(), "sellers": set(),
                                           "mine_recent": 0,
                                           "deals": 0, "grades": collections.defaultdict(lambda: ([], []))})
    recent = cfg.get("price_hours")
    recent = recent * 3600 if isinstance(recent, (int, float)) and not isinstance(recent, bool) and recent > 0 else None
    cat = collections.defaultdict(float)
    prefs = collections.defaultdict(lambda: collections.defaultdict(float))
    used = 0
    for r in settles:
        x, w = catalog[r["sku"]], weight(r["ts"])
        # the prices are what other buyers paid: our own buyer's deals at other shops (it buys low, Saved is its score)
        # were left out of the demand below but fed this median, the price our seller opens and holds at (GPT Pro 14,
        # 11/10; 대표님 23:48). A captive buyer's prices stay: it is not ours
        if r["buyer"] not in me_buyers and isinstance(r.get("amount"), (int, float)) and x.get("list_price"):
            sku[r["sku"]]["ratios"].append(r["amount"] / x["list_price"])
            by_grade = sku[r["sku"]]["grades"][r["grade"]] if r.get("grade") in ("A", "B", "C") else None
            if by_grade is not None:
                by_grade[0].append(r["amount"] / x["list_price"])
            if recent and now - r["ts"] <= recent:
                sku[r["sku"]]["recent"].append(r["amount"] / x["list_price"])
                if by_grade is not None:
                    by_grade[1].append(r["amount"] / x["list_price"])
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
            if recent and now - r["ts"] <= recent:
                s["mine_recent"] += 1
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
                   # price_hours: the median of the last hours' deals, when there are enough. The whole-round median
                   # ran 0.73-0.78 of list on the 30만+ goods while their last 2 h settled at 0.65-0.73 (ledger 11/06
                   # 17:00-19:00), so 1.05 x it opened above every winning quote: 10 of 52 quotes won after 18:22
                   "price_ratio": statistics.median(s["recent"]) if recent and len(s["recent"]) >= cfg["min_prices"]
                   else statistics.median(s["ratios"]) if len(s["ratios"]) >= cfg["min_prices"] else typical,
                   # the same per grade (A new, B damaged box, C refurbished): 11/06 20:4x-23:4x G8ec51935 sold at A 0.700,
                   # B 0.637, C 0.580 of list against 0.681 for all, so B units opened at the SKU median were agreed and
                   # paid elsewhere while A units sold under their market. A grade without min_prices deals is left out.
                   "grade_ratio": grade_ratios(s, recent, cfg["min_prices"]),
                   "deals": s["deals"], "buyers": len(s["buyers"]), "sellers": len(s["sellers"])}
        if recent:      # what we sell now: the weighted share lags by a day (11/07 08:0x G24f5f979 2.3/h at rate x
            skus[k]["mine_rate"] = s["mine_recent"] / (recent / 3600)    # my_share against 11.3/h of ours over 3 h)
    return {"active": used >= cfg["min_settles"], "n": used, "skus": skus,
            "cats": {c: {"rate": w / hours} for c, w in cat.items()},
            "buyers": {b: [c for c, _ in sorted(p.items(), key=lambda kv: -kv[1])[:5]] for b, p in prefs.items()},
            "captive": captive}
