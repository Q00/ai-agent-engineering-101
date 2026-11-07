"""Buyer decisions as pure functions (strategy §4, D6, D7, D8, D12).

    candidates     offers ∩ acceptable, ranked by share of list kept x chance of closing
    fold_ask       add a shop's first ask / list to our record of what each shop asks (memory/shop_asks.json)
    cap            the highest price worth paying for one SKU (D6); break_top prices an uncovered break at
                   buyer.break_penalty (Saved counts a deal that served nothing as -1)
    p_success      chance of closing = p_price x p_checkout (D8, P0 form)
    verify_receipt does a signed receipt fit the order and what was agreed
    build_accept   the accept terms the kit checks before paying (D7, D12)
    rate_deal      stars and review text from facts only (§4.5)

Value is always the list price of the SKU that will ship, never the budget. Numbers come from
the policy (strategy/SKILL.md); the few priors below are assumptions until the market shows more.
"""
from statistics import median

from memory import query
from tools.pricing import FOOD_GRADE_CUTS

Q_MIN_N = 30           # D6: replace the prior by the category's q upper bound from this many observations
WARRANTY_FULL_H = 6     # goods break within 6 h of the sale, so a 6 h warranty covers every break
REACH_LO, REACH_HI = 0.50, 0.85   # P0 prior for p_price before any quote: a cap below half of list rarely
                                  # closes, at a typical first ask (0.85 of list) it always can
PRICE_GAP_LIST = 0.15   # D8 p_price falls linearly to 0 when the ask is this share of list above the cap
ASK_ALPHA = 0.25        # weight of the newest first ask in a shop's ask share (exponentially weighted mean)
ASK_MIN_N = 3           # first asks seen from a shop and kind before its ask share replaces the P0 prior
ASK_KEEP_H = 48         # a shop not quoted for this long is forgotten
ASK_SHARE_LO, ASK_SHARE_HI = 0.2, 2.0   # a first ask outside this share of list is a misread price: not folded
# the most shelf life a claimed food grade can have left (agent.md 'Supply' cuts; update by hand if they change)
GRADE_TOP = {"A": 1.0, "B": FOOD_GRADE_CUTS[0], "C": FOOD_GRADE_CUTS[1]}


def list_price(catalog, sku):
    v = (catalog.get(sku) or {}).get("list_price")
    return int(v) if isinstance(v, (int, float)) and v > 0 else None


def kept_floor(pol):
    """The least share of list a deal must keep: max(buyer.min_surplus_list, buyer.min_kept). Saved (Patch 3.4) is
    the average share kept, so a deal below the running average lowers the score; min_kept pins the floor there."""
    b = pol["buyer"]
    return max(b["min_surplus_list"], b.get("min_kept", 0.0))


def break_top(kind, warranty_h, q_upper, pol):
    """The most of list a goods deal may cost when a break counts buyer.break_penalty against Saved (the market's
    rule: a deal that served nothing counts -1, 0 when refunded, so a warranted break costs nothing). With q the
    break chance the warranty leaves uncovered (q_upper, or q_prior before a measurement, times the part of the
    6 h not covered), the expected share kept is (1 - q)(1 - s) - q * penalty; keeping it at the kept floor
    gives s <= 1 - (floor + q * penalty) / (1 - q). 1.0 for food, a full warranty or penalty 0 (the old caps)."""
    b = pol["buyer"]
    pen = b.get("break_penalty", 0.0)
    if kind == "food" or (warranty_h and warranty_h >= WARRANTY_FULL_H) or not pen or pen <= 0:
        return 1.0
    q = q_upper if q_upper is not None else q_prior(pol)
    q = min(1.0, max(0.0, q)) * (1 - min(1.0, (warranty_h or 0) / WARRANTY_FULL_H))
    if q >= 1:
        return 0.0
    return round(max(0.0, 1 - (kept_floor(pol) + q * pen) / (1 - q)), 6)


def cap_share(kind, warranty_h, q_upper, pol):
    """D6 price cap as a share of the shipped SKU's list price, never above 1 - kept_floor, and for goods
    without the full warranty never above break_top (the break chance priced at buyer.break_penalty)."""
    b = pol["buyer"]
    top = round(1 - kept_floor(pol), 6)          # never a cap that leaves less than the minimum share kept
    if kind == "food":
        return min(b["cap_food"], top)
    if warranty_h and warranty_h >= WARRANTY_FULL_H:
        return min(b["cap_goods_warranty"], top)
    top = min(top, break_top(kind, warranty_h, q_upper, pol))
    if q_upper is not None:                      # measured break rate replaces the 0.70 start value
        return max(0.0, min(1 - q_upper - b["min_surplus_list"], top))
    return min(b["cap_goods_no_warranty"], top)


def cap(V, kind, warranty_h, q_upper, budget, pol):
    """Highest whole price worth paying: min(share x V, budget)."""
    return max(0, min(int(V * cap_share(kind, warranty_h, q_upper, pol)), int(budget)))


def expected_surplus(V, p, kind, q, warranty_h):
    """E[S] = (1 - q) V - p + q_w p. Food is worth V when the certificate shows enough hours
    (accept.min_hours_left makes the kit refuse otherwise). A warranty shorter than 6 h is assumed
    to cover breaks in proportion to its length."""
    if kind == "food":
        return V - p
    q_w = q * min(1.0, (warranty_h or 0) / WARRANTY_FULL_H)
    return (1 - q) * V - p + q_w * p


def p_checkout(stats):
    """Share of this shop's agreed deals that the kit paid, with a prior of 9 in 10."""
    return (stats.get("paid", 0) + 9) / (stats.get("attempts", 0) + 10)


def p_price(ask, cap_, V):
    """1 when the ask is at or below the cap; falls to 0 at PRICE_GAP_LIST x V above it."""
    if ask is None or ask <= cap_:
        return 1.0
    return max(0.0, 1 - (ask - cap_) / (PRICE_GAP_LIST * V))


def p_reach(cap_, V):
    """Before any quote: how likely a price at or below the cap is (P0 prior, see REACH_*)."""
    return min(1.0, max(0.0, (cap_ / V - REACH_LO) / (REACH_HI - REACH_LO)))


def p_success(stats, ask, cap_, V):
    """D8: P = P(price agreed) x P(checkout succeeds)."""
    pp, pc = p_price(ask, cap_, V), p_checkout(stats)
    return {"p": round(pp * pc, 4), "p_price": round(pp, 4), "p_checkout": round(pc, 4),
            "n": stats.get("attempts", 0)}


def q_prior(pol):
    """Goods break chance before Q_MIN_N observations: the one at which the start cap leaves exactly
    the minimum surplus (1 - cap_goods_no_warranty - min_surplus_list)."""
    b = pol["buyer"]
    return max(0.0, 1 - b["cap_goods_no_warranty"] - b["min_surplus_list"])


def goods_q(summary, leaf, pol, sku=None, grade=None):
    """(q for expected surplus, q_upper for the cap or None while there are too few observations). With a grade,
    the market's breakage table for the SKU x grade (query.break_q: SKU, then category, then grade) comes first."""
    if grade is not None:
        bq = query.break_q(summary, sku, grade, leaf)
        if bq:
            return bq["q"], bq["upper"]
    q = query.category_stats(summary, leaf).get("q") or {}
    if q.get("n", 0) >= Q_MIN_N and q.get("upper") is not None:
        return q["upper"], q["upper"]
    return q_prior(pol), None


def ask_key(url, kind):
    """memory/shop_asks.json key: the shop URL without a trailing / and the kind (food|goods)."""
    return f"{str(url or '').rstrip('/')}|{kind}"


def _ask_entry(e):
    """A well-formed shop_asks entry {n, share, w, ts}; anything else counts as no record."""
    return isinstance(e, dict) and type(e.get("n")) is int and e["n"] > 0 \
        and type(e.get("share")) in (int, float) and e["share"] > 0 and type(e.get("w", 0)) in (int, float) \
        and type(e.get("ts")) in (int, float)


def fresh_asks(asks, now):
    """The well-formed shop_asks entries updated within ASK_KEEP_H; {} for anything but a dict."""
    return {k: e for k, e in (asks if isinstance(asks, dict) else {}).items()
            if _ask_entry(e) and now - e["ts"] <= ASK_KEEP_H * 3600}


def fold_ask(asks, url, kind, share, now, warranty=False):
    """A new asks dict (fresh_asks) with one more first ask / V of a shop for a kind: the first observation sets
    share and w (1 when the shop granted the full warranty), each later one moves them by ASK_ALPHA. A share
    outside ASK_SHARE_LO..ASK_SHARE_HI is a misread price and is not folded."""
    out = fresh_asks(asks, now)
    if not ASK_SHARE_LO <= share <= ASK_SHARE_HI:
        return out
    key = ask_key(url, kind)
    e = out.get(key)
    w = 1.0 if warranty else 0.0
    if e is not None:
        share = e["share"] + ASK_ALPHA * (share - e["share"])
        w = e.get("w", 0) + ASK_ALPHA * (w - e.get("w", 0))
    out[key] = {"n": (e["n"] if e else 0) + 1, "share": round(share, 4), "w": round(w, 4), "ts": round(now)}
    return out


def candidates(order, shops, catalog, summary, pol, asks=None):
    """One best SKU per shop from offers ∩ acceptable, best first, at most buyer.max_shops shops.

    score = kept x p_checkout x p. kept is the expected share of list kept (Patch 3.4 Saved) at the likely price:
    the shop's settled share of list from the public ledger (query.shop_price: its sales of the SKU, else of
    the category, in 24 h) x V, else its estimated first ask (its ask share in asks x V, from ASK_MIN_N first
    asks of the kind), when below the cap, else the cap. p is p_price of that estimated ask, or the P0 p_reach without one. A goods shop that
    granted the 6 h warranty in most first replies (w >= 0.5) is judged at the warranty cap, as negotiate()
    raises the cap when the warranty comes. Ties go to the higher list price (every food SKU of a shop keeps the
    same share)."""
    acceptable = set(order.get("acceptable") or [])
    leaf = order.get("item")
    kind = order.get("kind")
    budget = order.get("budget") or 0
    q, q_upper = (0.0, None) if kind == "food" else goods_q(summary, leaf, pol)
    best = {}
    for shop in shops or []:
        url = (shop.get("url") or "").rstrip("/")
        if not url:
            continue
        stats = query.shop_stats(summary, url)
        pc = p_checkout(stats)
        for offer in shop.get("offers") or []:
            sku = offer.get("sku")
            V = list_price(catalog, sku)
            if sku not in acceptable or V is None or not offer.get("qty", 1):
                continue
            k = (catalog[sku].get("kind") or kind)
            c = cap(V, k, 0, q_upper, budget, pol)
            qk = q if k != "food" else 0.0
            surplus = expected_surplus(V, c, k, qk, 0)
            # the cap already keeps min_kept of list; the risk-adjusted surplus (q for goods) only has to clear the
            # smaller min_surplus_list, or every goods order without a measured warranty would be dropped unseen
            if c <= 0 or surplus < pol["buyer"]["min_surplus_list"] * V - 1:
                continue
            e = (asks or {}).get(ask_key(url, k))
            known = _ask_entry(e) and e["n"] >= ASK_MIN_N
            settled = query.shop_price(summary, url, sku, leaf)
            ask_est = int(settled["share"] * V) if settled else int(e["share"] * V) if known else None
            wh = WARRANTY_FULL_H if known and k != "food" and e.get("w", 0) >= 0.5 else 0
            c_est = cap(V, k, wh, q_upper, budget, pol) if wh else c
            p_est = c_est if ask_est is None else min(c_est, ask_est)
            kept = expected_surplus(V, p_est, k, qk, wh) / V
            pr = p_reach(c, V) if ask_est is None else p_price(ask_est, c_est, V)
            score = kept * pc * pr
            q_grade = {} if k == "food" else {g: r for g in ("A", "B", "C") if (r := query.break_q(summary, sku, g, leaf))}
            cand = {"shop": url, "shop_name": shop.get("name"), "sku": sku,
                    "name": offer.get("name") or catalog[sku].get("name") or sku, "V": V, "kind": k,
                    "cap": c, "q": q, "q_upper": q_upper, "q_grade": q_grade, "surplus_at_cap": round(surplus),
                    "ask_est": ask_est, "price_src": "ledger" if settled else "ask" if known else None,
                    "kept": round(kept, 4),
                    "p_checkout": round(pc, 4), "p_reach": round(pr, 4), "score": round(score, 4),
                    "shop_stats": stats,
                    "memory_refs": [query.summary_ref("shop", url), query.summary_ref("cat", leaf)],
                    "why": f"kept {kept:.3f} of list at {p_est} x p_checkout {pc:.2f} x "
                           + (f"p_reach {pr:.2f}" if ask_est is None else
                              f"p_price {pr:.2f} ({'settled' if settled else 'ask'} est {ask_est}"
                              + (f", {wh} h warranty cap {c_est})" if wh else ")"))}
            if url not in best or (cand["score"], V) > (best[url]["score"], best[url]["V"]):
                best[url] = cand
    ranked = sorted(best.values(), key=lambda c: (-c["score"], -c["V"], c["shop"]))
    return ranked[:pol["buyer"]["max_shops"]]


def reference_list(order, catalog):
    """Median list price of the order's acceptable SKUs, the base of the budget ratio."""
    prices = [p for p in (list_price(catalog, s) for s in order.get("acceptable") or []) if p]
    return median(prices) if prices else None


def verify_receipt(order, receipt, cand, catalog, pol, promised_unit=None, warranty_h=0):
    """None when the receipt fits; else why not. The kit checks sig_seller, we check the terms."""
    if not isinstance(receipt, dict) or not receipt.get("sig_seller"):
        return "no signed receipt"
    item, price = receipt.get("item"), receipt.get("price")
    if item != order.get("item") and item not in (order.get("acceptable") or []):
        return f"receipt item {item} is neither the order's item nor an acceptable SKU"
    if type(price) is not int or price <= 0:
        return f"receipt price {price!r} is not a whole number"
    if price > order.get("budget", 0):
        return "receipt price above the order's limit"
    if promised_unit and not receipt.get("unit"):
        return f"the shop promised unit {promised_unit} but the receipt names none"
    shelf, need = (catalog.get(item) or {}).get("shelf_hours"), order.get("use_within_hours")
    food_hours = order.get("kind") == "food" and isinstance(need, (int, float))    # build_accept's min_hours_left
    if receipt.get("cert") is False and (pol["buyer"]["accept_cert"] or food_hours):   # the kit would refuse it
        return "the receipt withholds the market certificate (cert: false), which accept asks for"
    claim = receipt.get("claim")
    top = GRADE_TOP.get(claim) if isinstance(claim, str) else None
    if top and isinstance(shelf, (int, float)) and isinstance(need, (int, float)) \
            and top * shelf <= need + pol["buyer"]["food_margin_h"]:  # grades only fall: min_hours_left must fail
        return f"claim {claim} means under {top * shelf:g} h left, the order needs {need} h"
    V = list_price(catalog, item)
    if V is not None:                              # a SKU receipt: the cap of the SKU that ships
        c = cap(V, cand["kind"], warranty_h, cand.get("q_upper"), order.get("budget", 0), pol)
        if price > c:
            return f"price {price} above the cap {c} for {item} (list {V})"
    elif price > cand["cap"]:
        return f"price {price} above the cap {cand['cap']}"
    return None


def build_accept(order, cand, receipt, negotiated_unit, warranty_h, pol):
    """The kit's accept: certificate, food hours (D7), warranty if agreed, and the D12 binding."""
    b = pol["buyer"]
    accept = {}
    if b["accept_cert"]:
        accept["cert"] = True
    if order.get("kind") == "food" and isinstance(order.get("use_within_hours"), (int, float)):
        accept["min_hours_left"] = order["use_within_hours"] + b["food_margin_h"]
    if order.get("kind") != "food" and warranty_h:
        accept["warranty_hours"] = warranty_h
    if b["binding"]:
        bind = {"max_price_list": cap_share(cand["kind"], warranty_h, cand.get("q_upper"), pol)}
        if receipt.get("item") in (order.get("acceptable") or []):
            bind["sku"] = receipt["item"]
        if negotiated_unit:
            bind["unit"] = negotiated_unit
        accept["bind"] = bind
    return accept


def rate_deal(deal_or_receipt, decision_row, provisional=False):
    """(stars, review) from facts only. Our own failures (timeouts, refusals by our policy) never
    reach here: a review is written only for a deal the shop agreed to.

    provisional: market_buyer rates the signed receipt as if it settles as agreed; review.py then
    replaces it with the settled facts of deal.json. decision_row is the buyer's order row, whose
    choice holds what was agreed (sku, unit, V, name)."""
    agreed = (decision_row or {}).get("choice")
    agreed = agreed if isinstance(agreed, dict) else {}
    if provisional:
        receipt, settled = deal_or_receipt, {}
    else:
        receipt, settled = deal_or_receipt.get("receipt") or {}, deal_or_receipt.get("settled") or {}
    price = receipt.get("price")
    sku = settled.get("sku") or receipt.get("item")
    name = settled.get("name") or agreed.get("name") or sku
    V = settled.get("value") or agreed.get("V")
    problems, facts = [], []
    if agreed.get("sku") and settled.get("sku") and settled["sku"] != agreed["sku"]:
        problems.append(f"shipped {settled['sku']}, agreed {agreed['sku']}")
    if receipt.get("unit") and settled.get("unit") and settled["unit"] != receipt["unit"]:
        problems.append(f"shipped unit {settled['unit']}, the receipt named {receipt['unit']}")
    if settled.get("claim_honest") is False:
        problems.append(f"claimed grade {settled.get('claim')}, the market graded {settled.get('grade')}")
    if settled.get("value") == 0:
        problems.append("the market valued it at 0 for this order")
    if problems:
        stars = 1 if problems[0].startswith("shipped") else 2
    elif isinstance(V, (int, float)) and V > 0 and isinstance(price, (int, float)):
        r = (V - price) / V
        stars = 5 if r >= 0.20 else 4 if r >= 0.05 else 3
        facts.append(f"{'agreed' if provisional else 'paid'} {price} for list {V}")
    else:
        stars = 3
        facts.append(f"agreed {price}")
    if provisional:
        facts.append("pending settlement")
    else:
        if settled.get("unit") and settled.get("unit") == receipt.get("unit"):
            facts.append(f"unit {settled['unit']} as named in the receipt")
        if settled.get("grade"):
            facts.append(f"grade {settled['grade']}" + (", claim honest" if settled.get("claim_honest") else ""))
        if settled.get("warranty_hours"):
            facts.append(f"{settled['warranty_hours']} h warranty")
    return stars, (f"{name} ({sku}): " + "; ".join(problems + facts) + ".")[:400]
