"""Buyer order speed, 2nd analysis (person's request 11/09 ~17:10; analysis only, changes nothing): from the kit log
(logs/market-kit.log*) and logs/soft_neg.jsonl, per order of the soft_neg window:
  - the time of one order by step, soft_neg deals vs fails (current way for reference): kit start, pre-steps (memory,
    stats, blocked shops), shop and SKU pick, shop connection (agent card), trade window (first message + first reply),
    waiting for the shop, my decisions, result and payment (stars -> ledger), and the wait for the next order
  - failed orders: one shop or several, and how they end (no stock, price, timeout / no answer, the shop ended it)
  - 3 options with estimated orders / deals per hour and saved effect, all-soft_neg basis:
      A quit early (first ask > X x market avg -> walk), B shop order by speed x close rate x price, C pre-step cache,
      and D (found here) a 2nd shop in the time left under the market's 20 s order spacing
The order cycle has a floor of ~20 s (no cycle under 19 s; the kit sleeps on the market's 429 for /api/orders and does
not log it), so the cycle is modelled as max(FLOOR, busy + GAP), calibrated on the data.
    python tools/order_speed2.py [hours]
"""
import json
import os
import re
import statistics as st
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOOR, GAP = 20.6, 1.6
STOCK = re.compile(r"not in stock|none of that|out of stock|sold out|no stock|품절|재고", re.I)


def segments(t0, t1):
    segs = []
    for name in ("market-kit.log.1", "market-kit.log"):
        p = os.path.join(HERE, "logs", name)
        if not os.path.exists(p):
            continue
        with open(p, "rb") as f:
            f.seek(max(0, os.path.getsize(p) - 40_000_000))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        day = datetime.fromtimestamp(os.path.getmtime(p)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        out, prev = [], None
        for ln in reversed(lines):                      # date back from the file's mtime
            m = re.match(r"(\d\d):(\d\d):(\d\d) (.*)", ln)
            if not m:
                continue
            t = day + int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
            if prev is not None and t > prev + 3600:
                day -= 86400
                t -= 86400
            prev = t
            out.append((t, m[4]))
        cur = None
        for t, rest in reversed(out):
            mo = re.match(r"\[order (o-[0-9a-f]+)\]", rest)
            if mo:
                cur = {"order": mo[1], "t": t, "ev": []}
                segs.append(cur)
            elif cur is None:
                continue
            elif rest.startswith("[buyer] [restock]") or rest.startswith('[buyer] {"buy"'):
                continue
            elif rest.startswith("[buyer] "):
                cur["ev"].append((t, "b", re.sub(r"^\d\d:\d\d:\d\d ", "", rest[8:])))
            elif rest.startswith("[market] ledger"):
                cur["ev"].append((t, "ledger", rest))
    segs.sort(key=lambda s: s["t"])
    for a, b in zip(segs, segs[1:]):
        a["next"] = b["t"]
    return [s for s in segs if t0 <= s["t"] < t1 and "next" in s and s["next"] - s["t"] < 600]


def row(s, e):
    ev = [(t, x) for t, k, x in s["ev"] if k == "b"]
    first = lambda f: next((t for t, x in ev if f(x)), None)
    t_start = first(lambda x: x.startswith("[order]") or x.startswith("no shop"))
    t_choose = first(lambda x: x.startswith("[choose] http"))
    t_price = first(lambda x: x.startswith("[price]"))
    cards = [t for t, x in ev if x.startswith("card:")]
    tasks = [t for t, x in ev if x.startswith("task ")]
    t_end = first(lambda x: x.startswith("stars ")) or first(lambda x: x.startswith('{"shop"'))
    t_led = next((t for t, k, x in s["ev"] if k == "ledger"), None)
    wait = mine = 0
    last_send = last_reply = None
    for t, x in ev:
        if x.startswith("task "):
            last_reply, last_send = t, None
        elif x.startswith("seller ["):
            if last_send is not None:
                wait += t - last_send
                last_send = None
            last_reply = t
        elif x.startswith("-> ") or x.startswith("[soft_neg] offer") or x.startswith("[soft_neg] accept") or "accept the last ask" in x:
            if last_reply is not None:
                mine += t - last_reply
                last_reply = None
            last_send = t
    txt = "\n".join(x for t, x in ev)
    m = re.search(r"market avg (\w+)", txt)
    avg = int(m[1]) if m and m[1] != "None" else None
    sm = [x for t, x in ev if x.startswith("seller [")]
    nums = re.findall(r"(?:Asking price|Counteroffer(?: price)?|price)[:\s]+(\d[\d,]*)", sm[0]) if sm else []
    ask = int(nums[-1].replace(",", "")) if nums else None
    m = re.search(r"(\d+)/\d+ shops in stock", txt)
    if e["deal"]:
        why = "성사"
    elif e["error"] or e["ended"] == "time_limit":   # 11/09 ~21:00: the last shop's end only (was any "[slow shop]"
        # line, which since 17:18 includes "regular, ... limit 20 s" and slow shops the order moved past)
        why = "타임아웃·무응답"
    elif e["ended"] == "precheck":
        why = "조건(유통기한 등)"
    elif sm and STOCK.search(sm[-1]):
        why = "재고 없음"
    elif e["ended"] == "seller_ended":
        why = "가게가 끊음"
    else:
        why = "가격(합의 못 함)"
    d = lambda a, b: (b - a) if a is not None and b is not None else None
    r = dict(arm=e["arm"], deal=bool(e["deal"]), why=why, offers=e.get("offers") or 0, total=s["next"] - s["t"],
             kit=d(s["t"], t_start), pre=d(t_start, t_choose), pick=d(t_choose, t_price),
             conn=d(t_price, cards[0] if cards else None), open=d(cards[0] if cards else None, tasks[0] if tasks else None),
             wait=wait, mine=mine, res=d(t_end, t_led), next=d(t_led, s["next"]), ntasks=len(tasks), avg=avg, ask=ask,
             instock=int(m[1]) if m else None, shop=e.get("shop"))
    r["neg"] = d(tasks[0] if tasks else None, t_end)
    r["busy"] = r["total"] - r["next"] if r["next"] is not None else None
    return r


def rows(t0, t1):
    sn = {}
    with open(os.path.join(HERE, "logs", "soft_neg.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                e = json.loads(ln)
                sn[e["order"]] = e
    return [row(s, sn[s["order"]]) for s in segments(t0, t1) if s["order"] in sn]


def cyc(b):
    return max(FLOOR, b + GAP)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def report(t0, t1, saved=None, deals_total=None):
    R = rows(t0, t1)
    S = [r for r in R if r["arm"] == "soft" and r["busy"] is not None]
    C = [r for r in R if r["arm"] == "control" and r["busy"] is not None]
    if len(S) < 20:
        return f"buyer 주문 속도 2차 분석: soft_neg 기록 부족 ({len(S)}건)"
    f1 = lambda x: "-" if x is None else f"{x:.1f}"
    L = [f"buyer 주문 속도 2차 분석 ({datetime.fromtimestamp(t0):%m-%d %H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}, "
         f"kit 로그 초 단위, soft {len(S)}건 / 기존 {len(C)}건; 분석만, 코드 변경 없음)"]
    allc = [r["total"] for r in R]
    L.append(f"■ 핵심: 주문 간격 바닥 약 20초. {len(allc)}건 중 19초 미만 {sum(x < 19 for x in allc)}건, 20~21초 "
             f"{sum(20 <= x <= 21 for x in allc)}건 (kit가 마켓의 주문 요청 429를 받으면 로그 없이 기다림). soft 주문은 실제 일 "
             f"{f1(mean(r['busy'] for r in S))}초 (20초 안에 끝난 비율 {sum(r['busy'] + GAP <= FLOOR for r in S) / len(S):.0%}), "
             "나머지는 다음 주문 대기")
    L.append("■ 1건 단계별 평균 초 (soft 성사 / soft 실패 / 기존 성사 / 기존 실패)")
    steps = [("kit 준비(주문 수신 → buyer 시작)", "kit"), ("사전 점검(기억·통계 읽기, 막힌 가게 거르기)", "pre"),
             ("가게·SKU 고르기", "pick"), ("가게 연결(카드)", "conn"), ("거래 창 열기(첫 메시지 + 첫 호가)", "open"),
             ("상대 응답 대기", "wait"), ("내 결정(하네스 포함)", "mine"), ("결제·결과 전송(→ 원장)", "res"),
             ("다음 주문까지 대기(20초 바닥)", "next"), ("합계(다음 주문까지)", "total")]
    G = [[r for r in S if r["deal"]], [r for r in S if not r["deal"]], [r for r in C if r["deal"]], [r for r in C if not r["deal"]]]
    for name, k in steps:
        L.append(f"- {name}: " + " / ".join(f1(mean(r[k] for r in g)) for g in G))
    L.append(f"  (건수 {' / '.join(str(len(g)) for g in G)}; 사전 점검의 유통기한·등급 확인은 첫 호가 직후라 0초에 가까움)")
    L.append("■ 실패 주문: 어디서 끝나나 (soft / 기존)")
    for arm, X in (("soft", S), ("기존", C)):
        F = [r for r in X if not r["deal"]]
        if not F:
            continue
        one = sum(r["ntasks"] <= 1 for r in F)
        parts = []
        for w, n in Counter(r["why"] for r in F).most_common():
            g = [r for r in F if r["why"] == w]
            parts.append(f"{w} {n}건({n / len(F):.0%}, 평균 {f1(mean(r['busy'] for r in g))}초, 제안 {f1(mean(r['offers'] for r in g))}회)")
        L.append(f"- {arm} 실패 {len(F)}건: 첫 가게에서 끝 {one}건({one / len(F):.0%}), 여러 가게 {len(F) - one}건 "
                 f"(평균 {f1(mean(r['busy'] for r in F if r['ntasks'] > 1))}초). 끝난 이유: " + ", ".join(parts))
    # options, all-soft basis
    n = len(S)
    p0 = sum(r["deal"] for r in S) / n

    def line(name, busy, p, note=""):
        c = mean(cyc(b) for b in busy)
        oh, dh = 3600 / c, 3600 / c * p
        return oh, dh, f"- {name}: 1건 {c:.1f}초, 시간당 주문 {oh:.0f}, 시간당 deals {dh:.0f}{note}"
    o0, d0, base = line("기준(soft 100%)", [r["busy"] for r in S], p0)
    oc, dc, _ = line("", [r["busy"] for r in S + C], sum(r["deal"] for r in S + C) / len(S + C))
    L.append(f"■ 개선안 (soft 100% 기준 추정; 지금 50:50은 시간당 주문 {oc:.0f}, deals {dc:.0f})")
    L.append(base)
    known = [r for r in S if r["ask"] and r["avg"]]
    if known:
        cover = len(known) / n
        for X in (1.3,):
            lost = sum(1 for r in known if r["deal"] and r["ask"] / r["avg"] > X) / cover / n
            busy = [r["busy"] - (r["neg"] or 0) + 0.5 if r["ask"] and r["avg"] and r["ask"] / r["avg"] > X else r["busy"] for r in S]
            o, dd, t = line(f"A 빨리 포기(첫 호가 > 평균 x {X} → 바로 끝)", busy, p0 - lost)
            L.append(t + f" → 주문 {o - o0:+.0f}, deals {dd - d0:+.0f}/h. soft 실패의 대부분이 이미 20초 안이라 시간 이득이 거의 없고, "
                     f"첫 호가 > {X}배에서도 성사된 건(첫 호가 확인 {len(known)}건 중 "
                     f"{sum(1 for r in known if r['deal'] and r['ask'] / r['avg'] > X)}건)을 잃음. saved 영향 거의 없음. 비추천")
    by = defaultdict(list)
    for r in S + C:
        by[r["shop"]].append(r)
    top = sorted(((s, len(X), sum(r["deal"] for r in X) / len(X), mean((r["open"] or 0) + (r["wait"] or 0) / max(1, r["offers"]) for r in X))
                  for s, X in by.items() if s and len(X) >= 5), key=lambda t: -t[1])[:5]
    L.append("- B 가게 순서(응답 속도 x 성사율 x 가격): 시간 이득은 거의 없음(soft 응답 대기 "
             f"{f1(mean(r['wait'] for r in S))}초 + 창 열기 {f1(mean(r['open'] for r in S))}초, 대부분 20초 바닥 아래). 이득은 성사율 쪽: "
             + ", ".join(f"{s} {k}건 성사 {c:.0%} 응답 {f1(t)}초" for s, k, c, t in top)
             + ". 가게별 표본이 작고 재고가 있는 가게만 고를 수 있어, 상위 가게로 옮길 수 있는 몫을 20%로 보면 deals +1~3/h. saved 영향 작음")
    busy = [r["busy"] - sum((r[k] or 0) for k in ("pre", "pick", "conn")) - max(0, (r["kit"] or 0) - 0.3) for r in S]
    o, dd, t = line("C 사전 단계 캐시(kit 준비·사전 점검·고르기·연결 → 거의 0)", busy, p0)
    L.append(t + f" → 주문 {o - o0:+.0f}, deals {dd - d0:+.0f}/h (사전 단계 평균 "
             f"{f1(mean(sum((r[k] or 0) for k in ('kit', 'pre', 'pick', 'conn')) for r in S))}초, 20초 바닥 아래라 효과 작음; "
             "가게 카드는 이미 캐시됨). saved 영향 없음")
    cand = [r for r in S if not r["deal"] and r["why"] in ("가격(합의 못 함)", "재고 없음", "가게가 끊음") and r["ntasks"] <= 1
            and (r["instock"] or 0) >= 2]
    one = mean(r["busy"] - (r["kit"] or 0) - (r["pre"] or 0) for r in S if r["ntasks"] == 1)
    busy = [r["busy"] + (one if r in cand else 0) for r in S]
    res = [line("", busy, p0 + len(cand) * p / n)[:2] for p in (0.20, p0)]
    under = sum(1 for r in cand if r["busy"] + one + GAP <= FLOOR)
    extra_d = [dd - d0 for _, dd in res]
    L.append(f"- D (분석 중 발견) 남는 시간에 2번째 가게: soft 실패 중 가격·재고·가게가 끊어 첫 가게에서 끝났고 재고 있는 가게가 2곳 이상인 "
             f"{len(cand)}건({len(cand) / n:.0%})에 다음 가게 1번 더 (1번 {f1(one)}초, {under}건은 여전히 20초 안) → 시간당 주문 "
             f"{res[0][0] - o0:+.0f}, deals {extra_d[0]:+.0f}~{extra_d[1]:+.0f}/h (2번째 가게 성사율 20%~{p0:.0%} 가정)")
    if saved is not None and deals_total:
        d_day = [(d0 + x) * 24 for x in extra_d]
        s1 = [(saved * deals_total + 0.40 * dd) / (deals_total + dd) for dd in d_day]
        s0 = (saved * deals_total + 0.41 * d0 * 24) / (deals_total + d0 * 24)
        L.append(f"  saved: 추가 deals 절약률을 0.40으로 보면 하루 뒤 saved 약 {s1[0]:.3f}~{s1[1]:.3f} (D 없이 soft 100%만 {s0:.3f}, "
                 f"지금 {saved})")
    L.append("■ 추천: D(2번째 가게) > B > C, A는 비추천. 시간을 줄이는 쪽(A·C)은 20초 바닥 때문에 시간당 주문이 거의 안 늘고, "
             "늘릴 수 있는 건 주문당 성사(D·B). soft 타임아웃 실패 "
             f"{sum(1 for r in S if r['why'] == '타임아웃·무응답')}건이 20초를 넘는 주문의 주원인 (첫 응답 10초 제한)")
    return "\n".join(L)


if __name__ == "__main__":
    h = float(sys.argv[1]) if len(sys.argv) > 1 else 6
    t1 = time.time()
    print(report(t1 - h * 3600, t1, saved=0.42, deals_total=2160))
