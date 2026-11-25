"""B3 + B1, one bundle switched on for every order (owner 11/08, telegram 7883 "1 2순위 함께 적용", 7896 "1시간 해당
전략으로 온전히 바꾸고 모니터링"; review/W-20261108-1355-AX경쟁분석/visitor-behavior.md section 4).

B3: after my one oneshot offer, a shop counter over 0.92 of its first ask is not taken; one more offer at 0.92 of the first
ask (never over my limit, the counter, nor at/under my last offer) goes instead, and the shop's next counter is taken by
oneshot as before. The buyers who beat us on saved held 3-4 rounds and got 3.7-12% off our shop; our buyer's final ask
was a median 0.952 of the first.
B1: the shops quoted after the chosen one rank by (food quote yield, or the default) x the shop's recent paid share
(.market/payments.jsonl joined to measure/orders.jsonl on the order, 6 h, shrunk to the overall share), so a shop whose
deals the kit could not pay goes later. The one slot for a shop that has not fitted stays.

Switch: .market/buyer_b3b1.json {"enabled": true}; missing, unreadable or anything else is off. BUYER_B3B1=0 in the
environment is off too. Read at every order (a buyer run is one order), so no restart either way.
"""
import json
import os
import time
import buyer_goods_risk_sku30
import buyer_goods_quote_yield30
import buyer_goods_quote_inquiry30

SWITCH = "buyer_b3b1.json"
ACCEPT_SHARE = .92
RATE_HOURS = 6
RATE_MIN = 3          # settled tries of a shop before its own share counts
RATE_PRIOR_WEIGHT = 2
RATE_BYTES = 12_000_000   # ~11 h of orders.jsonl (11/08), past the 6 h window


def enabled(md, env=None):
    env = os.environ if env is None else env
    if str(env.get("BUYER_B3B1") or "").strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get("enabled") is True


def b3_turn(baseline, history, limit, probe=False):
    """(the turn, a note or None). Changes only an offer right after my first and only offer; never in an order whose
    opening is a probe (1-won default/probe, opening discount, goods first-one), so those keep their own measurement."""
    if probe or not isinstance(baseline, dict) or baseline.get("action") != "offer":
        return baseline, None
    offers = [p for who, p in history if who == "you offered"]
    asks = [p for who, p in history if who == "shop asked"]
    if len(offers) != 1 or not asks:
        return baseline, None
    first, price, ask = asks[0], baseline.get("price"), baseline.get("shop_price")
    if not all(type(v) is int and v > 0 for v in (first, price, ask, limit)):
        return baseline, None
    target = min(int(first * ACCEPT_SHARE), limit, ask)
    if not offers[0] < target < price:
        return baseline, None
    return {**baseline, "price": target}, {"applied": True, "first": first, "baseline": price, "price": target}


def _tail(path, nbytes=RATE_BYTES):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for x in lines[1:] if size > nbytes else lines:
        try:
            r = json.loads(x)
        except ValueError:
            continue
        if isinstance(r, dict):
            yield r


def paid_rates(md, now=None, hours=RATE_HOURS):
    """({shop: shrunk paid share}, overall share) over the orders passed to the kit in the last hours whose payment line
    is in; ({}, None) when there is nothing to read."""
    now = time.time() if now is None else now
    try:
        paid = {p["order"]: p["paid"] for p in _tail(os.path.join(md, "payments.jsonl"))
                if isinstance(p.get("order"), str) and isinstance(p.get("paid"), bool)}
        tries = {}
        for r in _tail(os.path.join(md, "measure", "orders.jsonl")):
            ts = r.get("ts")
            if buyer_goods_risk_sku30.applied_b(r) or buyer_goods_quote_yield30.applied_b(r) or buyer_goods_quote_inquiry30.applied_b(r):
                continue
            if r.get("passed_to_kit") is True and isinstance(r.get("shop"), str) and isinstance(r.get("order"), str) and \
                    r["order"] in paid and type(ts) in (int, float) and now - hours * 3600 <= ts <= now:
                tries.setdefault(r["shop"], []).append(paid[r["order"]])
    except OSError:
        return {}, None
    n = sum(len(v) for v in tries.values())
    if not n:
        return {}, None
    prior = sum(sum(v) for v in tries.values()) / n
    return {s: (sum(v) + RATE_PRIOR_WEIGHT * prior) / (len(v) + RATE_PRIOR_WEIGHT)
            for s, v in tries.items() if len(v) >= RATE_MIN}, prior
