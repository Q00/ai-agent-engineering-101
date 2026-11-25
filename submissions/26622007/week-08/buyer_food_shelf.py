"""Food: a shop whose last unit of this SKU the kit would not pay for, for too little shelf life, is not picked over a
shop that can be (review/W-20261109-0738-AXPro주기, GPT Pro mw8fgl7t §2·§7 buyer 1).

The kit pays for food only when the unit lasts use_within_hours + SHELF_MARGIN (market_buyer.accept_policy). A shop that
does not say how long its unit lasts gets through shelf_refused, wins on price, and the deal ends unpaid: 11/08 07:41 -
11/09 07:41, 43 food orders ended "not paying: X h of shelf life left, the policy asks for Y" (refused 61 of 1,728 passed
to the kit), 11 of them with another numeric ask at or under the price we agreed. yeonsong2/G3434758a, 11/09 07:12-07:41:
paid, then 499 h, 499 h, 238 h, 241 h left against 540.1 asked, four unpaid in a row.

The last payment line of a shop x SKU within HOURS says what that shop is handing out now: refused for shelf life with
L h left, the unit it sends next is taken to last L less the hours since; paid, nothing is known against it. In arm B a
quote whose shop x SKU is expected short of this order's need leaves the pick when another quote within my limit stays
(an ask over it may end with no talk at all, which is no better than an unpaid try); else nothing changes. Both arms
read the history and note would_drop, so the 9 h reading can compare the orders the change could touch; only B acts.
Arm A and the switch off pick as before. A shop B leaves out gets no new payment line from B, so its evidence renews
from arm A's deals or ages out of the HOURS window.

Switch: .market/buyer_food_shelf.json {"enabled": true, "study": "<id>", "salt": "<salt>"}; missing, unreadable or
anything else is off, and off is the current pick exactly. BUYER_FOOD_SHELF=0 in the environment is off too. Read at
every order, so no restart either way. Arms: sha256(salt:order id), first byte odd is B, fixed before any quote.
"""
import hashlib
import json
import os
import re
import time

SWITCH = "buyer_food_shelf.json"
HOURS = 6
TAIL_BYTES = 12_000_000     # ~11 h of orders.jsonl (11/08), past the 6 h window; payments.jsonl is smaller per order
REFUSED = re.compile(r"not paying: (-?[0-9]+(?:\.[0-9]+)?) h of shelf life left, the policy asks for ([0-9]+(?:\.[0-9]+)?)")


def config(md, env=None):
    """{"study", "salt"} with the switch on, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_FOOD_SHELF") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    study, salt = d.get("study"), d.get("salt")
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt):
        return None
    return {"study": study, "salt": salt}


def arm(cfg, order_id):
    """"A" or "B" for a food order id under the study's salt; None without a study or an id."""
    if not cfg or not isinstance(order_id, str) or not order_id:
        return None
    return "B" if hashlib.sha256(f"{cfg['salt']}:{order_id}".encode("utf-8", "surrogatepass")).digest()[0] % 2 else "A"


def _tail(path, nbytes=TAIL_BYTES):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for x in lines[1:] if size > nbytes else lines:      # the first line of a tail may be cut
        try:
            r = json.loads(x)
        except ValueError:
            continue
        if isinstance(r, dict):
            yield r


def last_left(md, now=None, hours=HOURS):
    """{(shop, sku): hours its next unit is expected to last now, or None when its last payment went through} from the
    last payment line of each food order passed to the kit in the last hours."""
    now = time.time() if now is None else now
    pays = {}
    for p in _tail(os.path.join(md, "payments.jsonl")):
        if isinstance(p.get("order"), str) and isinstance(p.get("paid"), bool):
            pays[p["order"]] = p        # the last line of an order is its final state
    seen = {}
    for r in _tail(os.path.join(md, "measure", "orders.jsonl")):
        ts, o = r.get("ts"), r.get("order")
        if r.get("kind") != "food" or r.get("passed_to_kit") is not True or o not in pays or \
                not isinstance(r.get("shop"), str) or not isinstance(r.get("sku"), str) or \
                type(ts) not in (int, float) or not now - hours * 3600 <= ts <= now:
            continue
        p = pays[o]
        if p["paid"]:
            left = None
        else:
            m = REFUSED.search(str(p.get("reason") or ""))
            if not m:
                continue        # refused for something else: says nothing about shelf life
            left = float(m.group(1)) - (now - ts) / 3600
        key = (r["shop"], r["sku"])
        if key not in seen or ts >= seen[key][0]:
            seen[key] = (ts, left)
    return {k: left for k, (_, left) in seen.items()}


def state(md, order, env=None, now=None):
    """{"study", "arm", "left"} for a food order with a use_within_hours and the switch on, else None. Both arms read the
    history (A only notes what B would do); a history that cannot be read is {} with "error", the arm kept."""
    if (order or {}).get("kind") != "food" or not isinstance(order.get("use_within_hours"), (int, float)):
        return None
    cfg = config(md, env)
    a = arm(cfg, order.get("order"))
    if a is None:
        return None
    st = {"study": cfg["study"], "arm": a, "left": {}}
    try:
        st["left"] = last_left(md, now)
    except Exception as e:  # noqa: BLE001 - an odd line must not take one arm out of the study
        st["error"] = type(e).__name__
    return st


def short(st, shop, sku, need):
    """The hours a quote's next unit is expected to last when that is under need, else None."""
    if not st:
        return None
    left = (st.get("left") or {}).get((shop, sku))
    return left if left is not None and left < need else None


def keep(st, scored, need, fits=lambda s: True):
    """The scored quotes [(surplus, i, shop), ...] the pick chooses from: in arm B those not expected short of need, when
    one of them fits (its ask within my limit); else all of them. Notes st["would_drop"] in both arms, st["dropped"] in B."""
    if not st:
        return scored
    st.update(reached=True, n_scored=len(scored), would_drop=[], dropped=[])
    out = [x for x in scored if short(st, x[2]["name"], (x[2].get("_offer") or {}).get("sku"), need) is None]
    if len(out) == len(scored) or not any(fits(x[2]) for x in out):
        return scored
    st["would_drop"] = sorted({x[2]["name"] for x in scored} - {x[2]["name"] for x in out})
    if st.get("arm") != "B":
        return scored
    st["dropped"] = list(st["would_drop"])
    return out


def measurement(st, picked=None):
    """What orders.jsonl keeps for the 9 h ITT reading; None when the order is outside the study."""
    if not st:
        return None
    return {"study": st["study"], "arm": st["arm"], "reached": bool(st.get("reached")), "n_scored": st.get("n_scored"),
            "would_drop": list(st.get("would_drop") or []), "dropped": list(st.get("dropped") or []),
            "applied": bool(st.get("dropped")), "picked": picked, **({"error": st["error"]} if st.get("error") else {})}
