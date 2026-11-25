"""Plan A, the near-miss accept band (owner 11/08, telegram 7918 "바로 해보자 이거 중요해";
review/W-20261108-1645-AX성사율원인/report.md section 5A).

Of 1,925 orders that ended with no deal 11/07 16:43 - 11/08 16:43, about 89% ended on price: the shop's last ask was
1-10% over my limit (list x (1 - 0.30 / (1 - b)), about 0.68-0.70 of list) while the buyers ahead of us pay 0.70-0.76 of
list for goods. Goods converted 21%, food 81%.

The band: my own offers stay as they were (the oneshot steps, then one raise to the limit). Where the talk would then
end, a shop's final ask over my limit but at or under 105% of it (and never over the order's budget) is taken instead of
walking away; only what I take moves. The receipt check (market_buyer.over_limit) pays up to the same ceiling.
Everything else holds: hard_limits, the goods 6 h warranty (warranty_short, the short-warranty shop skip), the budget.

Switch: .market/buyer_accept_band.json {"enabled": true}; missing, unreadable or anything else is off, and off is the
old behaviour exactly. BUYER_ACCEPT_BAND=0 in the environment is off too. Read at every order (a buyer run is one
order), so no restart either way.
"""
import json
import os

SWITCH = "buyer_accept_band.json"
PERCENT = 105


def percent(md, env=None):
    """PERCENT when the switch is on, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_ACCEPT_BAND") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return PERCENT if isinstance(d, dict) and d.get("enabled") is True else None


def ceiling(limit, budget, pct, cap=None):
    """The most I take: pct% of the limit, rounded down, never over the order's budget nor cap (market_buyer.value_limit:
    with max_price_share under 0.7 the limit is value_limit and 105% of it would pass it, 11/08 18:12) nor under the
    limit."""
    if pct is None or not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return limit
    top = limit * pct // 100
    for most in (budget, cap):
        if isinstance(most, (int, float)) and not isinstance(most, bool):
            top = min(top, int(most))
    return max(limit, top)


def turn(baseline, history, limit, top, steps):
    """(the turn, a note or None). Where the turn would walk away, takes the shop's ask when it is over my limit and at
    or under top and my steps are spent: the shop's final ask, after my steps and my one raise to the limit. A shop that
    already turned down its own ask (my last offer was that ask) is left to walk, as before; any other turn, and a
    harness turn (None), is left alone. The taken turn carries "accept_band": True, which lets buyer.talk send it."""
    if not isinstance(baseline, dict) or baseline.get("action") != "walk_away":
        return baseline, None
    if not isinstance(top, int) or not isinstance(limit, int) or top <= limit:
        return baseline, None
    ask = baseline.get("shop_price")
    if type(ask) is not int or not limit < ask <= top:
        return baseline, None
    offers = [p for who, p in history if who == "you offered"]
    if not offers or len(offers) < len(steps or ()) or offers[-1] >= ask:
        return baseline, None
    return ({**baseline, "action": "offer", "price": ask, "accept_band": True},
            {"taken": ask, "limit": limit, "ceiling": top})
