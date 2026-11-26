"""Strategy 1 -- the shop remembers its own negotiations.

The buyer has had a memory since LAB 1; the shop started every negotiation
from nothing. Now each one it finishes -- sold, refused, or abandoned by the
buyer -- is kept in a journal: the item, every ask I showed, every offer that
came back, and how it ended.

Before the harness picks a counteroffer it is shown how earlier buyers of the
same item who opened the same way ended up: how many bought, at what price and
in how many rounds, and how many left and from which ask. The harness still
decides; it decides knowing what happened last time.

The shop never learns a buyer's name, but buyers are programs and repeat
themselves. Offers that match an earlier negotiation number for number are
almost certainly the same buyer with the same budget, so the shop can tell
when a customer it has sold to before is back -- the honest version of a
loyalty card.

Where the journal lives depends on where the shop runs: a .jsonl file on the
PC (`file_journal`), a Durable Object's storage on Cloudflare (worker/entry.py
passes its own `persist`). The Journal itself only holds the episodes.
"""
import json
import statistics
import time
from pathlib import Path

PATH = Path(__file__).resolve().parent / ".market" / "negotiations.jsonl"
SIMILAR = 6               # earlier negotiations shown to the harness
WALK_AFTER = 300          # seconds of silence after which a buyer has left (the kit's buyer timeout)
# Measured on the market ledger on 10-23 (two hours, 6150 results): a buyer
# whose first visit to a shop closed gave that shop 13.2 more deals within the
# hour (median 5); one whose first visit ended without a deal gave 1.8 (median 0).
REPEAT_AFTER_DEAL, REPEAT_AFTER_WALK = 13, 2


class Journal:
    def __init__(self, episodes=None, persist=None):
        self.episodes = list(episodes or [])
        self.persist = persist            # called with each new episode, to store it

    def record(self, item: str, asks: list, offers: list, outcome: str, price: int | None = None) -> None:
        """outcome: "deal", "rejected" (the shop ended it) or "walked" (the buyer did)."""
        ep = {"ts": round(time.time()), "item": item, "asks": asks, "offers": offers,
              "outcome": outcome, "price": price}
        self.episodes.append(ep)
        if self.persist:
            self.persist(ep)
        print(f"[seller] journal: {item} {outcome}" + (f" at {price}" if price else "")
              + f" after {len(offers)} offer(s)", flush=True)

    def recall(self, item: str, list_price: int, offers: list) -> str:
        """What my past negotiations of this item say about a buyer who has
        made these offers so far, as lines for the harness. Empty with no history."""
        past = [e for e in self.episodes if e["item"] == item and e["offers"] and e["asks"]]
        if not offers or not past:
            return ""
        r1 = offers[0] / max(1, list_price)             # how low this buyer opened, against my list price
        near = sorted(past, key=lambda e: abs(e["offers"][0] / max(1, e["asks"][0]) - r1))[:SIMILAR]
        lines = [f"Of the {len(near)} earlier buyers of this item whose first offer was closest to this "
                 f"one's: {_summary(near)}."]
        # One matching number is a coincidence; two in a row is a program repeating itself.
        same = [e for e in past if e["offers"][:len(offers)] == offers] if len(offers) >= 2 else []
        if same:
            lines.append(f"{len(same)} earlier negotiation(s) began with exactly these offers {offers}, "
                         f"most likely the same buyer coming back: {_summary(same)}.")
        lines.append(f"A first sale to a buyer has been followed by about {REPEAT_AFTER_DEAL} more from that "
                     f"buyer within the hour; a buyer who leaves without a deal brings about {REPEAT_AFTER_WALK}.")
        return "\n".join(lines)


def _summary(eps: list) -> str:
    deals = [e for e in eps if e["outcome"] == "deal"]
    left = [e for e in eps if e["outcome"] != "deal"]
    s = f"{len(deals)} bought"
    if deals:
        s += (f" (average {statistics.fmean(e['price'] for e in deals):.0f}, "
              f"{statistics.fmean(len(e['offers']) for e in deals):.1f} offers)")
    s += f", {len(left)} left"
    if left:
        s += f" (the last ask they saw: {', '.join(str(e['asks'][-1]) for e in left)})"
    return s


def file_journal(path: Path = PATH) -> Journal:
    """The PC's journal: one JSON line per negotiation, appended as it ends."""
    episodes = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                episodes.append(json.loads(line))
            except ValueError:                          # a half-written last line
                pass
    except OSError:
        pass

    def append(ep):
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ep) + "\n")
    return Journal(episodes, append)
