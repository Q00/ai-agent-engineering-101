"""Week 08 -- market memory: what sells, learned from the public ledger.

    python market_memory.py            # one update, then print a summary

The market does not publish which products sell or who wants what. Every
settled deal is on the ledger, though (`settle` entries name the SKU, the
amount, the grade, the claim and whether it was honest), and so is every
item that broke. This module is the tool both agents use to read it:

  update()   pulls the ledger since the last cursor and folds it into
             memory/market.json -- demand per category and per SKU (decayed,
             so yesterday's hit fades), price paid as a share of list price,
             each shop's claim honesty, and break rates per goods grade.
  load()     the file, for the restock, the seller and the buyer.

Nothing here calls the model. It is the context layer: compact numbers the
restock turns into stock, the seller into an anchor, and the buyer into a
shop ranking -- the raw ledger (thousands of entries an hour) never goes into
a prompt.
"""
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
HERE = Path(__file__).resolve().parent
FILE = HERE / "memory" / "market.json"
TAU = 24 * 3600            # demand decays with a one-day time constant
MAX_PAGES = 40             # 500 entries a page; a longer backlog is skipped, not replayed
KEEP_RATIOS = 300          # last paid/list ratios kept per category
KEEP_GRADE_PRICES = 15     # recent settle amounts kept per (SKU, grade): what this exact unit sells for
KEEP_SETTLES = 20000      # goods settles kept to match later `broken` entries


def empty():
    return {"cursor": None, "round": 3, "updated": 0, "cat": {}, "sku": {}, "shops": {},
            "grades": {}, "goods_settles": {}, "seen": {"settle": 0, "broken": 0, "order": 0}}


def load(path=FILE):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and "cat" in d else empty()
    except (OSError, ValueError):
        return empty()


def save(mem, path=FILE):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)                                  # the seller may be reading it


def catalog(state_dir=None):
    """sku -> {name, category, list_price, kind, shelf_hours}; the kit's copy if there is one."""
    for p in ([Path(state_dir) / "catalog.json"] if state_dir else []) + [HERE / ".market" / "catalog.json"]:
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
            if c:
                return c
        except (OSError, ValueError):
            pass
    r = httpx.get(f"{MARKET}/api/catalog", timeout=60)
    return {x["sku"]: x for x in r.json()}


def bump(row, now, w=1.0):
    """Exponentially decayed counter: `score` is roughly the sales of the last day."""
    dt = max(0.0, now - row.get("t", now))
    row["score"] = row.get("score", 0.0) * math.exp(-dt / TAU) + w
    row["t"] = now
    row["n"] = row.get("n", 0) + 1


def score_now(row, now=None):
    now = now or time.time()
    return row.get("score", 0.0) * math.exp(-max(0.0, now - row.get("t", now)) / TAU)


def shop_names():
    try:
        return {s["url"].rstrip("/"): s["name"] for s in
                httpx.get(f"{MARKET}/api/shops", timeout=30).json()["shops"]}
    except Exception:                                      # noqa: BLE001
        return {}


def fold(mem, e, cat, names):
    d, kind, now = e.get("data") or {}, e.get("kind"), e.get("ts") or time.time()
    if e.get("round", 3) < 3:
        return
    if kind == "order":
        mem["seen"]["order"] += 1
    elif kind == "settle":
        mem["seen"]["settle"] += 1
        sku = d.get("item")
        info = cat.get(sku) or {}
        c = info.get("category") or "?"
        crow = mem["cat"].setdefault(c, {"ratios": []})
        bump(crow, now)
        crow["last_seq"] = e.get("seq")                     # a ledger reference the forum can cite
        srow = mem["sku"].setdefault(sku, {})
        bump(srow, now)
        amount = d.get("amount")
        if info.get("list_price") and isinstance(amount, (int, float)):
            crow["ratios"] = (crow["ratios"] + [round(amount / info["list_price"], 3)])[-KEEP_RATIOS:]
            srow["last_amount"] = amount
        if isinstance(amount, (int, float)) and d.get("grade") is not None:
            g = srow.setdefault("by_grade", {}).setdefault(str(d["grade"]), [])
            g.append(amount)
            del g[:-KEEP_GRADE_PRICES]
        shop = names.get(str(d.get("shop", "")).rstrip("/"), d.get("shop_name") or d.get("shop") or "?")
        sh = mem["shops"].setdefault(shop, {"sold": 0, "claims": 0, "dishonest": 0, "broken": 0, "goods": 0})
        sh["sold"] += 1
        if d.get("claim") is not None:
            sh["claims"] += 1
            if d.get("claim_honest") is False:
                sh["dishonest"] += 1
        if info.get("kind") == "goods":
            sh["goods"] += 1
            g = mem["grades"].setdefault(str(d.get("grade")), {"sold": 0, "broken": 0})
            g["sold"] += 1
            key = d.get("order") or f"{d.get('buyer')}|{sku}|{int(now)}"
            mem["goods_settles"][key] = {"grade": str(d.get("grade")), "shop": shop, "sku": sku,
                                         "buyer": d.get("buyer"), "ts": now}
    elif kind == "broken":
        mem["seen"]["broken"] += 1
        hit = mem["goods_settles"].get(d.get("order") or "")
        if hit is None:                                     # no order on the entry: newest settle of that SKU to that buyer
            cands = [v for v in mem["goods_settles"].values()
                     if v["sku"] == d.get("item") and v.get("buyer") == d.get("buyer")]
            hit = max(cands, key=lambda v: v["ts"], default=None)
        if hit:
            mem["grades"].setdefault(hit["grade"], {"sold": 0, "broken": 0})["broken"] += 1
            mem["shops"].setdefault(hit["shop"], {"sold": 0, "claims": 0, "dishonest": 0,
                                                  "broken": 0, "goods": 0})["broken"] += 1


def update(state_dir=None, path=FILE, log=lambda m: None):
    mem = load(path)
    cat = catalog(state_dir)
    names = shop_names()
    with httpx.Client(timeout=30) as c:
        if mem["cursor"] is None:                           # first run: start near the newest entries
            r = c.get(f"{MARKET}/api/ledger", params={"after": -500})
            rows = r.json() if r.status_code == 200 else []
            for e in rows:
                fold(mem, e, cat, names)
            mem["cursor"] = rows[-1]["seq"] if rows else 0
        for page in range(MAX_PAGES):
            r = c.get(f"{MARKET}/api/ledger", params={"after": mem["cursor"]})
            rows = r.json() if r.status_code == 200 else []
            if not rows:
                break
            for e in rows:
                fold(mem, e, cat, names)
            mem["cursor"] = rows[-1]["seq"]
            if len(rows) < 500:
                break
        else:
            r = c.get(f"{MARKET}/api/ledger", params={"after": -1})
            if r.status_code == 200 and r.json():
                log(f"[memory] backlog over {MAX_PAGES} pages; skipping to #{r.json()[-1]['seq']}")
                mem["cursor"] = r.json()[-1]["seq"]
    if len(mem["goods_settles"]) > KEEP_SETTLES:
        keep = sorted(mem["goods_settles"].items(), key=lambda kv: kv[1]["ts"])[-KEEP_SETTLES // 2:]
        mem["goods_settles"] = dict(keep)
    mem["updated"] = time.time()
    save(mem, path)
    return mem


# ------------------------------------------------------------ what the agents ask

def sku_median(mem, sku, grade, min_n=4):
    """Median settle amount of this SKU in this grade across the market, or None with too few deals."""
    xs = sorted(((mem.get("sku") or {}).get(sku) or {}).get("by_grade", {}).get(str(grade), []))
    return xs[len(xs) // 2] if len(xs) >= min_n else None


def paid_ratio(mem, category, default=0.85):
    """Median price paid as a share of list price in a category (market-wide)."""
    r = (mem.get("cat", {}).get(category) or {}).get("ratios") or []
    return statistics.median(r) if len(r) >= 5 else default


def break_rate(mem, grade, default=0.08):
    g = mem.get("grades", {}).get(str(grade)) or {}
    return (g["broken"] + 1) / (g["sold"] + 1 / default) if g.get("sold") else default


def hot_categories(mem, k=20):
    now = time.time()
    rows = [(c, score_now(v, now)) for c, v in mem.get("cat", {}).items()]
    return sorted(rows, key=lambda x: -x[1])[:k]


def hot_skus(mem, k=40):
    now = time.time()
    rows = [(s, score_now(v, now)) for s, v in mem.get("sku", {}).items()]
    return sorted(rows, key=lambda x: -x[1])[:k]


def honesty(mem, shop):
    s = mem.get("shops", {}).get(shop) or {}
    return {"sold": s.get("sold", 0), "dishonest": s.get("dishonest", 0),
            "broken": s.get("broken", 0), "goods": s.get("goods", 0)}


if __name__ == "__main__":
    m = update(log=lambda x: print(x, file=sys.stderr))
    print(json.dumps({"cursor": m["cursor"], "seen": m["seen"], "hot": hot_categories(m, 10),
                      "grades": m["grades"]}, ensure_ascii=False, indent=1))
