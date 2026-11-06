"""Tool: the market's public ledger, kept as this agent's memory of what sells, and the shared policy.

    python market_tool.py stats        # print the summary the strategist reads

sync() keeps state/ledger.jsonl: the settle, broken, supply and expire entries of the last KEEP_H hours, fetched a
page at a time from GET /api/ledger?after=N (the cursor is in state/ledger_cursor.json). stats() turns them into
per-SKU numbers: settled deals, revenue and the median price as a share of the list price.

market_now() is the market's clock. This machine's clock runs minutes behind the market (no NTP), so the newest
ledger entry's ts gives the offset; restock windows and food shelf life are judged on the market's clock.

policy() is the strategist's latest policy.json merged over DEFAULT_POLICY, so every executor (restock, seller,
buyer) works with a full set of numbers even before the strategist has run once.
"""
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

MARKET = "https://market.ourolabs.ai"
HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
LEDGER = STATE / "ledger.jsonl"
CURSOR = STATE / "ledger_cursor.json"
SHOPS_MAP = STATE / "shops_map.json"
POLICY = HERE / "policy.json"
CATALOG = HERE / ".market" / "catalog.json"
KEEP_H = 6                      # hours of ledger kept on disk
PAGES_PER_SYNC = 8              # 500 entries each; the market writes about 250 a minute
KINDS = ("settle", "broken", "supply", "expire")
ME = "usagi"

DEFAULT_POLICY = {
    # restock
    "lookback_h": 2.0,          # sales window the restock ranks SKUs on
    "min_sales": 4,             # a SKU needs this many settled deals in the window to be bought
    "max_unit_list": 700000,    # skip SKUs whose list price is above this (one sofa would eat the capital)
    "max_surge": 1.08,          # skip SKUs whose supply cost is surging above this factor
    "min_shelf_h": 24,          # skip food that keeps for less than this
    "cash_reserve": 1000000,    # never spend below this balance (the bankruptcy line is 500,000)
    "window_spend_frac": 0.5,   # share of (balance - reserve) one supply window may spend (unsold goods stay in book at cost)
    "share_guess": 0.04,        # my share of a SKU's market sales before I have my own numbers
    "cover_h": 0.75,            # stock to cover this many hours of my expected sales
    "max_lines": 10,
    # seller
    "anchor_ratio": {"A": 0.74, "B": 0.68, "C": 0.55},  # price / list when the ledger has no median for the SKU
    "ask_premium": 0.06,        # first ask = anchor + this
    "floor_below_anchor": 0.06, # lowest price = anchor - this, never below cost x (1 + min_margin)
    "min_margin": 0.02,
    "clearance_h": 3.0,         # food this close to its date may go below cost (it would be lost at cost anyway)
    "warranty_h": {"A": 6, "B": 6, "C": 0},  # goods break within 6 h of the sale, so 6 h covers every break
    # buyer
    "buyer_open": 0.55,         # first offer as a share of list
    "buyer_max": 0.76,          # never pay more than this share of list (or the budget)
    "buyer_grades_goods": ["A", "B"],
    "buyer_hours_margin": 1.0,  # food must last use_within_hours + this
    "buyer_shops_tried": 3,
}


def market_now():
    try:
        return time.time() + json.loads(CURSOR.read_text())["skew"]
    except (OSError, ValueError, KeyError):
        return time.time()


def policy():
    p = json.loads(json.dumps(DEFAULT_POLICY))
    try:
        got = json.loads(POLICY.read_text(encoding="utf-8")).get("policy", {})
    except (OSError, ValueError):
        got = {}
    for k, v in got.items():
        if k in p and isinstance(p[k], dict) and isinstance(v, dict):
            p[k].update({g: x for g, x in v.items() if g in p[k]})
        elif k in p:
            p[k] = v
    return p


def catalog():
    try:
        return json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _get(path, **params):
    r = httpx.get(MARKET + path, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def sync():
    """Fetch new ledger entries into state/ledger.jsonl. Returns how many were added. Never raises."""
    STATE.mkdir(exist_ok=True)
    try:
        cur = json.loads(CURSOR.read_text())
    except (OSError, ValueError):
        cur = {}
    added = 0
    pages = PAGES_PER_SYNC if "seq" in cur else 70      # first run: backfill about two hours
    try:
        newest = _get("/api/ledger", after=-1)[-1]
        start = newest["seq"] - pages * 500
        after = max(cur.get("seq", start), start)    # far behind: skip ahead rather than fall further back
        with LEDGER.open("a", encoding="utf-8") as f:
            for _ in range(pages):
                rows = _get("/api/ledger", after=after)
                if not rows:
                    break
                for e in rows:
                    if e["kind"] in KINDS:
                        f.write(json.dumps({"seq": e["seq"], "ts": e["ts"], "kind": e["kind"], "data": e["data"]},
                                           ensure_ascii=False) + "\n")
                        added += 1
                after = rows[-1]["seq"]
                if len(rows) < 500:
                    break
        cur.update(seq=after, skew=newest["ts"] - time.time(), synced_at=time.time())
        CURSOR.write_text(json.dumps(cur))
        if time.time() - cur.get("shops_at", 0) > 600:   # shop URLs change when a kit rejoins
            m = {s["url"].rstrip("/"): s["name"] for s in _get("/api/shops")["shops"]}
            SHOPS_MAP.write_text(json.dumps(m, ensure_ascii=False))
            cur["shops_at"] = time.time()
            CURSOR.write_text(json.dumps(cur))
        prune()
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
        print(f"[market_tool] sync failed: {e!r}", file=sys.stderr)
    return added


def prune():
    """Drop entries older than KEEP_H once the oldest is an hour past it (one rewrite an hour, not every run)."""
    if not LEDGER.exists():
        return
    with LEDGER.open(encoding="utf-8") as f:
        first = f.readline()
    try:
        oldest = json.loads(first)["ts"]
    except (ValueError, KeyError):
        oldest = 0
    cut = market_now() - KEEP_H * 3600
    if oldest > cut - 3600:
        return
    keep = [l for l in LEDGER.read_text(encoding="utf-8").splitlines() if l and json.loads(l)["ts"] >= cut]
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    tmp.replace(LEDGER)


def entries(hours, kinds=KINDS):
    cut = market_now() - hours * 3600
    out = []
    if LEDGER.exists():
        for l in LEDGER.read_text(encoding="utf-8").splitlines():
            if not l:
                continue
            e = json.loads(l)
            if e["ts"] >= cut and e["kind"] in kinds:
                out.append(e)
    return out


def shop_names():
    try:
        return json.loads(SHOPS_MAP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def name_of(url):
    url = (url or "").rstrip("/")
    return shop_names().get(url) or url.split("/shop/")[-1]


def sku_stats(hours, cat=None):
    """sku -> {n, revenue, ratio (median price/list), shops, mine} over the last `hours` of settled deals."""
    cat = cat or catalog()
    acc = {}
    for e in entries(hours, ("settle",)):
        d = e["data"]
        sku = d.get("sku") or d.get("item")
        lp = (cat.get(sku) or {}).get("list_price")
        if not lp:
            continue
        a = acc.setdefault(sku, {"n": 0, "revenue": 0, "ratios": [], "shops": set(), "mine": 0})
        a["n"] += 1
        a["revenue"] += d["amount"]
        a["ratios"].append(d["amount"] / lp)
        shop = name_of(d.get("shop"))
        a["shops"].add(shop)
        a["mine"] += shop == ME
    return {k: {"n": a["n"], "revenue": a["revenue"], "ratio": round(statistics.median(a["ratios"]), 3),
                "shops": len(a["shops"]), "mine": a["mine"]} for k, a in acc.items()}


def summary(hours=2.0):
    """The compact view the strategist reads: top SKUs by revenue, categories, breakage by grade, my share."""
    cat = catalog()
    st = sku_stats(hours, cat)
    top = sorted(st.items(), key=lambda kv: -kv[1]["revenue"])[:15]
    cats = {}
    for sku, s in st.items():
        c = (cat.get(sku) or {}).get("category", "?").split(">")[-1]
        x = cats.setdefault(c, {"n": 0, "revenue": 0})
        x["n"] += s["n"]
        x["revenue"] += s["revenue"]
    settles = entries(hours, ("settle",))
    goods = [e["data"] for e in settles if (cat.get(e["data"].get("sku")) or {}).get("kind") == "goods"]
    broke = {e["data"].get("unit") for e in entries(hours, ("broken",))}
    rates = {}
    for g in "ABC":
        xs = [d for d in goods if d.get("grade") == g]
        rates[g] = {"sold": len(xs), "broke": sum(1 for d in xs if d.get("unit") in broke)}
    mine = [e["data"] for e in settles if name_of(e["data"].get("shop")) == ME]
    return {
        "hours": hours, "settles": len(settles),
        "top_skus": [{"sku": k, "name": cat.get(k, {}).get("name", "")[:40], "list": cat.get(k, {}).get("list_price"),
                      "kind": cat.get(k, {}).get("kind"), **v} for k, v in top],
        "top_categories": sorted(({"category": k, **v} for k, v in cats.items()), key=lambda x: -x["revenue"])[:10],
        "goods_breaks_by_grade": rates,
        "my_shop": {"deals": len(mine), "revenue": sum(d["amount"] for d in mine)},
        "market_share": round(len(mine) / len(settles), 4) if settles else 0,
    }


if __name__ == "__main__":
    if sys.argv[1:2] == ["stats"]:
        print(f"synced {sync()} entries", file=sys.stderr)
        print(json.dumps(summary(float(sys.argv[2]) if len(sys.argv) > 2 else 2.0), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
