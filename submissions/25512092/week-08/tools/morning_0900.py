"""11/10 09:00 summary (person's decision 11/09 ~23:55), one Telegram, read-only:
  1. urgent alerts overnight            2. brakes and safeguards that fired
  3. warnings that did not fire (2-in-a-row brakes, analyst check holds, frozen automatic policy changes)
  4. the scheduled checks: 00:15 aggressive, 02:12 high-price push, 02:27 D / high_accept, 03:25 shop order (6 h)
  5. shop Book / revenue per hour and rank; buyer saved (gap to 2nd), deals per hour, rank
  6. decisions needed
    python tools/morning_0900.py [--dry]      (waits until AT; --dry: now, printed, not sent)
"""
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)

AT = datetime(2026, 11, 10, 9, 0).timestamp()
T0 = datetime(2026, 11, 9, 23, 40).timestamp()
FIRED = ("cap_brake", "high_brake", "cash_pause", "cash_resume", "auto_reverted", "guard_revert")
WARNED = ("cap_brake_warn", "high_brake_warn", "overstock_warn", "check_hold", "check_no_revert", "frozen", "pinned")
REPORTS = (("00:15 공격적 변경 3시간", "aggressive_report"), ("02:12 고가 몰아주기 3시간", "highpush_report"),
           ("02:27 D·high_accept 6시간", "d_high_report"))


def alerts_in(t0, t1):
    out = []
    with open(os.path.join(HERE, "logs", "alerts.jsonl"), encoding="utf-8") as f:
        for ln in f:
            try:
                a = json.loads(ln)
            except ValueError:
                continue
            if t0 <= a.get("ts", 0) < t1:
                out.append(a)
    return out


def decisions_in(t0, t1, actions):
    out = []
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if any(f'"action": "{a}"' in ln for a in actions):
                d = json.loads(ln)
                if t0 <= d["ts"] < t1:
                    out.append(d)
    return out


def board_lines(now, cur=None, bad=None, board=None, db_path=None):
    """Board readings via tools/board_read.py: "now" = cur, else the live board, else the nearest snapshot; past points
    = the nearest snapshot (else logs/board_full.jsonl); a failed one shows "읽기 실패" and is added to bad."""
    import board_read as BR
    import board_speed as bs
    bad = [] if bad is None else bad
    if cur is None:
        cur = BR.now(board, db_path=db_path)
    if not cur.ok:
        bad.append("지금")
    L = []
    for h in (3, None):
        t = now - h * 3600 if h else T0
        p = BR.at(t, db_path=db_path)
        if not p.ok:
            ps, pb = (bs.hist_at(bs.FULL, "s", t) or {}).get(bs.ME), (bs.hist_at(bs.FULL, "b", t) or {}).get(bs.ME)
            p = BR.Point(t, ps, pb, "board_full") if ps or pb else p
        lab = f"최근 {h}시간" if h else f"밤사이 {(now - t) / 3600:.1f}시간"
        if not p.ok:
            bad.append(lab)
        f = lambda side, k: BR.show(BR.per_h(p, cur, side, k), "+,.0f")
        L.append(f"  {lab}: Book/h {f('s', 'book')}, 매출/h {f('s', 'revenue')}, deals/h {f('b', 'deals')}")
    g = lambda side, k, spec="": BR.show(cur.get(side, k), spec)
    second = BR.FAIL
    if cur.j is not None:
        B = [b for b in cur.j["buyers"] if b.get("all") is not None and b["name"] != bs.ME]
        second = BR.show(cur.get("b", "saved") - max(b["saved"] for b in B), "+.4f") if B and cur.get("b", "saved") \
            is not None else BR.FAIL
    L.insert(0, f"  가게 {g('s', 'rank')}위 (ALL {g('s', 'all')}, Book {g('s', 'book', ',')}, 매출 {g('s', 'revenue', ',')}); "
                f"buyer ALL {g('b', 'rank')}위 (ALL {g('b', 'all')}), saved {g('b', 'saved')} ({g('b', 'saved_rank')}위, "
                f"2위 차이 {second}), deals {g('b', 'deals', ',')} [{cur.src or BR.FAIL}]")
    if bad:
        L.append("  " + BR.fail_line(bad))
    return L


def build(now, cur=None, **kw):
    """(text, failed board readings) for board_read.send_with_retry."""
    bad = []
    return summary(now, cur=cur, bad=bad, **kw), bad


def summary(now, cur=None, bad=None, **kw):
    L = [f"09:00 아침 요약 {datetime.fromtimestamp(T0):%m-%d %H:%M} ~ {datetime.fromtimestamp(now):%m-%d %H:%M}"]
    A = alerts_in(T0, now)
    U = [a for a in A if a.get("level") == "urgent"]
    c = Counter(a["key"].split(":")[0] for a in U)
    L.append(f"1. 긴급 알림 {len(U)}건" + (": " + ", ".join(f"{k} {n}" for k, n in c.most_common(10)) if U else ""))
    for a in U[:6]:
        L.append(f"   {datetime.fromtimestamp(a['ts']):%H:%M} {a['text'][:140]}")
    F = decisions_in(T0, now, FIRED)
    stops = [a for a in U if a["key"].split(":")[0].endswith("_stop")]
    L.append(f"2. 발동한 브레이크·안전장치 {len(F) + len(stops)}건")
    for d in F[:8]:
        L.append(f"   {datetime.fromtimestamp(d['ts']):%H:%M} {d['action']} {d.get('target')}: {str(d.get('reason'))[:120]}")
    for a in stops[:6]:
        L.append(f"   {datetime.fromtimestamp(a['ts']):%H:%M} {a['key'].split(':')[0]}: {a['text'][:120]}")
    W = decisions_in(T0, now, WARNED)
    cw = Counter(d["action"] for d in W)
    L.append("3. 경고만 나고 발동 안 된 것 " + (", ".join(f"{k} {n}" for k, n in cw.items()) if W else "없음"))
    for d in [x for x in W if x["action"] not in ("frozen",)][:10]:
        L.append(f"   {datetime.fromtimestamp(d['ts']):%H:%M} {d['action']} {d.get('target')}: {str(d.get('reason'))[:130]}")
    fr = [d for d in W if d["action"] == "frozen"]
    if fr:
        L.append("   동결로 안 바뀐 CEO 자동 변경: " + ", ".join(sorted({f"{d['target']} -> {d['value']}" for d in fr}))[:300])
    L.append("4. 예약 점검")
    for lab, key in REPORTS:
        r = [a for a in A if a["key"].startswith(key)]
        L.append(f"   [{lab}] " + (r[-1]["text"][:600].replace("\n", "\n     ") if r else "아직 안 옴"))
    try:
        from depts import shop_order
        import shop_order_check
        t_on = shop_order.rules().get("since")
        L.append("   [03:25 가게 순서 6시간] " + shop_order_check.report(t_on, t_on + 6 * 3600).replace("\n", "\n     "))
    except Exception as e:
        L.append(f"   [03:25 가게 순서 6시간] 계산 실패 {type(e).__name__}")
    L.append("5. 보드")
    try:
        L += board_lines(now, cur=cur, bad=bad, **kw)
    except Exception as e:
        L.append(f"  보드 읽기 실패 {type(e).__name__}")
        if bad is not None:
            bad.append("보드")
    L.append("6. 결정 필요")
    L += [f"   - {x}" for x in needs(now)]
    return "\n".join(L)


def needs(now):
    out = ["밤 동안 완화한 브레이크 기준을 원래대로 돌릴지: STOCK_CAP 브레이크 중앙값 4시간(원래 3) / 미판매 35%·500만(원래 25%·300만) / "
           "2회 연속 발동(원래 1회); 고가 브레이크 영수증 1/3(원래 1/2) / 2회 연속; 자동 STOCK_CAP 과재고 판단 중앙값 4시간·"
           "미판매 35%·500만·2회 연속(원래 3시간·25%·1회)",
           "09:00에 풀린 것: 자동 정책 변경 동결, 분석관 3시간 점검 자동 되돌리기 꺼짐 (계속할지)",
           "승인 #39 HIGH_MAX 1.5M 24시간 고정은 11/10 23:40까지"]
    try:
        d = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
        try:
            ex = d.execute("SELECT id, key, value FROM proposals WHERE status='expired' AND ts>=?", (T0 - 3 * 3600,)).fetchall()
        finally:
            d.close()
        if ex:
            out.append("응답 없이 만료된 분석관 제안: " + ", ".join(f"#{i} {k} -> {v}" for i, k, v in ex))
    except Exception:
        pass
    H = decisions_in(T0, now, ("check_hold",))
    if H:
        out.append("판정 보류된 승인 점검: " + ", ".join(sorted({d["reason"].split(":")[0] + " " + d["target"] for d in H})))
    try:
        from depts import high_cost_exclude
        out.append(high_cost_exclude.line())
    except Exception:
        pass
    T = decisions_in(T0, now, ("request_trial_ok", "request_trial_drop"))
    if T:
        out.append("요청 기반 시험 결과: " + ", ".join(f"{d['target']} {'정식' if d['action'].endswith('ok') else '제외'}" for d in T))
    out += ["이전부터: C 30% 실험 여부, 공산품 단계 인하 12h vs 6h 시작, 고가 우선 매입 제안"]
    return out


def main():
    if "--dry" in sys.argv:
        print(summary(time.time()))
        return
    while time.time() < AT:
        time.sleep(min(60, max(1, AT - time.time())))
    import alerts
    try:
        import board_read as BR
        BR.send_with_retry(lambda cur: build(AT if cur is None else cur.ts, cur), alerts.send, "morning_0900:20261110")
    except Exception as e:
        alerts.send("urgent", "morning_0900_fail", f"09:00 요약 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
