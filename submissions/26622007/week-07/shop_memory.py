"""The shop's memory of its own negotiations and reviews, and the opening price it allows from them.

Memory file: {"negotiations": [{"time", "task", "item", "reserve", "open", "asks", "offers",
                                "result", "price"}]}
- In the market (SHOP_MARKET_NAME set, e.g. nocha12) it is memory/seller_memory.json, and the
  shop's reviews are read from the market's /api/shops every REVIEWS_TTL seconds.
- In the market test (no SHOP_MARKET_NAME) it is memory/seller_test_memory.json, without reviews.

Opening price: the harness proposes an opening ask; the code caps it at reserve * (1 + markup).
The markup comes from pricing.json tiers by review count and average stars, and steps down while
the recent conversion (deals / finished negotiations) is low. The reserve never moves.
"""
import json
import os
import threading
import time

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
NAME = os.environ.get("SHOP_MARKET_NAME")
MEMORY = os.environ.get("SHOP_MEMORY") or ("memory/seller_memory.json" if NAME else "memory/seller_test_memory.json")
PRICING = os.environ.get("SHOP_PRICING", "pricing.json")
REVIEWS_TTL = 120
RECENT = 10

DEFAULT_PRICING = {
    "tiers": [{"min_reviews": 0, "min_stars": 0, "markup": 0.10},
              {"min_reviews": 40, "min_stars": 3.8, "markup": 0.20},
              {"min_reviews": 80, "min_stars": 4.0, "markup": 0.30}],
    "low_conversion": 0.6, "step_down": 0.05, "min_markup": 0.05,
}

_lock = threading.Lock()
_reviews = {"at": 0.0, "count": None, "stars": None, "recent": [], "share": None}
PROOF_MIN = 3          # five-star reviews among the latest ones needed before the shop says so


def load_pricing():
    """Read every time, so the tiers can change without restarting the shop."""
    try:
        with open(PRICING, encoding="utf-8") as f:
            p = json.load(f)
        return {**DEFAULT_PRICING, **p}
    except (OSError, ValueError):
        return dict(DEFAULT_PRICING)


def load():
    try:
        with open(MEMORY, encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m.get("negotiations"), list) else {"negotiations": []}
    except (OSError, ValueError):
        return {"negotiations": []}


def record(entry):
    with _lock:
        m = load()
        m["negotiations"].append({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry})
        os.makedirs(os.path.dirname(MEMORY) or ".", exist_ok=True)
        tmp = MEMORY + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        os.replace(tmp, MEMORY)


def reviews():
    """(count, average stars) of this shop's reviews in the market, cached; (None, None) if unknown."""
    if not NAME:
        return None, None
    if time.time() - _reviews["at"] > REVIEWS_TTL:
        _reviews["at"] = time.time()   # also on failure, so a slow market is not asked on every turn
        try:
            shops = httpx.get(f"{MARKET}/api/shops", timeout=5).json()["shops"]
            me = next((s for s in shops if s.get("name") == NAME), None)
            if me is not None:
                stars = [r["stars"] for r in me.get("reviews") or [] if isinstance(r.get("stars"), (int, float))]
                _reviews["recent"] = stars
                board = httpx.get(f"{MARKET}/api/board", timeout=5).json()
                row = next((s for s in board.get("shops", []) if s.get("name") == NAME), {})
                _reviews["count"] = row.get("reviews", len(stars))
                _reviews["share"] = row.get("share")
                _reviews["stars"] = row.get("stars") or (sum(stars) / len(stars) if stars else None)
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    return _reviews["count"], _reviews["stars"]


def recent(item=None):
    done = [n for n in load()["negotiations"] if n.get("result") in ("deal", "rejected", "canceled")]
    last = done[-RECENT:]
    deals = [n for n in last if n["result"] == "deal"]
    same = [n["price"] for n in done if n["result"] == "deal" and n.get("item") == item][-5:] if item else []
    return {"finished": len(last), "deals": len(deals),
            "conversion": (len(deals) / len(last)) if last else None, "item_prices": same}


def markup(item=None):
    """(markup, why) for the opening cap of this item.

    review tier (gradual) -> share discount (below the target share) -> conversion step-down
    -> per-item ceiling. Never below min_markup; the reserve itself never moves."""
    p = load_pricing()
    count, stars = reviews()
    tier = p["tiers"][0]
    for t in p["tiers"]:
        if (count or 0) >= t.get("min_reviews", 0) and (stars or 0) >= t.get("min_stars", 0):
            tier = t
    m = tier["markup"]
    why = [f"tier {tier.get('min_reviews', 0)}+ reviews ({count} reviews, "
           f"{stars if stars is None else round(stars, 2)} stars) {m:.0%}"]
    share, sp = _reviews.get("share"), p.get("share", {})
    if share is not None and sp:
        target = sp.get("target", 0.10)
        cut = sp.get("cut_far", 0.05) if share < target / 2 else sp.get("cut_near", 0.03) if share < target else 0.0
        if cut:
            m -= cut
            why.append(f"share {share:.1%} < target {target:.0%} -> -{cut:.0%}")
    r = recent()
    if r["conversion"] is not None and r["finished"] >= 5 and r["conversion"] < p["low_conversion"]:
        m -= p["step_down"]
        why.append(f"conversion {r['deals']}/{r['finished']} low -> -{p['step_down']:.0%}")
    ceiling = (p.get("item_max_markup") or {}).get(item)
    if ceiling is not None and m > ceiling:
        m = ceiling
        why.append(f"{item} ceiling {ceiling:.0%}")
    m = max(p["min_markup"], m)
    return m, "; ".join(why)


def note(item):
    """One line for the harness prompt: what this shop's own recent trades say."""
    r = recent(item)
    parts = []
    if r["item_prices"]:
        parts.append(f"your last {item} deals closed at {', '.join(str(round(p)) for p in r['item_prices'])}")
    if r["conversion"] is not None:
        parts.append(f"{r['deals']} of your last {r['finished']} negotiations ended in a deal")
    return "; ".join(parts)


def social_proof():
    """A sentence without digits about real recent reviews, or "" when they do not back it up.

    Digits are left out on purpose: other buyers read prices from the shop's text."""
    reviews()
    fives = sum(1 for x in _reviews["recent"] if x >= 5)
    if fives >= PROOF_MIN:
        return "Recent buyers gave us five stars for fair prices and receipts that match the agreed price."
    return ""


# --- adaptive opening price (discounted Thompson sampling over markup arms)
#
# Each finished negotiation records the arm (markup over the reserve) it opened with. A visit counts
# as a success when it ended in a deal, with half credit if the buyer's public review gave it 3 stars
# or fewer; visits where the buyer never offered the reserve are left out (the budget was too low,
# no opening price would have helped). Older visits weigh less (GAMMA per newer visit of that item).
# Score per arm = sampled success rate * opening price, minus a penalty for being above the market's
# average deal price while the shop's share is under target. Reviews are read from the public
# ledger by the shop's own task ids.

import random

_stats = {"at": 0.0, "means": {}}
_ledger = {"seq": None, "stars": {}, "at": 0.0}


def market_means():
    """Average deal price per item in the current round, cached with the reviews."""
    if time.time() - _stats["at"] > REVIEWS_TTL:
        _stats["at"] = time.time()
        try:
            st = httpx.get(f"{MARKET}/api/stats", timeout=5).json()
            items = st["rounds"][str(st["round"])]["items"]
            _stats["means"] = {k: v["mean"] for k, v in items.items() if v.get("n", 0) >= 5}
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    return _stats["means"]


def review_stars():
    """{task id: stars} for this shop's deals, from the public ledger (incremental)."""
    if not NAME or time.time() - _ledger["at"] < REVIEWS_TTL:
        return _ledger["stars"]
    _ledger["at"] = time.time()
    mine = {n["task"] for n in load()["negotiations"] if n.get("result") == "deal"}
    try:
        after = _ledger["seq"]
        if after is None:   # start a few thousand entries back instead of from the beginning
            after = 0
            for guess in (20000, 15000, 12000, 9000, 6000, 3000, 0):
                page = httpx.get(f"{MARKET}/api/ledger", params={"after": guess}, timeout=5).json()
                if page:
                    after = guess
                    break
        for _ in range(20):
            page = httpx.get(f"{MARKET}/api/ledger", params={"after": after}, timeout=10).json()
            if not page:
                break
            for r in page:
                d = r.get("data") or {}
                rec = d.get("receipt") or {}
                if r.get("kind") == "result" and rec.get("task") in mine and d.get("stars"):
                    _ledger["stars"][rec["task"]] = d["stars"]
            after = page[-1]["seq"]
            if len(page) < 500:
                break
        _ledger["seq"] = after
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return _ledger["stars"]


def choose_opening(item, reserve):
    """(opening ask, arm, why). Never below the reserve."""
    reviews()                             # refreshes the share used below
    p = load_pricing()
    arms = p.get("arms", [0.0, 0.03, 0.06, 0.10, 0.15])
    gamma = p.get("gamma", 0.97)
    ceiling = (p.get("item_max_markup") or {}).get(item)
    if ceiling is not None:
        arms = [a for a in arms if a <= ceiling] or [min(arms)]
    stars = review_stars()
    a = {m: 1.0 for m in arms}
    b = {m: 1.0 for m in arms}
    def arm_of(n):                        # older records have no arm: take the nearest one to their opening
        if n.get("arm") in a:
            return n["arm"]
        if not n.get("open") or not n.get("reserve"):
            return None
        return min(arms, key=lambda m: abs(n["open"] - n["reserve"] * (1 + m)))

    visits = [dict(n, arm=arm_of(n)) for n in load()["negotiations"] if n.get("item") == item
              and n.get("result") in ("deal", "rejected", "canceled")]
    visits = [n for n in visits if n["arm"] is not None]
    weight = 1.0
    for n in reversed(visits):
        if n["result"] != "deal" and max(n.get("offers") or [0]) < n.get("reserve", reserve):
            continue                      # the buyer never reached the reserve: not the price's fault
        s = 0.0
        if n["result"] == "deal":
            s = 0.5 if stars.get(n["task"], 5) <= 3 else 1.0
        a[n["arm"]] += weight * s
        b[n["arm"]] += weight * (1 - s)
        weight *= gamma
    share, mean = _reviews.get("share"), market_means().get(item)
    sp = p.get("share", {})
    low = share is not None and share < sp.get("target", 0.10)
    lam = p.get("lambda_low_share", 2.0) if low else p.get("lambda", 0.3)
    scores = {}
    for m in arms:
        price = max(reserve, round(reserve * (1 + m)))
        theta = random.betavariate(a[m], b[m])
        over = max(0, price - mean) if mean else 0
        scores[m] = theta * price - lam * over
    best = max(scores, key=scores.get)
    price = max(reserve, round(reserve * (1 + best)))
    why = (f"arm +{best:.0%} of {[f'{m:.0%}' for m in arms]}, posterior "
           f"{a[best]:.1f}/{b[best]:.1f}, market mean {mean and round(mean, 1)}, "
           f"share {share if share is None else f'{share:.1%}'}{' (under target)' if low else ''}")
    return price, best, why
