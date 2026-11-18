"""High-price push (person's decision 11/09 ~23:10): apply, then one 3 h report.
  apply   STOCK_CAP automatic (cash + stock - 3M every run, exit 88%; stock_cap_auto.json, limits.stock_cap_now); the
          cap brake restarts from now (its stock trips turn the automatic cap off, back to 20M; its cash trip only
          pauses buying until cash > 3M); high_share 0.7 -> 0.85 with the CEO / QC ceilings 0.85 / 100 (the high-price
          brake still goes back to 0.7 / 60); request-based buying 2 lines, only SKUs selling within 1 h (code, already
          in depts/request_buy.py). The perishable rules and the 11/15 end cap are unchanged.
  report  3 h after vs the 3 h before: Book and revenue per hour, high-price buys / sales / margin, the cheap units'
          share of revenue, stars, blocked buying by cause, stock and cash, brakes.
    python tools/highpush_rollout.py [--report-dry | --report-only | --no-wait]
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

LOG = os.path.join(HERE, "logs", "highpush_rollout.log")
STATE = os.path.join(HERE, "logs", "highpush_state.json")
WHY = "person's decision 11/09 ~23:10: high-price push"
H = 3
ME = "choo-chu"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def cheap_share(db, t0, t1):
    from depts.qc import HIGH_MIN
    rows = db.execute("SELECT s.amount, m.cost FROM settles s LEFT JOIN my_stock m ON m.unit=s.unit "
                      "WHERE s.shop=? AND s.ts>=? AND s.ts<?", (ME, t0, t1)).fetchall()
    tot = sum(a or 0 for a, _ in rows)
    cheap = sum(a or 0 for a, c in rows if (c or 0) <= HIGH_MIN)
    return (cheap / tot if tot else None), tot


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
        (ca_, ra), (cb_, rb) = cheap_share(db, t, t1), cheap_share(db, t0, t)
    finally:
        db.close()
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
          if br.get("fired") and br["fired"] >= t else "안 걸림")
    try:
        hf = json.load(open(os.path.join(HERE, "logs", "high_focus_state.json"), encoding="utf-8"))
        hb = ("걸림 " + hf["tripped"]) if hf.get("tripped") and (hf.get("tripped_at") or t) >= t else "안 걸림"
    except (OSError, ValueError):
        hb = "-"
    hi, hj = sa["high"], sb["high"]
    L = [f"고가 몰아주기 {h:.1f}시간 점검: {datetime.fromtimestamp(t):%m-%d %H:%M} 적용, 후 vs 직전 같은 길이",
         f"- 시간당 Book {per(s1, st_, 'book')} vs {per(st_, s0, 'book')}, 시간당 매출 {per(s1, st_, 'revenue')} vs "
         f"{per(st_, s0, 'revenue')}, 가게 순위 {(st_ or {}).get('rank')} -> {(s1 or {}).get('rank')}",
         f"- 고가: 매입 {hi['buy_n']}개 {hi['buy_cost']:,} vs {hj['buy_n']}개 {hj['buy_cost']:,}, 판매 {hi['sell_n']}개 "
         f"{hi['rev']:,} vs {hj['sell_n']}개 {hj['rev']:,}, 이익률(마진/매출) {pf(hi['margin_rate'])} vs {pf(hj['margin_rate'])}",
         f"- 싼 물건(원가 10만 이하) 매출 비중 {pf(ca_)} vs {pf(cb_)} (전체 매출 {ra:,} vs {rb:,})",
         f"- 별점 {(st_ or {}).get('stars')} -> {(s1 or {}).get('stars')}, 순위용 별점 {(st_ or {}).get('stars_rank')} -> "
         f"{(s1 or {}).get('stars_rank')}",
         "- 막힌 매입 원인별 (후 vs 전): " + (", ".join(f"{k} {ka.get(k, 0)} vs {kb.get(k, 0)}" for k in
                                             sorted(set(ka) | set(kb), key=lambda k: -(ka.get(k, 0) + kb.get(k, 0)))) or "없음"),
         f"- 재고 원가 {start[0] + start[2]:,} -> {held[0] + held[2]:,} (고가 {held[0]:,} {held[1]}개 / 일반 {held[2]:,} "
         f"{held[3]}개), 현금 {start[4]:,} -> {held[4]:,}",
         f"- STOCK_CAP {'자동' if src == 'auto' else '고정'} {cap:,} (나가는 기준 {ex:,})"
         + (", 현금 브레이크로 매입 중지 중" if a.get("paused") else ""),
         f"- 브레이크: STOCK_CAP {cb}; 고가 {hb} (걸리면 0.7/60)"]
    return "\n".join(L)


def apply():
    import cap30_rollout as c30
    import limits
    import policy
    from depts import cap_brake, high_caps
    now = time.time()
    out = []
    a = {"enabled": True, "since": round(now), "reserve": 3_000_000, "exit_ratio": 0.88, "resume_cash": 3_000_000,
         "paused": False, "why": WHY + ": cap = cash + stock - 3M every run, STOCK_EXIT 88%; cash brake pauses buying "
                                      "until cash > 3M; the stock brakes go back to 20M; the 11/15 end cap first"}
    held = c30.held()
    cap = int(held[4] + held[0] + held[2] - a["reserve"])
    r = cap_brake.rules()
    r.update(enabled=True, since=round(now), pid=None,
             why=r.get("why", "") + "; 11/09 ~23:10 automatic cap (cash + stock - 3M): stock trips -> auto off, 20M; "
                                    "cash trip -> buying paused until cash > 3M")
    with open(cap_brake.RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    with open(limits.AUTO_CAP_PATH, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=1)
    with open(os.path.join(HERE, "limits.py"), "a", encoding="utf-8") as f:
        f.write(f"STOCK_CAP_CHANGES.append(({now:.0f}, {cap:_}))   # {datetime.now():%m/%d %H:%M} automatic cap on "
                f"(cash + stock - 3M; person 11/09 ~23:10)\n")
    out.append(f"STOCK_CAP 자동: 현금 {held[4]:,} + 재고 원가 {held[0] + held[2]:,} - 300만 = {cap:,} (나가는 기준 88% "
               f"{int(cap * 0.88):,}), 매 run 다시 계산")
    policy.write({"purchasing": {"high_share": 0.85}}, WHY + ": high_share 0.7 -> 0.85, ceilings 0.85 / 100", by="person")
    v = policy.version()
    high_caps.set_caps(0.85, 100, WHY + ": ceilings 0.85 / 100 (the high-price brake goes back to 0.7 / 60)")
    out.append(f"high_share 0.7 -> {policy.get('purchasing', 'high_share')} (policy v{v}), CEO·QC 자동 상한 0.85/100")
    out.append("요청 기반 매입 run당 4줄 -> 2줄, 회전 1시간 이하 SKU만 (목표 수량 8 그대로)")
    json.dump({"t": now, "held": held}, open(STATE, "w", encoding="utf-8"))
    for m in out:
        log("applied: " + m)
    return now, out


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    if "--report-only" in sys.argv:
        t = json.load(open(STATE, encoding="utf-8"))["t"]
    else:
        t, out = apply()
        print("\n".join(out))
        alerts.send("info", "highpush_on", f"고가 몰아주기 적용 {datetime.fromtimestamp(t):%H:%M}:\n- " + "\n- ".join(out)
                    + "\n- 브레이크: 판매 중앙값·미판매 조건은 걸리면 자동 계산 끄고 20M, 현금은 300만 넘을 때까지 매입만 중지; "
                    "11/15 막판 예약 우선. 고가 브레이크 복귀값 0.7/60, 부패주의 규칙 그대로. 3시간 점검 예정", log=log)
        if "--no-wait" in sys.argv:
            return
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"highpush_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
