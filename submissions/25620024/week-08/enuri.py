"""Week 08 — enuri.py: the Enuri team (named after the Korean price-comparison site), the buyer's scouts.
It never buys. It reads prices, quality and the forum, keeps a book the buyer reads before every order, and
scores its own forecasts so it gets better (self-improvement: forecast -> compare -> recalibrate).

  price    per SKU: the ledger's 24 h low and median (all shops), and per shop its last settles and asks
  quality  per shop: grade claims the ledger found true, units that broke after sale, refunds and stars (board);
           a shop below the bars gets verdict "avoid" and the buyer skips it
  forum    price posts that cite ledger entries (regular expressions only: no forum text reaches a model),
           shops' "in stock" ads with a price, and posts that try to instruct agents (flagged, never followed)
  asks     window-shopping (survey, run by the team orchestrator): opens a quote with a few shops for SKUs
           the buyer often needs and the book knows little about, reads the first ask, cancels. Nothing is bought.
           SKUs the marketing team asks about (private/enuri_requests.json) go first: the two teams are allies,
           marketing gets competitor asks it cannot see on the ledger, the Enuri team learns which SKUs matter.

The book predicts what the buyer will pay at each shop: median ask x k[kind]. Every tick it scores the
predictions against the buyer's deals since the last tick and moves k toward what happened (EMA). The
buyer team (Codex, buyer_team.py) reads the scorecard and may change the bars and the survey size.

    private/enuri_book.json      the book (written by tick, as the shop user)
    private/enuri_asks.json      asks from window-shopping (written by survey)
    private/enuri/state.json     calibration, last scored trade, scorecard history
    private/enuri_requests.json  SKUs the marketing team wants priced ({"skus": [...], "from": "marketing", "ts"})

summary(book) is the compact view the Codex teams read (buyer team, marketing research, sales team).
Review fixes (2026-11-04): quality() ran twice per tick; broken entries were joined to settle units by the broken
entry's own seq (it has no unit), so no shop ever had a breakage; a broken unit is now charged to the shop that last
sold that SKU to that buyer.
"""
import json
import re
import statistics
import time
from pathlib import Path

import market_model

HERE = Path(__file__).parent
BOOK = HERE / "private" / "enuri_book.json"
ASKS = HERE / "private" / "enuri_asks.json"
STATE = HERE / "private" / "enuri" / "state.json"
PLAN = HERE / "private" / "buyer_plan.json"
REQUESTS = HERE / "private" / "enuri_requests.json"
MEMORY = HERE / "memory.jsonl"
DEFAULTS = {"min_honest": 0.8, "max_broken": 0.15, "min_n": 5, "survey_quotes": 4, "tick_s": 300, "alpha": 0.3}
PRICE_POST = re.compile(r"sku (G[0-9a-f]{8})\D{0,40}?(\d{3,7})", re.I)
INJECTION = re.compile(r"ignore (?:all|previous|your)|AI (?:buyers?|agents?) (?:must|should)|system prompt|"
                       r"pay the (?:asking|full) price|give (?:it|us) 5 stars|approved by the (?:instructor|operator)", re.I)


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def settings():
    """DEFAULTS, overridden by the buyer team's plan ("enuri" section)."""
    return {**DEFAULTS, **(load(PLAN, {}).get("enuri") or {})}


def prices(c, hours=24):
    """{sku: {low, median, n, shops: {shop: {n, median, last}}}} from settles of every shop (names via supply units)."""
    rows = c.execute("""select su.shop, s.sku, s.amount, s.ts from settles s join settle_units x on x.seq = s.seq
                        join supply_units su on su.unit = x.unit where s.ts > ? and s.amount > 0 order by s.ts""",
                     (time.time() - hours * 3600,)).fetchall()
    by = {}
    for shop, sku, amount, ts in rows:
        by.setdefault(sku, {}).setdefault(shop, []).append(amount)
    out = {}
    for sku, shops in by.items():
        allp = [a for v in shops.values() for a in v]
        out[sku] = {"low": min(allp), "median": statistics.median(allp), "n": len(allp),
                    "shops": {s: {"n": len(v), "median": statistics.median(v), "last": v[-1]} for s, v in shops.items()}}
    return out


def quality(c, md, t, hours=24):
    """{shop: {deals, honest, broken, stars, refunds, verdict}} from the ledger and the board copy in shops.json."""
    since = time.time() - hours * 3600
    rows = c.execute("""select su.shop, count(*), sum(s.claim_honest), sum(s.claim_honest is not null)
                        from settles s join settle_units x on x.seq = s.seq join supply_units su on su.unit = x.unit
                        where s.ts > ? group by su.shop""", (since,)).fetchall()
    # a broken entry names the buyer and the SKU, not the unit: charge it to the shop that last sold it to them
    broke = dict(c.execute("""select (select su.shop from settles s join settle_units x on x.seq = s.seq
                                      join supply_units su on su.unit = x.unit
                                      where s.buyer = b.buyer and s.sku = b.sku and s.ts <= b.ts
                                      order by s.ts desc limit 1) as shop, count(*)
                              from broken b where b.ts > ? group by shop""", (since,)).fetchall())
    board = {s.get("name"): s for s in (load(Path(md) / "shops.json", []) or []) if isinstance(s, dict)}
    out = {}
    for shop, n, honest, checked in rows:
        h = round(honest / checked, 3) if checked else None
        br = round(broke.get(shop, 0) / n, 3) if n else 0
        verdict, why = "ok", None
        if n >= t["min_n"] and h is not None and h < t["min_honest"]:
            verdict, why = "avoid", f"honest grade claims {h:.0%} < {t['min_honest']:.0%} of {n} sales"
        elif n >= t["min_n"] and br > t["max_broken"]:
            verdict, why = "avoid", f"broke after sale {br:.0%} > {t['max_broken']:.0%} of {n} sales"
        b = board.get(shop) or {}
        out[shop] = {"deals": n, "honest": h, "broken": br, "stars": b.get("stars"), "refunds": b.get("refunds"),
                     "verdict": verdict, "why": why}
    return out


def forum_notes(md, now, hours=6):
    """Price mentions per SKU from posts that cite ledger entries; shops' in-stock ads; injection attempts."""
    posts = load(Path(md) / "forum.json", [])
    posts = posts.get("posts", posts) if isinstance(posts, dict) else posts
    cited, ads, flagged = {}, [], []
    for p in posts or []:
        if now - (p.get("ts") or 0) > hours * 3600 or p.get("author") == "dragonash":
            continue
        text = p.get("text") or ""
        if INJECTION.search(text):
            flagged.append({"id": p.get("id"), "author": p.get("author")})
            continue
        for sku, price in PRICE_POST.findall(text):
            if p.get("refs"):
                cited.setdefault(sku, []).append(int(price))
            if "in stock" in text.lower() or p.get("as") == "shop":
                ads.append({"sku": sku, "price": int(price), "author": p.get("author")})
    return {"cited_prices": {k: {"low": min(v), "n": len(v)} for k, v in cited.items()}, "ads": ads[-30:],
            "flagged": flagged[-20:]}


def trades_since(ts):
    out = []
    try:
        with MEMORY.open(encoding="utf-8") as f:
            for line in f:
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("ts", 0) > ts and m.get("outcome") == "deal" and m.get("agreed") and str(m.get("item", "")).startswith("G"):
                    out.append(m)
    except OSError:
        pass
    return out


def predict(book, shop, sku, kind=None, k=None):
    """What the buyer will pay at this shop for this SKU: the shop's own median (settles or asks), else the market's,
    times k[kind] (the calibration; the book's own k when none is given). None when the book knows nothing."""
    k = k if k is not None else (book.get("k") or {})
    p = (book.get("prices") or {}).get(sku) or {}
    own = (p.get("shops") or {}).get(shop) or {}
    ask = ((book.get("asks") or {}).get(sku) or {}).get(shop)
    base = own.get("median") or (ask and ask.get("ask")) or p.get("median")
    return round(base * k.get(kind or "goods", 1.0)) if base else None


def score(state, old_book, t):
    """Score the last book against the buyer's deals since, and move k[kind] toward paid / predicted."""
    k = state.setdefault("k", {"goods": 1.0, "food": 1.0})
    deals = trades_since(state.get("scored_to", time.time() - 3600))
    errs, by_kind = [], {}
    for m in deals:
        pred = predict(old_book, m["shop"], m["item"], m.get("kind"), k)
        if pred:
            errs.append((m["agreed"] - pred) / pred)
            by_kind.setdefault(m.get("kind") or "goods", []).append(m["agreed"] / pred)
    for kind, rs in by_kind.items():
        k[kind] = round(max(0.5, min(1.5, k.get(kind, 1.0) * (1 - t["alpha"] + t["alpha"] * statistics.median(rs)))), 4)
    if deals:
        state["scored_to"] = max(m["ts"] for m in deals)
    card = {"ts": time.time(), "deals": len(deals), "predicted": len(errs),
            "mae": round(statistics.mean(abs(e) for e in errs), 4) if errs else None,
            "bias": round(statistics.median(errs), 4) if errs else None, "k": dict(k),
            # paid above the cheapest shop the book knew: surplus the buyer left on the table
            "missed": sum(1 for m in deals if ((old_book.get("prices") or {}).get(m["item"]) or {}).get("low")
                          and m["agreed"] > 1.05 * old_book["prices"][m["item"]]["low"])}
    state["cards"] = (state.get("cards", []) + [card])[-100:]
    return card


def tick(md=Path(".market"), c=None, now=None, force=False):
    """Rebuild the book (every tick_s seconds). Returns the scorecard of this tick, or None when not due."""
    t, now = settings(), now or time.time()
    state = load(STATE, {})
    if not force and now - state.get("last_tick", 0) < t["tick_s"]:
        return None
    c = c or market_model.db()
    old = load(BOOK, {})
    card = score(state, old, t) if old else None
    q = quality(c, md, t)
    book = {"ts": now, "prices": prices(c), "quality": q, "forum": forum_notes(md, now),
            "asks": load(ASKS, {}).get("asks", {}), "k": state.get("k", {"goods": 1.0, "food": 1.0}),
            "avoid": sorted(s for s, x in q.items() if x["verdict"] == "avoid")}
    BOOK.parent.mkdir(exist_ok=True)
    BOOK.write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    state["last_tick"] = now
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return card or {"note": "first book"}


def book():
    return load(BOOK, {})


def requested(max_age_h=6):
    """SKUs the marketing team asked us to price (newest request only, while fresh)."""
    r = load(REQUESTS, {})
    if time.time() - (r.get("ts") or 0) > max_age_h * 3600:
        return []
    return [x for x in (r.get("skus") or []) if isinstance(x, str) and re.fullmatch(r"G[0-9a-f]{8}", x)][:10]


def survey(md=Path(".market"), n=None, per_sku=2):
    """Window-shop: for SKUs marketing asked about, then SKUs the buyer often needs and the book knows little
    about, ask a few shops for a quote, read the first ask, cancel. Nothing is bought; no order is used.
    survey_quotes = 0 (the buyer team's switch) turns it off; marketing requests may raise it up to 8."""
    import httpx  # noqa: F401  (market_buyer needs it; fail here, not halfway)
    from concurrent.futures import ThreadPoolExecutor
    import market_buyer as mb
    n = settings()["survey_quotes"] if n is None else n
    asked = requested()
    if n <= 0:
        return []
    n = max(n, min(8, per_sku * len(asked)))
    shops = load(Path(md) / "shops.json", []) or []
    cat = load(Path(md) / "catalog.json", {})
    cat = {x["sku"]: x for x in cat} if isinstance(cat, list) else cat
    b, asks = book(), load(ASKS, {"asks": {}})
    want = {}
    for m in trades_since(time.time() - 24 * 3600) + _orders_seen(24):
        want[m["item"]] = want.get(m["item"], 0) + 1
    known = lambda sku: ((b.get("prices") or {}).get(sku) or {}).get("n", 0)
    targets = asked + [s for s in sorted(want, key=lambda s: (known(s), -want[s])) if s not in asked]
    jobs, fresh = [], time.time() - 3 * 3600
    for sku in targets:
        lp = (cat.get(sku) or {}).get("list_price")
        seen = (asks["asks"].get(sku) or {})
        held = [(s, o) for s in shops if s.get("name") != "dragonash"
                for o in (s.get("offers") or []) if o.get("sku") == sku and o.get("qty")]
        held.sort(key=lambda so: (seen.get(so[0]["name"]) or {}).get("ts", 0) > fresh)   # unpriced shops first
        if lp:
            jobs += [(0, s, o, lp) for s, o in held[:per_sku]]
        if len(jobs) >= n:
            break
    if not jobs:
        return []
    mb.NEED.clear()
    with ThreadPoolExecutor(max(1, min(n, 4))) as ex:
        quotes = list(ex.map(mb.quote, jobs[:n]))
    for q in quotes:
        _, s, o, _ = q["c"]
        if q.get("task"):
            try:
                mb.rpc(q["url"], "CancelTask", {"id": q["task"]["id"]})
            except Exception:
                pass
        if q.get("ask"):
            asks["asks"].setdefault(o["sku"], {})[s["name"]] = {"ask": q["ask"], "ts": time.time(),
                                                                "for": "marketing" if o["sku"] in asked else "buyer"}
    cut = time.time() - 24 * 3600
    asks["asks"] = {k: {s: v for s, v in d.items() if v.get("ts", 0) > cut} for k, d in asks["asks"].items()}
    asks["asks"] = {k: d for k, d in asks["asks"].items() if d}
    ASKS.parent.mkdir(exist_ok=True)
    ASKS.write_text(json.dumps(asks, ensure_ascii=False), encoding="utf-8")
    return [(q["c"][1]["name"], q["c"][2]["sku"], q.get("ask") or q.get("error")) for q in quotes]


def summary(b=None, top=15):
    """The book in a few hundred tokens for the Codex teams: where shops' prices for one SKU differ most (money the
    buyer keeps by picking the cheap shop, and what marketing can undercut), shops to avoid and why, forum prices,
    and the Enuri team's own last scorecards."""
    b = book() if b is None else b
    if not b:
        return {"note": "no book yet"}
    spreads = []
    for sku, p in (b.get("prices") or {}).items():
        shop_px = {s: v["median"] for s, v in (p.get("shops") or {}).items()}
        for s, v in ((b.get("asks") or {}).get(sku) or {}).items():
            shop_px.setdefault(s, v.get("ask"))
        shop_px = {s: x for s, x in shop_px.items() if x}
        if len(shop_px) >= 2:
            cheap = min(shop_px, key=shop_px.get)
            med = statistics.median(shop_px.values())
            spreads.append({"sku": sku, "cheapest_shop": cheap, "cheapest": round(shop_px[cheap]), "median": round(med),
                            "gap": round(med - shop_px[cheap]), "gap_share": round(1 - shop_px[cheap] / med, 3),
                            "shops": len(shop_px), "settles_24h": p.get("n")})
    spreads.sort(key=lambda x: -x["gap"])                     # absolute money, as the board counts surplus
    q = b.get("quality") or {}
    fo = b.get("forum") or {}
    cards = load(STATE, {}).get("cards", [])[-5:]
    return {"book_age_min": round((time.time() - b.get("ts", 0)) / 60), "skus_priced": len(b.get("prices") or {}),
            "shops_rated": len(q), "k": b.get("k"), "price_spreads": spreads[:top],
            "avoid": [{"shop": s, "why": (q.get(s) or {}).get("why")} for s in b.get("avoid") or []],
            "forum_cited_prices": dict(sorted((fo.get("cited_prices") or {}).items(),
                                              key=lambda kv: -kv[1].get("n", 0))[:top]),
            "forum_flagged_posts": len(fo.get("flagged") or []),
            "asks_by_sku": sum(len(d) for d in (b.get("asks") or {}).values()), "requests": requested(),
            "scorecards": [{k: x.get(k) for k in ("ts", "deals", "predicted", "mae", "bias", "missed")} for x in cards]}


def _orders_seen(hours):
    """SKUs from the buyer's recent orders that did not become deals (memory lines with any outcome)."""
    out, since = [], time.time() - hours * 3600
    try:
        with MEMORY.open(encoding="utf-8") as f:
            for line in f:
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("ts", 0) > since and m.get("outcome") != "deal" and str(m.get("item", "")).startswith("G"):
                    out.append(m)
    except OSError:
        pass
    return out


if __name__ == "__main__":
    import sys
    if "--survey" in sys.argv:
        for row in survey():
            print(row)
    print(json.dumps(tick(force=True), ensure_ascii=False))
    b = book()
    print(f"book: {len(b.get('prices', {}))} SKUs priced, {len(b.get('quality', {}))} shops rated, avoid {b.get('avoid')}, "
          f"{len(b['forum']['cited_prices'])} SKUs cited on the forum, {len(b['forum']['flagged'])} posts flagged")
    print(json.dumps(summary(b, top=5), ensure_ascii=False, indent=1))
