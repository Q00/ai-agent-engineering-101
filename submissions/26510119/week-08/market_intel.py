"""The market researcher (B): reads the public ledger and the shop list, keeps what the other agents need.

No LLM. Each update() reads the ledger from where the last one stopped and keeps the recent events in
$MARKET_DIR/intel.json:
  settles: [ts, sku, price, shop, seq, data]            what actually sold, at what price (demand)
  stock:   {category leaf: units held by other shops}   what is already on offer (supply)
Round 3 `order` entries on the ledger carry no item or budget (agent.md), so demand is read from `settle`
entries: which SKUs sold, in which categories, at what share of the list price.
"""
import json
import os
import time
from pathlib import Path

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
MARKET_DIR = Path(os.environ.get("MARKET_DIR", Path(__file__).with_name(".market")))
INTEL = MARKET_DIR / "intel.json"
MAX_PAGES = 20           # 500 ledger entries a page; caps one update's time


def _catalog():
    try:
        c = json.loads((MARKET_DIR / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return c if isinstance(c, dict) else {x["sku"]: x for x in c}


def leaf(category):
    return (category or "").split(">")[-1]


def load():
    try:
        return json.loads(INTEL.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"seq": None, "settles": [], "stock": {}, "updated": 0}


def update(window_hours=6, me=None):
    """Read new ledger entries and the shops' stock; drop events older than the window. Returns the intel."""
    intel = load()
    cat = _catalog()
    with httpx.Client(timeout=20) as c:
        after = intel["seq"] if intel["seq"] is not None else -500
        for _ in range(MAX_PAGES):
            r = c.get(f"{MARKET}/api/ledger", params={"after": after})
            rows = r.json() if r.status_code == 200 else []
            for e in rows:
                if e.get("round", 0) < 3:
                    continue
                d = e.get("data") or {}
                if e["kind"] == "settle" and d.get("item"):
                    intel["settles"].append([e["ts"], d.get("item"), d.get("price"), d.get("shop"), e["seq"],
                                             {k: d.get(k) for k in ("buyer", "grade", "claim", "claim_honest",
                                                                    "warranty_hours", "unit") if k in d}])
            if rows:
                after = intel["seq"] = rows[-1]["seq"]
            if len(rows) < 500:
                break
        r = c.get(f"{MARKET}/api/shops")
        if r.status_code == 200:
            stock = {}
            for s in r.json().get("shops", []):
                if me and s.get("name") == me:
                    continue
                for sku, n in (s.get("stock") or {}).items():
                    k = leaf((cat.get(sku) or {}).get("category"))
                    stock[k] = stock.get(k, 0) + n
            intel["stock"] = stock
    cut = time.time() - window_hours * 3600
    intel["settles"] = [s for s in intel["settles"] if s[0] >= cut]
    intel["updated"] = time.time()
    tmp = INTEL.with_suffix(".tmp")
    tmp.write_text(json.dumps(intel, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, INTEL)
    return intel


def demand(intel):
    """Settled sales per category leaf and per SKU in the window: the market's only public signal of what sells."""
    cat = _catalog()
    by_cat, by_sku = {}, {}
    for row in intel["settles"]:
        sku = row[1]
        k = leaf((cat.get(sku) or {}).get("category"))
        by_cat[k] = by_cat.get(k, 0) + 1
        by_sku[sku] = by_sku.get(sku, 0) + 1
    return by_cat, by_sku


def price_share(intel):
    """Median settled price as a share of list price, per category leaf (None where nothing sold)."""
    cat = _catalog()
    shares = {}
    for row in intel["settles"]:
        x = cat.get(row[1])
        if x and row[2]:
            shares.setdefault(leaf(x["category"]), []).append(row[2] / x["list_price"])
    return {k: sorted(v)[len(v) // 2] for k, v in shares.items()}


if __name__ == "__main__":
    i = update()
    by_cat, by_sku = demand(i)
    print(json.dumps({"settles": len(i["settles"]),
                      "top_categories": sorted(by_cat.items(), key=lambda x: -x[1])[:10],
                      "stock_categories": len(i["stock"])}, ensure_ascii=False))
