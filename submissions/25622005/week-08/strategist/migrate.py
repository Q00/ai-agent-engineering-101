"""One-time value migrations of SKILL.md (source "migration"): a default the code release changed after files in
use were written, so the user never has to edit the live file by hand.

An entry {id, reason, changes {key: (from, to)}} sets each key to `to` only while its value in the file is still
`from` (the old shipped default, never tuned since); any other value is the strategist's or the user's and is
skipped. The rows go through the guard (no step, cooldown or max_changes, still LINKED and validate) and the run's
one SKILL.md edit; state["migrations"] lists the ids that are done (run.py marks an entry done once its rows were
written or refused, not while a write is held back).
"""
from strategist import guard

BIG_TICKET_SPLIT = ("big-ticket goods have their own line (stock.big_ticket_list), so goods_max_list only skips goods "
                    "categories: beds and sofas (median list 345k-568k, most of the market's revenue) open, and "
                    "stock.big_ticket_share still caps them")

MIGRATIONS = (
    {"id": "2026-11-06-patch-3.3-end-of-round",
     "reason": "Patch 3.3: unsold stock counts at cost, so no taper, goods stop at T-6 h, no below-cost clearing",
     "changes": {"stock.taper_h": (72, 0), "stock.goods_stop_h": (24, 6), "stock.clear_70_h": (24, 0),
                 "stock.clear_any_h": (6, 0)}},
    # goods_max_list was 50,000 as shipped and 40,000 as the strategist set it: one entry per old value
    {"id": "2026-11-06-big-ticket-split-40k", "reason": BIG_TICKET_SPLIT,
     "changes": {"stock.goods_max_list": (40000, 600000)}},
    {"id": "2026-11-06-big-ticket-split-50k", "reason": BIG_TICKET_SPLIT,
     "changes": {"stock.goods_max_list": (50000, 600000)}},
)


def pending(state):
    """The entries state["migrations"] does not list as done."""
    done = state.get("migrations") if isinstance(state.get("migrations"), list) else []
    return [m for m in MIGRATIONS if m["id"] not in done]


def rows(entries, pol):
    """(guard rows, skipped) for entries. skipped: {id: {key: {"live": value now, "from": value it migrates from}}}
    for every key whose value is not the entry's `from`."""
    out, skipped = [], {}
    for m in entries:
        for key, (old, new) in m["changes"].items():
            live = guard.value(pol, key)
            if live == old:
                out.append({"key": key, "value": new, "source": "migration", "reason": f"{m['id']}: {m['reason']}"})
            else:
                skipped.setdefault(m["id"], {})[key] = {"live": live, "from": old}
    return out, skipped
