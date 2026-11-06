"""The scoreboard model (11/4 night): the meta-harness's measure of where we stand and what moves us. Every
20 minutes, with the ledger, the market's board (/api/board) goes through the board's own formula, and
analysis/scoreboard.json says, for our shop and our buyer, what each part of the score gives now and what the
next few rows above us in it would take and give; and where the players would stand in PROJECT hours if every
shop and buyer kept its pace of the last RATE_WINDOW. The integrated analysis reads it in its briefing, and
linewatch reads our stars from it.

The formula, checked against the live board on 11/5 at 15:30 KST (53 shops, 49 buyers, every ALL within 0.0005):
    a part's percentile = (rows below + half of the other rows level with it) / (rows - 1)
    shop ALL  = 0.5 book + 0.3 revenue + 0.2 stars_rank, among shops with an ALL (a deal or a purchase)
    buyer ALL = 0.5 surplus + 0.3 forum + 0.1 deals + 0.1 posts, among buyers with a deal
    a player's total = its shop ALL + its buyer ALL
(stars_rank is the board's ranking figure. Book = cash + what the unsold units cost (/agent.md: "ALL uses book");
the board's profit column is cash. Until 11/5 15:40 this used profit: 0.154 off the board's ALL by then, as the
shops' stocks grew; on 11/4 22:50 the two still ranked the same. History lines before then carry profit and are
not read for paces: a line carries "v": 2.)
So passing one more row in a part is worth its weight / (rows - 1) whatever the part: what differs is the cost.
The class view (Patch 3.3, 11/5): the midterm ranks students among students. The board marks a student player
("student": true, with student_shop / student_buyer / student_total / student_pct); the same formula over the
students' own shops and buyers gives those within 0.0005 (42 shops, 41 buyers on 11/5 17:00). The scoreboard
works in that view whenever the board has it, and keeps our place among everyone beside it.

Each line of analysis/scoreboard_history.jsonl keeps every row's parts, for the paces.

The formula check (Kiro 2.0 stage 2, 11/6; three patches in three days changed the weights or the parts): every update
sets the formula in force against every row's ALL on the board (everyone's pools). Off by more than FIT_TOL on some
row, the weights are refit by least squares over the numeric fields half the rows carry (percentiles as above; weights
under half a FIT_STEP dropped, the rest rounded to FIT_STEP) and taken only if they give every row's ALL within
FIT_TOL: analysis/formula.json {"at", "shop", "buyer", "was", "residual"} then holds the weights in force (weights()),
the note says so, and a history line carries its parts ("k"). No fit within FIT_TOL: the weights stay and the note
and scoreboard.json's "formula" say how far off they are (a new part the board does not show, or a new rule).
"""
from collections import Counter
import json
import os
import statistics as st

import common
import forum

SHOP = {"book": 0.5, "revenue": 0.3, "stars_rank": 0.2}
BUYER = {"saved": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1}   # surplus until Patch 3.4 (11/6 06:00): saved, the
                                                                    # mean share of list kept per deal over deals + 100
UNIT = {"book": 1, "revenue": 1, "surplus": 1, "saved": 0.0001, "deals": 1, "posts": 1, "stars_rank": 0.01, "forum": 0.001}
LEVEL = ("stars_rank", "forum")      # averages: no pace is projected for them
AHEAD = 3                            # rows above us priced in each part
RATE_WINDOW = 3 * 3600               # the pace: the change since the history line nearest this long ago
RATE_MIN = 2 * 3600                  # ...at least this long ago
PROJECT = 6 * 3600
NEIGHBOURS = 5                       # the rows just above us whose median pace ours is set against
TAIL = 600_000                       # bytes of the history read back (about 3 h of lines at ~5 KB each, 20 min apart, with room)
VERSION = 3                          # history lines on book and saved (11/6 12:40; 2 = book with surplus, 11/5 15:40)
FIT_TOL = 0.005                      # the board's ALL against the formula's, the worst row
FIT_STEP = 0.05                      # refit weights are rounded to this
FIT_SKIP = {"all", "total", "student_total", "student_pct", "student_all", "pct", "rank", "place", "id"}


def folder():
    return common.MARKET_DIR / "analysis"


def pct(values: list, v) -> float:
    """The board's percentile of v among values (v's own row included in values)."""
    if len(values) < 2:
        return 1.0
    below = sum(1 for x in values if x < v)
    level = sum(1 for x in values if x == v) - 1
    return (below + 0.5 * level) / (len(values) - 1)


RAW = ":raw"             # a part the board takes as its value, not its percentile (Patch 3.5's forum rating, 0-1)


def base(k: str) -> str:
    return k.split(":")[0]


def num(row: dict, k: str):
    v = row.get(base(k))
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def score_of(k: str, col: list, v) -> float:
    """What one row's value of part k gives before its weight: the value itself for a raw part, else its percentile."""
    return v if k.endswith(RAW) else pct(col, v)


def pools(board: dict) -> tuple[list, list]:
    shops = [s for s in board.get("shops") or [] if isinstance(s, dict) and s.get("all") is not None and s.get("name")]
    buyers = [b for b in board.get("buyers") or [] if isinstance(b, dict) and num(b, "deals") > 0 and b.get("name")]
    return shops, buyers


def alls(rows: list, weights: dict) -> dict:
    """{name: ALL} for every row, by the formula."""
    cols = {k: [num(r, k) for r in rows] for k in weights}
    return {r["name"]: round(sum(w * score_of(k, cols[k], num(r, k)) for k, w in weights.items()), 4) for r in rows}


def parts(me: dict, rows: list, weights: dict) -> dict:
    """For each part: our value, percentile, what it gives, and the next AHEAD rows above us in it, each as
    {need: how much more of the part, gain: what our ALL gains}."""
    here, base_of = alls(rows, weights)[me["name"]], base
    out = {}
    for k, w in weights.items():
        v = num(me, k)
        col = [num(r, k) for r in rows]
        steps = []
        for t in sorted({x for x in col if x > v})[:AHEAD]:
            nv = round(t + UNIT.get(base_of(k), 1), 4)
            moved = [dict(r, **{base_of(k): nv}) if r["name"] == me["name"] else r for r in rows]
            steps.append({"need": round(nv - v, 4), "gain": round(alls(moved, weights)[me["name"]] - here, 4)})
        out[k] = {"value": v, "pct": round(pct(col, v), 4), "gives": round(w * score_of(k, col, v), 4), "next": steps}
    return out


def weights() -> tuple[dict, dict]:
    """(shop weights, buyer weights) in force: analysis/formula.json's after a refit, else SHOP and BUYER."""
    f = common.read_json(folder() / "formula.json", None)
    ok = lambda w: isinstance(w, dict) and w and all(isinstance(v, (int, float)) and v > 0 for v in w.values()) \
        and 0.5 <= sum(w.values()) <= 1.5
    if isinstance(f, dict) and ok(f.get("shop")) and ok(f.get("buyer")):
        return dict(f["shop"]), dict(f["buyer"])
    return dict(SHOP), dict(BUYER)


def keys_of(sw: dict, bw: dict) -> list:
    return [list(sw), list(bw)]


def residual(rows: list, w: dict) -> float | None:
    """The worst |board ALL - the formula's ALL| over the rows that carry the board's ALL (None under 5 of them)."""
    rows = [r for r in rows if isinstance(r.get("all"), (int, float)) and not isinstance(r.get("all"), bool)]
    if len(rows) < 5:
        return None
    ours = alls(rows, w)
    return round(max(abs(r["all"] - ours[r["name"]]) for r in rows), 4)


def fields(rows: list) -> list:
    """The numeric fields half the rows carry (a missing one counts 0, as on the board), but its totals and ranks."""
    seen = Counter(k for r in rows for k, v in r.items()
                   if k not in FIT_SKIP and isinstance(v, (int, float)) and not isinstance(v, bool))
    return sorted(k for k, n in seen.items() if n >= 0.5 * len(rows))


def solve(a: list, y: list) -> list | None:
    """Least squares by the normal equations (a handful of columns), or None when they are not independent."""
    n = len(a[0])
    m = [[sum(r[i] * r[j] for r in a) for j in range(n)] + [sum(r[i] * v for r, v in zip(a, y))] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-9:
            return None
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * z for x, z in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def refit(rows: list) -> dict | None:
    """Weights over the rows' fields that give every row's ALL within FIT_TOL, or None."""
    rows = [r for r in rows if isinstance(r.get("all"), (int, float)) and not isinstance(r.get("all"), bool)]
    cols, live = {}, []
    for k in fields(rows):
        col = [num(r, k) for r in rows]
        p = [pct(col, num(r, k)) for r in rows]
        if len(set(p)) > 1 and p not in cols.values():         # a field level for every row, or a copy of another: no part
            cols[k] = p
            live.append(k)
        if len(set(col)) > 1 and all(0 <= x <= 1 for x in col):  # 0-1 (a rating): its value may count as it is (3.5)
            cols[k + RAW] = [float(x) for x in col]
            live.append(k + RAW)
    if not live or len(rows) < 2 * len(live) + 5:
        return None
    y = [r["all"] for r in rows]
    while live:
        w = solve([[cols[k][i] for k in live] for i in range(len(rows))], y)
        if w is None:
            return None
        small = [k for k, x in zip(live, w) if x < FIT_STEP / 2]
        if not small:
            break
        live = [k for k in live if k not in small]
    if not live:
        return None
    fit = {k: round(round(x / FIT_STEP) * FIT_STEP, 4) for k, x in zip(live, w)}
    fit = {k: v for k, v in fit.items() if v > 0}
    r = residual(rows, fit) if fit else None
    return fit if r is not None and r <= FIT_TOL else None


def swapped(rows: list, w: dict) -> dict | None:
    """The weights in force with one part turned from its percentile to its value or back, when that alone gives
    every row's ALL within FIT_TOL (the best such), else None. Tried before a refit (11/6 18:57): Patch 3.5 turned the
    forum part raw, and the refit over the board's many co-moving fields (deals, orders, paid, served) split the
    weights between them and missed, while this one swap fit within 0.0005."""
    best = None
    for k in w:
        alt = base(k) if k.endswith(RAW) else k + RAW
        cand = {(alt if j == k else j): v for j, v in w.items()}
        r = residual(rows, cand)
        if r is not None and r <= FIT_TOL and (best is None or r < best[0]):
            best = (r, cand)
    return best[1] if best else None


def check_formula(now: float, shops: list, buyers: list) -> tuple[dict, dict, dict]:
    """(shop weights, buyer weights, {"shop": {...}, "buyer": {...}, "changed"}): the weights in force checked against
    the board, refit and saved when they are off and a fit is found."""
    sw, bw = weights()
    out, new = {"changed": False}, {}
    for side_, rows, w in (("shop", shops, sw), ("buyer", buyers, bw)):
        r = residual(rows, w)
        row = {"residual": r, "ok": r is None or r <= FIT_TOL}
        if not row["ok"]:
            fit = swapped(rows, w) or refit(rows)
            row["refit"] = fit
            if fit and fit != w:
                new[side_] = fit
        out[side_] = row
    if new:
        f = {"at": int(now), "shop": new.get("shop", sw), "buyer": new.get("buyer", bw), "was": {"shop": sw, "buyer": bw},
             "residual": {s: out[s]["residual"] for s in ("shop", "buyer")}}
        common.write_json(folder() / "formula.json", f)
        sw, bw, out["changed"] = f["shop"], f["buyer"], True
    return sw, bw, out


def compact(shops: list, buyers: list, sw: dict | None = None, bw: dict | None = None) -> dict:
    sw, bw = sw or SHOP, bw or BUYER
    out = {"shops": {s["name"]: [num(s, k) for k in sw] for s in shops},
           "buyers": {b["name"]: [num(b, k) for k in bw] for b in buyers}}
    if keys_of(sw, bw) != keys_of(SHOP, BUYER):
        out["k"] = keys_of(sw, bw)
    return out


def history_since(now: float, keys: list | None = None) -> dict | None:
    """The history line nearest RATE_WINDOW ago and at least RATE_MIN old, read from the file's tail (of the same
    parts as `keys`, the weights in force when not given)."""
    keys = keys or keys_of(*weights())
    path = folder() / "scoreboard_history.jsonl"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - TAIL))
        raw = f.read().splitlines()
    rows = []
    for line in raw:
        try:
            rows.append(json.loads(line))
        except ValueError:                                  # the first line of the tail is usually cut
            continue
    old = [r for r in rows if isinstance(r, dict) and r.get("v") == VERSION and now - (r.get("at") or 0) >= RATE_MIN
           and (r.get("k") or keys_of(SHOP, BUYER)) == keys]
    return min(old, key=lambda r: abs(now - r["at"] - RATE_WINDOW)) if old else None


def project(rows: list, weights: dict, then: dict, hours: float) -> tuple[list, dict]:
    """(the rows PROJECT ahead at each row's pace since `then`, the paces per hour {name: {part: pace}}); a row
    not in `then` keeps its values."""
    keys = list(weights)
    out, paces = [], {}
    for r in rows:
        before = then.get(r["name"])
        if not before or hours <= 0:
            out.append(r)
            continue
        pace = {k: (num(r, k) - before[i]) / hours for i, k in enumerate(keys) if base(k) not in LEVEL}
        paces[r["name"]] = pace
        out.append(dict(r, **{base(k): num(r, k) + pace[k] * PROJECT / 3600 for k in pace}))
    return out, paces


def place(totals: dict, name) -> int | None:
    return 1 + sum(1 for v in totals.values() if v > totals[name]) if name in totals else None


def side(x) -> dict:
    """A player's shop or buyer as the board gives it: a row, or only its name."""
    return x if isinstance(x, dict) else {"name": x} if isinstance(x, str) else {}


def view(board: dict) -> tuple[list, list, list, str, str]:
    """(shops, buyers, players, the total's key, "class" or "everyone"): the students' own shops and buyers and the
    students when the board marks any, else every row."""
    shops, buyers = pools(board)
    players = [p for p in board.get("players") or [] if isinstance(p, dict) and p.get("player")]
    students = [p for p in players if p.get("student")]
    if not students:
        return shops, buyers, players, "total", "everyone"
    sn = {side(p.get("shop")).get("name") for p in students}
    bn = {side(p.get("buyer")).get("name") for p in students}
    return ([s for s in shops if s["name"] in sn], [b for b in buyers if b["name"] in bn], students,
            "student_total", "class")


def update(now: float, board: dict) -> str:
    """scoreboard.json from the board (the class view when there is one); one history line (every row). Returns the
    line for the analysis log."""
    every_shops, every_buyers = pools(board)
    sw, bw, formula = check_formula(now, every_shops, every_buyers)
    everyone = [p for p in board.get("players") or [] if isinstance(p, dict) and p.get("player")]
    shops, buyers, players, key, label = view(board)
    me = next((p for p in players if forum.own(p.get("player"))), None)
    my_shop = next((s for s in shops if forum.own(s["name"])), None)
    my_buyer = next((b for b in buyers if forum.own(b["name"])), None)
    folder().mkdir(parents=True, exist_ok=True)
    then = history_since(now, keys_of(sw, bw))
    with open(folder() / "scoreboard_history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": int(now), "v": VERSION, **compact(every_shops, every_buyers, sw, bw)}, ensure_ascii=False,
                           separators=(",", ":")) + "\n")
    if not me or not players:
        return "scoreboard: our player is not on the board"
    totals = {p["player"]: num(p, key) for p in players}
    all_totals = {p["player"]: num(p, "total") for p in everyone}
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    mine = totals[me["player"]]
    above = [t for _, t in ranked if t > mine]
    sb = {"at": int(now), "view": label, "players": len(players), "place": place(totals, me["player"]), "total": mine,
          "everyone": {"place": place(all_totals, me["player"]), "total": all_totals.get(me["player"]), "players": len(everyone)},
          "top": [[n, t] for n, t in ranked[:3]],
          "gap_to_3rd": round(max(0.0, ranked[min(2, len(ranked) - 1)][1] - mine), 4),
          "gap_to_next": round(min(above) - mine, 4) if above else 0.0,
          "shop": {"all": alls(shops, sw).get(my_shop["name"]), "rows": len(shops), "parts": parts(my_shop, shops, sw)} if my_shop else None,
          "buyer": {"all": alls(buyers, bw).get(my_buyer["name"]), "rows": len(buyers), "parts": parts(my_buyer, buyers, bw)} if my_buyer else None,
          "formula": dict(formula, weights={"shop": sw, "buyer": bw})}
    note = (f"scoreboard ({label}): {sb['place']} of {len(players)}, {mine:.3f}, 3rd {ranked[min(2, len(ranked) - 1)][1]:.3f}"
            + (f"; everyone {sb['everyone']['place']} of {len(everyone)}" if label == "class" else ""))
    if formula["changed"]:
        note += f"; FORMULA CHANGED: refit to shop {sw}, buyer {bw} (formula.json)"
    elif not (formula["shop"]["ok"] and formula["buyer"]["ok"]):
        note += (f"; FORMULA OFF: the board's ALL differs by up to shop {formula['shop']['residual']}, buyer "
                 f"{formula['buyer']['residual']} and no refit fits")
    if then:
        hours = (now - then["at"]) / 3600
        p_shops, s_pace = project(shops, sw, then.get("shops") or {}, hours)
        p_buyers, b_pace = project(buyers, bw, then.get("buyers") or {}, hours)
        s_all, b_all = alls(p_shops, sw), alls(p_buyers, bw)
        future = {}
        for p in players:
            sh, by = side(p.get("shop")), side(p.get("buyer"))
            future[p["player"]] = (s_all.get(sh.get("name"), num(sh, "all")) + b_all.get(by.get("name"), num(by, "all")))
        f_ranked = sorted(future.values(), reverse=True)
        sb["projection"] = {"hours": PROJECT // 3600, "pace_hours": round(hours, 1), "place": place(future, me["player"]),
                            "total": round(future[me["player"]], 4), "third": round(f_ranked[min(2, len(f_ranked) - 1)], 4)}
        sb["pace"] = {}
        per_h = lambda x: round(x) if abs(x) >= 10 else round(x, 2)          # posts and deals move a few an hour
        for part, rows, paces, mine_row in (("shop", shops, s_pace, my_shop), ("buyer", buyers, b_pace, my_buyer)):
            if not mine_row or mine_row["name"] not in paces:
                continue
            for k, ours in paces[mine_row["name"]].items():
                near = sorted((r for r in rows if num(r, k) > num(mine_row, k) and r["name"] in paces), key=lambda r: num(r, k))[:NEIGHBOURS]
                others = [paces[r["name"]][k] for r in near]
                sb["pace"][f"{part}.{k}"] = {"ours_per_h": per_h(ours), "above_median_per_h": per_h(st.median(others)) if others else None}
        note += f"; in {PROJECT // 3600} h at this pace: {sb['projection']['place']}"
    common.write_json(folder() / "scoreboard.json", sb)
    return note


def read(now: float, fresh: float = 3600) -> dict | None:
    """scoreboard.json when it is younger than `fresh`, else None."""
    sb = common.read_json(folder() / "scoreboard.json", None)
    return sb if isinstance(sb, dict) and now - (sb.get("at") or 0) < fresh else None


def stars(now: float) -> float | None:
    """Our shop's stars_rank on the board, from a fresh scoreboard; None unless it is a figure from 1 to 5 (a
    board without the field gives 0 in the formula, which must not read as bad stars)."""
    sb = read(now)
    try:
        v = sb["shop"]["parts"]["stars_rank"]["value"]
    except (TypeError, KeyError):
        return None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and 1 <= v <= 5 else None


def brief(sb: dict | None, side: str | None = None) -> dict | None:
    """The scoreboard as a briefing reads it: place, gaps, the projection and pace, and each part's value,
    percentile and next step (all parts, or one side's)."""
    if not sb:
        return None
    out = {k: sb.get(k) for k in ("view", "place", "players", "total", "gap_to_3rd", "gap_to_next", "projection", "everyone")}
    for s in ("shop", "buyer"):
        if side in (None, s) and sb.get(s):
            out[s] = {"all": sb[s]["all"], **{k: {"value": v["value"], "pct": v["pct"], "next": v["next"][:2]}
                                              for k, v in sb[s]["parts"].items()}}
    if sb.get("pace"):
        out["pace_per_h_ours_vs_rows_above"] = {k: [v["ours_per_h"], v["above_median_per_h"]] for k, v in sb["pace"].items()
                                                if side is None or k.startswith(side)}
    return out
