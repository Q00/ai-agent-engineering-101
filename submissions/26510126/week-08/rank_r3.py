"""Where we stand on the midterm's rank (week-08 rubric part 3), with the instructor's q00 as a floor.

    python rank_r3.py        # our Players rank and percentile, the leader's gap, q00's rank, and the part to push

Rubric part 3 (30%): our percentile on the board's Players table (our shop ALL + our buyer ALL) at the time Round 3
ends. That table is the truth, so this reads it whenever the board fills it. Until then it recomputes the same
numbers from the board's shop and buyer tables (agent.md 11/3 20:29), only to name which part to push:
  shop ALL  = 0.5 profit + 0.3 revenue + 0.2 stars             (percentiles among shops with a deal or a purchase)
  buyer ALL = 0.5 surplus + 0.3 votes + 0.1 deals + 0.1 posts  (percentiles among buyers with a deal)
The target (the user, 11/3) is first place. Staying above q00 is the floor, not the goal. Both are reached honestly:
true grade claims, the certificate shown, no votes against others, no self-dealing, no deliberate bankruptcy.
"""
import json

US, TEACHER = "thisjonghoon", "q00"
SHOP_W = {"profit": 0.5, "revenue": 0.3, "stars": 0.2}
BUYER_W = {"surplus": 0.5, "votes": 0.3, "deals": 0.1, "posts": 0.1}
ALIASES = {"surplus": ("surplus", "saved"), "votes": ("votes", "forum", "score"), "posts": ("posts",)}


def value(row, key):
    for k in ALIASES.get(key, (key,)):
        if isinstance(row.get(k), (int, float)) and not isinstance(row.get(k), bool):
            return row[k]
    return None


def pct(x, xs):
    xs = [v for v in xs if v is not None]
    return sum(v <= x for v in xs) / len(xs) if xs and x is not None else None


def table_all(rows, weights, active):
    """name -> (ALL, parts) for every row, parts as percentiles; a part the table lacks is left out and the weights
    of the rest are scaled up (so Round 2's tables, without votes or posts, still give a comparable number)."""
    rows = [r for r in rows if active(r)]
    have = [k for k in weights if any(value(r, k) is not None for r in rows)]
    total = sum(weights[k] for k in have) or 1
    out = {}
    for r in rows:
        parts = {k: pct(value(r, k), [value(x, k) for x in rows]) for k in have}
        out[r.get("name")] = (sum(weights[k] * (parts[k] or 0) for k in have) / total, parts)
    return out


def owner(r):
    return ((r.get("owner") or {}).get("login") or r.get("name") or "").lower()


def board_players(board):
    """The board's own Players table, as [(owner, total)] best first, or None while it is empty. Field names are not
    documented yet, so a total is the first number found under the usual names, and the board's order is kept."""
    rows = board.get("players") or []
    if not rows:
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        who = ((r.get("owner") or {}).get("login") if isinstance(r.get("owner"), dict) else None) or r.get("login") or r.get("name") or ""
        total = next((r[k] for k in ("all", "ALL", "total", "score", "players") if isinstance(r.get(k), (int, float))), None)
        out.append((who.lower(), total))
    if all(t is not None for _, t in out):
        out.sort(key=lambda x: -x[1])
    return out


def standing(board):
    shops = table_all(board.get("shops") or [], SHOP_W, lambda r: (r.get("deals") or 0) > 0 or (r.get("purchases") or 0) > 0)
    buyers = table_all(board.get("buyers") or [], BUYER_W, lambda r: (r.get("deals") or 0) > 0)
    players = {}
    for kind, t in (("shop", shops), ("buyer", buyers)):
        rows = {r.get("name"): r for r in board.get(kind + "s") or []}
        for name, (score, _) in t.items():
            r = rows.get(name) or {}
            if r.get("house"):
                continue
            players.setdefault(owner(r), {"shop": 0.0, "buyer": 0.0})[kind] = score
    ranked = sorted(players.items(), key=lambda kv: -(kv[1]["shop"] + kv[1]["buyer"]))
    rank = {o: i + 1 for i, (o, _) in enumerate(ranked)}
    us, teacher = players.get(US, {}), players.get(TEACHER, {})
    weakest = None
    for kind, t, w in (("shop", shops, SHOP_W), ("buyer", buyers, BUYER_W)):
        parts = (t.get(US) or (None, {}))[1]
        for k, p in parts.items():
            if p is not None:
                gain = w[k] * (1 - p)             # what moving this part to the top would add
                if weakest is None or gain > weakest[2]:
                    weakest = (kind, k, round(gain, 3))
    our_all = round(us.get("shop", 0) + us.get("buyer", 0), 3)
    top_all = round(ranked[0][1]["shop"] + ranked[0][1]["buyer"], 3) if ranked else None
    source, n = "recomputed", len(players)
    official = board_players(board)
    if official:                                         # the board's table decides the grade
        source, n = "board", len(official)
        order = [o for o, _ in official]
        rank = {o: i + 1 for i, o in enumerate(order)}
        tot = dict(official)
        our_all = tot.get(US, our_all)
        top_all = official[0][1]
        ranked = [(o, None) for o in order]
    r = rank.get(US)
    return {"source": source, "players": n, "our_rank": r, "leader": ranked[0][0] if ranked else None,
            "our_pct": round((n - r) / (n - 1), 3) if r and n > 1 else None,
            "teacher_rank": rank.get(TEACHER), "our_all": our_all, "leader_all": top_all,
            "teacher_all": round(teacher.get("shop", 0) + teacher.get("buyer", 0), 3),
            "our_shop": {k: round(v, 3) for k, v in ((shops.get(US) or (0, {}))[1]).items() if v is not None},
            "our_buyer": {k: round(v, 3) for k, v in ((buyers.get(US) or (0, {}))[1]).items() if v is not None},
            "biggest_gain": weakest}


def line(s):
    if not s:
        return "rank unavailable"
    if s["our_rank"] == 1:
        lead = "we lead"
    elif isinstance(s.get("leader_all"), (int, float)) and isinstance(s.get("our_all"), (int, float)):
        lead = f"{s['leader']} leads by {s['leader_all'] - s['our_all']:.3f}"
    else:
        lead = f"{s['leader']} leads"
    floor = "above" if (s["teacher_rank"] or 10**6) > (s["our_rank"] or 10**6) else "BELOW"
    return (f"Players #{s['our_rank']}/{s['players']} pct {s['our_pct']} ({s['source']}), {lead}; "
            f"q00 #{s['teacher_rank']} (floor: we are {floor}); most to gain: {s['biggest_gain']}")


def fetch():
    import httpx
    return standing(httpx.get("https://market.ourolabs.ai/api/board", timeout=15).json())


if __name__ == "__main__":
    s = fetch()
    print(line(s))
    print(json.dumps(s, ensure_ascii=False))
