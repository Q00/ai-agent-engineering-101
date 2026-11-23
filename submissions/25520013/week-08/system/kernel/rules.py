"""Rule check for every seller and buyer decision (human code, not editable by the model).

Only game rules and output shape are checked here: allowed actions, unit and shop ids that exist, positive int
prices, the order budget. No strategy: a price below cost is allowed, the model decides. Any error replaces the whole
decision with the do-nothing action in SAFE.
"""

import copy

from kernel import common as c

MAX_MESSAGE = 600
MAX_CANDIDATES = 6
MAX_OFFERS = 8

SAFE = {
    "seller.open": {"action": "decline", "message": "Sorry, we can't take this order."},
    "seller.respond": {"action": "reject", "message": "No deal."},
    "buyer.plan": {"candidates": []},
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
        '"offers": [<보낼 제안 가격, 양의 정수, 보낼 순서대로, 1~8개, 각각 max_pay 이하>], '
        '"max_pay": <양의 정수, 예산 이하, 가게가 부른 값이 이 이하이면 커널이 바로 받음>}, ...]} '
        ', "accept": {"cert": <true|false>, "min_hours_left": <0 이상 수>, "grades": [<"A"|"B"|"C">], "warranty_hours": <0 이상 수>}} '
        "(후보는 최대 6개, 목록 순서대로 시도, 빈 목록이면 사지 않음. accept는 선택이며 넣은 항목만 키트가 결제 전에 확인하고 어긋나면 돈을 내지 않음)"
    ),
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
        offers = cand.get("offers")
        if not isinstance(offers, list) or not 1 <= len(offers) <= MAX_OFFERS:
            errors.append(f"candidate {i}: offers must be a list of 1 to {MAX_OFFERS}")
            continue
        if not all(_pos_int(o) for o in offers):
            errors.append(f"candidate {i}: every offer must be a positive int")
            continue
        if any(o > max_pay for o in offers):
            errors.append(f"candidate {i}: offer above max_pay")
            continue
        out.append({"shop": shop, "sku": sku, "offers": list(offers), "max_pay": max_pay})
    result = {"candidates": out}
    if "accept" in raw:
        acc = _accept(raw["accept"], errors)
        if acc:
            result["accept"] = acc
    return result


def _accept(a, errors):
    """Checkout conditions the kit enforces before paying (official agent.md). Shape only; values are the model's."""
    if not isinstance(a, dict):
        errors.append("accept must be an object")
        return {}
    out = {}
    for k, v in a.items():
        if k == "cert":
            if not isinstance(v, bool):
                errors.append("accept.cert must be true or false")
            else:
                out[k] = v
        elif k in ("min_hours_left", "warranty_hours"):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                errors.append(f"accept.{k} must be a number >= 0")
            else:
                out[k] = v
        elif k == "grades":
            if not isinstance(v, list) or not v or any(g not in ("A", "B", "C") for g in v):
                errors.append("accept.grades must be a non-empty list of A, B, C")
            else:
                out[k] = sorted(set(v))
        else:
            errors.append(f"accept.{k} is not a known condition")
    return out


_CHECKS = {
    "seller.open": _seller_open,
    "seller.respond": _seller_respond,
    "buyer.plan": _buyer_plan,
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
