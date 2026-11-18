"""STOCK_CAP 20M -> 30M (person's decision 11/08 ~20:35): limits.ANALYST_MAX STOCK_CAP 30M (code), STOCK_CAP by the
analyst path (b0 check, commit + push); high_max 30 and high_share unchanged, so the added room goes to non-high-price
buying. Brake (depts/cap_brake.py) unchanged in its three conditions (median 3 h, 6 h+ unsold share 25%, cash 2M),
but its low value 15M -> 20M: a trip puts STOCK_CAP back to 20M. hot_fast.json on: hot top 30 with a 24 h
supply -> sale median <= 1 h get my_share 0.8 (0.5 for the rest). Request-based buying 4 lines a run, target 8 for
fast SKUs (depts/request_buy.py, live from the next run). No seller restart (purchasing loads every run).
3 h later one Telegram, 3 h after vs 3 h before: cash, stock at cost (high / normal), units bought, sales, revenue,
turnover, buying held back by cause, brake state.
    python tools/cap30_rollout.py [--report-dry]          (log in logs/cap30_rollout.log)
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

LOG = os.path.join(HERE, "logs", "cap30_rollout.log")
WHY = "person's decision 11/08 ~20:35: STOCK_CAP 20M -> 30M, brake back to 20M, hot fast my_share 0.8, request 4 lines / 8"
NEW, LOW = 30_000_000, 20_000_000
WHEN = "11/08 ~20:35"
NOTE = ""                          # one more line in the 3 h report (11/09: the lock release came with it)
EXTRA = "요청 기반 4줄/목표 8, hot 상위 30 중 회전 1시간 이하 my_share 0.8. "
ME = "choo-chu"
H = 3


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


STATE = os.path.join(HERE, "logs", "cap30_state.json")


def held():
    """Stock at cost now from .market/inventory.json (expired out): (high cost, high n, normal cost, normal n, cash)."""
    from depts.qc import HIGH_MIN
    with open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8") as f:
        inv = json.load(f)
    u = [x.get("cost") or 0 for x in inv.get("units", []) if x.get("grade") != "expired"]
    hi, lo = [c for c in u if c > HIGH_MIN], [c for c in u if c <= HIGH_MIN]
    return sum(hi), len(hi), sum(lo), len(lo), inv.get("balance", 0)


def report(t, now=None):
    from cap20_rollout import window
    from cap20_again import causes
    t1 = min(t + H * 3600, now or time.time())
    t0 = t - (t1 - t)
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        a, b = window(db, t, t1), window(db, t0, t)
    finally:
        db.close()
    ca, cb = causes(t, t1), causes(t0, t)
    ha = held()
    try:
        hb = json.load(open(STATE, encoding="utf-8"))["start"]
    except (OSError, ValueError, KeyError):
        hb = ha
    try:
        st = json.load(open(os.path.join(HERE, "logs", "cap_brake_state.json"), encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    br = json.load(open(os.path.join(HERE, "cap_brake.json"), encoding="utf-8"))
    fired = br.get("fired") and br["fired"] >= t
    share = st.get("old_share")
    f = lambda p: f"{p[0]:,} -> {p[1]:,}" if p else "-"
    return (NOTE + "\n" if NOTE else "") + "\n".join([
        f"STOCK_CAP 30M {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}, "
        "후 vs 전 같은 길이)",
        f"- 현금 {hb[4]:,} -> {ha[4]:,} (cash 표: 후 {f(a['cash'])}, 전 {f(b['cash'])})",
        f"- 재고 원가(지금) 고가 {ha[0]:,} ({ha[1]}개) / 일반 {ha[2]:,} ({ha[3]}개) vs 적용 시점 고가 {hb[0]:,} ({hb[1]}개) / "
        f"일반 {hb[2]:,} ({hb[3]}개)",
        f"- 매입 {a['supplied']} vs {b['supplied']}개, 판매 {a['sales']} vs {b['sales']}건, 매출 {a['revenue']:,} vs "
        f"{b['revenue']:,} (마진 {a['margin']:,} vs {b['margin']:,})",
        f"- 회전: 입고→판매 중앙값 {a['median_h']} vs {b['median_h']}시간, 창 안 입고분 중 팔린 것 {a['supplied_sold']} vs "
        f"{b['supplied_sold']}",
        "- 막힌 매입 원인별 (후 vs 전): " + (", ".join(f"{k} {ca.get(k, 0)} vs {cb.get(k, 0)}" for k in
                                             sorted(set(ca) | set(cb), key=lambda k: -(ca.get(k, 0) + cb.get(k, 0)))) or "없음"),
        f"- 브레이크: {'걸림 ' + datetime.fromtimestamp(br['fired']).strftime('%H:%M') + ' -> STOCK_CAP 20M; ' + '; '.join(br.get('fired_why', [])) if fired else '안 걸림'}"
        f"; 중앙값 {st.get('median_h')}시간 (기준 3), 6시간+ 미판매 비율 "
        f"{('%.1f%%' % (100 * share)) if share is not None else '- (상향 후 6시간 지난 입고분 20개 미만)'} (기준 25%), "
        f"현금 {st.get('cash', 0):,} (기준 2M)"])


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
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
        res = analyst_apply.apply_limit("limits.STOCK_CAP", NEW, pid, why=f"person's decision {WHEN}")
    except Exception as e:
        d = analyst.db()
        d.execute("UPDATE proposals SET status='failed', result=? WHERE id=?", (str(e)[:300], pid))
        d.commit()
        d.close()
        alerts.send("urgent", "cap30_fail", f"STOCK_CAP 30M 상향 실패 ({type(e).__name__}: {str(e)[:150]}). 20M 그대로", log=log)
        log(f"failed: {e}")
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
    br.update(enabled=True, since=round(t), pid=pid, low=LOW, history=br.get("history", []) + [prev],
              why=br.get("why", "") + f"; {WHEN} cap 30M, a trip goes back to 20M")
    json.dump(br, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"t": t, "start": held()}, open(STATE, "w", encoding="utf-8"))
    hp = os.path.join(HERE, "hot_fast.json")
    hf = json.load(open(hp, encoding="utf-8"))
    hf.update(enabled=True, since=round(t))
    json.dump(hf, open(hp, "w", encoding="utf-8"), ensure_ascii=False)
    decisions.record("analyst", "approved_by_person", "limits.STOCK_CAP", NEW, f"#{pid}: {WHY} | {res['text']}")
    decisions.record("purchasing", "hot_fast_on", "hot_fast", hf, WHY)
    log(f"#{pid}: {res['text']}; brake low {LOW:,} since {t:.0f}; hot_fast on")
    alerts.send("info", "cap30_on", f"STOCK_CAP 20M -> 30M {datetime.now():%H:%M} (#{pid}, {res['text'][:140]}). high_max {__import__('policy').get('purchasing', 'high_max')}, "
                "고가 비중 그대로. 브레이크 3개 그대로, 걸리면 20M으로 복귀. " + EXTRA
                + "3시간 점검 예정 (분석관 3시간 점검은 시장 보정 기준)", log=log)
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"cap30_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
