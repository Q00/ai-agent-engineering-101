"""Our board terms over time, for the team: each specialist sees the terms it is scored on, now and 1/6/24 h ago.

Why (11/9 16:3x): the community manager tuned post kinds on mean net votes per post, where we were above the market,
while the board's forum rating (one stance per account per author, raw 0-1) had us #13 of 40 students and nobody saw
it; sales tuned ask_frac, which set 11 of 90 goods asks. The board snapshot (logs/board/board-round3.json) is
overwritten every 10 minutes, so no one could see whether a change moved a term. This keeps one compact row per meta
cycle (append-only) and shows each area its own terms with their trend.

  row(board)        our terms and the reference points (#1 and #10 of the students, the forum leader)
  append(board)     adds row(board) to HISTORY
  text(area)        the area's terms now and their change over 1, 6 and 24 h
"""
import json
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HISTORY = HERE / "logs" / "board_terms_r3.jsonl"
BOARD = HERE / "logs" / "board" / "board-round3.json"
US = "thisjonghoon"
AREAS = {
    "shop": (("student_rank", "our rank among the students (lower is better)"), ("student_total", "our Players total"),
             ("gap_10", "points to #10 of the students"), ("shop_all", "shop ALL"),
             ("book", "shop book, won (0.5 of shop ALL, a percentile)"), ("revenue", "shop revenue, won (0.3)"),
             ("stars_rank", "shop stars_rank (0.2)")),
    "buyer": (("student_rank", "our rank among the students"), ("buyer_all", "buyer ALL"),
              ("saved", "buyer saved (0.5 of buyer ALL, a percentile)"), ("deals", "buyer deals (0.1)"),
              ("forum", "forum rating (0.3, counts as itself)"), ("posts", "buyer posts (0.1)")),
    "forum": (("forum", "our forum rating (0.3 of buyer ALL, raw 0-1: one stance per voter account per author; a "
                        "dislike stance lowers it)"), ("forum_rank", "our forum rank among the students"),
              ("forum_lead", "the leading student's forum rating"), ("posts", "our buyer posts (0.1)")),
}


def row(board, now=None):
    st = sorted([r for r in board.get("players") or [] if r.get("student")], key=lambda r: -(r.get("student_total") or 0))
    me = next((r for r in st if r.get("player") == US), {})
    shop = next((r for r in board.get("shops") or [] if r.get("name") == US), {})
    buyer = next((r for r in board.get("buyers") or [] if r.get("name") == US), {})
    names = {(r.get("buyer") or {}).get("name") for r in st}
    fr = sorted([r for r in board.get("buyers") or [] if r.get("name") in names and isinstance(r.get("forum"), (int, float))],
                key=lambda r: -r["forum"])
    rank = next((i + 1 for i, r in enumerate(st) if r.get("player") == US), None)
    return {"t": round(now or time.time()), "student_rank": rank, "students": len(st),
            "student_total": me.get("student_total"),
            "gap_10": round(st[9]["student_total"] - (me.get("student_total") or 0), 3) if len(st) >= 10 else None,
            "rank10_total": st[9].get("student_total") if len(st) >= 10 else None,
            "top_total": st[0].get("student_total") if st else None,
            "shop_all": (shop or {}).get("all"), "book": shop.get("book"), "revenue": shop.get("revenue"),
            "stars_rank": shop.get("stars_rank"), "buyer_all": buyer.get("all"), "saved": buyer.get("saved"),
            "deals": buyer.get("deals"), "forum": buyer.get("forum"), "posts": buyer.get("posts"),
            "forum_rank": next((i + 1 for i, r in enumerate(fr) if r.get("name") == US), None),
            "forum_lead": fr[0]["forum"] if fr else None, "forum_leader": fr[0].get("name") if fr else None,
            "forum_students": len(fr)}


def append(board=None, now=None):
    if board is None:
        try:
            board = json.loads(BOARD.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    r = row(board, now)
    if r["student_rank"] is None:
        return None
    HISTORY.parent.mkdir(exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return r


LIVE = re.compile(r"\{live:([a-z_0-9]+)\}")


def render(text, current=None):
    """Fills {live:key} in a text the team reads (the standing orders) with the board's value now (11/9: the memory
    study warns against storing state that keeps changing; "our forum rating leads the market" sat in the orders
    while we were 13th). An unknown key or no board shows "?" rather than a stale number."""
    cur = current if current is not None else (append_free_row() or {})

    def val(m):
        v = cur.get(m.group(1))
        return "?" if v is None else f"{v:,}" if isinstance(v, int) and v >= 10_000 else str(v)
    return LIVE.sub(val, text)


def append_free_row():
    """row() of the saved board without appending to the history."""
    try:
        r = row(json.loads(BOARD.read_text(encoding="utf-8")))
        age = time.time() - BOARD.stat().st_mtime        # the board file is saved every 10 minutes
        r["as_of"] = time.strftime("%m/%d %H:%M", time.localtime(BOARD.stat().st_mtime)) + (" STALE" if age > 1800 else "")
        return r
    except (OSError, ValueError, KeyError, IndexError):
        return None


def rows():
    try:
        return [json.loads(l) for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def text(area, history=None, now=None):
    h = history if history is not None else rows()
    if not h:
        return ""
    cur, now = h[-1], now or h[-1]["t"]

    def ago(hours):
        old = [r for r in h if r["t"] <= now - hours * 3600]
        return old[-1] if old else None

    then = {x: ago(x) for x in (1, 6, 24)}
    out = [f"Your board terms (the market's board, {time.strftime('%m/%d %H:%M', time.localtime(cur['t']))}; change "
           f"over 1 h / 6 h / 24 h; these are what the midterm ranks):"]
    for k, label in AREAS[area]:
        v = cur.get(k)
        if v is None:
            continue
        d = [("n/a" if not then[x] or then[x].get(k) is None else f"{v - then[x][k]:+.4g}") for x in (1, 6, 24)]
        out.append(f"  {label}: {v:,} ({' / '.join(d)})" if isinstance(v, int) else
                   f"  {label}: {v} ({' / '.join(d)})")
    if area == "forum" and cur.get("forum_leader"):
        out.append(f"  forum leader now: {cur['forum_leader']}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    r = append()
    print(r)
    for a in (sys.argv[1:] or AREAS):
        print(text(a))
