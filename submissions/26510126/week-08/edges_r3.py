"""Where the board's own formula pays most for the next step, and what the rules allow there.

    python edges_r3.py            # print the lever table and the rule lines it rests on

The user (11/9): not to break rules, but to keep looking for the edges the rules leave open, aggressively, short of
bankruptcy. Two parts, both read every meta cycle (meta_r3.advice):
  - levers(): for each board term, how far our value must move to pass the next 1, 3 and 5 rivals, and the ALL points
    that buys (weight x percentile step). A term where a small move passes many rivals is an edge; one where the next
    rival is far away is not worth chasing.
  - rule_lines(): the lines of the market's current agent.md that define what counts (Book, revenue, saved, stars,
    storage, refunds, bankruptcy), quoted verbatim so the team reasons from the rule, not from memory, and sees a
    rule the moment it changes.
Honesty stays fixed: true grade claims, the certificate shown, no same-owner deals, no votes against others.
"""
import json
import re
from pathlib import Path

import metrics_r3
from metrics_r3 import ME, SHOP_TERMS, BUYER_TERMS, FIELD

HERE = Path(__file__).resolve().parent
RULES = Path.home() / "Developer" / "agent-market" / "rules"
KEYS = re.compile(r"\b(Book|book|revenue|saved|surplus|stars|storage|refund|warranty|bankrupt|served nothing|counts? )",
                  re.I)


def levers(board, steps=(1, 3, 5)):
    """[(side, term, weight, ours, [(rivals passed, value needed, points gained)])] for the percentile terms."""
    out = []
    for side, rows, terms in (("shop", board.get("shops") or [], SHOP_TERMS),
                              ("buyer", board.get("buyers") or [], BUYER_TERMS)):
        rows = [r for r in rows if r.get("deals")]
        mine = next((r for r in rows if r.get("name") == ME), None)
        if not mine:
            continue
        for term, w in terms.items():
            if (side, term) == ("buyer", "forum"):        # counts as the raw rating since patch 3.5
                continue
            f = FIELD.get(term, term) if side == "shop" else term   # stars: the board ranks stars_rank
            ours = mine.get(f)
            others = sorted(v for r in rows if r is not mine and isinstance(v := r.get(f), (int, float)))
            if not isinstance(ours, (int, float)) or not others:
                continue
            above = [v for v in others if v > ours]
            n = len(others)
            out.append((side, term, w, ours, [(k, above[k - 1], round(w * k / n, 3)) for k in steps if k <= len(above)]))
    return out


def rule_lines(limit=24):
    """The defining lines of the newest agent.md snapshot (rules/agent-md-*.md, written by the watch)."""
    snaps = sorted(RULES.glob("agent-md-*.md"))
    if not snaps:
        return []
    all_lines = snaps[-1].read_text(encoding="utf-8").splitlines()
    # from the scoring rules on (11/9 audit: the first 24 matches cut the saved rule mid-sentence, left out the forum
    # stance rules and kept a Round 2 line)
    start = next((i for i, x in enumerate(all_lines) if x.lstrip("- ").startswith("Rankings")), 0)
    lines = [x.strip() for x in all_lines[start:] if KEYS.search(x)]
    return lines[:limit]


def edges_text(board=None):
    if board is None:
        board = json.loads(metrics_r3.BOARD.read_text(encoding="utf-8"))
    out = ["Board levers (passing the next 1/3/5 rivals on each term, and the ALL points it buys):"]
    for side, term, w, ours, st in sorted(levers(board), key=lambda x: -(x[4][-1][2] if x[4] else 0)):
        fmt = (lambda v: f"{v / 1e6:.0f}M") if isinstance(ours, (int, float)) and abs(ours) >= 1e6 else (lambda v: f"{v:g}")
        steps = ", ".join(f"{k}: {fmt(v)} (+{p})" for k, v, p in st) or "already first"
        out.append(f"  {side} {term} (w {w}): ours {fmt(ours)} -> {steps}")
    rl = rule_lines()
    if rl:
        out.append("What counts, verbatim from the market's agent.md (look for edges here, within the rules):")
        out += [f"  | {x[:200]}" for x in rl]
    return "\n".join(out)


if __name__ == "__main__":
    print(edges_text())
