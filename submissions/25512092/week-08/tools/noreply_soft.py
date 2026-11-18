"""soft_neg no-reply analysis (person's request 11/09 ~20:00, analysis only, no behavior change).
Each order in the kit log is split into shop attempts; each attempt ends as
  no_card       the shop's agent card did not come within the limit (before any message)
  no_first      no reply to our opening message "I want to buy X (SKU)" (no price in it: the arm is not visible yet)
  mid_silent    the shop stopped answering after we sent an offer (exception after an offer)
  time_limit    our 75 s limit ran out while haggling
  answered      the shop answered and the attempt ended otherwise (deal, walk, no stock, shop ended, precheck)
Only mid_silent and time_limit come after our first offer, so only they can be caused by the 75% first offer.
Per shop and arm (soft / control): attempts, pre-offer no-reply rate, post-offer no-reply rate.
    python tools/noreply_soft.py [hours]          (default: since soft_neg started)
"""
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)

PRE, POST = ("no_card", "no_first"), ("mid_silent", "time_limit")


def url_names():
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        return {u.rstrip("/"): s for s, u in db.execute("SELECT shop, shop_url FROM buyer_deals WHERE shop_url IS NOT NULL "
                                                        "GROUP BY shop_url")}
    finally:
        db.close()


CHOOSE = re.compile(r"^\[choose\] (https?://\S+?): (.+?): shop score")
NEXT = re.compile(r"^\[choose\] trying next candidate (https?://\S+)")
SKIPPED = re.compile(r"^\[choose\] (.+?) skipped \(.*\), trying (.+)$")
SLOW = re.compile(r"^\[slow shop\] (.+?): no (card|first reply) within")
FAILED = re.compile(r"^shop (.+?) failed: (.*)$")


def attempts(lines, names):
    """[(shop, outcome, offers sent)] for one order's log lines."""
    out, cur, offers, closed = [], None, 0, True

    def close(outcome):
        nonlocal closed
        if cur is not None and not closed:
            out.append((cur, outcome, offers))
            closed = True

    for x in lines:
        m = CHOOSE.match(x) or NEXT.match(x) or SKIPPED.match(x)
        if m:
            if m.re is SKIPPED:
                close("answered")
                nm = m[2].strip()
            else:
                close("answered")
                nm = m[2] if m.re is CHOOSE else names.get(m[1].rstrip("/"), m[1])
            cur, offers, closed = nm, 0, False
            continue
        m = SLOW.match(x)
        if m:
            close("no_card" if m[2] == "card" else "no_first")
            continue
        m = FAILED.match(x)
        if m:
            close("mid_silent" if offers else "no_first")
            continue
        if x.startswith("-> ") or x.startswith("[soft_neg] offer") or x.startswith("[soft_neg] accept"):
            offers += 1
        elif x.startswith("time limit: walking away"):
            close("time_limit")
    close("answered")
    return out


def collect(t0, t1):
    import order_speed2 as o
    sn = {}
    with open(os.path.join(HERE, "logs", "soft_neg.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                e = json.loads(ln)
                sn[e["order"]] = e
    names = url_names()
    rows = []
    for s in o.segments(t0, t1):
        e = sn.get(s["order"])
        if not e:
            continue
        for shop, outc, offers in attempts([str(v[-1]) for v in s["ev"]], names):
            rows.append({"order": s["order"], "arm": e["arm"], "shop": shop, "out": outc, "offers": offers,
                         "deal": bool(e["deal"]) and shop == e.get("shop")})
    return rows


def report(t0, t1):
    from datetime import datetime
    A = collect(t0, t1)
    L = [f"soft_neg 무응답 분석 ({datetime.fromtimestamp(t0):%m-%d %H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}), 가게 시도 단위"]
    for arm in ("soft", "control"):
        X = [a for a in A if a["arm"] == arm]
        c = Counter(a["out"] for a in X)
        orders = len({a["order"] for a in X})
        L.append(f"- {arm}: 주문 {orders}건, 가게 시도 {len(X)}건: 카드 없음 {c['no_card']}, 첫 메시지 무응답 {c['no_first']} "
                 f"(둘 다 우리 가격 보내기 전), 제안 후 무응답 {c['mid_silent']}, 75초 시간 초과 {c['time_limit']}, 응답 {c['answered']}")
        off = [a for a in X if a["offers"] > 0 or a["out"] in POST]
        if off:
            L.append(f"  제안을 1번 이상 보낸 시도 {len(off)}건 중 그 뒤 무응답·시간 초과 "
                     f"{sum(a['out'] in POST for a in off)}건 ({sum(a['out'] in POST for a in off) / len(off):.1%})")
    per = defaultdict(lambda: {"soft": Counter(), "control": Counter()})
    for a in A:
        per[a["shop"]][a["arm"]]["n"] += 1
        per[a["shop"]][a["arm"]][a["out"]] += 1
        if a["offers"] > 0 or a["out"] in POST:
            per[a["shop"]][a["arm"]]["offered"] += 1
    rate = lambda c, keys: (sum(c[k] for k in keys) / c["n"]) if c["n"] else None
    top = sorted(per.items(), key=lambda kv: -sum(kv[1]["soft"][k] for k in PRE + POST))[:12]
    L.append("- 가게별 (soft 무응답 많은 순): soft 시도 / 보내기 전 무응답 / 제안 후 무응답 | control 같은 값")
    pf = lambda x: "-" if x is None else f"{x:.0%}"
    for shop, d in top:
        s, c = d["soft"], d["control"]
        if not s["n"]:
            continue
        post = lambda q: (sum(q[k] for k in POST) / q["offered"]) if q["offered"] else None
        L.append(f"  {shop}: soft {s['n']} / {pf(rate(s, PRE))} / {pf(post(s))} (제안 {s['offered']}) | control {c['n']} / "
                 f"{pf(rate(c, PRE))} / {pf(post(c))} (제안 {c['offered']})")
    return L, A


if __name__ == "__main__":
    from depts import soft_neg
    t1 = time.time()
    t0 = t1 - float(sys.argv[1]) * 3600 if len(sys.argv) > 1 else soft_neg.rules()["since"]
    L, A = report(t0, t1)
    print("\n".join(L))
