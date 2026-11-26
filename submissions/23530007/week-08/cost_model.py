"""Unit costs for the patch 3.6 saved formula (11-12).

Since patch 3.6 (live 11-12 ~08:40 UTC, all past deals rescored) a deal scores
    v = min(1, (list - price) / (list - unit cost))        broken: 0 refunded, -1 not
so what a price is worth depends on the unit's cost. The ledger shows every supply (shop, sku, qty, total, units)
and every settle (unit, grade, amount), so the cost of each sold unit is known exactly after the fact, and the
cost share of a SKU by grade can be estimated before a deal.

    python cost_model.py            sync supply costs from data/market_archive.db, write .market/cost_model.json
    from cost_model import unit_costs, cost_share
    unit_costs(["u-..", ...])  -> {unit: cost}
    cost_share(sku, grade, kind) -> cost / list estimate (SKU+grade median over 72 h, else the grade's median by kind,
                                    else agent.md's midpoint A .65 / B .50 / C .35)

State: data/cost_model.db (unit -> cost; gitignored with data/), .market/cost_model.json (shares). Run every tick by
watch_round.sh after ops/archive_db.py.
"""
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / "data" / "market_archive.db"
DB = HERE / "data" / "cost_model.db"
OUT = HERE / ".market" / "cost_model.json"
WINDOW = 72 * 3600
MIN_SKU = 3
MID = {"A": 0.65, "B": 0.50, "C": 0.35}          # agent.md: A 55-75%, B 40-60%, C 25-45% of list


def connect():
    db = sqlite3.connect(DB, timeout=60)
    db.execute("CREATE TABLE IF NOT EXISTS unit_cost (unit TEXT PRIMARY KEY, sku TEXT, shop TEXT, cost REAL, ts REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS sync (k TEXT PRIMARY KEY, v INTEGER)")
    return db


def sync(db):
    arc = sqlite3.connect(f"file:{ARCHIVE}?mode=ro", uri=True, timeout=60)
    last = (db.execute("SELECT v FROM sync WHERE k='supply_seq'").fetchone() or [-1])[0]
    n, top = 0, last
    for seq, ts, data in arc.execute("SELECT seq, ts, data FROM ledger WHERE kind='supply' AND seq > ? ORDER BY seq", (last,)):
        d = json.loads(data).get("data") or {}
        units = d.get("units") or []
        q = d.get("qty") or len(units)
        if units and q and d.get("total") is not None:
            c = d["total"] / q
            db.executemany("INSERT OR REPLACE INTO unit_cost VALUES (?,?,?,?,?)",
                           [(u, d.get("sku"), d.get("shop"), c, ts) for u in units])
            n += len(units)
        top = seq
    db.execute("INSERT OR REPLACE INTO sync VALUES ('supply_seq', ?)", (top,))
    db.commit()
    return n


def unit_costs(units):
    units = [u for u in units if u]
    if not units or not DB.exists():
        return {}
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    out = {}
    for i in range(0, len(units), 900):
        part = units[i:i + 900]
        q = ",".join("?" * len(part))
        out.update(dict(db.execute(f"SELECT unit, cost FROM unit_cost WHERE unit IN ({q})", part)))
    return out


def build(db):
    """Cost share by SKU+grade and by kind+grade from the settles of the last WINDOW."""
    cat = json.loads((HERE / ".market" / "catalog.json").read_text(encoding="utf-8"))
    arc = sqlite3.connect(f"file:{ARCHIVE}?mode=ro", uri=True, timeout=60)
    since = time.time() - WINDOW
    rows = []
    for (data,) in arc.execute("SELECT data FROM ledger WHERE kind='settle' AND ts > ?", (since,)):
        d = json.loads(data).get("data") or {}
        it = cat.get(d.get("sku")) or {}
        if it.get("list_price") and d.get("unit"):
            rows.append((d["unit"], d["sku"], d.get("grade"), it.get("kind") or ("food" if d.get("expires_at") else "goods"),
                         it["list_price"], d.get("amount")))
    costs = unit_costs([r[0] for r in rows])
    sku, kind = {}, {}
    for u, s, g, k, lp, amt in rows:
        if u in costs and g:
            c = costs[u] / lp
            sku.setdefault(f"{s}|{g}", []).append(c)
            kind.setdefault(f"{k}|{g}", []).append(c)
    out = {"t": time.time(), "settles": len(rows), "costed": sum(1 for r in rows if r[0] in costs),
           "kind": {k: {"n": len(v), "c": round(statistics.median(v), 4)} for k, v in kind.items()},
           "sku": {k: round(statistics.median(v), 4) for k, v in sku.items() if len(v) >= MIN_SKU}}
    OUT.write_text(json.dumps(out, ensure_ascii=False))
    return out


_C = {"t": 0, "m": {}}


def cost_share(sku, grade, kind):
    """cost / list estimate for a unit of `sku` in `grade` ('A'/'B'/'C'; None = B)."""
    if time.time() - _C["t"] > 600:
        _C["t"] = time.time()
        try:
            _C["m"] = json.loads(OUT.read_text())
        except (OSError, ValueError):
            pass
    g = grade if grade in MID else "B"
    m = _C["m"]
    v = (m.get("sku") or {}).get(f"{sku}|{g}")
    if v is None:
        v = ((m.get("kind") or {}).get(f"{kind}|{g}") or {}).get("c")
    return float(v) if v is not None else MID[g]


def deal_value(price, lp, cost, broken=None, warranty=0):
    """Patch 3.6 score of one deal."""
    if broken:
        return 0.0 if (warranty or 0) >= 6 else -1.0
    if lp <= cost:
        return 1.0
    return max(-1.0, min(1.0, (lp - price) / (lp - cost)))


if __name__ == "__main__":
    db = connect()
    t0 = time.time()
    n = sync(db)
    out = build(db)
    if "--quiet" not in sys.argv:
        print(f"synced {n} units in {time.time() - t0:.0f} s; settles {out['settles']}, costed {out['costed']}, "
              f"skus {len(out['sku'])}; by kind {out['kind']}")
