"""What an action is worth on the board: delta(board, action) -> (d Shop ALL, d Buyer ALL).

/rules (patches 3.3-3.5) and /agent.md:
  Shop ALL  = 0.5 book(pct) + 0.3 revenue(pct) + 0.2 stars(pct)
  Buyer ALL = 0.5 saved(pct) + 0.3 forum rating(value) + 0.1 deals(pct) + 0.1 posts(pct)
  Book      = balance - starting capital - bankruptcy money + unsold stock at cost
  Saved     = S / (D + 100),  S = sum over deals of surplus / list (broken goods: 0 under a warranty, -1 without)

The bandit's reward, Horvitz's B and C and the paid-dislike decision all read this one function, so "is it worth
it" is answered in the unit the round is ranked in.

A percentile is a step function: one more won of book moves nothing until it passes a neighbour. For decisions the
model uses the same percentile with a straight line between neighbours, so a small change has a small, signed
value and the value of a large one equals the real step count.
"""
import bisect
import json
import time

from . import config, market

SHOP_W = {"book": 0.5, "revenue": 0.3, "stars_rank": 0.2}
BUYER_W = {"saved": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1}
RAW = ("forum",)                      # counts as itself, not as a percentile
CACHE = "board-dist.json"
CACHE_S = 600                         # refetch the board at most every 10 min
STALE_S = 3 * 3600                    # older than this the board is not used: callers fall back to their constant
STORAGE_PER_DAY = 0.01


def pct(others, v):
    """Share of the ranked field at or below v, us included in the field: a straight line from one neighbour's
    value to the next, so pct(others[j]) = (j + 1) / n and a value above everyone gets len(others) / n."""
    n = len(others) + 1
    i = bisect.bisect_left(others, v)                 # others strictly below v
    if i >= len(others):
        return len(others) / n
    if i == 0:
        return 1 / n if v >= others[0] else 0.0
    lo, hi = others[i - 1], others[i]
    return (i + ((v - lo) / (hi - lo) if hi > lo else 1.0)) / n


class Board:
    """The ranked field without us (sorted values per scored metric) and our own values."""

    def __init__(self, shops, buyers, me_shop, me_buyer, ts=None):
        self.shops = {k: sorted(v) for k, v in shops.items()}
        self.buyers = {k: sorted(v) for k, v in buyers.items()}
        self.me_shop, self.me_buyer, self.ts = dict(me_shop), dict(me_buyer), ts or time.time()

    @classmethod
    def from_api(cls, board, names=None, ts=None):
        names = names or config.MY_NAMES

        def split(rows, keys):
            rows = [r for r in rows or [] if r.get("deals")]
            me = next((r for r in rows if r.get("name") in names), None)
            return ({k: [r.get(k) or 0 for r in rows if r is not me] for k in keys},
                    {k: (me or {}).get(k) or 0 for k in keys} if me else None)
        shops, me_shop = split(board.get("shops"), SHOP_W)
        buyers, me_buyer = split(board.get("buyers"), BUYER_W)
        if me_shop is None or me_buyer is None:
            return None
        return cls(shops, buyers, me_shop, me_buyer, ts)

    def to_json(self):
        return {"ts": self.ts, "shops": self.shops, "buyers": self.buyers, "me_shop": self.me_shop,
                "me_buyer": self.me_buyer}

    @classmethod
    def from_json(cls, d):
        return cls(d["shops"], d["buyers"], d["me_shop"], d["me_buyer"], d.get("ts"))

    @property
    def S(self):
        """Our sum of surplus / list, recovered from the board's saved and deals."""
        return self.me_buyer["saved"] * (self.me_buyer["deals"] + 100)


def load(max_age_s=CACHE_S, fetch=None, path=None, now=None):
    """The latest board, from the cache file when fresh enough, else from /api/board. None when neither is usable."""
    now = now or time.time()
    path = path or (config.STATE_DIR / CACHE)
    cached = None
    try:
        cached = Board.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if cached and now - cached.ts < max_age_s:
        return cached
    try:
        b = Board.from_api((fetch or (lambda: market.get_json("/api/board")))() or {}, ts=now)
        if b:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(b.to_json()), encoding="utf-8")
            tmp.replace(path)
            return b
    except Exception:
        pass
    return cached if cached and now - cached.ts < STALE_S else None


def shop_all(b, **change):
    """Shop ALL with our values moved by change (book=, revenue=, stars_rank= are added to the current ones)."""
    return sum(w * pct(b.shops[k], b.me_shop[k] + change.get(k, 0)) for k, w in SHOP_W.items())


def saved_after(b, d_S=0.0, d_deals=0):
    return (b.S + d_S) / (b.me_buyer["deals"] + d_deals + 100)


def buyer_all(b, d_S=0.0, d_deals=0, d_forum=0.0, d_posts=0):
    me = b.me_buyer
    return (BUYER_W["saved"] * pct(b.buyers["saved"], saved_after(b, d_S, d_deals))
            + BUYER_W["forum"] * (me["forum"] + d_forum)
            + BUYER_W["deals"] * pct(b.buyers["deals"], me["deals"] + d_deals)
            + BUYER_W["posts"] * pct(b.buyers["posts"], me["posts"] + d_posts))


def expected_r(discount, p_break=0.0, warranty=False):
    """Expected surplus / list of one goods deal: the discount when it holds, 0 (warranty) or -1 when it breaks."""
    return (1 - p_break) * discount - (0 if warranty else p_break)


def delta(b, action):
    """(d Shop ALL, d Buyer ALL) of one action. Kinds:
      sale          price, cost            book += price - cost, revenue += price
      hold          cost, days             goods: book -= 1% of cost per day
      expire        cost                   food past its date leaves book at its cost
      buy           r                      one more deal with surplus / list = r (use expected_r for goods)
      post                                 one more buyer post; d_forum when its effect on the rating is known
      paid_dislike  n, unit                unit x (n - 1)^2 leaves the shop's cash
      raw           d_book, d_revenue, d_stars, d_S, d_deals, d_forum, d_posts
    """
    k = action.get("kind")
    g = lambda name: action.get(name) or 0
    shop, buyer = {}, {}
    if k == "sale":
        shop = {"book": g("price") - g("cost"), "revenue": g("price")}
    elif k == "hold":
        shop = {"book": -g("cost") * STORAGE_PER_DAY * g("days")}
    elif k == "expire":
        shop = {"book": -g("cost")}
    elif k == "buy":
        buyer = {"d_S": g("r"), "d_deals": 1}
    elif k == "post":
        buyer = {"d_posts": 1, "d_forum": g("d_forum")}
    elif k == "paid_dislike":
        shop = {"book": -g("unit") * max(0, g("n") - 1) ** 2}
    elif k == "raw":
        shop = {"book": g("d_book"), "revenue": g("d_revenue"), "stars_rank": g("d_stars")}
        buyer = {x: g(x) for x in ("d_S", "d_deals", "d_forum", "d_posts")}
    else:
        raise ValueError(f"unknown action kind {k!r}")
    d_shop = shop_all(b, **shop) - shop_all(b) if any(shop.values()) else 0.0
    d_buyer = buyer_all(b, **buyer) - buyer_all(b) if any(buyer.values()) else 0.0
    return d_shop, d_buyer


def saved_threshold(b, lo=-1.0, hi=1.0):
    """The surplus / list at which one more deal leaves Buyer ALL unchanged. A deal above our saved raises saved;
    one below lowers it but still adds a deal (0.1 of ALL as a percentile), so the break-even sits at or under
    saved. Bisection on delta(buy r), which rises with r."""
    f = lambda r: delta(b, {"kind": "buy", "r": r})[1]
    if f(hi) <= 0:                                    # nothing to gain (we lead the field): do not lower saved
        return b.me_buyer["saved"]
    if f(lo) >= 0:
        return lo
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return hi
