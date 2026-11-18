"""Buyer surplus by list price band (person's request 11/09 ~17:25; analysis only, changes nothing).
Per order of the last 24 h (kit log: the order line's budget, the [price] line's list price of the SKU we went for, the
ledger line's SKU and price; logs/soft_neg.jsonl for the soft_neg / current arm since 14:27):
  - bands < 50k / 50-200k / 200-500k / >= 500k (list price; the budget when no SKU was reached): orders, close rate,
    surplus per deal (list - paid), share of all surplus; soft_neg, current (control) and all 24 h
  - the top 3 buyers by board surplus: surplus per deal and the list band of their deals (settles in market.db, 24 h)
  - orders >= 200k that failed: price / no stock / timeout / the shop ended it / budget short (the shop's last ask
    above our budget)
  - options for the expensive orders with estimated surplus / deals / saved changes
Surplus on the board is list - paid + refunds; refunds are left out here (both for us and for the others).
    python tools/surplus_bands.py
"""
import json
import os
import re
import sqlite3
import statistics as st
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
import order_speed2 as o2  # noqa: E402

BANDS = ("5만 미만", "5~20만", "20~50만", "50만 이상")


def band(x):
    return BANDS[0] if x < 50_000 else BANDS[1] if x < 200_000 else BANDS[2] if x < 500_000 else BANDS[3]


def catalog():
    cat = json.load(open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8"))
    items = cat.get("items") if isinstance(cat, dict) and "items" in cat else cat
    if isinstance(items, dict):
        items = list(items.values())
    return {i["sku"]: i.get("list_price") for i in items if isinstance(i, dict) and "sku" in i}


def budgets():
    out = {}
    for name in ("market-kit.log.1", "market-kit.log"):
        p = os.path.join(HERE, "logs", name)
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    m = re.match(r"\d\d:\d\d:\d\d \[order (o-[0-9a-f]+)\] (.+?), budget (\d+)", ln)
                    if m:
                        out[m[1]] = (m[2], int(m[3]))
    return out


def orders(t0, t1):
    LP, B = catalog(), budgets()
    arms = {}
    with open(os.path.join(HERE, "logs", "soft_neg.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                e = json.loads(ln)
                arms[e["order"]] = e["arm"]
    out = []
    for s in o2.segments(t0, t1):
        item, budget = B.get(s["order"], (None, None))
        if budget is None:
            continue
        ev = [x for t, k, x in s["ev"] if k == "b"]
        txt = "\n".join(ev)
        led = next((x for t, k, x in s["ev"] if k == "ledger"), "")
        m = re.search(r": (G\w+) at (\d+), stars \S+, verified True", led)
        deal = bool(m)
        lpm = re.search(r"\[price\] G\w+ list (\d+)", txt)
        lp = LP.get(m[1]) if m else (int(lpm[1]) if lpm else None)
        sm = [x for x in ev if x.startswith("seller [")]
        asks = [int(a.replace(",", "")) for x in sm for a in re.findall(r"(?:Asking price|Counteroffer(?: price)?|price)[:\s]+(\d[\d,]*)", x)]
        if deal:
            why = "성사"
        elif "no shop to trade with" in txt or re.search(r" 0/\d+ shops in stock", txt):
            why = "재고 없음"
        elif "failed:" in txt[txt.rfind("[choose]"):] or "time limit: walking" in txt[txt.rfind("[choose]"):]:
            # 11/09 ~21:00: only when the last shop tried did not answer (was any "[slow shop]" line in the order)
            why = "타임아웃·무응답"
        elif sm and o2.STOCK.search(sm[-1]):
            why = "재고 없음"
        elif "[precheck] skip" in txt:
            why = "조건(유통기한·등급)"
        elif asks and asks[-1] > budget:
            why = "예산 부족"
        elif "ENDED without deal" in txt:
            why = "가게가 끊음"
        else:
            why = "가격(합의 못 함)"
        avg = re.search(r"market avg (\d+)", txt)
        out.append(dict(order=s["order"], item=item, budget=budget, lp=lp, band=band(lp or budget), deal=deal,
                        paid=int(m[2]) if m else None, surplus=(lp - int(m[2])) if m and lp else 0, why=why,
                        arm=arms.get(s["order"]), last_ask=asks[-1] if asks else None, first_ask=asks[0] if asks else None,
                        avg=int(avg[1]) if avg else None, t=s["t"]))
    return out


def table(R, total_surplus):
    L = []
    for b in BANDS:
        X = [r for r in R if r["band"] == b]
        if not X:
            L.append(f"  {b}: 0건")
            continue
        D = [r for r in X if r["deal"]]
        s = sum(r["surplus"] for r in D)
        L.append(f"  {b}: 주문 {len(X)}건({len(X) / len(R):.0%}), 성사율 {len(D) / len(X):.0%}, 건당 surplus "
                 f"{(s / len(D)) if D else 0:,.0f}, surplus 비중 {s / total_surplus if total_surplus else 0:.0%}")
    return L


def others(now):
    LP = catalog()
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        import httpx
        B = httpx.get("https://market.ourolabs.ai/api/board?round=3", timeout=30).json()["buyers"]
        top = [b["name"] for b in sorted(B, key=lambda b: -(b.get("surplus") or 0))[:3]]
        L = []
        for name in top + ["choo-chu"]:
            rows = db.execute("SELECT sku, amount FROM settles WHERE buyer=? AND ts>?", (name, now - 86400)).fetchall()
            c, s = Counter(), defaultdict(int)
            for sku, amt in rows:
                lp = LP.get(sku) or 0
                c[band(lp)] += 1
                s[band(lp)] += lp - amt
            n = sum(c.values())
            if not n:
                continue
            L.append(f"  {name if name != 'choo-chu' else '우리(choo-chu)'}: 24시간 정산 {n}건, 건당 surplus "
                     f"{sum(s.values()) / n:,.0f}; 정가 구간 " + ", ".join(f"{b} {c[b] / n:.0%}" for b in BANDS)
                     + f"; 50만 이상 건당 {s[BANDS[3]] / max(1, c[BANDS[3]]):,.0f}")
        return top, L
    finally:
        db.close()


def report(now=None, saved=None, deals_total=None, surplus_total=None):
    now = now or time.time()
    R = orders(now - 86400, now)
    if len(R) < 50:
        return f"buyer surplus 분석: 주문 기록 부족 ({len(R)}건)"
    tot = sum(r["surplus"] for r in R if r["deal"])
    L = [f"buyer surplus 정가 구간 분석 ({datetime.fromtimestamp(R[0]['t']):%m-%d %H:%M} ~ {datetime.fromtimestamp(now):%m-%d %H:%M}, "
         f"주문 {len(R)}건; 정가 = 협상한 SKU의 정가, 없으면 예산; surplus = 정가 - 결제가, 환불 제외; 분석만)"]
    L.append(f"■ 24시간 전체: 성사 {sum(r['deal'] for r in R)}건, surplus {tot:,.0f}")
    L += table(R, tot)
    for arm, name in (("soft", "soft_neg"), ("control", "기존(실험 중 나머지 50%)")):
        X = [r for r in R if r["arm"] == arm]
        if X:
            ts = sum(r["surplus"] for r in X if r["deal"])
            L.append(f"■ {name}: 주문 {len(X)}건, 성사 {sum(r['deal'] for r in X)}건, surplus {ts:,.0f}")
            L += table(X, ts)
    try:
        top, ol = others(now)
        L.append("■ surplus 상위 buyer 3명과 비교 (원장 settles 24시간, 같은 계산)")
        L += ol
        L.append("  주문 카테고리는 시장이 정함(buyer마다 자주 사는 카테고리가 숨어 있음, 7주차 규칙). 상위 buyer는 시간당 주문 수는 "
                 "우리와 비슷하지만 정산의 약 40%가 50만 이상. 우리 주문은 대부분 식품이라 이 차이는 바꿀 수 없음. 우리가 바꿀 수 있는 건 "
                 "비싼 주문의 성사율과 건당 surplus")
    except Exception as e:
        L.append(f"■ 상위 buyer 비교 실패: {type(e).__name__}")
    H = [r for r in R if (r["lp"] or r["budget"]) >= 200_000]
    F = [r for r in H if not r["deal"]]
    if H:
        L.append(f"■ 20만 이상 주문 {len(H)}건, 성사 {len(H) - len(F)}건({(len(H) - len(F)) / len(H):.0%}), 실패 {len(F)}건 이유: "
                 + ", ".join(f"{w} {n}건" for w, n in Counter(r["why"] for r in F).most_common()))
        cats = Counter(r["item"] for r in H)
        L.append("  카테고리: " + ", ".join(f"{k} {v}" for k, v in cats.most_common(6)))
        pr = [r for r in F if r["why"] in ("가격(합의 못 함)", "예산 부족") and r["last_ask"] and r["avg"]]
        if pr:
            gap = sorted(r["last_ask"] / r["avg"] for r in pr)
            L.append(f"  가격·예산 실패 {len(pr)}건: 마지막 호가 / 시장 평균 중앙값 {gap[len(gap) // 2]:.2f} "
                     f"(1.05 이하 {sum(g <= 1.05 for g in gap)}건, 1.10 이하 {sum(g <= 1.10 for g in gap)}건)")
        D = [r for r in H if r["deal"] and r["lp"]]
        L.append(f"■ 비싼 주문(20만 이상) 성사율 올리기 (24시간 = 하루 기준; 지금 고가 성사 {len(D)}건, 건당 surplus "
                 f"{st.mean(r['surplus'] for r in D) if D else 0:,.0f}, 정가 대비 {sum(r['surplus'] for r in D) / max(1, sum(r['lp'] for r in D)):.0%})")
        ok = lambda r, x: r["lp"] and r["last_ask"] <= min(r["budget"], x * r["avg"])
        # each added deal: (list, surplus) at the shop's last ask; option 2 counts 80% of them (one more haggle may not land)
        o1 = [(r["lp"], r["lp"] - r["last_ask"]) for r in pr if ok(r, 1.05)]
        o2_ = [(r["lp"], r["lp"] - r["last_ask"]) for r in pr if ok(r, 1.10)]
        ended = [r for r in F if r["why"] in ("가게가 끊음", "재고 없음", "타임아웃·무응답") and r["lp"]]
        avg_save = sum(r["surplus"] for r in D) / max(1, sum(r["lp"] for r in D))
        o3 = [(r["lp"], r["lp"] * avg_save) for r in ended]
        opts = [("1) 20만 이상만 수락 기준을 시장 평균 x 1.00 → x 1.05로 (마지막 호가 기준, 예산 안)", o1, 1.0),
                ("2) 20만 이상만 흥정 1회 추가 + 수락 기준 x 1.10 (해당 건의 80% 성사 가정)", o2_, 0.8),
                ("3) 20만 이상은 가게가 끊거나 재고 없음·무응답이면 재고 있는 다음 가게를 끝까지 시도(최대 3곳; 성사 30%, "
                 "지금 고가 절약률 가정)", o3, 0.3)]
        lst_w = paid_w = None
        try:
            LP = catalog()
            db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
            rows = [(LP.get(k), a) for k, a in db.execute("SELECT sku, amount FROM settles WHERE buyer='choo-chu'") if LP.get(k)]
            db.close()
            lst_w, paid_w = sum(l for l, a in rows), sum(a for l, a in rows)
        except Exception:
            pass
        for name, X, p in opts:
            add = len(X) * p
            sp = sum(s for _, s in X) * p
            line = f"- {name}: 하루 성사 +{add:.0f}건 (대상 {len(X)}건), surplus +{sp:,.0f}"
            if saved is not None and deals_total and X:
                mean_new = (saved * deals_total + p * sum(s / l for l, s in X)) / (deals_total + add)
                line += f", saved {saved:.4f} -> 건당 평균 모델 {mean_new:.4f}"
                if lst_w:
                    w0 = (lst_w - paid_w) / lst_w
                    w1 = (lst_w - paid_w + sp) / (lst_w + p * sum(l for l, _ in X))
                    line += f" / 정가 가중 모델 {saved + (w1 - w0):.4f}"
            L.append(line)
        L.append("  saved 계산식은 공개돼 있지 않아 두 모델로 범위를 냄 (보드 saved는 어느 쪽과도 정확히 맞지 않음). 1)·2)는 지금 수락 "
                 "규칙(평균 x 1.00 이하)을 고가에만 넓히는 것. 하루 지표는 24시간 표본이라 작은 수의 고가 주문에 크게 흔들림")
        L.append("  주의: ALL 점수는 0.5 saved 순위 + 0.3 forum + 0.1 deals 순위 + 0.1 posts 순위라 surplus는 직접 안 들어감. "
                 "고가 확대는 surplus·deals를 늘리지만 saved를 조금 낮춤 (1위 유지 여부가 핵심)")
    return "\n".join(L)


if __name__ == "__main__":
    print(report(saved=0.42, deals_total=2164, surplus_total=86_436_095))
