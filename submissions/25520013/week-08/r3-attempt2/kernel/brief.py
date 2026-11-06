"""Situation briefing for the operator: facts only, no interpretation, no advice.

build(root, now) -> text. Each section reads its own source and says when the source is missing, so a broken file
shows up as a fact instead of a crash. Other participants appear only as hashed ids.
"""

import json
import os
import time
import statistics as st
from collections import Counter, defaultdict

from kernel import common as c
from kernel import restock, versions

BOARD_DIRS = ("a2/board", "icrl/state/board")
HARNESS_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness")
SHOP_KEYS = (
    "book",
    "revenue",
    "stars",
    "stars_rank",
    "deals",
    "profit",
    "stock_cost",
    "stock_units",
    "refunds",
    "broken",
    "claims",
    "visits",
    "share",
)
BUYER_KEYS = ("saved", "forum", "posts", "deals")  # Patch 3.4 (11-06): saved replaced surplus in buyer ALL
# ALL weights from MARKET_BRIEF (RULES): one rank in a column is worth about weight / (shops - 1) of that side's all.
# Which of stars / stars_rank the formula reads is not published, so both carry the stars weight.
ALL_WEIGHTS = {
    "가게": {"book": 0.5, "revenue": 0.3, "stars": 0.2, "stars_rank": 0.2},
    "구매자": {"saved": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1},
}
CLIMB_N = 3  # [가게]/[구매자]: gaps to the nearest shops above us, so a crowded column shows as small gaps
RATE_KEYS = {"saved": 1.0}  # keys that are rates; before Patch 3.4 the board's saved column was an amount
SELLER_DECISIONS = ("open", "accept", "cancel", "no_stock", "reject", "declined")
FLOW_BLOCK_H = 2  # [상품군별 흐름]: three 2 h blocks, so a change since the last few hours shows next to its market
FLOW_BLOCKS = 3
CATEGORY_TOP = 6


def board_snapshots(root):
    """-> sorted list of (ts, path) across the kernel's and attempt 1's snapshot dirs."""
    seen = {}
    for d in BOARD_DIRS:
        full = os.path.join(root, d)
        try:
            names = os.listdir(full)
        except OSError:
            continue
        for n in names:
            stem = n.split(".")[0]
            if stem.isdigit() and n.endswith(".json"):
                seen.setdefault(int(stem), os.path.join(full, n))
    return sorted(seen.items())


def _snapshot_near(snaps, ts):
    return min(snaps, key=lambda s: abs(s[0] - ts)) if snaps else None


def _rank(rows, key, me):
    vals = [r.get(key) or 0 for r in rows]
    return 1 + sum(1 for v in vals if v > (me.get(key) or 0))


def section_time(now, board_ts):
    left = (c.ROUND_END - now) / 3600
    line = f"[시각] {c.kst(now)} KST, 회차 종료(11-17 23:59)까지 {left:.0f}시간"
    if board_ts:
        line += f", 순위표 기준 {c.kst(board_ts, '%H:%M')}"
    return [line, f"[목표] {c.GOAL}"]


def section_rank(b, b6, login, hours6):
    P = b.get("players") or []
    me = next((p for p in P if c.is_mine(p, login)), None)
    if not me:
        return ["[순위] 순위표에서 우리 행을 찾지 못함"]
    E = sorted(P, key=lambda p: -(p.get("total") or 0))
    r = E.index(me) + 1
    out = [
        f"[순위 Everyone] {len(E)}명 중 {r}위, total {me['total']:.3f} | 1위 {E[0]['total']:.3f} "
        f"(차이 {E[0]['total'] - me['total']:.3f}), 2위 {E[1]['total']:.3f}"
        + (f", 10위 {E[9]['total']:.3f}" if len(E) >= 10 else "")
    ]
    P6 = (b6 or {}).get("players") or []
    me6 = next((p for p in P6 if c.is_mine(p, login)), None)
    if me6:
        r6 = _rank(P6, "total", me6)
        out.append(f"  {hours6:.0f}시간 전: {r6}위, total {me6.get('total') or 0:.3f}")
    top = []
    for p in E[:5]:
        who = "우리" if p is me else c.hid(p.get("player") or p.get("owner"))
        top.append(
            f"{who} {p.get('total') or 0:.3f} (가게 {(p.get('shop') or {}).get('all') or 0:.2f}, "
            f"구매자 {(p.get('buyer') or {}).get('all') or 0:.2f}, 학생 {'예' if p.get('student') else '아니오'})"
        )
    out.append("  상위 5명: " + ", ".join(top))
    S = sorted(
        (p for p in P if p.get("student")), key=lambda p: -(p.get("student_total") or 0)
    )
    if me in S:
        out.append(
            f"[순위 Class, 참고] 학생 {len(S)}명 중 {S.index(me) + 1}위, student_total "
            f"{me.get('student_total') or 0:.3f} | 1위 {S[0].get('student_total') or 0:.3f}, "
            f"Everyone 1위와 같은 사람 {'예' if S and S[0] is E[0] else '아니오'}"
        )
    return out


def section_side(rows, rows6, keys, label, login, hours6):
    me = next((x for x in rows if c.is_mine(x, login)), None)
    if not me:
        return [f"[{label}] 우리 행 없음"]
    me6 = next((x for x in rows6 if c.is_mine(x, login)), {}) or {}
    top = max(rows, key=lambda x: x.get("all") or 0)
    out = [
        f"[{label}] 우리 all {me.get('all') or 0:.3f}, {label} 1위 all {top.get('all') or 0:.3f}"
    ]
    for k in keys:
        v = me.get(k)
        vals = sorted(
            (x.get(k) for x in rows if isinstance(x.get(k), (int, float))), reverse=True
        )
        if not isinstance(v, (int, float)) or not vals:
            out.append(f"  {k}: {c.fmt(v)}")
            continue
        rk = 1 + sum(1 for x in vals if x > v)
        v6 = me6.get(k)
        if k in RATE_KEYS and isinstance(v6, (int, float)) and abs(v6) > RATE_KEYS[k]:
            v6 = None  # the 6 h old snapshot still holds the old meaning
        rate = (
            f", {hours6:.0f}시간 평균 {c.fmt((v - v6) / hours6)}/h"
            if isinstance(v6, (int, float)) and hours6
            else ""
        )
        out.append(
            f"  {k}: {c.fmt(v)} ({len(vals)}곳 중 {rk}위, 1위 {c.fmt(vals[0])}, 중앙 {c.fmt(st.median(vals))}"
            f"{rate})"
        )
        climb = climb_line(vals, v, (ALL_WEIGHTS.get(label) or {}).get(k), label)
        if climb:
            out.append(climb)
    return out


def climb_line(vals, v, weight, label):
    """'    바로 위 N곳까지 차이 ...' for one board column: the gaps from our value to the nearest values above it, and
    what one rank is worth in that side's all when the column has a weight (weight / (count - 1), an estimate since
    the board's percentile formula is not published). None when we are first."""
    above = sorted(x for x in vals if x > v)[:CLIMB_N]
    if not above:
        return None
    line = f"    바로 위 {len(above)}곳까지 차이: " + ", ".join(f"+{c.fmt(x - v)}" for x in above)
    if weight and len(vals) > 1:
        line += f"; 한 계단은 {label} all 약 +{weight / (len(vals) - 1):.4f} (가중치 {weight}, 추정)"
    return line


def section_inventory(root, now):
    inv = c.load_json(os.path.join(root, ".market/inventory.json"))
    cat = c.load_json(os.path.join(root, ".market/catalog.json"), {}) or {}
    if not isinstance(inv, dict):
        return ["[가게 상태] inventory.json 없음"]
    units = inv.get("units") or []
    byc = Counter((cat.get(u.get("sku")) or {}).get("category", "?") for u in units)
    exp24 = sum(
        1 for u in units if u.get("expires_at") and u["expires_at"] - now < 86400
    )
    return [
        f"[가게 상태] 잔고 {c.fmt(inv.get('balance'))} (시작 {c.fmt(inv.get('start'))}, 투입 "
        f"{c.fmt(inv.get('injected'))}), 파산선 {c.fmt(inv.get('bankrupt_line'))}, 남은 파산 "
        f"{inv.get('bankruptcies_left', '-')}회, 보관비 {c.fmt(inv.get('storage_per_day'))}/일",
        f"  재고 {len(units)}개: "
        + ", ".join(f"{k} {n}" for k, n in byc.most_common(6))
        + f" | 24시간 안에 유통기한 끝 {exp24}개",
    ]


def restock_plan_state(root, now):
    """("none" | "expired" | "valid", minutes left or None) for a2/restock_plan.json."""
    plan = c.load_json(os.path.join(root, "a2/restock_plan.json"))
    if not isinstance(plan, dict):
        return "none", None
    vu = plan.get("valid_until")
    if not isinstance(vu, (int, float)) or isinstance(vu, bool) or vu <= now:
        return "expired", None
    return "valid", round((vu - now) / 60)


def section_restock(root, now):
    """Facts about how restock runs: only the operator's plan buys stock."""
    state, left = restock_plan_state(root, now)
    plan = c.load_json(os.path.join(root, "a2/restock_plan.json"), {}) or {}
    inv = c.load_json(os.path.join(root, ".market/inventory.json"), {}) or {}
    w = inv.get("restock") or {}
    opens = w.get("opens_at")
    win = (
        "열림"
        if w.get("open")
        else f"{round((opens - now) / 60)}분 뒤 열림"
        if isinstance(opens, (int, float)) and not isinstance(opens, bool)
        else "모름"
    )
    sold = sum(
        1
        for m in c.tail_json(os.path.join(root, ".r3/metrics.jsonl"), 20_000_000, b'"accept"')
        if m.get("hook") == "seller" and m.get("decision") == "accept" and (m.get("ts") or 0) >= now - 3600
    )
    head = {
        "none": "없음",
        "expired": "만료",
        "valid": f"유효 ({left}분 남음, {len(plan.get('buy') or [])}줄)",
    }[state]
    done = c.load_json(os.path.join(root, "a2/restock_done.json"), {}) or {}
    path = os.path.join(root, "a2/restock_plan.json")
    if state == "valid" and done.get("plan_id") and done.get("plan_id") == restock.plan_id(path, plan):
        when = time.strftime("%H:%M", time.localtime(done.get("ts") or now))
        head += f", {when}에 이미 구매함 ({len(done.get('buy') or [])}줄). 이 계획으로는 다시 사지 않음"
    return [
        f"[재입고] 계획 {head} | 재고 {len(inv.get('units') or [])}개, 최근 1시간 판매 {sold}개 | 공급 창 {win}",
        "  재입고는 done의 restock_plan(a2/restock_plan.json)으로만 실행됨. 계획이 없거나 valid_until이 지나면 "
        "커널은 아무것도 사지 않음",
        "  계획 하나는 한 번만 구매됨: 공급 창이 열린 뒤 첫 실행에서 사고 끝남. 내용이 같아도 계획을 다시 쓰면 새 계획이라 "
        "다음 창에서 다시 삼. 이미 구매한 계획은 만료되게 두어도 됨",
    ]


def section_seller(root, now):
    dec = Counter()
    for m in c.tail_json(
        os.path.join(root, ".r3/metrics.jsonl"), 30_000_000, b'"seller"'
    ):
        if (
            m.get("hook") == "seller"
            and (m.get("ts") or 0) >= now - 86400
            and m.get("decision") in SELLER_DECISIONS
        ):
            dec[m["decision"]] += 1
    if not dec:
        return ["[판매 24시간] 기록 없음"]
    return [
        "[판매 24시간] 요청 결과 " + ", ".join(f"{k} {v}" for k, v in dec.most_common())
    ]


def section_market(root, now, login=""):
    cat = c.load_json(os.path.join(root, ".market/catalog.json"), {}) or {}
    rev, n, ours = defaultdict(float), 0, 0.0
    for e in c.tail_json(
        os.path.join(root, ".r3/events.jsonl"), 40_000_000, b'"settle"'
    ):
        if (
            e.get("type") != "settle"
            or e.get("src") != "market"
            or (e.get("ts") or 0) < now - 86400
        ):
            continue
        d = e.get("data") or {}
        a = float(d.get("amount") or 0)
        n += 1
        rev[(cat.get(d.get("sku")) or {}).get("category", "?")] += a
        if c.settle_side(d, login) == "shop":
            ours += a
    tot = sum(rev.values())
    shops = c.load_json(os.path.join(root, ".market/shops.json"))
    out = []
    if n:
        out.append(
            f"[시장 24시간] 정산 {n}건, 거래액 {c.fmt(tot)} (우리 가게 {ours / tot:.1%}), 품목군별 비중: "
            + ", ".join(
                f"{k} {v / tot:.0%}"
                for k, v in sorted(rev.items(), key=lambda kv: -kv[1])[:6]
            )
        )
    else:
        out.append("[시장 24시간] 정산 기록 없음")
    out.append(
        f"[시장] 지금 목록의 가게 {len(shops) if isinstance(shops, (list, dict)) else '-'}곳"
    )
    return out


def section_forum(root, login, snaps, now):
    out = []
    f = c.load_json(os.path.join(root, ".market/forum.json"))
    posts = (f.get("posts") if isinstance(f, dict) else f) or []
    mine = [p for p in posts if c.is_mine(p, login)]
    agg = defaultdict(lambda: [0, 0, 0, 0])
    for p in mine:
        a = agg[p.get("role") or "?"]
        a[0] += 1
        a[1] += p.get("likes") or 0
        a[2] += p.get("dislikes") or 0
        a[3] += p.get("score") or 0
    out.append(
        f"[포럼] 최근 글 {len(posts)}개 중 우리 글 {len(mine)}개"
        + (
            ""
            if not mine
            else ": "
            + ", ".join(
                f"{r} {v[0]}개 (세는 좋아요 {v[1]}, 싫어요 {v[2]}, 점수 합 {v[3]})"
                for r, v in agg.items()
            )
        )
    )
    last = max((p.get("ts") or 0 for p in mine), default=0)
    if last:
        out.append(f"  우리 마지막 글 {c.kst(last)} ({(now - last) / 3600:.1f}시간 전)")
    hist = []
    for ts, path in snaps[:: max(1, len(snaps) // 8)] + snaps[-1:]:
        b = c.load_json(path, {}) or {}
        me = next((x for x in b.get("buyers") or [] if c.is_mine(x, login)), None)
        if me:
            hist.append(
                f"{c.kst(ts, '%m-%d %H:%M')} forum {me.get('forum') or 0:.3f} posts {me.get('posts') or 0}"
            )
    if hist:
        out.append("  구매자 forum 점수 흐름: " + " | ".join(dict.fromkeys(hist)))
    return out


def section_calls(root, now):
    from kernel.trajectory import Trajectory

    rows = [
        r
        for r in Trajectory(os.path.join(root, "a2/trajectory.jsonl")).since(
            now - 86400
        )
        if r.get("kind") == "llm"
    ]
    if not rows:
        return ["[모델 호출 24시간] 기록 없음"]
    by = Counter(r.get("status") for r in rows)
    lat = [r["latency_s"] for r in rows if isinstance(r.get("latency_s"), (int, float))]
    refused = sum(v for k, v in by.items() if k in ("quota", "refused"))
    return [
        f"[모델 호출 24시간] {len(rows)}번, 결과 "
        + ", ".join(f"{k} {v}" for k, v in by.most_common())
        + f", 구독 한도 거절 {refused}번"
        + (f", 지연 중앙 {st.median(lat):.1f}초" if lat else "")
    ]


def _list_price(cat, sku):
    item = cat.get(sku) if isinstance(sku, str) else None
    item = item if isinstance(item, dict) else {}
    lp = item.get("list_price", item.get("price"))
    return lp if isinstance(lp, (int, float)) and lp > 0 else None


def _leaf(category):
    return str(category or "?").split(">")[-1].strip() or "?"


def _catalog(root):
    return restock._catalog(c.load_json(os.path.join(root, ".market/catalog.json"), {}) or {})


def seller_offers(root, cat, since):
    """[(ts, category leaf)] of each seller open (a unit offered to a request) since `since`, from .r3/metrics.jsonl.
    Declines carry no SKU, so per category the denominator is offers, not requests."""
    out = []
    for m in c.tail_json(os.path.join(root, ".r3/metrics.jsonl"), 60_000_000, b'"open"'):
        if m.get("hook") != "seller" or m.get("decision") != "open" or (m.get("ts") or 0) < since:
            continue
        o = m.get("outcome")
        if isinstance(o, str):
            try:
                o = json.loads(o)
            except ValueError:
                o = {}
        sku = o.get("sku") if isinstance(o, dict) else None
        out.append((m["ts"], _leaf((cat.get(sku) or {}).get("category"))))
    return out


def market_settles(root, since):
    """Every market settle since `since` (all shops, ours included)."""
    return [
        e
        for e in c.tail_json(os.path.join(root, ".r3/events.jsonl"), 40_000_000, b'"settle"')
        if e.get("type") == "settle" and e.get("src") == "market" and (e.get("ts") or 0) >= since
    ]


def category_stats(offers, settles, cat, login, since, until):
    """{category leaf: {offers, sales, market, our_ratio, market_ratio}} in [since, until).

    offers: our seller opens; sales: our shop's settles; market: every settle of the category in the same hours. sales /
    market is our share of what the market sold then, which the time of day and the request mix move far less than a
    raw conversion. Ratios are median amount / list price.
    """
    acc = defaultdict(lambda: {"offers": 0, "sales": 0, "market": 0, "ours": [], "all": []})
    for ts, leaf in offers:
        if since <= ts < until:
            acc[leaf]["offers"] += 1
    for e in settles:
        if not since <= (e.get("ts") or 0) < until:
            continue
        d = e.get("data") or {}
        k = acc[_leaf(d.get("category") or (cat.get(d.get("sku")) or {}).get("category"))]
        k["market"] += 1
        lp = d.get("list_price") if isinstance(d.get("list_price"), (int, float)) else _list_price(cat, d.get("sku"))
        a = d.get("amount")
        r = a / lp if lp and lp > 0 and isinstance(a, (int, float)) and not isinstance(a, bool) else None
        if r is not None:
            k["all"].append(r)
        if c.settle_side(d, login) == "shop":
            k["sales"] += 1
            if r is not None:
                k["ours"].append(r)
    return {
        leaf: {
            "offers": v["offers"],
            "sales": v["sales"],
            "market": v["market"],
            "our_ratio": round(st.median(v["ours"]), 3) if v["ours"] else None,
            "market_ratio": round(st.median(v["all"]), 3) if v["all"] else None,
        }
        for leaf, v in acc.items()
    }


def mix_adjusted(prev, cur):
    """Sales per offer of both versions weighted by the previous version's offer mix, over categories offered in both.
    ((p_prev, se_prev), (p_cur, se_cur), n_categories) or None. Per category the rate is capped at 1 (a settle can
    follow an offer from the window before)."""
    common = [k for k in prev if prev[k]["offers"] and (cur.get(k) or {}).get("offers")]
    tot = sum(prev[k]["offers"] for k in common)
    if not common or not tot:
        return None

    def rate(stats):
        p = se2 = 0.0
        for k in common:
            w, n = prev[k]["offers"] / tot, stats[k]["offers"]
            q = min(1.0, stats[k]["sales"] / n)
            p += w * q
            se2 += w * w * q * (1 - q) / n
        return round(p, 4), round(se2**0.5, 4)

    return rate(prev), rate(cur), len(common)


def trial_results(root, now, harness_root=None):
    """The running seller trial, both arms counted from .r3/metrics.jsonl rows tagged with its trial_v, or None.

    A request is one of open, declined (by the model) or a2_unit_taken; a sale is an accept. Revenue and margin per
    request average over all requests, zeros included, so their SE is the plain standard error of a mean.
    """
    t = versions.trial_state(harness_root or HARNESS_ROOT).get("seller")
    if not t:
        return None
    cat = _catalog(root)
    acc = {
        a: {"requests": 0, "offers": 0, "sales": 0, "rev": [], "margin": [], "ratios": [], "cats": defaultdict(lambda: [0, 0])}
        for a in ("base", "trial")
    }
    for m in c.tail_json(os.path.join(root, ".r3/metrics.jsonl"), 60_000_000, b'"trial_v"'):
        if m.get("hook") != "seller" or (m.get("ts") or 0) < t.get("started_ts", 0):
            continue
        o = m.get("outcome")
        if isinstance(o, str):
            try:
                o = json.loads(o)
            except ValueError:
                continue
        if not isinstance(o, dict) or o.get("trial_v") != t.get("version") or o.get("arm") not in acc:
            continue
        a, d = acc[o["arm"]], m.get("decision")
        leaf = _leaf((cat.get(o.get("sku")) or {}).get("category"))
        if d in ("open", "declined", "a2_unit_taken"):
            a["requests"] += 1
        if d == "open":
            a["offers"] += 1
            a["cats"][leaf][0] += 1
        if d == "accept" and isinstance(o.get("price"), (int, float)):
            p = float(o["price"])
            a["sales"] += 1
            a["rev"].append(p)
            if isinstance(o.get("cost"), (int, float)):
                a["margin"].append(p - o["cost"])
            lp = _list_price(cat, o.get("sku"))
            if lp:
                a["ratios"].append(p / lp)
            a["cats"][leaf][1] += 1

    def mean_se(vals, n):
        if not n:
            return None, None
        m = sum(vals) / n
        var = sum(v * v for v in vals) / n - m * m
        return m, (max(var, 0.0) / n) ** 0.5

    arms = {}
    for k, a in acc.items():
        n = a["requests"]
        rev, rev_se = mean_se(a["rev"], n)
        mar, mar_se = mean_se(a["margin"], n)
        arms[k] = {
            "requests": n,
            "offers": a["offers"],
            "sales": a["sales"],
            "conversion": round(a["sales"] / n, 4) if n else None,
            "conversion_se": _se(min(1.0, a["sales"] / n), n) if n else None,
            "revenue_per_request": rev,
            "revenue_per_request_se": rev_se,
            "margin_per_request": mar,
            "margin_per_request_se": mar_se,
            "price_to_list_median": round(st.median(a["ratios"]), 4) if a["ratios"] else None,
            "by_category": {leaf: {"offers": x[0], "sales": x[1]} for leaf, x in a["cats"].items()},
        }
    return {"trial": t, "arms": arms}


HORIZON_CAP_H = 72  # [동시 비교]: a horizon past this is printed as "over 72 h" next to the hours left in the round


def horizon_hours(d, se, hours):
    """Hours more, at the same request rate, until a difference d with standard error se (after `hours`) would sit 2
    SE from zero, assuming d stays put: SE shrinks as 1/sqrt(time), so the total time is hours * (2 se / |d|)^2.
    0.0 when it already does, None when d is 0 or there is no time or SE yet."""
    if not hours or not se or se <= 0:
        return None
    if not d:
        return None
    total = hours * (2 * se / abs(d)) ** 2
    return max(0.0, total - hours)


def trial_cost_lines(base, cand, hours, now):
    """Two fact lines for the operator: what the candidate arm has gained or lost in margin against the current
    version on the requests sent to it, and how long each difference needs to reach 2 SE at today's rate."""
    out = []
    d = cand["margin_per_request"] - base["margin_per_request"]
    se = (cand["margin_per_request_se"] ** 2 + base["margin_per_request_se"] ** 2) ** 0.5
    n = cand["requests"]
    out.append(
        f"  후보로 보낸 요청 {n}건에서 지금 판 대비 이익 차이 누적 약 {'+' if d >= 0 else ''}{c.fmt(d * n)} "
        f"(SE {c.fmt(se * n)}, 요청당 차이 x 후보 요청 수, 추정)"
    )
    left = (c.ROUND_END - now) / 3600
    parts = []
    for label, dk in (("판매/요청", "conversion"), ("요청당 이익", "margin_per_request")):
        dd = cand[dk] - base[dk]
        ss = (cand[dk + "_se"] ** 2 + base[dk + "_se"] ** 2) ** 0.5
        h = horizon_hours(dd, ss, hours)
        if h is None:
            parts.append(f"{label} 차이가 0이라 시간으로 가려지지 않음")
        elif h == 0:
            parts.append(f"{label} 이미 2 SE 밖")
        elif h > HORIZON_CAP_H:
            parts.append(f"{label} {HORIZON_CAP_H}시간 넘게 더")
        else:
            parts.append(f"{label} 약 {h:.1f}시간 더")
    out.append(
        "  지금 차이가 그대로이고 요청 속도가 같다면 2 SE에 닿기까지: " + ", ".join(parts)
        + f" (추정, 회차 종료까지 {left:.0f}시간)"
    )
    return out


def section_trial(root, now, harness_root=None):
    r = trial_results(root, now, harness_root)
    if r is None:
        if not versions.TRIAL_ENABLED:
            return ["[동시 비교] 꺼짐. 판매자 수정은 edits로 바로 적용하고 약 1시간 뒤 [판별 성과]로 유지, 다시 수정, 되돌리기를 정함"]
        return [f"[동시 비교] 진행 중 없음. done의 trial로 판매자 후보 판을 지금 판과 같은 시간에 나란히 돌릴 수 있음 "
                f"(시간 상한 {versions.TRIAL_MAX_H}시간)"]
    t, base, cand = r["trial"], r["arms"]["base"], r["arms"]["trial"]
    hours = (now - t["started_ts"]) / 3600
    out = [
        f"[동시 비교 판매자] 후보 판 {t['version']} 대 지금 판 {t['base']}, {c.kst(t['started_ts'])}부터 {hours:.1f}시간, "
        f"새 요청의 {t['share']:.0%}를 후보로 보냄. 요청을 같은 시간에 무작위로 나눴으므로 시간대와 요청 구성은 두 판에 같게 걸림. "
        f"시간 상한 {versions.TRIAL_MAX_H}시간 (남은 {max(0.0, versions.TRIAL_MAX_H - hours):.1f}시간): 넘으면 커널이 판정 없이 끝내고 지금 판을 유지함",
        f"  후보가 바꾼 파일: {', '.join(t.get('files') or []) or '-'} | 이유: {t.get('reason') or '-'}",
    ]

    def line(label, a):
        if not a["requests"]:
            return f"  {label}: 요청 0"
        return (
            f"  {label}: 요청 {a['requests']}, 제시 {a['offers']}, 판매 {a['sales']}, "
            f"판매/요청 {a['conversion']:.3f} (SE {a['conversion_se']:.3f}), "
            f"요청당 매출 {c.fmt(a['revenue_per_request'])} (SE {c.fmt(a['revenue_per_request_se'])}), "
            f"요청당 이익(가격-원가) {c.fmt(a['margin_per_request'])} (SE {c.fmt(a['margin_per_request_se'])}), "
            f"정가 대비 가격 중앙 {_num_or_dash(a['price_to_list_median'])}"
        )

    out.append(line(f"지금 판 {t['base']}", base))
    out.append(line(f"후보 판 {t['version']}", cand))
    if base["requests"] and cand["requests"]:
        def diff(k, fmt):
            d = cand[k] - base[k]
            se = (cand[k + "_se"] ** 2 + base[k + "_se"] ** 2) ** 0.5
            return f"{fmt(d)} (SE {fmt(se)})"

        sign = lambda f: (lambda x: ("+" if x >= 0 else "") + f(x))
        out.append(
            "  차이(후보 - 지금): 판매/요청 " + diff("conversion", sign(lambda x: f"{x:.3f}"))
            + ", 요청당 매출 " + diff("revenue_per_request", sign(c.fmt))
            + ", 요청당 이익 " + diff("margin_per_request", sign(c.fmt))
        )
        out += trial_cost_lines(base, cand, hours, now)
    cats = Counter()
    for a in (base, cand):
        for k, v in a["by_category"].items():
            cats[k] += v["offers"]
    if cats:
        cell = lambda a, k: "{sales}/{offers}".format(**a["by_category"].get(k, {"offers": 0, "sales": 0}))
        out.append(
            "  상품군별 판매/제시 (지금, 후보): "
            + ", ".join(f"{k} {cell(base, k)}, {cell(cand, k)}" for k, _ in cats.most_common(CATEGORY_TOP))
        )
    out.append("  별점은 리뷰가 거래 단위로 이어지지 않아 판별로 나누지 않음. 위 [판별 성과]의 판매와 별점은 두 판이 섞인 값임")
    return out


def section_categories(root, now, login):
    """Our sales per offer, our share of market settles and price/list, ours and the market's, per category in the
    last FLOW_BLOCKS blocks of FLOW_BLOCK_H hours. Numbers only."""
    cat = _catalog(root)
    since = now - FLOW_BLOCKS * FLOW_BLOCK_H * 3600
    offers, settles = seller_offers(root, cat, since), market_settles(root, since)
    blocks = [(since + i * FLOW_BLOCK_H * 3600, since + (i + 1) * FLOW_BLOCK_H * 3600) for i in range(FLOW_BLOCKS)]
    stats = [category_stats(offers, settles, cat, login, a, b) for a, b in blocks]
    top = Counter()
    for s in stats:
        for k, v in s.items():
            top[k] += v["offers"] + v["sales"]
    top = [k for k, n in top.most_common() if n][:CATEGORY_TOP]
    if not top:
        return ["[상품군별 흐름] 우리 제시와 판매 기록 없음"]
    empty = {"offers": 0, "sales": 0, "market": 0, "our_ratio": None, "market_ratio": None}

    def arrow(xs):
        return " → ".join(xs)

    out = [
        f"[상품군별 흐름] {c.kst(blocks[0][0], '%H:%M')}부터 {FLOW_BLOCK_H}시간씩 {FLOW_BLOCKS}칸, 오래된 칸에서 최근 칸 순. "
        "판매/제시는 우리 가게, 시장 점유는 같은 시간 그 상품군 시장 전체 정산 중 우리 판매 비율, "
        "체결가는 정가 대비 중앙값"
    ]
    for k in top:
        cells = [s.get(k, empty) for s in stats]
        sales = arrow(f"{x['sales']}/{x['offers']}" for x in cells)
        share = arrow(f"{x['sales'] / x['market']:.0%}" if x["market"] else "-" for x in cells)
        mkt = ", ".join(str(x["market"]) for x in cells)
        ours = arrow(_num_or_dash(x["our_ratio"]) for x in cells)
        allr = arrow(_num_or_dash(x["market_ratio"]) for x in cells)
        out.append(f"  {k}: 판매/제시 {sales} | 시장 점유 {share} (시장 정산 {mkt}) | 체결가 우리 {ours}, 시장 {allr}")
    return out


def version_windows(rows, target, now, default_hours=6.0, current=None):
    """[{version, since_ts, until_ts, since_known}] oldest first, from harness_edit and harness_revert rows.

    A version is active from its apply ts until the next edit or revert of the same target. The version before the
    first recorded edit is read from the last decision row of that agent before it; its start is unknown, so its
    window is the default_hours before the edit. With no edit and no decision row, `current` (the version on disk)
    gets the last default_hours.
    """
    edits = sorted(
        (r for r in rows if r.get("kind") in ("harness_edit", "harness_revert") and r.get("target") == target),
        key=lambda r: r.get("ts") or 0,
    )
    dec = [r for r in rows if r.get("kind") == "decision" and r.get("agent") == target]
    wins = []
    for i, r in enumerate(edits):
        until = edits[i + 1]["ts"] if i + 1 < len(edits) else now
        wins.append({"version": r.get("version"), "since_ts": r["ts"], "until_ts": until, "since_known": True})
    first = edits[0]["ts"] if edits else now
    before = [r for r in dec if (r.get("ts") or 0) < first and r.get("harness_version") is not None]
    if not before and not edits and current is not None:
        before = [{"harness_version": current}]
    if before:
        wins.insert(
            0,
            {
                "version": before[-1]["harness_version"],
                "since_ts": first - default_hours * 3600,
                "until_ts": first,
                "since_known": False,
            },
        )
    return wins


def _se(p, n):
    return round((p * (1 - p) / n) ** 0.5, 4) if n and 0 <= p <= 1 else None


def version_metrics(target, rows, settles, reviews, cat, login, since, until):
    """Numbers for one target in [since, until): seller from decision rows, our-shop settles and reviews of our shop;
    buyer from buyer_exec outcome rows (SKU from the accepted attempt of the same order)."""

    def inside(r):
        return since <= (r.get("ts") or 0) < until

    m = {}
    if target == "seller":
        dec = [
            r
            for r in rows
            if r.get("kind") == "decision"
            and r.get("agent") == "seller"
            and r.get("decision_kind") == "seller.open"
            and r.get("arm") != "trial"  # a side-by-side candidate's decisions belong to [동시 비교], not this version
            and inside(r)
        ]
        acts = Counter((r.get("decision") or {}).get("action") for r in dec)
        sold = [
            e
            for e in settles
            if inside(e) and c.settle_side(e.get("data") or {}, login) == "shop"
        ]
        ratios = []
        for e in sold:
            d = e.get("data") or {}
            lp = _list_price(cat, d.get("sku"))
            if lp and isinstance(d.get("amount"), (int, float)):
                ratios.append(d["amount"] / lp)
        m = {
            "requests": len(dec),
            "offers": acts.get("offer", 0),
            "declines": acts.get("decline", 0),
            "sales": len(sold),
        }
        if dec:
            m["conversion"] = round(len(sold) / len(dec), 4)
            m["conversion_se"] = _se(len(sold) / len(dec), len(dec))
        if ratios:
            m["price_to_list_median"] = round(st.median(ratios), 4)
            m["price_to_list_n"] = len(ratios)
        stars = [
            (e.get("data") or {}).get("stars")
            for e in reviews
            if inside(e) and e.get("role") == "shop"
        ]
        stars = [s for s in stars if isinstance(s, (int, float)) and not isinstance(s, bool)]
        if stars:
            m["stars_mean"] = round(st.mean(stars), 3)
            m["stars_n"] = len(stars)
    elif target == "buyer":
        ex = [r for r in rows if r.get("kind") == "buyer_exec" and r.get("agent") == "buyer"]
        sku_of = {
            r.get("order"): r.get("sku")
            for r in ex
            if r.get("event") == "attempt" and r.get("result") == "accepted"
        }
        outs = [r for r in ex if r.get("event") == "outcome" and inside(r)]
        deals = [r for r in outs if r.get("deal")]
        ratios = []
        for r in deals:
            lp = _list_price(cat, sku_of.get(r.get("order")))
            if lp and isinstance(r.get("price"), (int, float)):
                ratios.append(r["price"] / lp)
        m = {"orders": len(outs), "deals": len(deals)}
        if outs:
            m["deal_rate"] = round(len(deals) / len(outs), 4)
            m["deal_rate_se"] = _se(len(deals) / len(outs), len(outs))
        if ratios:
            m["price_to_list_median"] = round(st.median(ratios), 4)
            m["price_to_list_n"] = len(ratios)
    return m


def version_results(root, target, now, rows=None, last=2, harness_root=None):
    """The last `last` versions of target with their window and metrics: [{version, since_ts, until_ts,
    since_known, metrics}]."""
    login = c.owner_login(root)
    if rows is None:
        rows = list(c.tail_json(os.path.join(root, "a2/trajectory.jsonl"), 50_000_000))
    hdir = os.path.join(harness_root or HARNESS_ROOT, target, "VERSION")
    try:
        with open(hdir, encoding="utf-8") as f:
            current = int(f.read().strip())
    except (OSError, ValueError):
        current = None
    wins = version_windows(rows, target, now, current=current)[-last:]
    if not wins:
        return []
    lo = min(w["since_ts"] for w in wins)
    settles, reviews, offers = [], [], []
    cat = c.load_json(os.path.join(root, ".market/catalog.json"), {}) or {}
    if target == "seller":
        settles = market_settles(root, lo)
        offers = seller_offers(root, _catalog(root), lo)
        reviews = [
            e
            for e in c.tail_json(os.path.join(root, ".r3/events.jsonl"), 40_000_000, b'"review"')
            if e.get("type") == "review" and (e.get("ts") or 0) >= lo
        ]
    out = []
    for w in wins:
        m = version_metrics(target, rows, settles, reviews, cat, login, w["since_ts"], w["until_ts"])
        if target == "seller":
            by = category_stats(offers, settles, _catalog(root), login, w["since_ts"], w["until_ts"])
            m["by_category"] = {k: {x: v[x] for x in ("offers", "sales", "market")} for k, v in by.items()}
            n_mkt = sum(v["market"] for v in by.values())
            if n_mkt:
                m["market_share"] = round(m["sales"] / n_mkt, 4)
                m["market_share_se"] = _se(m["sales"] / n_mkt, n_mkt)
                m["market_settles"] = n_mkt
        out.append(dict(w, metrics=m))
    return out


def _num_or_dash(v):
    return f"{v:.3f}" if isinstance(v, float) else str(v) if v is not None else "-"


def section_versions(root, now, rows=None):
    """Per-version numbers for seller and buyer (current and previous), n and binomial SE. Numbers only."""
    out = []
    for target, label in (("seller", "판매자"), ("buyer", "구매자")):
        res = version_results(root, target, now, rows)
        if not res:
            out.append(f"[판별 성과 {label}] 판 기록 없음")
            continue
        for i, r in enumerate(reversed(res)):
            m = r["metrics"]
            start = c.kst(r["since_ts"]) if r["since_known"] else f"시작 불명, {c.kst(r['since_ts'])}부터만 셈"
            end = "지금" if i == 0 else c.kst(r["until_ts"])
            head = (
                f"[판별 성과 {label}] {'지금' if i == 0 else '이전'} 판 {r['version']} ({start}~{end}, "
                f"{(r['until_ts'] - r['since_ts']) / 3600:.1f}시간): "
            )
            if target == "seller":
                parts = [
                    f"요청 {m['requests']}",
                    f"제시 {m['offers']}",
                    f"거절 {m['declines']}",
                    f"판매 {m['sales']}",
                    f"성사율 {_num_or_dash(m.get('conversion'))} (SE {_num_or_dash(m.get('conversion_se'))}, n {m['requests']})",
                    f"정가 대비 가격 중앙 {_num_or_dash(m.get('price_to_list_median'))} (n {m.get('price_to_list_n', 0)})",
                ]
                if "stars_mean" in m:
                    parts.append(f"별점 평균 {m['stars_mean']:.2f} (n {m['stars_n']})")
                if "market_share" in m:
                    parts.append(
                        f"시장 점유 {m['market_share']:.1%} (SE {m['market_share_se']:.1%}, 시장 정산 {m['market_settles']})"
                    )
            else:
                parts = [
                    f"주문 {m['orders']}",
                    f"거래 {m['deals']}",
                    f"거래율 {_num_or_dash(m.get('deal_rate'))} (SE {_num_or_dash(m.get('deal_rate_se'))}, n {m['orders']})",
                    f"정가 대비 가격 중앙 {_num_or_dash(m.get('price_to_list_median'))} (n {m.get('price_to_list_n', 0)})",
                ]
            out.append(head + ", ".join(parts))
        if target == "seller" and len(res) >= 2:
            out += _seller_compare(res[-2], res[-1])
    return out


def _seller_compare(prev, cur):
    # previous vs current seller version with the time of day and request mix taken out as far as the data allows
    pb, cb = prev["metrics"].get("by_category") or {}, cur["metrics"].get("by_category") or {}
    out = [f"[판 비교 판매자] 판 {prev['version']} → 판 {cur['version']}, 시간대와 요청 구성의 영향을 덜어낸 값"]
    mix = mix_adjusted(pb, cb)
    if mix:
        (p0, s0), (p1, s1), n = mix
        out.append(
            f"  구성 보정 판매/제시 (판 {prev['version']}의 상품군 구성으로 가중, 상품군 {n}개): "
            f"{p0:.3f} (SE {s0:.3f}) → {p1:.3f} (SE {s1:.3f})"
        )
    shares = []
    for k in sorted(set(pb) | set(cb), key=lambda k: -((pb.get(k) or {}).get("sales", 0) + (cb.get(k) or {}).get("sales", 0))):
        a, b2 = pb.get(k) or {}, cb.get(k) or {}
        if not (a.get("sales") or b2.get("sales")):
            continue
        f = lambda x: f"{x['sales'] / x['market']:.0%}" if x.get("market") else "-"
        shares.append(f"{k} {f(a)} → {f(b2)}")
    if shares:
        out.append("  상품군별 시장 점유: " + ", ".join(shares[:CATEGORY_TOP]))
    out.append(
        "  읽는 법: 시간대나 요청 구성이 바뀌어도 상품군별 시장 점유와 구성 보정 값은 덜 흔들림. 두 값이 같은 방향으로 "
        "SE의 2배 넘게 바뀌었으면 시간대나 구성 차이만으로는 설명하기 어려움. 시장 정산 수와 시장 체결가가 함께 "
        "움직였으면 시장 쪽 변화임"
    )
    return out


RIVALS_TOP = 5  # [상위 가게]: the top shops by shop all, plus ours
NEGOTIATION_H = 6  # [협상 결과]: window
NEGOTIATION_TOP = 4  # categories with the most offers
NEGOTIATION_SKUS = 2  # SKUs per category, by offers


def section_rivals(b, b6, login, hours6):
    """[상위 가게]: per top shop (by shop all) and ours, the rate of revenue, deals and book over the last snapshot
    gap (about 6 h) and revenue per deal in it. Other shops by hashed name. Numbers only."""
    shops = [s for s in b.get("shops") or [] if isinstance(s.get("all"), (int, float))]
    me = next((s for s in shops if c.is_mine(s, login)), None)
    if not shops or not hours6:
        return ["[상위 가게] 순위표 비교 불가 (스냅샷 부족)"]
    old = {s.get("name"): s for s in (b6 or {}).get("shops") or []}
    rank = sorted(shops, key=lambda s: -s["all"])
    rows = rank[:RIVALS_TOP] + ([me] if me is not None and me not in rank[:RIVALS_TOP] else [])
    out = [f"[상위 가게 {hours6:.0f}시간] 가게 all 순서, 각 값은 이 구간의 시간당 증가 (우리 가게 all 순위 "
           f"{rank.index(me) + 1 if me in rank else '-'}/{len(rank)})"]
    for s in rows:
        o = old.get(s.get("name"))
        who = "우리" if s is me else c.hid(s.get("name"))
        if not o:
            out.append(f"  {who}: all {s['all']:.3f}, {hours6:.0f}시간 전 기록 없음")
            continue
        d = lambda k: ((s.get(k) or 0) - (o.get(k) or 0))
        deals = d("deals")
        out.append(
            f"  {who}: all {s['all']:.3f}, 매출 {c.fmt(d('revenue') / hours6)}/h, 거래 {deals / hours6:.0f}/h, "
            f"거래당 매출 {c.fmt(d('revenue') / deals) if deals > 0 else '-'}, book {c.fmt(d('book') / hours6)}/h, "
            f"재고 {s.get('stock_units', '-')}개, 별점 {s.get('stars', '-')}"
        )
    return out


# [가게 목표]: 성수님 목표(2026-11-06 17시) 가게 all 0.90. The formula below reproduces the board's shop all for all
# 58 shops within 0.0005 (11-06 16:50 snapshot): stars_rank, not stars, and a mid-rank percentile over every shop.
SHOP_GOAL = 0.90
SHOP_FORMULA = (("book", 0.5), ("revenue", 0.3), ("stars_rank", 0.2))
GOAL_STEPS = 5  # ranks above us listed per column


def _pct(vals, v):
    """Mid-rank percentile of v among vals (v included): ties count half."""
    if len(vals) < 2:
        return 0.0
    below = sum(1 for x in vals if x < v)
    eq = sum(1 for x in vals if x == v)
    return (below + 0.5 * (eq - 1)) / (len(vals) - 1)


def shop_all(shops, s):
    return sum(w * _pct([t.get(k) or 0 for t in shops], s.get(k) or 0) for k, w in SHOP_FORMULA)


def section_shop_goal(b, b6, login, hours6):
    """[가게 목표]: our shop all against SHOP_GOAL, the formula check against the board, and per column our rank,
    percentile, rate over the snapshot gap and, for the next GOAL_STEPS ranks above, the gap, that shop's rate and
    the shop all we would have there with the other columns unchanged. Numbers only."""
    shops = [s for s in b.get("shops") or [] if isinstance(s.get("all"), (int, float))]
    me = next((s for s in shops if c.is_mine(s, login)), None)
    if me is None or len(shops) < 2:
        return [f"[가게 목표 {SHOP_GOAL:.2f}] 우리 가게 행 없음"]
    old = {s.get("name"): s for s in (b6 or {}).get("shops") or []}
    err = max(abs(shop_all(shops, s) - s["all"]) for s in shops)
    rank = 1 + sum(1 for s in shops if s["all"] > me["all"])
    out = [f"[가게 목표 {SHOP_GOAL:.2f}] 우리 가게 all {me['all']:.3f} ({len(shops)}곳 중 {rank}위), 목표까지 "
           f"{SHOP_GOAL - me['all']:+.3f}",
           f"  공식: " + " + ".join(f"{w} {k}" for k, w in SHOP_FORMULA) + f" 백분위 ({len(shops)}곳, 동점은 중간 순위); "
           f"순위표 all과 최대 오차 {err:.4f}"]
    rate = lambda s, k: (((s.get(k) or 0) - ((old.get(s.get("name")) or {}).get(k) or 0)) / hours6
                         if hours6 and s.get("name") in old else None)
    fmt_rate = lambda r, k: "-" if r is None else (f"{r * hours6:+.2f}/{hours6:.0f}시간" if k == "stars_rank"
                                                    else f"{c.fmt(r)}/h")
    for k, w in SHOP_FORMULA:
        vals = [s.get(k) or 0 for s in shops]
        v = me.get(k) or 0
        r = 1 + sum(1 for x in vals if x > v)
        out.append(f"  {k}: {c.fmt(v)}, {r}위, 백분위 {_pct(vals, v):.3f}, 1위면 all {w * (1 - _pct(vals, v)):+.3f}, "
                   f"우리 {fmt_rate(rate(me, k), k)}")
        steps = []
        for tv in sorted({x for x in vals if x > v})[:GOAL_STEPS]:  # one entry per value: the board rounds stars
            at = [s for s in shops if (s.get(k) or 0) == tv]
            gain = w * (_pct(vals, tv) - _pct(vals, v))
            who = f"그 가게 {fmt_rate(rate(at[0], k), k)}" if len(at) == 1 else f"{len(at)}곳"
            steps.append(f"{1 + sum(1 for x in vals if x > tv)}위 +{c.fmt(tv - v)} ({who}, all {gain:+.3f})")
        if steps:
            out.append("    위 가게: " + "; ".join(steps))
    return out


PROFIT_H = 6  # [이익]: window
PROFIT_TOP = 5  # categories by our revenue


def section_profit(root, now, login):
    """[이익]: our sales in the last PROFIT_H hours with the unit cost of each sold unit (inventory unit_gone, joined by
    unit), gross profit and markup per category, next to what the whole market sold in that category and its median
    price/list. book = cash + unsold stock at cost (RULES), so book grows by this gross minus fees and expired stock."""
    since = now - PROFIT_H * 3600
    cat = _catalog(root)
    cost = {}
    for e in c.tail_json(os.path.join(root, ".r3/events.jsonl"), 40_000_000, b'"unit_gone"'):
        d = e.get("data") or {}
        if e.get("type") == "unit_gone" and (e.get("ts") or 0) >= since - 3600 and d.get("unit"):
            cost[d["unit"]] = (float(d.get("cost") or 0), d.get("why"), _leaf(d.get("category")))
    ours, mkt = defaultdict(lambda: [0, 0.0, 0.0, 0, []]), defaultdict(lambda: [0.0, []])
    expired = sum(v[0] for v in cost.values() if v[1] == "expired")
    for e in market_settles(root, since):
        d = e.get("data") or {}
        a = float(d.get("amount") or 0)
        leaf = _leaf((cat.get(d.get("sku")) or {}).get("category"))
        lp = _list_price(cat, d.get("sku"))
        mkt[leaf][0] += a
        if lp:
            mkt[leaf][1].append(a / lp)
        if c.settle_side(d, login) != "shop":
            continue
        u = cost.get(d.get("unit"))
        if u and leaf == "?":
            leaf = u[2]
        x = ours[leaf]
        x[0] += 1
        x[1] += a
        if u:
            x[2] += u[0]
            x[3] += 1
            x[4].append(a / u[0] if u[0] else 0)
    if not ours:
        return [f"[이익 {PROFIT_H}시간] 우리 판매 기록 없음"]
    R = sum(x[1] for x in ours.values())
    K = sum(x[2] for x in ours.values())
    Rk = sum(x[1] for x in ours.values() if x[3] == x[0])
    out = [f"[이익 {PROFIT_H}시간] 우리 판매 {sum(x[0] for x in ours.values())}건, 매출 {c.fmt(R)}, 판 물건 원가 {c.fmt(K)}, "
           f"매출 총이익 {c.fmt(R - K)} ({(R - K) / R:.1%}); 원가가 이어진 판매 {sum(x[3] for x in ours.values())}건, "
           f"만료 폐기 원가 {c.fmt(expired)}"]
    for leaf, x in sorted(ours.items(), key=lambda kv: -kv[1][1])[:PROFIT_TOP]:
        m = mkt.get(leaf) or [0.0, []]
        out.append(f"  {leaf}: 우리 {x[0]}건, 매출 {c.fmt(x[1])}, 총이익 {c.fmt(x[1] - x[2])}, 판매가/원가 중앙 "
                   f"{st.median(x[4]) if x[4] else 0:.2f}; 시장 전체 매출 {c.fmt(m[0])}, 우리 몫 "
                   f"{x[1] / m[0] if m[0] else 0:.1%}, 시장 판매가/정가 중앙 {st.median(m[1]) if m[1] else 0:.2f}")
    return out


SHARE_BLOCK_H = 3  # [시장 몫 24시간]: eight 3 h blocks, so a fall that began before the 6 h windows shows with its start
SHARE_BLOCKS = 8
SHARE_TOP = 2  # market categories by revenue shown per block


def section_share24(root, now, login):
    """[시장 몫 24시간]: per SHARE_BLOCK_H hour block, the market's and our revenue and our share, and for the market's
    SHARE_TOP categories by revenue our share, the market's and our median price/list and how many shops sold them.
    The 6 h sections cannot show when a change began; this one can. Numbers only."""
    since = now - SHARE_BLOCK_H * SHARE_BLOCKS * 3600
    cat = _catalog(root)
    blk = lambda ts: SHARE_BLOCKS - 1 - int((now - ts) // (SHARE_BLOCK_H * 3600))
    tot = defaultdict(lambda: [0.0, 0.0, 0])
    leaf_rev = Counter()
    per = defaultdict(lambda: defaultdict(lambda: {"m": 0.0, "u": 0.0, "mr": [], "ur": [], "shops": set()}))
    for e in c.tail_json(os.path.join(root, ".r3/events.jsonl"), 80_000_000, b'"settle"'):
        if e.get("type") != "settle" or e.get("src") != "market" or (e.get("ts") or 0) < since:
            continue
        d = e.get("data") or {}
        i = blk(e["ts"])
        if not 0 <= i < SHARE_BLOCKS:
            continue
        a = float(d.get("amount") or 0)
        leaf = _leaf((cat.get(d.get("sku")) or {}).get("category"))
        lp = _list_price(cat, d.get("sku"))
        mine = c.settle_side(d, login) == "shop"
        tot[i][0] += a
        leaf_rev[leaf] += a
        x = per[leaf][i]
        x["m"] += a
        x["shops"].add(str(d.get("shop")))
        if lp:
            x["mr"].append(a / lp)
        if mine:
            tot[i][1] += a
            tot[i][2] += 1
            x["u"] += a
            if lp:
                x["ur"].append(a / lp)
    if not tot:
        return ["[시장 몫 24시간] 체결 기록 없음"]
    top = [k for k, _ in leaf_rev.most_common(SHARE_TOP) if k != "?"]
    med = lambda v: f"{st.median(v):.2f}" if v else "-"
    out = [f"[시장 몫 24시간, {SHARE_BLOCK_H}시간 단위] 시작 시각: 시장 매출, 우리 매출(판매 건수), 몫 | "
           + "; ".join(f"{k} 우리 몫, 판매가/정가 중앙 시장 대 우리, 판 가게 수" for k in top)]
    for i in range(SHARE_BLOCKS):
        m, u, n = tot[i]
        t0 = time.strftime("%H:%M", time.localtime(now - (SHARE_BLOCKS - i) * SHARE_BLOCK_H * 3600))
        parts = []
        for k in top:
            x = per[k][i]
            parts.append(f"{x['u'] / x['m'] if x['m'] else 0:.1%}, {med(x['mr'])} 대 {med(x['ur'])}, {len(x['shops'])}곳")
        out.append(f"  {t0}: {c.fmt(m)}, {c.fmt(u)}({n}건), {u / m if m else 0:.1%} | " + "; ".join(parts))
    return out


STARS_H = 6  # [별점]: window
STARS_LOW = 3  # lowest reviewer accounts shown
STARS_MIN_N = 3  # reviews an account needs to be listed as low
STARS_TOP = 4  # categories with the most reviews


def section_stars(root, now, login):
    """[별점]: reviews of our shop in the last STARS_H hours, joined to our settles by order: the plain mean, the mean of
    per-account means (patch 3.4 counts one mean per reviewing account), the accounts with the lowest means, and the
    mean per grade and per category. Accounts appear as hashed ids."""
    since = now - STARS_H * 3600
    cat = _catalog(root)
    rv, ours = [], {}
    for e in c.tail_json(os.path.join(root, ".r3/events.jsonl"), 40_000_000, b'"review"'):
        if e.get("type") == "review" and e.get("role") == "shop" and (e.get("ts") or 0) >= since:
            d = e.get("data") or {}
            if isinstance(d.get("stars"), (int, float)) and not isinstance(d.get("stars"), bool):
                rv.append(d)
    if not rv:
        return [f"[별점 {STARS_H}시간] 우리 가게 리뷰 기록 없음"]
    for e in market_settles(root, since - 6 * 3600):
        d = e.get("data") or {}
        if d.get("order") and c.settle_side(d, login) == "shop":
            ours[d["order"]] = d
    acc, grade, leaf = defaultdict(list), defaultdict(list), defaultdict(list)
    for d in rv:
        s = ours.get(d.get("order"))
        if not s:
            continue
        acc[c.hid(s.get("buyer"))].append(d["stars"])
        grade[s.get("grade") or "-"].append(d["stars"])
        leaf[_leaf(s.get("category") or (cat.get(s.get("sku")) or {}).get("category"))].append(d["stars"])
    mean = lambda v: st.mean(v) if v else 0.0
    linked = sum(len(v) for v in acc.values())
    out = [f"[별점 {STARS_H}시간] 우리 가게 리뷰 {len(rv)}건, 평균 {mean([d['stars'] for d in rv]):.2f}; 판매와 이어진 {linked}건"]
    if not acc:
        return out
    am = {k: mean(v) for k, v in acc.items()}
    out.append(f"  계정별 평균의 평균 {mean(list(am.values())):.2f} (계정 {len(am)}곳; 패치 3.4 별점은 리뷰한 계정마다 평균 하나로 셈)")
    low = sorted((k for k in am if len(acc[k]) >= STARS_MIN_N), key=lambda k: am[k])[:STARS_LOW]
    if low:
        out.append("  평균이 낮은 계정: " + "; ".join(f"{k} 리뷰 {len(acc[k])}, 평균 {am[k]:.2f}" for k in low))
    out.append("  등급별: " + "; ".join(f"{g} 리뷰 {len(v)}, 평균 {mean(v):.2f}" for g, v in sorted(grade.items())))
    top = sorted(leaf, key=lambda k: -len(leaf[k]))[:STARS_TOP]
    out.append("  상품군별: " + "; ".join(
        f"{k} 리뷰 {len(leaf[k])}, 평균 {mean(leaf[k]):.2f}, 2점 이하 {sum(1 for x in leaf[k] if x <= 2)}" for k in top))
    return out


def section_negotiation(root, now, login):
    """[협상 결과]: per category with the most offers in the last NEGOTIATION_H hours, how our offers ended (sold,
    our reject, buyer left, idle, no record) and, for the ends without a sale, the rounds and our asks against list;
    then per top SKU our first ask, our sale price and the market's settle price (all shops) against list, and our
    share of that SKU's settles. Rows before a2_deal_end existed count as no record."""
    since = now - NEGOTIATION_H * 3600
    cat = _catalog(root)
    per = defaultdict(lambda: {"open": 0, "accept": 0, "reject": 0, "cancel": 0, "idle": 0, "r0": 0, "ask0": [], "last": []})
    sku = defaultdict(lambda: {"open": 0, "ask0": [], "sold": []})
    for m in c.tail_json(os.path.join(root, ".r3/metrics.jsonl"), 60_000_000, b'"seller"'):
        if m.get("hook") != "seller" or (m.get("ts") or 0) < since:
            continue
        o = m.get("outcome")
        if isinstance(o, str):
            try:
                o = json.loads(o)
            except ValueError:
                continue
        if not isinstance(o, dict) or not o.get("sku"):
            continue
        leaf = _leaf((cat.get(o["sku"]) or {}).get("category"))
        lp = _list_price(cat, o["sku"])
        d, a = m.get("decision"), per[leaf]
        if d == "open":
            a["open"] += 1
            sku[o["sku"]]["open"] += 1
            if lp and isinstance(o.get("ask"), (int, float)):
                sku[o["sku"]]["ask0"].append(o["ask"] / lp)
        elif d == "accept":
            a["accept"] += 1
            if lp and isinstance(o.get("price"), (int, float)):
                sku[o["sku"]]["sold"].append(o["price"] / lp)
        elif d == "reject":
            a["reject"] += 1
        elif d == "a2_deal_end" and o.get("why") in ("cancel", "idle"):
            a[o["why"]] += 1
            a["r0"] += 1 if not o.get("rounds") else 0
            if lp and isinstance(o.get("ask0"), (int, float)):
                a["ask0"].append(o["ask0"] / lp)
            if lp and isinstance(o.get("last_offer"), (int, float)):
                a["last"].append(o["last_offer"] / lp)
    top = sorted((k for k in per if per[k]["open"]), key=lambda k: -per[k]["open"])[:NEGOTIATION_TOP]
    if not top:
        return [f"[협상 결과 {NEGOTIATION_H}시간] 제시 기록 없음"]
    mk = defaultdict(lambda: {"all": [], "ours": 0, "n": 0})
    for e in market_settles(root, since):
        dd = e.get("data") or {}
        lp = _list_price(cat, dd.get("sku"))
        if dd.get("sku") in sku and lp and isinstance(dd.get("amount"), (int, float)):
            x = mk[dd["sku"]]
            x["all"].append(dd["amount"] / lp)
            x["n"] += 1
            x["ours"] += 1 if c.settle_side(dd, login) == "shop" else 0
    med = lambda v: f"{st.median(v):.2f}" if v else "-"
    out = [f"[협상 결과 {NEGOTIATION_H}시간] 상품군별 우리 제시의 끝 (판매, 우리 거절, 구매자가 닫음, 응답 없음, 기록 없음). "
           "기록 없음은 끝 기록(a2_deal_end)이 생기기 전 것이거나 시도 1이 맡은 것"]
    for leaf in top:
        a = per[leaf]
        ended = a["cancel"] + a["idle"]
        rest = a["open"] - a["accept"] - a["reject"] - ended
        line = (f"  {leaf}: 제시 {a['open']}, 판매 {a['accept']}, 우리 거절 {a['reject']}, 구매자가 닫음 {a['cancel']}, "
                f"응답 없음 {a['idle']}, 기록 없음 {max(0, rest)}")
        if ended:
            line += (f" | 판매 없이 끝난 {ended}건 중 첫 제시 뒤 바로 끝남 {a['r0']}, 우리 첫 제시가/정가 중앙 {med(a['ask0'])}, "
                     f"구매자 마지막 제안/정가 중앙 {med(a['last'])}")
        out.append(line)
        skus = sorted((k for k in sku if _leaf((cat.get(k) or {}).get("category")) == leaf), key=lambda k: -sku[k]["open"])
        for k in skus[:NEGOTIATION_SKUS]:
            x, y = sku[k], mk.get(k, {"all": [], "ours": 0, "n": 0})
            out.append(
                f"    {k}: 제시 {x['open']}, 우리 첫 제시가/정가 {med(x['ask0'])}, 우리 판매가/정가 {med(x['sold'])} (n {len(x['sold'])}), "
                f"시장 체결가/정가 {med(y['all'])} (시장 체결 {y['n']}, 우리 {y['ours']})"
            )
    return out


REFLECT_LOOKBACK_H = 6


def section_reflections(root, now, rows=None):
    """[지난 반성]: per target and version, the operator's last reflection (time and verdicts), how many reflections
    it wrote on that version in the last REFLECT_LOOKBACK_H hours, and how many new decisions of that agent came
    after the last one. Facts only: a version with few new decisions since its last reflection has little new
    evidence."""
    if rows is None:
        rows = list(c.tail_json(os.path.join(root, "a2/trajectory.jsonl"), 50_000_000))
    lo = now - REFLECT_LOOKBACK_H * 3600
    last, count = {}, Counter()
    for r in rows:
        if r.get("kind") == "reflection" and r.get("agent") == "operator" and (r.get("ts") or 0) >= lo:
            k = (r.get("target"), r.get("version"))
            count[k] += 1
            if (r.get("ts") or 0) >= (last.get(k, {}).get("ts") or 0):
                last[k] = r
    if not last:
        return [f"[지난 반성] 최근 {REFLECT_LOOKBACK_H}시간 기록 없음"]
    out = [f"[지난 반성] 최근 {REFLECT_LOOKBACK_H}시간, 판마다 마지막 반성과 그 뒤 새 결정 수"]
    for (target, version), r in sorted(last.items(), key=lambda kv: -(kv[1].get("ts") or 0)):
        ts = r.get("ts") or 0
        new = sum(
            1
            for x in rows
            if x.get("kind") == "decision" and x.get("agent") == target and (x.get("ts") or 0) > ts
        )
        verdicts = ", ".join(str(e.get("effect")) for e in r.get("effects") or [] if isinstance(e, dict)) or "-"
        out.append(
            f"  {target} 판 {version}: 마지막 {c.kst(ts, '%H:%M')} ({verdicts}), {REFLECT_LOOKBACK_H}시간 동안 "
            f"{count[(target, version)]}번, 그 뒤 새 {target} 결정 {new}건"
            if target in ("seller", "buyer")
            else f"  {target} 판 {version}: 마지막 {c.kst(ts, '%H:%M')} ({verdicts}), "
            f"{REFLECT_LOOKBACK_H}시간 동안 {count[(target, version)]}번"
        )
    return out


def section_harness(root, since_ts):
    from kernel.trajectory import Trajectory

    rows = [
        r
        for r in Trajectory(os.path.join(root, "a2/trajectory.jsonl")).since(since_ts)
        if r.get("kind") == "harness_edit"
    ]
    if not rows:
        return ["[지난 브리핑 이후 하네스 수정] 없음"]
    return [
        "[지난 브리핑 이후 하네스 수정] "
        + "; ".join(
            f"{c.kst(r['ts'], '%H:%M')} {r.get('target') or r.get('agent')} 판 {r.get('version')} {r.get('reason') or r.get('summary', '')}"[
                :200
            ]
            for r in rows
        )
    ]


def section_memory(root):
    """mem0 check score per ability (a2/memcheck.json) and daemon counters, each only when present."""
    from kernel import memcheck, memory

    out = []
    mc = c.load_json(os.path.join(root, "a2/memcheck.json"))
    if isinstance(mc, dict) and isinstance(mc.get("abilities"), dict):
        ab = mc["abilities"]
        ts = mc.get("ts")
        out.append(
            f"[기억 점검 LME-V2{' ' + c.kst(ts) if isinstance(ts, (int, float)) else ''}] "
            + ", ".join(
                f"{a} {(ab.get(a) or {}).get('correct', 0)}/{(ab.get(a) or {}).get('n', 0)}"
                for a in memcheck.ABILITIES
            )
        )
    s = memory.stats(memory.sock_path(root), timeout=0.3)
    if s:
        n = s.get("counts") or {}
        out.append(
            f"[기억 서버] 검색 {n.get('search', 0)}번, 추가 요청 {n.get('add', 0)}번, 추가 완료 "
            f"{n.get('add_done', 0)}번, 오류 {n.get('error', 0)}번, 대기열 {s.get('queue', 0)}"
        )
    elif out:
        out.append("[기억 서버] 응답 없음")
    return out or ["[기억] 점검 결과 없음, 기억 서버 응답 없음"]


def build(root=None, now=None, last_brief_ts=None):
    root = c.data_root(root)
    now = c.now_ts() if now is None else now
    login = c.owner_login(root)
    snaps = board_snapshots(root)
    out = []
    if not snaps:
        out += section_time(now, None)
        out.append("[순위] 순위표 스냅샷 없음")
        out += section_versions(root, now)
        out += section_trial(root, now)
        out += section_categories(root, now, login)
        out += section_calls(root, now)
    else:
        bts, bpath = snaps[-1]
        b = c.load_json(bpath, {}) or {}
        ts6, p6 = _snapshot_near(snaps, bts - 6 * 3600)
        b6 = (c.load_json(p6, {}) or {}) if ts6 < bts else {}
        hours6 = (bts - ts6) / 3600
        out += section_time(now, bts)
        out += section_rank(b, b6, login, hours6)
        out += section_shop_goal(b, b6, login, hours6)
        out += section_profit(root, now, login)
        out += section_share24(root, now, login)
        out += section_versions(root, now)
        out += section_rivals(b, b6, login, hours6)
        out += section_trial(root, now)
        out += section_reflections(root, now)
        out += section_categories(root, now, login)
        out += section_negotiation(root, now, login)
        out += section_stars(root, now, login)
        out += section_calls(root, now)
        out += section_side(
            b.get("shops") or [],
            b6.get("shops") or [],
            SHOP_KEYS,
            "가게",
            login,
            hours6,
        )
        out += section_side(
            b.get("buyers") or [],
            b6.get("buyers") or [],
            BUYER_KEYS,
            "구매자",
            login,
            hours6,
        )
    out += section_inventory(root, now)
    out += section_restock(root, now)
    out += section_seller(root, now)
    out += section_market(root, now, login)
    out += section_forum(root, login, snaps, now)
    out += section_memory(root)
    out += section_harness(
        root, last_brief_ts if last_brief_ts is not None else now - 1800
    )
    return c.mask("\n".join(out), (login,))


if __name__ == "__main__":
    print(build())
