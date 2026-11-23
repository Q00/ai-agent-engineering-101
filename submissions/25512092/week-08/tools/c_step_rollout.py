"""Person's decision 11/09 ~15:05: right after the 18:07 high-focus 3 h report ("report sent" in
logs/high_focus_rollout.log after 18:00; at the latest 18:40), one seller restart for both:
  1. open negotiations quiet for 5 min (was 10) are closed as expired (open_expiry.json expire_s 300)
  2. C grade high-price units (not sofas, not over-cost) step down their floor (high_ask.json c_step):
     > 12 h after supply max(cost x 1.10, 48 h market median x 0.6), > 24 h cost x 1.00 (11/09 ~15:15; was 1.10),
     never under cost; a lower
     existing markdown floor stays; the overcost_high rule is unchanged
Before: the target list (SKU, age, cost, floor now, new floor) by Telegram (--preview sends only that).
3 h later one Telegram, 3 h after vs 3 h before: expired, locked share, "not in stock" answers for SKUs I held;
C grade high-price negotiations and sales, profit on cost, the C grade high-price units left and their cost.
    python tools/c_step_rollout.py [--preview] [--now] [--report-dry]      (log in logs/c_step_rollout.log)
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

LOG = os.path.join(HERE, "logs", "c_step_rollout.log")
FROM = datetime(2026, 11, 9, 18, 0).timestamp()
LATEST = datetime(2026, 11, 9, 18, 40).timestamp()
H = 3
ME = "choo-chu"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def ready():
    try:
        with open(os.path.join(HERE, "logs", "high_focus_rollout.log"), encoding="utf-8") as f:
            for ln in f:
                if "report sent" in ln:
                    t = datetime.strptime(f"2026-{ln[:14]}", "%Y-%m-%d %H:%M:%S").timestamp()
                    if t >= FROM:
                        return True
    except (OSError, ValueError):
        pass
    return False


def units():
    with open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8") as f:
        return [u for u in json.load(f).get("units", []) if u.get("grade") != "expired"]


def targets(now=None):
    """C grade high-price units, not sofas, not over-cost: floor now (the seller's rules) and with the steps."""
    from depts import high_ask, overcost, sofa
    from depts.qc import HIGH_MIN
    from market_db import load_stats, price_of
    from pricing import floor
    import strategy
    now = now or time.time()
    stats = load_stats()
    case = strategy.current()[0] if hasattr(strategy, "current") else "normal"
    r = high_ask.rules()
    cs = dict(r.get("c_step") or {}, enabled=True)
    out = []
    for u in units():
        if u.get("grade") != "C" or (u.get("cost") or 0) <= HIGH_MIN or sofa.applies(u):
            continue
        avg, _ = price_of(u["sku"], stats)
        if overcost.is_overcost(u, avg):
            continue
        res, rule = floor(u, avg, case)
        cur = high_ask.floor(u, avg, res, case, now=now, rule=rule)
        step = high_ask.c_step_floor(u, now, cs)
        new = min(cur, step) if step is not None else cur
        age = (now - (u.get("supplied_at") or now)) / 3600
        out.append({"unit": u["unit"], "sku": u["sku"], "name": (u.get("name") or "")[:22], "age_h": round(age, 1),
                    "cost": int(u["cost"]), "floor": int(cur), "new": int(new), "avg": int(avg) if avg else None})
    return sorted(out, key=lambda x: -x["age_h"])


def targets_text(rows, head):
    ch = [r for r in rows if r["new"] < r["floor"]]
    L = [f"{head}: C등급 고가(소파·원가>평균 제외) {len(rows)}개, 원가 {sum(r['cost'] for r in rows):,}; 지금 하한이 내려가는 것 "
         f"{len(ch)}개 (12시간 이하는 12시간 지나면 적용)",
         "SKU / 나이 / 원가 / 지금 하한 -> 새 하한 (시장 평균)"]
    for r in rows:
        av = "-" if r["avg"] is None else f"{r['avg']:,}"
        same = "" if r["new"] < r["floor"] else " (변화 없음)"
        L.append(f"- {r['sku']} {r['name']}: {r['age_h']:.0f}시간, 원가 {r['cost']:,}, 하한 {r['floor']:,} -> {r['new']:,}"
                 f"{same} (평균 {av})")
    return "\n".join(L)


def c_window(db, t0, t1, now_units):
    from depts.qc import HIGH_MIN
    neg = db.execute("SELECT COUNT(*) FROM shop_deals s JOIN my_stock m ON m.unit=s.unit WHERE s.ts>=? AND s.ts<? AND "
                     "s.cost>? AND m.grade='C'", (t0, t1, HIGH_MIN)).fetchone()[0] if has_col(db, "my_stock", "grade") \
        and has_col(db, "shop_deals", "unit") else None
    s = db.execute("SELECT s.amount, m.cost FROM settles s JOIN my_stock m ON m.unit=s.unit WHERE s.shop=? AND s.ts>=? AND "
                   "s.ts<? AND m.cost>? AND s.grade='C'", (ME, t0, t1, HIGH_MIN)).fetchall()
    return {"neg": neg, "n": len(s), "rev": sum(a for a, _ in s), "cost": sum(c for _, c in s)}


CAUSES = (("lock", "다른 협상에 잡힘(5분 해제 대기 포함)"), ("receipt", "영수증 결제 대기"), ("policy", "정책 제한"), ("other", "기타"))


def causes(db, t0, t1, now_units):
    """(11/09 ~15:15) "not in stock" answers for a SKU I held, by the cause the seller logged (depts/nostock_cause; from
    the 18:07 restart on): {cause: n}, plus lock answers whose negotiation was over half the expiry, and unlogged."""
    from restart_1400 import held_at
    out = {k: 0 for k, _ in CAUSES}
    out.update(unlogged=0, near_expiry=0)
    with open(os.path.join(HERE, "logs", "subst_exp.jsonl"), encoding="utf-8") as f:
        for ln in f:
            e = json.loads(ln)
            if not (t0 <= e["ts"] < t1 and e.get("event") in ("offer", "no_offer") and e.get("x")):
                continue
            if not held_at(db, e["x"], e["ts"], now_units):
                continue
            c = e.get("cause")
            if not c:
                out["unlogged"] += 1
                continue
            out[c["cause"]] = out.get(c["cause"], 0) + 1
            out["near_expiry"] += c["cause"] == "lock" and bool(c.get("near_expiry"))
    return out


def causes_text(c):
    return (", ".join(f"{name} {c[k]}" for k, name in CAUSES) + f" (그중 잠금이 해제 시간 절반 넘게 열린 협상 {c['near_expiry']})"
            + (f", 원인 기록 없음 {c['unlogged']}" if c["unlogged"] else ""))


def has_col(db, t, c):
    return c in [r[1] for r in db.execute(f"PRAGMA table_info({t})")]


def report(t, now=None):
    import lock_watch
    from restart_1400 import lock_window
    t1 = min(t + H * 3600, now or time.time())
    us = units()
    now_units = {u["unit"] for u in us}
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        a, b = lock_window(db, t, t1, now_units), lock_window(db, t - (t1 - t), t, now_units)
        ca, cb = c_window(db, t, t1, now_units), c_window(db, t - (t1 - t), t, now_units)
        cz = causes(db, t, t1, now_units)
    finally:
        db.close()
    shares = []
    try:
        with open(os.path.join(HERE, "logs", "lock_watch.log"), encoding="utf-8") as f:
            for ln in f:
                if "{" in ln:
                    j = json.loads(ln[ln.index("{"):])
                    if t <= j.get("ts", 0) < t1 and "share" in j:
                        shares.append(j["share"])
    except (OSError, ValueError):
        pass
    m = lock_watch.measure()
    rows = targets()
    pc = lambda x: f"{(x['rev'] - x['cost']) / x['cost']:+.1%}" if x["cost"] else "-"
    return "\n".join([
        f"seller 재시작(협상 해제 5분 + C등급 고가 하한 단계 인하) {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ "
        f"{datetime.fromtimestamp(t1):%H:%M}, 후 vs 전 같은 길이)",
        f"- expired {lock_watch.expired_count(t, t1)} vs {lock_watch.expired_count(t - (t1 - t), t)}건",
        f"- 잠긴 재고 비율: 지금 {m['share']:.1%} ({m['locked']}/{m['held']}), 구간 중 매시 측정 "
        f"{', '.join(f'{x:.1%}' for x in shares) or '-'}",
        f"- 재고 없음 응답 {a['no_stock']} vs {b['no_stock']}건, 그중 실제 보유 SKU에 나간 것 {a['no_stock_held']} vs {b['no_stock_held']}건",
        f"- 보유 SKU 재고 없음 원인(적용 후, seller가 응답 시점에 기록): {causes_text(cz)}; 정책 제한은 고가 개수·브레이크가 매입만 "
        "막아서 0이 정상",
        f"- C등급 고가: 협상 {ca['neg'] if ca['neg'] is not None else '-'} vs {cb['neg'] if cb['neg'] is not None else '-'}, "
        f"판매 {ca['n']} vs {cb['n']}, 매출 {ca['rev']:,} vs {cb['rev']:,}, 원가 대비 이익 {pc(ca)} vs {pc(cb)}",
        f"- 남은 C등급 고가(소파·원가>평균 제외) {len(rows)}개, 원가 {sum(r['cost'] for r in rows):,}"])


def apply():
    import alerts
    import decisions
    from depts import high_ask, open_expiry
    from seller_conc_rollout import restart_seller, seller_back
    rows = targets()
    alerts.send("info", "c_step_list", targets_text(rows, "적용 직전 대상"), log=log)
    with open(open_expiry.RULES, "w", encoding="utf-8") as f:
        json.dump({"expire_s": 300, "note": "person 11/09 ~15:05: 10 -> 5 min"}, f)
    r = high_ask.rules()
    r["c_step"] = {"enabled": True, "h1": 12, "h2": 24, "cost": 1.10, "cost2": 1.00, "median": 0.6, "since": round(time.time()),
                   "note": "person 11/09 ~15:05: C grade high (not sofas, not over-cost) floor steps"}
    with open(high_ask.RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    t = time.time()
    killed = restart_seller()
    back = None
    for _ in range(36):
        time.sleep(5)
        back = seller_back(t)
        if back:
            break
    decisions.record("sales", "c_step_on", "high_ask.c_step", r["c_step"], "person 11/09 ~15:05; open expiry 300 s")
    log(f"applied: expire 300 s, c_step {r['c_step']}; seller restart killed {killed}, back {back}")
    alerts.send("info", "c_step_on", f"seller 재시작 {datetime.now():%H:%M} ({'완료' if back else '확인 안 됨'}): (1) 협상 자동 해제 10분 -> 5분 "
                "(2) C등급 고가(소파·원가>평균 제외) 하한: 입고 12시간 넘으면 max(원가 x 1.10, 48시간 중앙값 x 0.6), 24시간 넘으면 원가 x 1.00, "
                "원가 아래 없음, 더 낮은 기존 인하는 유지 (3) 원가>평균 고가도 원가가 평균보다 2% 이내로 비싸면 기존 손절(평균에 판매) 허용. 3시간 점검 예정", log=log)
    return t


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    import alerts
    if "--preview" in sys.argv:
        alerts.send("info", "c_step_preview", targets_text(targets(), "적용 전 미리보기(18:07 적용 예정, 그때 다시 보냄)"), log=log)
        return
    if "--now" not in sys.argv:
        log("waiting for the 18:07 high-focus report")
        while not ready() and time.time() < LATEST:
            time.sleep(30)
    t = apply()
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"c_step_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
