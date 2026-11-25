"""Round 3: my buyer's money is revenue for the shop it pays, and revenue is a third of a shop's ALL and the board's
revenue table. Of the 244 deals my buyer closed in the 6 h to 11/06 20:55, 157 (156M) went to the 16 shops at or above
90% of my shop's revenue, 93M of it to the two shops that lead the table (inextro 14% of its revenue, sergiobuilds 11%).

So a shop that is a revenue rival of mine is asked after the others: the quotes go to the other shops first, and since
the quotes stop at the first batch with an ask within my limit, a rival is usually asked only when no other shop of the
earlier batches asked within it. A rival quoted in the same batch as a fitting non-rival wins only when it leaves at least
MARGIN more of the list price. The limit itself (value_limit, saved_cap) does not change, so no deal costs more than it did; what can
change is which shop of those within the limit gets it.

Rivals come from the newest board snapshot the scorecard records (.market/board_history.jsonl, shops: name -> [book,
revenue, all]); a snapshot older than MAX_AGE_HOURS, or none, means no rivals and the buyer works as before.
Choosing a shop is the buyer's own call under the market rules (agent.md: "The market publishes neither ... what sells and
who wants what"); nothing here touches another agent's system."""
import json
import os
import time

ME = os.environ.get("SHOP_MARKET_NAME", "nocha12")
SHARE = 0.9              # a shop with at least this share of my revenue is a rival
MARGIN = 0.10            # a rival wins over a fitting non-rival only with this much more of the list price kept
MAX_AGE_HOURS = 3
HISTORY = "board_history.jsonl"
TAIL_BYTES = 400000      # one snapshot line is about 4 KB


def rivals(md, now=None, share=SHARE):
    """The set of shop names at or above share of my shop's revenue on the newest board snapshot in md."""
    now = time.time() if now is None else now
    try:
        with open(os.path.join(md, HISTORY), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return set()
    for line in reversed(lines):
        try:
            snap = json.loads(line)
        except ValueError:
            continue
        if not isinstance(snap, dict) or not isinstance(snap.get("ts"), (int, float)):
            continue
        if now - snap["ts"] > MAX_AGE_HOURS * 3600:
            return set()
        shops = snap.get("shops")
        if not isinstance(shops, dict):
            return set()
        rev = lambda v: v[1] if isinstance(v, list) and len(v) > 1 and isinstance(v[1], (int, float)) else None
        mine = rev(shops.get(ME))
        if not mine:
            return set()
        return {n for n, v in shops.items() if n != ME and rev(v) is not None and rev(v) >= share * mine}
    return set()


def others_first(shops, rival):
    """shops with the rivals moved to the end, each part in its own order."""
    return [s for s in shops if s.get("name") not in rival] + [s for s in shops if s.get("name") in rival]


def penalty(name, rival, margin=MARGIN):
    """What a rival's expected surplus share is lowered by when the quotes are ranked."""
    return margin if name in rival else 0.0
