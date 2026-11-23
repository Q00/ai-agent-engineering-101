"""Why the seller answered "not in stock" for a SKU it holds (person's decision 11/09 ~15:15), at the moment of the
answer (seller_r3, logged with the substitute experiment's offer / no_offer line, field "cause"):
  lock     every held unit of the SKU is in another open negotiation (incl. one waiting for the 5-min expiry)
  receipt  a held unit is in the receipt hold (sold with a receipt, waiting for the payment; receipt_hold.json)
  policy   a held unit kept back by a selling rule (none today: the high-price count and the brakes limit buying only)
  other    no held unit at that moment (the inventory file changed in between, a unit just sold, ...) or a free one
"""


def classify(x, units, open_, sold, now, expire_s):
    mine = [u for u in units if u.get("sku") == x and u.get("grade") != "expired"]
    ids = {u["unit"] for u in mine}
    lock_age = [now - (d.get("t") or now) for d in open_.values()
                if d.get("unit") and d["unit"].get("unit") in ids]
    locked = len(lock_age)
    receipt = sum(1 for u in ids if u in sold)
    free = len(ids) - locked - receipt
    if not ids or free > 0:
        cause = "other"
    elif locked:
        cause = "lock"
    elif receipt:
        cause = "receipt"
    else:
        cause = "policy"
    return {"cause": cause, "held": len(ids), "locked": locked, "receipt": receipt,
            "lock_age_max": round(max(lock_age), 1) if lock_age else None,
            "near_expiry": sum(1 for a in lock_age if a > expire_s * 0.5)}
