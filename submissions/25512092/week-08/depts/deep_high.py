"""Deep stock for fast-selling high-price SKUs (person's decision 11/10 ~12:30). High price = unit cost > qc.HIGH_MIN
(100,000), sofas included. A SKU whose units of my shop, sold in the last WINDOW_H (24 h):
  - number >= MIN_SOLD (2),
  - median supply -> sale time <= MEDIAN_H (3 h),
  - profit / revenue >= MIN_MARGIN (10%)
may be held up to max(2, my sales of it in the last 12 h), at most MAX_HOLD (5); any other high-price SKU 1 as before.
Recomputed every restock run (depts/purchasing.make_plan and sofa_trial); high_cost_exclude, the first ask <= the
market average, the high-price guard and the floors are unchanged (they still decide whether a unit is bought).
Rules in deep_high.json; each run's list in logs/deep_high.json (latest) and logs/deep_high.jsonl (history).
"""
import json
import os
import sqlite3
import statistics
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "deep_high.json")
OUT = os.path.join(HERE, "logs", "deep_high.json")
HIST = os.path.join(HERE, "logs", "deep_high.jsonl")
ME = "choo-chu"
DEFAULT = {"enabled": False, "window_h": 24, "min_sold": 2, "median_h": 3, "min_margin": 0.10, "recent_h": 12,
           "min_hold": 2, "max_hold": 5}


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return dict(DEFAULT, **json.load(f))
    except (OSError, ValueError):
        return dict(DEFAULT)


def compute(now=None, r=None, db_path=None):
    """{sku: {"limit", "sold", "sold_12h", "median_h", "margin", "name"}} for the SKUs that qualify."""
    from depts.qc import HIGH_MIN
    now = now or time.time()
    r = rules() if r is None else r
    db = sqlite3.connect(db_path or os.path.join(HERE, "market.db"), timeout=30)
    try:
        rows = db.execute("SELECT s.sku, s.ts, s.amount, m.cost, m.supplied_at, COALESCE(m.name, s.name) FROM settles s "
                          "JOIN my_stock m ON m.unit=s.unit WHERE s.shop=? AND s.ts>=? AND s.ts<=? AND m.cost>?",
                          (ME, now - r["window_h"] * 3600, now, HIGH_MIN)).fetchall()
    finally:
        db.close()
    g = {}
    for sku, ts, amt, cost, sup, name in rows:
        g.setdefault(sku, []).append((ts, amt or 0, cost or 0, sup, name))
    out = {}
    for sku, xs in g.items():
        ages = [(ts - sup) / 3600 for ts, _, _, sup, _ in xs if sup]
        rev = sum(a for _, a, _, _, _ in xs)
        margin = (rev - sum(c for _, _, c, _, _ in xs)) / rev if rev else None
        if len(xs) < r["min_sold"] or not ages or margin is None:
            continue
        med = statistics.median(ages)
        if med > r["median_h"] or margin < r["min_margin"]:
            continue
        n12 = sum(1 for ts, *_ in xs if ts >= now - r["recent_h"] * 3600)
        cap = r["max_hold"]                       # 11/12 ~09:50 (person): median <= fast_median_h (1.5 h) -> fast_max_hold (7)
        if r.get("fast_max_hold") and med <= r.get("fast_median_h", 0):
            cap = r["fast_max_hold"]
        out[sku] = {"limit": min(cap, max(r["min_hold"], n12)), "sold": len(xs), "sold_12h": n12,
                    "median_h": round(med, 2), "margin": round(margin, 4), "name": xs[-1][4]}
    return out


def current(now=None, log=None, write=True):
    """The list for this run (empty while off); written to OUT / HIST."""
    r = rules()
    if not r.get("enabled"):
        return {}
    now = now or time.time()
    try:
        out = compute(now, r)
    except Exception as e:
        if log:
            log(f"[deep high] failed: {type(e).__name__}: {e}")
        return {}
    if write:
        try:
            tmp = OUT + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"ts": round(now), "skus": out}, f, ensure_ascii=False)
            os.replace(tmp, OUT)
            with open(HIST, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": round(now), "limits": {k: v["limit"] for k, v in out.items()}}) + "\n")
        except OSError:
            pass
    if log and out:
        log(f"[deep high] {len(out)} fast high-price SKUs held deeper: "
            + ", ".join(f"{(v['name'] or k)[:20]} {v['limit']}" for k, v in out.items()))
    return out


def outside_high_max(deep, r=None):
    """11/13 ~15:40 (person): with "outside_high_max" in deep_high.json, the deep SKUs (this run's list) are out of the
    high_max count and room - their held units do not count, their lines fill up to the deep limit whatever the room;
    the other high-price SKUs keep high_max. Returns the set of those SKUs (empty while the rule is off)."""
    r = rules() if r is None else r
    return set(deep) if r.get("enabled") and r.get("outside_high_max") else set()


def limit(sku, deep):
    """Units of `sku` a run may hold (high price): the deep limit, else 1 - or 2 for a SKU my shop sold in the last
    24 h while wide_trial's TWO is on (11/13 ~00:00, person)."""
    if sku in deep:
        return deep[sku]["limit"]
    from depts import wide_trial
    return wide_trial.two_limit(sku)


def skus_between(t0, t1):
    """The SKUs on any run's list in [t0, t1) (logs/deep_high.jsonl)."""
    out = set()
    try:
        with open(HIST, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if t0 <= e["ts"] < t1:
                    out |= set(e.get("limits") or {})
    except OSError:
        pass
    return out


def window_stats(skus, t0, t1, db_path=None, step_s=600):
    """For `skus` in [t0, t1): buys (units, cost), my sales (units, revenue, profit), median supply -> sale hours of
    those sold, and stock-out hours (summed over the SKUs, sampled every step_s: none of my units of it held)."""
    skus = sorted(skus)
    out = {"skus": len(skus), "buy_n": 0, "buy_cost": 0, "sell_n": 0, "rev": 0, "profit": 0, "median_h": None,
           "out_h": 0.0}
    if not skus:
        return out
    q = ",".join("?" * len(skus))
    db = sqlite3.connect(db_path or os.path.join(HERE, "market.db"), timeout=30)
    try:
        b = db.execute(f"SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM my_stock WHERE sku IN ({q}) AND supplied_at>=? AND "
                       f"supplied_at<?", (*skus, t0, t1)).fetchone()
        s = db.execute(f"SELECT s.amount, m.cost, s.ts - m.supplied_at FROM settles s JOIN my_stock m ON m.unit=s.unit "
                       f"WHERE s.shop=? AND s.sku IN ({q}) AND s.ts>=? AND s.ts<?", (ME, *skus, t0, t1)).fetchall()
        life = db.execute(f"SELECT m.sku, m.supplied_at, (SELECT MIN(s.ts) FROM settles s WHERE s.unit=m.unit AND "
                          f"s.shop=?) FROM my_stock m WHERE m.sku IN ({q}) AND m.supplied_at<?", (ME, *skus, t1)).fetchall()
    finally:
        db.close()
    ages = [a / 3600 for _, _, a in s if a is not None]
    out.update(buy_n=b[0], buy_cost=int(b[1]), sell_n=len(s), rev=sum(a or 0 for a, _, _ in s),
               profit=sum((a or 0) - (c or 0) for a, c, _ in s),
               median_h=round(statistics.median(ages), 2) if ages else None)
    by = {}
    for sku, sup, sold in life:
        by.setdefault(sku, []).append((sup or 0, sold if sold is not None else float("inf")))
    t, n = t0, 0
    while t < t1:
        n += sum(1 for k in skus if not any(a <= t < z for a, z in by.get(k, [])))
        t += step_s
    out["out_h"] = round(n * step_s / 3600, 1)
    return out


def line(t0, t1, db_path=None):
    """One summary line (08:00 / 20:00): the SKUs on the list now, and the window's numbers for every SKU listed in it."""
    try:
        with open(OUT, encoding="utf-8") as f:
            now_n = len(json.load(f).get("skus") or {})
    except (OSError, ValueError):
        now_n = 0
    w = window_stats(skus_between(t0, t1), t0, t1, db_path)
    med = "-" if w["median_h"] is None else f"{w['median_h']}시간"
    return (f"잘 팔리는 고가 깊게 쌓기: 지금 대상 {now_n}개 SKU (최근 12시간 대상이었던 {w['skus']}개): 매입 {w['buy_n']}개 "
            f"{w['buy_cost']:,}, 판매 {w['sell_n']}개 매출 {w['rev']:,} 이익 {w['profit']:,}, 입고→판매 중앙값 {med}, "
            f"품절 합계 {w['out_h']}시간")
