"""Situation briefing for the operator: facts only, no interpretation, no advice.

build(root, now) -> text. Each section reads its own source and says when the source is missing, so a broken file
shows up as a fact instead of a crash. Other participants appear only as hashed ids.
"""

import functools
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
# RAW_KEYS enter all as their own value, not a percentile (Patch 3.5, 11-06: buyer forum rating 0 to 1).
ALL_WEIGHTS = {
    "가게": {"book": 0.5, "revenue": 0.3, "stars": 0.2, "stars_rank": 0.2},
    "구매자": {"saved": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1},
}
RAW_KEYS = {"구매자": ("forum",)}
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
        climb = climb_line(
            vals, v, (ALL_WEIGHTS.get(label) or {}).get(k), label, raw=k in RAW_KEYS.get(label, ())
        )
        if climb:
            out.append(climb)
    return out


def climb_line(vals, v, weight, label, raw=False):
    """'    바로 위 N곳까지 차이 ...' for one board column: the gaps from our value to the nearest values above it, and
    what one rank is worth in that side's all when the column has a weight (weight / (count - 1), an estimate since
    the board's percentile formula is not published). A raw column enters all as its value, so the line says what
    +0.01 is worth instead. None when we are first."""
    above = sorted(x for x in vals if x > v)[:CLIMB_N]
    if not above:
        return None
    line = f"    바로 위 {len(above)}곳까지 차이: " + ", ".join(f"+{c.fmt(x - v)}" for x in above)
    if weight and raw:
        line += f"; 순위가 아니라 값 그대로 all에 들어감(Patch 3.5): 0.01 오르면 {label} all +{weight * 0.01:.4f}"
    elif weight and len(vals) > 1:
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


NOSTOCK_WHY = {
    "none": "요청한 상품의 팔 수 있는 재고 없음(다른 협상에 묶인 것 포함)",
    "need": "적힌 요구 시간을 넘기는 단위 없음",
    "half_shelf": "요구 시간이 없는 요청이고 남은 기한이 진열기한 절반 이상인 단위 없음",
}
FOOD_TOP = 6


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def restock_food_lines(root, now, inv):
    """Food facts for [재입고]: per dated SKU in stock, the units a request with no stated need can get (남은 기한이
    진열기한 50% 이상, seller_bridge.UNSTATED_MIN_SHARE) and the median share of shelf left when supplied; then the last
    hour's no_stock rejections by reason. Live 11-06 20h: kimchi arrived at 36-46% of its shelf, so 0 of 22 could go
    to the requests without a stated need, and 43 of 63 no_stock rejections that hour were apples and kimchi."""
    from kernel.seller_bridge import UNSTATED_MIN_SHARE as share

    cat = _catalog(root)
    by = defaultdict(lambda: [0, 0, []])
    for u in inv.get("units") or []:
        if not isinstance(u, dict):
            continue
        sh = (cat.get(u.get("sku")) or {}).get("shelf_hours")
        e, s = u.get("expires_at"), u.get("supplied_at")
        if not _is_num(sh) or sh <= 0 or not _is_num(e) or e <= now:
            continue
        k = by[u.get("sku")]
        k[0] += 1
        k[1] += (e - now) / 3600 >= share * sh
        if _is_num(s) and e > s:
            k[2].append((e - s) / 3600 / sh)
    out = []
    if by:
        parts = [
            f"{k} {str((cat.get(k) or {}).get('name') or '')[:10]} {v[1]}/{v[0]}"
            + (f" (공급 때 {st.median(v[2]):.0%})" if v[2] else "")
            for k, v in sorted(by.items(), key=lambda x: -x[1][0])[:FOOD_TOP]
        ]
        out.append(
            f"  식품 재고 중 요구 시간이 없는 요청에 낼 수 있는 단위(남은 기한이 진열기한 {share:.0%} 이상)/재고, 괄호는 "
            "공급 때 남은 기한/진열기한의 중앙: " + ", ".join(parts)
        )
        out.append(
            f"  공급은 SKU와 수량만 고르고 남은 기한은 고를 수 없음. 공급 때 이미 {share:.0%} 미만인 단위는 요구 시간이 "
            "적힌 요청에만 팔리고, 못 팔면 만료 때 원가만큼 book에서 빠짐"
        )
    ns, nsk = Counter(), defaultdict(Counter)
    for m in c.tail_json(os.path.join(root, ".r3/metrics.jsonl"), 20_000_000, b'"no_stock"'):
        if m.get("decision") != "no_stock" or not _is_num(m.get("ts")) or m["ts"] < now - 3600:
            continue
        o = m.get("outcome")
        o = o if isinstance(o, dict) else {}
        sku = o.get("sku") if o.get("sku") in cat else None  # SKU_RE also matches words like "Greetings"
        w = o.get("why")
        # a request naming only the SKU has no name-word match (matched 0) though the SKU is in stock: keep its reason
        w = "?" if w not in NOSTOCK_WHY else "none" if not o.get("matched") and not by.get(sku) else w
        ns[w] += 1
        if sku:
            nsk[w][sku] += 1
    if ns:
        out.append(
            f"  최근 1시간 재고 없음 거절 {sum(ns.values())}건: "
            + "; ".join(
                f"{NOSTOCK_WHY.get(w, '사유 기록 전')} {n}"
                + (" (" + ", ".join(f"{k} {x}" for k, x in nsk[w].most_common(3)) + ")" if nsk[w] else "")
                for w, n in ns.most_common()
            )
        )
    return out


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
    if not os.path.exists(os.path.join(root, "a2/LIVE")):  # attempt 1 restocks; the plan file is not read
        return [
            f"[재입고] 재고 {len(inv.get('units') or [])}개, 최근 1시간 판매 {sold}개 | 공급 창 {win}",
            "  재입고는 규칙 코드가 공급 창마다 실행함: hooks/restock.py가 policy.py의 restock_plan 함수를 불러 "
            "policy의 stock_target, qty_per_buy, restock_priority와 섹션 shop의 한도로 살 물량을 정함(이 함수가 지금 실제 실행 경로임). "
            "쓰이지 않는 것은 시도 2 방식의 계획 파일(done의 restock_plan 항목)뿐임. 재고를 바꾸려면 값은 done.policy, 동작은 done.code",
        ] + restock_food_lines(root, now, inv)
    return [
        f"[재입고] 계획 {head} | 재고 {len(inv.get('units') or [])}개, 최근 1시간 판매 {sold}개 | 공급 창 {win}",
        "  재입고는 done의 restock_plan(a2/restock_plan.json)으로만 실행됨. 계획이 없거나 valid_until이 지나면 "
        "커널은 아무것도 사지 않음",
        "  계획 하나는 한 번만 구매됨: 공급 창이 열린 뒤 첫 실행에서 사고 끝남. 내용이 같아도 계획을 다시 쓰면 새 계획이라 "
        "다음 창에서 다시 삼. 이미 구매한 계획은 만료되게 두어도 됨",
    ] + restock_food_lines(root, now, inv)


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
    acc = defaultdict(lambda: {"offers": 0, "sales": 0, "market": 0, "ours": [], "all": [], "shops": set()})
    for ts, leaf in offers:
        if since <= ts < until:
            acc[leaf]["offers"] += 1
    for e in settles:
        if not since <= (e.get("ts") or 0) < until:
            continue
        d = e.get("data") or {}
        k = acc[_leaf(d.get("category") or (cat.get(d.get("sku")) or {}).get("category"))]
        k["market"] += 1
        k["shops"].add(str(d.get("shop")))
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
            "shops": len(v["shops"]),
        }
        for leaf, v in acc.items()
    }


MIX_MIN_OFFERS = 5  # a category enters the mix-adjusted rate only with this many offers in both versions


def mix_adjusted(prev, cur):
    """Sales per offer of both versions weighted by the previous version's offer mix, over categories with at least
    MIX_MIN_OFFERS offers in both. ((p_prev, se_prev), (p_cur, se_cur), n_categories) or None. Per category the rate
    is capped at 1 (a settle can follow an offer from the window before). The SE uses (sales + 1) / (offers + 2) per
    category, so a category at 0 or 1 out of a few offers does not count as certain (11-06: 김치 1/1 and 침구 5/5 had
    SE 0 and pushed the difference to 5.7 SE)."""
    common = [
        k for k in prev if prev[k]["offers"] >= MIX_MIN_OFFERS and (cur.get(k) or {}).get("offers", 0) >= MIX_MIN_OFFERS
    ]
    tot = sum(prev[k]["offers"] for k in common)
    if not common or not tot:
        return None

    def rate(stats):
        p = se2 = 0.0
        for k in common:
            w, n = prev[k]["offers"] / tot, stats[k]["offers"]
            q = min(1.0, stats[k]["sales"] / n)
            qs = min(1.0, (stats[k]["sales"] + 1) / (n + 2))
            p += w * q
            se2 += w * w * qs * (1 - qs) / n
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
            m["by_category"] = {
                k: {x: v[x] for x in ("offers", "sales", "market", "shops", "market_ratio", "our_ratio")}
                for k, v in by.items()
            }
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
        se = (s0**2 + s1**2) ** 0.5
        out.append(
            f"  구성 보정 판매/제시 (판 {prev['version']}의 상품군 구성으로 가중, 두 판 모두 제시 {MIX_MIN_OFFERS}건 이상인 상품군 {n}개): "
            f"{p0:.3f} (SE {s0:.3f}) → {p1:.3f} (SE {s1:.3f}), 차이 {p1 - p0:+.3f}, "
            f"차이/SE {(p1 - p0) / se if se else 0:+.1f}"
        )
    # per category, ours next to what the whole market did in the same hours, so a change on the market side (more
    # shops selling, a lower clearing price) is not read as an effect of the version
    hours = lambda r: max((r["until_ts"] - r["since_ts"]) / 3600, 1e-9)
    hp, hc = hours(prev), hours(cur)
    r2 = lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else "-"
    keys = sorted(set(pb) | set(cb), key=lambda k: -((pb.get(k) or {}).get("market", 0) + (cb.get(k) or {}).get("market", 0)))
    rows = []
    for k in keys[:COMPARE_TOP]:
        a, b2 = pb.get(k) or {}, cb.get(k) or {}
        f = lambda x: f"{x['sales'] / x['market']:.0%}" if x.get("market") else "-"
        rows.append(
            f"  {k}: 우리 몫 {f(a)} → {f(b2)} (판매 {a.get('sales', 0)} → {b2.get('sales', 0)}) | 시장 정산/시간 "
            f"{a.get('market', 0) / hp:.0f} → {b2.get('market', 0) / hc:.0f}, 판 가게 {a.get('shops', 0)} → "
            f"{b2.get('shops', 0)}곳, 시장 판매가/정가 중앙 {r2(a.get('market_ratio'))} → {r2(b2.get('market_ratio'))}, "
            f"우리 {r2(a.get('our_ratio'))} → {r2(b2.get('our_ratio'))}"
        )
    if rows:
        out.append(f"  상품군별 (시장 정산 많은 {len(rows)}개), 우리 대 같은 시간 시장 전체:")
        out += rows
    out.append(
        "  읽는 법: 차이/SE가 -2와 +2 사이면 잡음과 구분되지 않음. 판 가게 수, 시장 정산/시간, 시장 판매가가 같이 "
        "바뀌었으면 시장 쪽 변화가 섞인 것이고, 우리 몫과 우리 판매가만 바뀌었으면 판의 차이에 가까움"
    )
    return out


COMPARE_TOP = 3  # [판 비교]: categories by market settles in the two windows
RIVALS_TOP = 5  # [상위 가게]: the top shops by shop all, plus ours
RIVALS_MAX = 8  # [상위 가게]: every shop above us is listed, up to this many
RIVALS_CATS = 3  # [상위 가게]: category leaves per shop in its sold line
NEGOTIATION_H = 6  # [협상 결과]: window
NEGOTIATION_TOP = 4  # categories with the most offers
NEGOTIATION_SKUS = 2  # SKUs per category, by offers
ACCEPT_SETTLE_S = 600  # an accept counts as sold when our shop settles the same SKU within this many seconds after it


def rival_sales(settles, cat, url):
    """What the shop whose card url is `url` sold in `settles` (market settles carry the seller's card url as shop):
    deals, median amount / list price, median list price, grade shares, the share sold with a warranty and the
    category leaves by amount. None when it sold nothing."""
    rows = [e.get("data") or {} for e in settles if url and (e.get("data") or {}).get("shop") == url]
    if not rows:
        return None
    ratios, lps, amt = [], [], defaultdict(float)
    for d in rows:
        lp = d.get("list_price") if _is_num(d.get("list_price")) else _list_price(cat, d.get("sku"))
        a = d.get("amount") if _is_num(d.get("amount")) else None
        if lp and a is not None:
            ratios.append(a / lp)
        if lp:
            lps.append(lp)
        amt[_leaf(d.get("category") or (cat.get(d.get("sku")) or {}).get("category"))] += a or 0
    grades = Counter(str(d.get("grade") or "?") for d in rows)
    warranty = sum(1 for d in rows if _is_num(d.get("warranty_hours")) and d["warranty_hours"] > 0)
    tot = sum(amt.values())
    return {
        "deals": len(rows),
        "ratio": st.median(ratios) if ratios else None,
        "list_price": st.median(lps) if lps else None,
        "grades": {g: n / len(rows) for g, n in grades.items()},
        "warranty": warranty / len(rows),
        "cats": [(k, v / tot) for k, v in sorted(amt.items(), key=lambda kv: -kv[1])[:RIVALS_CATS]] if tot > 0 else [],
    }


def section_rivals(b, b6, login, hours6, settles=None, cat=None):
    """[상위 가게]: per shop above us (at least the top RIVALS_TOP, at most RIVALS_MAX) and ours, the rate of
    revenue, deals and book over the last snapshot gap (about 6 h), revenue per deal and book gain per revenue in it,
    and with `settles` what each one sold (rival_sales). Other shops by hashed name. Numbers only."""
    shops = [s for s in b.get("shops") or [] if isinstance(s.get("all"), (int, float))]
    me = next((s for s in shops if c.is_mine(s, login)), None)
    if not shops or not hours6:
        return ["[상위 가게] 순위표 비교 불가 (스냅샷 부족)"]
    old = {s.get("name"): s for s in (b6 or {}).get("shops") or []}
    rank = sorted(shops, key=lambda s: -s["all"])
    top = rank[: min(RIVALS_MAX, max(RIVALS_TOP, rank.index(me) if me in rank else 0))]
    rows = top + ([me] if me is not None and me not in top else [])
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
            + (f", book/매출 {d('book') / d('revenue'):.0%}" if d("revenue") > 0 else "")
        )
        sold = rival_sales(settles, cat or {}, (s.get("card") or {}).get("url")) if settles else None
        if sold:
            out.append(
                f"    판 것 {hours6:.0f}시간: 거래 {sold['deals']}, 정가 대비 중앙 "
                + (f"{sold['ratio']:.0%}" if sold["ratio"] is not None else "-")
                + ", 정가 중앙 " + (f"{sold['list_price']:,.0f}" if sold["list_price"] else "-") + ", 등급 "
                + " ".join(f"{g} {x:.0%}" for g, x in sorted(sold["grades"].items()))
                + f", 보증 붙은 거래 {sold['warranty']:.0%}, 상품군 "
                + ", ".join(f"{k} {x:.0%}" for k, x in sold["cats"])
            )
    return out


TURNOVER_H = 24  # [가게 회전]: window
# kernel card parts the auditor may take off a role's state card (uncard) and put back (card)
CARD_OPTIONAL = {"shop": ("가게 회전", "묶인 돈 수익", "실험 중간값", "커널 밖 변경"),
                 "buyer": ("실험 중간값", "커널 밖 변경")}
OUTSIDE_LOOP = "icrl/state/loop.json"  # the state file of attempt 1's experiment loop (the kernel only reads it)
OUTSIDE_MAX = 12  # lines shown; the loop runs a handful of lanes, a malformed file must not fill the card
OUTSIDE_VAL_CHARS = 40  # a policy value is a number or a short word; anything longer is cut


def _loop_parts(path):
    """Attempt 1's own split of a policy path (r3 icrl/space.py split): a category name may hold dots, so under
    categories. the name is everything up to the last dot."""
    if path.startswith("categories."):
        cat, _, key = path[len("categories."):].rpartition(".")
        return ["categories", cat, key]
    return path.split(".")


def _at(d, parts):
    """(value, found) at the keys in order; found False when a key is missing or a level is not a mapping."""
    for k in parts:
        if not isinstance(d, dict) or k not in d:
            return None, False
        d = d[k]
    return d, True


def _epoch(v):
    """A time that can be shown as a date: a number between 2017 and 2100, else None (NaN, inf, milliseconds, 5)."""
    return float(v) if _is_num(v) and 1.5e9 < v < 4.1e9 else None


def _shown(v, limit=OUTSIDE_VAL_CHARS):
    """A value from a file as one short printable piece of a line."""
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = "".join(ch if ch.isprintable() else " " for ch in s)
    return s if len(s) <= limit else s[:limit] + "…"


def outside_switching(root):
    """Policy values an experiment loop outside the kernel is switching: attempt 1's loop holds each value at one of
    two settings for a block of time and writes policy.json itself, so the kernel's edit history and its experiment
    ledger do not show it (live 11-12: three such values, blocks of about 21 and 42 minutes, one in the section of a
    running kernel experiment). Read from the loop's own state file; [] when it is missing, unreadable or idle.
    Entries that cannot be stated are left out (a path that is not a printable dotted name, two equal values), and
    so are the parts of a line that cannot (a time that is not a date, a block without two ends).
    -> [{"path", "a", "b", "id", "since", "blocks", "block_min", "arm_ts"}]: block_min the median length of the
    finished blocks, arm_ts the start of the last block when it has no end yet; None when unknown."""
    try:
        lanes = c.load_json(os.path.join(c.data_root(root), OUTSIDE_LOOP), {}).get("lanes")
    except (ValueError, OSError, AttributeError, RecursionError):
        return []
    same = lambda x, y: json.dumps(x, sort_keys=True) == json.dumps(y, sort_keys=True)
    out = []
    for lane in (lanes or {}).values() if isinstance(lanes, dict) else ():
        a = lane.get("active") if isinstance(lane, dict) else None
        if not isinstance(a, dict) or a.get("status") != "running":
            continue
        bl = [x for x in a.get("blocks") or [] if isinstance(x, dict)] if isinstance(a.get("blocks"), list) else []
        ends = [(_epoch(x.get("t0")), _epoch(x.get("t1"))) for x in bl]
        spans = [t1 - t0 for t0, t1 in ends if t0 and t1 and 0 < t1 - t0 <= 7 * 86400]
        last = bl[-1] if bl else {}
        xid = a.get("id")
        for e in a.get("edits") or [] if isinstance(a.get("edits"), list) else []:
            path = e.get("path") if isinstance(e, dict) else None
            if not (isinstance(path, str) and path.isprintable() and len(path) <= 200 and all(_loop_parts(path))):
                continue
            if "current" not in e or "value" not in e or same(e["current"], e["value"]):
                continue
            out.append({"path": path, "a": e["current"], "b": e["value"],
                        "id": xid if isinstance(xid, str) and xid.isprintable() and 0 < len(xid) <= 24 else None,
                        "since": _epoch(a.get("t_start")), "blocks": len(bl),
                        "block_min": st.median(spans) / 60 if spans else None,
                        "arm_ts": _epoch(last.get("t0")) if "t1" not in last else None})
    return sorted(out, key=lambda it: it["path"])


def section_outside(root, role=None, part=None):
    """[커널 밖 변경]: the values of outside_switching with the value policy.json holds now and when the loop's state
    file was last written (the lines are that file's content, not a reading of the present). role: only that side's
    values (buyer: paths under buyer.); part: only paths holding that text. [] when there is none. Facts only."""
    every = outside_switching(root)
    items = [it for it in every
             if (role is None or (role == "buyer") == it["path"].startswith("buyer."))
             and (not part or part in it["path"])]
    if not items:
        return []
    base = c.data_root(root)
    try:
        pol = c.load_json(os.path.join(base, "policy.json"), None)
    except (ValueError, OSError, RecursionError):
        pol = None
    try:
        written = _epoch(os.path.getmtime(os.path.join(base, OUTSIDE_LOOP)))
    except OSError:
        written = None
    side = {"buyer": "구매 쪽 ", "shop": "가게 쪽 "}.get(role) or ("이 구역 " if part else "")
    count = f"{len(every)}개 가운데 {side}{len(items)}개" if len(items) != len(every) else f"{len(items)}개"
    out = [f"[커널 밖 변경] 시도 1의 실험 루프가 구간마다 두 값 중 하나로 두는 정책 값 {count} "
           "(그 루프가 policy.json에 직접 써서 정책 수정 기록과 실험 장부에는 나오지 않음"
           + (f"; 루프 상태 파일이 마지막으로 쓰인 때 {c.kst(written)}" if written else "") + ")"]
    for it in items[:OUTSIDE_MAX]:
        sec, _, key = it["path"].rpartition(".")
        sec = sec[len("categories."):] if sec.startswith("categories.") else sec
        now_v, found = _at(pol, _loop_parts(it["path"]))
        out.append(
            f"  {sec} {key}: {_shown(it['a'])} 또는 {_shown(it['b'])}"
            + (f", 구간 약 {it['block_min']:.0f}분" if it["block_min"] and it["block_min"] >= 1 else "")
            + (f", {c.kst(it['since'])}부터" if it["since"] else "") + f" 구간 {it['blocks']}개"
            + (f", 마지막 구간은 {c.kst(it['arm_ts'], '%H:%M')}부터" if it["arm_ts"] else "")
            + (", policy.json을 읽지 못함" if not isinstance(pol, dict)
               else f", policy.json의 지금 값 {_shown(now_v)}" if found else ", policy.json에 이 키 없음")
            + (f" (루프의 실험 {it['id']})" if it["id"] else "")
        )
    if len(items) > OUTSIDE_MAX:
        out.append(f"  외 {len(items) - OUTSIDE_MAX}개")
    return out


def section_turnover(b, old, hours, login):
    """[가게 회전]: per shop above us by shop all (at most RIVALS_MAX; the next three below when we are first) and
    ours, over the snapshot gap `hours` (about TURNOVER_H): the gain of book, revenue and deals, the stock cost now
    and at the start, and the revenue gain divided by the stock cost now. Other shops by hashed name. Numbers only
    (live 11-12: the card held ranks and gaps, never how fast the shops above turn their stock)."""
    fin = lambda v: _is_num(v) and abs(v) < 1e300  # NaN, inf or an absurd size on the board is a missing value
    now_rows = [s for s in (b or {}).get("shops") or [] if isinstance(s, dict)]
    shops = [s for s in now_rows if fin(s.get("all"))]
    me = next((s for s in shops if c.is_mine(s, login)), None)
    if not fin(hours) or hours < 1:
        return ["[가게 회전] 순위표 비교 불가 (스냅샷 부족)"]
    if me is None:
        return ["[가게 회전] 순위표 비교 불가 (순위표에 우리 가게 행이 없거나 all 값이 없음)"]
    olds = [s for s in (old or {}).get("shops") or [] if isinstance(s, dict)]

    def key(s):
        """What pairs a shop's row with its row a day ago: its name together with its owner field, so a name that was
        swapped or taken over pairs with nothing (live 11-12: names are unique, an owner can hold several rows).
        None for a row without a name."""
        name = s.get("name")
        if not isinstance(name, (str, int)) or isinstance(name, bool) or name == "":
            return None
        return type(name).__name__, name, json.dumps(s.get("owner"), sort_keys=True, ensure_ascii=False, default=str)

    # a key two rows share (in either snapshot) finds no old row; ours is found by is_mine
    n_now, n_old = (Counter(key(s) for s in rows) for rows in (now_rows, olds))
    old_me = next((s for s in olds if c.is_mine(s, login)), None)
    prev = {key(s): s for s in olds if key(s) and n_old[key(s)] == 1 and s is not old_me}
    rank = sorted(shops, key=lambda s: -s["all"])
    at = rank.index(me)
    rows = rank[: min(RIVALS_MAX, at)] + [me] if at else rank[:4]
    show = lambda v: c.fmt(v) if fin(v) else "-"  # "-" for a column the board did not give
    signed = lambda v: ("+" if fin(v) and v > 0 else "") + show(v)
    what = ("우리가 1위라 우리와 바로 아래 가게" if not at
            else f"우리 위 {at}곳 중 위에서 {RIVALS_MAX}곳과 우리" if at > RIVALS_MAX else "우리 위 가게와 우리")
    out = [f"[가게 회전 {hours:.0f}시간] 가게 all 순서로 {what} (우리 {at + 1}/{len(rank)}위). book, 매출, 거래는 "
           "이 구간의 증가, 재고 원가는 지금 값(괄호는 구간 시작 때), 끝은 매출 증가를 지금 재고 원가로 나눈 배수"]
    for s in rows:
        who = f"  {rank.index(s) + 1}위 " + ("우리" if s is me else c.hid(s.get("name")))
        o = old_me if s is me else prev.get(key(s)) if n_now[key(s)] == 1 else None
        if not o:
            out.append(f"{who}: {hours:.0f}시간 전 기록 없음, 재고 원가 {show(s.get('stock_cost'))}")
            continue
        d = lambda k: s[k] - o[k] if fin(s.get(k)) and fin(o.get(k)) and fin(s[k] - o[k]) else None
        cost, rev, deals = s.get("stock_cost"), d("revenue"), d("deals")
        ratio = rev / cost if rev is not None and fin(cost) and cost > 0 else None
        out.append(
            f"{who}: book {signed(d('book'))}, 매출 {signed(rev)}, 거래 {f'{deals:+,.0f}' if deals is not None else '-'}, "
            f"재고 원가 {show(cost)} ({show(o.get('stock_cost'))})"
            + (f", 매출/재고 원가 {ratio:.1f}배" if fin(ratio) else "")
        )
    return out


# [가게 목표]: the goal is 1st overall (11-12: the 11-06 shop target 0.90 was dropped, it read as enough while the 1st
# shop stood at 0.977). The formula below reproduces the board's shop all for all 58 shops within 0.0005 (11-06 16:50
# snapshot): stars_rank, not stars, and a mid-rank percentile over every shop.
SHOP_FORMULA = (("book", 0.5), ("revenue", 0.3), ("stars_rank", 0.2))
GOAL_STEPS = 5  # ranks above us listed per column
GOAL_STEPS_BELOW = 3  # ranks below us listed per column


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
    """[가게 목표]: our shop all against the 1st shop's, the formula check against the board, and per column our rank,
    percentile, rate over the snapshot gap and, for the next GOAL_STEPS ranks above, the gap, that shop's rate and
    the shop all we would have there with the other columns unchanged. Numbers only."""
    shops = [s for s in b.get("shops") or [] if isinstance(s.get("all"), (int, float))]
    me = next((s for s in shops if c.is_mine(s, login)), None)
    if me is None or len(shops) < 2:
        return ["[가게 목표] 우리 가게 행 없음"]
    old = {s.get("name"): s for s in (b6 or {}).get("shops") or []}
    err = max(abs(shop_all(shops, s) - s["all"]) for s in shops)
    rank = 1 + sum(1 for s in shops if s["all"] > me["all"])
    top = max(s["all"] for s in shops)
    out = [f"[가게 목표] 우리 가게 all {me['all']:.3f} ({len(shops)}곳 중 {rank}위), 가게 1위 all {top:.3f}, 1위까지 "
           f"{top - me['all']:+.3f}",
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
        mine = rate(me, k)
        steps = []
        for tv in sorted({x for x in vals if x > v})[:GOAL_STEPS]:  # one entry per value: the board rounds stars
            at = [s for s in shops if (s.get(k) or 0) == tv]
            gain = w * (_pct(vals, tv) - _pct(vals, v))
            theirs = rate(at[0], k) if len(at) == 1 else None
            who = f"그 가게 {fmt_rate(theirs, k)}" if len(at) == 1 else f"{len(at)}곳"
            # live 11-06 23h: book 9위 was 1.3M above us but grew 2.3M/h against our 0.7M/h; the gap alone read as close
            if theirs is None or mine is None:
                eta = ""
            elif mine > theirs:
                eta = f", 지금 속도면 {(tv - v) / (mine - theirs):.1f}시간 뒤 따라잡음"
            else:
                eta = ", 지금 속도로는 못 따라잡음"
            steps.append(f"{1 + sum(1 for x in vals if x > tv)}위 +{c.fmt(tv - v)} ({who}, all {gain:+.3f}{eta})")
        if steps:
            out.append("    위 가게: " + "; ".join(steps))
        # live 11-06 22h: revenue 9위 sat 348M under 8위 and 212M over 10위 (no rank to win or lose by volume) while a
        # shop 13.5M under our book grew 5.7M/h against our 0.3M/h; the brief only listed ranks above
        below = []
        for tv in sorted({x for x in vals if x < v}, reverse=True)[:GOAL_STEPS_BELOW]:
            at = [s for s in shops if (s.get(k) or 0) == tv]
            theirs = rate(at[0], k) if len(at) == 1 else None
            if len(at) > 1:
                eta = f"{len(at)}곳"
            elif theirs is None or mine is None:
                eta = f"그 가게 {fmt_rate(theirs, k)}"
            elif theirs > mine:
                eta = f"그 가게 {fmt_rate(theirs, k)}, 지금 속도면 {(v - tv) / (theirs - mine):.1f}시간 뒤 우리를 넘음"
            else:
                eta = f"그 가게 {fmt_rate(theirs, k)}, 지금 속도로는 못 넘음"
            below.append(f"{1 + sum(1 for x in vals if x > tv)}위 -{c.fmt(v - tv)} ({eta})")
        if below:
            out.append(f"    아래 가게 (한 곳이 우리를 넘으면 all 약 -{w / (len(vals) - 1):.3f}): " + "; ".join(below))
    return out


PROFIT_H = 6  # [이익]: window
PROFIT_TOP = 5  # categories by our revenue


def section_profit(root, now, login):
    """[이익]: our sales in the last PROFIT_H hours with the unit cost of each sold unit (inventory unit_gone, joined by
    unit), gross profit and markup per category and, per grade, gross profit and the stars of the reviews of those
    sales (joined by order), next to what the whole market sold in that category and its median
    price/list. book = cash + unsold stock at cost (RULES), so book grows by this gross minus fees and expired stock."""
    since = now - PROFIT_H * 3600
    cat = _catalog(root)
    cost = {}
    for e in c.tail_json(os.path.join(root, ".r3/events.jsonl"), 40_000_000, b'"unit_gone"'):
        d = e.get("data") or {}
        if e.get("type") == "unit_gone" and (e.get("ts") or 0) >= since - 3600 and d.get("unit"):
            cost[d["unit"]] = (float(d.get("cost") or 0), d.get("why"), _leaf(d.get("category")))
    ours, mkt = defaultdict(lambda: [0, 0.0, 0.0, 0, []]), defaultdict(lambda: [0.0, []])
    by_grade = defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0.0, []]))  # leaf -> grade -> [n, rev, cost, stars]
    stars = {}
    for e in c.tail_json(os.path.join(root, ".r3/events.jsonl"), 40_000_000, b'"review"'):
        d = e.get("data") or {}
        if (e.get("type") == "review" and e.get("role") == "shop" and (e.get("ts") or 0) >= since and d.get("order")
                and isinstance(d.get("stars"), (int, float)) and not isinstance(d.get("stars"), bool)):
            stars[d["order"]] = d["stars"]
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
            g = by_grade[leaf][d.get("grade") or "-"]
            g[0] += 1
            g[1] += a
            g[2] += u[0]
            if d.get("order") in stars:
                g[3].append(stars[d["order"]])
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
        gs = by_grade.get(leaf) or {}
        if gs:
            out.append("    등급별: " + "; ".join(
                f"{g} {v[0]}건, 총이익 {c.fmt(v[1] - v[2])} ({(v[1] - v[2]) / v[1] if v[1] else 0:.0%}), "
                + (f"별점 {st.mean(v[3]):.2f} (리뷰 {len(v[3])})" if v[3] else "리뷰 없음")
                for g, v in sorted(gs.items())))
    return out


NET_PROFIT_H = 12  # [순이익]: longer than PROFIT_H so a leaf x grade row holds enough breaks to be stable
NET_BYTES_PER_H = 4_000_000  # events tail read per hour of window ([시장 몫 24시간] reads 80 MB for 24 h of settles)
NET_WARRANTY_SLACK_S = 60  # a break this long after the warranty end still got a refund (live check, 11-10)
NET_MEDIAN_MIN = 5  # breaks needed before the sale-to-break median is printed


def net_profit_data(root, now, hours, login, cat=None):
    """Our sales settled in the last `hours` with unit cost (unit_gone, else restock, joined by unit) and the breaks
    of those sales: a mine market broken row joins our latest not yet broken settle of the same (buyer, sku) at or
    before broke_at; it is refunded when that sale had warranty_hours and broke within them (+60 s). Refunds count
    in the window of the sale. -> (sales, unmatched breaks, sale-to-break seconds)."""
    since = now - hours * 3600
    cat = _catalog(root) if cat is None else cat
    path, nb = os.path.join(root, ".r3/events.jsonl"), int(NET_BYTES_PER_H * (hours + 2))
    cost = {}
    for e in c.tail_json(path, nb, b'"restock"'):
        d = e.get("data") or {}
        if e.get("type") == "restock" and e.get("mine") and d.get("unit") and isinstance(d.get("cost"), (int, float)):
            cost[d["unit"]] = (float(d["cost"]), _leaf(d.get("category")))
    for e in c.tail_json(path, nb, b'"unit_gone"'):
        d = e.get("data") or {}
        if e.get("type") == "unit_gone" and d.get("unit") and isinstance(d.get("cost"), (int, float)):
            cost[d["unit"]] = (float(d["cost"]), _leaf(d.get("category")))
    sales, by_key = [], defaultdict(list)
    for e in c.tail_json(path, nb, b'"settle"'):
        d = e.get("data") or {}
        if (e.get("type") != "settle" or e.get("src") != "market" or (e.get("ts") or 0) < since
                or c.settle_side(d, login) != "shop"):
            continue
        u = cost.get(d.get("unit"))
        leaf = _leaf((cat.get(d.get("sku")) or {}).get("category"))
        s = {"ts": e["ts"], "amount": float(d.get("amount") or 0), "cost": u[0] if u else None,
             "leaf": u[1] if u and leaf == "?" else leaf, "sku": d.get("sku"), "grade": d.get("grade") or "-",
             "wh": d.get("warranty_hours") or 0, "broke": False, "refund": 0.0}
        sales.append(s)
        by_key[(json.dumps(d.get("buyer")), d.get("sku"))].append(s)
    breaks = []
    for e in c.tail_json(path, nb, b'"broken"'):
        d = e.get("data") or {}
        if e.get("type") == "broken" and e.get("src") == "market" and e.get("mine"):
            at = d.get("broke_at") if isinstance(d.get("broke_at"), (int, float)) else e.get("ts")
            if isinstance(at, (int, float)) and at >= since:
                breaks.append((at, json.dumps(d.get("buyer")), d.get("sku")))
    unmatched, delays = 0, []
    for at, buyer, sku in sorted(breaks):
        s = max((x for x in by_key.get((buyer, sku), ()) if x["ts"] <= at and not x["broke"]),
                key=lambda x: x["ts"], default=None)
        if s is None:
            unmatched += 1
            continue
        s["broke"] = True
        delays.append(at - s["ts"])
        if s["wh"] and at - s["ts"] <= s["wh"] * 3600 + NET_WARRANTY_SLACK_S:
            s["refund"] = s["amount"]
    return sales, unmatched, delays


def _net_sum(sales):
    """[n, revenue, gross over sales with a cost, sales with a cost, broken, refunds n, refund amount]"""
    x = [0, 0.0, 0.0, 0, 0, 0, 0.0]
    for s in sales:
        x[0] += 1
        x[1] += s["amount"]
        if s["cost"] is not None:
            x[2] += s["amount"] - s["cost"]
            x[3] += 1
        x[4] += s["broke"]
        x[5] += s["refund"] > 0
        x[6] += s["refund"]
    return x


def _net_entry(label, x):
    return (f"{label} {x[0]}건, 총 {c.fmt(x[2])}, 고장 {x[4]}, 환불 {x[5]}건 {c.fmt(x[6])}, 순 {c.fmt(x[2] - x[6])}, "
            f"고장률 {x[4] / x[0]:.0%}")


def net_profit_lines(sales, unmatched, delays, hours, leaves=None, top=PROFIT_TOP):
    """[순이익] text: one total line, the recent-sales note and per category leaf (top by our revenue) one entry per
    grade, split by warranty when both kinds sold. leaves limits the leaf lines to those names."""
    if not sales:
        return [f"[순이익 {hours:g}시간, 판매 시각 기준] 우리 판매 기록 없음"]
    t = _net_sum(sales)
    out = [f"[순이익 {hours:g}시간, 판매 시각 기준] 우리 판매 {t[0]}건 (원가 이어진 {t[3]}건), 총이익 {c.fmt(t[2])}, "
           f"보증 환불 {t[5]}건 {c.fmt(t[6])}, 순이익 {c.fmt(t[2] - t[6])}; 고장 {t[4]}건 ({t[4] / t[0]:.1%}), "
           f"판매와 못 이은 고장 {unmatched}건",
           "  최근 몇 시간의 판매는 아직 고장날 수 있어 최근 순이익은 상한"
           + (f"; 판매에서 고장까지 중앙 {st.median(delays) / 3600:.1f}시간 (고장 {len(delays)}건)"
              if len(delays) >= NET_MEDIAN_MIN else "")]
    by_leaf = defaultdict(list)
    for s in sales:
        by_leaf[s["leaf"]].append(s)
    rows = sorted(by_leaf.items(), key=lambda kv: -sum(s["amount"] for s in kv[1]))
    for leaf, ss in [kv for kv in rows if leaves is None or kv[0] in leaves][:top]:
        parts = []
        for g in sorted({s["grade"] for s in ss}):
            gs = [s for s in ss if s["grade"] == g]
            split = [(" 보증", [s for s in gs if s["wh"]]), (" 무보증", [s for s in gs if not s["wh"]])]
            parts += [_net_entry(g + tag, _net_sum(sub)) for tag, sub in split if sub]
        out.append(f"  {leaf}: " + "; ".join(parts))
    return out


def section_net_profit(root, now, login):
    """[순이익]: net_profit_lines over the last NET_PROFIT_H hours. Numbers only."""
    return net_profit_lines(*net_profit_data(root, now, NET_PROFIT_H, login), NET_PROFIT_H)


BUYER_STEPS = 3  # [구매자 목표]: values above us per column; fewer than GOAL_STEPS to keep the section near 1.3k chars
BUYER_STEPS_BELOW = 2


def buyer_all(rows, r):
    """Buyer all from ALL_WEIGHTS: percentile columns among rows, RAW_KEYS columns as their own value."""
    raw = RAW_KEYS["구매자"]
    return sum(w * ((r.get(k) or 0) if k in raw else _pct([t.get(k) or 0 for t in rows], r.get(k) or 0))
               for k, w in ALL_WEIGHTS["구매자"].items())


def section_buyer_goal(b, b6, login, hours6):
    """[구매자 목표]: our buyer all, the formula check against the board, and per column our rank, percentile (forum:
    its value), rate over the snapshot gap and, for the next BUYER_STEPS values above, the gap, that buyer's rate and
    the all gain; BUYER_STEPS_BELOW values below. Percentiles are over every board row, alive or not (live 11-10: 64
    rows, 49 alive; all rows reproduce the board's all within 0.0005, alive rows only miss by up to 0.079). No goal
    number is set."""
    rows = [r for r in b.get("buyers") or [] if isinstance(r.get("all"), (int, float))]
    me = next((r for r in rows if c.is_mine(r, login)), None)
    if me is None or len(rows) < 2:
        return ["[구매자 목표] 우리 구매자 행 없음"]
    old = {r.get("name"): r for r in (b6 or {}).get("buyers") or []}
    raw, W = RAW_KEYS["구매자"], ALL_WEIGHTS["구매자"]
    err = max(abs(buyer_all(rows, r) - r["all"]) for r in rows)
    rank = 1 + sum(1 for r in rows if r["all"] > me["all"])
    out = [f"[구매자 목표] 우리 구매자 all {me['all']:.3f} ({len(rows)}곳 중 {rank}위)",
           "  공식: " + " + ".join(f"{w} {k}" + ("(값 그대로)" if k in raw else "") for k, w in W.items())
           + f", 나머지는 순위표 {len(rows)}곳 백분위 (동점은 중간 순위); 순위표 all과 최대 오차 {err:.4f}"]

    def rate(r, k):
        o = (old.get(r.get("name")) or {}).get(k)
        if not hours6 or r.get("name") not in old or (k in RATE_KEYS and isinstance(o, (int, float))
                                                        and abs(o) > RATE_KEYS[k]):
            return None  # the old snapshot still holds the pre-Patch 3.4 meaning of a rate column
        return ((r.get(k) or 0) - (o or 0)) / hours6

    fmt_rate = lambda r: "-" if r is None else f"{c.fmt(r)}/h"
    for k, w in W.items():
        vals = [r.get(k) or 0 for r in rows]
        v = me.get(k) or 0
        val = (lambda x: w * x) if k in raw else (lambda x: w * _pct(vals, x))
        mine = rate(me, k)
        out.append(f"  구매자 {k}: {c.fmt(v)}, {1 + sum(1 for x in vals if x > v)}위, "
                   + ("값 그대로" if k in raw else f"백분위 {_pct(vals, v):.3f}")
                   + f", 1위면 all {val(max(vals)) - val(v):+.3f}, 우리 {fmt_rate(mine)}")
        steps = []
        for tv in sorted({x for x in vals if x > v})[:BUYER_STEPS]:
            at = [r for r in rows if (r.get(k) or 0) == tv]
            theirs = rate(at[0], k) if len(at) == 1 else None
            who = f"그 구매자 {fmt_rate(theirs)}" if len(at) == 1 else f"{len(at)}곳"
            if theirs is None or mine is None:
                eta = ""
            elif mine > theirs:
                eta = f", 지금 속도면 {(tv - v) / (mine - theirs):.1f}시간 뒤 따라잡음"
            else:
                eta = ", 지금 속도로는 못 따라잡음"
            steps.append(f"{1 + sum(1 for x in vals if x > tv)}위 +{c.fmt(tv - v)} ({who}, all {val(tv) - val(v):+.3f}"
                         f"{eta})")
        if steps:
            out.append("    위 구매자: " + "; ".join(steps))
        below = []
        for tv in sorted({x for x in vals if x < v}, reverse=True)[:BUYER_STEPS_BELOW]:
            at = [r for r in rows if (r.get(k) or 0) == tv]
            theirs = rate(at[0], k) if len(at) == 1 else None
            if len(at) > 1:
                eta = f"{len(at)}곳"
            elif theirs is None or mine is None:
                eta = f"그 구매자 {fmt_rate(theirs)}"
            elif theirs > mine:
                eta = f"그 구매자 {fmt_rate(theirs)}, 지금 속도면 {(v - tv) / (theirs - mine):.1f}시간 뒤 우리를 넘음"
            else:
                eta = f"그 구매자 {fmt_rate(theirs)}, 지금 속도로는 못 넘음"
            below.append(f"{1 + sum(1 for x in vals if x > tv)}위 -{c.fmt(v - tv)} ({eta})")
        if below:
            lose = ("값 그대로라 순위가 바뀌어도 all 변화 없음" if k in raw
                    else f"한 곳이 우리를 넘으면 all 약 -{w / (len(vals) - 1):.4f}")
            out.append(f"    아래 구매자 ({lose}): " + "; ".join(below))
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
    """[협상 결과]: per category with the most offers in the last NEGOTIATION_H hours, how our offers ended (accepted,
    and of those how many our shop settled within ACCEPT_SETTLE_S, our reject, buyer left, idle, no record) and, for the ends without a sale, the rounds and our asks against list;
    then per top SKU our first ask, our sale price and the market's settle price (all shops) against list, and our
    share of that SKU's settles. Rows before a2_deal_end existed count as no record."""
    since = now - NEGOTIATION_H * 3600
    cat = _catalog(root)
    per = defaultdict(lambda: {"open": 0, "accept": 0, "reject": 0, "cancel": 0, "idle": 0, "r0": 0, "ask0": [], "last": []})
    sku = defaultdict(lambda: {"open": 0, "ask0": [], "sold": []})
    accepts = []  # (ts, sku, leaf): an accept is not a sale until our shop settles that SKU
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
            accepts.append((m.get("ts") or 0, o["sku"], leaf))
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
    our_settles = defaultdict(list)  # sku -> [ts] of our shop's settles
    for e in market_settles(root, since):
        dd = e.get("data") or {}
        if c.settle_side(dd, login) == "shop" and dd.get("sku"):
            our_settles[dd["sku"]].append(e.get("ts") or 0)
        lp = _list_price(cat, dd.get("sku"))
        if dd.get("sku") in sku and lp and isinstance(dd.get("amount"), (int, float)):
            x = mk[dd["sku"]]
            x["all"].append(dd["amount"] / lp)
            x["n"] += 1
            x["ours"] += 1 if c.settle_side(dd, login) == "shop" else 0
    # each accept takes the first unused settle of ours for the same SKU within ACCEPT_SETTLE_S after it
    for v in our_settles.values():
        v.sort()
    used, settled, hourly = defaultdict(int), Counter(), defaultdict(lambda: [0, 0])
    for ts, k, leaf in sorted(accepts):
        v = our_settles.get(k) or []
        hb = hourly[time.strftime("%H", time.localtime(ts))]
        hb[0] += 1
        while used[k] < len(v) and v[used[k]] < ts - 5:
            used[k] += 1
        if used[k] < len(v) and v[used[k]] <= ts + ACCEPT_SETTLE_S:
            used[k] += 1
            settled[leaf] += 1
            hb[1] += 1
    med = lambda v: f"{st.median(v):.2f}" if v else "-"
    out = [f"[협상 결과 {NEGOTIATION_H}시간] 상품군별 우리 제시의 끝 (수락, 우리 거절, 구매자가 닫음, 응답 없음, 기록 없음). "
           f"수락은 우리가 값을 받아들인 것이고 판매가 아님. 판매는 수락 뒤 {ACCEPT_SETTLE_S // 60}분 안에 우리 가게에서 "
           "같은 SKU가 정산된 수(정산). 기록 없음은 끝 기록(a2_deal_end)이 생기기 전 것이거나 시도 1이 맡은 것"]
    if hourly:
        # the 6 h totals below mix hours; a buyer group that takes our accept and does not pay shows as a dip here
        # (11-06 10~17시 45~60%, before and after 85~95%)
        out.append("  시간별 수락 중 정산 (전 상품군): " + ", ".join(
            f"{h}시 {x[1]}/{x[0]} ({x[1] / x[0]:.0%})" for h, x in sorted(hourly.items(), key=lambda kv: kv[0])))
    for leaf in top:
        a = per[leaf]
        ended = a["cancel"] + a["idle"]
        rest = a["open"] - a["accept"] - a["reject"] - ended
        rate = f" ({settled[leaf] / a['accept']:.0%})" if a["accept"] else ""
        line = (f"  {leaf}: 제시 {a['open']}, 수락 {a['accept']} 중 정산 {settled[leaf]}{rate}, 우리 거절 {a['reject']}, "
                f"구매자가 닫음 {a['cancel']}, 응답 없음 {a['idle']}, 기록 없음 {max(0, rest)}")
        if ended:
            line += (f" | 수락 없이 끝난 {ended}건 중 첫 제시 뒤 바로 끝남 {a['r0']}, 우리 첫 제시가/정가 중앙 {med(a['ask0'])}, "
                     f"구매자 마지막 제안/정가 중앙 {med(a['last'])}")
        out.append(line)
        skus = sorted((k for k in sku if _leaf((cat.get(k) or {}).get("category")) == leaf), key=lambda k: -sku[k]["open"])
        for k in skus[:NEGOTIATION_SKUS]:
            x, y = sku[k], mk.get(k, {"all": [], "ours": 0, "n": 0})
            out.append(
                f"    {k}: 제시 {x['open']}, 우리 첫 제시가/정가 {med(x['ask0'])}, 우리 수락가/정가 {med(x['sold'])} (n {len(x['sold'])}), "
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


# [목표 진척] (11-12: our total went 1.676 -> 1.658 over a day, 7th against the 1st's 1.773, and the card said nothing:
# the goal sections were only read when opened). A linear extrapolation of the board columns, not a forecast.
ROUND_END_TS = c.ROUND_END  # 2026-11-17 23:59 KST
GOAL_COLS = {"shops": tuple(k for k, _ in SHOP_FORMULA), "buyers": tuple(ALL_WEIGHTS["구매자"])}
GOAL_CLAMP = {"book": (0, None), "revenue": (0, None), "stars_rank": (0, 5), "saved": (None, 1), "forum": (0, 1),
              "deals": (0, None), "posts": (0, None)}


def _pid(p):
    return json.dumps(p.get("player") or p.get("owner"), sort_keys=True, ensure_ascii=False, default=str)


def _col_rate(row, old, k, hours):
    """Per-hour change of column k from old to row; None without an old value (or an old pre-Patch 3.4 rate value)."""
    o, v = (old or {}).get(k), row.get(k)
    if not hours or not _is_num(o) or not _is_num(v) or (k in RATE_KEYS and abs(o) > RATE_KEYS[k]):
        return None
    return (v - o) / hours


def goal_progress(cur, s6, s24, login, now):
    """Pure: our total against the 1st now, the change of both, and both at round end at the last 6 h rates.

    cur, s6, s24: (ts, board) of the newest snapshot and of those nearest now - 6 h and now - 24 h (None when missing
    or not older than cur). Every shop and buyer row's GOAL_COLS are extended linearly from cur to ROUND_END_TS at
    the row's own rate over s6 -> cur (a row without a row of the same name in s6 holds its values), clamped by
    GOAL_CLAMP; shop all and buyer all are recomputed with shop_all and buyer_all over all rows, and a player's total
    is the sum for the rows named in its shop and buyer objects (a side without such a row keeps its board all).
    cols: per weighted column of our rows, our value and rate, and the rate that reaches the column's projected 1st
    value by round end. -> dict, or None without our player row."""
    if not cur:
        return None
    bts, b = cur
    P = [p for p in b.get("players") or [] if isinstance(p, dict) and _is_num(p.get("total"))]
    me = next((p for p in P if c.is_mine(p, login)), None)
    if me is None:
        return None
    E = sorted(P, key=lambda p: -p["total"])
    top = E[0]

    def change(snap, p):
        if not snap or snap[0] >= bts:
            return None
        o = next((q for q in snap[1].get("players") or [] if isinstance(q, dict) and _is_num(q.get("total"))
                  and _pid(q) == _pid(p)), None)
        return None if o is None else {"d": p["total"] - o["total"], "h": (bts - snap[0]) / 3600}

    h6 = (bts - s6[0]) / 3600 if s6 and s6[0] < bts else 0.0
    span = max(0.0, (ROUND_END_TS - bts) / 3600)
    side_all, cols = {}, []
    for key, f in (("shops", shop_all), ("buyers", buyer_all)):
        rows = [r for r in b.get(key) or [] if isinstance(r, dict) and _is_num(r.get("all"))]
        old = {r.get("name"): r for r in ((s6[1].get(key) or []) if h6 else []) if isinstance(r, dict)}
        rates = [{k: _col_rate(r, old.get(r.get("name")), k, h6) for k in GOAL_COLS[key]} for r in rows]
        proj = []
        for r, rt in zip(rows, rates):
            q = dict(r)
            for k in GOAL_COLS[key]:
                if rt[k] is not None:
                    lo, hi = GOAL_CLAMP[k]
                    v = (r.get(k) or 0) + rt[k] * span
                    q[k] = min(hi if hi is not None else v, max(lo if lo is not None else v, v))
            proj.append(q)
        side_all[key] = {q.get("name"): f(proj, q) for q in proj}
        i = next((i for i, r in enumerate(rows) if c.is_mine(r, login)), None)
        if i is None or not h6:
            continue
        for k in GOAL_COLS[key]:
            best = max(q.get(k) or 0 for q in proj)
            v = rows[i].get(k) or 0
            cols.append({"side": key, "key": k, "value": v, "rate": rates[i][k], "proj": proj[i].get(k) or 0,
                         "top": best, "need": (best - v) / span if span else None,
                         "lead": (proj[i].get(k) or 0) >= best})

    def proj_total(p):
        t = 0.0
        for key, side in (("shops", "shop"), ("buyers", "buyer")):
            o = p.get(side) if isinstance(p.get(side), dict) else {}
            if o.get("name") in side_all[key]:
                t += side_all[key][o["name"]]
            elif _is_num(o.get("all")):
                t += o["all"]
        return t

    pt = [proj_total(p) for p in P]
    mine = pt[P.index(me)]
    j = max(range(len(P)), key=lambda n: pt[n])
    return {
        "ts": bts,
        "left_h": (ROUND_END_TS - now) / 3600,
        "n": len(P),
        "total": me["total"],
        "rank": E.index(me) + 1,
        "top_total": top["total"],
        "top_is_me": top is me,
        "change": {"me": {6: change(s6, me), 24: change(s24, me)}, "top": {6: change(s6, top), 24: change(s24, top)}},
        "proj": {"h": h6, "total": mine, "rank": 1 + sum(1 for x in pt if x > mine), "top_total": pt[j],
                 "top_is_me": P[j] is me, "top_rank_now": E.index(P[j]) + 1} if h6 else None,
        "cols": cols,
    }


def section_goal_progress(g, role=None):
    """[목표 진척] text of a goal_progress dict; role "shop" / "buyer" keeps that side's column line. Numbers only."""
    if not g:
        return ["[목표 진척] 자료 없음"]
    sg = lambda v: "-" if v is None else ("+" if v >= 0 else "") + c.fmt(v)
    ch = lambda who: ", ".join(f"{x['h']:.0f}시간 {x['d']:+.3f}" if x else f"{h}시간 자료 없음"
                               for h, x in g["change"][who].items())
    out = [f"[목표 진척] 회차 종료까지 {g['left_h']:.0f}시간 | 우리 {g['n']}명 중 {g['rank']}위 total {g['total']:.3f}, "
           f"1위 {g['top_total']:.3f}{' (우리)' if g['top_is_me'] else ''}, 차이 {g['top_total'] - g['total']:.3f}",
           f"  total 변화: 우리 {ch('me')} | 1위 {ch('top')}"]
    p = g["proj"]
    if not p:
        return out + ["  종료 예상: 6시간 전 순위표 없음"]
    out.append(f"  종료 예상 (각 행의 순위표 값을 최근 {p['h']:.0f}시간 속도로 직선 연장, 백분위 다시 셈): 우리 "
               f"{p['total']:.3f} {p['rank']}위, 예상 1위 "
               + ("우리" if p["top_is_me"] else f"{p['top_total']:.3f} (지금 {p['top_rank_now']}위)"))
    for key, label in (("shops", "가게"), ("buyers", "구매자")):
        if role == {"shops": "buyer", "buyers": "shop"}[key]:
            continue
        cs = [x for x in g["cols"] if x["side"] == key]
        if cs:
            out.append(f"  {label} 항목 예상 1위 값까지 필요/지금 속도: " + "; ".join(
                f"{x['key']} 예상 1위, 지금 {sg(x['rate'])}/h" if x["lead"]
                else f"{x['key']} {sg(x['need'])}/h / {sg(x['rate'])}/h" for x in cs))
    return out


def goal(root=None, now=None):
    """goal_progress for a data root (the auditor's facts); None without snapshots or our row."""
    return _Ctx(root, now, None).goal


class _Ctx:
    """Inputs the sections share, each computed on first use so that building one section skips the others' reads."""

    def __init__(self, root, now, last_brief_ts):
        self.root = c.data_root(root)
        self.now = c.now_ts() if now is None else now
        self.last_brief_ts = last_brief_ts
        self.login = c.owner_login(self.root)

    @functools.cached_property
    def snaps(self):
        return board_snapshots(self.root)

    @functools.cached_property
    def board(self):
        """(bts, b, ts6, b6, hours6): the newest snapshot and the one nearest 6 h before it; None without snapshots."""
        if not self.snaps:
            return None
        bts, bpath = self.snaps[-1]
        b = c.load_json(bpath, {}) or {}
        ts6, p6 = _snapshot_near(self.snaps, bts - 6 * 3600)
        b6 = (c.load_json(p6, {}) or {}) if ts6 < bts else {}
        return bts, b, ts6, b6, (bts - ts6) / 3600

    @functools.cached_property
    def board_day(self):
        """(b, old, hours): the newest snapshot and the one nearest TURNOVER_H before it ({} and 0 without one)."""
        if not self.board:
            return {}, {}, 0.0
        bts, b = self.board[0], self.board[1]
        ts, p = _snapshot_near(self.snaps, bts - TURNOVER_H * 3600)
        return b, ((c.load_json(p, {}) or {}) if ts < bts else {}), (bts - ts) / 3600

    @functools.cached_property
    def goal(self):
        """goal_progress over the newest snapshot and those nearest now - 6 h and now - 24 h; None without snapshots."""
        if not self.board:
            return None
        bts = self.board[0]

        def near(h):
            ts, p = _snapshot_near(self.snaps, self.now - h * 3600)
            return (ts, c.load_json(p, {}) or {}) if ts < bts else None

        return goal_progress((bts, self.board[1]), near(6), near(24), self.login, self.now)

    @property
    def goal_args(self):
        _, b, _, b6, hours6 = self.board
        return b, b6, self.login, hours6

    def side(self, key, keys, label):
        _, b, _, b6, hours6 = self.board
        return section_side(b.get(key) or [], b6.get(key) or [], keys, label, self.login, hours6)


# (name, one-line description, thunk, need): build() order. need "snaps" = only built when board snapshots exist,
# "board" = also reads the snapshots (section() says they are missing), "ask" = as "board" but built only when asked,
# "asked" = built only when asked and needs no snapshot
# for by name (section(), the card), so build() stays what it was.
SECTIONS = [
    ("시각", "지금 시각, 회차 남은 시간, 목표",
     lambda x: section_time(x.now, x.board[0] if x.board else None), None),
    ("순위", "Everyone·Class 순위, 1위와의 차이, 상위 5명",
     lambda x: section_rank(*x.goal_args) if x.board else ["[순위] 순위표 스냅샷 없음"], None),
    ("가게 목표", "가게 all 공식, 항목별 위아래 가게와 속도", lambda x: section_shop_goal(*x.goal_args), "board"),
    ("구매자 목표", "구매자 all 공식, 항목별 위아래 구매자", lambda x: section_buyer_goal(*x.goal_args), "board"),
    ("목표 진척", "1위와 차이·변화, 종료 예상 total·순위, 필요 속도",
     lambda x: section_goal_progress(x.goal), "ask"),
    ("이익", "6시간 상품군·등급별 매출 총이익과 시장", lambda x: section_profit(x.root, x.now, x.login), "snaps"),
    ("순이익", "12시간 보증 환불 뺀 순이익, 상품군·등급별",
     lambda x: section_net_profit(x.root, x.now, x.login), "snaps"),
    ("시장 몫", "24시간 3시간 단위 시장 매출과 우리 몫", lambda x: section_share24(x.root, x.now, x.login), "snaps"),
    ("판별 성과", "판매자·구매자 지금 판과 이전 판 지표", lambda x: section_versions(x.root, x.now), None),
    ("상위 가게", "위 가게들의 시간당 증가와 판 상품군",
     lambda x: section_rivals(*x.goal_args, market_settles(x.root, x.board[2]), _catalog(x.root)), "board"),
    ("가게 회전", "24시간 위 가게와 우리의 book·매출·거래 증가, 재고 원가",
     lambda x: section_turnover(*x.board_day, x.login), "ask"),
    ("커널 밖 변경", "시도 1의 실험 루프가 지금 두 값 사이에서 바꾸는 정책 값",
     lambda x: section_outside(x.root) or ["[커널 밖 변경] 지금 없음"], "asked"),
    ("동시 비교", "판매자 후보 판 동시 비교 상태", lambda x: section_trial(x.root, x.now), None),
    ("지난 반성", "판마다 마지막 반성과 그 뒤 결정 수", lambda x: section_reflections(x.root, x.now), "snaps"),
    ("상품군 흐름", "2시간 단위 상품군별 우리 제시와 판매",
     lambda x: section_categories(x.root, x.now, x.login), None),
    ("협상 결과", "6시간 상품군별 우리 제시가 끝난 방식",
     lambda x: section_negotiation(x.root, x.now, x.login), "snaps"),
    ("별점", "6시간 우리 가게 리뷰와 별점", lambda x: section_stars(x.root, x.now, x.login), "snaps"),
    ("모델 호출", "24시간 모델 호출 결과와 지연", lambda x: section_calls(x.root, x.now), None),
    ("가게", "순위표 가게 항목별 값, 순위, 위 차이", lambda x: x.side("shops", SHOP_KEYS, "가게"), "board"),
    ("구매자", "순위표 구매자 항목별 값, 순위, 위 차이", lambda x: x.side("buyers", BUYER_KEYS, "구매자"), "board"),
    ("가게 상태", "잔고, 파산선, 재고 수, 유통기한", lambda x: section_inventory(x.root, x.now), None),
    ("재입고", "재입고 계획 상태, 식품 재고, 재고 없음 거절", lambda x: section_restock(x.root, x.now), None),
    ("판매 24시간", "24시간 판매 요청 결과 수", lambda x: section_seller(x.root, x.now), None),
    ("시장 24시간", "24시간 시장 정산, 품목군 비중, 가게 수",
     lambda x: section_market(x.root, x.now, x.login), None),
    ("포럼", "우리 포럼 글과 구매자 forum 점수 흐름",
     lambda x: section_forum(x.root, x.login, x.snaps, x.now), None),
    ("기억", "기억 점검 점수와 기억 서버 수", lambda x: section_memory(x.root), None),
    ("하네스 수정", "지난 브리핑 뒤 하네스 수정",
     lambda x: section_harness(x.root, x.last_brief_ts if x.last_brief_ts is not None else x.now - 1800), None),
]


def build(root=None, now=None, last_brief_ts=None):
    x = _Ctx(root, now, last_brief_ts)
    out = []
    for _, _, fn, need in SECTIONS:
        if need is None or (x.snaps and need not in ("ask", "asked")):
            out += fn(x)
    return c.mask("\n".join(out), (x.login,))


def section(root, name, now=None):
    """One registry section's text (last brief time: 30 min ago), or the list of valid names."""
    x = _Ctx(root, now, None)
    for n, _, fn, need in SECTIONS:
        if n == name:
            lines = [f"[{n}] 순위표 스냅샷 없음"] if need in ("board", "ask") and not x.snaps else fn(x)
            return c.mask("\n".join(lines), (x.login,))
    return f"섹션 없음: {name}. 쓸 수 있는 이름: " + ", ".join(n for n, *_ in SECTIONS)


COMPACT_CHARS = 10_000  # build_compact hard cap (DESIGN 14.2: always-on bundle). 11-12: 8,000 -> 10,000, the shop card
# grew from about 4,600 to 6,300 chars before the caller's lines with [가게 회전], the leaf returns and the interim lines
COMPACT_GAP_S = 1200  # [지난 깨어남 이후]: reference this long before now when the caller gives no last brief time
COMPACT_NET_N = 3  # best and worst leaf x grade x warranty rows by net
COMPACT_FULL = ("시각", "목표 진척")  # registry sections build_compact prints whole, so the index leaves them out
BANKRUPT_NEAR = 2.0  # operator settings default bankrupt_near_factor: alert when balance <= bankrupt line x this


def _standing(x):
    """[순위]: Everyone rank, total and gap to 1st; shop all and buyer all with their ranks."""
    _, b, _, _, _ = x.board
    P = b.get("players") or []
    me = next((p for p in P if c.is_mine(p, x.login)), None)
    if not me:
        return ["[순위] 순위표에서 우리 행을 찾지 못함"]
    E = sorted(P, key=lambda p: -(p.get("total") or 0))
    t = me.get("total") or 0
    out = [f"[순위] Everyone {len(E)}명 중 {E.index(me) + 1}위, total {t:.3f}, 1위와 차이 "
           f"{(E[0].get('total') or 0) - t:.3f}"]
    parts = []
    for key, label in (("shops", "가게"), ("buyers", "구매자")):
        rows = [r for r in b.get(key) or [] if _is_num(r.get("all"))]
        m = next((r for r in rows if c.is_mine(r, x.login)), None)
        parts.append(f"{label} all {m['all']:.3f} ({len(rows)}곳 중 {1 + sum(1 for r in rows if r['all'] > m['all'])}위)"
                     if m else f"{label} 행 없음")
    return out + ["  " + ", ".join(parts)]


def _position(rows, old, me, k, w, raw, hours6, label):
    """One [점수 위치] line: our value and rank, the gap and all gain to the next value above, and below us the
    soonest shop to pass us at the snapshot-gap rates (else the nearest value below). Same rates as [가게 목표]."""
    vals = [r.get(k) or 0 for r in rows]
    v = me.get(k) or 0
    val = (lambda t: w * t) if raw else (lambda t: w * _pct(vals, t))

    def rate(r):
        o = (old.get(r.get("name")) or {}).get(k)
        if not hours6 or r.get("name") not in old or (k in RATE_KEYS and _is_num(o) and abs(o) > RATE_KEYS[k]):
            return None
        return ((r.get(k) or 0) - (o or 0)) / hours6

    rk = lambda t: 1 + sum(1 for y in vals if y > t)
    mine = rate(me)
    line = f"  {label} {k}: {c.fmt(v)}, {rk(v)}위" + (f", 우리 {c.fmt(mine)}/h" if mine is not None else "")
    above = [y for y in vals if y > v]
    if above:
        tv = min(above)
        line += f" | 위 {rk(tv)}위 +{c.fmt(tv - v)} (all {val(tv) - val(v):+.3f})"
    else:
        line += " | 1위"
    below, threat = sorted({y for y in vals if y < v}, reverse=True)[:GOAL_STEPS_BELOW], None
    for tv in below:
        at = [r for r in rows if (r.get(k) or 0) == tv]
        theirs = rate(at[0]) if len(at) == 1 else None
        if theirs is not None and mine is not None and theirs > mine:
            eta = (v - tv) / (theirs - mine)
            if threat is None or eta < threat[1]:
                threat = (tv, eta)
    if threat:
        line += f" | 아래 {rk(threat[0])}위 -{c.fmt(v - threat[0])}, 지금 속도면 {threat[1]:.1f}시간 뒤 우리를 넘음"
    elif below:
        line += f" | 바로 아래 {rk(below[0])}위 -{c.fmt(v - below[0])}"
    return line


def _positions(x):
    _, b, _, b6, hours6 = x.board
    out = ["[점수 위치] 항목: 값, 순위 | 바로 위 값까지 차이 (all 변화) | 아래에서 가장 먼저 우리를 넘을 곳"]
    for key, formula, raw, label in (("shops", SHOP_FORMULA, (), "가게"),
                                     ("buyers", tuple(ALL_WEIGHTS["구매자"].items()), RAW_KEYS["구매자"], "구매자")):
        rows = [r for r in b.get(key) or [] if _is_num(r.get("all"))]
        me = next((r for r in rows if c.is_mine(r, x.login)), None)
        if me is None or len(rows) < 2:
            out.append(f"  {label} 행 없음")
            continue
        old = {r.get("name"): r for r in b6.get(key) or []}
        out += [_position(rows, old, me, k, w, k in raw, hours6, label) for k, w in formula]
    return out


def _changes(x):
    """[지난 깨어남 이후]: the newest snapshot against the one nearest the last brief (else COMPACT_GAP_S ago)."""
    snaps = x.snaps
    bts, b = x.board[0], x.board[1]
    ref = x.last_brief_ts if x.last_brief_ts is not None else x.now - COMPACT_GAP_S
    t0, p0 = _snapshot_near(snaps, ref)
    if t0 >= bts:
        return [f"[지난 깨어남 이후] 새 순위표 없음 (마지막 {c.kst(bts, '%H:%M')})"]
    b0 = c.load_json(p0, {}) or {}
    out = []
    P0, P1 = b0.get("players") or [], b.get("players") or []
    m0 = next((p for p in P0 if c.is_mine(p, x.login)), None)
    m1 = next((p for p in P1 if c.is_mine(p, x.login)), None)
    if m0 and m1:
        r0, r1 = _rank(P0, "total", m0), _rank(P1, "total", m1)
        d = (m1.get("total") or 0) - (m0.get("total") or 0)
        if r0 != r1 or d:
            out.append(f"  Everyone {f'{r0}위 → {r1}위' if r0 != r1 else f'{r1}위 그대로'}, total {d:+.3f}")
    for key, keys, label in (("shops", [k for k, _ in SHOP_FORMULA], "가게"),
                             ("buyers", list(ALL_WEIGHTS["구매자"]), "구매자")):
        R0, R1 = b0.get(key) or [], b.get(key) or []
        s0 = next((r for r in R0 if c.is_mine(r, x.login)), None)
        s1 = next((r for r in R1 if c.is_mine(r, x.login)), None)
        if not s0 or not s1:
            continue
        parts = []
        for k in ["all"] + keys:
            v0, v1 = s0.get(k), s1.get(k)
            if not _is_num(v0) or not _is_num(v1) or (k in RATE_KEYS and abs(v0) > RATE_KEYS[k]):
                continue
            r0, r1 = _rank(R0, k, s0), _rank(R1, k, s1)
            if v1 == v0 and r0 == r1:
                continue
            d = f"{v1 - v0:+.3f}" if k == "all" else f"{'+' if v1 >= v0 else ''}{c.fmt(v1 - v0)}"
            parts.append(f"{k} {d}" + (f" ({r0}위 → {r1}위)" if r0 != r1 else ""))
        if parts:
            out.append(f"  {label}: " + ", ".join(parts))
    head = f"[지난 깨어남 이후] 순위표 {c.kst(t0, '%H:%M')} → {c.kst(bts, '%H:%M')}"
    return [head] + out if out else [head + ", 바뀐 값 없음"]


def _net_compact(x):
    """[순이익] header line and the COMPACT_NET_N best and worst (leaf, grade, warranty) rows by net."""
    sales, unmatched, delays = net_profit_data(x.root, x.now, NET_PROFIT_H, x.login)
    out = net_profit_lines(sales, unmatched, delays, NET_PROFIT_H)[:1]
    groups = defaultdict(list)
    for s in sales:
        groups[(s["leaf"], s["grade"], " 보증" if s["wh"] else " 무보증")].append(s)
    rows = sorted(((k, _net_sum(v)) for k, v in groups.items()), key=lambda kv: -(kv[1][2] - kv[1][6]))
    n = COMPACT_NET_N
    if rows:
        out.append("  순 상위: " + "; ".join(_net_entry(f"{l} {g}{t}", v) for (l, g, t), v in rows[:n]))
    if rows[n:]:
        out.append("  순 하위: " + "; ".join(_net_entry(f"{l} {g}{t}", v) for (l, g, t), v in rows[max(n, len(rows) - n):][::-1]))
    return out


def _shop_compact(x):
    """[가게 상태]: balance against the bankrupt line, stock by category, restock plan, last hour's no_stock line."""
    inv = c.load_json(os.path.join(x.root, ".market/inventory.json"))
    state, left = restock_plan_state(x.root, x.now)
    plan = {"none": "없음", "expired": "만료", "valid": f"유효 ({left}분 남음)"}[state]
    if not isinstance(inv, dict):
        return ["[가게 상태] inventory.json 없음", f"  재입고 계획 {plan}"]
    cat = c.load_json(os.path.join(x.root, ".market/catalog.json"), {}) or {}
    units = inv.get("units") or []
    byc = Counter((cat.get(u.get("sku")) or {}).get("category", "?") for u in units if isinstance(u, dict))
    exp24 = sum(1 for u in units if isinstance(u, dict) and _is_num(u.get("expires_at"))
                and u["expires_at"] - x.now < 86400)
    out = [f"[가게 상태] 잔고 {c.fmt(inv.get('balance'))}, 파산선 {c.fmt(inv.get('bankrupt_line'))}, 남은 파산 "
           f"{inv.get('bankruptcies_left', '-')}회 | 재고 {len(units)}개: "
           + ", ".join(f"{k} {n}" for k, n in byc.most_common(3)) + f" | 24시간 안에 유통기한 끝 {exp24}개",
           ]
    if os.path.exists(os.path.join(x.root, "a2", "LIVE")):  # the plan only drives restock while attempt 2 is live
        out.append(f"  재입고 계획 {plan}")
    return out + [ln for ln in restock_food_lines(x.root, x.now, inv) if ln.startswith("  최근 1시간 재고 없음 거절")]


MONEY_TOP = 5  # leaves shown by stock cost in [돈이 있는 곳]
MONEY_RESTOCK_H = 2
HUMAN_NOTE_H = 6  # guide section 10 entries this recent are named on the card (24 h showed 24 entries live)
HUMAN_NOTE_N = 5  # the newest ones
HUMAN_NOTE_CHARS = 200


def _money_compact(x, ret=True):
    """[돈이 있는 곳]: cash, stock cost per leaf with its units, the last NET_PROFIT_H hours' sales and the stock
    divided by that pace, and the last hours' restock runs. Facts only (live 11-12: the card showed stock in units,
    so where the money sat was never on it). ret: each leaf also gets those hours' net profit (gross over sales with
    a known cost minus warranty refunds, the [순이익] rule) and that net scaled to a day over the leaf's stock cost
    now, which reads against the storage rate in the header (live 11-12: 218.8M sat in a leaf at price/cost 1.05)."""
    inv = c.load_json(os.path.join(x.root, ".market/inventory.json"))
    if not isinstance(inv, dict):
        return []
    cat = _catalog(x.root)
    by = defaultdict(lambda: [0, 0.0, []])
    for u in inv.get("units") or []:
        if isinstance(u, dict):
            g = by[_leaf((cat.get(u.get("sku")) or {}).get("category"))]
            g[0] += 1
            g[1] += float(u.get("cost") or 0) if _is_num(u.get("cost")) else 0.0
            if _is_num(u.get("supplied_at")) and u["supplied_at"] <= x.now:
                g[2].append((x.now - u["supplied_at"]) / 3600)
    rate = inv.get("storage_per_day") if _is_num(inv.get("storage_per_day")) and 0 < inv["storage_per_day"] < 1 else None
    keep = lambda cost: f", 보관비 하루 {c.fmt(cost * rate)}" if rate else ""
    age = lambda hs: f", 들어온 지 중앙 {st.median(hs):.0f}시간(가장 오래된 것 {max(hs):.0f}시간)" if hs else ""
    sales = net_profit_data(x.root, x.now, NET_PROFIT_H, x.login, cat)[0]
    sold = Counter(s["leaf"] for s in sales)
    net = defaultdict(lambda: [0.0, 0])  # leaf -> [gross over sales with a cost minus refunds, sales without a cost]
    for s in sales:
        net[s["leaf"]][0] += (s["amount"] - s["cost"] if s["cost"] is not None else 0.0) - s["refund"]
        net[s["leaf"]][1] += s["cost"] is None

    def earned(leaf, cost):
        if not ret or not sold.get(leaf):
            return ""
        g, unknown = net[leaf]
        return (f", 순이익 {c.fmt(g)}"
                + (f" (하루로 환산하면 원가 합의 {g * 24 / NET_PROFIT_H / cost:.1%})" if cost > 0 else "")
                + (f", 원가 모르는 판매 {unknown}개는 총이익 0으로 셈" if unknown else ""))

    rows = sorted(by.items(), key=lambda kv: -kv[1][1])
    out = [f"[돈이 있는 곳] 현금 {c.fmt(inv.get('balance'))}, 재고 원가 합 {c.fmt(sum(v[1] for v in by.values()))} "
           f"({sum(v[0] for v in by.values())}개)"
           + (f", 보관비 하루 {c.fmt(sum(v[1] for v in by.values()) * rate)} (원가의 {rate:.1%})" if rate else "")
           + f". 아래는 원가가 큰 상품군부터, 최근 {NET_PROFIT_H}시간 판매 수와 그 속도로 지금 재고를 나눈 시간"
           + (f", 같은 {NET_PROFIT_H}시간의 순이익(보증 환불 뺌)과 그것을 하루로 환산해 지금 원가 합으로 나눈 값" if ret else "")]
    for leaf, (n, cost, hs) in rows[:MONEY_TOP]:
        k = sold.get(leaf, 0)
        pace = f"판매 {k}개, 그 속도로 {n / k * NET_PROFIT_H:.0f}시간 치" if k else "판매 0개"
        out.append(f"  {leaf}: {n}개 원가 합 {c.fmt(cost)}, {pace}{earned(leaf, cost)}{keep(cost)}{age(hs)}")
    if rows[MONEY_TOP:]:
        rest = rows[MONEY_TOP:]
        out.append(f"  그 밖 {len(rest)}개 상품군: {sum(v[0] for _, v in rest)}개 원가 합 {c.fmt(sum(v[1] for _, v in rest))}, "
                   f"판매 {sum(sold.get(k, 0) for k, _ in rest)}개")
    gone = sum(n for k, n in sold.items() if k not in by)
    if gone:
        out.append(f"  지금 재고가 없는 상품군의 최근 {NET_PROFIT_H}시간 판매 {gone}개")
    from kernel import code_edit  # lazy: code_edit is not needed to import the brief

    log = code_edit.restock_log(x.root, MONEY_RESTOCK_H, x.now).split("\n")[:2]
    if log and log[0].startswith("재입고 실행"):
        out.append("  " + ", ".join(log))
    last = None
    for m in c.tail_json(os.path.join(x.root, ".r3/metrics.jsonl"), 80_000_000, b'"restock_plan"'):
        o = m.get("outcome") if isinstance(m.get("outcome"), dict) else {}
        if m.get("hook") == "restock" and not o.get("why") and _is_num(o.get("qty")) and o["qty"] > 0 and _is_num(m.get("ts")):
            last = (m["ts"], o["qty"])
    if last and last[0] <= x.now:
        out.append(f"  마지막으로 산 재입고 실행: {c.kst(last[0])} ({(x.now - last[0]) / 3600:.1f}시간 전), 수량 {last[1]:g}")
    return out


HOURLY_H = 6  # [시간대별]: clock hours shown, the current (partial) hour last


def _hourly(x, role):
    """[시간대별]: per clock hour, our shop's settled sales (count, revenue) and our buyer's settled buys (count,
    paid/value). Facts only: the 12 h totals on the card hide whether the pace is rising or falling."""
    since = (int(x.now) // 3600 - (HOURLY_H - 1)) * 3600
    hr = defaultdict(lambda: [0, 0.0, 0, 0.0, 0.0])
    for e in c.tail_json(os.path.join(x.root, ".r3/events.jsonl"), NET_BYTES_PER_H * (HOURLY_H + 2), b'"settle"'):
        ts, d = e.get("ts"), e.get("data") or {}
        if e.get("type") != "settle" or not _is_num(ts) or not since <= ts <= x.now:
            continue
        h = hr[int(ts) // 3600]
        if e.get("src") == "market" and c.settle_side(d, x.login) == "shop":
            h[0] += 1
            h[1] += d.get("amount") if _is_num(d.get("amount")) else 0
        elif e.get("src") == "deal" and e.get("role") == "buyer":
            h[2] += 1
            if _is_num(d.get("amount")) and _is_num(d.get("value")):
                h[3] += d["amount"]
                h[4] += d["value"]
    hours = range(since // 3600, int(x.now) // 3600 + 1)
    hh = lambda k: c.kst(k * 3600, "%H시")
    out = [f"[시간대별] 최근 {HOURLY_H}개 시각(마지막은 진행 중인 {int(x.now % 3600 // 60)}분째 시각)"]
    if role in (None, "shop"):
        out.append("  가게 정산 판매 건수(매출): " + ", ".join(f"{hh(k)} {hr[k][0]}({c.fmt(hr[k][1])})" for k in hours))
    if role in (None, "buyer"):
        out.append("  구매 정산 건수(지불액/가치): " + ", ".join(
            f"{hh(k)} {hr[k][2]}" + (f"({hr[k][3] / hr[k][4]:.2f})" if hr[k][4] > 0 else "") for k in hours))
    return out


BUY_CARD_LINES = 8


def _buy_compact(x):
    """[구매 시도]: the head of the buy_attempts tool for the last 6 hours (the buyer operator's card)."""
    from kernel import tools  # lazy: tools imports this module

    lines = tools.buy_attempts(x.root, 6, x.now).split("\n")
    return ["[구매 시도] " + lines[0]] + lines[1:BUY_CARD_LINES] + ["  (전체는 buy_attempts 도구)"]


def _human_notes(x):
    """[사람이 적은 것]: the head lines of guide section 10 entries dated in the last HUMAN_NOTE_H hours (the section
    is appended to and far too long for the prompt, so a new entry was invisible until someone read the section)."""
    import re
    from datetime import datetime

    from kernel import tools  # lazy: tools imports this module

    _, secs = tools._guide_sections()
    hit = tools._guide_find(secs, "10") if secs else None
    if not hit:
        return []
    year, out = datetime.fromtimestamp(x.now, c.KST).year, []
    for ln in hit[1].splitlines():
        m = re.match(r"- (\d\d)-(\d\d) (\d\d):(\d\d) ", ln)
        if not m:
            continue
        try:
            ts = datetime(year, *map(int, m.groups()), tzinfo=c.KST).timestamp()
        except ValueError:
            continue
        if 0 <= x.now - ts <= HUMAN_NOTE_H * 3600:
            out.append("  " + ln[:HUMAN_NOTE_CHARS] + (" …" if len(ln) > HUMAN_NOTE_CHARS else ""))
    if not out:
        return []
    out = out[-HUMAN_NOTE_N:]
    return [f"[사람이 적은 것] 시장 설명서 10절에 최근 {HUMAN_NOTE_H}시간 동안 새로 적힌 항목 {len(out)}개 "
            "(전체와 근거는 guide 도구 section 10)"] + out


def _alerts(x):
    """[알림]: lines that appear only when true."""
    out = []
    sh = c.load_json(os.path.join(x.root, ".market/shops.json"))
    rows = sh if isinstance(sh, list) else list(sh.values()) if isinstance(sh, dict) else []
    # live shops.json rows carry only name, offers, reviews, url (11-10): without an owner field nothing can be said
    owned = any(isinstance(r, dict) and any(k in r for k in ("owner", "player", "github")) for r in rows)
    if owned and not any(isinstance(r, dict) and c.is_mine(r, x.login) for r in rows):
        out.append(f"  우리 가게가 지금 가게 목록에 없음 (목록 {len(rows)}곳)")
    inv = c.load_json(os.path.join(x.root, ".market/inventory.json"), {}) or {}
    bal, line = (inv.get("balance"), inv.get("bankrupt_line")) if isinstance(inv, dict) else (None, None)
    if _is_num(bal) and _is_num(line) and bal <= (line * BANKRUPT_NEAR if line > 0 else line):
        out.append(f"  잔고 {c.fmt(bal)}가 파산선 {c.fmt(line)}의 {BANKRUPT_NEAR:g}배 이하")
    since = x.now - 3600
    traj = os.path.join(x.root, "a2/trajectory.jsonl")
    calls = Counter(r.get("status") for r in c.tail_json(traj, 20_000_000, b'"llm"')
                    if r.get("kind") == "llm" and (r.get("ts") or 0) >= since and r.get("status") != "ok")
    if calls:
        out.append("  최근 1시간 모델 호출 오류: " + ", ".join(f"{k} {n}" for k, n in calls.most_common()))
    errs = Counter(m.get("decision") for m in c.tail_json(os.path.join(x.root, ".r3/metrics.jsonl"), 20_000_000, b'_error"')
                   if str(m.get("decision") or "").endswith("_error") and (m.get("ts") or 0) >= since)
    errs["판매자 결정 errors"] = sum(1 for r in c.tail_json(traj, 20_000_000, b'"decision"')
                                if r.get("kind") == "decision" and r.get("agent") == "seller"
                                and (r.get("ts") or 0) >= since and r.get("errors"))
    if +errs:
        out.append("  최근 1시간 판매 처리 오류: " + ", ".join(f"{k} {n}" for k, n in (+errs).most_common()))
    if not any(c.settle_side(e.get("data") or {}, x.login) == "shop" for e in market_settles(x.root, since)):
        out.append("  최근 1시간 우리 판매 0건")
    return ["[알림]"] + out if out else []


FLIP_H = 2  # [키 왕복]: window of policy edits
FLIP_BYTES = 20_000_000  # trajectory tail read for it


def _flip_flops(x):
    """[키 왕복]: policy keys whose edited value changed direction 2+ times in the last FLIP_H hours, with the values
    in order and the number of edits; nothing when no key did."""
    chains = {}
    for r in c.tail_json(os.path.join(x.root, "a2/trajectory.jsonl"), FLIP_BYTES, b'"policy_edit"'):
        if not isinstance(r, dict) or r.get("kind") != "policy_edit":
            continue
        ts, sec, key = r.get("ts"), r.get("section"), r.get("key")
        if not _is_num(ts) or ts < x.now - FLIP_H * 3600 or not all(v is None or isinstance(v, str) for v in (sec, key)):
            continue  # a malformed row is skipped, not allowed to blank the whole line
        chains.setdefault((sec, key), [r.get("old")]).append(r.get("new"))
    out = []
    for (sec, key), vals in chains.items():
        signs = [(b > a) - (b < a) for a, b in zip(vals, vals[1:]) if _is_num(a) and _is_num(b) and a != b]
        if sum(s != t for s, t in zip(signs, signs[1:])) >= 2:
            shown = "→".join(f"{v:g}" if _is_num(v) else str(v) for v in vals)
            out.append(f"{_leaf(sec)} {key} {shown} ({len(vals) - 1}번 바꿈)")
    return [f"[키 왕복] 최근 {FLIP_H}시간 방향을 2번 이상 바꾼 키: " + "; ".join(out)] if out else []


def build_compact(root=None, now=None, last_brief_ts=None, extra=None, role=None, off=()):
    """Always-on brief (DESIGN 14.2): time, standing, score position, changes since the last wake, `extra` (caller's
    lines), net profit, shop state, alerts, then an index of the registry sections for the brief_section tool.
    Each part returns fewer lines when its source is missing; COMPACT_CHARS caps the whole, index kept.
    role: a role operator's card leaves out the other side's parts (shop: no buy attempts; buyer: no net profit and
    stock money); None shows both. off: CARD_OPTIONAL names left out (the auditor took them off this card)."""
    x = _Ctx(root, now, last_brief_ts)
    off = set(off or ())
    board = lambda f: (lambda: f(x) if x.board else [])
    parts = [
        ("목표 진척", lambda: section_goal_progress(x.goal, role)),
        ("시각", lambda: section_time(x.now, x.board[0] if x.board else None)),
        ("순위", lambda: _standing(x) if x.board else ["[순위] 순위표 스냅샷 없음"]),
        ("점수 위치", board(_positions)),
        ("가게 회전", lambda: section_turnover(*x.board_day, x.login)
         if x.board and role != "buyer" and "가게 회전" not in off else []),
        ("지난 깨어남 이후", board(_changes)),
        ("extra", lambda: [str(ln) for ln in extra or []]),
        ("커널 밖 변경", lambda: section_outside(x.root, role) if "커널 밖 변경" not in off else []),
        ("시간대별", lambda: _hourly(x, role)),
        ("순이익", lambda: _net_compact(x) if role != "buyer" else []),
        ("가게 상태", lambda: _shop_compact(x)),
        ("돈이 있는 곳", lambda: _money_compact(x, "묶인 돈 수익" not in off) if role != "buyer" else []),
        ("구매 시도", lambda: _buy_compact(x) if role != "shop" else []),
        ("사람이 적은 것", lambda: _human_notes(x)),
        ("키 왕복", lambda: _flip_flops(x)),
        ("알림", lambda: _alerts(x)),
    ]
    out = []
    for label, fn in parts:
        try:
            out += fn()
        except Exception as e:  # a malformed source shows as a fact, the bundle still goes out
            out.append(f"[{label}] 만들지 못함: {type(e).__name__}")
    index = "\n".join(["[더 볼 것] brief_section 도구로 읽음"]
                      + [f"- {n}: {d}" for n, d, _, _ in SECTIONS if n not in COMPACT_FULL])
    text = c.mask("\n".join(out), (x.login,))
    if len(text) + 1 + len(index) > COMPACT_CHARS:
        note = f"\n[잘림: 묶음 상한 {COMPACT_CHARS}자, 나머지는 brief_section으로]"
        text = text[:COMPACT_CHARS - len(index) - len(note) - 1] + note
    return text + "\n" + index


if __name__ == "__main__":
    print(build())
