"""Buyer-only, all-sales break statistics. No maturity filtering or persistent cache."""
BREAK_WINDOW = 7 * 3600


def _scan(rows):
    by_key, by_order, sales, breaks = {}, {}, [], []
    for r in rows:
        kind = r["k"]
        if kind == "settle":
            sales.append(r)
            if r.get("ts"):
                by_key.setdefault((r.get("buyer"), r.get("sku")), []).append(r)
                if r.get("order"):
                    by_order[r["order"]] = r
        elif kind == "broken" and r.get("ts"):
            breaks.append(r)
    used, out = set(), []
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
    return out, sales


def break_stats(rows, catalog):
    """Same contract as market_model.break_stats(rows, catalog, mature_only=False)."""
    linked, sales = _scan(rows)
    goods = lambda r: (catalog.get(r.get("sku")) or {}).get("kind") == "goods"
    broke = [s for _, s in linked if goods(s)]
    sold = [r for r in sales if goods(r)]
    by_sku, by_grade, by_shop, sku_grade = {}, {}, {}, {}
    for group, counter in ((sold, "sold"), (broke, "broke")):
        for r in group:
            by_sku.setdefault(str(r.get("sku")), {"sold": 0, "broke": 0})[counter] += 1
            by_grade.setdefault(str(r.get("grade")), {"sold": 0, "broke": 0})[counter] += 1
            by_shop.setdefault(str(r.get("shop")), {"sold": 0, "broke": 0})[counter] += 1
            sku_grade.setdefault(f"{r.get('sku')}|{r.get('grade')}", {"sold": 0, "broke": 0})[counter] += 1
    return {"sold": len(sold), "broke": len(broke), "rate": len(broke) / len(sold) if sold else None,
            "by_sku": by_sku, "by_grade": by_grade, "by_shop": by_shop, "by_sku_grade": sku_grade}
