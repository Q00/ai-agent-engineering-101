"""11/11 09:00 summary of the unattended run (person's decision 11/10 ~15:00), one Telegram, read-only:
  1. shop Book / revenue / rank every 3 h overnight     2. buyer ALL / saved (rank, gap to 2nd and 3rd) / deals every 3 h
  3. urgent alerts                                         4. warnings and info held from Telegram (auto mode), by kind
  5. decisions needed
    python tools/auto_0900.py [--dry]      (waits until AT; --dry: now, printed, not sent)
"""
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)

T0 = datetime(2026, 11, 10, 15, 30).timestamp()
AT = datetime(2026, 11, 11, 9, 0).timestamp()
STEP_H = 3
WARNED = ("cap_brake_warn", "high_brake_warn", "overstock_warn", "check_hold", "check_no_revert", "frozen", "pinned",
          "subst_exp_stop_warn", "shop_order_warn")


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


def trend(now):
    import board_speed as bs
    L = ["1. 가게 추이 (시각: 순위, Book, 매출)"]
    B = ["2. buyer 추이 (시각: ALL 순위, ALL, saved(순위), deals)"]
    t = T0
    pts = []
    while t < now:
        pts.append(t)
        t += STEP_H * 3600
    pts.append(now)
    prev = None
    for t in pts:
        s, b = bs.mine_at(t)
        lab = datetime.fromtimestamp(t).strftime("%m-%d %H:%M")
        if s:
            d = f" (+{s['book'] - prev[0]:,} / +{s['revenue'] - prev[1]:,})" if prev else ""
            L.append(f"   {lab}: {s.get('rank')}위, Book {s['book']:,}, 매출 {s['revenue']:,}{d}")
            prev = (s["book"], s["revenue"])
        if b:
            B.append(f"   {lab}: {b.get('rank')}위, ALL {b.get('all')}, saved {b.get('saved')} ({b.get('saved_rank')}위), "
                     f"deals {b.get('deals'):,}")
    try:
        j = bs.board()
        sv = sorted((x for x in j["buyers"] if x.get("all") is not None), key=lambda x: -x["saved"])
        me = next(i for i, x in enumerate(sv) if x["name"] == bs.ME)
        top = ", ".join(f"{i + 1}. {x['name']} {x['saved']:.4f}" for i, x in enumerate(sv[:4]))
        B.append(f"   지금 saved 1~4위: {top} (우리 {me + 1}위; 3위 기준 안전장치 고가 +0.020 / D +0.012 / soft_neg·수락 +0.006)")
    except Exception as e:
        B.append(f"   지금 보드 읽기 실패 {type(e).__name__}")
    return L + B


def held_lines(A):
    H = [a for a in A if a.get("telegram") == "held"]
    c = Counter(a["key"].split(":")[0] for a in H)
    L = [f"4. 경고·정보 모음 (텔레그램 보류 {len(H)}건): " + (", ".join(f"{k} {n}" for k, n in c.most_common(15)) or "없음")]
    seen = set()
    for a in reversed(H):                          # the latest of each kind
        k = a["key"].split(":")[0]
        if k in seen or len(seen) >= 10:
            continue
        seen.add(k)
        L.append(f"   {datetime.fromtimestamp(a['ts']):%H:%M} {k}: {a['text'][:160]}")
    import morning_0900 as M
    W = M.decisions_in(T0, AT + 3600, WARNED)
    if W:
        L.append("   경고만 나고 발동 안 된 것: " + ", ".join(f"{k} {n}" for k, n in Counter(d["action"] for d in W).items()))
    return L


def needs(now):
    out = ["자동 모드 끝 (11/11 09:00): 자동 정책 변경 동결, 분석관 자동 되돌리기 꺼짐, 텔레그램 긴급만, 남은 브레이크 경고만 -> 계속할지",
           "HIGH_MAX 150만·high_share 0.7·high_max 100 고정은 11/11 12:00까지"]
    try:
        import sqlite3
        d = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
        try:
            ex = d.execute("SELECT id, key, value FROM proposals WHERE status='expired' AND ts>=?", (T0,)).fetchall()
        finally:
            d.close()
        if ex:
            out.append("응답 없이 만료된 분석관 제안: " + ", ".join(f"#{i} {k} -> {v}" for i, k, v in ex))
    except Exception:
        pass
    try:
        with open(os.path.join(HERE, "logs", "watchdog_state.json"), encoding="utf-8") as f:
            st = json.load(f)
        r = {k: len(v) for k, v in (st.get("restarts") or {}).items() if v}
        if r or st.get("stopped"):
            out.append(f"프로세스 재시작: {r or '없음'}" + (f", 재시작 멈춤: {list(st['stopped'])}" if st.get("stopped") else ""))
    except (OSError, ValueError):
        pass
    try:
        from depts import high_cost_exclude
        out.append(high_cost_exclude.line())
    except Exception:
        pass
    out.append("이전부터: C 30% 실험 여부, 공산품 단계 인하 12h vs 6h 시작, 고가 우선 매입 제안")
    return out


def summary(now):
    A = alerts_in(T0, now)
    U = [a for a in A if a.get("level") == "urgent"]
    L = [f"09:00 자동 운영 요약 {datetime.fromtimestamp(T0):%m-%d %H:%M} ~ {datetime.fromtimestamp(now):%m-%d %H:%M}"]
    try:
        L += trend(now)
    except Exception as e:
        L.append(f"1~2. 보드 추이 실패 {type(e).__name__}")
    c = Counter(a["key"].split(":")[0] for a in U)
    L.append(f"3. 긴급 알림 {len(U)}건" + (": " + ", ".join(f"{k} {n}" for k, n in c.most_common(10)) if U else ""))
    for a in U[-8:]:
        L.append(f"   {datetime.fromtimestamp(a['ts']):%H:%M} {a['text'][:150]}")
    try:
        L += held_lines(A)
    except Exception as e:
        L.append(f"4. 경고 모음 실패 {type(e).__name__}")
    L.append("5. 결정 필요")
    L += [f"   - {x}" for x in needs(now)]
    return "\n".join(L)


def main():
    if "--dry" in sys.argv:
        print(summary(time.time()))
        return
    while time.time() < AT:
        time.sleep(min(60, max(1, AT - time.time())))
    import alerts
    try:
        alerts.send("daily", "auto_0900:20261111", summary(AT))
    except Exception as e:
        alerts.send("urgent", "auto_0900_fail", f"09:00 자동 운영 요약 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
