"""11/10 ~11:50 (person's decision): apply, then one 3 h report.
  apply   STOCK_CAP automatic again (cash + stock - 3M, exit 88%); the cap brake and the automatic cap's overstock case
          judge unsold units at OLD_H (12 h, was 6 h) from now (units supplied after the switch-on only); share 35%,
          unsold cost 5M, 2 checks in a row and the median 4 h unchanged. high_share 0.7 (policy), pinned 24 h (no CEO /
          QC automatic change); high_max 100 unchanged. Sofa warranty on again (A / B 72 h, C none), the automatic
          switch-off now "48 h sofa refunds > 30% of the 48 h sofa profit" (sofa_rules.json off_rule, alerts.py).
  report  3 h after vs the 3 h before: Book and revenue per hour, high-price buys / sales / stock, sofa sales and close
          rate (after the warranty is back), blocked buying by cause, brakes.
    python tools/reenable_1110.py [--report-dry | --report-only | --no-wait]
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

LOG = os.path.join(HERE, "logs", "reenable_1110.log")
STATE = os.path.join(HERE, "logs", "reenable_1110_state.json")
WHY = "person's decision 11/10 ~11:50"
H = 3
OLD_H = 12
LIMITS_PY = os.path.join(HERE, "limits.py")
SOFA_OFF = {"window_h": 48, "share": 0.30}


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def _dump(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def apply(now=None, held=None):
    import limits
    import policy
    from depts import cap_brake, policy_notify, sofa, turnover_case
    now = now or time.time()
    if held is None:
        import cap30_rollout as c30
        held = c30.held()
    out = []
    a = limits.auto_cap_rules()
    hist = a.pop("history", [])
    if a.get("fired"):
        hist.append({"fired": a.pop("fired"), "fired_why": a.pop("fired_why", [])})
    a.update(enabled=True, since=round(now), paused=False, history=hist,
             why=a.get("why", "") + f"; {WHY}: on again, unsold judged at {OLD_H} h from now")
    a["case"] = dict(a.get("case") or {}, old_h=OLD_H)
    _dump(limits.AUTO_CAP_PATH, a)
    try:
        os.remove(turnover_case.STATE)                   # the overstock streak starts again
    except OSError:
        pass
    r = cap_brake.rules()
    if r.get("fired"):
        r.setdefault("history", []).append({"fired": r.pop("fired"), "fired_why": r.pop("fired_why", [])})
    r.update(enabled=True, since=round(now), pid=None, old_h=OLD_H, warn_streak=0,
             why=r.get("why", "") + f"; {WHY}: automatic cap on again, unsold at {OLD_H} h (35%, 5M, 2 in a row kept)")
    _dump(cap_brake.RULES, r)
    cap = int(held[4] + held[0] + held[2] - a["reserve"])
    with open(LIMITS_PY, "a", encoding="utf-8") as f:
        f.write(f"STOCK_CAP_CHANGES.append(({now:.0f}, {cap:_}))   # {datetime.fromtimestamp(now):%m/%d %H:%M} automatic "
                f"cap on again (cash + stock - 3M; person 11/10 ~11:50)\n")
    out.append(f"STOCK_CAP 자동 다시 켬: 현금 {held[4]:,} + 재고 원가 {held[0] + held[2]:,} - 300만 = {cap:,} (나가는 기준 88%), "
               f"매 run 재계산")
    out.append(f"브레이크·과재고 판단 미판매 기준 6시간 -> {OLD_H}시간 넘은 입고분 (지금 이후 입고분부터), 35%·500만·2회 연속·"
               "중앙값 4시간 그대로")
    old = policy.get("purchasing", "high_share")
    policy.write({"purchasing": {"high_share": 0.7}}, WHY + ": high_share 0.7, pinned 24 h", by="person")
    policy_notify.pin("purchasing.high_share", now)
    out.append(f"high_share {old} -> {policy.get('purchasing', 'high_share')} (policy v{policy.version()}), 24시간 고정 "
               f"({datetime.fromtimestamp(now + policy_notify.PIN_S):%m/%d %H:%M}까지 CEO·QC 자동 변경 없음), high_max "
               f"{policy.get('purchasing', 'high_max')} 그대로")
    s = sofa.rules()
    s.update(warranty=True, warranty_since=round(now), c_no_warranty=True, off_rule=SOFA_OFF,
             note=s.get("note", "") + f"; {WHY}: warranty on again (A / B 72 h, C none), off when 48 h refunds > 30% of "
                                      "48 h sofa profit")
    with open(sofa.RULES, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    out.append("소파 보증 다시 켬: A/B 72시간, C 없음. 자동 해제: 최근 48시간 소파 환불액 > 소파 이익의 30%")
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"t": now, "held": held}, f)
    for m in out:
        log("applied: " + m)
    return now, out


def report(t, now=None):
    from depts.substitute import catalog
    import cap30_rollout as c30
    import limits
    from cap20_again import causes
    from aggressive_rollout import mine_at, shop_window
    t1 = min(t + H * 3600, now or time.time())
    t0 = t - (t1 - t)
    h = max(1e-6, (t1 - t) / 3600)
    cat = catalog()
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        sa, sb = shop_window(db, t, t1, cat), shop_window(db, t0, t, cat)
    finally:
        db.close()
    oa, ob = alerts.sofa_numbers(t, t1), alerts.sofa_numbers(t0, t)
    (s1, _), (st_, _), (s0, _) = mine_at(t1), mine_at(t), mine_at(t0)
    per = lambda x, y, k: f"{(x[k] - y[k]) / h:+,.0f}" if x and y and x.get(k) is not None and y.get(k) is not None else "-"
    pf = lambda x: "-" if x is None else f"{x:.1%}"
    ka, kb = causes(t, t1), causes(t0, t)
    held = c30.held()
    try:
        start = json.load(open(STATE, encoding="utf-8"))["held"]
    except (OSError, ValueError, KeyError):
        start = held
    a = limits.auto_cap_rules()
    cap, ex, src = limits.stock_cap_now(held[4], held[0] + held[2])
    br = json.load(open(os.path.join(HERE, "cap_brake.json"), encoding="utf-8"))
    cb = (f"걸림 {datetime.fromtimestamp(br['fired']):%H:%M} -> 자동 계산 끄고 20M; " + "; ".join(br.get("fired_why", []))
          if br.get("fired") and br["fired"] >= t else f"안 걸림 (연속 {br.get('warn_streak', 0)}/{br.get('consecutive', 1)})")
    try:
        tc = json.load(open(os.path.join(HERE, "logs", "turnover_case.json"), encoding="utf-8"))
        oc = f"연속 {tc.get('streak', 0)}/{(a.get('case') or {}).get('consecutive', 1)}"
    except (OSError, ValueError):
        oc = "경고 없음"
    try:
        hf = json.load(open(os.path.join(HERE, "logs", "high_focus_state.json"), encoding="utf-8"))
        hb = ("걸림 " + hf["tripped"]) if hf.get("tripped") and (hf.get("tripped_at") or t) >= t else "안 걸림"
    except (OSError, ValueError):
        hb = "-"
    from depts import sofa
    so = sofa.rules()
    sw = "켜짐" if so.get("warranty") else f"꺼짐 ({so.get('warranty_off_reason')})"
    hi, hj = sa["high"], sb["high"]
    L = [f"재적용 {h:.1f}시간 점검: {datetime.fromtimestamp(t):%m-%d %H:%M} 적용, 후 vs 직전 같은 길이",
         f"- 시간당 Book {per(s1, st_, 'book')} vs {per(st_, s0, 'book')}, 시간당 매출 {per(s1, st_, 'revenue')} vs "
         f"{per(st_, s0, 'revenue')}, 가게 순위 {(st_ or {}).get('rank')} -> {(s1 or {}).get('rank')}",
         f"- 고가: 매입 {hi['buy_n']}개 {hi['buy_cost']:,} vs {hj['buy_n']}개 {hj['buy_cost']:,}, 판매 {hi['sell_n']}개 "
         f"{hi['rev']:,} vs {hj['sell_n']}개 {hj['rev']:,}, 이익률 {pf(hi['margin_rate'])} vs {pf(hj['margin_rate'])}; "
         f"고가 재고 {start[0]:,} {start[1]}개 -> {held[0]:,} {held[1]}개",
         f"- 소파 (보증 {sw}): 매출 {oa['revenue']:,} ({oa['sold']}개) vs {ob['revenue']:,} ({ob['sold']}개), 성사율 "
         f"{pf(oa['rate'])} ({oa['receipts']}/{oa['negotiations']}) vs {pf(ob['rate'])} ({ob['receipts']}/{ob['negotiations']}), "
         f"이익 {oa['profit']:,} vs {ob['profit']:,}, 보증 환불 {oa['refunds']}건 {oa['refund_amount']:,}",
         "- 막힌 매입 원인별 (후 vs 전): " + (", ".join(f"{k} {ka.get(k, 0)} vs {kb.get(k, 0)}" for k in
                                             sorted(set(ka) | set(kb), key=lambda k: -(ka.get(k, 0) + kb.get(k, 0)))) or "없음"),
         f"- 재고 원가 {start[0] + start[2]:,} -> {held[0] + held[2]:,}, 현금 {start[4]:,} -> {held[4]:,}; STOCK_CAP "
         f"{'자동' if src == 'auto' else '고정'} {cap:,} (나가는 기준 {ex:,})" + (", 현금 브레이크로 매입 중지 중" if a.get("paused") else ""),
         f"- 브레이크: STOCK_CAP {cb}; 과재고 판단 {oc}; 고가 {hb}"]
    try:                                       # 11/10 ~14:00 (person): hourly sales, normal stock, "not in stock"
        import sales_trend
        L.append(sales_trend.text(t1, 12))
        if sales_trend.request_lines_text():
            L.append(sales_trend.request_lines_text())
    except Exception as e:
        L.append(f"- 시간대별 판매·일반 재고 계산 실패 {type(e).__name__}: {e}")
    return "\n".join(L)


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    if "--report-only" in sys.argv:
        t = json.load(open(STATE, encoding="utf-8"))["t"]
    else:
        t, out = apply()
        print("\n".join(out))
        alerts.send("info", "reenable_1110_on", f"재적용 {datetime.fromtimestamp(t):%H:%M}:\n- " + "\n- ".join(out)
                    + "\n- 분석관 3시간 점검: 섞인 변경 있으면 보류, 없을 때만 자동 되돌리기. 3시간 점검 예정", log=log)
        if "--no-wait" in sys.argv:
            return
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"reenable_1110_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
