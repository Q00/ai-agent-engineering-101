"""STOCK_CAP 15M -> 20M (person's decision 11/07 ~20:40; CASH_RESERVE 1.0M unchanged) and its 3 h report.
  1. now: a proposal row (approved by the person) and analyst_apply.apply_limit (b0 before/after, STOCK_EXIT 88%,
     commit + push; the analyst's own 3 h check follows), then cap_brake.json enabled with since = now
  2. 3 h later: one Telegram, 3 h after vs 3 h before: revenue and sales, turnover (median supply -> sale, units
     supplied and how many of them sold), storage, buying blocked or cut by STOCK_CAP (cap_runs), cash and stock,
     the brake's latest numbers
    python tools/cap20_rollout.py            (log in logs/cap20_rollout.log)
    python tools/cap20_rollout.py --report-dry   (the report for the window so far, printed)
"""
import json
import os
import statistics
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
import alerts  # noqa: E402
import decisions  # noqa: E402

LOG = os.path.join(HERE, "logs", "cap20_rollout.log")
WHY = "person's decision 11/07 ~20:40: STOCK_CAP 15M -> 20M with the cap brake"
NEW = 20_000_000
ME = "choo-chu"
H = 3


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def window(db, t0, t1):
    s = db.execute("SELECT s.ts, s.amount, m.supplied_at, m.cost FROM settles s LEFT JOIN my_stock m ON m.unit=s.unit "
                   "WHERE s.shop=? AND s.ts>=? AND s.ts<?", (ME, t0, t1)).fetchall()
    ages = [ts - sup for ts, _, sup, _ in s if sup]
    sup = db.execute("SELECT unit FROM my_stock WHERE supplied_at>=? AND supplied_at<?", (t0, t1)).fetchall()
    sold = db.execute("SELECT COUNT(DISTINCT m.unit) FROM my_stock m JOIN settles s ON s.unit=m.unit AND s.shop=? "
                      "WHERE m.supplied_at>=? AND m.supplied_at<? AND s.ts<?", (ME, t0, t1, t1)).fetchone()[0]
    cash = db.execute("SELECT ts, balance, stock_cost, storage_per_day FROM cash WHERE ts>=? AND ts<? ORDER BY ts",
                      (t0, t1)).fetchall()
    try:
        cap = db.execute("SELECT shop_case, budget_free - budget FROM cap_runs WHERE ts>=? AND ts<?", (t0, t1)).fetchall()
    except sqlite3.Error:
        cap = []
    return {"sales": len(s), "revenue": sum(a for _, a, _, _ in s),
            "margin": sum(a - c for _, a, _, c in s if c), "median_h": round(statistics.median(ages) / 3600, 2) if ages else None,
            "supplied": len(sup), "supplied_sold": sold,
            "storage": round(sum(r[3] or 0 for r in cash) / len(cash) * (cash[-1][0] - cash[0][0]) / 86400) if cash else None,
            "cash": (cash[0][1], cash[-1][1]) if cash else None, "stock": (cash[0][2], cash[-1][2]) if cash else None,
            "cap_runs": len(cap), "cap_blocked": sum(1 for c in cap if c[0] == "overstock"),
            "cap_cut": sum(c[1] or 0 for c in cap)}


def report(t, now=None):
    t1 = min(t + H * 3600, now or time.time())
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        a, b = window(db, t, t1), window(db, t - (t1 - t), t)
    finally:
        db.close()
    try:
        st = json.load(open(os.path.join(HERE, "logs", "cap_brake_state.json"), encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    br = json.load(open(os.path.join(HERE, "cap_brake.json"), encoding="utf-8"))
    f = lambda p: f"{p[0]:,} -> {p[1]:,}" if p else "-"
    return "\n".join([
        f"STOCK_CAP 20M {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}), "
        "후 vs 전 같은 길이",
        f"- 매출 {a['revenue']:,} vs {b['revenue']:,} / 판매 {a['sales']} vs {b['sales']} / 마진 {a['margin']:,} vs {b['margin']:,}",
        f"- 회전: 입고→판매 중앙값 {a['median_h']} vs {b['median_h']}시간, 입고 {a['supplied']} vs {b['supplied']}개 중 "
        f"창 안에 팔린 것 {a['supplied_sold']} vs {b['supplied_sold']}",
        f"- 보관비 약 {a['storage']:,} vs {(b['storage'] or 0):,}",
        f"- 막힌 매입(STOCK_CAP): run {a['cap_runs']} vs {b['cap_runs']} (완전 막힘 {a['cap_blocked']} vs {b['cap_blocked']}), "
        f"깎인 예산 {a['cap_cut']:,} vs {b['cap_cut']:,}",
        f"- 현금 {f(a['cash'])}, 재고 원가 {f(a['stock'])}",
        f"- 브레이크: {'걸림 ' + datetime.fromtimestamp(br['fired']).strftime('%H:%M') + ' ' + '; '.join(br.get('fired_why', [])) if br.get('fired') else '안 걸림'}"
        f" (최근 값: 중앙값 {st.get('median_h')}시간, 상향 후 입고분 6시간+ {st.get('old_cost', 0):,}, 현금 {st.get('cash', 0):,})"])


def main():
    if "--report-dry" in sys.argv:
        br = json.load(open(os.path.join(HERE, "cap_brake.json"), encoding="utf-8"))
        print(report(br.get("since") or time.time() - 3600))
        return
    from depts import analyst, analyst_apply
    now = time.time()
    d = analyst.db()
    try:
        analyst_apply._cols(d)
        cur = d.execute("INSERT INTO proposals (ts,kind,key,value,reason,evidence,status,expires,decided_ts,result,asked,"
                        "checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)", (now, "limit", "limits.STOCK_CAP", json.dumps(NEW),
                                                                      WHY, "person", "approved", now, now, "applying", 1))
        pid = cur.lastrowid
        d.commit()
    finally:
        d.close()
    try:
        res = analyst_apply.apply_limit("limits.STOCK_CAP", NEW, pid, why="person's decision 11/07 ~20:40")
    except Exception as e:
        d = analyst.db()
        d.execute("UPDATE proposals SET status='failed', result=? WHERE id=?", (str(e)[:300], pid))
        d.commit()
        d.close()
        alerts.send("urgent", "cap20_fail", f"STOCK_CAP 20M 적용 실패 ({type(e).__name__}: {str(e)[:150]}). 15M 그대로", log=log)
        log(f"failed: {e}")
        return
    analyst_apply._store(pid, res)
    d = analyst.db()
    d.execute("UPDATE proposals SET result=? WHERE id=?", (res["text"], pid))
    d.commit()
    d.close()
    t = time.time()
    br = json.load(open(os.path.join(HERE, "cap_brake.json"), encoding="utf-8"))
    br.update(enabled=True, since=round(t), pid=pid)
    json.dump(br, open(os.path.join(HERE, "cap_brake.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    decisions.record("analyst", "approved_by_person", "limits.STOCK_CAP", NEW, f"#{pid}: {WHY} | {res['text']}")
    log(f"applied #{pid}: {res['text']}; brake enabled since {t:.0f}")
    alerts.send("info", "cap20_on", f"STOCK_CAP 15M -> 20M 적용 {datetime.now():%H:%M} (#{pid}, {res['text'][:140]}). "
                "CASH_RESERVE 1.0M 그대로. 자동 브레이크 켬: 판매 중앙값 3시간 초과(최근 6시간) / 상향 후 입고분 중 6시간+ "
                "안 팔린 원가 3M 초과 / 현금 2M 미만 -> 15M + 긴급 알림. 3시간 점검 보고 예정", log=log)
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    text = report(t)
    alerts.send("daily", f"cap20_report:{int(t)}", text, log=log)
    log("report sent")


if __name__ == "__main__":
    main()
