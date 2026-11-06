"""Focus and trend SKUs (stock.focus_skus, stock.trend_skus): the market's best sellers, scored and gated by our
code every run from the ledger tables (strategist/collect.py sku_tables), never by the model.

Popularity is what the market settles (ledger), not what shops buy (/api/supply-prices lists shop buying).
score = rate6 x value x momentum x scarcity: rate6 market settles per hour over 6 h, value sqrt(list price /
VALUE_REF) (Shop ALL weighs Book and revenue, and both scale with the price of what sells: most of the market's
revenue is beds and sofas, which settle far fewer units than kimchi), momentum clamp(rate1 / rate6, 0.5, 2),
scarcity clamp(2 / cover_h, 0.5, 2) with cover_h = units on all shelves (/api/shops) / max(rate6, 0.5), 1 without
shelf data. x BONUS when the SKU still has more than FREE_LEFT units of the market's free hourly supply left
(Patch 3.2: the first `free` units an hour cost the plain price), x BONUS when a top-3 shop's reviews name it
(a hint only: every candidate already has market settles). Candidates have at least MIN_SETTLES settles in 6 h.
Gates (a gated SKU is in neither list):
- margin: p_clear - c_kind x f_now x seller.floor_cost_mult < stock.min_margin_share (p_clear: the SKU's mean
  paid/list with 3 or more settles, else its kind's median; c_kind our mean supply cost/list; f_now the surge
  factor at the market's units of the last hour); skipped without a p_clear.
- congested: other shops' supply in the last hour (the market's units less ours: our own restock of a listed SKU
  must not push it out) > CONGESTION x the settles of an hour (the last hour's or rate6, the larger: one quiet
  hour must not flag a steady seller).
- price: we held >= FLAG_HELD units for FLAG_HELD_H hours, sold none in 6 h while the market sold >= FLAG_MARKET:
  the price, not depth, is the problem (brief price_check). Judged only once both floors have been at or under
  FLOOR_OK for FLAG_HELD_H hours (state["trend"]["floor_ok_since"]); before that the SKU is listed, unjudged.
Focus food needs catalog shelf_hours >= stock.food_shelf_hours, trend food >= TREND_FOOD_SHELF_H.
focus_skus: the top stock.focus_max; trend_skus: the next stock.trend_max. A listed SKU stays while it ranks
within KEEP_RANK x the list size (or always until the counts cover 6 h); a new one needs stock.focus_min_settles
(focus) or MIN_SETTLES (trend), at most FOCUS_SWAPS / TREND_SWAPS new per run.
"""
import math

MIN_SETTLES = 5
KEEP_RANK = 2
FOCUS_SWAPS = 3
TREND_SWAPS = 4
TREND_FOOD_SHELF_H = 48
CONGESTION = 1.5
FLAG_HELD, FLAG_HELD_H, FLAG_MARKET = 3, 6, 20
FLOOR_OK = (0.70, 1.15)                 # (seller.floor_list, seller.floor_cost_mult) at or under the guard's ceilings
BONUS = 1.2
VALUE_REF = 10_000                      # value = sqrt(list / VALUE_REF): a 10,000 item weighs 1, a 2.5 M sofa about 16
FREE_LEFT = 10
WINDOW_S = 6 * 3600
C_FALLBACK = {"food": 0.51, "goods": 0.61}
SURGE = {"free": 50, "per_unit": 0.002, "max": 1.5}
# keys stock.* that may not be in policy.DEFAULTS yet
FALLBACK = {"trend_max": 16, "min_margin_share": 0.05, "trend_skus": []}


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _clamp(x, lo, hi):
    return min(hi, max(lo, x))


def stock_key(pol, key):
    st = pol.get("stock") if isinstance(pol, dict) and isinstance(pol.get("stock"), dict) else {}
    return st[key] if key in st else FALLBACK.get(key)


def floor_ok_since(ts, pol, now):
    """When both floors came at or under FLOOR_OK (kept while they stay there), else None."""
    se = pol.get("seller") if isinstance(pol, dict) and isinstance(pol.get("seller"), dict) else {}
    fl, fc = se.get("floor_list"), se.get("floor_cost_mult")
    ok = _num(fl) and _num(fc) and fl <= FLOOR_OK[0] + 1e-9 and fc <= FLOOR_OK[1] + 1e-9
    return (ts if _num(ts) else now) if ok else None


def surge(sp):
    """{free, per_unit, max} from facts' supply_prices, the Patch 3.2 numbers when missing."""
    sp = sp if isinstance(sp, dict) else {}
    return {k: sp[k] if _num(sp.get(k)) else v for k, v in SURGE.items()}


def factor(u1h, sg):
    return round(min(sg["max"], 1 + sg["per_unit"] * max(0, u1h - sg["free"])), 4)


def score(view, catalog, pol, now, shelf=None, units=(), sp=None, surge_units=None, reviews=None, floor_since=None):
    """(rows, price_check). rows: the candidates, best first: {sku, kind, list, s6, s1, o6, rate6, rate1, share,
    held, shelf, shops, cover_h, p_clear, f_now, u1h, u1_other, free_left, margin, score, gates, focus_ok, trend_ok}.
    price_check: the SKUs the price gate looks at, with judged (False until the floors were fixed 6 h ago)."""
    view = view if isinstance(view, dict) else {}
    catalog = catalog if isinstance(catalog, dict) else {}
    st, se = pol["stock"], pol["seller"]
    sg = surge(sp)
    fcm = se["floor_cost_mult"]
    min_margin = stock_key(pol, "min_margin_share")
    h1, h6 = view.get("h1") or 1, view.get("h6") or 6
    pc, ck = view.get("p_clear") or {}, view.get("c_kind") or {}
    shelf_units = shelf.get("units") if isinstance(shelf, dict) and isinstance(shelf.get("units"), dict) else None
    shelf_shops = shelf.get("shops") if isinstance(shelf, dict) and isinstance(shelf.get("shops"), dict) else {}
    hits = reviews.get("sku") if isinstance(reviews, dict) and isinstance(reviews.get("sku"), dict) else {}
    judged = _num(floor_since) and now - floor_since >= FLAG_HELD_H * 3600
    held, old = {}, {}
    for u in units or ():
        if isinstance(u, dict) and u.get("grade") != "expired" and isinstance(u.get("sku"), str):
            held[u["sku"]] = held.get(u["sku"], 0) + 1
            if _num(u.get("supplied_at")) and now - u["supplied_at"] >= FLAG_HELD_H * 3600:
                old.setdefault(u["sku"], []).append(u)
    rows, checks = [], []
    for sku, t in (view.get("sku") or {}).items():
        c = catalog.get(sku)
        if not isinstance(c, list) or len(c) < 4 or not isinstance(t, dict) or t.get("s6", 0) < MIN_SETTLES:
            continue
        kind, lp = c[3], c[2] if _num(c[2]) else None
        shelf_h = c[4] if len(c) > 4 and _num(c[4]) else None
        rate6, rate1 = t["s6"] / h6, t.get("s1", 0) / h1
        momentum = _clamp(rate1 / rate6, 0.5, 2) if rate6 else 1
        on_shelf = shelf_units.get(sku, 0) if shelf_units is not None else None
        cover_h = round(on_shelf / max(rate6, 0.5), 2) if on_shelf is not None else None
        scarcity = 1 if cover_h is None else 2 if cover_h <= 0 else _clamp(2 / cover_h, 0.5, 2)
        u1h = max(t.get("u1", 0), (surge_units or {}).get(sku, 0) if isinstance(surge_units, dict) else 0)
        f_now, free_left = factor(u1h, sg), sg["free"] - u1h
        value = math.sqrt(max(lp or 0, 1) / VALUE_REF)
        sc = rate6 * value * momentum * scarcity * (BONUS if free_left > FREE_LEFT else 1) * (BONUS if hits.get(sku) else 1)
        k = kind if kind in C_FALLBACK else "goods"
        p = t.get("pl6") if (t.get("pl_n") or 0) >= 3 else ((pc.get(k) or {}).get("h6") or (pc.get(k) or {}).get("h24"))
        margin = round(p - (ck.get(k) or C_FALLBACK[k]) * f_now * fcm, 4) if _num(p) else None
        gates = []
        if margin is not None and margin < min_margin:
            gates.append("margin")
        u1_other = max(0, u1h - (t.get("ou1") or 0))
        if u1_other > CONGESTION * max(t.get("s1", 0), rate6):
            gates.append("congested")
        flag = len(old.get(sku, ())) >= FLAG_HELD and t.get("o6", 0) == 0 and t["s6"] >= FLAG_MARKET
        if flag:
            cl = [u["cost"] / lp for u in old[sku] if _num(u.get("cost")) and lp]
            checks.append({"sku": sku, "held": held.get(sku, 0), "s6": t["s6"], "p_clear": p,
                           "our_floor_list": round(max(se["floor_list"], fcm * sum(cl) / len(cl)), 3) if cl else None,
                           "judged": bool(judged)})
            if judged:
                gates.append("price")
        rows.append({"sku": sku, "kind": kind, "list": lp, "s6": t["s6"], "s1": t.get("s1", 0), "o6": t.get("o6", 0),
                     "rate6": round(rate6, 2), "rate1": round(rate1, 2),
                     "share": round(t.get("o6", 0) / t["s6"], 3), "held": held.get(sku, 0), "shelf": on_shelf,
                     "shops": shelf_shops.get(sku) if shelf_units is not None else None, "cover_h": cover_h,
                     "p_clear": p, "f_now": f_now, "u1h": u1h, "u1_other": u1_other, "free_left": free_left,
                     "margin": margin, "value": round(value, 3),
                     "score": round(sc, 3), "gates": gates,
                     "focus_ok": kind != "food" or (shelf_h is not None and shelf_h >= st["food_shelf_hours"]),
                     "trend_ok": kind != "food" or (shelf_h is not None and shelf_h >= TREND_FOOD_SHELF_H)})
    rows.sort(key=lambda r: (-r["score"], -r["s6"], r["sku"]))
    return rows, checks


def _pick(rows, current, n, min_settles, swaps, covered):
    """focus.choose's rule over any list: keep a current SKU ranked within KEEP_RANK x n (always while not
    covered), fill free places with the best new SKUs having min_settles, at most swaps of them."""
    rank = {r["sku"]: i for i, r in enumerate(rows)}
    keep = [s for s in current if not covered or rank.get(s, math.inf) < KEEP_RANK * n]
    keep = sorted(keep, key=lambda s: rank.get(s, math.inf))[:n] if len(keep) > n else keep
    new = [r["sku"] for r in rows if r["s6"] >= min_settles and r["sku"] not in keep]
    return keep + new[:max(0, min(swaps, n - len(keep)))]


def choose(rows, focus_now, trend_now, pol, covered=True):
    """(focus_skus, trend_skus) from score's rows."""
    st = pol["stock"]
    ok = [r for r in rows if not r["gates"]]
    focus = _pick([r for r in ok if r["focus_ok"]], list(focus_now), st["focus_max"], st["focus_min_settles"],
                  FOCUS_SWAPS, covered)
    rest = [r for r in ok if r["trend_ok"] and r["sku"] not in focus]
    trend = _pick(rest, [s for s in trend_now if s not in focus], int(stock_key(pol, "trend_max") or 0),
                  MIN_SETTLES, TREND_SWAPS, covered)
    return focus, trend
