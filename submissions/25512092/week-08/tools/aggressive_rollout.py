"""More aggressive (person's decision 11/09 ~21:20), applied at once:
  shop   high_max 60 -> 100 (policy + QC table + CEO / QC ceilings 0.7 / 100, tools/high_focus_rollout.set_high);
         HIGH_MAX (unit cost cap of high-price buying) 1.0M -> 1.5M by the analyst path (b0 check, commit + push);
         sofa limits x2 -> x3 (3 a window, 30 unsold, 3 lines, unit cost 1.5M; sofa_rules.json x3);
         the high brake goes back to 0.7 / 60 (tools/high_focus_rollout.py --resume, restarted separately).
         Unchanged: first ask <= market avg, the high-price buy guard, floors, overcost_high, C grade steps, G5e3784e6
         out, C grade sofas without a warranty, the brake conditions.
  buyer  list >= 200,000: accept up to x 1.10 (was 1.05) and one more soft haggle (high_accept.json factor 1.10,
         extra_haggles 1); the staged saved guard (2nd + 0.025 first switches this off) unchanged; the no-reply
         next-shop rule unchanged (no 30 s limit on it).
3 h later one Telegram (3 h after vs the 3 h before, the change time shown; the STOCK_CAP 40M of 19:23 and its 22:24
report overlap): shop revenue and Book, high / sofa buys, sales and margin, high sales >= 1.5M, cash and stock,
brakes; buyer deals and surplus per hour, D by first-shop reason, orders >= 200k close rate, saved and the 2nd.
    python tools/aggressive_rollout.py [--report-dry]          (log in logs/aggressive_rollout.log)
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

LOG = os.path.join(HERE, "logs", "aggressive_rollout.log")
STATE = os.path.join(HERE, "logs", "aggressive_state.json")
WHY = "person's decision 11/09 ~21:20: more aggressive"
H = 3
ME = "choo-chu"
SOFA_CAT = "소파/의자/스툴"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def board():
    from accept_watch import board as b
    return b()


def mine_at(t):
    """Via tools/board_read.py (the common board reading)."""
    import board_read
    return board_read.mine(t)


def shop_window(db, t0, t1, cat):
    """High-price and sofa buys / sales in [t0, t1), sales at >= 1.5M, units bought in the new 1.0-1.5M cost range."""
    from depts.qc import HIGH_MIN
    sofa = lambda k: ((cat.get(k) or {}).get("category") or "").split(">")[-1] == SOFA_CAT
    bought = db.execute("SELECT sku, cost FROM my_stock WHERE supplied_at>=? AND supplied_at<?", (t0, t1)).fetchall()
    sold = db.execute("SELECT s.sku, s.amount, m.cost FROM settles s LEFT JOIN my_stock m ON m.unit=s.unit "
                      "WHERE s.shop=? AND s.ts>=? AND s.ts<?", (ME, t0, t1)).fetchall()
    out = {}
    for name, keep in (("high", lambda k, c: (c or 0) > HIGH_MIN), ("sofa", lambda k, c: sofa(k))):
        b = [c for k, c in bought if keep(k, c)]
        s = [(a, c) for k, a, c in sold if keep(k, c)]
        rev = sum(a for a, _ in s)
        mar = sum(a - c for a, c in s if c)
        out[name] = {"buy_n": len(b), "buy_cost": sum(c or 0 for c in b), "sell_n": len(s), "rev": rev,
                     "margin_rate": mar / rev if rev else None}
    out["sold_15m"] = sum(1 for _, a, _ in sold if (a or 0) >= 1_500_000)
    out["bought_new_range"] = sum(1 for _, c in bought if 1_000_000 < (c or 0) <= 1_500_000)
    out["sold_new_range"] = sum(1 for _, _, c in sold if 1_000_000 < (c or 0) <= 1_500_000)
    return out


def buyer_window(t0, t1):
    import soft_neg_watch as w
    import surplus_bands
    import saved_guard_watch as gw
    from depts import soft_neg
    R = w._jsonl(soft_neg.LOG, t0, t1)
    D = [r for r in R if r.get("first_shop")]
    O = surplus_bands.orders(t0, t1)
    Hh = [r for r in O if (r["lp"] or r["budget"]) >= 200_000]
    HD = [r for r in Hh if r["deal"]]
    ha = [r for r in R if r.get("high_accept") and r.get("paid_ok")]
    return {"D": D, "by_reason": gw.by_reason(D), "h_n": len(Hh), "h_rate": len(HD) / len(Hh) if Hh else None,
            "h_surplus": sum(r["surplus"] for r in HD) / len(HD) if HD else None, "ha": len(ha)}


def report(t, now=None):
    from depts.substitute import catalog
    import cap30_rollout as c30
    from cap20_rollout import window
    t1 = min(t + H * 3600, now or time.time())
    t0 = t - (t1 - t)
    h = max(1e-6, (t1 - t) / 3600)
    cat = catalog()
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        a, b = window(db, t, t1), window(db, t0, t)
        sa, sb = shop_window(db, t, t1, cat), shop_window(db, t0, t, cat)
    finally:
        db.close()
    (s_now, b_now), (s_t, b_t), (s_0, b_0) = mine_at(t1), mine_at(t), mine_at(t0)
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    held = c30.held()
    start = st.get("held") or held
    pf = lambda x: "-" if x is None else f"{x:.1%}"
    d = lambda x, y, k: (x[k] - y[k]) if x and y and x.get(k) is not None and y.get(k) is not None else None
    f0 = lambda x: "-" if x is None else f"{x:+,.0f}"
    L = [f"공격적 변경 {H}시간 점검: 변경 {datetime.fromtimestamp(t):%m-%d %H:%M} 적용, {datetime.fromtimestamp(t):%H:%M}~"
         f"{datetime.fromtimestamp(t1):%H:%M} vs 직전 같은 길이 (19:23 STOCK_CAP 40M과 겹침)", "[shop]"]
    L.append(f"- 매출 {a['revenue']:,} vs {b['revenue']:,} (시간당 {a['revenue'] / h:,.0f} vs {b['revenue'] / h:,.0f}), Book 증가 "
             f"{f0(d(s_now, s_t, 'book'))} vs {f0(d(s_t, s_0, 'book'))}, 가게 순위 {(s_t or {}).get('rank')} -> {(s_now or {}).get('rank')}")
    for k, nm in (("high", "고가"), ("sofa", "소파")):
        x, y = sa[k], sb[k]
        L.append(f"- {nm}: 매입 {x['buy_n']}개 {x['buy_cost']:,} vs {y['buy_n']}개 {y['buy_cost']:,}, 판매 {x['sell_n']}개 {x['rev']:,} vs "
                 f"{y['sell_n']}개 {y['rev']:,}, 이익률(마진/매출) {pf(x['margin_rate'])} vs {pf(y['margin_rate'])}")
    L.append(f"- 150만 원 이상 고가 판매 {sa['sold_15m']}건 vs {sb['sold_15m']}건; 새 원가 구간(100만 초과~150만) 매입 "
             f"{sa['bought_new_range']}개, 판매 {sa['sold_new_range']}개")
    L.append(f"- 현금 {start[4]:,} -> {held[4]:,}, 재고 원가 {start[0] + start[2]:,} -> {held[0] + held[2]:,} (고가 {held[0]:,} "
             f"{held[1]}개 / 일반 {held[2]:,} {held[3]}개)")
    try:
        hf = json.load(open(os.path.join(HERE, "logs", "high_focus_state.json"), encoding="utf-8"))
        last = [ln for ln in open(os.path.join(HERE, "logs", "high_focus_rollout.log"), encoding="utf-8") if " check " in ln][-1:]
        hb = ("걸림 " + hf["tripped"]) if hf.get("tripped") else ("안 걸림; 마지막 " + (last[0].strip() if last else "-"))
    except (OSError, ValueError):
        hb = "-"
    br = json.load(open(os.path.join(HERE, "cap_brake.json"), encoding="utf-8"))
    cb = ("걸림 " + datetime.fromtimestamp(br["fired"]).strftime("%H:%M") + " " + "; ".join(br.get("fired_why", []))
          if br.get("fired") and br["fired"] >= t else "안 걸림")
    L.append(f"- 브레이크: 고가 {hb} (걸리면 0.7/60); STOCK_CAP {cb}")
    L.append("[buyer]")
    ba, bb_ = buyer_window(t, t1), buyer_window(t0, t)
    L.append(f"- 시간당 deals {f0(d(b_now, b_t, 'deals') / h if d(b_now, b_t, 'deals') is not None else None)} vs "
             f"{f0(d(b_t, b_0, 'deals') / h if d(b_t, b_0, 'deals') is not None else None)}, 시간당 surplus "
             f"{f0(d(b_now, b_t, 'surplus') / h if d(b_now, b_t, 'surplus') is not None else None)} vs "
             f"{f0(d(b_t, b_0, 'surplus') / h if d(b_t, b_0, 'surplus') is not None else None)}")
    L.append(f"- D: 시도 {len(ba['D'])}건, 성사 {sum(1 for r in ba['D'] if r.get('deal'))}건 (직전 {len(bb_['D'])}건 / "
             f"{sum(1 for r in bb_['D'] if r.get('deal'))}건)\n{ba['by_reason']}")
    L.append(f"- 20만 이상 주문 {ba['h_n']}건 성사율 {pf(ba['h_rate'])} (직전 {bb_['h_n']}건 {pf(bb_['h_rate'])}), 건당 surplus "
             f"{'-' if ba['h_surplus'] is None else f'{ba['h_surplus']:,.0f}'}; 시장 평균 넘겨 성사(x1.10까지) {ba['ha']}건")
    try:
        bn = board()
        b0 = st.get("board") or {}
        L.append(f"- saved {b0.get('saved')} -> {bn['saved']}, 2위 {b0.get('second')} -> {bn['second']}, 차이 "
                 f"{bn['saved'] - bn['second']:.4f} (고가 완화 끄는 기준 2위 + 0.025, D + 0.015, soft_neg + 0.008), saved "
                 f"{bn['saved_rank']}위, ALL {bn['all_rank']}위")
    except Exception as e:
        L.append(f"- saved: 보드 읽기 실패 {type(e).__name__}")
    return "\n".join(L)


def apply():
    import cap30_rollout as c30
    import high_focus_rollout as hf
    from depts import analyst, analyst_apply, high_accept, sofa
    out = []
    now = time.time()
    b = board()
    # 1. HIGH_MAX 1.0M -> 1.5M, the analyst path (b0 check, commit + push)
    d = analyst.db()
    try:
        analyst_apply._cols(d)
        cur = d.execute("INSERT INTO proposals (ts,kind,key,value,reason,evidence,status,expires,decided_ts,result,asked,"
                        "checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)", (now, "limit", "limits.HIGH_MAX", json.dumps(1_500_000),
                                                                      WHY, "person", "approved", now, now, "applying", 1))
        pid = cur.lastrowid
        d.commit()
    finally:
        d.close()
    try:
        res = analyst_apply.apply_limit("limits.HIGH_MAX", 1_500_000, pid, why="person's decision 11/09 ~21:20")
        analyst_apply._store(pid, res)
        d = analyst.db()
        d.execute("UPDATE proposals SET result=? WHERE id=?", (res["text"], pid))
        d.commit()
        d.close()
        decisions.record("analyst", "approved_by_person", "limits.HIGH_MAX", 1_500_000, f"#{pid}: {WHY} | {res['text']}")
        out.append(f"HIGH_MAX 1.0M -> 1.5M (#{pid}, {res['text'][:120]})")
    except Exception as e:
        d = analyst.db()
        d.execute("UPDATE proposals SET status='failed', result=? WHERE id=?", (str(e)[:300], pid))
        d.commit()
        d.close()
        out.append(f"HIGH_MAX 1.5M 실패, 1.0M 그대로 ({type(e).__name__}: {str(e)[:120]})")
    # 2. high_max 100, share 0.7, ceilings 0.7 / 100
    old, v = hf.set_high(0.7, 100, "person 11/09 ~21:20 (more aggressive): high_max 60 -> 100, ceilings 0.7 / 100")
    out.append(f"high_max {old[1]} -> 100, high_share {old[0]} -> 0.7 (policy v{v}, CEO·QC 자동 상한 0.7/100)")
    # 3. sofa x3
    r = sofa.rules()
    r.update(x3=True, x3_since=round(time.time()))
    with open(sofa.RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    decisions.record("purchasing", "sofa_x3", "sofa_rules", sofa.limits_now(r), "person 11/09 ~21:20: sofa limits x3")
    out.append("소파 한도 3배: 창당 3개, 미판매 30개, 줄 3, 단위 원가 150만")
    # 4. buyer: list >= 200k x 1.10 and one more haggle
    ha = high_accept.rules()
    ha.update(factor=1.10, extra_haggles=1, aggressive_since=round(time.time()))
    with open(high_accept.RULES, "w", encoding="utf-8") as f:
        json.dump(ha, f, ensure_ascii=False)
    decisions.record("buyer", "high_accept_110_on", high_accept.TAG, {"factor": 1.10, "extra_haggles": 1}, WHY)
    out.append("buyer 정가 20만 이상: 수락 시장 평균 x 1.10까지 + 흥정 1회 추가"
               + ("" if high_accept.on(ha) else " (단, high_accept가 지금 꺼져 있어 적용 안 됨)"))
    t = time.time()
    json.dump({"t": t, "held": c30.held(), "board": b}, open(STATE, "w", encoding="utf-8"))
    log("applied: " + " | ".join(out))
    return t, out


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    if "--report-only" in sys.argv:
        t = json.load(open(STATE, encoding="utf-8"))["t"]
    else:
        t, out = apply()
        print("\n".join(out))
        alerts.send("info", "aggressive_on", f"공격적 변경 적용 {datetime.fromtimestamp(t):%H:%M}:\n- " + "\n- ".join(out)
                    + "\n- 고가 브레이크 복귀값 0.7/60 (조건 그대로), 무응답 가게 다음 시도는 지금처럼(30초 상한 없음)\n"
                    "첫 호가·고가 가드·하한·overcost·C등급 단계 인하·우아미 제외·C등급 소파 보증 없음·saved 단계형 안전장치 그대로. "
                    "3시간 점검 예정", log=log)
        if "--no-wait" in sys.argv:
            return
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"aggressive_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
