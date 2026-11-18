"""Undo of the average-order-value change + high-price asks against the market average (person's decision 11/08 ~17:00):
  high_max 60 -> 30 (policy.json + QC table; POLICY_RANGES, CEO split rule and QC MAX_CAP back to 30 in the code),
  HIGH_MAX 1.5M -> 1.0M (the analyst path: proposal row, b0 before / after, commit + push),
  depts/high_ask.py (high_ask.json): first ask of a high-price unit <= market average (floor unchanged), G3e0d6c67 /
  G53b150b5 at 98% of the average (never under cost), no high-price buy whose expected first ask is above the average
  (decision high_price_above_avg); the seller hooks load at the restart done here.
3 h later one Telegram, 3 h after vs 3 h before: high-price negotiations, receipts, sales, high-price stock at cost,
all revenue, cheap (<= HIGH_MIN) buying, high_price_above_avg skips, adjusted first asks.
    python tools/high_revert.py [--report-dry]
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

LOG = os.path.join(HERE, "logs", "high_revert.log")
WHY = "person's decision 11/08 ~17:00: revenue fell with the high-price expansion; back to high_max 30 / HIGH_MAX 1.0M"
ME = "choo-chu"
H = 3


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def window(db, t0, t1):
    from depts.qc import HIGH_MIN
    neg = db.execute("SELECT COUNT(*), SUM(outcome='receipt') FROM shop_deals WHERE ts>=? AND ts<? AND cost>?",
                     (t0, t1, HIGH_MIN)).fetchone()
    s = db.execute("SELECT s.amount, m.cost FROM settles s LEFT JOIN my_stock m ON m.unit=s.unit WHERE s.shop=? AND "
                   "s.ts>=? AND s.ts<?", (ME, t0, t1)).fetchall()
    hi = [(a, c) for a, c in s if (c or 0) > HIGH_MIN]
    held = db.execute("SELECT COALESCE(SUM(m.cost),0), COUNT(*) FROM my_stock m WHERE m.cost>? AND m.supplied_at<? AND NOT "
                      "EXISTS (SELECT 1 FROM settles x WHERE x.unit=m.unit AND x.shop=? AND x.ts>=m.supplied_at AND x.ts<?)",
                      (HIGH_MIN, t1, ME, t1)).fetchone()
    cheap = db.execute("SELECT COUNT(*), COALESCE(SUM(cost),0) FROM my_stock WHERE cost<=? AND supplied_at>=? AND supplied_at<?",
                       (HIGH_MIN, t0, t1)).fetchone()
    skips = 0
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if "high_price_above_avg" in ln and t0 <= json.loads(ln)["ts"] < t1:
                skips += 1
    return {"neg": neg[0], "rec": neg[1] or 0, "hi_n": len(hi), "hi_rev": sum(a for a, _ in hi),
            "hi_profit": sum(a - c for a, c in hi), "held_cost": held[0], "held_n": held[1], "sales": len(s),
            "rev": sum(a for a, _ in s), "cheap_n": cheap[0], "cheap_cost": cheap[1], "skips": skips}


def adjusted(t0, t1):
    """'[high ask]' lines of the seller (newest kit console log) in [t0, t1): (all, clearance)."""
    import glob
    n = c = 0
    f = sorted(glob.glob(os.path.join(HERE, "logs", "kit-console-*.txt")), key=os.path.getmtime)[-1]
    day0 = time.mktime(time.localtime(t0)[:3] + (0, 0, 0, 0, 0, -1))
    with open(f, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if "[high ask]" not in ln:
                continue
            try:
                h, m, s = map(int, ln[:8].split(":"))
            except ValueError:
                continue
            if t0 <= day0 + h * 3600 + m * 60 + s < t1:
                n += 1
                c += "clear" in ln
    return n, c


def report(t, now=None):
    t1 = min(t + H * 3600, now or time.time())
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        a, b = window(db, t, t1), window(db, t - (t1 - t), t)
        g = db.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM settles WHERE shop=? AND sku='G3e0d6c67' AND ts>=? AND ts<?",
                       (ME, t, t1)).fetchone()
    finally:
        db.close()
    n, c = adjusted(t, t1)
    return "\n".join([
        f"고가 되돌리기 + 첫 호가 시장 평균 이하 {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ "
        f"{datetime.fromtimestamp(t1):%H:%M}, 후 vs 전)",
        f"- 고가 협상 {a['neg']} vs {b['neg']}, 영수증 {a['rec']} vs {b['rec']}",
        f"- 고가 판매 {a['hi_n']}개 매출 {a['hi_rev']:,} (이익 {a['hi_profit']:,}) vs {b['hi_n']}개 {b['hi_rev']:,} ({b['hi_profit']:,})",
        f"- 고가 재고 원가(끝 시점) {a['held_cost']:,} ({a['held_n']}개) vs {b['held_cost']:,} ({b['held_n']}개)",
        f"- 전체 매출 {a['rev']:,} ({a['sales']}건) vs {b['rev']:,} ({b['sales']}건)",
        f"- 싼 물건(원가 10만 이하) 매입 {a['cheap_n']}개 원가 {a['cheap_cost']:,} vs {b['cheap_n']}개 {b['cheap_cost']:,}",
        f"- high_price_above_avg 매입 스킵 {a['skips']}건; 첫 호가 조정 {n}건 (그중 G3e0d6c67 정리 {c}건), "
        f"G3e0d6c67 판매 {g[0]}개 {g[1]:,}"])


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    import policy
    from depts import analyst, analyst_apply
    from market_db import connect
    from seller_conc_rollout import restart_seller, seller_back
    now = time.time()
    old_hm = policy.get("purchasing", "high_max")
    v = policy.write({"purchasing": {"high_max": 30}}, WHY + ": high_max 60 -> 30", by="person")
    db = connect()
    try:
        row = db.execute("SELECT high_share FROM policy ORDER BY ts DESC LIMIT 1").fetchone()
        db.execute("INSERT INTO policy VALUES (?,?,?,?)", (time.time(), row[0] if row else 0.6, 30,
                                                           "11/08 person's decision ~17:00: high-price units 60 -> 30"))
        db.commit()
    finally:
        db.close()
    decisions.record("purchasing", "high_max", "purchasing.high_max", [old_hm, 30], WHY + f"; policy v{v.get('version')}")
    log(f"high_max {old_hm} -> 30 (policy v{v.get('version')}, QC table)")
    d = analyst.db()
    try:
        analyst_apply._cols(d)
        cur = d.execute("INSERT INTO proposals (ts,kind,key,value,reason,evidence,status,expires,decided_ts,result,asked,"
                        "checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)", (now, "limit", "limits.HIGH_MAX", json.dumps(1_000_000),
                                                                      WHY, "person", "approved", now, now, "applying", 1))
        pid = cur.lastrowid
        d.commit()
    finally:
        d.close()
    try:
        res = analyst_apply.apply_limit("limits.HIGH_MAX", 1_000_000, pid, why="person's decision 11/08 ~17:00")
        analyst_apply._store(pid, res)
        d = analyst.db()
        d.execute("UPDATE proposals SET result=? WHERE id=?", (res["text"], pid))
        d.commit()
        d.close()
        decisions.record("analyst", "approved_by_person", "limits.HIGH_MAX", 1_000_000, f"#{pid}: {WHY} | {res['text']}")
        hm_text = res["text"]
    except Exception as e:
        d = analyst.db()
        d.execute("UPDATE proposals SET status='failed', result=? WHERE id=?", (str(e)[:300], pid))
        d.commit()
        d.close()
        hm_text = f"HIGH_MAX 1.0M 실패 ({type(e).__name__}: {str(e)[:120]}), 1.5M 그대로"
        alerts.send("urgent", "high_revert_hm_fail", hm_text, log=log)
    log(f"HIGH_MAX: {hm_text}")
    t = time.time()
    killed = restart_seller()
    back = None
    for _ in range(36):
        time.sleep(5)
        back = seller_back(t)
        if back:
            break
    log(f"seller restart: killed {killed}, back {back}")
    decisions.record("cs", "seller_restart", "high_ask", bool(back), "11/08 ~17:00: high-price first ask <= market average")
    alerts.send("info" if back else "urgent", "high_revert_on",
                f"고가 되돌리기 적용 {datetime.now():%H:%M}: high_max {old_hm} -> 30 (policy v{v.get('version')}), "
                f"HIGH_MAX: {hm_text[:140]}. 고가 첫 호가 시장 평균 이하, G3e0d6c67 평균 98% (원가 이상), 매입 필터 on. "
                f"seller 재시작 {'완료' if back else '확인 안 됨 - 확인 필요'}. 3시간 점검 예정", log=log)
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"high_revert_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
