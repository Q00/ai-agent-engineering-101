"""Read-only look at the public ledger as our memory holds it (table settles): what a SKU or a category sold
for in the last hours, overall or split by grade, shop or SKU, with ledger seqs to cite."""
import sqlite3
import statistics
import time

MAX_HOURS, MAX_GROUPS = 72, 12
GROUPS = {"grade": "grade", "shop": "shop", "sku": "sku"}


def _summary(rows):
    ratios = sorted(r[1] / r[2] for r in rows)
    return {"deals": len(rows), "price_to_list": {"min": round(ratios[0], 3), "median": round(statistics.median(ratios), 3),
                                                  "max": round(ratios[-1], 3)},
            "refs": [r[0] for r in rows[:3]]}


def run(db_path, sku=None, category=None, hours=24, by=None, kind=None, now=None):
    """sku: exact SKU id. category: a category name or part of one. hours: look-back, at most 72.
    by: None, "grade", "shop" or "sku". kind: "food" or "goods" to narrow a category."""
    if not sku and not category:
        return {"error": "give a sku or a category"}
    if by is not None and by not in GROUPS:
        return {"error": "by must be one of grade, shop, sku"}
    since = (time.time() if now is None else now) - max(0.1, min(float(hours), MAX_HOURS)) * 3600
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        if sku:
            where, args = "sku = ?", [str(sku)]
        else:
            cats = [r[0] for r in c.execute("SELECT DISTINCT category FROM settles WHERE category LIKE ?",
                                            (f"%{category}%",))][:8]
            if not cats:
                return {"deals": 0, "note": f"no category matches {category!r}"}
            where, args = f"category IN ({','.join('?' * len(cats))})", cats
        if kind in ("food", "goods"):
            where += " AND kind = ?"
            args.append(kind)
        col = GROUPS.get(by)
        rows = c.execute(f"SELECT seq, amount, list_price, {col or 'NULL'} FROM settles WHERE {where} AND ts > ? AND "
                         f"list_price > 0 AND amount > 0 ORDER BY seq DESC", (*args, since)).fetchall()
    finally:
        c.close()
    if not rows:
        return {"deals": 0}
    out = _summary(rows)
    out["hours"] = round(min(float(hours), MAX_HOURS), 1)
    if col:
        groups = {}
        for r in rows:
            groups.setdefault(r[3], []).append(r)
        top = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:MAX_GROUPS]
        out["by_" + by] = [{by: k, **_summary(v)} for k, v in top]
    return out
