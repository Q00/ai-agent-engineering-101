"""B4: the buyer's shop notebook and purchase book, kept by code (no model writes a fact).

    buyer/notebook.json     per shop (by name): the outcomes of our last agreed deals there
                            (settled, unpaid, error) and when, the times it turned us away and the
                            times we agreed a deal there (the last day), false grade claims,
                            breakdowns (and how many had no warranty), the shares of the list price
                            we paid, the url seen

A failure counts against a shop for a day (EXPIRE), then is forgotten; a settled deal counts until
newer outcomes push it out. Being turned away before any offer ("none of that in stock") is not a
failure: a busy shop's stock list is seconds old and its best sellers go fast. A shop that turns us
away on most tries (3 or more times in a day and more than 3 times per deal there) is passed over:
on 11/4 four house shops did so on 67-94% of our tries with no deal, against 4-15% at the busiest
real shops. Until then a turn-away counted as a failure and a failure never expired, so a shop
locked after 3 never got the try that could clear it: 33 of the 47 shops were locked at 19:30, and 16%
of the orders since 17:12 had been dropped for it.
    buyer/purchases.jsonl   one line per settled purchase: order, shop, SKU, name, price, list
                            price, grade, claim, warranty, shelf life left, when it settled
    buyer/breaks.jsonl      a breakdown matched to the purchase it came from
    buyer/pending.json      deals agreed in an order, until payments.jsonl says whether we paid

When: market_buyer.py calls sync() at the start of each order (the kit wrote payments.jsonl
and broken.jsonl after the previous one ended); review.py calls on_settled() after each
settled deal. B1 reads trust(), paid_ratio() and the filters; B3 reads broke_here().
"""
import json
import time

import common

RECENT = 5               # the deals that count for a shop's recent record
KEEP = 10
UNKNOWN_TRUST = 0.8      # a shop we have not dealt with
PRIOR = 2                # pseudo-deals at UNKNOWN_TRUST that soften a short record
BREAK_WINDOW = 7 * 3600  # goods break within 6 h of the sale; an hour of slack for clocks
PAY_WAIT = 600           # seconds after a deal with no payment line: it was not paid
EXPIRE = 24 * 3600       # a failure older than this no longer counts against the shop
TURNED_MIN, TURNED_PER_DEAL = 3, 3   # turn-aways in a day, and per deal there, that make a shop one to pass over


def folder():
    return common.MARKET_DIR / "buyer"


def load() -> dict:
    return common.read_json(folder() / "notebook.json", {}) or {}


def save(nb: dict) -> None:
    common.write_json(folder() / "notebook.json", nb)


def entry(nb: dict, name: str) -> dict:
    return nb.setdefault(name, {"recent": [], "recent_at": [], "settled": 0, "unpaid": 0, "errors": 0, "refused": 0,
                                "turned": [], "agreed_at": [], "false_claims": 0, "broken": 0,
                                "broken_no_warranty": 0, "ratios": [], "url": None})


def times(e: dict) -> list:
    """recent_at lined up with recent; an entry from before times were kept (11/4) has None."""
    r, at = e.get("recent") or [], list(e.get("recent_at") or [])
    return ([None] * (len(r) - len(at)) + at)[-len(r):] if r else []


def outcome(nb: dict, name: str, what: str, url: str | None = None, now: float | None = None) -> None:
    """what: settled, unpaid (agreed but not paid: the kit refused, the receipt was unsigned, or no
    payment line came) or error (the shop broke off). A turn-away is not an outcome: turned_away()."""
    e = entry(nb, name)
    at = times(e)
    e["recent"] = (e["recent"] + [what])[-KEEP:]
    e["recent_at"] = (at + [int(now or time.time())])[-KEEP:]
    key = {"settled": "settled", "unpaid": "unpaid", "error": "errors"}[what]
    e[key] = e.get(key, 0) + 1
    if url:
        e["url"] = url


def turned_away(nb: dict, name: str, url: str | None = None, now: float | None = None) -> None:
    """The shop turned us away before any offer, for a product its stock list showed."""
    now = now or time.time()
    e = entry(nb, name)
    e["turned"] = [t for t in e.get("turned") or [] if now - t < EXPIRE] + [int(now)]
    e["refused"] = e.get("refused", 0) + 1
    if url:
        e["url"] = url


def agreed(nb: dict, name: str, now: float | None = None) -> None:
    """We agreed a deal there (paid or not): what a shop's turn-aways are weighed against."""
    now = now or time.time()
    e = entry(nb, name)
    e["agreed_at"] = [t for t in e.get("agreed_at") or [] if now - t < EXPIRE] + [int(now)]


def add_pending(order: str, **deal) -> None:
    p = common.read_json(folder() / "pending.json", {}) or {}
    p[order] = dict(deal, ts=time.time())
    common.write_json(folder() / "pending.json", p)


def sync(nb: dict) -> None:
    """Fold in what the kit wrote since the last order: payments and breakdowns."""
    md, state_path = common.MARKET_DIR, folder() / "state.json"
    state = common.read_json(state_path, {}) or {}
    pending = common.read_json(folder() / "pending.json", {}) or {}
    pays = common.read_jsonl(md / "payments.jsonl")
    for p in pays[state.get("payments", 0):]:
        deal = pending.pop(p.get("order"), None)
        if not deal:
            continue
        if p.get("paid"):
            outcome(nb, deal["shop"], "settled", deal.get("url"))
            if deal.get("list_price"):
                e = entry(nb, deal["shop"])
                e["ratios"] = (e["ratios"] + [round(deal["price"] / deal["list_price"], 3)])[-20:]
        else:
            policy = "policy asks" in str(p.get("reason") or "")        # the kit held to our own terms (shelf life):
            if not policy:                                              # no mark against the shop (11/6)
                outcome(nb, deal["shop"], "unpaid", deal.get("url"))
            common.record("unpaid_buys", order=p.get("order"), shop=deal["shop"], sku=deal.get("sku"),
                          reason=p.get("reason"), policy=policy)
    state["payments"] = len(pays)
    news = common.read_jsonl(md / "broken.jsonl")
    if news[state.get("broken", 0):]:
        bought = common.read_jsonl(folder() / "purchases.jsonl")
        for b in news[state.get("broken", 0):]:
            match = [x for x in bought if x.get("name") == b.get("name")
                     and 0 <= b.get("broke_at", 0) - x.get("settled_at", 0) <= BREAK_WINDOW]
            if not match:
                common.write_json(folder() / "unmatched_breaks.json",
                                  (common.read_json(folder() / "unmatched_breaks.json", []) or []) + [b])
                continue
            x = max(match, key=lambda x: x["settled_at"])
            e = entry(nb, x["shop"])
            e["broken"] += 1
            e["broken_no_warranty"] += not x.get("warranty_hours")
            with open(folder() / "breaks.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(x, broke_at=b.get("broke_at")), ensure_ascii=False) + "\n")
    state["broken"] = len(news)
    # a deal whose payment line never came: the kit pays within seconds of the deal, so after PAY_WAIT it was
    # not paid (before 11/4 such deals were dropped after a day, unrecorded, and the shop kept its trust)
    for order, deal in [(k, v) for k, v in pending.items() if time.time() - v.get("ts", 0) >= PAY_WAIT]:
        pending.pop(order)
        outcome(nb, deal["shop"], "unpaid", deal.get("url"))
        common.record("unpaid_buys", order=order, shop=deal["shop"], sku=deal.get("sku"),
                      reason=f"no payment line within {PAY_WAIT // 60} minutes of the deal")
    common.write_json(folder() / "pending.json", pending)
    common.write_json(state_path, state)


def on_settled(deal: dict) -> dict | None:
    """review.py: deal.json -> a purchases.jsonl line, and a false grade claim into the notebook."""
    order = (deal.get("order") or {}).get("order")
    pend = (common.read_json(folder() / "pending.json", {}) or {}).get(order) or {}
    got, rec = deal.get("settled") or {}, deal.get("receipt") or {}
    shop = pend.get("shop") or got.get("shop")
    if not shop:
        return None
    line = {"order": order, "shop": shop, "sku": got.get("sku") or rec.get("item"), "name": got.get("name"),
            "price": rec.get("price"), "list_price": pend.get("list_price"), "grade": got.get("grade"),
            "claim": got.get("claim"), "claim_honest": got.get("claim_honest"),
            "warranty_hours": got.get("warranty_hours"), "expires_at": got.get("expires_at"),
            "value": got.get("value"), "settled_at": time.time()}
    folder().mkdir(parents=True, exist_ok=True)
    with open(folder() / "purchases.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    if common.worse_than_claimed(got):                  # goods better than claimed are no deceit (11/6 16:40)
        nb = load()
        entry(nb, shop)["false_claims"] += 1
        save(nb)
    return line


def trust(nb: dict, name: str, summary: dict | None = None) -> float:
    """0..1: how far to count on a deal with this shop ending settled and as described."""
    e = nb.get(name)
    if not e:
        t = UNKNOWN_TRUST
    else:
        recent = live(nb, name)[-RECENT:]
        t = (recent.count("settled") + UNKNOWN_TRUST * PRIOR) / (len(recent) + PRIOR)
        if e.get("false_claims"):
            t *= 0.5
        if e.get("broken_no_warranty"):
            t *= 0.8
    shop = ((summary or {}).get("by_shop") or {}).get(name) or {}
    if shop.get("claims", 0) >= 5 and shop.get("honest", 0) / shop["claims"] < 0.5:
        t *= 0.7                                                  # the market's record: mostly false claims
    return round(max(0.05, min(1.0, t)), 3)


def live(nb: dict, name: str, now: float | None = None) -> list:
    """The shop's recent outcomes that still count: a settled deal always, a failure for EXPIRE seconds
    (one with no time, from before times were kept, until newer outcomes push it out). A "refused" left
    from before 11/4 19:00, when a turn-away was a failure, no longer counts."""
    now = now or time.time()
    e = nb.get(name) or {}
    return [w for w, t in zip(e.get("recent") or [], times(e))
            if w == "settled" or (w != "refused" and (t is None or now - t < EXPIRE))]


def failures(nb: dict, name: str, now: float | None = None) -> int:
    return sum(1 for x in live(nb, name, now)[-RECENT:] if x != "settled")


def failed_in_a_row(nb: dict, name: str, n: int = 3, now: float | None = None) -> bool:
    recent = live(nb, name, now)
    return len(recent) >= n and all(x != "settled" for x in recent[-n:])


def turned_counts(nb: dict, name: str, now: float | None = None) -> tuple[int, int]:
    """(times it turned us away, deals we agreed there), in the last day."""
    now = now or time.time()
    e = nb.get(name) or {}
    return (sum(1 for t in e.get("turned") or [] if now - t < EXPIRE),
            sum(1 for t in e.get("agreed_at") or [] if now - t < EXPIRE))


def turns_away(nb: dict, name: str, now: float | None = None) -> bool:
    """It turns us away on most tries: TURNED_MIN or more times in a day, more than TURNED_PER_DEAL per deal."""
    t, d = turned_counts(nb, name, now)
    return t >= TURNED_MIN and t > TURNED_PER_DEAL * d


def paid_ratio(nb: dict, name: str) -> tuple[float | None, float | None]:
    """(average, lowest) share of the list price we paid this shop, or (None, None)."""
    r = (nb.get(name) or {}).get("ratios") or []
    return (round(sum(r) / len(r), 3), min(r)) if r else (None, None)


def broke_here(nb: dict, name: str) -> bool:
    return bool((nb.get(name) or {}).get("broken"))
