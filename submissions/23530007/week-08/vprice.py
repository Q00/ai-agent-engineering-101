"""Price policy of the buyer on the patch 3.6 score (11-12, user: 'rebuild the buyer for the current rule').

A deal scores v = min(1, (list - price) / (list - unit cost)); a warranted break 0, an unwarranted one -1. So the
whole price question is one number per unit: v, the share of the shop's margin we keep. Everything the buyer does
with a price is written in v here and turned into won only at the last moment:

    plan(kind, list_price, sku, grade, m_star, bp, budget, soft)  -> dict
        c        cost / list estimate for this SKU and grade (cost_model: exact costs of the units sold in 72 h)
        floor_v  the least a deal may score and still lift the final saved: (m* + delta) / (1 - p_break(grade));
                 for food m* + food_delta (food does not break; a spoiled unit is refused before the price talk)
        cap      the most we pay: the price at floor_v, within the order's budget
        take_v   an ask that already scores this much is taken at once (the next order only comes when this ends)
        offers   the counters, in v, from open_v down by step_v, `rounds` of them (a soft shop, one that ends talks
                 on low offers, starts halfway between its ask and open_v)
    v_of(price, list_price, c), price_for(v, list_price, c)
    move(plan, ask, n_offers, last_offer, prev_asks) -> ("take", price) | ("offer", price) | ("walk", None)

Knobs (params.json buyer.base, the buyer strategist's): take_v, open_v, step_v, rounds, food_delta, goods_delta,
near_v (tolerance on the receipt), soft_rounds.
"""
BREAK_P = {"A": 0.03, "B": 0.13, "C": 0.40}   # 11-11: 3,909 goods sold 6.5-54 h ago: A 2.3%, B 13.4%, C 34-46%
DEFAULTS = {"take_v": 0.90, "open_v": 0.96, "step_v": 0.05, "rounds": 3, "soft_rounds": 2,
            "food_delta": 0.0, "goods_delta": 0.03, "near_v": 0.03, "anchor_x": 0.45, "anchor_share": 0.5}


def knob(bp, k):
    v = bp.get(k)
    return DEFAULTS[k] if v is None else v


def cost_share(sku, grade, kind):
    try:
        import cost_model
        return float(cost_model.cost_share(sku, grade, kind))
    except Exception:
        return {"A": 0.62, "B": 0.55, "C": 0.48}.get(grade or "B", 0.55)


def v_of(price, lp, c):
    if lp <= 0:
        return 0.0
    if c >= 1:
        return 1.0
    return min(1.0, (1 - price / lp) / (1 - c))


def price_for(v, lp, c):
    return int(lp * (1 - v * (1 - c)))


def floor_v(kind, grade, m_star, bp):
    """11-12 15:00 (user: 'the rule changed a lot, change our buyer policy a lot'): the floor is the BREAK-EVEN v
    (v_floor, the per-deal v at which one more deal neither lifts nor lowers our projected final class rank: the
    saved-mean loss equals the deals-percentile gain), not m* (the mean): goods A deals at EV .745-.80 were refused at
    ~27 orders/h under the m* + .02 floor. Without v_floor it falls back to m*."""
    # 11-12 23:50 (user: 'saved changes in real time; when it moves, wake the buyer or adjust the negotiation'):
    # with v_floor_rel set the floor's base is m* + v_floor_rel, and m* is recomputed from the board's saved every
    # 15 min (buyer_score.py), so the floors follow saved without a model run
    rel = bp.get("v_floor_rel")
    base = (m_star + float(rel)) if rel is not None else (bp.get("v_floor") or m_star)
    if kind == "food":
        return base + knob(bp, "food_delta")
    p = BREAK_P.get(grade or "A", 0.03)
    return (base + knob(bp, "goods_delta")) / (1 - p)


def plan(kind, lp, sku, grade, m_star, bp, budget, soft=False):
    """grade None for goods = the shop has not said: planned as A (70% of goods supply; the receipt check uses the
    claimed grade). Food is planned at the B cost share (the median food grade)."""
    g = grade or ("B" if kind == "food" else "A")
    c = cost_share(sku, g, kind)
    fv = floor_v(kind, g, m_star, bp)
    cap = min(int(budget), price_for(min(fv, 1.0), lp, c)) if fv <= 1.0 else 0
    rounds = int(knob(bp, "soft_rounds" if soft else "rounds"))
    offers = [round(knob(bp, "open_v") - i * knob(bp, "step_v"), 3) for i in range(max(0, rounds))]
    offers = [max(fv, o) for o in offers]
    # 11-12 23:40 A/B 'anchor' (SungJinho's buyer 25620027: .888 per deal vs our .839 over 12 h, same shops, e.g.
    # psyoung82 goods .935 at .62 of list vs ours .79 at .69; its first number is ~.45 of list, under cost, and it takes
    # the shop's counter; shops counter between their ask and the offer, so a low anchor pulls the counter to cost)
    import random
    # 11-13: per kind (food won the A/B: .756 vs .691 per order; peers pay food at .545-.566 of list, we .580)
    share_k = bp.get(f"anchor_share_{kind}")
    anchor = random.random() < (knob(bp, "anchor_share") if share_k is None else share_k)
    return {"kind": kind, "lp": lp, "sku": sku, "grade": g, "c": round(c, 4), "floor_v": round(fv, 4), "cap": cap,
            "take_v": knob(bp, "take_v"), "offers": offers, "soft": soft and not anchor, "impossible": fv > 1.0,
            "near_v": knob(bp, "near_v"), "anchor": anchor, "anchor_x": knob(bp, "anchor_x")}


def move(pl, ask, n_offers, last_offer, prev_asks, hurry=False):
    """One haggling step. `ask` the shop's current price, `n_offers` how many numbers we gave, `last_offer` ours.
    hurry (11-12): the order is near the end of its free 20 s slot; an ask at or under the cap is taken at once."""
    lp, c = pl["lp"], pl["c"]
    if ask is None:
        return "walk", None
    va = v_of(ask, lp, c)
    if ask <= pl["cap"] and (va >= pl["take_v"] or n_offers >= len(pl["offers"]) or hurry):
        return "take", ask
    if n_offers < len(pl["offers"]):
        target_v = pl["offers"][n_offers]
        if pl["soft"] and n_offers == 0:              # a shop that ends talks on low offers: start halfway to its ask
            target_v = (target_v + va) / 2 if va < target_v else target_v
        offer = price_for(target_v, lp, c)
        if pl.get("anchor") and n_offers == 0:        # the first number: far under cost (see plan)
            offer = min(offer, int(lp * pl["anchor_x"]))
        offer = min(offer, pl["cap"] or offer, ask)
        if last_offer:
            offer = max(offer, last_offer)
        if offer >= ask:
            return "take", ask
        return "offer", max(1, offer)
    # out of counters and the ask is still above the cap
    if prev_asks and ask < prev_asks[-1] and pl["cap"]:
        return "offer", min(pl["cap"], ask)          # still coming down: hold at the cap once more
    return "walk", None


def receipt_ok(price, lp, sku, claim, kind, m_star, bp):
    """(ok, v, floor) for a signed receipt: paid when the claimed grade's score is within near_v of its floor."""
    g = claim if claim in BREAK_P else ("B" if kind == "food" else "A")
    c = cost_share(sku, g, kind)
    v = v_of(price, lp, c)
    fv = floor_v(kind, g, m_star, bp)
    return v >= fv - knob(bp, "near_v"), round(v, 4), round(fv, 4)


if __name__ == "__main__":
    bp = {}
    for kind, lp, g in (("goods", 1_000_000, None), ("goods", 1_000_000, "B"), ("goods", 1_000_000, "C"), ("food", 20_000, None)):
        pl = plan(kind, lp, "G0", g, 0.747, bp, 10 ** 9)
        print(kind, g, {k: pl[k] for k in ("c", "floor_v", "cap", "offers", "impossible")}, "cap/list", round(pl["cap"] / lp, 3))
    pl = plan("goods", 1_000_000, "G0", "A", 0.747, bp, 10 ** 9)
    asks, n, last = [720_000], 0, None
    while True:
        act, p = move(pl, asks[-1], n, last, asks[:-1])
        print("ask", asks[-1], "->", act, p, "v", round(v_of(asks[-1], pl["lp"], pl["c"]), 3))
        if act != "offer":
            break
        n, last = n + 1, p
        asks.append(int(asks[-1] * 0.97))
