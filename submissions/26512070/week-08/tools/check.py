"""check.*: the limits of HARNESS.md 6, as tools. The act.* tools run the same checks again before acting.

Limits come from the active strategy (memory/strategy/active.json, limits), never from a prompt. Every
refusal says why and what would pass.
"""
from core import index
from tools import market_files as mf
from tools.registry import Refused, tool

_UNIT = {"type": "string", "minLength": 1}
_PRICE = {"type": "integer", "minimum": 0}


BREAK_WINDOW_H = 6          # agent.md: goods can break up to 6 hours after the sale


def stock_base(lim, inv, balance, stock_value):
    """What max_stock_share is a share of: the starting capital (D31), or with stock_base = equity the balance plus
    the stock at cost (user 11/4 14:0x: at 15.7M balance the cap of 160% of the 5M start left 2.1M to buy while 9M
    of cash sat idle and 35 of 86 visits found no stock), so the cap grows with the shop."""
    if lim.get("stock_base") == "equity" and balance is not None:
        return balance + stock_value
    return inv.get("start") or balance or 0


def warranty_reserve(ctx):
    """Cash kept for refunds (user 11/3): our sales still inside the warranty, times the strategy's reserve share.
    Returns (reserve, open sales total, open count). The settle time is when the collector saw it (a little late,
    so a sale stays open a little longer: on the safe side)."""
    w = ctx.strategy.get("warranty") or {}
    hours, share = w.get("hours"), w.get("reserve_share") or 0
    if not hours or not share:
        return 0, 0, 0
    kinds, cat, now = w.get("kinds") or ["goods"], mf.catalog(ctx), ctx.now()
    hours = min(hours, BREAK_WINDOW_H)   # 11/4 12:4x: goods break only within 6 h of the sale (agent.md), so a
    # longer warranty adds no refund risk past that and holds no more cash
    open_ = [e["data"]["price"] for e in index.events(["seller.settled"], since=now - hours * 3600)
             if now - e["ts"] < hours * 3600 and (cat.get(e["data"].get("sku")) or {}).get("kind") in kinds
             and isinstance(e["data"].get("price"), (int, float))]
    return round(sum(open_) * share), sum(open_), len(open_)


def cost_share(ctx):
    """(expected, worst) cost as a share of list price. Costs are drawn on arrival (A: 55-75% of list); user 11/4
    17:2x (strategy brief 17:15, option A): plan at the expected cost (playbook.restock.cost_share, 65%: bought units
    came in at about 62%), not the worst 75% - at the worst cost a window planned 17.93M and left 33 lines out for
    cash. The worst cost still keeps the plain cash floor (check.buy)."""
    worst = mf.COST_SHARE["A"][1]
    exp = ((ctx.strategy.get("playbook") or {}).get("restock") or {}).get("cost_share") or worst
    return min(exp, worst), worst


def _limits(ctx):
    lim = ctx.strategy.get("limits")
    if not lim:
        raise Refused("no active strategy with limits", "the outer runtime writes memory/strategy/active.json")
    return lim


@tool("check.price", "May the shop sell this unit at this price? Below cost is allowed only in the phase and by the "
      "share the strategy's limits allow, and, when the strategy says so (Welcome Food), only on a buyer's first deal "
      "and only for the kinds and list prices it names.",
      {"type": "object", "required": ["unit", "price"], "properties": {"unit": _UNIT, "price": _PRICE}})
def price(ctx, unit, price):
    u = mf.unit(ctx, unit)
    if u is None:
        raise Refused(f"unit {unit} is not in our stock", "me.inventory lists the units we have")
    if price <= 0:
        raise Refused("a price must be a whole number above 0")
    cost = u["cost"]
    if price >= cost:
        return {"ok_price": price, "cost": cost}
    bc = _limits(ctx)["below_cost"]
    phase = ctx.strategy.get("phase")
    floor = cost - int(cost * bc["max_loss_share_of_cost"])
    if not bc["allowed"] or (bc.get("only_in_phase") and phase != bc["only_in_phase"]):
        raise Refused(f"{price} is below cost {cost}; selling below cost is not allowed in phase {phase!r}",
                      f"ask {cost} or more")
    if bc.get("kinds") and u.get("kind") not in bc["kinds"]:
        raise Refused(f"{price} is below cost {cost}; below cost only for {bc['kinds']}", f"ask {cost} or more")
    if bc.get("max_list_price") is not None and (u.get("list_price") or 0) > bc["max_list_price"]:
        raise Refused(f"{price} is below cost {cost}; below cost only up to list price {bc['max_list_price']}",
                      f"ask {cost} or more")
    if bc.get("grades"):
        now_grade = mf.current_grade(u, (mf.catalog(ctx).get(u["sku"]) or {}).get("shelf_hours"), ctx.now())
        if now_grade not in bc["grades"]:
            raise Refused(f"{price} is below cost {cost}; below cost only for grade {bc['grades']} now, this unit is "
                          f"{now_grade}", f"ask {cost} or more")
    if bc.get("first_deal_only") and ctx.situation.get("first_time") is False:
        raise Refused(f"{price} is below cost {cost}: no first-visit discount, this buyer has dealt with us before",
                      f"ask {cost} or more")
    if price < floor:
        raise Refused(f"{price} loses more than {bc['max_loss_share_of_cost']:.0%} of cost {cost}",
                      f"the lowest allowed price is {floor}")
    return {"ok_price": price, "cost": cost, "below_cost_by": cost - price, "lowest_allowed": floor}


@tool("check.offer", "May our buyer offer this price on this order? Never above the budget (the open mandate's limit).",
      {"type": "object", "required": ["price", "budget"], "properties": {"price": _PRICE, "budget": _PRICE}})
def offer(ctx, price, budget):
    if price <= 0:
        raise Refused("an offer must be a whole number above 0")
    if price > budget:
        raise Refused(f"{price} is over the budget {budget}", f"offer {budget} or less, or walk away")
    return {"ok_price": price, "budget": budget, "left_in_budget": budget - price}


@tool("check.claim", "May the shop claim this grade for this unit? Only the grade it really has now (the market "
      "checks the claim at checkout and settle, and the ledger shows it).",
      {"type": "object", "required": ["unit", "grade"],
       "properties": {"unit": _UNIT, "grade": {"type": "string", "enum": ["A", "B", "C"]}}})
def claim(ctx, unit, grade):
    u = mf.unit(ctx, unit)
    if u is None:
        raise Refused(f"unit {unit} is not in our stock", "me.inventory lists the units we have")
    shelf = (mf.catalog(ctx).get(u["sku"]) or {}).get("shelf_hours")
    now_grade = mf.current_grade(u, shelf, ctx.now())
    if grade != now_grade:
        raise Refused(f"unit {unit} is grade {now_grade} now, not {grade}",
                      f"claim {now_grade}, or make no claim" if now_grade in ("A", "B", "C") else "make no claim")
    return {"unit": unit, "grade": grade}


@tool("check.receipt", "Does the shop's receipt fit what we agreed and the order? A receipt above the agreed price, "
      "for an item the order does not accept, or without the shop's signature is not paid (HARNESS.md 6).",
      {"type": "object", "required": ["receipt", "agreed_price"],
       "properties": {"receipt": {"type": "object"}, "agreed_price": {"type": ["integer", "null"]},
                      "acceptable": {"type": "array"}, "budget": {"type": ["integer", "null"]}}})
def receipt(ctx, receipt, agreed_price, acceptable=None, budget=None):
    p = receipt.get("price")
    if not receipt.get("sig_seller"):
        raise Refused("the receipt has no sig_seller", "the kit will not sign or pay it; pass it on as null")
    if not isinstance(p, int):
        raise Refused(f"the receipt price {p!r} is not a whole number")
    if agreed_price is None:
        raise Refused("there is no agreed price to compare with", "do not pay a receipt for a price we never offered")
    if p > agreed_price:
        raise Refused(f"cheated: agreed {agreed_price}, receipt {p}", "pass the receipt on as null; record the shop")
    if acceptable and receipt.get("item") not in acceptable:
        raise Refused(f"cheated: receipt names {receipt.get('item')!r}, not an SKU the order accepts")
    if budget is not None and p > budget:
        raise Refused(f"cheated: receipt {p} is above the budget {budget}")
    return {"price": p, "agreed_price": agreed_price, "below_agreed_by": agreed_price - p}


def room(ctx):
    """User 11/4 02:1x: the buy list is planned within the money we may spend, so no plan fails for money.
    {cash: balance - cash floor - warranty reserve, stock: stock cap - stock held (at cost), have: units per SKU,
    worst(sku): the cost check.buy counts per unit (the expected cost, cost_share)}. The same numbers check.buy checks."""
    lim = _limits(ctx)
    cat, inv = mf.catalog(ctx), mf.inventory(ctx)
    balance = inv.get("balance")
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    have = {}
    for u in units:
        have[u["sku"]] = have.get(u["sku"], 0) + 1
    reserve = warranty_reserve(ctx)[0]
    base = stock_base(lim, inv, balance, sum(u["cost"] for u in units))
    exp, worst = cost_share(ctx)
    return {"cash": None if balance is None else balance - lim["cash_floor"] - reserve,
            "cash_worst": None if balance is None else balance - lim["cash_floor"],
            "unit_worst": lambda sku: round((cat.get(sku) or {}).get("list_price", 0) * worst * mf.surge_factor(ctx, sku)),
            "stock": lim["max_stock_share"] * base - sum(u["cost"] for u in units), "have": have,
            "per_sku": lim["max_units_per_sku"], "balance": balance, "reserve": reserve, "floor": lim["cash_floor"],
            "worst": lambda sku: round((cat.get(sku) or {}).get("list_price", 0) * exp * mf.surge_factor(ctx, sku))}


@tool("check.buy", "May restock buy this plan? Checks the kit's format (10 lines, 1-20 each) and the limits: units per "
      "SKU, stock value as a share of the balance, food share of the stock, and the cash floor. Costs are not known "
      "before arrival, so it counts the expected cost (the playbook's cost_share of list price) against the cash floor "
      "plus the warranty reserve and the stock cap, and the most a unit can cost (75% of list) against the cash floor.",
      {"type": "object", "required": ["plan"],
       "properties": {"plan": {"type": "array", "items": {
           "type": "object", "required": ["sku", "qty"],
           "properties": {"sku": _UNIT, "qty": {"type": "integer", "minimum": 1, "maximum": 20}}}}}})
def buy(ctx, plan):
    lim = _limits(ctx)
    if len(plan) > 10:
        raise Refused(f"{len(plan)} lines; the kit buys at most 10 per run", "keep the 10 that matter most")
    cat, inv = mf.catalog(ctx), mf.inventory(ctx)
    balance = inv.get("balance")
    if balance is None:
        raise Refused("no balance in inventory.json yet", "wait for the kit's next sync (every 10 s)")
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    have = {}
    for u in units:
        have[u["sku"]] = have.get(u["sku"], 0) + 1
    stock_value = sum(u["cost"] for u in units)
    food_value = sum(u["cost"] for u in units if u.get("kind") == "food")
    food_share_before = food_value / stock_value if stock_value else 0
    spend = spend_worst = 0
    exp, worst_share = cost_share(ctx)
    for line in plan:
        c = cat.get(line["sku"])
        if c is None:
            raise Refused(f"sku {line['sku']} is not in the catalog")
        if have.get(line["sku"], 0) + line["qty"] > lim["max_units_per_sku"]:
            raise Refused(f"{line['sku']}: {have.get(line['sku'], 0)} in stock + {line['qty']} is over "
                          f"{lim['max_units_per_sku']} per SKU", f"buy at most {lim['max_units_per_sku'] - have.get(line['sku'], 0)}")
        have[line["sku"]] = have.get(line["sku"], 0) + line["qty"]     # the same SKU on two lines counts once
        f = mf.surge_factor(ctx, line["sku"])             # patch 3.4 surge cost
        est = round(c["list_price"] * exp * f) * line["qty"]
        spend += est
        spend_worst += round(c["list_price"] * worst_share * f) * line["qty"]
        if c.get("kind") == "food":
            food_value += est
    reserve, open_total, open_n = warranty_reserve(ctx)
    if balance - spend < lim["cash_floor"] + reserve:
        raise Refused(f"up to {spend} would take the balance {balance} below the floor {lim['cash_floor']}"
                      + (f" plus the warranty reserve {reserve} ({open_n} sales, {open_total}, still in warranty)"
                         if reserve else ""), "buy less, or cheaper SKUs")
    if balance - spend_worst < lim["cash_floor"]:
        raise Refused(f"at the worst cost (75% of list) up to {spend_worst} would take the balance {balance} below "
                      f"the floor {lim['cash_floor']}", "buy less, or cheaper SKUs")
    # D31: "40% of the balance (2,000,000)" - of the starting capital, so the cap does not shrink as stock is bought
    base = stock_base(lim, inv, balance, stock_value)
    cap = lim["max_stock_share"] * base
    if stock_value + spend > cap:
        raise Refused(f"stock would be up to {stock_value + spend}, over {lim['max_stock_share']:.0%} of the "
                      f"capital ({round(cap)})", "sell down first or buy less")
    total = stock_value + spend
    share = food_value / total if total else 0
    # Refuse only a plan that pushes the food share up past the limit; a plan that lowers it is the way back.
    if share > lim["food_share_max"] and share > food_share_before:
        raise Refused(f"food would be {share:.0%} of the stock, over {lim['food_share_max']:.0%}",
                      "buy fewer food units or more goods")
    return {"lines": len(plan), "expected_spend": spend, "worst_case_spend": spend_worst,
            "balance_after_at_least": balance - spend_worst,
            "stock_after_at_most": total, "food_share_after_at_most": round(food_value / total, 4) if total else 0}
