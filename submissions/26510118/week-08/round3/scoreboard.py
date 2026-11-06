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
"""
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


def folder():
    return common.MARKET_DIR / "analysis"


def pct(values: list, v) -> float:
    """The board's percentile of v among values (v's own row included in values)."""
    if len(values) < 2:
        return 1.0
    below = sum(1 for x in values if x < v)
    level = sum(1 for x in values if x == v) - 1
    return (below + 0.5 * level) / (len(values) - 1)


def num(row: dict, k: str):
    v = row.get(k)
    return v if isinstance(v, (int, float)) else 0


def pools(board: dict) -> tuple[list, list]:
    shops = [s for s in board.get("shops") or [] if isinstance(s, dict) and s.get("all") is not None and s.get("name")]
    buyers = [b for b in board.get("buyers") or [] if isinstance(b, dict) and num(b, "deals") > 0 and b.get("name")]
    return shops, buyers


def alls(rows: list, weights: dict) -> dict:
    """{name: ALL} for every row, by the formula."""
    cols = {k: [num(r, k) for r in rows] for k in weights}
    return {r["name"]: round(sum(w * pct(cols[k], num(r, k)) for k, w in weights.items()), 4) for r in rows}


def parts(me: dict, rows: list, weights: dict) -> dict:
    """For each part: our value, percentile, what it gives, and the next AHEAD rows above us in it, each as
    {need: how much more of the part, gain: what our ALL gains}."""
    base = alls(rows, weights)[me["name"]]
    out = {}
    for k, w in weights.items():
        v = num(me, k)
        col = [num(r, k) for r in rows]
        steps = []
        for t in sorted({x for x in col if x > v})[:AHEAD]:
            nv = round(t + UNIT[k], 4)
            moved = [dict(r, **{k: nv}) if r["name"] == me["name"] else r for r in rows]
            steps.append({"need": round(nv - v, 4), "gain": round(alls(moved, weights)[me["name"]] - base, 4)})
        out[k] = {"value": v, "pct": round(pct(col, v), 4), "gives": round(w * pct(col, v), 4), "next": steps}
    return out


def compact(shops: list, buyers: list) -> dict:
    return {"shops": {s["name"]: [num(s, k) for k in SHOP] for s in shops},
            "buyers": {b["name"]: [num(b, k) for k in BUYER] for b in buyers}}


def history_since(now: float) -> dict | None:
    """The history line nearest RATE_WINDOW ago and at least RATE_MIN old, read from the file's tail."""
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
    old = [r for r in rows if isinstance(r, dict) and r.get("v") == VERSION and now - (r.get("at") or 0) >= RATE_MIN]
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
        pace = {k: (num(r, k) - before[i]) / hours for i, k in enumerate(keys) if k not in LEVEL}
        paces[r["name"]] = pace
        out.append(dict(r, **{k: num(r, k) + pace[k] * PROJECT / 3600 for k in pace}))
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
    everyone = [p for p in board.get("players") or [] if isinstance(p, dict) and p.get("player")]
    shops, buyers, players, key, label = view(board)
    me = next((p for p in players if forum.own(p.get("player"))), None)
    my_shop = next((s for s in shops if forum.own(s["name"])), None)
    my_buyer = next((b for b in buyers if forum.own(b["name"])), None)
    folder().mkdir(parents=True, exist_ok=True)
    then = history_since(now)
    with open(folder() / "scoreboard_history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": int(now), "v": VERSION, **compact(every_shops, every_buyers)}, ensure_ascii=False,
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
          "shop": {"all": alls(shops, SHOP).get(my_shop["name"]), "rows": len(shops), "parts": parts(my_shop, shops, SHOP)} if my_shop else None,
          "buyer": {"all": alls(buyers, BUYER).get(my_buyer["name"]), "rows": len(buyers), "parts": parts(my_buyer, buyers, BUYER)} if my_buyer else None}
    note = (f"scoreboard ({label}): {sb['place']} of {len(players)}, {mine:.3f}, 3rd {ranked[min(2, len(ranked) - 1)][1]:.3f}"
            + (f"; everyone {sb['everyone']['place']} of {len(everyone)}" if label == "class" else ""))
    if then:
        hours = (now - then["at"]) / 3600
        p_shops, s_pace = project(shops, SHOP, then.get("shops") or {}, hours)
        p_buyers, b_pace = project(buyers, BUYER, then.get("buyers") or {}, hours)
        s_all, b_all = alls(p_shops, SHOP), alls(p_buyers, BUYER)
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
