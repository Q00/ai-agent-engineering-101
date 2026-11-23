"""The 11/09 14:00 seller restart (person's decisions 11/09 ~11:10 and ~11:30), right after the high-price 3 h report of
tools/high40_rollout.py ("report sent" in logs/high40_rollout.log; at the latest 14:20). Replaces
tools/sofa_grade_rollout.py. The restart loads:
  1. C grade sofas without a warranty, A / B 72 h (depts/sofa.warranty_for)
  2. quiet negotiations (10 min) closed as expired, their units free again (depts/open_expiry.py; tools/lock_watch.py
     records every hour and alerts over 20% locked)
  3. B grade high-price warranty 48 h (depts/b_warranty.py, b_warranty.json switched on here): TV / 냉방 on;
     침대/매트리스 24 h trial on 50% of the units (tag b_warranty_trial); other categories none
Reports: 3 h after (lock release, 3 h after vs 3 h before) and 24 h after (B warranty, per category, with vs without).
    python tools/restart_1400.py [--lock-dry | --b-dry]          (log in logs/restart_1400.log)
"""
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)

LOG = os.path.join(HERE, "logs", "restart_1400.log")
AFTER = os.path.join(HERE, "logs", "high40_rollout.log")
LATEST = datetime(2026, 11, 9, 14, 20).timestamp()
ME = "choo-chu"
STOCK_RE = re.compile(r"no stock|out of stock|none of that|sold out|wrong|not what", re.I)   # depts/insights.py


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def report_sent():
    try:
        with open(AFTER, encoding="utf-8") as f:
            return any("report sent" in ln for ln in f)
    except OSError:
        return False


def _db():
    return sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)


def held_at(db, sku, ts, now_units):
    """A unit of sku was mine at ts: supplied before, not sold by me before, and sold later or still held now."""
    for unit, sup in db.execute("SELECT unit, supplied_at FROM my_stock WHERE sku=? AND supplied_at<=?", (sku, ts)):
        sold = db.execute("SELECT MIN(ts) FROM settles WHERE unit=? AND shop=?", (unit, ME)).fetchone()[0]
        if (sold is not None and sold >= ts) or (sold is None and unit in now_units):
            return True
    return False


def lock_window(db, t0, t1, now_units):
    """Named-SKU "not in stock" answers (substitute offers or plain no-stock, logs/subst_exp.jsonl), those for a
    SKU I held at that moment, sales, revenue, one-star stock / wrong-item reviews."""
    nost = held = offers = 0
    with open(os.path.join(HERE, "logs", "subst_exp.jsonl"), encoding="utf-8") as f:
        for ln in f:
            e = json.loads(ln)
            if t0 <= e["ts"] < t1 and e.get("event") in ("offer", "no_offer"):
                nost += 1
                offers += e["event"] == "offer"
                held += bool(e.get("x")) and held_at(db, e["x"], e["ts"], now_units)
    s = db.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM settles WHERE shop=? AND ts>=? AND ts<?", (ME, t0, t1)).fetchone()
    one = sum(1 for (r,) in db.execute("SELECT review FROM my_results WHERE stars<=1 AND ts>=? AND ts<?", (t0, t1))
              if STOCK_RE.search(r or ""))
    return {"no_stock": nost, "no_stock_held": held, "offers": offers, "sales": s[0], "revenue": s[1], "one_star_stock": one}


def lock_report(t, now=None):
    t1 = min(t + 3 * 3600, now or time.time())
    inv = json.load(open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8"))
    now_units = {u["unit"] for u in inv.get("units", [])}
    db = _db()
    try:
        a, b = lock_window(db, t, t1, now_units), lock_window(db, t - (t1 - t), t, now_units)
    finally:
        db.close()
    try:
        import lock_watch
        m = lock_watch.measure()
        lw = f"지금 협상에 잡힌 재고 {m['locked']}/{m['held']} ({m['share']:.1%}), expired {lock_watch.expired_count(t, t1)}건"
    except Exception as e:
        lw = f"잠금 측정 실패 ({type(e).__name__})"
    return "\n".join([
        f"오래 열린 협상 자동 해제 {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}, "
        "후 vs 전 같은 길이; 같은 재시작에 C등급 소파 보증·B등급 보증 시험, STOCK_CAP 30M도 함께 적용됨)",
        f"- 재고 없음 응답(이름 지정 SKU, 대체 제안 포함) {a['no_stock']} vs {b['no_stock']}건",
        f"- 그중 실제 보유 SKU에 나간 것 {a['no_stock_held']} vs {b['no_stock_held']}건",
        f"- 대체 제안(후보를 보낸 것) {a['offers']} vs {b['offers']}건 (11:58~12:0x 대체 A안 중단 구간은 후보 없이 '재고 없음')",
        f"- 판매 {a['sales']} vs {b['sales']}건, 매출 {a['revenue']:,} vs {b['revenue']:,}",
        f"- 1점 리뷰(재고/품목) {a['one_star_stock']} vs {b['one_star_stock']}건",
        f"- {lw}"])


def b_rows(db, t0, t1):
    """Negotiations of B grade high-price non-sofa units in the B warranty categories, with the warranty or not."""
    from depts import b_warranty, sofa
    from depts.qc import HIGH_MIN
    r = b_warranty.rules()
    cats = set(r.get("on_cats") or []) | set(r.get("trial_cats") or [])
    cat = json.load(open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8"))
    leaf = lambda k: ((cat.get(k) or {}).get("category") or "").split(">")[-1]
    out = []
    for task, ts, sku, unit, cost, outcome, price, wh in db.execute(
            "SELECT d.task, d.ts, d.sku, d.unit, d.cost, d.outcome, d.price, d.warranty_hours FROM shop_deals d "
            "JOIN my_stock m ON m.unit=d.unit WHERE d.ts>=? AND d.ts<? AND m.grade='B' AND d.cost>?", (t0, t1, HIGH_MIN)):
        if leaf(sku) in cats and not sofa.is_sofa(sku):
            out.append({"task": task, "ts": ts, "sku": sku, "unit": unit, "cost": cost, "outcome": outcome,
                        "cat": leaf(sku), "w": bool(wh)})
    return out


def b_report(t, now=None):
    t1 = min(t + 24 * 3600, now or time.time())
    db = _db()
    try:
        rows = b_rows(db, t, t1)
        lines = [f"B등급 고가 보증 48시간 {(t1 - t) / 3600:.0f}시간 보고 ({datetime.fromtimestamp(t):%m-%d %H:%M} ~ "
                 f"{datetime.fromtimestamp(t1):%m-%d %H:%M}); 침대/매트리스는 단위 50% 시험(b_warranty_trial)"]
        for c in sorted({r["cat"] for r in rows}) or ["-"]:
            for w in (True, False):
                rs = [r for r in rows if r["cat"] == c and r["w"] == w]
                if not rs:
                    continue
                closed = [r for r in rs if r["outcome"] not in (None, "open")]
                units = {r["unit"] for r in rs}
                sales = db.execute(f"SELECT unit, amount, buyer, ts FROM settles WHERE shop=? AND ts>=? AND unit IN "
                                   f"({','.join('?' * len(units))})", (ME, t, *units)).fetchall()
                cost = {r["unit"]: r["cost"] for r in rs}
                ref = []
                for unit, amt, buyer, ts in sales:
                    b = db.execute("SELECT refunded FROM broken WHERE shop=? AND buyer=? AND refunded>0 AND ts>=? AND ts<?",
                                   (ME, buyer, ts, ts + 72 * 3600)).fetchone()
                    if b and db.execute("SELECT sku FROM my_stock WHERE unit=?", (unit,)).fetchone()[0] in {r["sku"] for r in rs}:
                        ref.append(b[0])
                stars = []
                for unit, amt, buyer, ts in sales:
                    s = db.execute("SELECT stars FROM my_results WHERE buyer=? AND ts>=? AND ts<=? AND stars IS NOT NULL "
                                   "ORDER BY ts LIMIT 1", (buyer, ts - 30, ts + 600)).fetchone()
                    if s:
                        stars.append(s[0])
                rec = sum(1 for r in closed if r["outcome"] == "receipt")
                net = sum(a - cost.get(u, 0) for u, a, _, _ in sales) - sum(ref)
                lines.append(
                    f"- {c} 보증 {'있음' if w else '없음'}: 협상 {len(closed)}, 성사 {rec} ({rec / len(closed) if closed else 0:.0%}), "
                    f"판매 {len(sales)} (판매가 중앙값 {int(statistics.median([s[1] for s in sales])) if sales else 0:,}), "
                    f"환불 {len(ref)}건 {sum(ref):,}, 협상당 순이익 {int(net / len(closed)) if closed else 0:,}, "
                    f"별점 {round(sum(stars) / len(stars), 2) if stars else '-'} (n {len(stars)})")
    finally:
        db.close()
    if len(lines) == 1:
        lines.append("- 해당 협상 없음")
    lines.append("시험 끝: 침대/매트리스 B등급은 보증 없음으로 돌아감 (TV·냉방은 유지). 계속할지는 사람 결정")
    return "\n".join(lines)


def main():
    if "--lock-dry" in sys.argv:
        print(lock_report(time.time() - 3600))
        return
    if "--b-dry" in sys.argv:
        print(b_report(time.time() - 24 * 3600))
        return
    import alerts
    import decisions
    from depts import b_warranty
    from seller_conc_rollout import restart_seller, seller_back
    log("waiting for the 14:00 high-price report")
    while "--now" not in sys.argv and not report_sent() and time.time() < LATEST:   # 11/09 ~12:05: --now
        time.sleep(20)
    t = time.time()
    r = b_warranty.rules()
    r.update(enabled=True, since=round(t), trial_since=round(t), trial_until=round(t + 24 * 3600))
    with open(b_warranty.RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    killed = restart_seller()
    back = None
    for _ in range(36):
        time.sleep(5)
        back = seller_back(t)
        if back:
            break
    log(f"b_warranty on {r}; seller restart: killed {killed}, back {back}")
    for k, v in (("sofa.warranty_for", "C grade sofas without a warranty, A / B 72 h"),
                 ("open_expiry", "negotiations quiet 10 min closed as expired"),
                 ("b_warranty", "B grade high-price 48 h: TV / 냉방 on, 침대/매트리스 24 h 50% trial")):
        decisions.record("cs", "seller_restart", k, bool(back), "person 11/09: " + v)
    alerts.send("info" if back else "urgent", "restart_1400",
                f"14:00 seller 재시작 {datetime.now():%H:%M}: (1) C등급 소파 보증 없음, A/B 72시간 (2) 10분 무활동 협상 expired, "
                "물건 다시 판매, 매시간 잠금 기록(20% 넘으면 긴급) (3) B등급 고가 보증 48시간: TV·냉방 붙임, 침대/매트리스 24시간 "
                f"50% 시험. seller 재시작 {'완료' if back else '확인 안 됨 - 확인 필요'}. 3시간 뒤 잠금 해제 점검, 24시간 뒤 B 보증 보고",
                log=log)
    time.sleep(90)                                     # 11/09 ~12:05: the lock share right after the restart
    try:
        import lock_watch
        m = lock_watch.measure()
        alerts.send("info", "restart_lock_now", f"재시작 직후 협상에 잡힌 재고 {m['share']:.1%} ({m['locked']}/{m['held']}개, 원가 "
                    f"{m['locked_cost']:,}); 열린 협상 {m['open']}", log=log)
    except Exception as e:
        log(f"lock share after the restart failed: {type(e).__name__}: {e}")
    while time.time() < t + 3600 + 30:                 # the first hour's "not in stock" answers
        time.sleep(30)
    try:
        db = _db()
        inv = json.load(open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8"))
        a = lock_window(db, t, t + 3600, {u["unit"] for u in inv.get("units", [])})
        b = lock_window(db, t - 3600, t, {u["unit"] for u in inv.get("units", [])})
        db.close()
        import lock_watch
        m = lock_watch.measure()
        alerts.send("info", "restart_lock_1h", f"재시작 후 1시간: '재고 없음' 응답 {a['no_stock']}건 (그중 보유 SKU {a['no_stock_held']}) vs "
                    f"직전 1시간 {b['no_stock']}건 ({b['no_stock_held']}); 판매 {a['sales']} vs {b['sales']}; 지금 잠긴 재고 "
                    f"{m['share']:.1%}, expired {lock_watch.expired_count(t, t + 3600)}건", log=log)
    except Exception as e:
        log(f"1 h lock note failed: {type(e).__name__}: {e}")
    while time.time() < t + 3 * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"lock_report:{int(t)}", lock_report(t), log=log)
    log("lock report sent")
    while time.time() < t + 24 * 3600 + 1800:          # 30 min more for the last payments, breaks and results
        time.sleep(60)
    alerts.send("daily", f"b_warranty_report:{int(t)}", b_report(t), log=log)
    log("b report sent")


if __name__ == "__main__":
    main()
