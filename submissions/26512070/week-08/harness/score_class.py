"""score.class (design R2, 16-2): where we stand among the students only (the board's Class view), which is what the
midterm grades (design 0).

    py -m harness.score_class            # from the newest kept board snapshot (outer/board_history.py)

Who is a student: the board's own flag (players[].student), the market's definition. The course roster (roster/*.md,
GitHub ids) is only a cross-check: on 11/6 00:4x the board marked 42 students, the roster held 46 ids, 2 students on
the board were not in the roster and 6 roster ids were not on the board.
Parts: each part of shop ALL and buyer ALL is a percentile among the students' ranked shops and buyers (core/score.py
for the rule: ranked rows only, missing 0, ties half). Checked against the board's student_shop / student_buyer.
"""
import json
import re
import sys

from core import paths, score
from harness import out_dir
from outer.position import snapshots

ME = "gom31"


def roster_ids():
    """GitHub ids in the course roster (lower case), or an empty set when the roster is not next to us."""
    d = paths.root().parents[2] / "roster"
    ids = set()
    for f in d.glob("*.md") if d.exists() else []:
        m = re.search(r"github:\s*(\S+)", f.read_text(encoding="utf-8"))
        if m:
            ids.add(m.group(1).lower())
    return ids


def class_view(board, me=ME):
    players = [p for p in board.get("players") or [] if p.get("student") and isinstance(p.get("student_total"),
                                                                                        (int, float))]
    mine = next((p for p in players if (p.get("login") or p.get("player")) == me), None)
    if mine is None:
        return None
    players.sort(key=lambda p: -p["student_total"])
    above = [p for p in players if p["student_total"] > mine["student_total"]]
    nxt = min(above, key=lambda p: p["student_total"]) if above else None
    third = players[2]["student_total"] if len(players) >= 3 else None
    out = {"ts": board.get("ts"), "students": len(players), "rank": len(above) + 1,
           "total": mine["student_total"], "percentile": mine.get("student_pct"),
           # the board's Class view: (students - rank) / (students - 1), 30/41 = 0.732 on 11/6 00:4x
           "percentile_calc": round((len(players) - len(above) - 1) / max(1, len(players) - 1), 3),
           "next": nxt and (nxt.get("login") or nxt.get("player")),
           "gap_next": nxt and round(nxt["student_total"] - mine["student_total"], 4),
           "gap_top3": third and round(third - mine["student_total"], 4),
           "board": {"shop": mine.get("student_shop"), "buyer": mine.get("student_buyer")}}
    shop_names = {(p.get("shop") or {}).get("name") for p in players}
    buyer_names = {(p.get("buyer") or {}).get("name") for p in players}
    for side_, rows, names, weights in (("shop", board.get("shops"), shop_names, score.SHOP),
                                        ("buyer", board.get("buyers"), buyer_names, score.BUYER)):
        ranked = (score.ranked_shops if side_ == "shop" else score.ranked_buyers)(
            [r for r in rows or [] if r.get("name") in names])
        me_row = next((r for r in ranked if r.get("name") == (mine.get(side_) or {}).get("name")), None)
        s = score.side(ranked, me_row, weights)
        if s:
            for f, prt in s["parts"].items():                 # who is just above in this part, by name
                v = prt["value"]
                up = [r for r in ranked if score._val(r, f) > v]
                prt["next"] = min(up, key=lambda r: score._val(r, f)).get("name") if up else None
        out[side_] = s
    ro = roster_ids()
    if ro:
        on_board = {(p.get("login") or "").lower() for p in players}
        out["roster_check"] = {"roster": len(ro), "board_not_in_roster": sorted(on_board - ro),
                               "roster_not_on_board": len(ro - on_board)}
    return out


def latest(now=None):
    import time
    snaps = snapshots((now or time.time()) - 3 * 3600)
    return class_view(snaps[-1]) if snaps else None


def write(view):
    p = out_dir() / "score_class.json"
    p.write_text(json.dumps(view, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


if __name__ == "__main__":
    v = latest()
    if "--write" in sys.argv and v:
        write(v)
    print(json.dumps(v, ensure_ascii=False, indent=1))
