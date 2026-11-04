"""Board reader and reward weights (DESIGN.md sec 5, 10).

GET /api/board at most every 1800 s (read-only, no query, no identity in the request), cached under
<state>/board/<ts>.json (last 48 kept). On failure the last cache is used; after 3 consecutive 401/429 the weights
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
KEEP = 48
W_SHOP = {"profit": 0.5, "revenue": 0.3, "stars_rank": 0.2}
W_BUYER = {"surplus": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1}
REWARD_TERMS = {
    "profit": "shops",
    "revenue": "shops",
    "surplus": "buyers",
    "deals": "buyers",
}
OFFICIAL = {**W_SHOP, **W_BUYER}


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
    return sorted(d.glob("*.json"), key=lambda p: int(p.stem))


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
    """Fallback: official weights over our own 24 h per-hour level of each term (scale), so terms stay comparable."""
    return {
        "mode": "fixed",
        "w": {t: OFFICIAL[t] / max(abs(scale.get(t) or 0), 1.0) for t in REWARD_TERMS},
    }


def summary(board, login):
    out = {}
    for group, W in (("shops", W_SHOP), ("buyers", W_BUYER)):
        me = _ours(board.get(group) or [], login)
        if me is None:
            continue
        for term in W:
            vals = sorted(
                float(r.get(term) or 0)
                for r in board.get(group) or []
                if not r.get("house")
            )
            v = float(me.get(term) or 0)
            above = [x for x in vals if x > v]
            below = [x for x in vals if x < v]
            out[term] = {
                "ours": v,
                "pct": round(pct(v, vals) or 0, 3),
                "gap_up": round(min(above) - v, 2) if above else None,
                "gap_down": round(v - max(below), 2) if below else None,
            }
    return out
