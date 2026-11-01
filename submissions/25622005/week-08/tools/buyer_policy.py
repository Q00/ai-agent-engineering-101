"""Buyer decisions as pure functions (strategy §4, D6, D7, D8, D12).

    candidates     offers ∩ acceptable, ranked by expected surplus x chance of closing
    cap            the highest price worth paying for one SKU (D6)
    p_success      chance of closing = p_price x p_checkout (D8, P0 form)
    verify_receipt does a signed receipt fit the order and what was agreed
    build_accept   the accept terms the kit checks before paying (D7, D12)
    rate_deal      stars and review text from facts only (§4.5)

Value is always the list price of the SKU that will ship, never the budget. Numbers come from
the policy (strategy/SKILL.md); the few priors below are assumptions until the market shows more.
"""
from statistics import median

from memory import query

Q_MIN_N = 30           # D6: replace the prior by the category's q upper bound from this many observations
WARRANTY_FULL_H = 6     # goods break within 6 h of the sale, so a 6 h warranty covers every break
REACH_LO, REACH_HI = 0.50, 0.85   # P0 prior for p_price before any quote: a cap below half of list rarely
                                  # closes, at a typical first ask (0.85 of list) it always can
PRICE_GAP_LIST = 0.15   # D8 p_price falls linearly to 0 when the ask is this share of list above the cap


def list_price(catalog, sku):
    v = (catalog.get(sku) or {}).get("list_price")
    return int(v) if isinstance(v, (int, float)) and v > 0 else None


def cap_share(kind, warranty_h, q_upper, pol):
    """D6 price cap as a share of the shipped SKU's list price."""
    b = pol["buyer"]
    if kind == "food":
        return b["cap_food"]
    if warranty_h and warranty_h >= WARRANTY_FULL_H:
        return b["cap_goods_warranty"]
    if q_upper is not None:                      # measured break rate replaces the 0.70 start value
        return max(0.0, 1 - q_upper - b["min_surplus_list"])
    return b["cap_goods_no_warranty"]


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


def goods_q(summary, leaf, pol):
    """(q for expected surplus, q_upper for the cap or None while there are too few observations)."""
    q = query.category_stats(summary, leaf).get("q") or {}
    if q.get("n", 0) >= Q_MIN_N and q.get("upper") is not None:
        return q["upper"], q["upper"]
    return q_prior(pol), None


def candidates(order, shops, catalog, summary, pol):
    """One best SKU per shop from offers ∩ acceptable, best first, at most buyer.max_shops shops."""
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
            surplus = expected_surplus(V, c, k, q if k != "food" else 0.0, 0)
            if c <= 0 or surplus < pol["buyer"]["min_surplus_list"] * V - 1:
                continue
            pr = p_reach(c, V)
            score = surplus * pc * pr
            cand = {"shop": url, "shop_name": shop.get("name"), "sku": sku,
                    "name": offer.get("name") or catalog[sku].get("name") or sku, "V": V, "kind": k,
                    "cap": c, "q": q, "q_upper": q_upper, "surplus_at_cap": round(surplus),
                    "p_checkout": round(pc, 4), "p_reach": round(pr, 4), "score": round(score, 1),
                    "shop_stats": stats,
                    "memory_refs": [query.summary_ref("shop", url), query.summary_ref("cat", leaf)],
                    "why": f"E[S] at cap {round(surplus)} x p_checkout {pc:.2f} x p_reach {pr:.2f}"}
            if url not in best or cand["score"] > best[url]["score"]:
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
