"""Hourly status push for the user (Round 3): class rank, how it moved, and when we could pass each player above.

    python summary.py            (watch_round.sh runs it every 15 min; it sends at most once per EVERY seconds)
    python summary.py --force    send now
    python summary.py --dry      print the message, send nothing, keep the state as it was

The goal is first place on student_total (the class ranking: shop + buyer scored among students only), so
this ranks on student_total, not on the open board's total. Each run keeps a snapshot of every student's
total in .market/summary_state.json (history, 48 h). The passing time for a player above us is
gap / (our gain per hour - their gain per hour), both measured over the last WINDOW (12 h) of snapshots;
when the gap is not closing it says so instead of giving a time.
"""
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / ".market" / "summary_state.json"
NAME = "yms979"
EVERY = 3600 - 120            # cron runs every 15 min; a little slack so it lands on the hour's run
WINDOW = int(2.5 * 3600)      # rates over the last ~2 h of snapshots (user 11-08: 6 h is too long; 2 h or 1 h)
MIN_SPAN = 1800               # and need at least 30 min of them
KEEP = 48 * 3600
ROUND_END = datetime(2026, 11, 17, 14, 59, tzinfo=timezone.utc)
KST = timezone(timedelta(hours=9))


def kst(t):
    return datetime.fromtimestamp(t, KST).strftime("%m/%d %H:%M")


def rate(hist, name, now):
    """Gain per hour of name's student_total over the last WINDOW of snapshots, or None if too little data."""
    pts = [(h["t"], h["totals"][name]) for h in hist if name in h["totals"] and now - h["t"] <= WINDOW]
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < MIN_SPAN:
        return None
    return (pts[-1][1] - pts[0][1]) / ((pts[-1][0] - pts[0][0]) / 3600)


def eta(gap, ours, theirs, now):
    if ours is None or theirs is None:
        return "속도 측정 중"
    close = ours - theirs
    if close <= 0:
        return f"현재 속도로는 못 따라잡음 (격차 {'벌어지는' if close < 0 else '그대로인'} 중)"
    t = now + gap / close * 3600
    if t > ROUND_END.timestamp():
        return "라운드 안에는 어려움 (현재 속도 기준)"
    return f"약 {gap / close:.0f}시간 뒤, {kst(t)} KST"


def first_place(b, cls, me, now):
    """What first place needs from the buyer, holding our shop score: the student_buyer gap to the leader, the
    saved that closes it (buyer = 0.5 x saved percentile among students + ..., so gap / 0.5 of percentile, read
    off the students' saved values), and when our saved gets there at the last 6 h of our own deals."""
    top = next(p for p in cls if p["player"] != NAME)
    need = top["student_total"] - me["student_shop"] + 0.001          # student_buyer that ties the leader
    gap = need - me["student_buyer"]
    if gap <= 0:
        return [f"★ 1등 조건 충족: 구매자 {me['student_buyer']:.3f} ≥ 필요 {need:.3f}"]
    buyers = {x["name"]: x for x in b["buyers"] if x.get("saved") is not None}
    names = {p["player"]: (p.get("buyer") or {}).get("name") for p in cls}
    vals = sorted(buyers[n]["saved"] for n in names.values() if n in buyers)
    u = buyers[NAME]
    pct = lambda v: (sum(q < v for q in vals) + 0.5 * sum(q == v for q in vals)) / len(vals)
    want = min(1.0, pct(u["saved"]) + gap / 0.5)
    target = next((v + 0.0005 for v in vals if pct(v + 0.0005) >= want), None)
    out = [f"★ 1등까지: 구매자 {me['student_buyer']:.3f} → {need:.3f} 필요 (+{gap:.3f}, 1위 {top['player']} {top['student_total']:.3f},"
           f" 상점 {me['student_shop']:.3f} 유지 가정)"]
    if target is None:
        return out + ["  saved만으로는 부족 (포럼/거래/글도 필요)"]
    import buyer_strategist as bs
    rows = [r for r in bs.trades(now - 6 * 3600) if r.get("outcome") == "deal" and r.get("saved") is not None]
    p, dph = (sum(r["saved"] for r in rows) / len(rows), len(rows) / 6) if rows else (None, 0)
    d = u["deals"] + 100
    line = f"  saved {u['saved']:.4f} → 목표 {target:.4f} (백분위 {pct(u['saved']):.2f} → {want:.2f})"
    if p is None or p <= target:
        return out + [line, f"  최근 거래당 {p or 0:.3f}로는 목표 saved에 못 미침: 거래당 saved를 올려야 함"]
    n = (target * d - u["saved"] * d) / (p - target)
    t = now + n / dph * 3600
    return out + [line, f"  최근 6시간 시간당 {dph:.0f}건 × 거래당 {p:.3f} → 약 {n / dph:.0f}시간 뒤 ({kst(t)} KST)"
                        f"{'' if t < ROUND_END.timestamp() else ', 라운드 종료 후'}; 다른 구매자들이 오르면 늦어짐"]


def races(b, st, now):
    """The shop race and the buyer race, each among students, each with its own 2nd and 3rd and the move since the
    last push (user 11-08: do not feel safe on the total; the 2nd shop and the 2nd overall are different players)."""
    stud = [p for p in b["players"] if p.get("student")]
    shops = {x["name"]: x for x in b["shops"]}
    prev = st.get("races", {})
    out, snap = [], {}
    for key, label in (("student_shop", "상점"), ("student_buyer", "구매자")):
        L = sorted((p for p in stud if p.get(key) is not None), key=lambda p: -p[key])
        r = next(i for i, p in enumerate(L) if p["player"] == NAME)
        me = L[r]
        old = prev.get(key, {})
        d = lambda p: (f" ({p[key] - old[p['player']]:+.3f})" if p["player"] in old else "")
        head = f"[{label}] 반 {r + 1}위 {me[key]:.3f}{d(me)}"
        if r == 0:
            sec = L[1:3]
            gap = me[key] - sec[0][key]
            oldgap = old.get(NAME, None) is not None and sec[0]["player"] in old and (old[NAME] - old[sec[0]["player"]])
            head += f" | 2위와 {gap:+.3f}" + (f" (지난 알림 {oldgap:+.3f})" if oldgap is not False else "")
            if oldgap is not False and gap < oldgap - 0.003:
                head += " ⚠️ 격차 감소"
        out.append(head)
        around = L[max(0, r - 2): r] + L[r + 1: r + 3] if r else L[1:3]
        for p in around:
            extra = ""
            if key == "student_shop":
                x = shops.get((p.get("shop") or {}).get("name"), {})
                extra = f" 장부 {(x.get('book') or 0) / 1e6:.0f}M 별점 {x.get('stars')}"
            out.append(f"  {L.index(p) + 1}위 {p['player']} {p[key]:.3f}{d(p)}{extra}")
        snap[key] = {p["player"]: p[key] for p in L}
    st["_races_new"] = snap
    return out


def main():
    st = json.loads(STATE.read_text()) if STATE.is_file() else {}
    now = time.time()
    if "--force" not in sys.argv and "--dry" not in sys.argv and now - st.get("t", 0) < EVERY:
        return
    with urllib.request.urlopen("https://market.ourolabs.ai/api/board?round=3", timeout=30) as r:
        b = json.load(r)
    cls = sorted((p for p in b["players"] if p.get("student")), key=lambda p: -(p.get("student_total") or 0))
    totals = {p["player"]: p["student_total"] for p in cls}
    rank = next(i + 1 for i, p in enumerate(cls) if p["player"] == NAME)
    me = cls[rank - 1]

    hist = [h for h in st.get("history", []) if now - h["t"] <= KEEP] + [{"t": now, "totals": totals}]
    ours = rate(hist, NAME, now)

    old_rank = st.get("class_rank")
    move = ("" if old_rank is None else f" ▲{old_rank - rank}" if rank < old_rank else
            f" ▼{rank - old_rank}" if rank > old_rank else " =")
    since = f" (지난 알림 {kst(st['t'])} 이후 {me['student_total'] - st['class_total']:+.3f})" if "class_total" in st else ""
    lines = [f"반 순위 {rank}위/{len(cls)}{move}, 합계 {me['student_total']:.3f}{since}",
             f"  상점 {me['student_shop']:.3f} + 구매자 {me['student_buyer']:.3f}"
             + (f", 최근 시간당 {ours:+.4f}" if ours is not None else "")]

    try:   # 11-07 user: shop is 1st, so first place comes from the buyer score; the push leads with that
        lines += first_place(b, cls, me, now)
    except Exception as e:
        lines.append(f"1등 계산 실패: {e}")

    try:   # 11-08 user: read the shop race and the buyer race apart; the 2nd shop is not the 2nd overall
        lines += races(b, st, now)
    except Exception as e:
        lines.append(f"상점/구매자 순위 계산 실패: {e}")

    lines.append("[전체] 위 순위와 따라잡는 시점:")
    for i, p in enumerate(cls[:rank - 1]):
        gap = p["student_total"] - me["student_total"]
        theirs = rate(hist, p["player"], now)
        sp = f", 시간당 {theirs:+.4f}" if theirs is not None else ""
        lines.append(f"  {i + 1}위 {p['player']} {p['student_total']:.3f} (차이 {gap:.3f}{sp}) → {eta(gap, ours, theirs, now)}")
    if rank < len(cls):
        nxt = cls[rank]
        lines.append(f"아래: {rank + 1}위 {nxt['player']} {nxt['student_total']:.3f} (우리보다 {me['student_total'] - nxt['student_total']:.3f} 아래)")

    prev = {n: i + 1 for i, n in enumerate(st.get("order", []))}
    moved = [f"{p['player']} {prev[p['player']]}→{i + 1}" for i, p in enumerate(cls[:max(rank + 1, 6)])
             if p["player"] in prev and prev[p["player"]] != i + 1]
    if moved:
        lines.append("순위 변동: " + ", ".join(moved))

    u = next((x for x in b["buyers"] if x["name"] == NAME), None)
    if u:
        lines.append(f"구매자 거래 {u['deals']}, 글 {u.get('posts') or 0} | 라운드 종료까지 {(ROUND_END.timestamp() - now) / 3600:.0f}시간")
    try:   # 11-07 buyer A/B (one more counter vs take the ask), over all shops: score gain per order decides
        import buyer_strategist as bs
        import memory_curator as mc
        ab = {x["key"]: x for x in mc.breakdown(bs, now)["push_ab"]}
        f = lambda x: f"{x['deals']}건/{x['orders']}주문, 거래당 {x['saved_per_deal']}, 주문당 점수 {x['score_gain_per_order']:+.4f}"
        for p, c, label in (("push3", "control2", "A/B3 더 깊게(p25-0.10, 2번)"), ("push2", "control2", "A/B2 첫 제시가도 깎기"),
                            ("push", "control", "A/B1 한번 더 깎기")):
            if p in ab and c in ab:
                lines.append(f"구매 {label}: {f(ab[p])} | 그대로: {f(ab[c])}")
        ca = {x["key"]: x for x in mc.breakdown(bs, now)["cheapfood_ab_food"]}
        if "cheapfood" in ca and "base" in ca:
            lines.append(f"구매 A/B 식품 싼 등급+낮게 시작: {f(ca['cheapfood'])} | 기존: {f(ca['base'])}")
        ga2 = {x["key"]: x for x in mc.breakdown(bs, now)["gradeaware_ab_goods"]}
        if "gradeaware" in ga2 and "base" in ga2:
            lines.append(f"구매 A/B 가구 등급별 협상: {f(ga2['gradeaware'])} | 기존: {f(ga2['base'])}")
        ka = {x["key"]: x for x in mc.breakdown(bs, now)["skubest_ab"]}
        if "skubest" in ka and "base" in ka:
            lines.append(f"구매 A/B 상품 선택(saved 기준): {f(ka['skubest'])} | 기존(최고가): {f(ka['base'])}")
        ga = {x["key"]: x for x in mc.breakdown(bs, now)["cheapgoods_ab_goods"]}
        if "cheapgoods" in ga and "base" in ga:
            lines.append(f"구매 A/B 가구 보증 C·B 우선: {f(ga['cheapgoods'])} | 기존: {f(ga['base'])}")
        wa = {x["key"]: x for x in mc.breakdown(bs, now)["wpen_ab_goods"]}
        if "wpen" in wa and "base" in wa:
            lines.append(f"구매 A/B 가구 보증 우선: {f(wa['wpen'])} | 기존: {f(wa['base'])} (+{wa['wpen']['minus_one']}/{wa['base']['minus_one']} 고장)")
    except Exception:
        pass
    try:   # 11-11 (user: the API bill): what the agents spent in the last 24 h and the credit left
        import model_env
        sp = model_env.spend_summary(now)
        roles = ", ".join(f"{k} ${v:.2f}" for k, v in list(sp["per_role"].items())[:4])
        lines.append(f"API 비용 24h ${sp['today']:.2f} ({sp['calls']}회: {roles}), 크레딧 약 ${sp['credit_left']:.0f} 남음")
    except Exception:
        pass
    msg = "\n".join(lines)

    if "--dry" in sys.argv:
        print(msg)
        return
    sent = subprocess.run([sys.executable, str(HERE / "notify.py"), f"[yms979] 반 {rank}위{move}", msg],
                          capture_output=True, text=True).stdout.strip()
    STATE.write_text(json.dumps({"t": now, "class_rank": rank, "class_total": me["student_total"],
                                 "races": st.get("_races_new", st.get("races", {})),
                                 "order": [p["player"] for p in cls], "history": hist}))
    print(msg + f"\n(push: {sent or 'none'})")


if __name__ == "__main__":
    main()
