"""Situation briefing for the operator: facts only, no interpretation, no advice.

build(root, now) -> text. Each section reads its own source and says when the source is missing, so a broken file
shows up as a fact instead of a crash. Other participants appear only as hashed ids.
"""

import os
import statistics as st
from collections import Counter, defaultdict

from kernel import common as c

BOARD_DIRS = ("a2/board", "icrl/state/board")
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
BUYER_KEYS = ("surplus", "forum", "posts", "deals")
SELLER_DECISIONS = ("open", "accept", "cancel", "no_stock", "reject", "declined")


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


def section_market(root, now):
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
        if d.get("own_owner") is True:
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
            f"{c.kst(r['ts'], '%H:%M')} {r.get('agent')} 판 {r.get('version')} {r.get('summary', '')}"[
                :200
            ]
            for r in rows
        )
    ]


def build(root=None, now=None, last_brief_ts=None):
    root = c.data_root(root)
    now = c.now_ts() if now is None else now
    login = c.owner_login(root)
    snaps = board_snapshots(root)
    out = []
    if not snaps:
        out += section_time(now, None)
        out.append("[순위] 순위표 스냅샷 없음")
    else:
        bts, bpath = snaps[-1]
        b = c.load_json(bpath, {}) or {}
        ts6, p6 = _snapshot_near(snaps, bts - 6 * 3600)
        b6 = (c.load_json(p6, {}) or {}) if ts6 < bts else {}
        hours6 = (bts - ts6) / 3600
        out += section_time(now, bts)
        out += section_rank(b, b6, login, hours6)
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
    out += section_seller(root, now)
    out += section_market(root, now)
    out += section_forum(root, login, snaps, now)
    out += section_calls(root, now)
    out += section_harness(
        root, last_brief_ts if last_brief_ts is not None else now - 1800
    )
    return c.mask("\n".join(out), (login,))


if __name__ == "__main__":
    print(build())
