"""soft_neg 6 h check (person's decision 11/09 ~17:00): at the experiment start + 6 h (20:27) send one report, soft_neg vs
the current way (close rate, time per negotiation, orders and deals per hour, saving per deal, saved change), and widen
soft_neg to 100% of the orders on the spot (soft_neg.json share 1.0, read per order) only if all hold:
  saved >= SAVED_OK and first on saved; soft close rate >= current - RATE_TOL (11/09 ~17:10, was "higher");
  soft time per order (kit, order to next order) < current (11/09 ~17:10, new); soft saving per deal >= SAVE_OK
The report also has orders and deals per hour of both ways together vs the same length before the start, and a second
message: the order speed analysis (tools/order_speed2.py, analysis only).
Any one missing (or too few records): stays 50%. The guard in tools/soft_neg_watch.py (stop at saved < 0.397 or not
first) keeps running unchanged; it reads soft_neg.json fresh, so it also stops the widened run.
    python tools/soft_neg_mid.py [--dry]          (log in logs/soft_neg_watch.log)
"""
import os
import sys
import time
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
from depts import soft_neg  # noqa: E402
import soft_neg_watch as w  # noqa: E402

MID_H = 6
SAVED_OK, SAVE_OK, RATE_TOL = 0.41, 0.35, 0.02


def decide(s, c, b):
    """(widen?, [reasons not met])"""
    miss = []
    if b["saved"] < SAVED_OK:
        miss.append(f"saved {b['saved']} < {SAVED_OK}")
    if b["saved_rank"] != 1:
        miss.append(f"saved {b['saved_rank']}위")
    if not s or not c:
        miss.append("기록 부족")
    else:
        if s["rate"] < c["rate"] - RATE_TOL - 1e-9:
            miss.append(f"성사율 {s['rate']:.1%} < 기존 {c['rate']:.1%} - {RATE_TOL * 100:.0f}%p")
        if s.get("kit") is None or c.get("kit") is None or not s["kit"] < c["kit"]:
            miss.append(f"주문 1건(다음 주문까지) soft {s.get('kit')}초 >= 기존 {c.get('kit')}초")
        if s["save"] is None or s["save"] < SAVE_OK:
            got = "-" if s["save"] is None else f"{s['save']:.1%}"
            miss.append(f"soft 건당 절약률 {got} < {SAVE_OK:.0%}")
    return not miss, miss


def arms(t0, t1):
    R = w._jsonl(soft_neg.LOG, t0, t1)
    cyc = w.kit_cycles(t0, t1)
    h = max(1e-6, (t1 - t0) / 3600)
    return (w.arm_stats([r for r in R if r["arm"] == "soft"], cyc, h),
            w.arm_stats([r for r in R if r["arm"] == "control"], cyc, h))


def regular_text(now):
    """Before / after the 20 s limit for regular shops (buyer_speed.json regular_since, 11/09 ~17:20): close rate of the
    orders (soft_neg.jsonl, both arms) overall and at regular shops, and slow_shop_skip decisions (regular / other)."""
    import json
    from depts import buyer_speed
    r = buyer_speed.rules()
    ta = r.get("regular_since")
    if not ta:
        return "- 단골 가게 20초 제한: 적용 기록 없음"
    regs = set(buyer_speed.regulars(now=now))
    tb = max(r.get("since") or 0, ta - (now - ta))              # same length before, with the 10 s rule on
    out = []
    for a, b in ((tb, ta), (ta, now)):
        R = w._jsonl(soft_neg.LOG, a, b)
        g = [x for x in R if x.get("shop") in regs]
        sk = Counter()
        with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
            for ln in f:
                if '"slow_shop_skip"' in ln:
                    e = json.loads(ln)
                    if a <= e["ts"] < b:
                        sk["regular" if e["target"] in regs else "other"] += 1
        h = max(1e-6, (b - a) / 3600)
        rate = lambda X: f"{sum(1 for x in X if x['deal']) / len(X):.1%} ({len(X)}건)" if X else "-"
        out.append(f"성사율 전체 {rate(R)}, 단골 가게 {rate(g)}; skip 단골 {sk['regular']}건 / 그 외 {sk['other']}건 "
                   f"(시간당 {sk['regular'] / h:.1f} / {sk['other'] / h:.1f})")
    return (f"- 단골 가게(24시간 성사 20건 이상: {', '.join(sorted(regs))}) 카드·첫 응답 제한 10초 → 20초, "
            f"{datetime.fromtimestamp(ta):%H:%M} 적용\n  적용 전 ({datetime.fromtimestamp(tb):%H:%M}~"
            f"{datetime.fromtimestamp(ta):%H:%M}): {out[0]}\n  적용 후 (~{datetime.fromtimestamp(now):%H:%M}): {out[1]}")


def chunks(text, n=3800):
    """Split at line ends into parts of at most n characters (a longer single line is cut)."""
    out, cur = [], ""
    for ln in text.split("\n"):
        while len(ln) > n:
            if cur:
                out.append(cur)
                cur = ""
            out.append(ln[:n])
            ln = ln[n:]
        if cur and len(cur) + 1 + len(ln) > n:
            out.append(cur)
            cur = ln
        else:
            cur = f"{cur}\n{ln}" if cur else ln
    if cur:
        out.append(cur)
    return out


def apply_d_high(b, now, decisions):
    """11/09 ~17:40 (person): with soft_neg at 100%, switch on plan D (second_shop.json) and high_accept_105
    (high_accept.json), unless the staged guard would switch one off at once; tools/saved_guard_watch.py guards them
    and reports 6 h later. Returns the text for the message."""
    import json
    from depts import high_accept, saved_guard, second_shop
    out = []
    for key, mod, extra, name in (("second_shop", second_shop, {}, "D 2번째 가게(second_shop)"),
                                  ("high_accept", high_accept, {"min_list": high_accept.MIN_LIST, "factor": high_accept.FACTOR},
                                   "비싼 주문 수락 완화(high_accept_105: 정가 20만 이상, 시장 평균 x 1.05까지)")):
        why = saved_guard.why(key, b)
        if why:
            out.append(f"{name}: 적용 안 함, 안전장치 기준에 이미 걸림 ({why})")
            decisions.record("buyer", f"{mod.TAG}_skip", mod.TAG, b, f"not switched on at 20:27: {why}")
            continue
        r = dict(mod.rules(), enabled=True, since=round(now), applied_by="soft_neg_mid", board=b, stopped=None,
                 stop_reason=None, **extra)
        with open(mod.RULES, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False)
        decisions.record("buyer", f"{mod.TAG}_on", mod.TAG, b, "person 11/09 ~17:40: on with soft_neg at 100%")
        out.append(f"{name}: 적용")
    return ("이어서 적용 (" + datetime.fromtimestamp(now).strftime("%H:%M") + "): " + "; ".join(out)
            + ". 안전장치: 2위 saved + 0.025 미만이면 고가 완화 끔, + 0.015 미만이면 D 끔 (30분마다). 6시간 뒤 점검 보고")


def b0_of(t0):
    """The board at the start, from the watcher's 'on, board {...}' log line."""
    import ast
    with open(w.LOG, encoding="utf-8") as f:
        for ln in f:
            if " on, board {" in ln:
                b = ast.literal_eval(ln.split(" on, board ", 1)[1].strip())
    return b


def main():
    r = soft_neg.rules()
    t0 = r.get("since")
    dry = "--dry" in sys.argv
    at = t0 + MID_H * 3600
    if not dry:
        while time.time() < at:
            time.sleep(min(300, max(5, at - time.time())))
    r = soft_neg.rules()
    import alerts
    import decisions
    if not soft_neg.on(r=r):
        w.log(f"6h check: soft_neg not on (stopped {r.get('stopped')}, {r.get('stop_reason')}), nothing to do")
        if not dry:
            alerts.send("info", f"soft_neg_mid:{int(t0)}", f"soft_neg 6시간 점검: 이미 꺼져 있음 ({r.get('stop_reason')}), "
                        "확대 안 함. D(2번째 가게)·비싼 주문 수락 완화(high_accept_105)도 적용하지 않음", log=w.log)
        return
    now = time.time()
    b0, b1 = b0_of(t0), w.board()
    s, c = arms(t0, now)
    ok, miss = decide(s, c, b1)
    text = w.report(t0, b0, b1, now=now).replace("결과", "중간 점검", 1)
    oh = w.orders_per_h(t0, now)
    text += (f"\n- 전체(두 방식 합계) 시간당 주문 {oh['orders'][0]} vs 시작 전 같은 길이 "
             f"({datetime.fromtimestamp(2 * t0 - now):%H:%M}~{datetime.fromtimestamp(t0):%H:%M}) {oh['orders'][1]}, "
             f"시간당 deals {oh['deals'][0]} vs {oh['deals'][1]}")
    try:
        text += "\n" + regular_text(now)
    except Exception as e:
        text += f"\n- 단골 가게 20초 제한 전후: 계산 실패 {type(e).__name__}"
    if ok:
        text += ("\n조건 모두 충족 (saved >= 0.41·1위, 성사율 soft >= 기존 - 2%p, 주문 1건(다음 주문까지) soft < 기존, "
                 "soft 건당 절약률 >= 35%): soft_neg 주문 100%로 확대. 안전장치(2위 saved + 0.008 미만 또는 1위 상실 시 중지), "
                 "24시간 끝(11/10 14:27)은 그대로")
    else:
        text += ("\n조건 미충족 (" + "; ".join(miss) + "): 50% 유지. 안전장치(2위 saved + 0.008 미만 또는 1위 상실 시 중지) 그대로."
                 "\nD(2번째 가게)·비싼 주문 수락 완화(high_accept_105)·그 6시간 점검은 적용하지 않음 (확대 조건)")
    try:
        import order_speed2
        speed = order_speed2.report(t0, now, saved=b1["saved"], deals_total=b1["deals"])
    except Exception as e:
        speed = f"buyer 주문 속도 2차 분석 실패: {type(e).__name__}: {e}"
    try:
        import surplus_bands                          # 11/09 ~17:25: surplus by list price band, analysis only
        surplus = surplus_bands.report(now, saved=b1["saved"], deals_total=b1["deals"])
    except Exception as e:
        surplus = f"buyer surplus 분석 실패: {type(e).__name__}: {e}"
    if dry:
        print(text + "\n\n" + speed + "\n\n" + surplus)
        return
    if ok:
        w.set_rule(share=1.0, widened=round(now))
        decisions.record("buyer", "soft_neg_widen", "soft_neg", b1, "person 11/09 ~17:00: 6 h check all met -> 100% of orders")
        text += "\n" + apply_d_high(b1, now, decisions)
    else:
        decisions.record("buyer", "soft_neg_keep", "soft_neg", b1, "6 h check not met: " + "; ".join(miss))
    alerts.send("info", f"soft_neg_mid:{int(t0)}", text, log=w.log)
    alerts.send("info", f"order_speed2:{int(t0)}", speed, log=w.log)
    for i, part in enumerate(chunks(surplus)):        # Telegram cuts at 4,000 characters
        alerts.send("info", f"surplus_bands:{int(t0)}:{i}", part, log=w.log)
    w.log(f"6h check sent, widen={ok} {miss}")


if __name__ == "__main__":
    main()
