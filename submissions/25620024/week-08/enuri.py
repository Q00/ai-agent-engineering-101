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

The book predicts what the buyer will pay at each shop: median ask x k[kind]. Every tick it scores the
predictions against the buyer's deals since the last tick and moves k toward what happened (EMA). The
buyer team (Codex, buyer_team.py) reads the scorecard and may change the bars and the survey size.

    private/enuri_book.json      the book (written by tick, as the shop user)
    private/enuri_asks.json      asks from window-shopping (written by survey)
    private/enuri/state.json     calibration, last scored trade, scorecard history
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
                        join supply_units su on su.unit = x.unit where s.ts > ? order by s.ts""",
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
    broke = dict(c.execute("""select su.shop, count(*) from broken b join settle_units x on x.seq = b.seq
                              join supply_units su on su.unit = x.unit where b.ts > ? group by su.shop""",
                           (since,)).fetchall())
    board = {s.get("name"): s for s in (load(Path(md) / "shops.json", []) or []) if isinstance(s, dict)}
    out = {}
    for shop, n, honest, checked in rows:
        h = round(honest / checked, 3) if checked else None
        br = round(broke.get(shop, 0) / n, 3) if n else 0
        verdict = "ok"
        if n >= t["min_n"] and ((h is not None and h < t["min_honest"]) or br > t["max_broken"]):
            verdict = "avoid"
        b = board.get(shop) or {}
        out[shop] = {"deals": n, "honest": h, "broken": br, "stars": b.get("stars"), "refunds": b.get("refunds"),
                     "verdict": verdict}
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


def predict(book, shop, sku, kind, k):
    """What the buyer will pay at this shop for this SKU: the shop's own median (settles or asks), else the market's."""
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
    book = {"ts": now, "prices": prices(c), "quality": quality(c, md, t), "forum": forum_notes(md, now),
            "asks": load(ASKS, {}).get("asks", {}), "k": state.get("k", {"goods": 1.0, "food": 1.0}),
            "avoid": sorted(s for s, q in quality(c, md, t).items() if q["verdict"] == "avoid")}
    BOOK.parent.mkdir(exist_ok=True)
    BOOK.write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    state["last_tick"] = now
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return card or {"note": "first book"}


def book():
    return load(BOOK, {})


def survey(md=Path(".market"), n=None):
    """Window-shop: for SKUs the buyer often needs and the book knows little about, ask a few shops for a quote,
    read the first ask, cancel. Nothing is bought; no order is used."""
    import httpx
    from concurrent.futures import ThreadPoolExecutor
    import market_buyer as mb
    n = settings()["survey_quotes"] if n is None else n
    if n <= 0:
        return []
    shops = load(Path(md) / "shops.json", []) or []
    cat = load(Path(md) / "catalog.json", {})
    cat = {x["sku"]: x for x in cat} if isinstance(cat, list) else cat
    b = book()
    want = {}
    for m in trades_since(time.time() - 24 * 3600) + [x for x in _orders_seen(24)]:
        want[m["item"]] = want.get(m["item"], 0) + 1
    known = lambda sku: ((b.get("prices") or {}).get(sku) or {}).get("n", 0)
    targets = sorted(want, key=lambda s: (known(s), -want[s]))
    jobs = []
    for sku in targets:
        for s in shops:
            o = next((o for o in s.get("offers") or [] if o.get("sku") == sku and o.get("qty")), None)
            lp = (cat.get(sku) or {}).get("list_price")
            if o and lp and s.get("name") != "dragonash":
                jobs.append((0, s, o, lp))
        if len(jobs) >= n:
            break
    mb.NEED.clear()
    with ThreadPoolExecutor(max(1, min(n, 4))) as ex:
        quotes = list(ex.map(mb.quote, jobs[:n]))
    asks = load(ASKS, {"asks": {}})
    for q in quotes:
        _, s, o, _ = q["c"]
        if q.get("task"):
            try:
                mb.rpc(q["url"], "CancelTask", {"id": q["task"]["id"]})
            except Exception:
                pass
        if q.get("ask"):
            asks["asks"].setdefault(o["sku"], {})[s["name"]] = {"ask": q["ask"], "ts": time.time()}
    cut = time.time() - 24 * 3600
    asks["asks"] = {k: {s: v for s, v in d.items() if v["ts"] > cut} for k, d in asks["asks"].items()}
    ASKS.parent.mkdir(exist_ok=True)
    ASKS.write_text(json.dumps(asks, ensure_ascii=False), encoding="utf-8")
    return [(q["c"][1]["name"], q["c"][2]["sku"], q.get("ask") or q.get("error")) for q in quotes]


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
