"""soft_neg 24 h experiment (person's decision 11/09 ~14:40, depts/soft_neg.py): switches it on (soft_neg.json, read
per order), checks the board every CHECK_S and switches it off at once (urgent Telegram) when my buyer's saved falls
under SAVED_MIN or it is no longer first on saved; after 24 h (or the stop) switches it off and reports soft_neg vs
the current way: close rate, time per negotiation, time per order (buyer process and the kit's order to next order),
orders and deals per hour, saving per deal (vs list), saved change, the share the shop ended; and the accept rule
(100%) fires in the window vs the replay baseline (close 48.2%).
11/09 ~17:40 (person): the guard is staged by the 2nd best saved (depts/saved_guard.py): off when my saved < 2nd +
0.008 or not first (was saved < 0.397 or not first). --resume picks up a running experiment (since / until from
soft_neg.json, the start board from this log) without switching anything on.
    python tools/soft_neg_watch.py [--report-dry | --resume]          (log in logs/soft_neg_watch.log)
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
from depts import accept_rule, soft_neg  # noqa: E402

CHECK_S, H = 1800, 24
SAVED_MIN = 0.397
SIM_BASE = 0.482
LOG = os.path.join(HERE, "logs", "soft_neg_watch.log")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def board():
    from accept_watch import board as b
    return b()


def _jsonl(path, t0, t1):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    e = json.loads(ln)
                    if t0 <= e["ts"] < t1:
                        out.append(e)
    except (OSError, ValueError):
        pass
    return out


def kit_cycles(t0, t1):
    """{order id: seconds from its kit order line to the next one} in [t0, t1); each log file dated back from its mtime."""
    pts = []
    for name in ("market-kit.log.1", "market-kit.log"):
        p = os.path.join(HERE, "logs", name)
        if not os.path.exists(p):
            continue
        with open(p, "rb") as f:
            f.seek(max(0, os.path.getsize(p) - 60_000_000))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        day = datetime.fromtimestamp(os.path.getmtime(p)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        prev = None
        for ln in reversed(lines):
            m = re.match(r"(\d\d):(\d\d):(\d\d) \[order (o-[0-9a-f]+)\]", ln)
            if not m:
                continue
            t = day + int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
            if prev is not None and t > prev + 3600:
                day -= 86400
                t -= 86400
            prev = t
            if t0 - 600 <= t < t1 + 600:
                pts.append((t, m[4]))
    pts = sorted(set(pts))
    return {o: b - a for (a, o), (b, _) in zip(pts, pts[1:]) if b - a < 600 and t0 <= a < t1}


def arm_stats(R, cyc, hours):
    n = len(R)
    if not n:
        return None
    deals = [r for r in R if r["deal"]]
    paid = [r for r in deals if r.get("paid_ok") and r.get("price") and r.get("list_price")]
    mean = lambda xs: round(statistics.mean(xs), 1) if xs else None
    c = [cyc[r["order"]] for r in R if r.get("order") in cyc]
    return {"orders": n, "deals": len(deals), "rate": len(deals) / n,
            "neg": mean([r["neg_secs"] for r in R if r.get("neg_secs")]),
            "order": mean([r["order_secs"] for r in R if r.get("order_secs")]), "kit": mean(c), "kit_n": len(c),
            "orders_h": n / hours, "deals_h": len(deals) / hours,
            "save": statistics.mean((r["list_price"] - r["price"]) / r["list_price"] for r in paid) if paid else None,
            "save_sum": sum((r["list_price"] - r["price"]) / r["list_price"] for r in paid),
            "shop_end": sum(1 for r in R if r.get("shop_ended")) / n,
            "accept_rule": sum(1 for r in R if r.get("accept_rule")),
            "accept_rule_deals": sum(1 for r in R if r.get("accept_rule") and r["deal"]),
            "soft_used": sum(1 for r in R if r.get("soft_used"))}


def orders_per_h(t0, t1):
    """My orders and deals per hour in [t0, t1) and in the same length before (board_hist)."""
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        def at(t):                                # the common board reading (tools/board_read.py)
            import board_read
            return board_read.mine(t)[1]
        a, b, c = at(t0 - (t1 - t0)), at(t0), at(t1)
    finally:
        db.close()
    h = (t1 - t0) / 3600
    f = lambda x, y, k: round((y[k] - x[k]) / h, 1) if x and y else None
    return {"orders": (f(b, c, "orders"), f(a, b, "orders")), "deals": (f(b, c, "deals"), f(a, b, "deals"))}


def report(t0, b0, b1, stopped=None, now=None):
    t1 = now or time.time()
    h = max(1e-6, (t1 - t0) / 3600)
    R = _jsonl(soft_neg.LOG, t0, t1)
    cyc = kit_cycles(t0, t1)
    s = arm_stats([r for r in R if r["arm"] == "soft"], cyc, h)
    c = arm_stats([r for r in R if r["arm"] == "control"], cyc, h)
    acc = _jsonl(accept_rule.LOG, t0, t1)
    oh = orders_per_h(t0, t1)
    pct = lambda x: f"{x:.1%}" if x is not None else "-"
    num = lambda x: f"{x:.1f}" if x is not None else "-"
    L = [f"buyer soft_neg {'중단' if stopped else f'{h:.1f}시간'} 결과 ({datetime.fromtimestamp(t0):%m-%d %H:%M} ~ "
         f"{datetime.fromtimestamp(t1):%m-%d %H:%M}, soft_neg vs 기존, {share_text()})" + (f": {stopped}" if stopped else "")]
    if not s or not c:
        L.append(f"- 기록 부족 (soft {sum(r['arm'] == 'soft' for r in R)}건, 기존 {sum(r['arm'] == 'control' for r in R)}건)")
    else:
        L += [f"- 주문 {s['orders']} vs {c['orders']}건 (soft 규칙 실제 사용 {s['soft_used']}건), 성사 {s['deals']} vs {c['deals']}, "
              f"성사율 {pct(s['rate'])} vs {pct(c['rate'])}",
              f"- 협상 1건 {num(s['neg'])} vs {num(c['neg'])}초; 주문 1건 처리(buyer) {num(s['order'])} vs {num(c['order'])}초, "
              f"kit 기준 다음 주문까지 {num(s['kit'])} vs {num(c['kit'])}초 ({s['kit_n']}/{c['kit_n']}건)",
              f"- 시간당 주문 {num(s['orders_h'])} vs {num(c['orders_h'])}, 시간당 deals {num(s['deals_h'])} vs {num(c['deals_h'])} "
              f"(전체 시간당 주문 {oh['orders'][0]} vs 직전 같은 길이 {oh['orders'][1]}, deals {oh['deals'][0]} vs {oh['deals'][1]})",
              f"- 건당 절약률(정가 대비, 결제된 건) {pct(s['save'])} vs {pct(c['save'])}; 절약률 합 {s['save_sum']:.1f} vs {c['save_sum']:.1f}",
              f"- 가게가 먼저 끝낸 비율 {pct(s['shop_end'])} vs {pct(c['shop_end'])}",
              f"- 수락 규칙(100%) 실제 발동: 전체 {len(acc)}건, 그중 성사 {sum(1 for a in acc if a.get('deal'))}건 "
              f"(soft 주문 {s['accept_rule']}건, 기존 주문 {c['accept_rule']}건 / 성사 {c['accept_rule_deals']}); "
              f"기존 쪽 실제 성사율 {pct(c['rate'])} vs 시뮬레이션 기준선 {SIM_BASE:.1%}"]
    L.append(f"- saved {b0['saved']} -> {b1['saved']} (2위 {b0['second']} -> {b1['second']}), saved 순위 {b0['saved_rank']} -> "
             f"{b1['saved_rank']}, deals {b0['deals']} -> {b1['deals']}, ALL {b0['all']} ({b0['all_rank']}위) -> {b1['all']} "
             f"({b1['all_rank']}위)")
    return "\n".join(L)


def share_text():
    r = soft_neg.rules()
    w = r.get("widened")
    return f"주문 50:50, {datetime.fromtimestamp(w):%H:%M}부터 soft 100%" if w else f"주문 soft {r.get('share', 0.5):.0%}"


def set_rule(**kw):
    r = soft_neg.rules()
    r.update(kw)
    with open(soft_neg.RULES, "w", encoding="utf-8") as f:
        json.dump(r, f)


def guard(b):
    from depts import saved_guard
    return saved_guard.why("soft_neg", b)


def main():
    if "--report-dry" in sys.argv:
        b = board()
        print(report(time.time() - 3600, b, b))
        return
    import alerts
    import decisions
    if "--resume" in sys.argv:
        resume(alerts, decisions)
        return
    t0 = time.time()
    b0 = board()
    set_rule(enabled=True, since=round(t0), until=round(t0 + H * 3600), share=soft_neg.SHARE, first=soft_neg.FIRST,
             haggles=soft_neg.HAGGLES, accept=soft_neg.ACCEPT, low=soft_neg.LOW, stopped=None, ended=None)
    decisions.record("buyer", "soft_neg_on", "soft_neg", b0, "person 11/09 ~14:40: 24 h, half the orders: first offer 75% "
                     "of the market average, accept an ask <= the average, at most 2 offers, code only; stop at saved < "
                     "0.397 or not first on saved")
    alerts.send("info", "soft_neg_on", f"buyer soft_neg 24시간 실험 시작 {datetime.now():%H:%M}: 주문 50%에 첫 제안 시장 평균 x 0.75, "
                "호가 <= 평균이면 즉시 수락, 흥정 최대 2회 (코드, LLM 0회). 나머지 50%는 기존 방식. 등급·영수증·인젝션·커튼 규칙 그대로. "
                f"시작 saved {b0['saved']} (2위 {b0['second']}). 30분마다 확인, saved < {SAVED_MIN} 또는 saved 1위 상실이면 즉시 끔",
                log=log)
    log(f"on, board {b0}")
    loop(t0, b0, t0 + H * 3600, alerts, decisions)


def start_board():
    import ast
    b = None
    with open(LOG, encoding="utf-8") as f:
        for ln in f:
            if " on, board {" in ln:
                b = ast.literal_eval(ln.split(" on, board ", 1)[1].strip())
    return b


def resume(alerts, decisions):
    r = soft_neg.rules()
    if not soft_neg.on(r=r):
        log("resume: soft_neg not on, nothing to watch")
        return
    t0, end = r["since"], r["until"]
    log(f"resumed (guard: 3rd + 0.006, depts/saved_guard.py), since {datetime.fromtimestamp(t0):%m-%d %H:%M}, "
        f"until {datetime.fromtimestamp(end):%m-%d %H:%M}")
    loop(t0, start_board(), end, alerts, decisions)


def loop(t0, b0, end, alerts, decisions):
    while time.time() < end:
        time.sleep(min(CHECK_S, max(5, end - time.time())))
        try:
            b = board()
        except Exception as e:
            log(f"board failed: {type(e).__name__}")
            continue
        log(f"check {b}")
        why = guard(b)
        if why:
            set_rule(enabled=False, stopped=round(time.time()), stop_reason=why)
            decisions.record("buyer", "soft_neg_stop", "soft_neg", b, why)
            alerts.send("urgent", "soft_neg_stop", f"buyer soft_neg 즉시 중단: {why}\n" + report(t0, b0, b, stopped=why), log=log)
            log(f"stopped: {why}")
            return
    set_rule(enabled=False, ended=round(time.time()))
    b1 = board()
    decisions.record("buyer", "soft_neg_end", "soft_neg", b1, "until reached, off; keep it or not is the person's decision")
    alerts.send("daily", f"soft_neg_report:{int(t0)}", report(t0, b0, b1) + "\n24시간 끝: soft_neg 꺼짐. 계속할지는 사람 결정",
                log=log)
    log("report sent")


if __name__ == "__main__":
    main()
