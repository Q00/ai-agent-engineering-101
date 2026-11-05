"""Read-only observation tools. Each returns a capped text observation; summaries first, detail on request.

No tool writes to the market. Other participants are hashed; our own rows are marked 우리.
"""

import json
import os
import shutil
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


HARNESS_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness")
PROBE_MAX_N = 50  # each probe case is one model call


def _quantiles(xs):
    xs = sorted(xs)
    if not xs:
        return "-"
    q = [xs[min(len(xs) - 1, int(p * len(xs)))] for p in (0.25, 0.5, 0.75)]
    return f"p25 {q[0]:.3f}, 중앙 {q[1]:.3f}, p75 {q[2]:.3f} (n {len(xs)})"


def sku_stats(root, sku=None, category=None, hours=6, now=None, limit=15):
    """Our shop per SKU over the last `hours`: sales, price/list quantiles, requests (seller.open decisions whose
    memory_keys name the SKU or category) and sales/requests. Filter by sku or category prefix."""
    now = c.now_ts() if now is None else now
    since = now - float(hours) * 3600
    login = c.owner_login(root)
    cat = c.load_json(os.path.join(root, ".market/catalog.json"), {}) or {}

    def keep(s):
        if sku and s != sku:
            return False
        return not category or str((cat.get(s) or {}).get("category", "")).startswith(category)

    sales, ratios, reqs = Counter(), {}, Counter()
    for e in c.tail_json(os.path.join(root, ".r3/events.jsonl"), 40_000_000, b'"settle"'):
        d = e.get("data") or {}
        if (
            e.get("type") != "settle"
            or (e.get("ts") or 0) < since
            or c.settle_side(d, login) != "shop"
            or not keep(d.get("sku"))
        ):
            continue
        sales[d.get("sku")] += 1
        lp = b._list_price(cat, d.get("sku"))
        if lp and isinstance(d.get("amount"), (int, float)):
            ratios.setdefault(d.get("sku"), []).append(d["amount"] / lp)
    for r in c.tail_json(os.path.join(root, "a2/trajectory.jsonl"), 50_000_000, b'"seller.open"'):
        if r.get("kind") != "decision" or r.get("agent") != "seller" or (r.get("ts") or 0) < since:
            continue
        for k in r.get("memory_keys") or []:
            if k in cat and keep(k):
                reqs[k] += 1
    skus = sorted(set(sales) | set(reqs), key=lambda k: -(sales[k] + reqs[k]))
    all_r = [x for v in ratios.values() for x in v]
    n_s, n_q = sum(sales.values()), sum(reqs.values())
    lines = [
        f"우리 가게 SKU 집계 최근 {hours}시간{' sku=' + sku if sku else ''}{' category=' + category if category else ''}: "
        f"판매 {n_s}, 요청 {n_q}, 판매/요청 {n_s / n_q:.3f}" if n_q else
        f"우리 가게 SKU 집계 최근 {hours}시간: 판매 {n_s}, 요청 0",
        f"정가 대비 판매가 전체: {_quantiles(all_r)}",
    ]
    for k in skus[: max(1, int(limit))]:
        conv = f"{sales[k] / reqs[k]:.3f}" if reqs[k] else "-"
        lines.append(
            f"{k} {str((cat.get(k) or {}).get('name') or '')[:30]} 정가 {c.fmt(b._list_price(cat, k))}: 판매 {sales[k]}, "
            f"요청 {reqs[k]}, 판매/요청 {conv}, 정가 대비 {_quantiles(ratios.get(k, []))}"
        )
    return c.cap("\n".join(lines), MAX_CHARS)


def versions(root, harness_root=None):
    """Harness versions per agent: apply time, reason, changed files, which is current and which is last_good."""
    from kernel import versions as vz

    st = vz.load_state(harness_root or HARNESS_ROOT)
    lines = []
    for agent in vz.AGENTS:
        a = st.get(agent) or {}
        if not a:
            lines.append(f"{agent}: 기록 없음")
            continue
        lines.append(f"{agent}: 지금 판 {a.get('current')}, 마지막 정상 판 {a.get('last_good')}")
        for h in (a.get("history") or [])[-12:]:
            when = c.kst(h["ts"]) if isinstance(h.get("ts"), (int, float)) else "-"
            back = f" (판 {h['restored_from']} 복원)" if h.get("restored_from") else ""
            lines.append(
                f"  판 {h.get('version')} {when}{back} 파일 {', '.join(h.get('files') or []) or '-'}: "
                f"{str(h.get('reason') or '')[:200]}"
            )
    return c.cap("\n".join(lines), MAX_CHARS)


def trajectory(root, kind=None, agent=None, hours=6, limit=20, contains=None, now=None):
    """Deep-tier search of a2/trajectory.jsonl: counts by kind and agent first, then the latest `limit` matching
    rows (each cut to 600 chars). contains is a plain substring filter on the row."""
    now = c.now_ts() if now is None else now
    since = now - float(hours) * 3600
    needle = json.dumps(kind).encode() if kind else None
    hits = []
    for r in c.tail_json(os.path.join(root, "a2/trajectory.jsonl"), 50_000_000, needle):
        if (r.get("ts") or 0) < since or (kind and r.get("kind") != kind) or (agent and r.get("agent") != agent):
            continue
        line = json.dumps(r, ensure_ascii=False)
        if contains and contains not in line:
            continue
        hits.append(line)
    by = Counter()
    for line in hits:
        r = json.loads(line)
        by[(r.get("agent"), r.get("kind"))] += 1
    lines = [
        f"궤적 최근 {hours}시간 kind={kind} agent={agent}: {len(hits)}줄. "
        + ", ".join(f"{a}/{k} {n}" for (a, k), n in by.most_common(12))
    ]
    lines += [x[:600] for x in hits[-max(0, int(limit)) :]]
    return c.cap(c.mask("\n".join(lines), (c.owner_login(root),)), MAX_CHARS)


def harness_read(root, target, path=None, version=None, harness_root=None):
    """Read a harness: no path lists the files and sizes; path returns that file. version reads a library copy."""
    from kernel import versions as vz

    hr = harness_root or HARNESS_ROOT
    if target not in vz.AGENTS:
        return f"target은 {', '.join(vz.AGENTS)} 중 하나"
    d = os.path.join(hr, target) if version is None else os.path.join(vz.library_dir(hr), "harnesses", target, str(int(version)))
    if not os.path.isdir(d):
        return f"{target} 판 {version} 없음"
    if path is None:
        out = []
        for top, _, files in os.walk(d):
            for f in sorted(files):
                p = os.path.join(top, f)
                out.append(f"{os.path.relpath(p, d)} {os.path.getsize(p)}바이트")
        return c.cap(f"{target} {'지금 판' if version is None else '판 ' + str(version)}\n" + "\n".join(sorted(out)), MAX_CHARS)
    err = vz.path_error(path) if path not in ("VERSION", "EXPERIENCE.md") else None
    p = os.path.join(d, *str(path).split("/"))
    if err or not os.path.isfile(p):
        return err or f"파일 없음: {path}"
    with open(p, encoding="utf-8") as f:
        return c.cap(f.read(), MAX_CHARS)


def replay_probe(root, target, version=None, files=None, harness_dir=None, n=20, harness_root=None, dry=False):
    """Run recorded requests (seller) or the current order (buyer) through a candidate harness, no market contact.

    Candidate: version (a library copy), files ({path: content or null} applied to the current harness), harness_dir
    (only inside a2/probe/), or none (the current harness as a baseline). n is capped at 50 (one model call each).
    Writes only under a2/probe/. Returns decision counts by action, asks vs list price, routes and error kinds.
    """
    import time

    from kernel import harness as hz
    from kernel import replay
    from kernel import versions as vz

    if target not in ("seller", "buyer"):
        return "target은 seller 또는 buyer"
    hr = harness_root or HARNESS_ROOT
    n = max(1, min(PROBE_MAX_N, int(n)))
    probe = os.path.join(root, "a2/probe")
    pdir = os.path.join(probe, f"{target}-{int(time.time() * 1000)}")
    os.makedirs(pdir, exist_ok=True)
    if harness_dir is not None:
        hdir = os.path.realpath(os.path.join(probe, harness_dir) if not os.path.isabs(harness_dir) else harness_dir)
        if os.path.commonpath([hdir, os.path.realpath(probe)]) != os.path.realpath(probe):
            return "harness_dir는 a2/probe/ 안이어야 함"
        errs = hz.validate_dir(hdir)
    elif version is not None:
        src = os.path.join(vz.library_dir(hr), "harnesses", target, str(int(version)))
        if not os.path.isdir(src):
            return f"{target} 판 {version}이 라이브러리에 없음"
        hdir = os.path.join(pdir, target)
        shutil.copytree(src, hdir)
        errs = hz.validate_dir(hdir)
    elif files is not None:
        hdir = os.path.join(pdir, target)
        errs = vz.stage(os.path.join(hr, target), hdir, files, (c.owner_login(root),))
    else:
        hdir = os.path.join(hr, target)
        errs = hz.validate_dir(hdir)
    if errs:
        return "후보 하네스 형식 오류: " + "; ".join(errs)
    out = os.path.join(pdir, "trajectory.jsonl")
    summary = replay.run(root, target, n, out, dry=dry, harness_dir=hdir)
    lp = {}
    inv = c.load_json(os.path.join(root, ".market/inventory.json"), {}) or {}
    cat = c.load_json(os.path.join(root, ".market/catalog.json"), {}) or {}
    for u in inv.get("units") or []:
        if isinstance(u, dict):
            lp[u.get("unit")] = b._list_price(cat, u.get("sku"))
    ratios = []
    for r in c.tail_json(out, 50_000_000, b'"decision"'):
        d = r.get("decision") or {}
        if d.get("action") == "offer" and lp.get(d.get("unit")) and isinstance(d.get("ask"), (int, float)):
            ratios.append(d["ask"] / lp[d["unit"]])
    lines = [
        f"replay_probe {target} n {summary['n']}, 형식 통과 {summary['valid']}, 경로 {summary['routes']}",
        f"결정 {summary['actions']}",
        f"부른 값 / 정가: {_quantiles(ratios)}",
        f"오류 종류 {summary['error_kinds'] or '없음'}, 지연 {summary['latency_s']}",
        f"기록: a2/probe/{os.path.basename(pdir)}/trajectory.jsonl",
    ]
    return c.cap("\n".join(lines), MAX_CHARS)


TOOLS = {
    "board": (board, ("operator",)),
    "ledger": (ledger, ("operator",)),
    "events": (events, ("operator",)),
    "shops": (shops, ("operator", "seller", "buyer")),
    "catalog": (catalog, ("operator", "seller", "buyer")),
    "inventory": (inventory, ("operator", "seller", "buyer")),
    "sku_stats": (sku_stats, ("operator",)),
    "versions": (versions, ("operator",)),
    "trajectory": (trajectory, ("operator",)),
    "harness_read": (harness_read, ("operator",)),
    "replay_probe": (replay_probe, ("operator",)),
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
