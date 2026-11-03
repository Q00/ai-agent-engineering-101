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

  where    a market-wide index from shops.json: every SKU any shop has in stock, who holds it, how many, its list
           price, so an order is never short of candidates because the buyer only knew the shops it traded with

advise(order, shops) is the buyer's shopping advice per order (round3 follows it): shops ranked by the surplus they
should leave (budget - expected price, the board's measure) times quality, the first-offer target and the most to
pay. Without a book the buyer falls back to its own ranking.

Accountability (the owner's rule: the Enuri team answers for every missed opportunity and every wrong purchase).
Every tick it judges the buyer's new memory lines against the book it gave, and the ledger since:
  missed   an order with no deal although the book knew a shop with stock in reach (list x min_reach <= budget),
           an order with no candidates whose SKU some shop held, a no-deal at the shop Enuri ranked first
  wrong    a deal with a receipt problem (wrong price or SKU), a false grade claim or a broken unit (ledger), a deal
           the kit never settled (food that would not last, grade or certificate refused), a deal at an avoid shop,
           a price above 1.05 x the book's low
  lost     the surplus those cost, in money. The book carries a 24 h line ("enuri_accountability"), every case
           raises a penalty on that shop that advise() ranks by, and summary() puts them first for the teams.

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
ME = "dragonash"
SHOPS_API = "https://market.ourolabs.ai/api/shops"
KIT_LOG = HERE / "logs" / "market-kit.log"
REFUSAL = re.compile(r"refus|declin|not paid|reject", re.I)
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
                    "shops": {s: {"n": len(v), "median": statistics.median(v), "low": min(v), "last": v[-1]}
                              for s, v in shops.items()}}
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


def lines_since(ts):
    """Every memory line after ts (any outcome), minus retracted ones."""
    out = []
    try:
        with MEMORY.open(encoding="utf-8") as f:
            for line in f:
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if (m.get("ts") or 0) > ts or "retracts" in m:
                    out.append(m)
    except OSError:
        pass
    gone = {m["retracts"] for m in out if "retracts" in m}
    return [m for m in out if "retracts" not in m and (m.get("ts") or 0) > ts and m.get("ts") not in gone]


def where(md):
    """Market-wide index from shops.json and the catalog: {sku: {list, kind, shops: [{shop, qty, url, name}]}},
    every SKU some shop has in stock (ours left out: the buyer does not buy from its own shop)."""
    shops = load(Path(md) / "shops.json", []) or []
    cat = market_model.catalog(md)
    idx = {}
    try:      # shops.json holds only the current order's SKUs; the market's /api/shops lists every shop's stock
        import httpx
        market = httpx.get(SHOPS_API, timeout=10).json().get("shops") or [] if SHOPS_API else []
    except Exception:
        market = []
    for s in market:
        if not isinstance(s, dict) or s.get("name") == ME:
            continue
        for sku, qty in (s.get("stock") or {}).items():
            if qty:
                r = cat.get(sku) or {}
                e = idx.setdefault(sku, {"list": r.get("list_price"), "kind": r.get("kind"), "shops": []})
                e["shops"].append({"shop": s.get("name"), "qty": qty, "url": s.get("url"), "name": r.get("name")})
    if market:
        return idx, len(cat), len(market)
    for s in shops if isinstance(shops, list) else []:
        if not isinstance(s, dict) or s.get("name") == ME:
            continue
        for o in s.get("offers") or []:
            if o.get("qty") and o.get("sku"):
                r = cat.get(o["sku"]) or {}
                e = idx.setdefault(o["sku"], {"list": r.get("list_price"), "kind": r.get("kind"), "shops": []})
                e["shops"].append({"shop": s.get("name"), "qty": o["qty"], "url": s.get("url"),
                                   "name": o.get("name") or r.get("name")})
    return idx, len(cat), len(shops) if isinstance(shops, list) else 0


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
            "overpaid": sum(1 for m in deals if ((old_book.get("prices") or {}).get(m["item"]) or {}).get("low")
                          and m["agreed"] > 1.05 * old_book["prices"][m["item"]]["low"])}
    state["cards"] = (state.get("cards", []) + [card])[-100:]
    return card


def honest_low(book, sku):
    """The SKU's 24 h low at shops the book does not avoid (a liar's price is no benchmark)."""
    avoid = set(book.get("avoid") or [])
    lows = [v.get("low", v.get("median")) for s, v in ((((book.get("prices") or {}).get(sku) or {}).get("shops"))
                                                        or {}).items() if s not in avoid]
    return min(lows) if lows else None


def expected(book, shop, sku, kind, lp, t):
    """predict(), else the market's 24 h median for the SKU, else the buyer's prior share of the list price."""
    return predict(book, shop, sku, kind) or ((book.get("prices") or {}).get(sku) or {}).get("median") \
        or (lp * t.get("prior_price_ratio", 0.85) if lp else None)


def _case(cases, key, now, kind, typ, m, lost, why):
    cases.setdefault(key, {"ts": now, "kind": kind, "type": typ, "shop": m.get("shop"), "sku": m.get("item"),
                           "budget": m.get("budget"), "lost": round(max(0, lost or 0)), "why": why[:160]})


def judge(state, old, c, now, t):
    """Accountability: new memory lines judged against the book Enuri gave (old), plus the ledger's verdict on our
    buyer's deals. Cases are keyed, so a tick never counts one twice; kept 24 h."""
    bt = _buyer_theta()
    cases = state.setdefault("cases", {})
    new = lines_since(state.get("judged_to", now - 3600))
    idx, avoid = old.get("where") or {}, set(old.get("avoid") or [])
    low = lambda sku: honest_low(old, sku)
    for m in new:
        key, out, budget = f"m:{m.get('ts')}", m.get("outcome"), m.get("budget") or 0
        if out == "deal" and m.get("agreed"):
            a = m["agreed"]
            if str(m.get("problem") or "").startswith("receipt"):
                _case(cases, key, now, "wrong", "receipt problem", m, a, m["problem"])
            elif m.get("shop") in avoid:
                _case(cases, key, now, "wrong", "bought at an avoid shop", m, a - (low(m.get("item")) or a), m["shop"])
            elif low(m.get("item")) and a > 1.05 * low(m["item"]):
                _case(cases, key, now, "wrong", "overpaid", m, a - low(m["item"]), f"paid {a}, book low {low(m['item'])}")
        elif out in ("no deal", "no answer", "no candidates") and budget:
            skus = [m["item"]] if str(m.get("item", "")).startswith("G") else (m.get("acceptable") or [])
            best = None
            for sku in skus:
                e = idx.get(sku) or {}
                for h in e.get("shops") or []:
                    if h["shop"] in avoid:
                        continue
                    x = expected(old, h["shop"], sku, e.get("kind"), e.get("list"), bt)
                    reach = (e.get("list") and e["list"] * bt.get("min_reach", 0.45) <= budget) or (x and x <= budget)
                    if reach and x and (best is None or x < best[0]):
                        best = (x, h["shop"], sku)
            if out == "no candidates" and best:
                _case(cases, key, now, "missed", "no candidates, but stocked", m, budget - best[0],
                      f"{best[1]} had {best[2]} at ~{best[0]:.0f}")
            elif m.get("enuri_first") and m.get("enuri_first") == m.get("shop"):
                _case(cases, key, now, "missed", "first pick failed", m, budget - (best[0] if best else budget),
                      f"{out} at {m.get('shop')}: {m.get('problem') or ''}")
            elif best:
                _case(cases, key, now, "missed", "no deal, in reach", m, budget - best[0],
                      f"{out}; {best[1]} had {best[2]} at ~{best[0]:.0f}")
    if new:
        state["judged_to"] = max(m.get("ts") or 0 for m in new)
    since = now - 24 * 3600
    for seq, ts, sku, amount, shop in c.execute(
            """select s.seq, s.ts, s.sku, s.amount, su.shop from settles s left join settle_units x on x.seq = s.seq
               left join supply_units su on su.unit = x.unit where s.buyer = ? and s.claim_honest = 0 and s.ts > ?""",
            (ME, since)).fetchall():
        _case(cases, f"g:{seq}", now, "wrong", "false grade claim", {"shop": shop, "item": sku}, 0, f"settle {seq}")
    for seq, ts, sku in c.execute("select seq, ts, sku from broken where buyer = ? and ts > ?", (ME, since)).fetchall():
        row = c.execute("""select s.amount, su.shop from settles s left join settle_units x on x.seq = s.seq
                           left join supply_units su on su.unit = x.unit where s.buyer = ? and s.sku = ? and s.ts <= ?
                           order by s.ts desc limit 1""", (ME, sku, ts)).fetchone() or (0, None)
        _case(cases, f"b:{seq}", now, "wrong", "broken after sale", {"shop": row[1], "item": sku}, row[0], f"broken {seq}")
    for m in lines_since(since):                       # deals the kit never paid for: no settle near the deal
        if m.get("outcome") == "deal" and m.get("agreed") and str(m.get("item", "")).startswith("G") \
                and now - m["ts"] > 1800 and f"u:{m['ts']}" not in cases and not c.execute(
                    "select 1 from settles where buyer = ? and sku = ? and ts between ? and ?",
                    (ME, m["item"], m["ts"] - 900, m["ts"] + 3600)).fetchone():
            _case(cases, f"u:{m['ts']}", now, "wrong", "deal never settled", m, (m.get("budget") or 0) - m["agreed"],
                  "kit refused to pay (shelf life, grade or certificate?)")
    state["cases"] = cases = {k: v for k, v in cases.items() if v["ts"] > since}
    pen = state.setdefault("penalty", {})              # every fresh case makes Enuri trust that shop less
    for s in list(pen):
        pen[s] = round(pen[s] * 0.95, 4)
    for v in cases.values():
        if v["ts"] == now and v.get("shop"):
            pen[v["shop"]] = round(min(0.5, 0.8 * pen.get(v["shop"], 0) + 0.2), 4)
    state["penalty"] = {s: x for s, x in pen.items() if x >= 0.01}
    try:                                               # the kit's own words, as a hint only (format not fixed)
        tail = KIT_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]
        refusals = sum(1 for x in tail if REFUSAL.search(x) and "buy" in x.lower())
    except OSError:
        refusals = None
    acc = {"window_h": 24, "missed": 0, "wrong": 0, "lost": 0, "lost_missed": 0, "lost_wrong": 0, "by_type": {},
           "kit_log_refusal_lines": refusals}
    for v in cases.values():
        acc[v["kind"]] += 1
        acc["lost"] += v["lost"]
        acc["lost_" + v["kind"]] += v["lost"]
        bt_ = acc["by_type"].setdefault(v["type"], {"n": 0, "lost": 0})
        bt_["n"] += 1
        bt_["lost"] += v["lost"]
    acc["cases"] = sorted(cases.values(), key=lambda v: -v["lost"])[:10]
    return acc


def _buyer_theta():
    try:
        import buyer_memory
        return buyer_memory.theta()
    except Exception:
        return {"min_reach": 0.45, "r3_first_offer": 0.6, "max_pay_ratio": 0.95, "prior_price_ratio": 0.85}


def advise(order, shops, b=None, t=None, cat=None):
    """The buyer's shopping advice for one order: shops ranked by the surplus they should leave (budget minus
    expected price; the board counts surplus in money) x quality x (1 - Enuri's penalty for that shop), the
    first-offer target and the most to pay. Shops from the market-wide index fill in any the order's list lacks."""
    b = book() if b is None else b
    t = t or _buyer_theta()
    budget, kind, ok = order["budget"], order.get("kind"), set(order.get("acceptable") or [])
    avoid, q, pen = set(b.get("avoid") or []), b.get("quality") or {}, b.get("penalty") or {}
    idx = b.get("where") or {}
    held = {}                                                  # (shop, sku) -> (url, product name, list)
    for s in shops:
        for o in s.get("offers") or []:
            if o.get("sku") in ok and o.get("qty") and s.get("name") != ME:
                held[(s["name"], o["sku"])] = (s.get("url"), o.get("name"), (idx.get(o["sku"]) or {}).get("list"))
    for sku in ok:
        for h in (idx.get(sku) or {}).get("shops") or []:
            held.setdefault((h["shop"], sku), (h.get("url"), h.get("name"), idx[sku].get("list")))
    rows, warn, skipped = {}, [], {"avoid": 0, "reach": 0, "no list": 0}
    for (shop, sku), (url, name, lp) in held.items():
        lp = lp or ((cat or {}).get(sku) or {}).get("list_price")
        if shop in avoid:
            skipped["avoid"] += 1
            continue
        if not lp or not url:
            skipped["no list"] += 1
            continue
        x = expected(b, shop, sku, kind, lp, t)
        if lp * t["min_reach"] > budget and not (x and x <= budget):
            skipped["reach"] += 1
            continue
        qa = q.get(shop) or {}
        honest = qa.get("honest") if qa.get("honest") is not None else 0.9
        keep = (budget - min(x, budget)) * (0.5 + 0.5 * honest) * (1 - qa.get("broken", 0)) * (1 - pen.get(shop, 0))
        target = int(lp * t["r3_first_offer"])
        p, hl = (b.get("prices") or {}).get(sku) or {}, honest_low(b, sku)
        if hl:
            target = int(max(0.45 * lp, min(target, hl)))
        walk = min(budget, int(lp * t["max_pay_ratio"]))
        meds = [v["median"] for s_, v in (p.get("shops") or {}).items() if s_ not in avoid]
        if p.get("n", 0) >= 3 and meds:                        # well known price: do not pay far above the market
            walk = min(walk, max(int(1.1 * statistics.median(meds)), int(1.05 * x), target))
        target = min(target, int(0.9 * walk))
        row = {"shop": shop, "sku": sku, "url": url, "name": name, "expected_price": round(x), "list_price": lp,
               "reach_ok": True, "keep": round(keep), "target": target, "walk_away": walk,
               "quality": {k: qa.get(k) for k in ("deals", "honest", "broken", "verdict")} if qa else None,
               "why": f"expect {round(x)} ({'book' if predict(b, shop, sku, kind) else 'market/prior'}), "
                      f"keep ~{round(budget - min(x, budget))}" + (f", penalty {pen[shop]}" if pen.get(shop) else "")}
        if shop not in rows or row["keep"] > rows[shop]["keep"]:
            rows[shop] = row
    ranked = sorted(rows.values(), key=lambda r: -r["keep"])
    if not held:
        warn.append("no shop in the market has an acceptable SKU in stock")
    if skipped["avoid"]:
        warn.append(f"{skipped['avoid']} offers at avoid shops skipped")
    if skipped["reach"]:
        warn.append(f"{skipped['reach']} offers out of reach (list x {t['min_reach']} > budget {budget})")
    if skipped["no list"]:
        warn.append(f"{skipped['no list']} offers without a list price or url")
    return {"ranked": ranked, "target": ranked[0]["target"] if ranked else None,
            "walk_away": ranked[0]["walk_away"] if ranked else None, "warnings": warn}


def tick(md=Path(".market"), c=None, now=None, force=False):
    """Rebuild the book (every tick_s seconds). Returns the scorecard of this tick, or None when not due."""
    t, now = settings(), now or time.time()
    state = load(STATE, {})
    if not force and now - state.get("last_tick", 0) < t["tick_s"]:
        return None
    c = c or market_model.db()
    old = load(BOOK, {})
    card = score(state, old, t) if old else None
    acc = judge(state, old, c, now, t) if old else None
    if card is not None:
        card.update(missed=acc["missed"], wrong=acc["wrong"], lost=acc["lost"])
    q = quality(c, md, t)
    idx, n_cat, n_shops = where(md)
    book = {"ts": now, "prices": prices(c), "quality": q, "forum": forum_notes(md, now),
            "asks": load(ASKS, {}).get("asks", {}), "k": state.get("k", {"goods": 1.0, "food": 1.0}),
            "avoid": sorted(s for s, x in q.items() if x["verdict"] == "avoid"), "where": idx,
            "penalty": state.get("penalty", {}), "enuri_accountability": acc or old.get("enuri_accountability"),
            "coverage": {"catalog_skus": n_cat, "shops": n_shops, "skus_in_stock": len(idx),
                         "skus_priced_24h": 0, "stocked_and_priced": 0}}
    book["coverage"].update(skus_priced_24h=len(book["prices"]),
                            stocked_and_priced=len(set(idx) & set(book["prices"])))
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
    acc = dict(b.get("enuri_accountability") or {})
    return {"enuri_accountability": acc, "coverage": b.get("coverage"),
            "shop_penalties": dict(sorted((b.get("penalty") or {}).items(), key=lambda kv: -kv[1])[:10]),
            "book_age_min": round((time.time() - b.get("ts", 0)) / 60), "skus_priced": len(b.get("prices") or {}),
            "shops_rated": len(q), "k": b.get("k"), "price_spreads": spreads[:top],
            "avoid": [{"shop": s, "why": (q.get(s) or {}).get("why")} for s in b.get("avoid") or []],
            "forum_cited_prices": dict(sorted((fo.get("cited_prices") or {}).items(),
                                              key=lambda kv: -kv[1].get("n", 0))[:top]),
            "forum_flagged_posts": len(fo.get("flagged") or []),
            "asks_by_sku": sum(len(d) for d in (b.get("asks") or {}).values()), "requests": requested(),
            "scorecards": [{k: x.get(k) for k in ("ts", "deals", "predicted", "mae", "bias", "overpaid", "missed",
                                                  "wrong", "lost")} for x in cards]}


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
