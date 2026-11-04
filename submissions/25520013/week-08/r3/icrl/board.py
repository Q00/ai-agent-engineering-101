"""Board reader and reward weights (DESIGN.md sec 5, 10).

GET /api/board at most every 1800 s (read-only, no query, no identity in the request), cached under
<state>/board/<ts>.json (last 60 kept). On failure the last cache is used; after 3 consecutive 401/429 the weights
fall back to the fixed official weights and an alarm is logged. fetch is injectable (tests never touch the network).

Weight of term k = official weight x percentile slope, the slope being the mid-rank percentile change when our value
moves +-5 % among participants with deals > 0 (house rows excluded), per unit of the term. A weighted per-hour
proxy sum_k w_k x rate_k is then in official-score units per hour for every key, so keys are comparable.
"""

import json
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://market.ourolabs.ai/api/board"
PERIOD_S = 1800
KEEP = (
    60  # 30 h: a long block's previous-day comparison still finds its board snapshots
)
W_SHOP = {"profit": 0.5, "revenue": 0.3, "stars_rank": 0.2}
W_BUYER = {"surplus": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1}
REWARD_TERMS = {
    "profit": "shops",
    "revenue": "shops",
    "stars_rank": "shops",
    "surplus": "buyers",
    "deals": "buyers",
    "forum": "buyers",
    "posts": "buyers",
}
OFFICIAL = {**W_SHOP, **W_BUYER}
# Terms with no per-block ledger signal: measured as the change of our board value between the cached snapshots
# nearest the block start and end (sec 5). A snapshot farther than SNAP_TOL_S from either end is missing.
BOARD_TERMS = {"stars_rank": "shops", "forum": "buyers", "posts": "buyers"}
SNAP_TOL_S = PERIOD_S
# Fallback floor of a term's hourly scale = its board display resolution (currency terms: 1 won, deals: 1).
RESOLUTION = {"stars_rank": 0.01, "forum": 0.001, "posts": 1.0}
STARS_GUARD = 0.05  # a shop experiment block whose raw stars fall by >= this is flagged


def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "r3-icrl/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def _cache(sdir):
    d = Path(sdir) / "board"
    d.mkdir(parents=True, exist_ok=True)
    return sorted(
        (p for p in d.glob("*.json") if p.stem.isascii() and p.stem.isdigit()),
        key=lambda p: int(p.stem),
    )


def latest(sdir):
    for p in reversed(_cache(sdir)):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def refresh(sdir, now, fetch=http_get):
    """-> (board or None, status dict). Fetches only when PERIOD_S passed since the last try."""
    sp = Path(sdir) / "board_status.json"
    try:
        st = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {"last_try": None, "auth_fails": 0, "alarm": False}
    if st["last_try"] is None or now - st["last_try"] >= PERIOD_S:
        _cache(sdir)
        st["last_try"] = now
        try:
            code, body = fetch(URL)
            if code == 200:
                board = json.loads(body)
                (Path(sdir) / "board" / f"{int(now)}.json").write_text(
                    json.dumps(board), encoding="utf-8"
                )
                st.update(auth_fails=0, alarm=False, last_ok=now, last_code=200)
            else:
                st["last_code"] = code
                st["auth_fails"] = st["auth_fails"] + 1 if code in (401, 429) else 0
        except Exception as e:  # noqa: BLE001 - network / parse: keep the cache
            st["last_code"] = type(e).__name__
        st["alarm"] = st["auth_fails"] >= 3
        for p in _cache(sdir)[:-KEEP]:
            p.unlink()
        sp.write_text(json.dumps(st), encoding="utf-8")
    return (None if st["alarm"] else latest(sdir)), st


def pct(x, pop):
    """Mid-rank board percentile (= rlpo reward.pct for numeric values)."""
    if len(pop) < 2:
        return None
    below = sum(v < x for v in pop)
    tied = sum(v == x for v in pop)
    return (below + 0.5 * (tied - 1)) / (len(pop) - 1)


def _ours(rows, login):
    lg = (login or "").lower()
    for r in rows:
        if (
            not r.get("house")
            and ((r.get("owner") or {}).get("login") or "").lower() == lg
        ):
            return r
    return None


def _pop(board, group, term, me):
    rows = [
        r
        for r in board.get(group) or []
        if not r.get("house") and (r.get("deals") or 0) > 0 and r is not me
    ]
    return [float(r.get(term) or 0) for r in rows]


def slope(v, others):
    """Percentile per unit of the term at our value v (v <= 0: the participants' median is used)."""
    if not others:
        return 0.0
    if v <= 0:
        v = sorted(others)[len(others) // 2] or 1.0
    up, dn = v * 1.05, v * 0.95
    s = (pct(up, others + [up]) - pct(dn, others + [dn])) / (0.10 * v)
    if s <= 0:  # nobody within +-5 %: one rank over the gap to the next above
        above = [x for x in others if x > v]
        gap = (min(above) - v) if above else v
        s = 1 / len(others) / max(gap, 1e-9)
    return s


def weights(board, login):
    """-> {"mode": "board", "w": {term: weight}, "slope": {...}} for the reward terms."""
    w, sl = {}, {}
    for term, group in REWARD_TERMS.items():
        me = _ours(board.get(group) or [], login)
        v = float((me or {}).get(term) or 0)
        sl[term] = slope(v, _pop(board, group, term, me))
        w[term] = OFFICIAL[term] * sl[term]
    return {"mode": "board", "w": w, "slope": sl}


def fixed_weights(scale):
    """Fallback: official weights over our own 24 h per-hour level of each term (scale), so terms stay comparable.
    The floor is the term's resolution, so a missing or zero scale keeps the weight finite."""
    return {
        "mode": "fixed",
        "w": {
            t: OFFICIAL[t] / max(abs(scale.get(t) or 0), RESOLUTION.get(t, 1.0))
            for t in REWARD_TERMS
        },
    }


def _snap_values(p, login):
    """Our board values in snapshot file p: {stars, stars_rank, forum, posts} (None when absent)."""
    try:
        board = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out = {}
    for group in ("shops", "buyers"):
        me = _ours(board.get(group) or [], login) or {}
        for term in [t for t, g in BOARD_TERMS.items() if g == group] + (
            ["stars"] if group == "shops" else []
        ):
            v = me.get(term)
            out[term] = float(v) if isinstance(v, (int, float)) else None
    return out


def nearest(sdir, t, tol=SNAP_TOL_S):
    """-> (ts, path) of the cached snapshot nearest t within tol, or (None, None)."""
    best = None
    for p in _cache(sdir):
        d = abs(int(p.stem) - t)
        if d <= tol and (best is None or d < best[0]):
            best = (d, int(p.stem), p)
    return (best[1], best[2]) if best else (None, None)


def block_terms(sdir, login, t0, t1):
    """Board-measured terms over a block: {term_h: per-hour change between the snapshots nearest t0 and t1,
    missing: [terms with no pair of snapshots, contributing 0], snaps: [ts0, ts1], stars_drop: raw stars fall}."""
    out = {f"{t}_h": 0.0 for t in BOARD_TERMS}
    out.update(missing=sorted(BOARD_TERMS), snaps=None, stars_drop=None)
    if sdir is None:
        return out
    s0, p0 = nearest(sdir, t0)
    s1, p1 = nearest(sdir, t1)
    if s0 is None or s1 is None or s1 <= s0:
        return out
    v0, v1 = _snap_values(p0, login), _snap_values(p1, login)
    if v0 is None or v1 is None:
        return out
    h = (s1 - s0) / 3600
    out["snaps"], out["missing"] = [s0, s1], []
    for t in BOARD_TERMS:
        if v0[t] is None or v1[t] is None:
            out["missing"].append(t)
        else:
            out[f"{t}_h"] = (v1[t] - v0[t]) / h
    a = v0["stars"] if v0["stars"] is not None else v0["stars_rank"]
    b = v1["stars"] if v1["stars"] is not None else v1["stars_rank"]
    if a is not None and b is not None:
        out["stars_drop"] = round(a - b, 4)
    return out


def term_scale(sdir, login, now, hours=24):
    """Fallback scale of the board terms: mean |change| per hour between consecutive cached snapshots in the last
    `hours`. Terms without two usable snapshots are absent (fixed_weights then uses the resolution floor)."""
    snaps = [
        (int(p.stem), _snap_values(p, login))
        for p in _cache(sdir)
        if now - hours * 3600 <= int(p.stem) <= now
    ]
    snaps = [(s, v) for s, v in snaps if v is not None]
    out = {}
    for t in BOARD_TERMS:
        r = [
            abs(v1[t] - v0[t]) / ((s1 - s0) / 3600)
            for (s0, v0), (s1, v1) in zip(snaps, snaps[1:])
            if s1 > s0 and v0[t] is not None and v1[t] is not None
        ]
        if r:
            out[t] = sum(r) / len(r)
    return out


def ranked(rows):
    """The board's own population for its percentile columns: rows with a non-null 'all'. For shops that is the live
    participant shops plus the house shops, for buyers it equals deals > 0 (v1.26: reproduces the 'all' column of
    51/51 shops and 48/48 buyers on all 7 snapshots from 22:14 KST 2026-11-04 to 01:24 KST 2026-11-05)."""
    return [r for r in rows if r.get("all") is not None]


def _num(r, t):
    v = r.get(t)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _sig(x):
    return float(f"{x:.3g}")


def summary(board, login):
    """Our standing per official term, computed like the board's 'all' column (v1.26, 2026-11-05: the population was
    every non-house row before, which showed surplus at pct 0.831 against the official 0.574). Per term: ours, pct
    (mid-rank), rank (1 = top) of n, the gap to the next value above and below (3 significant digits) and per_rank,
    the score one rank step is worth (official weight / (n - 1))."""
    out = {}
    for group, W in (("shops", W_SHOP), ("buyers", W_BUYER)):
        me = _ours(board.get(group) or [], login)
        if me is None:
            continue
        rows = ranked(board.get(group) or [])
        if not any(r is me for r in rows):
            rows = rows + [me]
        n = len(rows)
        for term, wt in W.items():
            vals = [_num(r, term) for r in rows]
            v = _num(me, term)
            above = [x for x in vals if x > v]
            below = [x for x in vals if x < v]
            out[term] = {
                "ours": v,
                "pct": round(pct(v, vals) or 0, 3),
                "rank": len(above) + 1,
                "n": n,
                "gap_up": _sig(min(above) - v) if above else None,
                "gap_down": _sig(v - max(below)) if below else None,
                "per_rank": round(wt / max(n - 1, 1), 4),
            }
    return out


def _obj(p, k):
    v = p.get(k)
    return v if isinstance(v, dict) else {}


def standing(board, login):
    """Our player row (matched by our shop's name) -> {total, rank, n, gap_next, gap_top, shop_all, buyer_all}, or
    None when it is not on the board. Player rows that are not objects or have no numeric total are left out, so odd
    server data cannot cost the tick."""
    me = _ours(board.get("shops") or [], login)
    rows = board.get("players")
    pl = [
        p
        for p in (rows if isinstance(rows, list) else [])
        if isinstance(p, dict) and isinstance(p.get("total"), (int, float)) and not isinstance(p.get("total"), bool)
    ]
    mine = [p for p in pl if me is not None and _obj(p, "shop").get("name") == me.get("name")]
    if not mine:
        return None
    t = float(mine[0]["total"])
    above = sorted(float(p["total"]) for p in pl if p["total"] > t)
    return {
        "total": round(t, 3),
        "rank": len(above) + 1,
        "n": len(pl),
        "gap_next": round(above[0] - t, 3) if above else None,
        "gap_top": round(above[-1] - t, 3) if above else None,
        "shop_all": _obj(mine[0], "shop").get("all"),
        "buyer_all": _obj(mine[0], "buyer").get("all"),
    }
