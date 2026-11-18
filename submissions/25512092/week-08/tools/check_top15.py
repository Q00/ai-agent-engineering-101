"""3 h check of the 11/11 ~20:35 changes (person's decision): HIGH_MAX 1.5M -> 3.0M (pinned to 11/13 12:00), over 1.5M
at most 10 units held and 2 a SKU, their warranty only A grade in a category with market break <= 6%. One Telegram
(daily) H hours after the start, after vs the same length before:
  over-1.5M units: bought (units, cost), sold of them (units, revenue, profit, margin), supply -> sale median hours,
      warranty sales, refunds on my over-1.5M sales; held now (units, the most of one SKU)
  cash and stock at cost at the start and now (market.db cash)
  Book and revenue per hour (tools/board_read.py: the live board / snapshot, "읽기 실패" and again with the next one)
    python tools/check_top15.py [--start [--t0=EPOCH] | --dry]
"""
import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
STATE = os.path.join(HERE, "logs", "check_top15.json")
H = 3
MIN = 1_500_000
ME = "choo-chu"


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def start(t0=None):
    s = {"t0": round(t0 or time.time())}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f)
    return s


def top(t0, t1, now, db_path=None):
    """Over-1.5M units supplied in [t0, t1) and what happened to them by now."""
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=60)
    try:
        rows = d.execute("SELECT m.unit, m.sku, m.cost, m.supplied_at, s.amount, s.ts, s.buyer, s.warranty_hours FROM "
                         "my_stock m LEFT JOIN settles s ON s.unit=m.unit AND s.shop=? WHERE m.cost>? AND "
                         "m.supplied_at>=? AND m.supplied_at<?", (ME, MIN, t0, t1)).fetchall()
        sold = [r for r in rows if r[4] is not None and r[5] <= now]
        keys = {(r[1], r[6]): r[5] for r in sold}
        ref = [0, 0]
        for ts, sku, buyer, amt in d.execute("SELECT ts, sku, buyer, refunded FROM broken WHERE shop=? AND refunded>0 "
                                             "AND ts>=? AND ts<?", (ME, t0, now)):
            if (sku, buyer) in keys and keys[(sku, buyer)] <= ts:
                ref[0] += 1
                ref[1] += amt
    finally:
        d.close()
    rev, cost_s = sum(r[4] for r in sold), sum(r[2] for r in sold)
    return {"n": len(rows), "cost": int(sum(r[2] for r in rows)), "sold": len(sold), "rev": int(rev),
            "profit": int(rev - cost_s), "margin": (rev - cost_s) / rev if rev else None,
            "med_h": statistics.median([(r[5] - r[3]) / 3600 for r in sold]) if sold else None,
            "warranty": sum(1 for r in sold if r[7]), "refunds": ref[0], "refund_amt": int(ref[1])}


def last24(t1, db_path=None, hours=24):
    """11/12 ~09:30 (person, for the HIGH_MAX call on 11/13): over-1.5M units bought in [t1 - 24 h, t1) (units, cost)
    and sold in it whenever bought (units, revenue, cost, profit, margin), refunds on my over-1.5M sales in it."""
    t0 = t1 - hours * 3600
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=60)
    try:
        b = d.execute("SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM my_stock WHERE cost>? AND supplied_at>=? AND "
                      "supplied_at<?", (MIN, t0, t1)).fetchone()
        s = d.execute("SELECT COUNT(*), COALESCE(SUM(s.amount), 0), COALESCE(SUM(m.cost), 0) FROM settles s JOIN my_stock m "
                      "ON m.unit=s.unit WHERE s.shop=? AND m.cost>? AND s.ts>=? AND s.ts<?", (ME, MIN, t0, t1)).fetchone()
        r = d.execute("SELECT COUNT(*), COALESCE(SUM(b.refunded), 0) FROM broken b WHERE b.shop=? AND b.refunded>0 AND "
                      "b.ts>=? AND b.ts<? AND EXISTS (SELECT 1 FROM settles s JOIN my_stock m ON m.unit=s.unit WHERE "
                      "s.shop=b.shop AND s.sku=b.sku AND s.buyer=b.buyer AND s.ts<=b.ts AND m.cost>?)",
                      (ME, t0, t1, MIN)).fetchone()
    finally:
        d.close()
    rev, cost = int(s[1]), int(s[2])
    return {"bought": b[0], "bought_cost": int(b[1]), "sold": s[0], "rev": rev, "sold_cost": cost, "profit": rev - cost,
            "margin": (rev - cost) / rev if rev else None, "refunds": r[0], "refund_amt": int(r[1])}


def last24_line(t1, db_path=None):
    x = last24(t1, db_path)
    n_held, most = held_now()
    m = "-" if x["margin"] is None else f"{x['margin']:.1%}"
    return (f"150만 초과 고가 24시간 누적 ({datetime.fromtimestamp(t1 - 86400):%m-%d %H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}): "
            f"매입 {x['bought']}개 원가 {x['bought_cost']:,}; 판매 {x['sold']}개 매출 {x['rev']:,} 원가 {x['sold_cost']:,} 이익 "
            f"{x['profit']:,} (이익률 {m}); 환불 {x['refunds']}건 {x['refund_amt']:,}; 지금 보유 "
            f"{'-' if n_held is None else n_held}개 (SKU당 최다 {'-' if most is None else most}) | HIGH_MAX 고정 11-13 12:00까지")


def top_high_rules():
    try:
        from depts import top_high
        return top_high.rules()
    except Exception:
        return {}


def held_now(state=None):
    try:
        with open(os.path.join(state or os.path.join(HERE, ".market"), "inventory.json"), encoding="utf-8") as f:
            units = json.load(f).get("units") or []
    except (OSError, ValueError):
        return None, None
    per = {}
    for u in units:
        if u.get("grade") != "expired" and (u.get("cost") or 0) > MIN:
            per[u["sku"]] = per.get(u["sku"], 0) + 1
    return sum(per.values()), max(per.values()) if per else 0


def cash_at(t, db_path=None):
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=60)
    try:
        return d.execute("SELECT ts, balance, stock_cost FROM cash ORDER BY ABS(ts - ?) LIMIT 1", (t,)).fetchone()
    finally:
        d.close()


def f0(v):
    return "-" if v is None else f"{v:,.0f}"


def report(s, now=None, cur=None, board=None, db_path=None, clock=time.time, state=None):
    import board_read as BR
    now = now or time.time()
    t0 = s["t0"]
    h = max((now - t0) / 3600, 0.01)
    a, p = top(t0, now, now, db_path), top(t0 - h * 3600, t0, now, db_path)
    pc = lambda v: "-" if v is None else f"{v:.1%}"
    n_held, most = held_now(state)
    c0, c1 = cash_at(t0, db_path), cash_at(now, db_path)
    L = [f"HIGH_MAX 300만·150만 초과 고가 {h:.1f}시간 점검: {datetime.fromtimestamp(t0):%m-%d %H:%M} 적용, 후 vs 직전 같은 길이"]
    L.append(f"- 150만 초과 매입 {a['n']}개 원가 {a['cost']:,} vs {p['n']}개 {p['cost']:,}; 그중 판매 {a['sold']}개 매출 "
             f"{a['rev']:,} 이익 {a['profit']:,} (이익률 {pc(a['margin'])}) vs {p['sold']}개 {p['rev']:,} {p['profit']:,} "
             f"({pc(p['margin'])})")
    L.append(f"- 회전(입고→판매 중앙값) {f0(a['med_h']) if a['med_h'] is None else format(a['med_h'], '.2f')}시간 vs "
             f"{'-' if p['med_h'] is None else format(p['med_h'], '.2f')}시간; 보증 붙은 판매 {a['warranty']} vs {p['warranty']}개; "
             f"환불 {a['refunds']}건 {a['refund_amt']:,} vs {p['refunds']}건 {p['refund_amt']:,}")
    L.append(f"- 150만 초과 지금 보유 {n_held if n_held is not None else '-'}개 (한도 {top_high_rules().get('max_units', 10)}), SKU당 최다 {most if most is not None else '-'}개 (한도 {top_high_rules().get('max_per_sku', 2)})")
    if c0 and c1:
        L.append(f"- 현금 {c0[1]:,} -> {c1[1]:,} ({c1[1] - c0[1]:+,}), 재고 원가 {c0[2]:,} -> {c1[2]:,} ({c1[2] - c0[2]:+,})")
    if cur is None:
        cur = BR.now(board, clock=clock, db_path=db_path)
    pp, q0 = BR.at(t0 - h * 3600, db_path=db_path), BR.at(t0, db_path=db_path)
    bad = BR.failed(**{"직전 시작": pp, "적용 때": q0, "지금": cur})
    ph = lambda x, y, k: BR.show(BR.per_h(x, y, "s", k), ",.0f")
    L.append(f"- 가게 시간당 Book {ph(q0, cur, 'book')} vs {ph(pp, q0, 'book')}, 시간당 매출 {ph(q0, cur, 'revenue')} vs "
             f"{ph(pp, q0, 'revenue')}, 순위 {BR.show(q0.get('s', 'rank'))} -> {BR.show(cur.get('s', 'rank'))}")
    if bad:
        L.append(BR.fail_line(bad))
    return "\n".join(L), bad


def send_with_retry(s, send, now=None, sleep=time.sleep, db_path=None, board=None, clock=time.time, state=None):
    import board_read as BR
    now = now or time.time()
    return BR.send_with_retry(
        lambda cur: report(s, now=now if cur is None else cur.ts, cur=cur, board=board, db_path=db_path, clock=clock,
                           state=state),
        send, f"check_top15:{s['t0']}", sleep=sleep, db_path=db_path, now_ts=now)


def main():
    if "--start" in sys.argv:
        t0 = next((float(x.split("=", 1)[1]) for x in sys.argv if x.startswith("--t0=")), None)
        print(start(t0))
        return
    s = load() or start()
    if "--dry" in sys.argv:
        print(report(s)[0])
        return
    while time.time() < s["t0"] + H * 3600:
        time.sleep(min(60, max(1, s["t0"] + H * 3600 - time.time())))
    import alerts
    try:
        send_with_retry(s, alerts.send)
    except Exception as e:
        alerts.send("urgent", "check_top15_fail", f"HIGH_MAX 300만 3시간 점검 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
