"""The last hour of raw market data and our own logs (seller, buyer, restock, forum, the kit's log),
turned into a compact brief for the model.

The model sees the brief, never the raw data, so the brief has to fit in a prompt (< 16 KB as JSON)
and carry numbers a change can be argued from: our rank among real shops and buyers, what moved since
the last run, prices against list price, why our deals fail. Text another agent wrote (shop pitches,
forum posts, reviews) is kept apart under keys starting with "untrusted_", cut short and with angle
brackets replaced, so decide.py can fence it off as data. Other shops' names and item names that are
not catalog SKUs are untrusted too: leaders carry a neutral id (L1, L2, ...) and the name only under
untrusted_name. Round 3 boards replace some Round 2 fields (ALL, surplus, forum, honest claims, refunds,
the players table); both sets are read. Any source may be missing; the brief then just lacks that part.
Our stock (inventory) and the focus SKUs (focus, chosen by strategist/focus.py) come from run.py.
The ledger rows are the ones since the last run: rates over them divide by ledger_h, the hours they span.
score splits our ALL into the market's weighted percentiles (weights parsed from its rules by
strategist/facts.py), bands shows the last hour's settles by list price band (strategist/collect.py
ledger_buckets), seller and buyer_log count our own decision logs of the window.
From the SKU tables (collect.sku_tables, 6 h per SKU, 24 h per kind) and the trend scoring (strategist/trend.py):
focus lists the candidates with their rates, our share, held units, market cover and gates, and price_check;
economics has the line budget, turnover, unsold goods, spoilage, the big-ticket budget, the surge allowance
left on our SKUs, p_clear against our floor per kind and the four expansion conditions; mix has the category
leaves (market rate, our share, /api/stats deals since the last run) and what the top 3 shops' reviews name
(counts only).
"""
import json
import math
import re
import statistics
from collections import Counter

from strategist import collect

ME = "psyoung82"
LIMIT = 16000               # bytes of compact UTF-8 JSON
# Round 2 fields, then Round 3 ones (board.html econShops/econBuyers); a missing field is None.
SHOP_F = ("deals", "share", "visits", "stars", "reviews", "revenue", "profit",
          "all", "stars_rank", "claims", "honest_claims", "broken", "refunds")
BUYER_F = ("orders", "deals", "paid", "saved", "held", "hopeless", "all", "surplus", "forum", "served", "posts")
INV_TOP = 8                 # SKUs listed by cost tied up
FOOD_RISK_H = 24            # food with fewer hours left is at risk
FOCUS_TOP = 12
LEAF_TOP = 8
FREE_TOP = 8
STOCKOUT_TOP = 6            # big-ticket SKUs that sell and are (nearly) sold out
SLOW_TOP = 5                # big-ticket SKUs whose units sit
SLOW_COVER_H = 48           # held / our daily rate above this many hours is a slow line
C_FALLBACK = {"food": 0.51, "goods": 0.61}
# plan 11-06 section 3(d): grow stock only while these hold
EXPAND = {"turnover_day": 1.5, "unsold_goods_share": 0.20, "spoilage_share": 0.01, "balance": 10_000_000}
TURN_MIN_H = 3              # hours of our 24 h sums before turnover per day is given
SCORE_F = {"shop": ("book", "revenue", "stars"), "buyer": ("saved", "forum", "deals", "posts")}   # Patch 3.4: saved
BAND_NAMES = ("<20k", "20k-50k", "50k-100k", "100k-200k", "200k-500k", "500k-1M", ">=1M")


# --- small helpers: every input may be missing or of the wrong type

def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _dict(x):
    return x if isinstance(x, dict) else {}


def _rows(x):
    return [r for r in x if isinstance(r, dict)] if isinstance(x, list) else []


def _r(x, n=4):
    return round(x, n) if isinstance(x, float) else x


def _ratio(a, b):
    a, b = _num(a), _num(b)
    return round(a / b, 4) if a is not None and b else None


def _median(xs):
    return _r(statistics.median(xs), 3) if xs else None


def _clip(s, n):
    """Other agents' text: one line, no control characters or angle brackets, at most n characters."""
    s = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(s if s is not None else "")).split())
    s = s.replace("<", "‹").replace(">", "›")
    return s if len(s) <= n else s[:n - 1] + "…"


def _name(s, n=40):
    return re.sub(r"[^\w.-]", "", str(s if s is not None else ""))[:n]


def _login(row):
    o = row.get("owner")
    return o.get("login") if isinstance(o, dict) else o


def _find(rows, me):
    return next((r for r in rows if r.get("name") == me or r.get("player") == me or _login(r) == me), None)


def our_url(board, me=ME):
    """Our shop's card URL on the board ("" when not found)."""
    return str(_dict(_dict(_find(_rows(_dict(board).get("shops")), me)).get("card")).get("url") or "").rstrip("/")


def _ours(shop, me, our_url):
    s = str(shop or "").rstrip("/")
    return bool(s) and (s == me or s == our_url)


# --- our standing

def _rank(pool, mine, f, lower_better=False):
    """rank 1 = best among rows that have f; pct 0..100, higher is better."""
    v = _num(mine.get(f))
    vals = [_num(r.get(f)) for r in pool if r is not mine]
    vals = [x for x in vals if x is not None]
    if v is None:
        return {}
    better = sum(1 for x in vals if (x < v if lower_better else x > v))
    n = len(vals) + 1
    return {f"{f}_rank": better + 1, f"{f}_pct": round(100 * (n - 1 - better) / (n - 1)) if n > 1 else 100}


def _pool(rows, mine):
    return [r for r in rows if r is not mine and not r.get("house") and (_num(r.get("deals")) or 0) > 0] + [mine]


def _me(shops, buyers, players, me):
    out = {}
    s = _find(shops, me)
    if s:
        d = {f: _r(_num(s.get(f))) for f in SHOP_F}
        d["conversion"] = _ratio(s.get("deals"), s.get("visits"))
        pool = _pool(shops, s)
        for f in ("all", "deals", "profit", "stars", "stars_rank"):
            d.update(_rank(pool, s, f))
        d["ranked"] = len(pool)
        out["shop"] = d
    b = _find(buyers, me)
    if b:
        d = {f: _r(_num(b.get(f))) for f in BUYER_F}
        d["conversion"] = _ratio(b.get("deals"), b.get("orders"))
        pool = _pool(buyers, b)
        for f in ("all", "surplus", "deals"):
            d.update(_rank(pool, b, f))
        d.update(_rank(pool, b, "paid", lower_better=True))
        d["ranked"] = len(pool)
        out["buyer"] = d
    p = _find(players, me)
    if p:
        out["player"] = _player(p, players)
    return out


def _flat(p):
    """Numbers of a players row, nested ones flattened: {"total", "shop": {"all"}} -> total, shop_all."""
    out = {}
    for k, v in p.items():
        if _num(v) is not None:
            out[k] = _r(v)
        elif isinstance(v, dict):
            out.update({f"{k}_{kk}": _r(vv) for kk, vv in v.items() if _num(vv) is not None})
    return out


def _player(p, players):
    d = dict(list(_flat(p).items())[:15])
    if "total" in d:                                    # Round 3: rank by total among non-house players
        pool = [_flat(r) for r in players if r is not p and not r.get("house")]
        mine = {"total": d["total"]}
        d.update(_rank(pool + [mine], mine, "total"))
        d["ranked"] = len(pool) + 1
    return d


def _delta(me, prev, now):
    pm = _dict(prev).get("me")
    if not isinstance(pm, dict):
        return None
    pts = _num(_dict(prev).get("ts"))
    out = {"hours": round((now - pts) / 3600, 2) if pts is not None else None}
    for sec, d in me.items():
        pd = _dict(pm.get(sec))
        out[sec] = {k: (_r(v - pd[k]) if _num(pd.get(k)) is not None else None)
                    for k, v in d.items() if _num(v) is not None}
    return out


def _leaders(shops, me):
    """Top real shops by ALL (Round 3), deals and profit. A shop is "L<n>" in every list; its name and
    pitch (other agents' text) appear once, in its first row, under untrusted_ keys."""
    real = [s for s in shops if not s.get("house") and _find([s], me) is None]
    ids = {}

    def row(s):
        first = id(s) not in ids
        ids.setdefault(id(s), f"L{len(ids) + 1}")
        r = {"id": ids[id(s)], **{f: _r(_num(s.get(f))) for f in ("all", "deals", "share", "stars", "profit", "revenue")},
             "conversion": _ratio(s.get("deals"), s.get("visits"))}
        if first:
            r["untrusted_name"] = _clip(s.get("name"), 40)
            r["untrusted_pitch"] = _clip(_dict(s.get("card")).get("description"), 160)
        return r

    top = lambda f: sorted((s for s in real if _num(s.get(f)) is not None), key=lambda s: -s[f])[:5]
    out = {"by_all": [row(s) for s in top("all")]} if any(_num(s.get("all")) is not None for s in real) else {}
    out["by_deals"] = [row(s) for s in top("deals")]
    out["by_profit"] = [row(s) for s in top("profit")]
    return out


def _market(board, stats):
    totals = {k: v for k, v in _dict(board.get("totals")).items() if _num(v) is not None}
    st = _dict(stats)
    return {"totals": totals, "round_stats": {k: st.get(k) for k in ("results", "deals", "walkaways")
                                              if _num(st.get(k)) is not None}}


# --- the window

def _prices(trades, ledger, catalog, since, me, our_url):
    """Deal prices per item: trades plus ledger results (the same deals share an id; counted once)."""
    seen, obs = set(), {}

    def add(did, item, price, ours):
        price = _num(price)
        if item is None or not price or price <= 0 or (did is not None and did in seen):
            return
        if did is not None:
            seen.add(did)
        key = item if isinstance(item, str) and item in catalog else _clip(item, 32)
        obs.setdefault(key, []).append((price, ours))

    for t in _rows(trades):
        if (_num(t.get("ts")) or 0) >= since:
            add(t.get("id"), t.get("item"), t.get("price"), _ours(t.get("shop"), me, our_url))
    for r in ledger:
        if r.get("kind") == "result":
            d = _dict(r.get("data"))
            rc = _dict(d.get("receipt"))
            add(d.get("id"), rc.get("item"), rc.get("price"), _our_result(d, me, our_url))

    out = []
    for key, ps in sorted(obs.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:15]:
        c = catalog.get(key) if isinstance(catalog.get(key), list) and len(catalog[key]) >= 4 else None
        lp = _num(c[2]) if c else None
        ours = [p for p, o in ps if o]
        # an item that is not a catalog SKU is a name another agent chose: data, not a key
        row = {"key": key, "name": _clip(c[0], 40) if c else None} if c else {"key": None, "untrusted_item": key}
        row |= {"n": len(ps),
               "median": _median([p for p, _ in ps]), "list": lp,
               "median_list": _median([round(p / lp, 4) for p, _ in ps]) if lp else None,
               "ours_n": len(ours), "ours_median": _median(ours),
               "ours_median_list": _median([round(p / lp, 4) for p in ours]) if lp and ours else None}
        out.append(row)
    return out


def _our_result(d, me, our_url):
    """Our shop's result: by the shop URL, or by the receipt's shop name (the URL of a quick tunnel
    changes on every restart, so results from before a restart carry an older URL)."""
    return _ours(d.get("shop"), me, our_url) or _ours(_dict(d.get("receipt")).get("shop"), me, our_url)


def _window(ledger, me, our_url, gap, first):
    """Counts over the ledger rows in the window. covered_s is the time they actually span: on a first
    run (first) or after a gap it is far less than window_h, and partial says so."""
    results = [_dict(r.get("data")) for r in ledger if r.get("kind") == "result"]
    ts = [r["ts"] for r in ledger if _num(r.get("ts")) is not None]
    kinds = Counter(_name(r.get("kind")) for r in ledger)
    fails = Counter(_clip(k, 40) for d in results for k, ok in _dict(d.get("checks")).items() if ok is False)
    side = lambda rows: {"results": len(rows), "verified": sum(1 for d in rows if d.get("verified") is True)}
    return {"rows": len(ledger), "kinds": dict(kinds.most_common(10)), "orders": kinds.get("order", 0),
            **side(results), "walkaways": sum(1 for d in results if not d.get("receipt")),
            "failing_checks": dict(fails.most_common(8)),
            "our_shop": side([d for d in results if _our_result(d, me, our_url)]),
            "our_buyer": side([d for d in results if d.get("buyer") == me]), "ledger_gap": bool(gap),
            "covered_s": round(max(ts) - min(ts)) if ts else 0, "partial": bool(gap or first)}


def _demand(summary):
    """Our summary's demand vs supply by category (the market's /api/demand table counts SKUs, not demand, and
    is no longer read); None without it."""
    cats = _dict(_dict(summary).get("market_categories"))
    rows = []
    for k, v in cats.items():
        dem, sup = _num(_dict(v).get("demand")), _num(_dict(v).get("supply"))
        if dem is not None:
            rows.append({"category": _clip(k, 40), "demand": _r(dem, 2), "supply": sup,
                         "ratio": round((dem + 1) / ((sup or 0) + 1), 2)})
    return {"source": "own summary", "top": sorted(rows, key=lambda r: -r["ratio"])[:10]} if rows else None


def _choice_key(r):
    c = r.get("choice")
    k = str(r.get("kind"))
    if isinstance(c, dict) and c.get("action"):
        k += ":" + str(c["action"])
    return _clip(k, 40)


def _ops(own, posts, since, me):
    dec = _dict(own.get("decisions"))
    ms = lambda rows: _median([r["elapsed_ms"] for r in rows if _num(r.get("elapsed_ms")) is not None])
    seller = _rows(dec.get("seller"))
    orders = [r for r in _rows(dec.get("buyer")) if r.get("kind") == "order"]
    no_deal = Counter(_clip(r.get("why"), 80) for r in orders if not isinstance(r.get("choice"), dict))
    restock = [r for r in _rows(dec.get("restock")) if r.get("kind") == "restock"]
    out = {"seller": {"n": len(seller), "by_choice": dict(Counter(map(_choice_key, seller)).most_common(10)),
                      "median_ms": ms(seller)},
           "buyer": {"orders": len(orders), "deals": sum(1 for r in orders if isinstance(r.get("choice"), dict)),
                     "no_deal_why": dict(no_deal.most_common(5)), "median_ms": ms(orders)},
           "reviews": len(_rows(dec.get("review"))),
           "policy_errors": sum(1 for rows in dec.values() for r in _rows(rows) if r.get("kind") == "policy_error")}
    if restock:
        last = restock[-1]
        inp, buy = _dict(last.get("inputs")), last.get("choice") if isinstance(last.get("choice"), list) else []
        win, held = _dict(inp.get("window")), _dict(inp.get("stock"))
        out["restock"] = {"runs": len(restock),
                          # restock.py buys nothing while the supply window is closed (stock.window_lead_s)
                          "window_closed_runs": sum(1 for r in restock if isinstance(r.get("rules"), list)
                                                    and "stock.window_lead_s" in r["rules"]),
                          "ts": last.get("ts"), "lines": len(buy),
                          "buy": [{"sku": _name(_dict(b).get("sku")), "qty": _num(_dict(b).get("qty"))} for b in buy[:5]],
                          "balance": _num(inp.get("balance")), "units": _num(inp.get("units")),
                          "headroom": _num(inp.get("headroom")), "why": _clip(last.get("why"), 120),
                          "window": {"open": win.get("open") if isinstance(win.get("open"), bool) else None,
                                     "in_s": _num(win.get("in_s"))},
                          "stock": {k: _num(held.get(k)) for k in ("skus", "units", "empty_picks", "overpriced")},
                          # memory ingest per run: ran, skipped (window, locked, migration_pending) or error
                          "ingest": dict(Counter(map(_ingest, restock)).most_common(5))}
        planned = next((r for r in reversed(restock) if isinstance(_dict(r.get("inputs")).get("excluded"), dict)), None)
        if planned:                                     # the last run that planned: why SKUs were left out
            pin = _dict(planned.get("inputs"))
            out["restock"]["excluded"] = {_name(k): _num(v) for k, v in list(pin["excluded"].items())[:12]}
            out["restock"]["surge"] = _surge(pin.get("surge"))
    acp = _rows(own.get("acp"))
    # the kit logs the cause under "error" (refuse: the message; settle_refused: the failed checks)
    refused = Counter(_clip(r.get("error") or r.get("reason"), 60)
                      for r in acp if r.get("event") in ("refuse", "settle_refused"))
    out["acp"] = {"events": dict(Counter(_name(r.get("event")) for r in acp)), "refusals": dict(refused.most_common(5))}
    out["kit"] = _kit(_dict(own.get("kit")))
    out["forum"] = _our_forum(posts, _rows(dec.get("forum")), since, me)
    # logs whose window did not fit in what collect.read_own reads: their counts above are too low
    out["truncated_logs"] = [_name(x) for x in own.get("truncated") or [] if isinstance(x, str)]
    return out


def _ingest(r):
    ing = _dict(_dict(r.get("inputs")).get("ingest"))
    return "error" if ing.get("error") else _clip(ing["skipped"], 20) if isinstance(ing.get("skipped"), str) else "ran"


def _surge(v):
    """The restock row's surge part ([{sku, factor, ...}] or {sku: factor}) as {sku: factor}, at most 8."""
    if isinstance(v, dict):
        pairs = list(v.items())
    else:
        pairs = [(_dict(x).get("sku"), _dict(x).get("factor")) for x in v] if isinstance(v, list) else []
    return {_name(k): _num(f) for k, f in pairs[:8] if isinstance(k, str)}


def _kit(k):
    """collect._kit's counts; why our buyer did not pay quotes shops' answers, so it is untrusted."""
    nums = {f: _num(k.get(f)) for f in ("lines", "starts", "orders", "errors", "tracebacks", "supply_bought",
                                        "supply_units", "supply_cost", "paid", "paid_total", "pay_refused")}
    refused = {_clip(f, 20): _num(n) for f, n in _dict(k.get("supply_refused")).items()}
    why = sorted(((_num(n) or 0, w) for w, n in _dict(k.get("pay_refused_why")).items()), key=lambda x: -x[0])
    return {**nums, "supply_refused": refused,
            "untrusted_pay_refused_why": [{"n": n, "why": _clip(w, 80)} for n, w in why[:5]]}


def _votes(p):
    if _num(p.get("score")) is not None:
        return p["score"]
    if _num(p.get("likes")) is not None:
        return p["likes"] - (_num(p.get("dislikes")) or 0)
    return _num(p.get("votes")) or 0


def _our_forum(posts, rows, since, me):
    """Our buyer's posts on the public forum (each one counts toward buyer ALL, unless hidden) and the
    forum hook's decisions: posted, or skipped and why. The forum API returns only the newest 100 posts:
    sample_covered_s is the time they span, so posts_window is a lower bound when it is under the window."""
    posts = _rows(posts)
    ours = [p for p in posts if (p.get("author") == me or _login(p) == me) and p.get("role") == "buyer"]
    recent = [p for p in ours if (_num(p.get("ts")) or 0) >= since]
    skipped = Counter(_clip(r.get("why"), 40) for r in rows if _dict(r.get("choice")).get("posted") is False)
    ts = [p["ts"] for p in posts if _num(p.get("ts")) is not None]
    return {"sample_covered_s": int(max(ts) - min(ts)) if ts else None,
            "posts": len(ours), "posts_window": len(recent),
            "mean_score": round(sum(map(_votes, ours)) / len(ours), 2) if ours else None,
            "likes": sum(_num(p.get("likes")) or 0 for p in ours),
            "dislikes": sum(_num(p.get("dislikes")) or 0 for p in ours),
            "posted": sum(1 for r in rows if _dict(r.get("choice")).get("posted") is True),
            "skipped": dict(skipped.most_common(5))}


def _forum(posts, since):
    posts = _rows(posts)
    top = sorted(posts, key=lambda p: -_votes(p))[:5]
    return {"n": len(posts), "n_window": sum(1 for p in posts if (_num(p.get("ts")) or 0) >= since),
            "untrusted_top": [{"author": _clip(p.get("author"), 40), "votes": _votes(p), "ts": _num(p.get("ts")),
                               "text": _clip(p.get("text") or p.get("body") or p.get("title"), 200)} for p in top]}


def _reviews(rows):
    rows = sorted(_rows(rows), key=lambda r: -(_num(r.get("ts")) or 0))
    stars = [r["stars"] for r in rows if _num(r.get("stars")) is not None]
    hist = Counter(str(round(s)) for s in stars)
    return {"n": len(rows), "mean": round(sum(stars) / len(stars), 2) if stars else None,
            "stars_hist": {k: hist.get(k, 0) for k in ("1", "2", "3", "4", "5")},
            "untrusted_last": [{"stars": _num(r.get("stars")), "ts": _num(r.get("ts")),
                                "text": _clip(r.get("review"), 120)} for r in rows[:5]]}


def _inventory(inv, ledger, our_url, me, pol, now, ledger_h, flows, known):
    """Our stock from .market/inventory.json: cost against the D1 cap, the SKUs that tie up the most money (how
    old, food hours left, our sales per hour over the ledger rows' ledger_h hours, hours of cover at that rate),
    stale goods, food near expiry, expired units, and the units in / sold / gone unsold since the last run (None
    on the first run). Our settles are those to our shop URL or of a unit we hold or held at the last run (known)."""
    if "units" not in _dict(inv):
        return None
    units = _rows(inv["units"])
    st, se = _dict(_dict(pol).get("stock")), _dict(_dict(pol).get("seller"))
    live = [u for u in units if u.get("grade") != "expired"]
    cost = lambda us: round(sum(_num(u.get("cost")) or 0 for u in us))
    known = set(known) | {str(u.get("unit")) for u in units}
    sold = Counter(d.get("sku") for d in (_dict(r.get("data")) for r in ledger if r.get("kind") == "settle")
                   if _ours(d.get("shop"), me, our_url) or str(d.get("unit")) in known)
    by = {}
    for u in live:
        by.setdefault(u.get("sku"), []).append(u)

    def row(sku, us):
        rate = sold[sku] / ledger_h
        ages = [now - t for t in (_num(u.get("supplied_at")) for u in us) if t is not None]
        left = [(t - now) / 3600 for t in (_num(u.get("expires_at")) for u in us if u.get("kind") == "food")
                if t is not None]
        return {"sku": _name(sku), "qty": len(us), "cost": cost(us),
                "age_h": round(max(ages) / 3600, 1) if ages else None,
                "food_left_h": round(min(left), 1) if left else None, "sales_per_h": _r(rate, 2),
                "cover_h": round(len(us) / rate, 1) if rate else None}

    stale_s = (_num(se.get("goods_stale_days")) or math.inf) * 86400
    stale = [u for u in live if u.get("kind") == "goods" and now - (_num(u.get("supplied_at")) or now) > stale_s]
    risk = [u for u in live if u.get("kind") == "food"
            and (_num(u.get("expires_at")) or math.inf) - now < FOOD_RISK_H * 3600]
    expired = [u for u in units if u.get("grade") == "expired"]
    balance, inv_cost, share = _num(_dict(inv).get("balance")), cost(live), _num(st.get("inventory_cap_share"))
    return {"balance": balance, "inv_cost": inv_cost,
            "cap": round(share * (balance + inv_cost)) if balance is not None and share is not None else None,
            "units": len(live), "skus": len(by),
            "top": [row(k, us) for k, us in sorted(by.items(), key=lambda kv: -cost(kv[1]))[:INV_TOP]],
            "stale_goods": {"n": len(stale), "cost": cost(stale)}, "food_at_risk": {"n": len(risk), "cost": cost(risk)},
            "expired": {"n": len(expired), "cost": cost(expired)}, "since_last_run": flows}


def _focus(f, inv):
    """The focus and trend lists now and as this run chose them, the top candidates of strategist/trend.py (market
    settles per hour over 6 h and 1 h, our share of them, units we hold, hours the market's shelves cover, the
    surge factor and free units left, the margin, score and gates), price_check, and forum mentions per SKU (a
    hint only: the choice never reads them)."""
    held = Counter(u.get("sku") for u in _rows(_dict(inv).get("units")) if u.get("grade") != "expired")
    nxt, tnxt = [_name(s) for s in f.get("next") or []], [_name(s) for s in f.get("trend_next") or []]
    keys = ("rate6", "rate1", "share", "cover_h", "shops", "p_clear", "f_now", "free_left", "margin", "score")
    return {"current": [_name(s) for s in f.get("current") or []], "next": nxt,
            "trend_current": [_name(s) for s in f.get("trend_current") or []], "trend_next": tnxt,
            "covered_h": _num(f.get("covered_h")),
            "candidates": [{"sku": _name(r.get("sku")), "kind": _name(r.get("kind")),
                            "list": "F" if r.get("sku") in nxt else "T" if r.get("sku") in tnxt else None,
                            "held": held[r.get("sku")]} | {k: _num(r.get(k)) for k in keys}
                           | {"gates": [_name(g) for g in r.get("gates") or []]}
                           for r in _rows(f.get("candidates"))[:FOCUS_TOP]],
            "price_check": [{"sku": _name(r.get("sku"))} | {k: _num(r.get(k)) for k in
                                                            ("held", "s6", "p_clear", "our_floor_list")}
                            | {"judged": r.get("judged") is True} for r in _rows(f.get("price_check"))[:6]],
            "forum_mentions": {_name(k): _num(v) for k, v in _dict(f.get("forum_mentions")).items()}}


# --- economics (plan 11-06 section 3(d)) and the market mix, from the SKU tables

def _economics(tables, own, pol, market, catalog, now):
    """Line budget (supply lines and units of the window from the kit log, SKUs held, refill_h = SKUs held / lines
    per hour), turnover (24 h cost of goods sold / stock cost, per day; scaled to a day while the sums span
    day_h < 24 hours, None under TURN_MIN_H), unsold goods (goods units held 24 h or more
    of SKUs we sold none of in 24 h, as a share of stock cost), spoilage (24 h expired cost / cost of goods sold),
    the big-ticket budget (goods listed above stock.big_ticket_list: held cost against stock.big_ticket_share x
    inventory_cap_share x (cash + stock cost)), the free surge units left on our SKUs, p_clear per kind against our
    floor/list, and the expansion conditions. None without tables.
    capital: the D1 headroom as restock computes it, what is left of the big-ticket budget, the storage cost a day
    (market fact storage_pct_day) and the carry (storage a day / 24 h margin at the goods p_clear): what one more
    unit of stock costs against what it earns. stockouts: big-ticket SKUs we sold in 6 h (o6) and hold at most one
    unit of (their ACP sessions in the window: created, completed), with the market's 6 h settles (s6); demand they
    carried that our shelf cannot serve (sold_out_share: of the big-ticket SKUs we sold, the share sold out); and
    slow lines (held cost whose cover at our 24 h rate is over SLOW_COVER_H hours, or that sold none)."""
    t = _dict(tables)
    if not t:
        return None
    inv, st, se = _dict(own.get("inventory")), _dict(_dict(pol).get("stock")), _dict(_dict(pol).get("seller"))
    units = [u for u in _rows(inv.get("units")) if u.get("grade") != "expired"]
    cost = lambda us: round(sum(_num(u.get("cost")) or 0 for u in us))
    inv_cost, balance = cost(units), _num(inv.get("balance"))
    kit, wh = _dict(own.get("kit")), _num(own.get("window_h")) or 1
    lines, got = _num(kit.get("supply_bought")), _num(kit.get("supply_units"))
    every = _num(_dict(_dict(market).get("facts")).get("supply_every_min")) or 20
    skus = len({u.get("sku") for u in units})
    lines_h = lines / wh if lines is not None else None
    day = _dict(t.get("day"))
    o24 = _dict(day.get("o"))
    cogs = _num(day.get("cogs")) or 0
    unsold = [u for u in units if u.get("kind") == "goods" and not o24.get(u.get("sku"))
              and now - (_num(u.get("supplied_at")) or now) >= 86400]
    bmax = _num(st.get("big_ticket_list"))
    big = [u for u in units if u.get("kind") == "goods" and bmax is not None
           and (_list_price(catalog, u.get("sku")) or 0) > bmax]
    share = _num(st.get("big_ticket_share")) if "big_ticket_share" in st else 0.40
    cap = _num(st.get("inventory_cap_share"))
    budget = round(share * cap * (balance + inv_cost)) if None not in (share, cap, balance) else None
    # per day: the 24 h sums scaled up while they span less (None under TURN_MIN_H: too few hours to say)
    day_h = _num(day.get("h"))
    turnover = round(cogs / inv_cost * 24 / day_h, 2) if inv_cost and day_h and day_h >= TURN_MIN_H else None
    unsold_share = round(cost(unsold) / inv_cost, 3) if inv_cost else None
    spoil = round((_num(day.get("exp")) or 0) / cogs, 4) if cogs else None
    sp = _dict(_dict(market).get("supply_prices"))
    free = _num(sp.get("free")) if _num(sp.get("free")) is not None else 50
    su, rows = _dict(_dict(market).get("surge_units")), _dict(t.get("sku"))
    u1h = {s: max(_num(_dict(rows.get(s)).get("u1")) or 0, _num(su.get(s)) or 0) for s in {u.get("sku") for u in units}}
    left = sorted(((s, free - u) for s, u in u1h.items() if u > 0 and isinstance(s, str)), key=lambda x: x[1])
    pc, ck = _dict(t.get("p_clear")), _dict(t.get("c_kind"))
    fl, fcm = _num(se.get("floor_list")), _num(se.get("floor_cost_mult"))
    kinds = {}
    for k in ("food", "goods"):
        c = _num(ck.get(k))
        kinds[k] = {"p_clear_6h": _num(_dict(pc.get(k)).get("h6")), "p_clear_24h": _num(_dict(pc.get(k)).get("h24")),
                    "n_24h": _num(_dict(pc.get(k)).get("n24")), "our_cost_list": c,
                    "our_floor_list": round(max(fl, fcm * (c or C_FALLBACK[k])), 3) if fl is not None and fcm else None}
    cond = {"turnover_day": turnover, "unsold_goods_share": unsold_share, "spoilage_share": spoil, "balance": balance}
    capital = _capital(units, big, balance, inv_cost, budget, st, market, kinds, day_h, cogs, _num(day.get("rev")))
    stockouts = _stockouts(big, rows, o24, day_h, _rows(own.get("acp")), catalog, bmax)
    ok = {k: (v >= EXPAND[k] if k in ("turnover_day", "balance") else v <= EXPAND[k] if k == "unsold_goods_share"
              else v < EXPAND[k]) if v is not None else None for k, v in cond.items()}
    return {"covered_h": _num(t.get("covered_h")),
            "lines": {"lines_h": _r(lines_h, 2), "lines_window": round(lines_h * every / 60, 1) if lines_h else None,
                      "units_line": round(got / lines, 1) if lines and got is not None else None, "skus_held": skus,
                      "refill_h": round(skus / lines_h, 2) if lines_h else None},
            "stock_cost": inv_cost, "cogs_24h": round(cogs), "rev_24h": _num(day.get("rev")), "day_h": day_h,
            "big_ticket": {"skus": len({u.get("sku") for u in big}), "held_cost": cost(big), "budget": budget,
                           "of_budget": round(cost(big) / budget, 3) if budget else None, "share_key": share},
            "surge_free_left": {_name(s): v for s, v in left[:FREE_TOP]},
            "kinds": kinds, "expand": cond, "expand_ok": ok, "expand_all_ok": all(v is True for v in ok.values()),
            "capital": capital, "stockouts": stockouts}


def _capital(units, big, balance, inv_cost, budget, st, market, kinds, day_h, cogs, rev):
    """See _economics. headroom = min(cap x (cash + stock) - stock, cash - cash_floor), as restock's D1; margin_24h =
    (revenue - cost of goods sold) / cost of goods sold over the 24 h sums; carry_day = storage a day / stock cost;
    hold_vs_margin = carry_day x 24 / (margin_24h / (day_h / 24)): above 1 a day of holding costs more than selling
    earns, so clearing stale goods below cost pays (None without the numbers)."""
    cap, floor_ = _num(st.get("inventory_cap_share")), _num(st.get("cash_floor"))
    cost = lambda us: round(sum(_num(u.get("cost")) or 0 for u in us))
    goods = cost([u for u in units if u.get("kind") == "goods"])
    pct = _num(_dict(_dict(market).get("facts")).get("storage_pct_day"))
    storage = round(goods * pct / 100) if pct is not None else None
    headroom = (round(max(0, min(cap * (balance + inv_cost) - inv_cost, balance - (floor_ or 0))))
                if None not in (cap, balance) else None)
    margin = round((rev - cogs) / cogs, 3) if cogs and rev is not None else None
    carry = round(storage / inv_cost, 4) if storage is not None and inv_cost else None
    hold = (round(carry / (margin * 24 / day_h), 2) if carry is not None and margin and margin > 0 and day_h
            else None)
    return {"cash": balance, "stock_cost": inv_cost, "goods_cost": goods, "cash_floor": floor_, "headroom": headroom,
            "big_ticket_free": round(budget - cost(big)) if budget is not None else None,
            "storage_pct_day": pct, "storage_cost_day": storage, "margin_24h": margin, "carry_day": carry,
            "hold_vs_margin": hold, "stock_at_cost": _dict(_dict(market).get("facts")).get("book_counts_stock_at_cost")}


def _stockouts(big, rows, o24, day_h, acp, catalog, bmax):
    """See _economics. big: our big-ticket units; rows: table_view's sku table (s6 market settles, o6 ours); acp: the
    window's ACP session rows (event create/complete per sku); bmax: stock.big_ticket_list, so a sold-out SKU we no
    longer hold counts only when the catalog lists it as goods above that line."""
    held = Counter(u.get("sku") for u in big)
    cost = Counter()
    for u in big:
        cost[u.get("sku")] += _num(u.get("cost")) or 0
    big_skus = set(held) | {s for s, r in rows.items() if (_num(_dict(r).get("o6")) or 0) > 0 and isinstance(s, str)
                            and bmax is not None and (_list_price(catalog, s) or 0) > bmax
                            and _kind_of(catalog, s) == "goods" and s not in held}
    sess = {}
    for r in acp:
        s, ev = r.get("sku"), r.get("event")
        if isinstance(s, str) and ev in ("create", "complete"):
            sess.setdefault(s, Counter())[ev] += 1
    sold = [s for s in big_skus if (_num(_dict(rows.get(s)).get("o6")) or 0) > 0]
    out = []
    for s in sold:
        if held[s] > 1:
            continue
        r, lp = _dict(rows.get(s)), _list_price(catalog, s)
        out.append({"sku": _name(s), "list": lp, "held": held[s], "o6": _num(r.get("o6")), "s6": _num(r.get("s6")),
                    "sessions": {k: sess.get(s, Counter())[k] for k in ("create", "complete")}})
    out.sort(key=lambda x: -((x["o6"] or 0) * (x["list"] or 0)))
    rate_d = lambda s: (o24.get(s) or 0) / day_h * 24 if day_h else None
    slow = []
    for s, n in held.items():
        if n < 2:
            continue
        rd = rate_d(s)
        cover = round(n / rd * 24, 1) if rd else None
        if cover is None or cover > SLOW_COVER_H:
            slow.append({"sku": _name(s), "held": n, "cost": round(cost[s]), "sold_24h": o24.get(s) or 0, "cover_h": cover})
    slow.sort(key=lambda x: -x["cost"])
    return {"big_skus_sold_6h": len(sold), "sold_out": len(out),
            "sold_out_share": round(len(out) / len(sold), 2) if sold else None,
            "winners_sold_out": out[:STOCKOUT_TOP], "slow_lines": slow[:SLOW_TOP],
            "slow_cost": round(sum(x["cost"] for x in slow))}


def _list_price(catalog, sku):
    c = catalog.get(sku) if isinstance(sku, str) else None
    return _num(c[2]) if isinstance(c, list) and len(c) > 2 else None


def _kind_of(catalog, sku):
    c = catalog.get(sku) if isinstance(sku, str) else None
    return c[3] if isinstance(c, list) and len(c) > 3 and isinstance(c[3], str) else None


def _mix(tables, raw, catalog):
    """The category leaves the market settles most over 6 h (per hour, our share, /api/stats deals per hour since
    the last run) and the top 3 shops' review counts by SKU and leaf. None without either."""
    t = _dict(tables)
    rows, h6 = _dict(t.get("sku")), _num(t.get("h6")) or 6
    leaves = {}
    for sku, r in rows.items():
        c = catalog.get(sku)
        leaf = str(c[1] or "").split(">")[-1] if isinstance(c, list) and len(c) > 1 else None
        if leaf and isinstance(r, dict):
            x = leaves.setdefault(leaf, [0, 0])
            x[0] += _num(r.get("s6")) or 0
            x[1] += _num(r.get("o6")) or 0
    ld = _dict(raw.get("leaf_deltas"))
    dn, dh = _dict(ld.get("n")), _num(ld.get("h"))
    top = sorted(leaves.items(), key=lambda kv: -kv[1][0])[:LEAF_TOP]
    out = {"leaves": [{"leaf": _clip(k, 30), "rate6": round(v[0] / h6, 1), "share": _ratio(v[1], v[0]),
                       "stats_deals_h": round(dn.get(k, 0) / dh, 1) if dh else None} for k, v in top]}
    tr = _dict(raw.get("top_reviews"))
    if tr:
        out["top3_reviews"] = {"shops": _num(tr.get("shops")), "reviews": _num(tr.get("reviews")),
                               "sku": {_name(k): _num(v) for k, v in list(_dict(tr.get("sku")).items())[:8]},
                               "leaf": {_clip(k, 30): _num(v) for k, v in list(_dict(tr.get("leaf")).items())[:5]},
                               "paid_list_median": _num(tr.get("paid_list_median"))}
    return out if out["leaves"] or tr else None


# --- score parts, list price bands, our seller and buyer logs

def _ledger_h(raw, ledger, window_h):
    """Hours the ledger rows span: since the last run's read (collect: ledger_h), at most window_h. A first read
    holds only the newest rows: the time they span. Unknown: window_h."""
    h = _num(raw.get("ledger_h"))
    if h and h > 0:
        return round(min(window_h, h), 4)
    ts = [r["ts"] for r in ledger if _num(r.get("ts")) is not None]
    if raw.get("ledger_first") and ts:
        return round(min(window_h, max((max(ts) - min(ts)) / 3600, 1 / 60)), 4)
    return window_h


def _score(shops, buyers, me, weights):
    """Per side, each ALL part: our value, pct (the share of the other ranked rows strictly below ours, approximate:
    the market's pool may differ), and with the market's weights w and gain = w * (1 - pct), the most ALL a
    part can still add, highest first. recon = sum(w * pct), shown next to the board's all."""
    out = {}
    for side, rows in (("shop", shops), ("buyer", buyers)):
        mine = _find(rows, me)
        if not mine:
            continue
        others = [r for r in _pool(rows, mine) if r is not mine]
        w = _dict(weights.get(side))
        parts = []
        for f in (w or SCORE_F[side]):
            v = _num(mine.get(f))
            vals = [x for x in (_num(r.get(f)) for r in others) if x is not None]
            pct = round(sum(1 for x in vals if v is not None and x < v) / len(vals), 4) if vals and v is not None else None
            part = {"f": _name(f), "value": _r(v), "pct": pct}
            if _num(w.get(f)) is not None:
                part |= {"w": w[f], "gain": round(w[f] * (1 - pct), 4) if pct is not None else None}
            parts.append(part)
        if w:
            parts.sort(key=lambda p: -(p["gain"] or 0))
        d = {"parts": parts, "all": _r(_num(mine.get("all"))), "pool": len(others) + 1}
        if w and all(p["pct"] is not None for p in parts):
            d["recon"] = round(sum(p["w"] * p["pct"] for p in parts), 4)
        out[side] = d
    return out


def _bands(buckets, now):
    """Market and our settles of the last hour by list price band, from the 10-minute ledger buckets (None
    without any). rev_share: the band's part of all market (or all our) settle revenue."""
    tot = collect.bucket_total(_dict(buckets), now - 3600, now + collect.BUCKET_S)
    if not tot:
        return None
    rev = sum(tot[f"rev.b{i}"] for i in range(len(BAND_NAMES))) or None
    ours = sum(tot[f"ours_rev.b{i}"] for i in range(len(BAND_NAMES))) or None
    rows = [{"band": name, "mkt_settles": tot[f"settle.b{i}"], "mkt_rev_share": _ratio(tot[f"rev.b{i}"], rev),
             "our_settles": tot[f"ours.b{i}"], "our_rev_share": _ratio(tot[f"ours_rev.b{i}"], ours)}
            for i, name in enumerate(BAND_NAMES)]
    return {"h": 1, "settles": tot["settle"], "ours": tot["ours"],
            "rows": [r for r in rows if r["mkt_settles"] or r["our_settles"]]}


def _seller(rows):
    """Our seller's window: tasks answered, signed (and the rate), how the rest ended, warranty asked and sold,
    and the median price/list of signs and offer/list of the offers we rejected."""
    act = lambda r: _dict(r.get("choice")).get("action")
    why = lambda r: str(r.get("why") or "")
    inp = lambda r: _dict(r.get("inputs"))
    first = [r for r in rows if r.get("kind") == "quote" and why(r).startswith("first reply")]
    signs = [r for r in rows if r.get("kind") == "sign"]
    rejects = [r for r in rows if r.get("kind") == "reject" and act(r) == "reject"]
    per_list = lambda xs, f: _median([round(p / lp, 4) for p, lp in ((f(r), _num(inp(r).get("list_price")))
                                                                     for r in xs) if _num(p) and lp])
    return {"first": len(first), "signed": len(signs), "sign_rate": _ratio(len(signs), len(first)),
            "canceled": sum(1 for r in rows if act(r) == "canceled"),
            "final": sum(1 for r in rows if act(r) == "final"), "rejected": len(rejects),
            "no_price": sum(1 for r in rows if why(r) == "no price in the message"),
            "no_stock": sum(1 for r in rows if why(r).startswith("no unit in stock")),
            "llm_exception": sum(1 for r in rows if r.get("kind") == "llm_exception"),
            "warranty_req": sum(1 for r in first if inp(r).get("warranty_req")),
            "warranty_sold": sum(1 for r in signs if (_num(_dict(r.get("choice")).get("warranty_hours")) or 0) > 0),
            "sign_price_list": per_list(signs, lambda r: _dict(r.get("choice")).get("price")),
            "reject_offer_list": per_list(rejects, lambda r: inp(r).get("offer"))}


def _buyer_log(rows):
    """Our buyer's orders of the window by kind (food, goods): orders, deals, conversion, orders with no
    candidate shop; and the shop tries whose receipt failed our check (receipt_problem). Its text names
    shop-chosen items, so the classes (digits removed) are untrusted."""
    orders = [r for r in rows if r.get("kind") == "order"]
    out = {}
    for k in ("food", "goods"):
        rs = [r for r in orders if _dict(r.get("inputs")).get("kind") == k]
        deals = sum(1 for r in rs if isinstance(r.get("choice"), dict))
        out[k] = {"orders": len(rs), "deals": deals, "conv": _ratio(deals, len(rs)),
                  "no_candidates": sum(1 for r in rs if not r.get("candidates"))}
    problems = Counter(_clip(re.sub(r"\d+", "#", str(t["receipt_problem"])), 60) for r in orders
                       for t in _rows(r.get("tried")) if t.get("receipt_problem"))
    out["tries"] = sum(len(_rows(r.get("tried"))) for r in orders)
    out["receipt_problems"] = sum(problems.values())
    out["untrusted_receipt_problem_why"] = [{"n": n, "why": w} for w, n in problems.most_common(3)]
    return out


# --- size

def size(b):
    return len(json.dumps(b, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _fit(b):
    """Drop the least useful detail first until the brief is under LIMIT; b["trimmed"] counts the steps."""
    def pitches(n):
        for r in [r for rows in b["leaders"].values() for r in rows]:
            if "untrusted_pitch" in r:
                r["untrusted_pitch"] = r["untrusted_pitch"][:n]
                if not n:
                    r.pop("untrusted_pitch")

    def cut(path, n):
        d = b
        for k in path[:-1]:
            d = _dict(d.get(k))
        if isinstance(d.get(path[-1]), list):
            d[path[-1]] = d[path[-1]][:n]

    steps = [lambda: cut(("buyer_log", "untrusted_receipt_problem_why"), 1), lambda: b["ops"].get("restock") and
             b["ops"]["restock"].pop("surge", None), lambda: b["bands"] and cut(("bands", "rows"), 4),
             lambda: cut(("prices",), 10), lambda: pitches(80), lambda: cut(("forum", "untrusted_top"), 3),
             lambda: cut(("ops", "kit", "untrusted_pay_refused_why"), 2), lambda: cut(("ops", "restock", "buy"), 2),
             lambda: cut(("reviews_of_us", "untrusted_last"), 3), lambda: cut(("demand", "top"), 5),
             lambda: cut(("prices",), 6), lambda: pitches(0),
             lambda: [r.pop("name", None) for r in b["prices"]],
             lambda: cut(("leaders", "by_profit"), 3), lambda: cut(("leaders", "by_deals"), 3),
             lambda: b["leaders"].get("by_all") and cut(("leaders", "by_all"), 3),
             lambda: cut(("forum", "untrusted_top"), 0), lambda: cut(("reviews_of_us", "untrusted_last"), 0),
             lambda: cut(("ops", "kit", "untrusted_pay_refused_why"), 0), lambda: cut(("ops", "restock", "buy"), 0),
             lambda: cut(("mix", "leaves"), 5), lambda: cut(("focus", "price_check"), 3),
             lambda: b.get("mix") and b["mix"].pop("top3_reviews", None),
             lambda: cut(("inventory", "top"), 5), lambda: cut(("focus", "candidates"), 6),
             lambda: cut(("inventory", "top"), 3), lambda: cut(("focus", "candidates"), 3)]
    b["trimmed"] = 0
    for step in steps:
        if size(b) < LIMIT:
            break
        step()
        b["trimmed"] += 1
    return b


def brief(raw, own, prev, pol, now, me=ME, window_h=1, flows=None, known=(), focus=None, buckets=None,
          weights=None, tables=None, market=None):
    """The brief for this run. raw from collect.fetch_public, own from collect.read_own, prev the last brief
    (or None), pol the policy dict on disk. From run.py: flows, the inventory moves since the last run; known,
    the unit ids held at the last run; focus, {current, next, trend_current, trend_next, covered_h, candidates,
    price_check, forum_mentions}; buckets, state["ledger_buckets"]; weights, {"shop": {field: w}, "buyer": {...}}
    from the market's rules; tables, collect.table_view of state["sku_tables"]; market, facts.collect's dict.
    JSON-serialisable, under LIMIT bytes."""
    raw, own = _dict(raw), _dict(own)
    board = _dict(raw.get("board"))
    shops, buyers, players = _rows(board.get("shops")), _rows(board.get("buyers")), _rows(board.get("players"))
    url = our_url(board, me)
    since = now - window_h * 3600
    ledger = [r for r in _rows(raw.get("ledger")) if (_num(r.get("ts")) or 0) >= since]
    catalog = _dict(raw.get("catalog"))
    end_ts = _num(_dict(_dict(pol).get("stock")).get("end_ts"))
    ledger_h = _ledger_h(raw, ledger, window_h)
    b = {"round": board.get("round", _dict(raw.get("stats")).get("round")), "ts": round(now, 3),
         "window_h": window_h, "ledger_h": ledger_h,
         "hours_left": round((end_ts - now) / 3600, 1) if end_ts is not None else None,
         "me": _me(shops, buyers, players, me)}
    b["delta"] = _delta(b["me"], prev, now)
    b["score"] = _score(shops, buyers, me, _dict(weights))
    b["leaders"] = _leaders(shops, me)
    b["market"] = _market(board, raw.get("stats"))
    b["prices"] = _prices(raw.get("trades"), ledger, catalog, since, me, url)
    b["market_window"] = _window(ledger, me, url, raw.get("ledger_gap"), raw.get("ledger_first"))
    b["bands"] = _bands(buckets, now)
    b["demand"] = _demand(own.get("summary"))
    b["ops"] = _ops(own, raw.get("forum"), since, me)
    dec = _dict(own.get("decisions"))
    b["seller"] = _seller(_rows(dec.get("seller")))
    b["buyer_log"] = _buyer_log(_rows(dec.get("buyer")))
    b["forum"] = _forum(raw.get("forum"), since)
    b["reviews_of_us"] = _reviews(raw.get("shop_reviews"))
    b["inventory"] = _inventory(own.get("inventory"), ledger, url, me, pol, now, ledger_h, flows, known)
    if focus:
        b["focus"] = _focus(focus, own.get("inventory"))
    try:                                                # derived numbers: a strange input must not stop the brief
        b["economics"] = _economics(tables, own, pol, market, catalog, now)
        b["mix"] = _mix(tables, raw, catalog)
    except Exception as e:
        b["economics"] = b["mix"] = None
        b["_errors"] = [f"economics: {type(e).__name__}: {str(e)[:120]}"]
    errs = (raw["errors"] if isinstance(raw.get("errors"), list) else []) + b.pop("_errors", [])
    b["errors"] = [_clip(e, 200) for e in errs if isinstance(e, str)][:20]
    return _fit(b)
