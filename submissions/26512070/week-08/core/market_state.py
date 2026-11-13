"""The market's state per SKU from the public ledger, read incrementally (user 11/13 10:3x: "the logs show what every
shop holds and what sells at what price, but our system is slow").

    py -m core.market_state            # read the new ledger lines and write memory/facts/market_state.json

Until 11/13 the rules saw only SKU sale counts over 6 h (restock), each shop's median paid share (buyer) and a fixed
open share for every SKU (seller). The ledger has more: every supply line names the shop and its units, every settle
and expire line removes a unit, so what each shop holds is supply - settled - expired; every settle gives the price.
On 11/13 10:2x 106 SKUs sold three times what the whole market still held, and a 3.06M sofa that sold 36 in 6 h was
held by 14 shops and not by us.

The cursor (memory/facts/market_state_cursor.json) keeps the byte offset per ledger file, the units each shop holds
and the last WINDOW_S of settles, so a pass reads only what the collector appended (it runs after each collect round,
every 3 min). The first pass reads the last BOOT_DAYS ledger files (about a minute).
"""
import json
import statistics as st
import sys
import time

from core import atomic, paths

WINDOW_S = 6 * 3600
BOOT_DAYS = 8                 # units bought earlier than this and never sold are rare; they are left out
ME = "gom31-certified-goods"
FRESH_S = 15 * 60             # readers ignore a state older than this
MIN_N = 5                     # settles in the window before a SKU's prices are used
SCARCE = 2.0                  # sold in 6 h per unit held now, from which a SKU counts as scarce

_KINDS = (b'"kind": "supply"', b'"kind": "settle"', b'"kind": "expire"')


def _cursor_path():
    return paths.memory() / "facts" / "market_state_cursor.json"


def out_path():
    return paths.memory() / "facts" / "market_state.json"


def _catalog():
    c = atomic.read_json(paths.root() / ".market" / "catalog.json", default=None) or {}
    c = c.get("items", c) if isinstance(c, dict) else c
    return {x["sku"]: x for x in c} if isinstance(c, list) else c


def _apply(cur, d, cat):
    x, k = d.get("data") or {}, d.get("kind")
    held = cur["held"]
    if k == "supply":
        for u in x.get("units") or []:
            held[u] = [x.get("shop"), x.get("sku")]
            if x.get("shop") == ME:                 # core.price_curve joins our talks (by unit) to their SKU
                cur.setdefault("ours", {})[u] = [x.get("sku"), round(d["ts"])]
    elif k == "settle":
        held.pop(x.get("unit"), None)
        c = cat.get(x.get("sku")) or {}
        if c.get("list_price") and isinstance(x.get("amount"), (int, float)):
            cur["recent"].append([round(d["ts"], 1), x["sku"], x.get("shop"), round(x["amount"] / c["list_price"], 4),
                                  x.get("grade"), x.get("warranty_hours") or 0, x.get("buyer")])
    elif k == "expire":
        for u in x.get("units") or []:
            held.pop(u, None)


def update(now=None):
    """Read the ledger lines appended since the last pass and write the state. Returns a short summary."""
    now = now or time.time()
    cur = atomic.read_json(_cursor_path(), default=None) or {"files": {}, "held": {}, "recent": []}
    files = sorted((paths.memory() / "market").glob("ledger-*.jsonl"))
    if cur["files"]:
        last = max(cur["files"])
        files = [f for f in files if f.name >= last]
    else:
        files = files[-BOOT_DAYS:]
    cat, lines = _catalog(), 0
    for f in files:
        pos = cur["files"].get(f.name, 0)
        with open(f, "rb") as fh:
            fh.seek(pos)
            chunk = fh.read()
        end = chunk.rfind(b"\n") + 1               # a line the collector is still writing waits for the next pass
        for line in chunk[:end].splitlines():
            if not any(k in line for k in _KINDS):
                continue
            try:
                _apply(cur, json.loads(line), cat)
                lines += 1
            except (ValueError, KeyError, TypeError):
                continue
        cur["files"][f.name] = pos + end
    cur["files"] = {n: p for n, p in sorted(cur["files"].items())[-3:]}
    cur["recent"] = [r for r in cur["recent"] if r[0] >= now - WINDOW_S]
    cur["ours"] = {u: v for u, v in (cur.get("ours") or {}).items() if v[1] >= now - BOOT_DAYS * 86400}
    atomic.write_json(_cursor_path(), cur)
    doc = build(cur, cat, now)
    atomic.write_json(out_path(), doc)
    _CACHE.clear()
    return {"lines": lines, "units_held": len(cur["held"]), "skus": len(doc["skus"]), "scarce": doc["scarce_n"]}


def our_units():
    """{unit: sku} of every unit our shop bought in the last BOOT_DAYS (from the cursor)."""
    cur = atomic.read_json(_cursor_path(), default=None) or {}
    return {u: v[0] for u, v in (cur.get("ours") or {}).items()}


def _q(xs, f):
    return xs[min(len(xs) - 1, int(f * (len(xs) - 1) + 0.5))]


def build(cur, cat, now):
    holders = {}
    for shop, sku in cur["held"].values():
        holders.setdefault(sku, {}).setdefault(shop, 0)
        holders[sku][shop] += 1
    sold, sold1 = {}, {}
    for ts, sku, *_rest in cur["recent"]:
        sold.setdefault(sku, []).append(_rest[1])
        if ts >= now - 3600:
            sold1[sku] = sold1.get(sku, 0) + 1
    skus = {}
    for sku in set(holders) | set(sold):
        c = cat.get(sku) or {}
        h = holders.get(sku) or {}
        p = sorted(sold.get(sku) or [])
        held = sum(h.values())
        x = {"kind": c.get("kind"), "list": c.get("list_price"), "n6": len(p), "n1": sold1.get(sku, 0),
             "held": held, "shops": len(h), "ours": h.get(ME, 0),
             "scarcity": round(len(p) / max(1, held), 2)}
        if p:
            x.update(p25=_q(p, 0.25), p50=round(st.median(p), 4), p75=_q(p, 0.75))
        skus[sku] = x
    shops = {}
    for shop, _ in cur["held"].values():
        shops[shop] = shops.get(shop, 0) + 1
    return {"ts": now, "window_h": WINDOW_S / 3600, "skus": skus,
            "holders": {s: h for s, h in holders.items()},
            "shops": shops, "scarce_n": sum(1 for x in skus.values() if x["n6"] >= 6 and x["scarcity"] >= SCARCE)}


_CACHE = {}


def load(now=None):
    """The state when it is fresh (FRESH_S), else None. Cached per file change, so callers can ask per turn."""
    now = now or time.time()
    f = out_path()
    try:
        mt = f.stat().st_mtime
    except OSError:
        return None
    if _CACHE.get("mt") != mt:
        _CACHE.clear()
        _CACHE.update(mt=mt, doc=atomic.read_json(f, default=None))
    doc = _CACHE.get("doc")
    if not doc or now - doc.get("ts", 0) > FRESH_S:
        return None
    return doc


def sku(code, now=None):
    """One SKU's market numbers when the state is fresh and the SKU has MIN_N settles in the window, else None."""
    doc = load(now)
    x = (doc or {}).get("skus", {}).get(code)
    return x if x and x.get("n6", 0) >= MIN_N else None


def holds(shop, code, now=None):
    """Units of the SKU this shop holds, or None when unknown (no fresh state, or the shop holds nothing at all -
    a shop that buys under another name than it sells under is not judged)."""
    doc = load(now)
    if not doc or shop not in doc.get("shops", {}):
        return None
    return (doc.get("holders", {}).get(code) or {}).get(shop, 0)


if __name__ == "__main__":
    t = time.time()
    print(update(), f"{time.time() - t:.1f} s")
    sys.exit(0)
