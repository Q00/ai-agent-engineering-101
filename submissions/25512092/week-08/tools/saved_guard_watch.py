"""Saved guard watcher (person's decision 11/11 ~10:30, depts/saved_guard.py): every CHECK_S reads the board and, for
high_accept, second_shop (D), soft_neg and the accept rule, switches off below its absolute saved threshold and on
again at or above its higher one (only what this guard switched off), urgent Telegram + decision each time; no rank
condition. Earlier: staged by the 2nd (11/09) / 3rd (11/10) best saved, off only (soft_neg and the accept rule in their
own watchers, tools/soft_neg_watch.py and tools/accept_watch.py, which ended when they switched off).
Also: shop_order's guard (tools/shop_order_check.py), and one D / expensive-order report REPORT_H after soft_neg_mid.
    python tools/saved_guard_watch.py [--report-dry]          (log in logs/saved_guard.log)
"""
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
from depts import accept_rule, high_accept, saved_guard, second_shop, soft_neg  # noqa: E402

CHECK_S, REPORT_H = 1800, 6
LOG = os.path.join(HERE, "logs", "saved_guard.log")
STATE = os.path.join(HERE, "logs", "saved_guard_state.json")
FEATURES = (("high_accept", high_accept, "비싼 주문 수락 기준 완화(high_accept 1.10)"),
            # 11/12 ~16:20 (person): D (second_shop) is no longer switched off for saved (its deals save more)
            ("soft_neg", soft_neg, "soft_neg"),                       # 11/11 ~10:30 (person): all four here, with
            ("accept_rule", accept_rule, "수락 규칙(상시, 시장 평균 100%)"))   # switching on again (depts/saved_guard.py)


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f)


def set_rules(mod, **kw):
    r = mod.rules()
    r.update(kw)
    with open(mod.RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)


def board():
    """accept_watch's summary, plus (11/12 ~16:20) student_second: the best saved of the other student buyers (the board
    Players table's student flag; the midterm ranks students only)."""
    import httpx
    from accept_watch import board as b
    j = httpx.get("https://market.ourolabs.ai/api/board", params={"round": 3}, timeout=30).json()
    out = b(j)
    out["student_second"] = student_second(j)
    out["student_below"] = student_below(j, out["saved"])
    return out


def student_below(j, mine, me="choo-chu"):
    """11/12 ~18:30: the best saved among the other student buyers that is not above mine (the one just below me)."""
    stud = {(p.get("buyer") or {}).get("name") for p in j.get("players") or [] if p.get("student")}
    v = [x["saved"] for x in j.get("buyers") or [] if x["name"] in stud and x["name"] != me
         and x.get("saved") is not None and x["saved"] <= mine]
    return max(v) if v else None


def student_second(j, me="choo-chu"):
    stud = {(p.get("buyer") or {}).get("name") for p in j.get("players") or [] if p.get("student")}
    v = [x["saved"] for x in j.get("buyers") or [] if x["name"] in stud and x["name"] != me and x.get("saved") is not None]
    return max(v) if v else None


def a_guard(b, alerts, decisions, now=None, path=None):
    """11/12 ~18:30 (person): grade_rule.json a_as_bc by my saved vs the student just below me (depts/saved_guard.a_switch);
    each change: the rules file, a decision and a Telegram. Returns the new value or None."""
    from depts import grade_rule
    now = now or time.time()
    path = path or grade_rule.RULES
    r = grade_rule.rules(path)
    if r.get("a_as_bc_locked"):                     # 11/13 ~13:20 (person): A switch off and never on again
        return None
    sw = saved_guard.a_switch(b, bool(r.get("a_as_bc")))
    if sw is None:
        return None
    val, why = sw
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    raw.update(a_as_bc=val, a_as_bc_ts=round(now), a_as_bc_why=why)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)
    decisions.record("buyer", "grade_a_as_bc", "grade_rule.json", val, why)
    alerts.send("urgent", f"grade_a_as_bc:{int(now)}", "buyer A등급 " + ("B·C 기준으로 전환 (첫 제안 0.65, 즉시 수락 1.00)"
                if val else "원래 기준으로 복귀 (첫 제안 0.75, 즉시 수락 1.05)") + f": {why}", log=log)
    log(f"a_as_bc -> {val} ({why})")
    return val


def on_kw(key, now):
    """The rules to switch `key` on again (soft_neg: 100% of orders to the round end)."""
    kw = dict(enabled=True, stopped=None, stop_reason=None, guard_off=False, guard_on=round(now))
    if key == "soft_neg":
        from limits import ROUND_END
        kw.update(until=round(ROUND_END), share=1.0, ended=None)
    if key == "accept_rule":
        kw.update(until=None, ended=None, mode="always")
    return kw


def check(b, alerts, decisions, now=None):
    """11/11 ~10:40 (person): absolute saved thresholds, raised over the 4th, off and on again (depts/saved_guard.py); no rank condition.
    Returns [(name, 'off' / 'on')]."""
    now = now or time.time()
    out = []
    for key, mod, name in FEATURES:
        r = mod.rules()
        if mod.on():
            why = saved_guard.why(key, b)
            if not why:
                continue
            set_rules(mod, enabled=False, stopped=round(now), stop_reason=why, guard_off=True)
            decisions.record("buyer", f"{mod.TAG}_stop", mod.TAG, b, why)
            alerts.send("urgent", f"{mod.TAG}_stop:{int(now)}", f"buyer {name} 끔: {why} (기준 {saved_guard.text(key, b)})", log=log)
            log(f"{key} off: {why}")
            out.append((key, "off"))
        elif r.get("guard_off"):
            why = saved_guard.back(key, b)
            if not why:
                continue
            set_rules(mod, **on_kw(key, now))
            decisions.record("buyer", f"{mod.TAG}_on", mod.TAG, b, why + " - on again (saved guard)")
            alerts.send("urgent", f"{mod.TAG}_on:{int(now)}", f"buyer {name} 다시 켬: {why} (기준 {saved_guard.text(key, b)})", log=log)
            log(f"{key} on again: {why}")
            out.append((key, "on"))
    return out


def shop_order_check(alerts, decisions, now=None):
    """11/09 ~21:30: switch only shop_order's two rules off (stock_first, rate_order) when the close rate or deals per
    hour drop (tools/shop_order_check.guard); everything else stays."""
    from depts import shop_order
    import shop_order_check as so
    why = so.guard(now)
    if not why:
        return None
    if alerts.brakes_warn_only():                      # 11/10 ~15:00 (person, auto mode): a warning only, rules stay
        st = state()
        if time.time() - st.get("shop_order_warned", 0) >= 3600:
            st["shop_order_warned"] = round(time.time())
            save_state(st)
            decisions.record("buyer", f"{shop_order.TAG}_warn", shop_order.TAG, None, why + " - warning only (auto mode)")
            alerts.send("info", f"{shop_order.TAG}_warn:{int(time.time())}", f"buyer 가게 순서 중단 조건 경고 (자동 모드: 유지): {why}",
                        log=log)
        return None
    r = shop_order.rules()
    r.update(stock_first=False, rate_order=False, stopped=round(now or time.time()), stop_reason=why)
    with open(shop_order.RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    decisions.record("buyer", f"{shop_order.TAG}_stop", shop_order.TAG, r.get("base"), why)
    alerts.send("urgent", f"{shop_order.TAG}_stop", f"buyer 가게 순서(재고 없음 뒤로·성사율 순) 끔: {why}. 나머지는 유지", log=log)
    log(f"shop_order off: {why}")
    return why


FLOOR = 20.6                                   # the order spacing floor (tools/order_speed2.py)


def d_effect(R, cyc, h):
    """D's cost and gain (11/09 ~18:00 person): time D added to its orders (2nd shop: order time - the first shop's
    negotiation - ~0.5 s of pre-steps), the cycle (order to next order) with D vs without (the cycle without the 2nd
    try, never under the ~20 s floor), and deals per hour net of the orders lost to the longer cycles (lost orders x the
    first-shop close rate of the other orders). Returns text lines."""
    D = [r for r in R if r.get("first_shop")]
    if not D:
        return ["- D 시간·순증: 시도 없음"]
    added = {r["order"]: max(0.0, (r.get("order_secs") or 0) - ((r["first_shop"] or {}).get("secs") or 0) - 0.5) for r in D}
    cd = [(cyc[o], a) for o, a in added.items() if o in cyc]
    saved_t = sum(c - max(FLOOR, c - a) for c, a in cd)
    allc = list(cyc.values())
    mean_c = sum(allc) / len(allc) if allc else FLOOR
    others = [r for r in R if not r.get("first_shop")]
    p = sum(1 for r in others if r.get("deal")) / len(others) if others else 0
    lost_orders = saved_t / mean_c
    d_deals = sum(1 for r in D if r.get("deal"))
    soft_other = [cyc[r["order"]] for r in others if r.get("arm") == "soft" and r["order"] in cyc]
    f1 = lambda x: f"{x:.1f}"
    return [f"- D로 늘어난 주문 1건 처리 시간: D 주문 평균 +{f1(sum(added.values()) / len(added))}초 (2번째 가게), 다음 주문까지 "
            f"D 주문 {f1(sum(c for c, _ in cd) / len(cd)) if cd else '-'}초 vs 다른 soft 주문 "
            f"{f1(sum(soft_other) / len(soft_other)) if soft_other else '-'}초; 20초 바닥을 넘겨 실제로 늘어난 시간 합 {saved_t:.0f}초 "
            f"(전체 주문 평균 +{saved_t / max(1, len(allc)):.2f}초)",
            f"- D가 없었을 때 대비 시간당 deals 순증: D 성사 {d_deals / h:+.1f}/h - 늘어난 시간 때문에 못 받은 주문 "
            f"{lost_orders / h:.1f}/h x 첫 가게 성사율 {p:.0%} = {(d_deals - lost_orders * p) / h:+.1f}/h"]


WHY = {"walk": "가격", "turn_limit": "가격", "seller_ended": "끊김", "no_reply": "무응답", "time_limit": "무응답"}


def by_reason(D):
    """11/09 ~20:00 (person): D tries and close rate by how the first shop ended (가격 / 재고 / 끊김 / 무응답).
    Entries before 20:00 have no "why": seller_ended counts as 끊김 then (no stock not told apart)."""
    g = {}
    for r in D:
        f = r.get("first_shop") or {}
        k = f.get("why") or WHY.get(f.get("ended"), f.get("ended") or "?")
        g.setdefault(k, []).append(bool(r.get("deal")))
    if not g:
        return "  첫 가게 종료 이유별: 없음"
    order = ["가격", "재고", "끊김", "무응답"]
    keys = [k for k in order if k in g] + sorted(k for k in g if k not in order)
    return "  첫 가게 종료 이유별 D 시도 / 성사율: " + ", ".join(
        f"{k} {len(g[k])}건 / {sum(g[k]) / len(g[k]):.0%}" for k in keys)


def report(t0, b0, b1, t1=None):
    import soft_neg_watch as w
    import surplus_bands
    t1 = t1 or time.time()
    h = max(1e-6, (t1 - t0) / 3600)
    R = w._jsonl(soft_neg.LOG, t0, t1)
    D = [r for r in R if r.get("first_shop")]
    Dd = [r for r in D if r.get("deal") and r.get("paid_ok") and r.get("price") and r.get("list_price")]
    pct = lambda x: f"{x:.1%}" if x is not None else "-"
    L = [f"buyer D·고가 수락 {h:.1f}시간 점검 ({datetime.fromtimestamp(t0):%m-%d %H:%M} ~ {datetime.fromtimestamp(t1):%H:%M})"]
    stops = [f"{n} {datetime.fromtimestamp(m.rules()['stopped']):%H:%M} 꺼짐({m.rules().get('stop_reason')})"
             for _, m, n in FEATURES if m.rules().get("stopped") and m.rules()["stopped"] >= t0]
    if stops:
        L.append("- 안전장치로 꺼짐: " + "; ".join(stops))
    L.append(f"- D 2번째 가게: 시도 {len(D)}건 (soft 주문 {sum(r['arm'] == 'soft' for r in R)}건 중), 성사 "
             f"{sum(1 for r in D if r.get('deal'))}건, 결제 {len(Dd)}건, 성사율 "
             f"{pct(sum(1 for r in D if r.get('deal')) / len(D) if D else None)}, 건당 절약률(정가 대비) "
             f"{pct(sum((r['list_price'] - r['price']) / r['list_price'] for r in Dd) / len(Dd) if Dd else None)}")
    L.append(by_reason(D))
    try:
        L += d_effect(R, w.kit_cycles(t0, t1), h)
    except Exception as e:
        L.append(f"- D 시간·순증: 계산 실패 {type(e).__name__}")
    try:
        O = surplus_bands.orders(t0, t1)
        H = [r for r in O if (r["lp"] or r["budget"]) >= 200_000]
        HD = [r for r in H if r["deal"]]
        ha = [r for r in R if r.get("high_accept") and r.get("paid_ok")]
        L.append(f"- 비싼 주문(정가 20만 이상): {len(H)}건, 성사율 {pct(len(HD) / len(H) if H else None)}, 건당 surplus "
                 f"{(sum(r['surplus'] for r in HD) / len(HD)) if HD else 0:,.0f}, high_accept_105로 성사(시장 평균 초과 가격) "
                 f"{len(ha)}건 (그 surplus {sum(r['list_price'] - r['price'] for r in ha if r.get('list_price') and r.get('price')):,})")
    except Exception as e:
        L.append(f"- 비싼 주문: 계산 실패 {type(e).__name__}")
    oh = w.orders_per_h(t0, t1)
    L.append(f"- 전체 시간당 주문 {oh['orders'][0]} vs 직전 같은 길이 {oh['orders'][1]}, 시간당 deals {oh['deals'][0]} vs "
             f"{oh['deals'][1]}")
    if b0:
        L.append(f"- surplus +{b1['surplus'] - b0['surplus']:,} (시간당 {(b1['surplus'] - b0['surplus']) / h:,.0f}), saved "
                 f"{b0['saved']} -> {b1['saved']}, 2위 saved {b0['second']} -> {b1['second']} (차이 "
                 f"{b1['saved'] - b1['second']:.4f}; 끄는/켜는 기준 saved 고가 0.395/0.400, D 0.390/0.395, soft_neg·수락 0.383/0.388, 4위 > 0.375면 4위 + 0.018/0.013/0.008), deals "
                 f"{b0['deals']} -> {b1['deals']}")
    return "\n".join(L)


def main():
    if "--report-dry" in sys.argv:
        b = board()
        print(report(time.time() - 3 * 3600, b, b))
        return
    import alerts
    import decisions
    from limits import ROUND_END
    log(f"start (11/12 ~18:30: fixed thresholds removed; A as B/C when saved < student below + {saved_guard.A_BELOW}, back at "
        f"+ {saved_guard.A_BACK})")
    while time.time() < ROUND_END:
        st = state()
        ss = second_shop.rules()
        t_on = ss.get("since") if ss.get("applied_by") == "soft_neg_mid" else None
        if t_on and not st.get(f"report:{int(t_on)}") and time.time() >= t_on + REPORT_H * 3600:
            try:
                b1 = board()
                alerts.send("daily", f"d_high_report:{int(t_on)}", report(t_on, ss.get("board"), b1), log=log)
                log("6h report sent")
            except Exception as e:
                log(f"report failed: {type(e).__name__}: {e}")
            st[f"report:{int(t_on)}"] = round(time.time())
            save_state(st)
        try:                                       # 11/11 ~10:30: every time (switching on again too)
            b = board()
            log(f"check {b}")
            a_guard(b, alerts, decisions)               # 11/12 ~18:30: the only saved rule (fixed thresholds removed)
        except Exception as e:
            log(f"check failed: {type(e).__name__}: {e}")
        try:
            shop_order_check(alerts, decisions)
        except Exception as e:
            log(f"shop_order check failed: {type(e).__name__}")
        nxt = CHECK_S
        if t_on and not st.get(f"report:{int(t_on)}"):
            nxt = max(5, min(CHECK_S, t_on + REPORT_H * 3600 - time.time()))
        time.sleep(nxt)


if __name__ == "__main__":
    main()
