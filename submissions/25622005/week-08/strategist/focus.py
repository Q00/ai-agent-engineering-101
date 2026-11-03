"""Focus SKUs (stock.focus_skus): the few SKUs the restock hook stocks deeper, picked from the public ledger.

A SKU is worth stocking deeper when the market settles it often and few shops supply it. Each run adds the
ledger rows it read to rolling 6 h counts kept in the strategist's state: settles per SKU in 30-minute
buckets, and per SKU the shops that supplied it with the time each was last seen (our own supply left out).
Older buckets and suppliers drop out. The choice is deterministic, never the model's; the model sees the
list and may tune stock.focus_cover_mult. A focus SKU stays while it is still near the top (within
KEEP_RANK x stock.focus_max, at least half of stock.focus_min_settles), so the list does not flap, and at
most MAX_SWAPS new SKUs enter per run. Until the counts cover 6 h no focus SKU is dropped for low counts.
Forum posts that name SKUs are counted for the brief only and never move the list.
"""
import math
import re
from collections import Counter

ME = "psyoung82"
WINDOW_S = 6 * 3600
BUCKET_S = 1800
KEEP_RANK = 2
MAX_SWAPS = 3
REFS = 3                    # recent settle seqs kept per SKU as evidence
SKU = re.compile(r"[A-Za-z0-9_-]{1,40}")       # what policy.validate accepts in stock.focus_skus
MENTION = re.compile(r"\bG[0-9a-fA-F]{8}\b")


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def update(fs, ledger, now, me=ME):
    """Add this run's ledger rows to the counts in fs (state["focus"], changed in place), dropping rows older
    than 6 h. fs["since"] is when the counts began; it restarts when nothing in them is left."""
    settles = fs.setdefault("settles", {})          # bucket start (str) -> {sku: n}
    suppliers = fs.setdefault("suppliers", {})      # sku -> {shop name: last supply ts}
    refs = fs.setdefault("refs", {})                # sku -> [[seq, ts], ...], newest last
    cut = now - WINDOW_S
    for k in [k for k in settles if int(k) + BUCKET_S <= cut]:
        del settles[k]
    for sku in list(suppliers):
        suppliers[sku] = {s: t for s, t in suppliers[sku].items() if t > cut}
        if not suppliers[sku]:
            del suppliers[sku]
    for sku in list(refs):
        refs[sku] = [x for x in refs[sku] if x[1] > cut]
        if not refs[sku]:
            del refs[sku]
    if not settles or not _num(fs.get("since")):
        fs["since"] = now
    for r in ledger if isinstance(ledger, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("data"), dict):
            continue
        sku, ts = r["data"].get("sku"), r.get("ts") if _num(r.get("ts")) else now
        if not isinstance(sku, str) or not SKU.fullmatch(sku) or ts <= cut:
            continue
        if r.get("kind") == "settle":
            b = settles.setdefault(str(int(ts // BUCKET_S * BUCKET_S)), {})
            b[sku] = b.get(sku, 0) + 1
            if type(r.get("seq")) is int:
                refs[sku] = (refs.get(sku, []) + [[r["seq"], ts]])[-REFS:]
        elif r.get("kind") == "supply" and isinstance(r["data"].get("shop"), str) and r["data"]["shop"] != me:
            shops = suppliers.setdefault(sku, {})
            name = r["data"]["shop"][:40]
            shops[name] = max(ts, shops.get(name, 0))
    return fs


def covered_h(fs, now):
    return round((now - fs.get("since", now)) / 3600, 1)


def stats(fs, sku):
    """{sku, settles_6h, suppliers_6h, score, refs} from the counts."""
    n = sum(b.get(sku, 0) for b in fs.get("settles", {}).values())
    k = len(fs.get("suppliers", {}).get(sku, {}))
    return {"sku": sku, "settles_6h": n, "suppliers_6h": k, "score": round(n / (k + 1), 2),
            "refs": [x[0] for x in fs.get("refs", {}).get(sku, [])]}


def select(fs, catalog, pol, now):
    """Ranked rows (stats) of the SKUs that may be focus SKUs: in the catalog, food only with a catalog
    shelf_hours of at least stock.food_shelf_hours, and at least half of stock.focus_min_settles settles in
    6 h (half, so that choose can keep a current focus SKU; a new one needs the full number)."""
    st = pol["stock"]
    totals = Counter()
    for b in fs.get("settles", {}).values():
        totals.update(b)
    rows = []
    for sku, n in totals.items():
        c = catalog.get(sku)
        if not isinstance(c, list) or len(c) < 4 or n < st["focus_min_settles"] / 2:
            continue
        shelf = c[4] if len(c) > 4 and _num(c[4]) else None
        if c[3] == "food" and (shelf is None or shelf < st["food_shelf_hours"]):
            continue
        rows.append(stats(fs, sku))
    return sorted(rows, key=lambda r: (-r["score"], -r["settles_6h"], r["sku"]))


def choose(rows, current, pol, covered=True):
    """The new focus list from select's rows. A current SKU stays while it ranks within KEEP_RANK x focus_max
    (or always while the counts do not cover 6 h yet); free places take the best new SKUs with at least
    focus_min_settles, at most MAX_SWAPS per run. Kept SKUs keep their order, new ones follow by rank."""
    st = pol["stock"]
    n = st["focus_max"]
    rank = {r["sku"]: i for i, r in enumerate(rows)}
    keep = [s for s in current if not covered or rank.get(s, math.inf) < KEEP_RANK * n]
    keep = sorted(keep, key=lambda s: rank.get(s, math.inf))[:n] if len(keep) > n else keep
    new = [r["sku"] for r in rows if r["settles_6h"] >= st["focus_min_settles"] and r["sku"] not in keep]
    return keep + new[:max(0, min(MAX_SWAPS, n - len(keep)))]


def mentions(posts):
    """{sku: posts naming it} over the fetched forum posts, the 10 most named. A hint for the brief only."""
    c = Counter()
    for p in posts if isinstance(posts, list) else []:
        if isinstance(p, dict):
            text = " ".join(str(p.get(k) or "") for k in ("title", "body", "text"))
            c.update(set(MENTION.findall(text)))
    return dict(c.most_common(10))
