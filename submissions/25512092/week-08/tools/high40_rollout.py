"""Analyst proposal #30 approved by the person (purchasing.high_share 0.6, policy.json) and high_max 30 -> 40
(policy.json + QC policy table; POLICY_RANGES, CEO split rule and QC MAX_CAP 40 in the code) (person's decision
11/09 ~11:00). The high-price buy guard, the first ask <= market average and the floor max(cost x 1.05, median x 0.80)
are unchanged. Purchasing reads them every run (no restart). #26 / #27 / #28 are marked rejected by the person.
3 h later one Telegram, 3 h after vs 3 h before: high-price negotiations, receipts, sales, revenue and profit, stock
at cost and units, all revenue, cheap buying, high_price_above_avg skips, high-price refunds.
    python tools/high40_rollout.py [--report-dry]          (log in logs/high40_rollout.log)
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import alerts  # noqa: E402
import decisions  # noqa: E402

LOG = os.path.join(HERE, "logs", "high40_rollout.log")
WHY = "person's decision 11/09 ~11:00: #30 high_share 0.6 approved, high_max 30 -> 40"
H = 3


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def refunds(db, t0, t1):
    """High-price refunds in [t0, t1): (count, amount), from the refunds table when there is one."""
    from depts.qc import HIGH_MIN
    try:
        cols = [r[1] for r in db.execute("PRAGMA table_info(refunds)")]
    except sqlite3.Error:
        cols = []
    if not cols:
        return None
    amt = "amount" if "amount" in cols else "price" if "price" in cols else None
    r = db.execute(f"SELECT COUNT(*), COALESCE(SUM(r.{amt}),0) FROM refunds r JOIN my_stock m ON m.unit=r.unit "
                   "WHERE r.ts>=? AND r.ts<? AND m.cost>?", (t0, t1, HIGH_MIN)).fetchone() if amt and "unit" in cols else None
    return r


def report(t, now=None):
    from high_revert import window
    t1 = min(t + H * 3600, now or time.time())
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        a, b = window(db, t, t1), window(db, t - (t1 - t), t)
        try:
            ra, rb = refunds(db, t, t1), refunds(db, t - (t1 - t), t)
        except sqlite3.Error:
            ra = rb = None
    finally:
        db.close()
    pc = lambda x: f"{x['hi_profit'] / (x['hi_rev'] - x['hi_profit']):+.1%}" if x["hi_rev"] - x["hi_profit"] else "-"
    lines = [
        f"고가 비중 0.6 + high_max 40 {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ "
        f"{datetime.fromtimestamp(t1):%H:%M}, 후 vs 전)",
        f"- 고가 협상 {a['neg']} vs {b['neg']}, 영수증 {a['rec']} vs {b['rec']}",
        f"- 고가 판매 {a['hi_n']}개 매출 {a['hi_rev']:,} 이익 {a['hi_profit']:,} ({pc(a)}) vs {b['hi_n']}개 {b['hi_rev']:,} "
        f"{b['hi_profit']:,} ({pc(b)})",
        f"- 고가 재고(끝 시점) 원가 {a['held_cost']:,} {a['held_n']}개 vs {b['held_cost']:,} {b['held_n']}개",
        f"- 전체 매출 {a['rev']:,} ({a['sales']}건) vs {b['rev']:,} ({b['sales']}건)",
        f"- 싼 물건 매입 {a['cheap_n']}개 원가 {a['cheap_cost']:,} vs {b['cheap_n']}개 {b['cheap_cost']:,}; "
        f"high_price_above_avg 스킵 {a['skips']} vs {b['skips']}"]
    if ra is not None and rb is not None:
        lines.append(f"- 고가 환불 {ra[0]}건 {ra[1]:,} vs {rb[0]}건 {rb[1]:,}")
    return "\n".join(lines)


def cut_report():
    """11/09 ~12:05 (person): the seller restarted mid-window; the 3 h report covers the start .. the restart only.
    python tools/high40_rollout.py --report-at T0 --cut T1 (sent at T0 + 3 h)."""
    import alerts
    t0 = float(sys.argv[sys.argv.index("--report-at") + 1])
    cut = float(sys.argv[sys.argv.index("--cut") + 1])
    while time.time() < t0 + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"high40_report:{int(t0)}", f"[재시작으로 구간이 끊김] {datetime.fromtimestamp(cut):%H:%M} seller 재시작"
                "(잠금 해제·C등급 소파 보증·B등급 보증)과 STOCK_CAP 30M 적용으로, 이 점검은 재시작 시각까지만 비교\n"
                + report(t0, now=cut), log=log)
    log("report sent")


def main():
    if "--report-at" in sys.argv:
        cut_report()
        return
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    import policy
    from depts import analyst, analyst_apply
    from market_db import connect
    now = time.time()
    d = analyst.db()
    d.row_factory = sqlite3.Row
    try:
        p30 = dict(d.execute("SELECT * FROM proposals WHERE id=30").fetchone())
    finally:
        d.close()
    res = analyst_apply.apply_policy("purchasing.high_share", json.loads(p30["value"]), 30)
    d = analyst.db()
    try:
        analyst_apply._cols(d)
        d.execute("UPDATE proposals SET status='approved', decided_ts=?, result=?, undo=?, checked=0 WHERE id=30",
                  (now, "person 11/09 ~11:00 (chat): " + res["text"], json.dumps(res["undo"])))
        for pid in (26, 27, 28):
            d.execute("UPDATE proposals SET status='rejected', decided_ts=?, result=? WHERE id=?",
                      (now, "person 11/09 ~11:00: not applied", pid))
        d.commit()
    finally:
        d.close()
    decisions.record("analyst", "approved", "purchasing.high_share", 0.6, f"#30: person 11/09 ~11:00 (chat) | {res['text']}")
    for pid in (26, 27, 28):
        decisions.record("analyst", "rejected", f"#{pid}", None, "person 11/09 ~11:00: not applied")
    old_hm = policy.get("purchasing", "high_max")
    v = policy.write({"purchasing": {"high_max": 40}}, WHY + f": high_max {old_hm} -> 40", by="person")
    db = connect()
    try:
        db.execute("INSERT INTO policy VALUES (?,?,?,?)", (time.time(), 0.6, 40, "11/09 person's decision ~11:00: #30 "
                                                                                "high_share 0.6, high-price units 30 -> 40"))
        db.commit()
    finally:
        db.close()
    decisions.record("purchasing", "high_max", "purchasing.high_max", [old_hm, 40], WHY + f"; policy v{v.get('version')}")
    t = time.time()
    log(f"#30 {res['text']}; high_max {old_hm} -> 40 (policy v{v.get('version')}, QC table); #26-#28 rejected")
    alerts.send("info", "high40_on", f"고가 확대 적용 {datetime.now():%H:%M}: 분석관 #30 승인 (high_share {res['text'][:80]}), "
                f"high_max {old_hm} -> 40 (policy v{v.get('version')}; CEO·QC 자동 규칙 상한 40). 고가 매입 가드, 첫 호가 평균 "
                "이하, 하한 max(원가 x 1.05, 중앙값 x 0.8) 그대로. #26·#27·#28 적용 안 함. 3시간 점검 예정", log=log)
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"high40_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
