"""What the midterm grades, and the numbers a change is judged by.

score_gap(): the board's Players rank is shop ALL + buyer ALL, each a weighted sum of percentiles (agent.md: shops by
profit 0.5, revenue 0.3, stars 0.2; buyers by surplus 0.5, forum votes 0.3, deals 0.1, posts 0.1). For each term it
gives our percentile and the points it leaves on the table (weight x (1 - percentile)), so the harness works on the
term that costs most, not on the one it happens to see.

window(t0, t1): the fixed metrics a hypothesis may name, from our own logs (code computes them, no model):
  buyer_close_rate      paid orders / orders
  buyer_surplus_order   (list - price) of paid deals / orders
  buyer_paid_h          paid deals per hour
  shelf_refused_h       payments the kit refused for too little shelf life, per hour
  shop_deals_h          our sales paid on the ledger per hour (not receipts signed: buyers pay one of several shops)
  shop_margin_h         (price - cost) of those deals per hour
  forum_votes_post      net votes per post our buyer wrote in the window (forum.json keeps the newest 100 posts, so
                        only the last hours can be read; forum votes are 0.3 of the buyer's ALL)
  buyer_saved_deal      mean of (list - price) / (list - unit cost guess), capped at 1, over the paid deals: the board's
                        saved term per deal since patch 3.6 (cost unknown to the buyer: buyer_r3.COST_GUESS), so a cheap order counts as much as a dear one; goods that break later count -1 on the
                        board, which this window cannot see yet
  buyer_dear_close      paid orders / orders among orders whose reference price is DEAR or more. 11/3 21:00-11/5 11:00:
                        the 216 orders of 300k or more (5%) gave 55% of our surplus, closing 61%; one more of them
                        closed is worth about ten cheap deals
  buyer_secs_order      median seconds from an order to our buyer's answer (lower is better). The market sends the
                        next order no sooner than 20 s after the last, so time past 20 s is orders lost: 31% of orders
                        took longer, no-deal ones 23 s at the median and 66 s at p90, and we got 139 orders an hour
                        where 20 s each would give about 170
  outage_min            minutes of the window the market listed no shops (the kit logs 'no shops in the market yet'
                        about every 30 s; 11/5 16:12-16:45 and 17:16-17:23). The per-hour metrics count only the
                        minutes the market was up, so an outage does not read as a price or stock effect
"""
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOARD = HERE / "logs" / "board" / "board-round3.json"
# the rules of 11/5 03:49: the shop's first term is book (cash + what unsold units cost), no longer cash ("profit")
SHOP_TERMS = {"book": 0.5, "revenue": 0.3, "stars": 0.2}
# the board ranks the stars term on stars_rank, not the raw mean (11/9 audit: ALL fits stars_rank with a mean error of
# 0.020 against 0.045 for stars over 58 shops; we sit at 4.57 raw, about p0.56, but 4.30 on stars_rank, about p0.28)
FIELD = {"stars": "stars_rank"}
# the rules of 11/6 06:02: the buyer's first term is saved, the sum over deals of surplus / list price (-1 for a deal that
# served nothing, 0 if refunded) divided by deals + 100, so a snack and a sofa at 30% off score the same
BUYER_TERMS = {"saved": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1}
METRICS = ("board_pts_h", "shop_pts_h", "buyer_pts_h", "shop_revenue_h", "buyer_close_rate", "buyer_surplus_order", "buyer_paid_h", "shelf_refused_h", "shop_deals_h",
           "shop_margin_h", "forum_votes_post", "buyer_dear_close", "buyer_secs_order", "buyer_saved_deal")
LOWER_IS_BETTER = {"shelf_refused_h", "buyer_secs_order"}
DEAR = 100000
KIT_LOG = "logs/market-kit.log"
OUTAGE_LINE = "no shops in the market yet"
TAIL = 16_000_000                  # the kit log grows about 1 MB an hour; 16 MB covers more than the windows judged
ME = "thisjonghoon"


def jl(path):
    try:
        return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def pct(values, mine):
    """Share of the others we beat or tie, the board's kind of percentile."""
    others = [v for v in values if v is not None]
    if mine is None or not others:
        return None
    return round(sum(v <= mine for v in others) / len(others), 3)


def score_gap(board=None):
    """[(side, term, weight, our value, percentile, points lost)], the costliest first."""
    if board is None:
        try:
            board = json.loads(BOARD.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
    out = []
    for side, rows, terms, alive in (("shop", board.get("shops") or [], SHOP_TERMS, lambda r: r.get("deals")),
                                     ("buyer", board.get("buyers") or [], BUYER_TERMS, lambda r: r.get("deals"))):
        rows = [r for r in rows if alive(r)]
        mine = next((r for r in rows if r.get("name") == ME), None)
        if not mine:
            continue
        for term, w in terms.items():
            # patch 3.5 (11/6 15:53): the forum rating counts as itself in the buyer's ALL, not as a percentile
            p = mine.get(term) if (side, term) == ("buyer", "forum") and isinstance(mine.get(term), (int, float)) \
                else pct([r.get(term) for r in rows], mine.get(term))
            if p is not None:
                out.append((side, term, w, mine.get(term), p, round(w * (1 - p), 3)))
    return sorted(out, key=lambda x: -x[5])


def gap_text(board=None):
    rows = score_gap(board)
    if not rows:
        return "(no board snapshot)"
    return "\n".join(f"- {s} {t} (weight {w}): ours {v}, {'rating' if (s, t) == ('buyer', 'forum') else 'percentile'} {p:.2f}, points lost {lost}"
                     for s, t, w, v, p, lost in rows)


def _ts(text):
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return None


def outage_secs(t0, t1, base=HERE):
    """Seconds of [t0, t1) the market listed no shops: each 'no shops' line of the kit log stands for the 30 s the kit
    waits before it asks again. The log's lines carry only a clock time, read on the day of t1."""
    path = Path(base) / KIT_LOG
    try:
        with path.open("rb") as f:
            f.seek(max(path.stat().st_size - TAIL, 0))
            text = f.read().decode("utf-8", "ignore")
    except OSError:
        return 0
    day = time.strftime("%Y-%m-%d ", time.localtime(t1))
    seen = set()
    for line in text.splitlines():
        if OUTAGE_LINE not in line:
            continue
        t = _ts(day + line[:8])
        if t is not None and t > t1 + 60:
            t -= 86400                                # a line from the day before
        if t is not None and t0 <= t < t1:
            seen.add(t)
    return min(len(seen) * 30, t1 - t0)


def settled_sales(t0, t1, base=HERE):
    """Our shop's paid sales in [t0, t1): {"t", "price", "cost"} per settle on the market's ledger (the small cache
    market_intel keeps, .market/ledger-recent.json, the last 6 h). Until 11/7 these metrics counted every receipt the
    seller signed (talks_r3.jsonl), but a buyer haggles with several shops and pays one: from 06:52 to 09:35 on 11/7,
    17 of our 300k+ agreements (18M) were paid once, so margin/h read 2-4M against Book growing 0.7-0.9M an hour.
    A unit's cost is its supply entry's total / qty; one not found there is matched to the talk agreed at that price."""
    base = Path(base)
    try:
        entries = json.loads((base / ".market" / "ledger-recent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    urls = {(e["data"].get("url") or "").rstrip("/") for e in entries
            if e.get("kind") == "join" and e["data"].get("name") == ME and e["data"].get("role") == "shop"}
    cost = {}
    for e in entries:
        d = e.get("data") or {}
        if e.get("kind") == "supply" and d.get("shop") == ME and d.get("units"):
            for u in d["units"]:
                cost[u] = (d.get("total") or 0) / len(d["units"])
    talks = [x for x in jl(base / "logs" / "talks_r3.jsonl") if x.get("price") and t0 - 900 <= x.get("t", 0) < t1]
    out = []
    for e in entries:
        d = e.get("data") or {}
        if e.get("kind") != "settle" or not t0 <= e.get("ts", 0) < t1 or str(d.get("shop") or "").rstrip("/") not in urls:
            continue
        c = cost.get(d.get("unit"))
        if c is None:
            c = next((x.get("cost") for x in reversed(talks) if x["price"] == d.get("amount") and x["t"] <= e["ts"]), 0)
        out.append({"t": e["ts"], "price": d.get("amount") or 0, "cost": c or 0})
    return out


def our_refunds(t0, t1, base=HERE, entries=None):
    """What the market refunded from our shop for broken goods in [t0, t1) (the ledger's broken entries): a refund takes
    the price back out of our cash, so out of Book, and out of revenue (11/9: the shop's metrics counted none)."""
    base = Path(base)
    if entries is None:
        try:
            entries = json.loads((base / ".market" / "ledger-recent.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
    urls = {(e["data"].get("url") or "").rstrip("/") for e in entries
            if e.get("kind") == "join" and e["data"].get("name") == ME and e["data"].get("role") == "shop"}
    return sum((e.get("data") or {}).get("refunded") or 0 for e in entries if e.get("kind") == "broken"
               and t0 <= e.get("ts", 0) < t1 and str((e.get("data") or {}).get("shop") or "").rstrip("/") in urls)


def paid_share(t0, t1, base=HERE):
    """(our paid sales, the whole market's paid sales) in [t0, t1) from the ledger cache: the fast guard compares our
    share before and after a change, so the market's own rhythm (night, outages) cancels out."""
    base = Path(base)
    try:
        entries = json.loads((base / ".market" / "ledger-recent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, 0
    market = sum(1 for e in entries if e.get("kind") == "settle" and t0 <= e.get("ts", 0) < t1)
    return len(settled_sales(t0, t1, base)), market


BUCKETS = Path("logs") / "market_buckets_r3.jsonl"
BUCKET = 600
# the ledger cache keeps only the last ~6 h of settles (11/9: first settle 05:36 at 11:40), so a 2 h verdict could
# never be judged again later and the coder's 3 h before/after verdict, taken a few minutes past t + 3 h, lost the
# start of its before-window. Each meta cycle files the closed 10-minute buckets here: ours and the market's paid
# sales, count and value, and our margin


def _settles(base):
    try:
        entries = json.loads((Path(base) / ".market" / "ledger-recent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in entries if e.get("kind") == "settle"]


def record_buckets(base=HERE, now=None):
    """Append the closed 10-minute buckets the cache holds and the file lacks; returns how many were added."""
    base, now = Path(base), now or time.time()
    path = base / BUCKETS
    done = {x.get("t") for x in jl(path)}
    settles = _settles(base)
    if not settles:
        return 0
    first, last = min(e["ts"] for e in settles), max(e["ts"] for e in settles)
    # only buckets the cache has surely caught up on (15 min before its newest settle): a fetch that failed or lagged
    # would otherwise file a half-empty bucket for good (11/9 review: "ledger fetch failed ReadTimeout" at 13:0x)
    end = min(int(now), int(last) - 900) // BUCKET * BUCKET
    todo = [b for b in range((int(first) // BUCKET + 1) * BUCKET, end, BUCKET) if b not in done]
    if not todo:
        return 0
    rows = {b: {"t": b, "ours_n": 0, "ours_value": 0, "ours_margin": 0, "mkt_n": 0, "mkt_value": 0} for b in todo}
    for e in settles:
        b = int(e["ts"]) // BUCKET * BUCKET
        if b in rows:
            rows[b]["mkt_n"] += 1
            rows[b]["mkt_value"] += (e.get("data") or {}).get("amount") or 0
    try:
        entries = json.loads((base / ".market" / "ledger-recent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        entries = []
    for b0 in todo:                                     # refunds out of our value and margin, as on the board
        rf = our_refunds(b0, b0 + BUCKET, base, entries)
        rows[b0]["ours_value"] -= rf
        rows[b0]["ours_margin"] -= rf
        rows[b0]["ours_refund"] = rf
    for x in settled_sales(todo[0], todo[-1] + BUCKET, base):
        b = int(x["t"]) // BUCKET * BUCKET
        if b in rows:
            rows[b]["ours_n"] += 1
            rows[b]["ours_value"] += x["price"]
            rows[b]["ours_margin"] += x["price"] - (x.get("cost") or 0)
    path.parent.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for b in todo:
            f.write(json.dumps(rows[b]) + "\n")
    return len(todo)


def shop_side(t0, t1, base=HERE):
    """Ours and the market's paid sales in [t0, t1): from the ledger cache when it reaches back to t0, else from the
    10-minute buckets (whole buckets inside the span, at least 90% of it). None when neither covers it."""
    base = Path(base)
    settles = _settles(base)
    if settles and min(e["ts"] for e in settles) <= t0:
        ours = settled_sales(t0, t1, base)
        mkt = [e for e in settles if t0 <= e["ts"] < t1]
        refunds = our_refunds(t0, t1, base)
        return {"ours_n": len(ours), "ours_value": sum(x["price"] for x in ours) - refunds,
                "ours_margin": sum(x["price"] - (x.get("cost") or 0) for x in ours) - refunds, "mkt_n": len(mkt),
                "mkt_value": sum((e.get("data") or {}).get("amount") or 0 for e in mkt), "from": "ledger"}
    rows = [x for x in jl(base / BUCKETS) if t0 <= x.get("t", 0) and x["t"] + BUCKET <= t1]
    if not rows or len({x["t"] for x in rows}) * BUCKET < 0.9 * (t1 - t0):
        return None
    out = {k: sum(x.get(k) or 0 for x in rows) for k in ("ours_n", "ours_value", "ours_margin", "mkt_n", "mkt_value")}
    return dict(out, **{"from": "buckets"})


def market_share(t0, t1, base=HERE):
    """Our part of the market's paid sales in [t0, t1): by count and by value (settled amounts), from the ledger cache
    or the 10-minute buckets (shop_side). 11/9: the 2 h number verdicts compared our raw margin/h before and after a
    change, so a change made at 06:00 looked good for the morning rush and one made at 22:00 bad for the night; the
    market's own sales over the same hours divide that clock out. Shares None with no market sales or no data."""
    s = shop_side(t0, t1, base) or {}
    n, v = s.get("mkt_n") or 0, s.get("mkt_value") or 0
    return {"ours": s.get("ours_n", 0), "market": n, "market_value": v,
            "share_n": round(s["ours_n"] / n, 4) if n else None,
            "share_value": round(s["ours_value"] / v, 4) if v else None}


def lever_prices(board=None, k=3):
    """ALL points per unit of Book, revenue and buyer saved where we stand now: the weight times the percentile step of
    passing the next k rivals, over the distance to the k-th. One price list for every verdict, so a change is judged
    on what the board pays for it (11/9: the loops judged margin/h, margin per talk or deals/h, each a different aim,
    and a playbook that won on margin per talk cut revenue and sales)."""
    if board is None:
        try:
            board = json.loads(BOARD.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    out = {}
    for side, term, key in (("shops", "book", "book"), ("shops", "revenue", "revenue"), ("shops", "stars", "stars"),
                            ("buyers", "saved", "saved")):
        rows = [r for r in board.get(side) or [] if r.get("deals")]
        mine = next((r for r in rows if r.get("name") == ME), None)
        f = FIELD.get(term, term) if side == "shops" else term
        if not mine or not isinstance(mine.get(f), (int, float)):
            continue
        above = sorted(v for r in rows if r is not mine and isinstance(v := r.get(f), (int, float)) and v > mine[f])
        if not above:
            continue
        kk = min(k, len(above))
        w = (SHOP_TERMS if side == "shops" else BUYER_TERMS)[term]
        out[key] = w * kk / (len(rows) - 1) / max(above[kk - 1] - mine[f], 1e-9)
        if side == "buyers":
            out["deals_total"] = mine.get("deals") or 0
            out["saved_now"] = mine[term]
        if key == "stars":
            out["reviews_total"] = mine.get("reviews") or 0
            # the change is measured against stars_rank too (review 16:5x: against the raw 4.57 the term came out 2.3x
            # too large and outweighed book and revenue in a 2 h window)
            out["stars_now"] = mine[f]
    return out


def our_stars(t0, t1, base=HERE):
    """Stars of the verified reviews of our paid deals in [t0, t1) (the market's result entries): only these move our
    shop's stars, 0.2 of its ALL (11/9 review: the shop's points had no stars term)."""
    base = Path(base)
    try:
        entries = json.loads((base / ".market" / "ledger-recent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    urls = {(e["data"].get("url") or "").rstrip("/") for e in entries
            if e.get("kind") == "join" and e["data"].get("name") == ME and e["data"].get("role") == "shop"}
    return [e["data"]["stars"] for e in entries if e.get("kind") == "result" and t0 <= e.get("ts", 0) < t1
            and (e.get("data") or {}).get("verified") and isinstance(e["data"].get("stars"), int)
            and str(e["data"].get("shop") or "").rstrip("/") in urls]


def shop_points_h(margin_h, revenue_h, prices=None, reviews_h=0, stars_mean=None):
    """The shop's part of board points/h (Book via paid margin, revenue). 11/9: the buyer's saved term was 82-91% of
    board_pts_h over a 2 h window, so shop changes judged on the total read the buyer's swings; a shop change is judged
    on this part alone."""
    p = prices if prices is not None else lever_prices()
    if not p:
        return None
    pts = margin_h * p.get("book", 0) + revenue_h * p.get("revenue", 0)
    if reviews_h and stars_mean is not None and "stars" in p and p.get("reviews_total"):
        # each new review moves our mean stars by (its stars - the mean now) / reviews so far, as saved moves
        pts += reviews_h * (stars_mean - p["stars_now"]) / p["reviews_total"] * p["stars"]
    return round(pts, 6)


COST_GUESS = 0.52                                     # a unit's cost as a share of list for the saved scale: calibrated so
                                                      # the window's mean matches the board's gain per new deal (0.842 over
                                                      # 11/14-15: deals 17,886 -> 19,331, saved 0.7185 -> 0.7277); 0.60 capped
                                                      # most deals at 1 (mean 0.938)
COST_GUESS_GRADE = {"A": 0.65, "B": 0.50, "C": 0.35}   # buyer_r3.COST_GUESS_GRADE


def buyer_points_h(paid_h, saved_deal, prices=None):
    """The buyer's part of board points/h: saved per deal against our running mean, over deals + 100."""
    p = prices if prices is not None else lever_prices()
    if not p or saved_deal is None or "saved" not in p:
        return None
    return round(paid_h * (saved_deal - p["saved_now"]) / (p["deals_total"] + 100) * p["saved"], 6)


def board_points_h(margin_h, revenue_h, paid_h, saved_deal, prices=None):
    """Estimated ALL points an hour at this pace: Book grows by the margin of paid sales, revenue by their price, and
    saved moves by each paid buyer deal's distance from our running mean, over deals + 100 (the board's formula)."""
    p = prices if prices is not None else lever_prices()
    if not p:
        return None
    pts = margin_h * p.get("book", 0) + revenue_h * p.get("revenue", 0)
    if saved_deal is not None and "saved" in p:
        pts += paid_h * (saved_deal - p["saved_now"]) / (p["deals_total"] + 100) * p["saved"]
    return round(pts, 5)


def window(t0, t1, base=HERE, keep=None):
    """`keep` (an order id -> bool, the code A/B's arm): the buyer's metrics over those orders only; per hour they
    count the arm's half, so compare an arm with the other arm, not with a whole hour."""
    base = Path(base)
    down = outage_secs(t0, t1, base)
    hours = max((t1 - t0 - down) / 3600, 0.25)
    orders = {d["order"]: d for d in jl(base / "logs" / "demand_r3.jsonl")
              if d.get("event") == "order" and t0 <= d.get("t", 0) < t1 and d.get("order")
              and (keep is None or keep(d["order"]))}
    dear = {o for o, d in orders.items() if (d.get("ref") or 0) >= DEAR}
    mem = jl(base / "memory.jsonl")
    lists = {m.get("order"): m.get("list_price") for m in mem if m.get("kind") == "trade" and m.get("list_price")}
    paid, refused = [], 0
    for m in mem:
        if m.get("kind") != "payment":
            continue
        t = _ts(m.get("time"))
        if t is None or not t0 <= t < t1 or (keep is not None and not keep(m.get("order"))):
            continue
        if m.get("paid"):
            paid.append(m)
        elif "shelf life" in (m.get("reason") or ""):
            refused += 1
    secs = sorted(_ts(m.get("time")) - orders[m["order"]]["t"] for m in mem
                  if m.get("kind") == "trade" and m.get("order") in orders and _ts(m.get("time")))
    secs = [x for x in secs if 0 <= x < 600]
    dear_paid = sum(1 for m in paid if m.get("order") in dear)
    # the board's saved per deal since patch 3.6: (list - price) / (list - unit cost), capped at 1; the unit's cost is
    # unknown to the buyer, so it is taken as the buyer's COST_GUESS share of list (by grade when the entry has one).
    # Leak audit #44: the old (list - price) / list (~0.40) was set against saved_now (~0.73, the new scale), so
    # buyer_points_h read almost every paid deal as lost points
    rates = [min(1.0, (lists[m["order"]] - m["price"]) / (lists[m["order"]] * (1 - COST_GUESS_GRADE.get(m.get("grade"), COST_GUESS))))
             for m in paid if lists.get(m.get("order")) and m.get("price") is not None]
    stars = our_stars(t0, t1, base)
    surplus = sum((lists.get(m.get("order")) or 0) - (m.get("price") or 0) for m in paid if lists.get(m.get("order")))
    sold = settled_sales(t0, t1, base)
    try:
        posts = json.loads((base / ".market" / "forum.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        posts = []
    posts = posts if isinstance(posts, list) else posts.get("posts", []) if isinstance(posts, dict) else []
    ours = [p for p in posts if isinstance(p, dict) and p.get("author") == ME and p.get("role", "buyer") == "buyer"
            and t0 <= (p.get("ts") or 0) < t1]
    refunds = our_refunds(t0, t1, base)
    margin_h = (sum(x["price"] - (x.get("cost") or 0) for x in sold) - refunds) / hours
    revenue_h = (sum(x["price"] for x in sold) - refunds) / hours
    saved_deal = sum(rates) / len(rates) if rates else None
    return {
        "board_pts_h": board_points_h(margin_h, revenue_h, len(paid) / hours, saved_deal),
        "shop_pts_h": shop_points_h(margin_h, revenue_h, reviews_h=len(stars) / hours,
                                    stars_mean=sum(stars) / len(stars) if stars else None),
        "shop_stars": round(sum(stars) / len(stars), 3) if stars else None,
        "buyer_saved_sd": round((sum((x - sum(rates) / len(rates)) ** 2 for x in rates) / (len(rates) - 1)) ** 0.5, 4)
        if len(rates) > 1 else None,
        "buyer_saved_n": len(rates),
        "buyer_pts_h": buyer_points_h(len(paid) / hours, saved_deal),
        "shop_revenue_h": round(revenue_h),
        "buyer_close_rate": round(len(paid) / len(orders), 3) if orders else None,
        "buyer_surplus_order": round(surplus / len(orders)) if orders else None,
        "buyer_paid_h": round(len(paid) / hours, 1),
        "shelf_refused_h": round(refused / hours, 1),
        "shop_deals_h": round(len(sold) / hours, 1),
        "shop_margin_h": round(margin_h),
        "forum_votes_post": round(sum((p.get("likes") or 0) - (p.get("dislikes") or 0) for p in ours) / len(ours), 2)
        if ours else None,
        "buyer_saved_deal": round(saved_deal, 4) if saved_deal is not None else None,
        "buyer_dear_close": round(dear_paid / len(dear), 3) if len(dear) >= 5 else None,
        "buyer_secs_order": round(secs[len(secs) // 2], 1) if secs else None,
        "orders": len(orders),
        "outage_min": round(down / 60),
    }


def change(metric, before, after):
    """Relative change of a metric, signed so that positive is better; None when it cannot be told."""
    b, a = (before or {}).get(metric), (after or {}).get(metric)
    if b is None or a is None or b == 0:
        return None
    r = (a - b) / abs(b)
    return round(-r if metric in LOWER_IS_BETTER else r, 3)


if __name__ == "__main__":
    now = time.time()
    print(gap_text())
    print(json.dumps(window(now - 3600, now), ensure_ascii=False))
