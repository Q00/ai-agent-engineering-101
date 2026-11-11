"""Read-only observation tools. Each returns a capped text observation; summaries first, detail on request.

No tool writes to the market. Other participants are hashed; our own rows are marked 우리.
"""

import json
import os
import re
import shutil
from collections import Counter

from kernel import brief as b
from kernel import common as c
from kernel import memory

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


def policy(root, days=1, section=None):
    """시도 1 규칙의 policy.json 값: 구역(defaults.food, defaults.goods, 상품군 전체 경로, shop, buyer.defaults.food, buyer.defaults.goods)마다 편집 가능 키의 지금 값,  허용 범위와 한 번에 바꿀 수 있는 폭, 최근 days일의 정책 수정 기록. section(구역 이름의 일부, 예: "음료", "buyer")을 주면 그 구역 줄과 그 구역의 수정 기록만 돌려줘 결과가 잘리지 않음. LIVE가 꺼진 동안 done.policy로 고침."""
    from kernel import policy_edit as pe
    from kernel.trajectory import Trajectory

    cats = {v.get("category") for v in b._catalog(root).values() if isinstance(v, dict) and v.get("category")}
    days = max(1, min(7, int(days))) if isinstance(days, (int, float)) and not isinstance(days, bool) else 1
    rows = pe.recent(Trajectory(os.path.join(root, "a2/trajectory.jsonl")), c.now_ts(), days * 24)
    hist = [f"- {c.kst(r.get('ts'))} {r.get('section')} {r.get('key')} {r.get('old')} -> {r.get('new')}: {r.get('reason')}" for r in rows[-30:]]
    view = pe.view(root, cats)
    if isinstance(section, str) and section.strip():
        sec = section.strip()
        view = "\n".join(ln for ln in view.split("\n") if not ln.startswith("- ") or sec in ln.split(": ", 1)[0])
        hist = [h for h in hist if sec in h]
    return c.cap(view + "\n최근 정책 수정\n" + ("\n".join(hist) or "없음"), MAX_CHARS)


def stockouts(root, hours=1, now=None):
    """재고 없음으로 거절한 요청을 상품군 잎과 SKU별로 셈: 최근 hours시간(1~24)의 거절 수, 지금 그 SKU의 우리 재고 수. 어느 상품군의 stock_target, qty_per_buy를 올릴지 정할 때 씀."""
    from collections import Counter

    now = c.now_ts() if now is None else now
    hours = max(1, min(24, hours)) if isinstance(hours, (int, float)) and not isinstance(hours, bool) else 1
    cat = b._catalog(root)
    inv = c.load_json(os.path.join(root, ".market/inventory.json"))
    have = Counter(u.get("sku") for u in (inv.get("units") or [] if isinstance(inv, dict) else []) if isinstance(u, dict))
    by_sku, short, total = Counter(), Counter(), 0
    for m in c.tail_json(os.path.join(root, ".r3/metrics.jsonl"), 20_000_000, b'"no_stock"'):
        ts = m.get("ts")
        if m.get("decision") != "no_stock" or not isinstance(ts, (int, float)) or ts < now - hours * 3600 or ts > now:
            continue
        total += 1
        o = m.get("outcome") if isinstance(m.get("outcome"), dict) else {}
        sku = o.get("sku") if o.get("sku") in cat else None
        if sku is None:  # rule code v2.12 logs only the request text: take the first catalog SKU id named in it
            sku = next((x for x in re.findall(r"\b[A-Z][0-9a-f]{8}\b", str(o.get("request") or "")) if x in cat), None)
        by_sku[sku] += 1
        short[sku] += "short_shelf" in o  # stock existed but its remaining shelf was too short for the request
    leaves = {}
    for sku, n in by_sku.items():
        if sku is None:
            continue
        info = cat.get(sku) if isinstance(cat.get(sku), dict) else {}
        leaves.setdefault(str(info.get("category") or "?"), []).append((n, sku, str(info.get("name") or "")[:30]))
    out = [f"최근 {hours:g}시간 재고 없음 거절 {total}건 (SKU를 알 수 없는 요청 {by_sku.get(None, 0)}건)"]
    for leaf, rows in sorted(leaves.items(), key=lambda kv: -sum(r[0] for r in kv[1]))[:12]:
        out.append(f"- {leaf}: {sum(r[0] for r in rows)}건 (그중 재고는 있으나 남은 기한이 짧아 거절 {sum(short[r[1]] for r in rows)}건)")
        out += [f"    {sku} {name}: 거절 {n}건, 지금 재고 {have.get(sku, 0)}개" for n, sku, name in sorted(rows, reverse=True)[:5]]
    return c.cap("\n".join(out), MAX_CHARS)


NET_SKU_TOP = 15  # net_profit: per-SKU rows for one category leaf


def net_profit(root, hours=12, category=None, now=None):
    """Our gross profit minus warranty refunds over the last `hours` (1-48) by sale time, per category leaf and grade
    with breaks, refunds and break rate. category (a leaf) adds per-SKU rows: sku, grade, n, gross, refunds, net,
    break rate, top 15 by |net|."""
    now = c.now_ts() if now is None else now
    hours = max(1.0, min(48.0, float(hours))) if isinstance(hours, (int, float)) and not isinstance(hours, bool) else 12.0
    sales, unmatched, delays = b.net_profit_data(root, now, hours, c.owner_login(root))
    leaf = b._leaf(category) if category else None
    lines = b.net_profit_lines(sales, unmatched, delays, hours, leaves=None if leaf is None else {leaf})
    if leaf is not None:
        by = {}
        for s in sales:
            if s["leaf"] == leaf:
                by.setdefault((s["sku"], s["grade"]), []).append(s)
        rows = sorted(((k, b._net_sum(v)) for k, v in by.items()), key=lambda kv: -abs(kv[1][2] - kv[1][6]))
        lines.append(f"{leaf} SKU별 {len(rows)}묶음" + ("" if rows else ": 판매 없음"))
        lines += [f"{sku} {g}: {x[0]}건, 총 {c.fmt(x[2])}, 환불 {c.fmt(x[6])}, 순 {c.fmt(x[2] - x[6])}, "
                  f"고장률 {x[4] / x[0]:.0%}" for (sku, g), x in rows[:NET_SKU_TOP]]
    return c.cap("\n".join(lines), MAX_CHARS)


def recall(root, query, agent="operator", k=5):
    """Search the mem0 memory (lessons, harness results, shop habits seen by our buyer). agent = operator, seller or
    buyer (whose memories), k = 1..10. Returns the best matches, or says the memory daemon did not answer."""
    if agent not in ("operator", "seller", "buyer") or not isinstance(query, str) or not query.strip():
        return "인자 오류: query는 글, agent는 operator, seller, buyer 중 하나"
    hits = memory.search(memory.sock_path(root), agent, query, k if isinstance(k, int) else 5, timeout=RECALL_TIMEOUT_S)
    if not hits:
        return f"기억 없음 또는 메모리 데몬 응답 없음 ({agent}, {query[:60]})"
    return c.cap(f"기억 {agent} {len(hits)}건\n" + "\n".join(f"- {h}" for h in hits), MAX_CHARS)


RECALL_TIMEOUT_S = 5.0

def brief_section(root, section):
    """브리핑 한 절 전체. section은 브리핑 끝 [더 볼 것] 목록의 이름(예: 순이익, 가게 목표, 협상 결과). 모르는 이름이면 쓸 수 있는 이름을 돌려줌."""
    return c.cap(b.section(root, section), MAX_CHARS)  # not `name`: call() takes the tool name as its first argument


GUIDE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "MARKET_BRIEF.md")


def _guide_sections():
    """-> (path, [(heading title, text with heading)]) of the market guide's "## " sections; None when unreadable."""
    path = os.environ.get("R3A2_MARKET_BRIEF") or GUIDE_PATH
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return path, None
    secs = []
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            secs.append([line[3:].strip(), line])
        elif secs:
            secs[-1][1] += line
    return path, [tuple(s) for s in secs]


def _guide_toc(secs):
    return [f"- {t} ({len(body)}자)" for t, body in secs]


def _guide_find(secs, q):
    """The first section whose title starts with "<q>." when q is a number, else whose title contains q."""
    q = str(q).strip()
    return next((s for s in secs if (s[0].startswith(q + ".") if q.isdigit() else q and q in s[0])), None)


def guide(root, section=None):
    """시장 설명서(MARKET_BRIEF.md). section 없으면 목차(절 제목과 글자 수), 있으면 그 절: 번호(예: 10) 또는 제목 일부. 긴 절은 마지막 부분만 줌(기록 절은 아래로 덧붙여 최신이 끝에 있음)."""
    path, secs = _guide_sections()
    if secs is None:
        return f"시장 설명서 없음: {os.path.basename(path)}"
    toc = "시장 설명서 목차\n" + "\n".join(_guide_toc(secs))
    if section is None:
        return c.cap(toc, MAX_CHARS)
    q = str(section).strip()
    hit = _guide_find(secs, q)
    if hit is None:
        return c.cap(f"절 없음: {q}\n{toc}", MAX_CHARS)
    body = hit[1]
    if len(body) <= MAX_CHARS:
        return body
    note = f"[앞부분 생략: '{hit[0]}' 전체 {len(body)}자 중 마지막 부분만. 이 절은 아래로 덧붙여 가므로 최신이 끝에 있음]\n"
    return note + body[-max(0, MAX_CHARS - len(note)):]


def guide_always(sections=("1", "7")):
    """Text for the always-on prompt: the guide's table of contents, then the given sections (number or title part)
    in full. Not a tool; the guide tool reads the rest."""
    path, secs = _guide_sections()
    if secs is None:
        return f"시장 설명서 없음: {os.path.basename(path)}"
    body = []
    for q in sections:
        hit = _guide_find(secs, q)
        body.append(hit[1].rstrip("\n") if hit else f"절 없음: {q}")
    return "\n\n".join(["\n".join(["## 시장 설명서 목차 (나머지 절은 guide 도구로 읽음)"] + _guide_toc(secs))] + body)


def code_search(root, pattern, file=None):
    """규칙 코드(policy.py, hooks/*.py)에서 pattern(정규식, 틀리면 글자 그대로)을 찾아 '파일:줄: 내용'을 40줄까지 줌. file을 주면 그 파일만 찾음. 원인을 코드에서 찾을 때 씀."""
    from kernel import code_edit

    return code_edit.search(root, pattern, file)


def code_read(root, file, start=1, lines=120, end=None, count=None):
    """규칙 코드 파일 하나를 줄 번호와 함께 읽음 (한 번에 200줄까지, start부터 lines줄. end를 주면 그 줄까지). done.code로 고치려는 파일은 이번 점검에서 이 도구로 먼저 읽어야 함."""
    from kernel import code_edit

    if isinstance(count, int) and not isinstance(count, bool):  # 11-11: 5 of 12 live calls failed on end= / count=
        lines = count
    elif isinstance(end, int) and not isinstance(end, bool) and isinstance(start, int):
        lines = end - start + 1
    return code_edit.read(root, file, start, lines)


def experiments(root, hours=6, now=None):
    """실험 장부 전체: 진행 중인 실험 모두(범위, 키, 값, 판정 시각, 건 운영자)와 최근 hours시간에 끝난 실험의 판정, 그리고 되돌림 뒤 새 실험이 막힌 범위와 풀리는 시각. 새 실험을 걸기 전에 겹치는 범위를 볼 때 씀."""
    from kernel import experiments as ex

    now = c.now_ts() if now is None else now
    try:
        hours = min(48.0, max(1.0, float(hours)))
    except (TypeError, ValueError):
        hours = 6.0
    rows = [e for e in ex.load(root) if isinstance(e, dict) and "id" in e]
    who = lambda e: f" [{ex.role_of(e) or '-'}]"
    run = [e for e in rows if e.get("status") == "running"]
    out = [f"진행 중 {len(run)}건 (범위가 겹치는 새 실험은 거절됨)"]
    for e in run:
        try:
            out.append(ex._run_line(e) + f", 범위 {e.get('section')}" + who(e))
        except Exception:  # noqa: BLE001 - a malformed entry is shown by id only
            out.append(f"  진행 #{e.get('id')}")
    ended = [e for e in rows if e.get("status") != "running" and ex._num_ok(e.get("ended_ts")) and e["ended_ts"] >= now - hours * 3600]
    out.append(f"최근 {hours:g}시간에 끝난 실험 {len(ended)}건")
    for e in sorted(ended, key=lambda e: -e["ended_ts"]):
        try:
            line = ex._ended_line(e) + f", 범위 {e.get('section')}, 끝 {c.kst(e['ended_ts'], '%H:%M')}" + who(e)
        except Exception:  # noqa: BLE001
            line = f"  #{e.get('id')} {e.get('status')}"
        if e.get("status") in ex.BLOCK_AFTER and e.get("mode") != "split" and now < e["ended_ts"] + ex.CONTAM_S:
            line += f" → 이 범위의 새 전후 비교 실험은 {c.kst(e['ended_ts'] + ex.CONTAM_S, '%H:%M')}까지 막힘"
        out.append(line)
    return c.cap("\n".join(out), MAX_CHARS)


def restock_log(root, hours=2, now=None):
    """재입고 훅 기록: 최근 hours시간(0.5~24)의 실행 수와 멈춘 사유(why)별 수, 산 실행(줄, 수량, 예산, 메모), 상품군 잎과 SKU별 산 수량, 재고 상한이나 예산으로 멈춘 횟수와 마지막 재고 원가, 잔고."""
    from kernel import code_edit

    return code_edit.restock_log(root, hours, now)


ROLES = ("shop", "buyer")  # the role operators (kernel.operator --role); one shared toolbox
SEARCH_MAX = 8
SEARCH_SKIP = ("versions", "harness_read", "replay_probe")  # operator.HARNESS_TOOLS: not callable in compact mode
SEARCH_ARGS_SKIP = ("root", "now", "harness_root", "dry")
_TOKEN = re.compile(r"[0-9A-Za-z_가-힣]+")


def _search_entries(root):
    """[(name, description, argument names, maker role or None)] of every tool an operator can call: built-ins with
    the auditor's re-descriptions (hidden ones left out), then the operator-made q_ tools."""
    import inspect

    from kernel import auditor, made_tools

    st = auditor.tool_state(root)
    out = []
    for name, (fn, agents) in TOOLS.items():
        if "operator" not in agents or name in SEARCH_SKIP or (name in st["hidden"] and name not in auditor.CORE_TOOLS):
            continue
        args = [p for p in inspect.signature(fn).parameters if p not in SEARCH_ARGS_SKIP]
        out.append((name, st["desc"].get(name) or " ".join((fn.__doc__ or "").split()), args, None))
    for t in made_tools.load(root):
        desc = st["desc"].get(t["name"]) or " ".join(str(t.get("description") or "").split())
        out.append((t["name"], desc, ["hours"], t.get("role") or "operator"))
    return out


def tool_search(root, query):
    """도구 찾기: query(한글이나 영어 낱말, 여럿이면 띄어 씀)를 모든 도구(운영자가 만든 q_ 도구 포함)의 이름과 설명에서 찾아 맞는 도구 8개까지 설명 전체, 인자 이름, 추천 목록에 둔 운영자(shop, buyer)를 줌. 도구 목록에 이름만 나온 도구를 쓰기 전에 부름."""
    from kernel import auditor

    if not isinstance(query, str) or not query.strip():
        return "인자 오류: query는 찾을 낱말 글"
    q = query.strip()
    toks = list(dict.fromkeys(t.lower() for t in _TOKEN.findall(q))) or [q.lower()]
    rec = {r: set(auditor.recommended(root, r)) for r in ROLES}
    scored = []
    for name, desc, args, by in _search_entries(root):
        n, d = name.lower(), desc.lower()
        s = sum(3 * (t in n) + (t in d) for t in toks)
        if s:
            scored.append((-s, name, desc, args, by))
    hits = sorted(scored)[:SEARCH_MAX]
    out = [f"도구 찾기 '{q[:60]}': {len(hits)}개" + ("" if hits else ". 다른 낱말(한글 또는 영어)로 다시 찾음")]
    for _, name, desc, args, by in hits:
        who = ", ".join(r for r in ROLES if name in rec[r]) or "없음"
        made = "" if by is None else f", 만든 도구(만든 쪽 {by})"
        out.append(f"- {name}({', '.join(args)}) 추천: {who}{made}\n  {desc}")
    return c.cap("\n".join(out), MAX_CHARS)


# Default recommended lists of the role operators, by what each tool reads (the auditor moves tools in and out,
# kernel.auditor.recommended). Every tool stays callable by both roles; a role sees the others by name only.
RECOMMENDED = {
    "shop": ("board", "ledger", "events", "stockouts", "sku_stats", "net_profit", "inventory", "restock_log",
             "policy", "experiments", "code_search", "code_read", "brief_section", "guide", "recall", "tool_search"),
    "buyer": ("board", "ledger", "events", "shops", "catalog", "policy", "experiments", "code_search", "code_read",
              "brief_section", "guide", "recall", "tool_search"),
}

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
    "policy": (policy, ("operator",)),
    "net_profit": (net_profit, ("operator",)),
    "stockouts": (stockouts, ("operator",)),
    "recall": (recall, ("operator",)),
    "brief_section": (brief_section, ("operator",)),
    "guide": (guide, ("operator",)),
    "code_search": (code_search, ("operator",)),
    "code_read": (code_read, ("operator",)),
    "restock_log": (restock_log, ("operator",)),
    "experiments": (experiments, ("operator",)),
    "tool_search": (tool_search, ("operator",)),
}


def call(name, agent, root, /, **kwargs):
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
