"""The last hour of raw market data and our own logs (seller, buyer, restock, forum, the kit's log),
turned into a compact brief for the model.

The model sees the brief, never the raw data, so the brief has to fit in a prompt (< 12 KB as JSON)
and carry numbers a change can be argued from: our rank among real shops and buyers, what moved since
the last run, prices against list price, why our deals fail. Text another agent wrote (shop pitches,
forum posts, reviews) is kept apart under keys starting with "untrusted_", cut short and with angle
brackets replaced, so decide.py can fence it off as data. Other shops' names and item names that are
not catalog SKUs are untrusted too: leaders carry a neutral id (L1, L2, ...) and the name only under
untrusted_name. Round 3 boards replace some Round 2 fields (ALL, surplus, forum, honest claims, refunds,
the players table); both sets are read. Any source may be missing; the brief then just lacks that part.
Our stock (inventory) and the focus SKUs (focus, chosen by strategist/focus.py) come from run.py.
"""
import json
import math
import re
import statistics
from collections import Counter

ME = "psyoung82"
LIMIT = 12000               # bytes of compact UTF-8 JSON
# Round 2 fields, then Round 3 ones (board.html econShops/econBuyers); a missing field is None.
SHOP_F = ("deals", "share", "visits", "stars", "reviews", "revenue", "profit",
          "all", "stars_rank", "claims", "honest_claims", "broken", "refunds")
BUYER_F = ("orders", "deals", "paid", "saved", "held", "hopeless", "all", "surplus", "forum", "served", "posts")
INV_TOP = 8                 # SKUs listed by cost tied up
FOOD_RISK_H = 24            # food with fewer hours left is at risk
FOCUS_TOP = 12


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


def _demand(summary, demand):
    """Our summary's demand vs supply by category when it has them, else the market's table by SKU count."""
    med = {}
    for d in _rows(demand):
        for k in (d.get("item"), str(d.get("category") or "").split(">")[-1]):
            if k:
                med.setdefault(k, d.get("median_price"))
    cats = _dict(_dict(summary).get("market_categories"))
    rows = []
    for k, v in cats.items():
        dem, sup = _num(_dict(v).get("demand")), _num(_dict(v).get("supply"))
        if dem is not None:
            rows.append({"category": _clip(k, 40), "demand": _r(dem, 2), "supply": sup,
                         "ratio": round((dem + 1) / ((sup or 0) + 1), 2), "median_price": med.get(k)})
    if rows:
        return {"source": "own summary", "top": sorted(rows, key=lambda r: -r["ratio"])[:10]}
    top = sorted(_rows(demand), key=lambda d: -(_num(d.get("skus")) or 0))[:10]
    return {"source": "market demand (by skus)",
            "top": [{"category": _clip(d.get("item") or d.get("category"), 40), "kind": _name(d.get("kind")),
                     "skus": _num(d.get("skus")), "median_price": _num(d.get("median_price"))} for d in top]}


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
                          "stock": {k: _num(held.get(k)) for k in ("skus", "units", "empty_picks")}}
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


def _inventory(inv, ledger, our_url, me, pol, now, window_h, flows, known):
    """Our stock from .market/inventory.json: cost against the D1 cap, the SKUs that tie up the most money (how
    old, food hours left, our sales per hour in the window, hours of cover at that rate), stale goods, food near
    expiry, expired units, and the units in / sold / gone unsold since the last run (None on the first run).
    Our settles are those to our shop URL or of a unit we hold or held at the last run (known)."""
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
        rate = sold[sku] / window_h
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
    """The focus list now and as this run chose it, the top candidates (with the units we hold of each) and
    forum mentions per SKU (a hint only: the choice never reads them)."""
    held = Counter(u.get("sku") for u in _rows(_dict(inv).get("units")) if u.get("grade") != "expired")
    return {"current": [_name(s) for s in f.get("current") or []], "next": [_name(s) for s in f.get("next") or []],
            "covered_h": _num(f.get("covered_h")),
            "candidates": [{k: r.get(k) for k in ("settles_6h", "suppliers_6h", "score")}
                           | {"sku": _name(r.get("sku")), "held": held[r.get("sku")]}
                           for r in _rows(f.get("candidates"))[:FOCUS_TOP]],
            "forum_mentions": {_name(k): _num(v) for k, v in _dict(f.get("forum_mentions")).items()}}


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

    steps = [lambda: cut(("prices",), 10), lambda: pitches(80), lambda: cut(("forum", "untrusted_top"), 3),
             lambda: cut(("ops", "kit", "untrusted_pay_refused_why"), 2), lambda: cut(("ops", "restock", "buy"), 2),
             lambda: cut(("reviews_of_us", "untrusted_last"), 3), lambda: cut(("demand", "top"), 5),
             lambda: cut(("prices",), 6), lambda: pitches(0),
             lambda: [r.pop("name", None) for r in b["prices"]],
             lambda: cut(("leaders", "by_profit"), 3), lambda: cut(("leaders", "by_deals"), 3),
             lambda: b["leaders"].get("by_all") and cut(("leaders", "by_all"), 3),
             lambda: cut(("forum", "untrusted_top"), 0), lambda: cut(("reviews_of_us", "untrusted_last"), 0),
             lambda: cut(("ops", "kit", "untrusted_pay_refused_why"), 0), lambda: cut(("ops", "restock", "buy"), 0),
             lambda: cut(("inventory", "top"), 5), lambda: cut(("focus", "candidates"), 6),
             lambda: cut(("inventory", "top"), 3), lambda: cut(("focus", "candidates"), 3)]
    b["trimmed"] = 0
    for step in steps:
        if size(b) < LIMIT:
            break
        step()
        b["trimmed"] += 1
    return b


def brief(raw, own, prev, pol, now, me=ME, window_h=1, flows=None, known=(), focus=None):
    """The brief for this run. raw from collect.fetch_public, own from collect.read_own, prev the last brief
    (or None), pol the policy dict on disk. From run.py: flows, the inventory moves since the last run; known,
    the unit ids held at the last run; focus, {current, next, covered_h, candidates, forum_mentions}.
    JSON-serialisable, under LIMIT bytes."""
    raw, own = _dict(raw), _dict(own)
    board = _dict(raw.get("board"))
    shops, buyers, players = _rows(board.get("shops")), _rows(board.get("buyers")), _rows(board.get("players"))
    our_url = str(_dict(_dict(_find(shops, me)).get("card")).get("url") or "").rstrip("/")
    since = now - window_h * 3600
    ledger = [r for r in _rows(raw.get("ledger")) if (_num(r.get("ts")) or 0) >= since]
    catalog = _dict(raw.get("catalog"))
    end_ts = _num(_dict(_dict(pol).get("stock")).get("end_ts"))
    b = {"round": board.get("round", _dict(raw.get("stats")).get("round")), "ts": round(now, 3),
         "window_h": window_h, "hours_left": round((end_ts - now) / 3600, 1) if end_ts is not None else None,
         "me": _me(shops, buyers, players, me)}
    b["delta"] = _delta(b["me"], prev, now)
    b["leaders"] = _leaders(shops, me)
    b["market"] = _market(board, raw.get("stats"))
    b["prices"] = _prices(raw.get("trades"), ledger, catalog, since, me, our_url)
    b["market_window"] = _window(ledger, me, our_url, raw.get("ledger_gap"), raw.get("ledger_first"))
    b["demand"] = _demand(own.get("summary"), raw.get("demand"))
    b["ops"] = _ops(own, raw.get("forum"), since, me)
    b["forum"] = _forum(raw.get("forum"), since)
    b["reviews_of_us"] = _reviews(raw.get("shop_reviews"))
    b["inventory"] = _inventory(own.get("inventory"), ledger, our_url, me, pol, now, window_h, flows, known)
    if focus:
        b["focus"] = _focus(focus, own.get("inventory"))
    b["errors"] = [_clip(e, 200) for e in raw.get("errors") or [] if isinstance(e, str)][:20]
    return _fit(b)
