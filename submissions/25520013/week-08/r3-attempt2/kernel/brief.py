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
from kernel import restock

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
    return out


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
        out += section_versions(root, now)
        out += section_categories(root, now, login)
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
