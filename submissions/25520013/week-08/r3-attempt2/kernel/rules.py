"""Rule check for every seller and buyer decision (human code, not editable by the model).

Only game rules and output shape are checked here: allowed actions, unit and shop ids that exist, positive int
prices, the order budget. No strategy: a price below cost is allowed, the model decides. Any error replaces the whole
decision with the do-nothing action in SAFE.
"""

import copy

from kernel import common as c

MAX_MESSAGE = 600
MAX_CANDIDATES = 6

SAFE = {
    "seller.open": {"action": "decline", "message": "Sorry, we can't take this order."},
    "seller.respond": {"action": "reject", "message": "No deal."},
    "buyer.plan": {"candidates": []},
    "buyer.respond": {"action": "walk"},
}

SCHEMAS = {
    "seller.open": (
        '{"action": "offer" | "decline", "unit": "<ctx의 units 중 하나의 unit 값, offer일 때 필수>", '
        '"ask": <양의 정수, offer일 때 필수>, "message": "<구매자에게 보낼 글, 선택, 600자까지>"}'
    ),
    "seller.respond": (
        '{"action": "accept" | "counter" | "reject", "price": <양의 정수, accept와 counter일 때 필수>, '
        '"message": "<구매자에게 보낼 글, 선택, 600자까지>"}'
    ),
    "buyer.plan": (
        '{"candidates": [{"shop": "<상황의 shops 중 하나의 id>", "sku": "<그 가게 offers의 sku>", '
        '"max_pay": <양의 정수, 예산 이하>}, ...]} (최대 6개, 빈 목록이면 사지 않음)'
    ),
    "buyer.respond": '{"action": "offer" | "accept" | "walk", "price": <양의 정수, 예산 이하, offer와 accept일 때 필수>}',
}


def _pos_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _message(raw, out, errors):
    if "message" not in raw:
        return
    m = raw["message"]
    if not isinstance(m, str):
        errors.append("message must be a string")
        return
    out["message"] = c.mask(m)[:MAX_MESSAGE]


def _seller_open(raw, ctx, errors):
    action = raw.get("action")
    if action not in ("offer", "decline"):
        errors.append(f"action must be offer or decline, got {action!r}")
        return {}
    out = {"action": action}
    if action == "offer":
        units = {u.get("unit") for u in ctx.get("units") or [] if isinstance(u, dict)}
        if raw.get("unit") not in units:
            errors.append(f"unit {raw.get('unit')!r} is not among the candidate units")
        else:
            out["unit"] = raw["unit"]
        if not _pos_int(raw.get("ask")):
            errors.append("ask must be a positive int")
        else:
            out["ask"] = raw["ask"]
    _message(raw, out, errors)
    return out


def _seller_respond(raw, ctx, errors):
    action = raw.get("action")
    if action not in ("accept", "counter", "reject"):
        errors.append(f"action must be accept, counter or reject, got {action!r}")
        return {}
    out = {"action": action}
    if action in ("accept", "counter"):
        if not _pos_int(raw.get("price")):
            errors.append("price must be a positive int")
        else:
            out["price"] = raw["price"]
    _message(raw, out, errors)
    return out


def _budget(ctx, errors):
    b = ctx.get("budget")
    if not isinstance(b, (int, float)) or isinstance(b, bool):
        errors.append("ctx has no numeric budget")
        return None
    return b


def _buyer_plan(raw, ctx, errors):
    cands = raw.get("candidates")
    if not isinstance(cands, list):
        errors.append("candidates must be a list")
        return {}
    if len(cands) > MAX_CANDIDATES:
        errors.append(f"at most {MAX_CANDIDATES} candidates")
        return {}
    budget = _budget(ctx, errors)
    shops = {
        s.get("url"): {
            o.get("sku") for o in s.get("offers") or [] if isinstance(o, dict)
        }
        for s in ctx.get("shops") or []
        if isinstance(s, dict) and s.get("url")
    }
    out = []
    for i, cand in enumerate(cands):
        if not isinstance(cand, dict):
            errors.append(f"candidate {i} must be an object")
            continue
        shop, sku, max_pay = cand.get("shop"), cand.get("sku"), cand.get("max_pay")
        if shop not in shops:
            errors.append(f"candidate {i}: shop is not listed")
            continue
        if sku not in shops[shop]:
            errors.append(f"candidate {i}: sku {sku!r} is not offered by that shop")
            continue
        if not _pos_int(max_pay):
            errors.append(f"candidate {i}: max_pay must be a positive int")
            continue
        if budget is not None and max_pay > budget:
            errors.append(f"candidate {i}: max_pay above budget")
            continue
        out.append({"shop": shop, "sku": sku, "max_pay": max_pay})
    return {"candidates": out}


def _buyer_respond(raw, ctx, errors):
    action = raw.get("action")
    if action not in ("offer", "accept", "walk"):
        errors.append(f"action must be offer, accept or walk, got {action!r}")
        return {}
    out = {"action": action}
    if action in ("offer", "accept"):
        budget = _budget(ctx, errors)
        price = raw.get("price")
        if not _pos_int(price):
            errors.append("price must be a positive int")
        elif budget is not None and price > budget:
            errors.append("price above budget")
        else:
            out["price"] = price
    return out


_CHECKS = {
    "seller.open": _seller_open,
    "seller.respond": _seller_respond,
    "buyer.plan": _buyer_plan,
    "buyer.respond": _buyer_respond,
}


def validate(kind, raw, ctx):
    """Return (decision, errors). With any error the decision is a copy of SAFE[kind]."""
    check = _CHECKS.get(kind)
    if check is None:
        return {}, [f"unknown decision kind {kind!r}"]
    if not isinstance(raw, dict):
        return copy.deepcopy(SAFE[kind]), ["decision must be a JSON object"]
    errors = []
    out = check(raw, ctx or {}, errors)
    if errors:
        return copy.deepcopy(SAFE[kind]), errors
    return out, []
