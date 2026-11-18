"""STOCK_CAP 20M -> 40M (person's decision 11/09 ~19:00, after the 18:47 brake trip at 39.8% of only 1.97M):
limits.ANALYST_MAX STOCK_CAP 40M (code), STOCK_CAP by the analyst path (b0 check, commit + push). Brake
(depts/cap_brake.py) condition 2 now: share of the unsold cost > 25% AND that cost > 3M (old_unsold_min), judged only
once the eligible cost is >= 5M (min_old_cost, was 20 units); median 3 h, cash 2M and the trip back to 20M unchanged.
hot_fast and the rest unchanged. 3 h later one Telegram, 3 h after vs 3 h before: revenue, high-price and sofa sales,
stock at cost, cash, buying held back by cause, brake state.
    python tools/cap40_rollout.py [--report-dry | --no-wait | --report-only]          (log in logs/cap40_rollout.log)
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
import cap30_rollout as c30  # noqa: E402

LOG = os.path.join(HERE, "logs", "cap40_rollout.log")
STATE = os.path.join(HERE, "logs", "cap40_state.json")
WHY = ("person's decision 11/09 ~19:00: STOCK_CAP 20M -> 40M, brake condition 2 = share > 25% AND unsold > 3M, "
       "judged from 5M eligible cost; a trip goes back to 20M")
NEW, LOW, H = 40_000_000, 20_000_000, 3
ME = "choo-chu"
SOFA_CAT = "소파/의자/스툴"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def hi_sofa(db, t0, t1):
    """High-price (unit cost > qc.HIGH_MIN) and sofa sales in [t0, t1): (n, revenue, margin) each."""
    from depts.qc import HIGH_MIN
    from depts.substitute import catalog
    cat = catalog()
    rows = db.execute("SELECT s.sku, s.amount, m.cost FROM settles s LEFT JOIN my_stock m ON m.unit=s.unit "
                      "WHERE s.shop=? AND s.ts>=? AND s.ts<?", (ME, t0, t1)).fetchall()
    sofa = lambda k: ((cat.get(k) or {}).get("category") or "").split(">")[-1] == SOFA_CAT
    out = {}
    for name, keep in (("high", lambda k, cost: (cost or 0) > HIGH_MIN), ("sofa", lambda k, cost: sofa(k))):
        x = [(a, cost) for k, a, cost in rows if keep(k, cost)]
        out[name] = (len(x), sum(a for a, _ in x), sum(a - cost for a, cost in x if cost))
    return out


def report(t, now=None):
    from cap20_rollout import window
    from cap20_again import causes
    t1 = min(t + H * 3600, now or time.time())
    t0 = t - (t1 - t)
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        a, b = window(db, t, t1), window(db, t0, t)
        ha, hb = hi_sofa(db, t, t1), hi_sofa(db, t0, t)
    finally:
        db.close()
    ca, cb = causes(t, t1), causes(t0, t)
    now_h = c30.held()
    try:
        start = json.load(open(STATE, encoding="utf-8"))["start"]
    except (OSError, ValueError, KeyError):
        start = now_h
    try:
        st = json.load(open(os.path.join(HERE, "logs", "cap_brake_state.json"), encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    br = json.load(open(os.path.join(HERE, "cap_brake.json"), encoding="utf-8"))
    fired = br.get("fired") and br["fired"] >= t
    share = st.get("old_share")
    f = lambda p: f"{p[0]:,} -> {p[1]:,}" if p else "-"
    s3 = lambda p: f"{p[0]}건 {p[1]:,} (마진 {p[2]:,})"
    el = st.get("old_eligible_cost") or 0
    return "\n".join([
        f"STOCK_CAP 40M {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}, "
        "후 vs 전 같은 길이)",
        f"- 매출 {a['revenue']:,} vs {b['revenue']:,} (판매 {a['sales']} vs {b['sales']}건, 마진 {a['margin']:,} vs {b['margin']:,})",
        f"- 고가 판매 {s3(ha['high'])} vs {s3(hb['high'])}; 소파 판매 {s3(ha['sofa'])} vs {s3(hb['sofa'])}",
        f"- 재고 원가(지금) 고가 {now_h[0]:,} ({now_h[1]}개) / 일반 {now_h[2]:,} ({now_h[3]}개), 합 {now_h[0] + now_h[2]:,} vs "
        f"적용 시점 {start[0] + start[2]:,} (고가 {start[0]:,} / 일반 {start[2]:,}); 매입 {a['supplied']} vs {b['supplied']}개",
        f"- 현금 {start[4]:,} -> {now_h[4]:,} (cash 표: 후 {f(a['cash'])}, 전 {f(b['cash'])})",
        "- 막힌 매입 원인별 (후 vs 전): " + (", ".join(f"{k} {ca.get(k, 0)} vs {cb.get(k, 0)}" for k in
                                             sorted(set(ca) | set(cb), key=lambda k: -(ca.get(k, 0) + cb.get(k, 0)))) or "없음"),
        f"- 브레이크: {'걸림 ' + datetime.fromtimestamp(br['fired']).strftime('%H:%M') + ' -> STOCK_CAP 20M; ' + '; '.join(br.get('fired_why', [])) if fired else '안 걸림'}"
        f"; 중앙값 {st.get('median_h')}시간 (기준 3), 상향 후 6시간+ 입고분 원가 {el:,} 중 미판매 "
        f"{(st.get('old_unsold_cost') or 0):,} ({('%.1f%%' % (100 * share)) if share is not None else '-'}; 기준 25% 그리고 3M 초과, "
        f"판단은 원가 5M 이상부터{'' if el >= 5_000_000 else ' - 아직 미달'}), 현금 {st.get('cash', 0):,} (기준 2M)"])


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    if "--report-only" in sys.argv:                 # applied already (--no-wait): wait for the 3 h report only
        t = json.load(open(STATE, encoding="utf-8"))["t"]
        while time.time() < t + H * 3600 + 60:
            time.sleep(60)
        alerts.send("daily", f"cap40_report:{int(t)}", report(t), log=log)
        log("report sent")
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
        res = analyst_apply.apply_limit("limits.STOCK_CAP", NEW, pid, why="person's decision 11/09 ~19:00")
    except Exception as e:
        d = analyst.db()
        d.execute("UPDATE proposals SET status='failed', result=? WHERE id=?", (str(e)[:300], pid))
        d.commit()
        d.close()
        alerts.send("urgent", "cap40_fail", f"STOCK_CAP 40M 상향 실패 ({type(e).__name__}: {str(e)[:150]}). 20M 그대로", log=log)
        log(f"failed: {e}")
        print("FAILED", e)
        return
    analyst_apply._store(pid, res)
    d = analyst.db()
    d.execute("UPDATE proposals SET result=? WHERE id=?", (res["text"], pid))
    d.commit()
    d.close()
    t = time.time()
    path = os.path.join(HERE, "cap_brake.json")
    br = json.load(open(path, encoding="utf-8"))
    prev = {k: br.pop(k) for k in ("fired", "fired_why") if k in br}
    br.update(enabled=True, since=round(t), pid=pid, low=LOW, old_unsold_min=3_000_000, min_old_cost=5_000_000,
              history=br.get("history", []) + [prev],
              why=br.get("why", "") + "; 11/09 ~19:00 cap 40M, condition 2 share > 25% AND unsold > 3M from 5M eligible, "
                                      "a trip goes back to 20M")
    json.dump(br, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"t": t, "start": c30.held()}, open(STATE, "w", encoding="utf-8"))
    decisions.record("analyst", "approved_by_person", "limits.STOCK_CAP", NEW, f"#{pid}: {WHY} | {res['text']}")
    log(f"#{pid}: {res['text']}; brake low {LOW:,} since {t:.0f}")
    print(f"#{pid}: {res['text']}")
    alerts.send("info", "cap40_on", f"STOCK_CAP 20M -> 40M {datetime.now():%H:%M} (#{pid}, {res['text'][:140]}). 브레이크 조건 2: "
                "상향 후 6시간+ 입고분 미판매 비율 > 25% 그리고 미판매 원가 > 3M (판단은 그 원가 5M 이상부터). 중앙값 3시간·현금 2M, "
                "걸리면 20M 복귀는 그대로. 3시간 점검 예정", log=log)
    if "--no-wait" in sys.argv:
        return
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"cap40_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
