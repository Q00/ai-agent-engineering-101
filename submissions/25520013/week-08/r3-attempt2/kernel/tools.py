"""Read-only observation tools. Each returns a capped text observation; summaries first, detail on request.

No tool writes to the market. Other participants are hashed; our own rows are marked 우리.
"""

import json
import os
from collections import Counter

from kernel import brief as b
from kernel import common as c

MAX_CHARS = 16_000  # about 4k tokens (estimate); the operator may change it through its harness settings


def _board(root):
    snaps = b.board_snapshots(root)
    return (snaps[-1][0], c.load_json(snaps[-1][1], {}) or {}) if snaps else (None, {})


def board(root, view="everyone", top=10):
    """Players table: top N plus our row. view = everyone (total) or class (student_total, students only)."""
    login = c.owner_login(root)
    ts, bd = _board(root)
    P = bd.get("players") or []
    if not P:
        return "순위표 스냅샷 없음"
    key = "student_total" if view == "class" else "total"
    rows = [p for p in P if p.get("student")] if view == "class" else list(P)
    rows.sort(key=lambda p: -(p.get(key) or 0))
    lines = [f"순위표 {view} {c.kst(ts)} 기준, {len(rows)}명"]
    for i, p in enumerate(rows, 1):
        mine = c.is_mine(p, login)
        if i <= top or mine:
            who = "우리" if mine else c.hid(p.get("player") or p.get("owner"))
            lines.append(
                f"{i}. {who} {key} {p.get(key) or 0:.3f} 가게 {(p.get('shop') or {}).get('all') or 0:.3f} "
                f"구매자 {(p.get('buyer') or {}).get('all') or 0:.3f} 학생 {'예' if p.get('student') else '아니오'}"
            )
    return c.cap("\n".join(lines), MAX_CHARS)


def ledger(root, hours=6):
    """Our shop and buyer terms now vs about `hours` ago, from board snapshots."""
    login = c.owner_login(root)
    snaps = b.board_snapshots(root)
    if not snaps:
        return "순위표 스냅샷 없음"
    t1, p1 = snaps[-1]
    t0, p0 = min(snaps, key=lambda s: abs(s[0] - (t1 - hours * 3600)))
    b0, b1 = c.load_json(p0, {}) or {}, c.load_json(p1, {}) or {}
    lines = [f"장부 {c.kst(t0)} → {c.kst(t1)} ({(t1 - t0) / 3600:.1f}시간)"]
    for side, keys in (("shops", b.SHOP_KEYS), ("buyers", b.BUYER_KEYS)):
        m0 = next((x for x in b0.get(side) or [] if c.is_mine(x, login)), {}) or {}
        m1 = next((x for x in b1.get(side) or [] if c.is_mine(x, login)), {}) or {}
        parts = []
        for k in ("all",) + tuple(keys):
            v0, v1 = m0.get(k), m1.get(k)
            if isinstance(v1, (int, float)):
                d = (
                    f" ({'+' if (v1 - (v0 or 0)) >= 0 else ''}{c.fmt(v1 - v0)})"
                    if isinstance(v0, (int, float))
                    else ""
                )
                parts.append(f"{k} {c.fmt(v1)}{d}")
        lines.append(f"{'가게' if side == 'shops' else '구매자'}: " + ", ".join(parts))
    inv = c.load_json(os.path.join(root, ".market/inventory.json"), {}) or {}
    lines.append(
        f"지금 잔고 {c.fmt(inv.get('balance'))}, 재고 {len(inv.get('units') or [])}개"
    )
    return c.cap("\n".join(lines), MAX_CHARS)


def events(root, type="settle", hours=1.0, sku=None, ours=None, limit=20, now=None):
    """Event log filtered by type, window, SKU and whether it involves our shop: count, then the latest samples."""
    login = c.owner_login(root)
    now = c.now_ts() if now is None else now
    needle = json.dumps(type).encode() if type else None
    hits = []
    for e in c.tail_json(os.path.join(root, ".r3/events.jsonl"), 40_000_000, needle):
        if type and e.get("type") != type:
            continue
        if (e.get("ts") or 0) < now - hours * 3600:
            continue
        d = e.get("data") or {}
        if sku and d.get("sku") != sku:
            continue
        if ours is not None and (c.settle_side(d, login) == "shop") != bool(ours):
            continue
        hits.append(e)
    by_sku = Counter((e.get("data") or {}).get("sku") for e in hits)
    lines = [
        f"events type={type} 최근 {hours}시간: {len(hits)}건"
        + (
            ", SKU별 상위 " + ", ".join(f"{k} {v}" for k, v in by_sku.most_common(8))
            if hits
            else ""
        )
    ]
    for e in hits[-max(0, int(limit)) :]:
        lines.append(json.dumps(c.scrub(e, login), ensure_ascii=False))
    return c.cap("\n".join(lines), MAX_CHARS)


def shops(root):
    """Shops currently listed by the market: count and per-shop fields, names hashed."""
    login = c.owner_login(root)
    sh = c.load_json(os.path.join(root, ".market/shops.json"))
    if sh is None:
        return "shops.json 없음"
    rows = (
        sh
        if isinstance(sh, list)
        else list(sh.values())
        if isinstance(sh, dict)
        else []
    )
    out = []
    for r in rows:
        r = dict(r) if isinstance(r, dict) else {"value": r}
        if "name" in r and not c.is_mine(r, login):
            r["name"] = c.hid(r["name"])
        out.append(json.dumps(c.scrub(r, login), ensure_ascii=False))
    lines = [f"가게 {len(rows)}곳"] + out
    return c.cap("\n".join(lines), MAX_CHARS)


def catalog(root, category=None, query=None, sku=None):
    """Market catalog (public data, 5,600+ items): filter by category prefix, name substring or SKU."""
    cat = c.load_json(os.path.join(root, ".market/catalog.json"), {}) or {}
    items = [(k, v or {}) for k, v in cat.items()
             if (not sku or k == sku) and (not category or str((v or {}).get("category", "")).startswith(category))
             and (not query or query in str((v or {}).get("name", "")))]
    cats = Counter(v.get("category") for _, v in items)
    lines = [f"상품 {len(items)}개, 품목군 {len(cats)}개: " + ", ".join(f"{k} {n}" for k, n in cats.most_common(10))]
    lines += [f"{k} | {v.get('name')} | {v.get('category')} | 정가 {v.get('list_price')} | 종류 {v.get('kind')} | "
              f"유통 {v.get('shelf_hours')}시간" for k, v in items]
    return c.cap("\n".join(lines), MAX_CHARS)


def inventory(root, now=None):
    """Our stock grouped by SKU and grade: count, cost, nearest expiry."""
    now = c.now_ts() if now is None else now
    inv = c.load_json(os.path.join(root, ".market/inventory.json"))
    if not isinstance(inv, dict):
        return "inventory.json 없음"
    cat = c.load_json(os.path.join(root, ".market/catalog.json"), {}) or {}
    groups = {}
    for u in inv.get("units") or []:
        g = groups.setdefault(
            (u.get("sku"), u.get("grade")), {"n": 0, "cost": 0.0, "exp": None}
        )
        g["n"] += 1
        g["cost"] += float(u.get("cost") or 0)
        if u.get("expires_at"):
            g["exp"] = min(g["exp"] or u["expires_at"], u["expires_at"])
    lines = [
        f"잔고 {c.fmt(inv.get('balance'))}, 재고 {sum(g['n'] for g in groups.values())}개, {len(groups)}묶음"
    ]
    for (sku, grade), g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
        exp = (
            f", 가장 이른 유통기한 {(g['exp'] - now) / 3600:.0f}시간 뒤"
            if g["exp"]
            else ""
        )
        item = cat.get(sku) or {}
        lines.append(f"{sku} {str(item.get('name') or '')[:40]} 정가 {c.fmt(item.get('list_price'))} 등급 {grade}: "
                     f"{g['n']}개, 원가 합 {c.fmt(g['cost'])}{exp}")
    return c.cap("\n".join(lines), MAX_CHARS)


TOOLS = {
    "board": (board, ("operator",)),
    "ledger": (ledger, ("operator",)),
    "events": (events, ("operator",)),
    "shops": (shops, ("operator", "seller", "buyer")),
    "catalog": (catalog, ("operator", "seller", "buyer")),
    "inventory": (inventory, ("operator", "seller", "buyer")),
}


def call(name, agent, root, **kwargs):
    """Run a tool for an agent. Unknown tool, wrong agent or bad arguments come back as an observation."""
    if name not in TOOLS:
        return f"도구 없음: {name}. 쓸 수 있는 도구: {', '.join(k for k, v in TOOLS.items() if agent in v[1])}"
    fn, agents = TOOLS[name]
    if agent not in agents:
        return f"{agent}는 {name} 도구를 쓸 수 없음"
    try:
        return fn(root, **kwargs)
    except TypeError as e:
        return f"인자 오류: {e}. 설명: {fn.__doc__}"
