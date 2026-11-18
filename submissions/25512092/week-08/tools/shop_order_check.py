"""shop_order (depts/shop_order.py, person's decision 11/09 ~21:30): the guard and the 6 h check.
  guard(now)   from GUARD_AFTER_H after the switch-on, the last GUARD_H: close rate (orders, logs/soft_neg.jsonl) under
               the base - RATE_DROP (5 %p) or paid deals per hour under base x (1 - DPH_DROP) (20%), with at least
               MIN_ORDERS orders, only while soft_neg is on (from its last switch-on; off -> no judgement, 11/11) -> the reason (tools/saved_guard_watch.py switches only stock_first / rate_order off)
  report(t_on) the 6 h after vs the 6 h before: close rate, the share of orders that failed on no stock, deals per
               hour, saved change, each shop's share of our tries (in the 08:00 summary, tools/morning_summary.py)
    python tools/shop_order_check.py [--dry]
"""
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
from depts import shop_order, soft_neg  # noqa: E402

GUARD_AFTER_H, GUARD_H, RATE_DROP, DPH_DROP, MIN_ORDERS = 2, 2, 0.05, 0.20, 100
REPORT_H = 6


def orders(t0, t1, path=None):
    out = []
    try:
        with open(path or soft_neg.LOG, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if t0 <= e.get("ts", 0) < t1:
                    out.append(e)
    except OSError:
        pass
    return out


def stats(t0, t1, path=None):
    R = orders(t0, t1, path)
    h = max(1e-6, (t1 - t0) / 3600)
    return {"n": len(R), "rate": sum(1 for r in R if r.get("deal")) / len(R) if R else None,
            "dph": sum(1 for r in R if r.get("paid_ok")) / h}


def base(now, path=None):
    """Taken at the switch-on: the 6 h before."""
    return stats(now - 6 * 3600, now, path)


def soft_since(sn=None, now=None):
    """11/11 ~10:45 (person): the start of soft_neg's current on stretch (its last switch-on: guard_on by the saved
    guard, else since), or None while soft_neg is off. The guard uses only orders from then on."""
    sn = soft_neg.rules() if sn is None else sn
    if not soft_neg.on(now=now, r=sn):
        return None
    return max(sn.get("guard_on") or 0, sn.get("since") or 0)


def guard(now=None, r=None, path=None, sn=None):
    """The reason to switch both off, or None. 11/11 ~10:45 (person): only orders while soft_neg is on (from its last
    switch-on), at least MIN_ORDERS of them in the window; soft_neg off -> no judgement."""
    now = now or time.time()
    r = shop_order.rules() if r is None else r
    if not shop_order.on(r, now) or now - (r.get("since") or now) < GUARD_AFTER_H * 3600 or not r.get("base"):
        return None
    t_sn = soft_since(sn, now)
    if t_sn is None:
        return None
    b, s = r["base"], stats(max(now - GUARD_H * 3600, t_sn), now, path)
    if s["n"] < MIN_ORDERS or s["rate"] is None or b.get("rate") is None:
        return None
    if s["rate"] < b["rate"] - RATE_DROP:
        return f"성사율 {s['rate']:.1%} < 적용 전 {b['rate']:.1%} - {RATE_DROP * 100:.0f}%p (최근 {GUARD_H}시간 중 soft_neg 켜진 구간 {s['n']}건)"
    if s["dph"] < b["dph"] * (1 - DPH_DROP):
        return f"시간당 deals {s['dph']:.1f} < 적용 전 {b['dph']:.1f} x {1 - DPH_DROP:.0%} (최근 {GUARD_H}시간 중 soft_neg 켜진 구간 {s['n']}건)"
    return None


def _mine(t):
    """My buyer row at t via tools/board_read.py (the common board reading); None = 읽기 실패."""
    import board_read
    return board_read.mine(t)[1]


def shop_share(t0, t1):
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        rows = db.execute("SELECT shop, COUNT(*) FROM buyer_deals WHERE ts>=? AND ts<? AND outcome IN ('deal','fail','error') "
                          "GROUP BY shop", (t0, t1)).fetchall()
    finally:
        db.close()
    n = sum(c for _, c in rows) or 1
    return {s: c / n for s, c in rows}


def report(t_on, t1=None):
    import order_speed2 as o
    t1 = min(t1 or time.time(), t_on + REPORT_H * 3600)
    t0 = t_on - (t1 - t_on)
    a, b = stats(t_on, t1), stats(t0, t_on)
    pf = lambda x: "-" if x is None else f"{x:.1%}"
    ns = []
    for x, y in ((t_on, t1), (t0, t_on)):
        R = o.rows(x, y)
        ns.append((sum(1 for r in R if r["why"] == "재고 없음") / len(R)) if R else None)
    m1, m_on, m0 = _mine(t1), _mine(t_on), _mine(t0)
    h = (t1 - t_on) / 3600
    from board_read import FAIL
    dph = lambda x, y: f"{(x['deals'] - y['deals']) / h:.1f}" if x and y else FAIL
    sa, sb = shop_share(t_on, t1), shop_share(t0, t_on)
    top = sorted(set(sa) | set(sb), key=lambda s: -(sa.get(s, 0) + sb.get(s, 0)))[:8]
    r = shop_order.rules()
    st = (f"꺼짐 {datetime.fromtimestamp(r['stopped']):%H:%M} ({r.get('stop_reason')})" if r.get("stopped") else "켜짐")
    L = [f"가게 순서(재고 없음 가게 뒤로 + 우리 성사율 순) {h:.1f}시간 점검: {datetime.fromtimestamp(t_on):%m-%d %H:%M} 적용, "
         f"후 vs 전 같은 길이 ({st})",
         f"  성사율 {pf(a['rate'])} ({a['n']}건) vs {pf(b['rate'])} ({b['n']}건), 재고 없음으로 실패한 주문 비율 {pf(ns[0])} vs {pf(ns[1])}",
         f"  시간당 deals {dph(m1, m_on)} vs {dph(m_on, m0)} (결제 기준 {a['dph']:.1f} vs {b['dph']:.1f}), saved "
         f"{(m_on or {}).get('saved', FAIL)} -> {(m1 or {}).get('saved', FAIL)}",
         "  가게별 시도 비중 (후 vs 전): " + ", ".join(f"{s} {sa.get(s, 0):.0%} vs {sb.get(s, 0):.0%}" for s in top),
         "  (21:14 공격적 변경·D·high_accept 1.10이 비교 구간에 섞임)"]
    return "\n".join(L)


if __name__ == "__main__":
    r = shop_order.rules()
    t = r.get("since") or time.time() - 3600
    print(report(t))
    print("guard:", guard())
