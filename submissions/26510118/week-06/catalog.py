"""Week 06, challenge 1 -- how the shop finds an item among thousands.

3513 items will not fit in a prompt, and pasting them every turn would be slow
and expensive even if they did. The split here is the same one the rest of the
submission uses: the code does what is mechanical, the harness does what needs
reading.

    code      narrows 3513 items to the 8 closest, by word overlap weighted
              with inverse document frequency plus a bonus when a number in
              the request is near a number in the entry ("about 40 books"
              against "holds about 40 books")
    harness   picks one of those 8, because "not taller than my desk" and
              "something wooden" are judgements about fit, not string matches

Exact names win before any of that. The trade test still sends "I want to buy
a lamp" and expects the lamp with reserve 100, and the catalogue holds 235
lamps; a search would happily return a brass one at 140 and fail the run. An
item whose name appears in the request as a whole word is simply that item.
"""
import json
import math
import re
from pathlib import Path

CATALOG_FILE = Path(__file__).with_name("catalog.json")

STOP = {"a", "an", "the", "for", "to", "of", "and", "or", "is", "it", "its", "my",
        "me", "i", "want", "buy", "need", "looking", "something", "with", "that",
        "about", "some", "would", "like", "please", "can", "you", "have", "in",
        "on", "at", "not", "than", "more", "less", "but", "no", "any", "cm"}

_ITEMS: list[dict] = []
_IDF: dict[str, float] = {}


def _tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+|\d+", s.lower()) if t not in STOP]


tokens = _tokens          # the same splitter the shops use, for callers


def load() -> list[dict]:
    """Read the catalogue once and precompute what the search needs."""
    global _ITEMS, _IDF
    if _ITEMS:
        return _ITEMS
    _ITEMS = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    for it in _ITEMS:
        toks = _tokens(it["name"] + " " + it["description"])
        it["_tok"] = set(toks)
        it["_num"] = [int(t) for t in toks if t.isdigit()]
    _reindex()
    return _ITEMS


def configure(categories=None, mult: float = 1.0) -> int:
    """Narrow this process's catalogue to one shop's range and prices.

    Challenge 2 runs twenty shops, and twenty identical shops would make the
    Agent Cards worthless to read -- the buyer has to be able to tell a
    lighting shop from a seating one, and a cheap one from an expensive one.
    Each shop is this same server with a slice of the catalogue and a price
    multiplier. Called once at startup, before any search; with no arguments
    the shop keeps the whole catalogue at list prices, which is what the trade
    test gets.
    """
    items = load()
    if categories:
        keep = {c.strip().lower() for c in categories}
        items = [i for i in items if i["category"] in keep]
    if mult != 1.0:
        items = [dict(i, reserve=max(20, round(i["reserve"] * mult))) for i in items]
    global _ITEMS
    _ITEMS = items
    _reindex()
    return len(_ITEMS)


def _reindex() -> None:
    global _IDF
    df: dict[str, int] = {}
    for it in _ITEMS:
        for t in it["_tok"]:
            df[t] = df.get(t, 0) + 1
    n = max(1, len(_ITEMS))
    _IDF = {t: math.log(n / c) for t, c in df.items()}


def categories() -> list[str]:
    return sorted({i["category"] for i in load()})


def by_id(item_id: str):
    return next((i for i in load() if i["id"] == item_id), None)


def exact(text: str):
    """The item when the request is nothing but its name.

    "I want to buy a lamp" strips to ["lamp"], which is exactly the name of
    one catalogue entry, so it is that entry and no search runs -- which is
    what keeps the trade test resolving to the lamp at 100 rather than one of
    the other 234 lamps. "I'd like a rustic oak chair" strips to three words,
    is not a bare name, and goes to the search where it belongs.
    """
    want = _tokens(text)
    if not want:
        return None
    for it in load():
        if _tokens(it["name"]) == want:
            return it
    return None


def search(query: str, k: int = 8) -> list[dict]:
    """The k closest entries. Pure code: no harness call, no network."""
    items = load()
    q = _tokens(query)
    qset, qnums = set(q), [int(t) for t in q if t.isdigit()]
    scored = []
    for it in items:
        s = sum(_IDF.get(t, 0.0) for t in qset & it["_tok"])
        if s <= 0:
            continue
        for a in qnums:                       # "40 books" near "about 40 books"
            for b in it["_num"]:
                if b and abs(a - b) <= max(2, 0.2 * max(a, b)):
                    s += 2.0
        scored.append((s, it))
    scored.sort(key=lambda p: (-p[0], p[1]["id"]))
    return [it for _, it in scored[:k]]


def line(it: dict) -> str:
    """One candidate as the harness sees it. No reserve: the choice is about
    fit, and a model shown the prices would start optimising the wrong thing."""
    return f"{it['name']} -- {it['description']}"
