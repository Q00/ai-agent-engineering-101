"""Read-only market tools for the strategists, served over MCP stdio (11-11; user: build the tools and use them).

The strategists ran with no tools: everything had to be pushed into a 60-100k-char prompt, and a follow-up question
went to the curator and came back 1-2 h later (4 of 10 answers 'not measurable'). With these tools a strategist can
check a cause inside the run. All tools are read-only (local files + public market GETs) and cost no API credit;
each answer is capped at MAX_CHARS.

Run by `claude -p --mcp-config <json> --strict-mcp-config --allowedTools mcp__market`; see MCP_CONFIG below.
A minimal JSON-RPC 2.0 / MCP stdio server, no SDK (the venv has none).

    python market_tools.py --selftest      # call every tool once and print the sizes
"""
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
MAX_CHARS = 6000
PROTOCOL = "2025-06-18"


def _read(p, default=None):
    try:
        return json.loads((HERE / p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _score(t):
    if t.get("broken"):
        return 0.0 if (t.get("warranty") or 0) >= 6 else -1.0
    return 1 - t["price"] / t["list"]


def _mean(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def _trades(hours):
    now = time.time()
    return [t for t in (_read("buyer_memory.json", {}) or {}).get("trades", []) if now - t.get("t", 0) < hours * 3600]


# --- tools -------------------------------------------------------------------------------------------------

def our_trades(shop=None, kind=None, category=None, outcome=None, hours=6, limit=25):
    """Our buyer's visit records over the last `hours` (<= 48), filtered; summary + the newest rows."""
    hours = min(float(hours or 6), 48)
    rows = _trades(hours)
    if shop:
        rows = [t for t in rows if t.get("shop") == shop]
    if kind:
        rows = [t for t in rows if t.get("kind") == kind]
    if category:
        rows = [t for t in rows if category in str(t.get("category") or "")]
    if outcome:
        rows = [t for t in rows if str(t.get("outcome", "")).startswith(outcome)]
    deals = [t for t in rows if t.get("outcome") == "deal" and t.get("list") and t.get("price")]
    outs = {}
    for t in rows:
        k = str(t.get("outcome", "")).split(" (")[0].split(":")[0]
        outs[k] = outs.get(k, 0) + 1
    keep = ("shop", "kind", "category", "outcome", "price", "list", "cap", "asks", "warranty", "graded", "broken", "expected")
    sample = [{k: t.get(k) for k in keep if t.get(k) not in (None, [], "")} for t in rows[-int(limit or 25):]]
    return {"visits": len(rows), "outcomes": outs, "deals": len(deals), "v_per_deal": _mean([_score(t) for t in deals]),
            "breaks": sum(1 for t in deals if t.get("broken")), "newest": sample}


def shop_market(shop, kind=None):
    """The whole market's settles at `shop` over the last ~6 h (ledger cache): n, saved per deal, price quantiles
    (x = price/list), warranty and break shares, per kind and the top categories; plus OUR realized v there (24 h)."""
    st = _read(".market/shop_value.json", {}) or {}
    stats = st.get("stats") or {}
    out = {k: v for k, v in stats.items() if k.startswith(f"{shop}|") and (not kind or f"|{kind}" in k)}
    cats = sorted(((k, v) for k, v in out.items() if k.count("|") == 2), key=lambda kv: -kv[1].get("n", 0))[:8]
    base = {k: v for k, v in out.items() if k.count("|") == 1}
    ours = [t for t in _trades(24) if t.get("shop") == shop and t.get("outcome") == "deal" and t.get("list") and t.get("price")
            and (not kind or t.get("kind") == kind)]
    return {"market": base, "by_category": dict(cats), "ours_24h": {"n": len(ours), "v": _mean([_score(t) for t in ours])}}


def food_losses(hours=6):
    """Food orders that ended without a deal: count by final outcome; failed visits by shop with the shop's lowest
    ask and our cap (x = price/list) and the v a deal at that ask would have scored; too-short by shop."""
    rows = [t for t in _trades(min(float(hours or 6), 48)) if t.get("kind") == "food"]
    orders = {}
    for t in rows:
        orders.setdefault(t.get("order"), []).append(t)
    end = {}
    for xs in orders.values():
        k = "deal" if any(t.get("outcome") == "deal" for t in xs) else str(xs[-1].get("outcome", "")).split(" (")[0].split(":")[0]
        end[k] = end.get(k, 0) + 1
    by = {}
    for t in rows:
        o = str(t.get("outcome", "")).split(" (")[0]
        if t.get("shop") and o in ("rejected", "too dear", "over budget", "walked away"):
            e = by.setdefault(t["shop"], {"failed": 0, "asks": [], "caps": []})
            e["failed"] += 1
            if t.get("asks") and t.get("list"):
                e["asks"].append(min(t["asks"]) / t["list"])
            if t.get("cap") and t.get("list"):
                e["caps"].append(t["cap"] / t["list"])
    shops = {s: {"failed": e["failed"], "lowest_ask_x": round(statistics.median(e["asks"]), 3) if e["asks"] else None,
                 "our_cap_x": round(statistics.median(e["caps"]), 3) if e["caps"] else None,
                 "v_at_ask": round(1 - statistics.median(e["asks"]), 3) if e["asks"] else None}
             for s, e in sorted(by.items(), key=lambda kv: -kv[1]["failed"])[:12]}
    short = {}
    for t in rows:
        if str(t.get("outcome", "")).startswith("too short"):
            short[t.get("shop")] = short.get(t.get("shop"), 0) + 1
    return {"food_orders": len(orders), "ended": end, "failed_visits_by_shop": shops,
            "too_short_by_shop": dict(sorted(short.items(), key=lambda kv: -kv[1])[:8]),
            "note": "a deal helps the final score only if v > m* (see score); v_at_ask under m* means the refusal was right"}


def realized_by_shop(kind=None, hours=24):
    """Our realized v per shop over `hours` (deals n >= 3), sorted by volume, with the share of our deals."""
    deals = [t for t in _trades(min(float(hours or 24), 48)) if t.get("outcome") == "deal" and t.get("list") and t.get("price")
             and (not kind or t.get("kind") == kind)]
    by = {}
    for t in deals:
        by.setdefault(t.get("shop"), []).append(_score(t))
    rows = sorted(((s, len(v), _mean(v)) for s, v in by.items() if len(v) >= 3), key=lambda r: -r[1])
    return {"deals": len(deals), "v": _mean([_score(t) for t in deals]),
            "by_shop": [{"shop": s, "n": n, "share": round(n / len(deals), 3), "v": v} for s, n, v in rows[:20]]}


def class_ladder():
    """Students' saved ladder around us with the deals at v .36/.42 needed to pass each (from buyer_strategist)."""
    import buyer_strategist as bs
    return bs.class_ladder(None)


def buyer_profile(name):
    """Another buyer's last-6-h profile from the ledger cache: shops, kinds, grades, prices, saved per deal."""
    import market_picture as mp
    return mp.profile_buyer(name)


def shop_profile(name):
    """A shop's last-6-h profile: what it sells, prices, buyers, stock."""
    import market_picture as mp
    p = mp.profile_shop(name)
    return {k: (v[:8] if isinstance(v, list) else v) for k, v in p.items()} if isinstance(p, dict) else p


def score():
    """The scorekeeper (buyer_score.json): m_now, m*, v by kind, calibration, breaks, skips, arms per order."""
    sc = _read(".market/buyer_score.json", {}) or {}
    return {k: v for k, v in sc.items() if k not in ("hist", "ours")}


# --- shop tools (11-11, user: more and more useful tools) --------------------------------------------------

def _seller_rows(hours, events=("deal",)):
    now = time.time()
    try:
        raw = (HERE / "seller_r3_log.jsonl").read_bytes()[-40_000_000:].decode("utf-8", "ignore").splitlines()[1:]
    except OSError:
        return []
    out = []
    for line in raw:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") in events and r.get("t", 0) > now - hours * 3600:
            out.append(r)
    return out


def _catalog():
    return _read(".market/catalog.json", {}) or {}


def sku_sales(sku=None, hours=24, top=15):
    """Our shop's sales per SKU over `hours` (<= 48): closes, margin, mean x = price/list, grade mix, stock now and
    its grades; one SKU in detail when `sku` is given, else the top SKUs by margin."""
    hours = min(float(hours or 24), 48)
    cat = _catalog()
    inv = _read(".market/inventory.json", {}) or {}
    stock = {}
    for u in inv.get("units", []):
        e = stock.setdefault(u["sku"], {"units": 0, "grades": {}})
        e["units"] += 1
        e["grades"][u.get("grade")] = e["grades"].get(u.get("grade"), 0) + 1
    by = {}
    for r in _seller_rows(hours):
        k = r.get("sku")
        if sku and k != sku:
            continue
        e = by.setdefault(k, {"closes": 0, "margin": 0, "xs": [], "grades": {}})
        e["closes"] += 1
        e["margin"] += (r.get("price") or 0) - (r.get("cost") or 0)
        lp = (cat.get(k) or {}).get("list_price")
        if lp and r.get("price"):
            e["xs"].append(r["price"] / lp)
        e["grades"][r.get("grade")] = e["grades"].get(r.get("grade"), 0) + 1
    rows = sorted(by.items(), key=lambda kv: -kv[1]["margin"])[: (1 if sku else int(top or 15))]
    return {"hours": hours, "skus": [{"sku": k, "name": str((cat.get(k) or {}).get("name"))[:30], "closes": e["closes"],
                                      "margin_M": round(e["margin"] / 1e6, 2), "mean_x": _mean(e["xs"]), "grades": e["grades"],
                                      "stock": stock.get(k)} for k, e in rows]}


def margin_by_grade(hours=6):
    """Our shop's agreed closes and margin per grade over `hours`, and what was actually PAID (the unit settled on
    the ledger). 11-11: 'which grades carry the book' read from closes was wrong: grade C showed 47 M of margin on
    204 closes in 6 h, of which 33 were paid for 6 M; paid margin was 4.7 M/h while the book grew +0.63 M/h (refunds
    of broken warranted goods, storage and expired food take the rest)."""
    by = {}
    h = min(float(hours or 6), 48)
    rows = _seller_rows(h)
    sv = _read(HERE / ".market" / "shop_value.json", {}) or {}
    D = sv.get("deals") or []
    paid_units = {d.get("unit") for d in D if d.get("shop") == "yms979"}
    t1 = (max(d["ts"] for d in D) - 120) if D else 0
    for r in rows:
        e = by.setdefault(r.get("grade"), [0, 0, 0, 0, 0])
        e[0] += 1
        e[1] += (r.get("price") or 0) - (r.get("cost") or 0)
        if r.get("t", 0) <= t1 and r.get("unit") in paid_units:
            e[3] += 1
            e[4] += (r.get("price") or 0) - (r.get("cost") or 0)
    return {"hours": h, "paid_margin_M_h": round(sum(e[4] for e in by.values()) / h / 1e6, 2),
            "agreed_margin_M_h": round(sum(e[1] for e in by.values()) / h / 1e6, 2),
            "by_grade": {g: {"closes": e[0], "paid": e[3], "paid_share": round(e[3] / e[0], 2), "margin_on_closes_M": round(e[1] / 1e6, 1),
                             "margin_paid_M": round(e[4] / 1e6, 1), "paid_margin_per_deal_k": round(e[4] / e[3] / 1e3) if e[3] else None}
                         for g, e in by.items()},
            "note": "decide on the PAID columns; closes newer than the ledger cache (~10 min) count as unpaid"}


def unpaid_closes(hours=3):
    """Our shop's closes that never settled (seller log vs the ledger cache, by unit), by grade and SKU."""
    h = min(float(hours or 3), 5)
    sv = _read(".market/shop_value.json", {}) or {}
    settled = {d.get("unit") for d in sv.get("deals", []) if d.get("shop") == "yms979"}
    now = time.time()
    rows = [r for r in _seller_rows(h) if now - r.get("t", now) > 600]
    miss = [r for r in rows if r.get("unit") and r["unit"] not in settled]
    g = {}
    for r in miss:
        g[r.get("grade")] = g.get(r.get("grade"), 0) + 1
    sk = {}
    for r in miss:
        sk[r.get("sku")] = sk.get(r.get("sku"), 0) + 1
    return {"hours": h, "closes": len(rows), "unsettled": len(miss), "by_grade": g,
            "top_skus": dict(sorted(sk.items(), key=lambda kv: -kv[1])[:8]),
            "note": "a close the buyer's kit refuses (grade, warranty, shelf) holds the unit 300 s and earns nothing"}


def stars_accounts():
    """Who rates our shop below 5 (paid results, 24 h): per account n, mean, mean at other shops, claims, x, reasons."""
    import strategist
    return strategist.stars_accounts()


def stars_ladder():
    """The student shops' stars_rank ladder around us and what one rank is worth."""
    import strategist
    return strategist.stars_ladder(None)


def book_race():
    """Books, our lead, 6 h slopes, closure ETA and the revenue neighbours."""
    import strategist
    return strategist.book_race()


def stock_turnover():
    """11-17: per SKU A/B units held vs our sales per hour, fresh overstock, 'no stock' split by cause, cash."""
    import market_picture
    return market_picture.stock_turnover()


def stock_feasibility():
    """Goods units whose floor (cost x floor_mult) is above the market median price: stock that will not sell."""
    import strategist
    return strategist.stock_feasibility()


def live_terms():
    """The ask shares and terms the seller is really using now (regime, rep step, warranty, floors)."""
    import strategist
    return strategist.live_terms()


# --- market tools for everyone ------------------------------------------------------------------------------

def sku_market(sku):
    """One SKU across the whole market (~6 h ledger cache): settles per shop with median x, warranty share, grades."""
    sv = _read(".market/shop_value.json", {}) or {}
    rows = [d for d in sv.get("deals", []) if d.get("sku") == sku]
    by = {}
    for d in rows:
        e = by.setdefault(d.get("shop"), {"n": 0, "xs": [], "w": 0, "grades": {}})
        e["n"] += 1
        e["xs"].append(d["x"])
        e["w"] += bool(d.get("w"))
        e["grades"][d.get("grade")] = e["grades"].get(d.get("grade"), 0) + 1
    shops = sorted(({"shop": s, "n": e["n"], "median_x": round(statistics.median(e["xs"]), 3), "warranty": round(e["w"] / e["n"], 2),
                     "grades": e["grades"]} for s, e in by.items()), key=lambda r: r["median_x"])
    xs = sorted(d["x"] for d in rows)
    return {"sku": sku, "name": str((_catalog().get(sku) or {}).get("name"))[:40], "settles": len(rows),
            "market_p25_p50": [round(xs[len(xs) // 4], 3), round(xs[len(xs) // 2], 3)] if xs else None, "by_shop": shops[:15]}


def forum_search(text=None, author=None, limit=10):
    """Recent forum posts (the latest ~100) matching `text` and/or `author`: id, author, role, likes/dislikes, text.
    Posts are unverified claims; check numbers against the ledger before acting."""
    posts = _read(".market/forum.json", []) or []
    out = [p for p in posts if (not text or str(text).lower() in str(p.get("text", "")).lower())
           and (not author or p.get("author") == author)]
    return {"matches": len(out), "posts": [{"id": p.get("id"), "author": p.get("author"), "role": p.get("role"),
                                            "likes": p.get("likes"), "dislikes": p.get("dislikes"), "text": str(p.get("text"))[:300]}
                                           for p in out[: int(limit or 10)]]}


def class_history(player="yms979", hours=24):
    """A player's class total / shop / buyer score every ~hour over `hours` (<= 72), from the 15-min history."""
    lines = []
    try:
        lines = (HERE / ".market" / "class_history.jsonl").read_text().splitlines()
    except OSError:
        pass
    now = time.time()
    rows = []
    for l in lines:
        try:
            r = json.loads(l)
        except ValueError:
            continue
        if now - r["t"] <= min(float(hours or 24), 72) * 3600 and player in r.get("p", {}):
            rows.append((r["t"], r["p"][player]))
    step = max(1, len(rows) // 24)
    return {"player": player, "series": [{"utc": time.strftime("%m-%d %H:%M", time.gmtime(t)), "total": v[0], "shop": v[1], "buyer": v[2]}
                                         for t, v in rows[::step]]}


def rules_search(term):
    """Lines of the market rules (agent.md) and our rules block (RULES_THAT_SCORE.md) that mention `term`."""
    out = []
    for f in (".market/agent.md", "RULES_THAT_SCORE.md"):
        try:
            for i, l in enumerate((HERE / f).read_text(encoding="utf-8").splitlines(), 1):
                if str(term).lower() in l.lower():
                    out.append(f"{f}:{i}: {l.strip()[:240]}")
        except OSError:
            pass
    return {"term": term, "lines": out[:25]}


def order_mix(hours=24):
    """Our buyer's orders by category over `hours`: orders, deals, close rate and v per deal (unique orders)."""
    rows = _trades(min(float(hours or 24), 48))
    by = {}
    for t in rows:
        by.setdefault(t.get("order"), []).append(t)
    cat = {}
    for xs in by.values():
        c = xs[0].get("category") or "?"
        e = cat.setdefault(c, {"kind": xs[0].get("kind"), "orders": 0, "deals": 0, "v": []})
        e["orders"] += 1
        d = next((t for t in xs if t.get("outcome") == "deal" and t.get("list") and t.get("price")), None)
        if d:
            e["deals"] += 1
            e["v"].append(_score(d))
    rows_out = sorted(({"category": c, "kind": e["kind"], "orders": e["orders"], "close_rate": round(e["deals"] / e["orders"], 2),
                        "v": _mean(e["v"])} for c, e in cat.items()), key=lambda r: -r["orders"])
    return {"orders": len(by), "by_category": rows_out[:20]}


def projection(rate=None, v=None):
    """Our final saved and saved rank among students at a scenario (deals/h, per-deal v), with every other student
    at its last-6-h pace; no arguments = a grid of 30/40/50 deals/h x v .38-.44."""
    sys.path.insert(0, str(HERE / "ops"))
    import whatif
    return whatif.project(rate, v)


def peer_best(category, hours=6, top=6):
    """11-11 (user: 'why must I tell you the obvious'): in one category (leaf name, e.g. 침대/매트리스), the buyers
    with the best realized v over `hours` (n >= 8): v, median x, grade mix, warranty share, breaks, top shops; the
    market by grade (median x, break share, v if warranted = (1-x)(1-p), unwarranted = (1-x)(1-p) - p); and us."""
    st_ = _read(HERE / ".market" / "shop_value.json", {}) or {}
    D, br = st_.get("deals") or [], st_.get("broken") or {}
    if not D:
        return {"error": "no ledger cache"}
    t1 = max(d["ts"] for d in D)
    R = [d for d in D if d.get("cat", "").split(">")[-1] == category and t1 - d["ts"] <= min(float(hours or 6), 6) * 3600]

    def v(d):
        u = d.get("unit")
        return (0.0 if br[u] else -1.0) if u in br else 1 - d["x"]
    by = {}
    for d in R:
        by.setdefault(d.get("buyer"), []).append(d)
    rows = []
    for b, L in by.items():
        if len(L) < 8 and b != "yms979":
            continue
        g = {k: sum(1 for d in L if d.get("grade") == k) for k in "ABC"}
        shops = {}
        for d in L:
            shops[d["shop"]] = shops.get(d["shop"], 0) + 1
        rows.append({"buyer": b, "n": len(L), "v": round(_mean([v(d) for d in L]), 3),
                     "x_med": round(statistics.median(d["x"] for d in L), 3), "grades": g,
                     "warranty": round(sum(1 for d in L if d.get("w")) / len(L), 2),
                     "breaks": sum(1 for d in L if d.get("unit") in br),
                     "top_shops": sorted(shops.items(), key=lambda kv: -kv[1])[:3]})
    rows.sort(key=lambda r: -r["v"])
    grade = {}
    for k in "ABC":
        xs = [d["x"] for d in R if d.get("grade") == k]
        if len(xs) >= 5:
            p = sum(1 for d in R if d.get("grade") == k and d.get("unit") in br) / len(xs)
            xm = statistics.median(xs)
            grade[k] = {"n": len(xs), "x_med": round(xm, 3), "break_share": round(p, 3),
                        "v_if_warranted": round((1 - xm) * (1 - p), 3), "v_if_not": round((1 - xm) * (1 - p) - p, 3)}
    return {"category": category, "market": {"n": len(R), "v": round(_mean([v(d) for d in R]), 3)}, "by_grade": grade,
            "best_buyers": rows[:top], "us": next((r for r in rows if r["buyer"] == "yms979"), None)}


def final_rank(rate=30.0, v=0.40):
    """Projected final class TOTAL ranks: buyer by projected saved rank (ours at the scenario), shop by its 24-h trend
    and by half its 48-h trend (damped), top 10 + us. Rough extrapolation (ops/final_rank.py)."""
    sys.path.insert(0, str(HERE / "ops"))
    import final_rank as fr
    comp = fr.project_components(float(rate) if rate else None, float(v) if v else None)   # 11-11: part by part
    return comp or fr.project(float(rate or 30), float(v or 0.40))


def shop_health():
    """Is our shop alive and reachable now: board alive flag, market-wide dead count, our agent card from outside,
    last seller task age, kit exits and tunnel relists over 6 h, verdict OK / MARKET OUTAGE (not us) / PROBLEM."""
    sys.path.insert(0, str(HERE / "ops"))
    import health
    return health.check()


def frontier(days=3, bucket=12):
    """The saved frontier over time (sampled ledger): per-deal v, food share, warranty share by bucket for the market,
    all students, the top-8 saved students and us (ops/frontier.py). Slow (~3-6 min): call at most once per run."""
    sys.path.insert(0, str(HERE / "ops"))
    import frontier as fr
    return fr.frontier(float(days or 3), float(bucket or 12), pages_per_bucket=4)


TOOLS = {
    "our_trades": (our_trades, "Our buyer's visit records (last hours <= 48), filtered by shop/kind/category/outcome prefix: outcome counts, deals, v per deal (rules scoring: refunded break 0, unrefunded -1), breaks, newest rows.",
                   {"shop": "string", "kind": "string", "category": "string", "outcome": "string", "hours": "number", "limit": "integer"}, []),
    "shop_market": (shop_market, "The whole market's settles at a shop (~6 h): n, saved per deal, p15/p25/p50 price quantiles (x = price/list), warranty and break shares, by kind and category; plus our realized v there over 24 h.",
                    {"shop": "string", "kind": "string"}, ["shop"]),
    "food_losses": (food_losses, "Food orders that ended without a deal (last hours): outcomes, failed visits by shop with the shop's lowest ask vs our cap and the v a deal at that ask would score, too-short by shop.",
                    {"hours": "number"}, []),
    "realized_by_shop": (realized_by_shop, "Our realized v per shop (last hours, default 24), by kind, with each shop's share of our deals.",
                         {"kind": "string", "hours": "number"}, []),
    "class_ladder": (class_ladder, "Students' saved ladder around us and the deals needed to pass each at v .36/.42.", {}, []),
    "buyer_profile": (buyer_profile, "Another buyer's last-6-h profile (shops, kinds, grades, prices, saved per deal).", {"name": "string"}, ["name"]),
    "shop_profile": (shop_profile, "A shop's last-6-h profile (what it sells, prices, buyers, stock).", {"name": "string"}, ["name"]),
    "score": (score, "The scorekeeper: m_now, m* (projected final mean), v by kind 6/24 h, calibration, breaks, skips, per-order arm scores.", {}, []),
    "sku_sales": (sku_sales, "Our shop's sales per SKU (hours <= 48): closes, margin, mean x, grade mix, stock now; one SKU when given.", {"sku": "string", "hours": "number", "top": "integer"}, []),
    "margin_by_grade": (margin_by_grade, "Our shop's agreed closes per grade over hours and what was actually PAID (settled): paid share, margin on closes vs paid margin. Decide on the paid columns.", {"hours": "number"}, []),
    "unpaid_closes": (unpaid_closes, "Our shop's closes that never settled (last hours), by grade and SKU.", {"hours": "number"}, []),
    "stars_accounts": (stars_accounts, "Accounts rating our shop below 5 (paid, 24 h): n, mean, mean elsewhere, claims, x, reasons.", {}, []),
    "stars_ladder": (stars_ladder, "Student shops' stars_rank ladder around us and the value of one rank.", {}, []),
    "stock_turnover": (stock_turnover, "Stock turnover: A/B units held per SKU vs our sales per hour (hours of cover), overstock bought in 24 h beyond 6 h of sales, 'no stock' answers per hour split into SKUs held only as grade C vs not held, cash. Use it before any restock or cover move.", {}, []),
    "book_race": (book_race, "Books, our lead, 6 h slopes, closure ETA, revenue neighbours.", {}, []),
    "stock_feasibility": (stock_feasibility, "Goods units whose floor is above the market median price (will not sell), top SKUs.", {}, []),
    "live_terms": (live_terms, "The ask shares and terms the seller really uses now (regime, rep step, warranty, floors).", {}, []),
    "sku_market": (sku_market, "One SKU across the market (~6 h): settles per shop with median x, warranty share, grades; market p25/p50.", {"sku": "string"}, ["sku"]),
    "forum_search": (forum_search, "Recent forum posts matching text and/or author (unverified claims).", {"text": "string", "author": "string", "limit": "integer"}, []),
    "class_history": (class_history, "A player's class total/shop/buyer score over hours (<= 72).", {"player": "string", "hours": "number"}, []),
    "rules_search": (rules_search, "Lines of the market rules (agent.md) and our rules block mentioning a term.", {"term": "string"}, ["term"]),
    "projection": (projection, "Our final saved and saved rank among students for a scenario (deals/h, per-deal v), others at their last-6-h pace; no args = grid.", {"rate": "number", "v": "number"}, []),
    "peer_best": (peer_best, "In one category (leaf, e.g. 침대/매트리스): the buyers with the best realized v over 6 h (v, median x, grades, warranty, breaks, shops), the market by grade with v if warranted / not, and us.", {"category": "string", "hours": "number", "top": "integer"}, ["category"]),
    "final_rank": (final_rank, "Projected final class TOTAL ranks (top 10 + us), part by part: every student's book, revenue, deals and posts at their last-6-h slope, saved at the per-deal value the board implies, stars and forum held; ranks now>end per part. rate, v set OUR buyer's deals/h and per-deal value (omit = our current pace). Gaps under .03 are a tie.", {"rate": "number", "v": "number"}, []),
    "shop_health": (shop_health, "Is our shop alive and reachable now: board alive flag, market-wide dead count, our card from outside, last seller task age, kit exits and relists over 6 h, verdict.", {}, []),
    "frontier": (frontier, "The saved frontier over time (sampled ledger, buckets of `bucket` h over `days`): per-deal v, food share, warranty share for market, students, top-8 saved students and us. Slow; once per run.", {"days": "number", "bucket": "number"}, []),
    "order_mix": (order_mix, "Our buyer's orders by category over hours: orders, close rate, v per deal.", {"hours": "number"}, []),
}


def _schema(props, req):
    return {"type": "object", "properties": {k: {"type": t} for k, t in props.items()}, "required": req}


def call(name, args):
    fn = TOOLS[name][0]
    try:
        res = fn(**(args or {}))
        text = json.dumps(res, ensure_ascii=False, default=str)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"error: {type(e).__name__}: {e}"}], "isError": True}
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + " ...(truncated)"
    try:
        with open(HERE / "logs" / "market_tools.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {name} {json.dumps(args, ensure_ascii=False)} -> {len(text)} chars\n")
    except OSError:
        pass
    return {"content": [{"type": "text", "text": text}]}


def serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:                               # notifications (e.g. notifications/initialized)
            continue
        if method == "initialize":
            res = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL,
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "market", "version": "1.0"}}
        elif method == "tools/list":
            res = {"tools": [{"name": n, "description": d, "inputSchema": _schema(p, r)} for n, (_, d, p, r) in TOOLS.items()]}
        elif method == "tools/call":
            p = msg.get("params") or {}
            if p.get("name") not in TOOLS:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}}) + "\n")
                sys.stdout.flush()
                continue
            res = call(p["name"], p.get("arguments") or {})
        elif method == "ping":
            res = {}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}, ensure_ascii=False) + "\n")
        sys.stdout.flush()


MCP_CONFIG = HERE / ".market" / "mcp_market.json"


def write_config():
    MCP_CONFIG.parent.mkdir(exist_ok=True)
    MCP_CONFIG.write_text(json.dumps({"mcpServers": {"market": {"command": str(HERE / ".venv" / "bin" / "python"),
                                                                   "args": [str(HERE / "market_tools.py")]}}}))
    return MCP_CONFIG


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        for n, args in (("our_trades", {"kind": "food", "hours": 3}), ("shop_market", {"shop": "psyoung82"}), ("food_losses", {}),
                        ("realized_by_shop", {"kind": "food"}), ("class_ladder", {}), ("buyer_profile", {"name": "move_76"}),
                        ("shop_profile", {"name": "nocha12"}), ("score", {}), ("sku_sales", {"hours": 6, "top": 5}),
                        ("margin_by_grade", {}), ("unpaid_closes", {}), ("stars_accounts", {}), ("stars_ladder", {}),
                        ("book_race", {}), ("stock_feasibility", {}), ("live_terms", {}), ("sku_market", {"sku": "G200e39a3"}),
                        ("forum_search", {"text": "yms979"}), ("class_history", {"player": "seochanit", "hours": 12}),
                        ("rules_search", {"term": "refund"}), ("order_mix", {})):
            r = call(n, args)
            print(n, "error" if r.get("isError") else "ok", len(r["content"][0]["text"]), r["content"][0]["text"][:140])
        print("config", write_config())
    else:
        serve()
