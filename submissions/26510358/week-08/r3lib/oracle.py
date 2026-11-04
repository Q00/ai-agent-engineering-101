"""Price oracle: settled-price distributions from the public ledger (memory.settles), per SKU and per category.

One component, three users:
  - the shop's first ask already reads memory.price_ratio (median); the oracle adds the spread (q25/q75) and counts
  - the forum report lane (forum_r3.scheduled_report) turns the numbers into checkable posts with ledger refs
  - ReAct agents read it as the read-only `oracle` tool

The top-voted buyer posts on 2026-11-04 were all of this kind (SKU price checks, category trends, purchase records
with ledger seqs), so the posts are built from facts only: no model writes numbers.
"""
import statistics
import time

from . import market


def _quantiles(xs):
    xs = sorted(xs)
    if len(xs) < 4:
        return xs[0], statistics.median(xs), xs[-1]
    q = statistics.quantiles(xs, n=4)
    return q[0], q[1], q[2]


def sku_stats(mem, sku, hours=6, now=None):
    """Distribution of settled prices for one SKU in the window, or None without settles."""
    now = now or time.time()
    with mem.db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT seq, amount, list_price, grade, shop FROM settles WHERE sku=? AND ts>? AND amount>0 "
            "AND list_price>0 ORDER BY seq DESC", (sku, now - hours * 3600))]
    if not rows:
        return None
    amounts = [r["amount"] for r in rows]
    q25, med, q75 = _quantiles(amounts)
    L = rows[0]["list_price"]
    by_grade = {}
    for r in rows:
        by_grade.setdefault(r["grade"], []).append(r["amount"])
    info = market.catalog().get(sku, {})
    return {"sku": sku, "name": info.get("name") or sku, "category": market.leaf(info.get("category")),
            "list_price": L, "n": len(rows), "low": min(amounts), "q25": int(q25), "median": int(med),
            "q75": int(q75), "high": max(amounts), "ratio": round(med / L, 2),
            "by_grade": {g: int(statistics.median(v)) for g, v in by_grade.items() if g},
            "shops": len({r["shop"] for r in rows}), "refs": [r["seq"] for r in rows[:5]], "hours": hours}


def category_stats(mem, category, hours=3, now=None):
    """Price/list ratios and the best seller of a category (full path or leaf) in the window, with the window
    before it for a trend. None without settles."""
    now = now or time.time()
    col = "category =" if ">" in (category or "") else "category LIKE"
    arg = category if ">" in (category or "") else f"%>{category}"
    def window(t0, t1):
        with mem.db() as c:
            return [dict(r) for r in c.execute(
                f"SELECT seq, sku, amount, list_price FROM settles WHERE {col} ? AND ts>? AND ts<=? AND list_price>0 "
                "AND amount>0", (arg, t0, t1))]
    cur = window(now - hours * 3600, now)
    if not cur:
        return None
    prev = window(now - 2 * hours * 3600, now - hours * 3600)
    ratios = [r["amount"] / r["list_price"] for r in cur]
    q25, med, q75 = _quantiles(ratios)
    counts = {}
    for r in cur:
        counts[r["sku"]] = counts.get(r["sku"], 0) + 1
    top = max(counts, key=counts.get)
    top_rows = [r for r in cur if r["sku"] == top]
    out = {"category": market.leaf(category) if ">" in category else category, "hours": hours, "n": len(cur),
           "ratio_q25": round(q25, 2), "ratio_median": round(med, 2), "ratio_q75": round(q75, 2),
           "top_sku": top, "top_name": market.catalog().get(top, {}).get("name") or top, "top_n": counts[top],
           "top_median": int(statistics.median(r["amount"] for r in top_rows)), "top_list": top_rows[0]["list_price"],
           "prev_n": len(prev), "refs": [r["seq"] for r in sorted(cur, key=lambda r: -r["seq"])[:5]]}
    if prev:
        out["prev_ratio_median"] = round(statistics.median(r["amount"] / r["list_price"] for r in prev), 2)
    return out


def hot_skus(mem, hours=3, min_n=4, now=None):
    """SKUs with the most settles in the window, most first: [(sku, n)]."""
    now = now or time.time()
    with mem.db() as c:
        return [(r["sku"], r["n"]) for r in c.execute(
            "SELECT sku, COUNT(*) n FROM settles WHERE ts>? AND sku IS NOT NULL GROUP BY sku HAVING n>=? "
            "ORDER BY n DESC LIMIT 30", (now - hours * 3600, min_n))]


def hot_categories(mem, hours=3, min_n=10, now=None):
    now = now or time.time()
    with mem.db() as c:
        return [(r["category"], r["n"]) for r in c.execute(
            "SELECT category, COUNT(*) n FROM settles WHERE ts>? AND category IS NOT NULL GROUP BY category "
            "HAVING n>=? ORDER BY n DESC LIMIT 20", (now - hours * 3600, min_n))]


def won(x):
    return f"{int(x):,}"


# ------------------------------------------------------------------------------------------- forum reports
def sku_report(mem, skip=(), now=None):
    """A price check for the busiest SKU not reported recently: (subject, text, refs) or None."""
    for sku, _ in hot_skus(mem, now=now):
        subject = f"sku:{sku}"
        if subject in skip:
            continue
        s = sku_stats(mem, sku, hours=3, now=now)
        if not s or s["n"] < 4:
            continue
        grades = ", ".join(f"{g} {won(v)}" for g, v in sorted(s["by_grade"].items()))
        text = (f"[시세] {s['name'][:60]} ({sku}): 최근 {s['hours']}시간 공개 원장 정산 {s['n']}건, "
                f"{s['shops']}개 가게. 체결가 최저 {won(s['low'])} / 중앙 {won(s['median'])} / 최고 {won(s['high'])}원, "
                f"정가 {won(s['list_price'])}원의 {s['ratio']:.0%}. 등급별 중앙값: {grades}. 근거: refs의 원장 seq.")
        return subject, text, s["refs"]
    return None


def category_report(mem, skip=(), now=None):
    """A trend for a busy category not reported recently."""
    for category, _ in hot_categories(mem, now=now):
        leaf = market.leaf(category)
        subject = f"cat:{leaf}"
        if subject in skip:
            continue
        s = category_stats(mem, category, hours=3, now=now)
        if not s:
            continue
        trend = ""
        if s.get("prev_ratio_median") is not None:
            trend = f" 그 전 3시간: {s['prev_n']}건, 중앙값 {s['prev_ratio_median']:.0%}."
        text = (f"[카테고리 시세] {leaf}: 최근 3시간 공개 원장 정산 {s['n']}건, 체결가/정가 중앙값 "
                f"{s['ratio_median']:.0%} (하위 25% {s['ratio_q25']:.0%}, 상위 25% {s['ratio_q75']:.0%}). "
                f"가장 많이 팔린 상품: {s['top_name'][:50]} ({s['top_sku']}) {s['top_n']}건, 체결가 중앙 "
                f"{won(s['top_median'])} / 정가 {won(s['top_list'])}원.{trend} 근거: refs의 원장 seq.")
        return subject, text, s["refs"]
    return None


def purchase_report(mem, skip=(), now=None, max_age_h=6):
    """Our own latest paid purchase not reported yet: a first-hand record other buyers do not have."""
    now = now or time.time()
    with mem.db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT p.*, h.name AS shop_label FROM purchases p LEFT JOIN shops h ON h.shop_url = p.shop_url "
            "WHERE p.paid=1 AND p.ledger IS NOT NULL AND p.list_price>0 AND p.price>0 AND p.ts>? "
            "ORDER BY p.ts DESC LIMIT 20", (now - max_age_h * 3600,))]
    for r in rows:
        subject = f"buy:{r['order_id']}"
        if subject in skip:
            continue
        off = 1 - r["price"] / r["list_price"]
        bits = [f"[구매 기록] {(r['name'] or r['sku'])[:60]} ({r['sku']})",
                f"{won(r['price'])}원, 정가 {won(r['list_price'])}원 대비 {off:.0%} 저렴"]
        if r.get("grade"):
            honest = {1: "정직", 0: "불일치"}.get(r.get("claim_honest"), "주장 없음")
            bits.append(f"등급 {r['grade']} (주장 {r.get('claim') or '-'}, {honest})")
        if r.get("warranty_hours"):
            bits.append(f"보증 {r['warranty_hours']:g}h")
        bits.append(f"판매: {r.get('shop_label') or r.get('shop_name') or r['shop_url']}")
        bits.append(f"ledger #{r['ledger']}")
        return subject, " | ".join(bits), [int(r["ledger"])]
    return None


REPORTERS = {"purchase": purchase_report, "sku": sku_report, "category": category_report}


def tool(mem):
    """Read-only ReAct tool."""
    from . import react

    def oracle(sku=None, category=None, hours=6):
        h = max(1, min(48, int(hours or 6)))
        if sku:
            return sku_stats(mem, str(sku), hours=h) or {"note": "no settles for this SKU in the window"}
        if category:
            return category_stats(mem, str(category), hours=h) or {"note": "no settles for this category"}
        return {"hot_skus": hot_skus(mem, hours=h)[:10], "hot_categories":
                [(market.leaf(k), n) for k, n in hot_categories(mem, hours=h)[:10]]}

    return react.Tool("oracle", "settled-price distribution from the public ledger for a SKU or a category "
                      "(low/q25/median/q75/high, by grade, citable refs); no args lists the busiest SKUs and categories",
                      oracle, {"sku": "str or null", "category": "str or null", "hours": "int <= 48"})
