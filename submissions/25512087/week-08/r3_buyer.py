"""Round 3 buying (week 08, fix 1): which SKU to ask for, and a ladder anchored on what the deal is worth.

Round 3 orders name a category (`item`) and the SKUs that fit it (`acceptable`); shops list what they stock
(`offers` in shops.json). A settled deal is worth the SKU's list price to the order (0 if the food does not last
`use_within_hours` or the goods break), and surplus = value - price. So the anchor is the list price of the SKU we
buy, not the budget: the Round 2 ladder (75/86/94/100 % of the budget) could pay above the value and always gave
the shop a quarter of the saving at once.

- SKU: among the chosen shop's offers that fit the order, the one with the largest expected surplus
  (list price minus EST_PRICE x list price), among those whose expected price fits the budget.
- Shop: the trust score of market_buyer.choose_shop, among shops whose best SKU is worth at least
  NEAR_BEST of the best one on the market (a slightly less valuable SKU at a trusted shop beats an unknown one).
- Ladder: VALUE_RUNGS x list price (the first rung offered twice, B3), then the cap: MAX_SHARE x list price or
  the budget, whichever is lower. A shop's cost is 25-75 % of the list price (by grade), so the lower rungs are signable.
- Accept policy for the kit: the market's certificate always; food must last use_within_hours (+ a margin)
  at checkout, since spoiled food is worth 0.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from negotiation import Decision

VALUE_RUNGS = (0.50, 0.50, 0.60, 0.70, 0.80)   # then the cap
MAX_SHARE = 0.92          # never pay more than 92 % of the value (the deal must leave surplus)
EST_PRICE = 0.70          # what a sale usually costs us, as a share of list price (grade A cost 55-75 % + margin)
NEAR_BEST = 0.80
FOOD_MARGIN_H = 0.5       # settle comes after checkout; keep half an hour in hand


def apply_policy() -> dict:
    """The improvement loop's overrides (clamped by tuning.py): rungs, cap, SKU pick."""
    global VALUE_RUNGS, MAX_SHARE, EST_PRICE, NEAR_BEST, SHELF_STRIKES
    import tuning
    over = tuning.load("buyer")
    r1, step = over.get("rung_1", VALUE_RUNGS[0]), over.get("rung_step", 0.10)
    if "rung_1" in over or "rung_step" in over:
        VALUE_RUNGS = (r1, r1) + tuple(round(r1 + step * i, 4) for i in (1, 2, 3))
    MAX_SHARE = over.get("max_share", MAX_SHARE)
    EST_PRICE = over.get("est_price", EST_PRICE)
    NEAR_BEST = over.get("near_best", NEAR_BEST)
    SHELF_STRIKES = int(over.get("shelf_strikes", SHELF_STRIKES))
    return over


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def r3_order(path: Path = Path(".market/order.json")) -> dict | None:
    """The current order when it is a Round 3 order (it lists acceptable SKUs), else None."""
    o = load_json(path, None)
    return o if isinstance(o, dict) and o.get("acceptable") else None


def value_of(sku: str, catalog: dict) -> int:
    return int((catalog.get(sku) or {}).get("list_price") or 0)


def best_offer(shop: dict, order: dict, catalog: dict) -> dict | None:
    """{"sku", "name", "value"} for the shop's offer with the largest expected surplus within the budget."""
    budget = int(order.get("budget") or 0)
    fits = []
    for o in shop.get("offers") or []:
        sku = o.get("sku")
        v = value_of(sku, catalog)
        if sku in (order.get("acceptable") or []) and v > 0 and (o.get("qty") or 0) > 0:
            fits.append({"sku": sku, "name": o.get("name") or sku, "value": v})
    if not fits:
        return None
    ok = [f for f in fits if math.ceil(EST_PRICE * f["value"]) <= budget]
    if ok:
        return max(ok, key=lambda f: (f["value"] - EST_PRICE * f["value"], f["sku"]))
    return min(fits, key=lambda f: (f["value"], f["sku"]))       # nothing fits: the cheapest, as a long shot


def shortlist(shops: list[dict], order: dict, catalog: dict) -> list[dict]:
    """Shops with a fitting offer whose best SKU is worth at least NEAR_BEST of the market's best;
    each carries its pick under "_pick"."""
    picked = []
    for s in shops:
        b = best_offer(s, order, catalog)
        if b:
            picked.append(dict(s, _pick=b))
    if not picked:
        return []
    budget = int(order.get("budget") or 0)
    top = max(p["_pick"]["value"] for p in picked if math.ceil(EST_PRICE * p["_pick"]["value"]) <= budget) \
        if any(math.ceil(EST_PRICE * p["_pick"]["value"]) <= budget for p in picked) else None
    if top is None:
        return picked
    return [p for p in picked if p["_pick"]["value"] >= NEAR_BEST * top
            and math.ceil(EST_PRICE * p["_pick"]["value"]) <= budget] or picked


DISCOUNT_SHOPS = 3        # 11/06 (Patch 3.5, Saved): hand choose_shop only the shops that sell cheapest to us
UNKNOWN_SHARE = 0.70      # a shop we have no deal with yet is assumed to sell at 70 % of list


def shop_shares(memories: list[dict], last: int = 3000) -> dict[str, float]:
    """11/06: what each shop made us pay, as the median share of list price, over our Round 3 deals.
    Buyer ALL now scores Saved (the average share of list price kept per deal), so the shop's price level matters
    more than how much money the deal is worth."""
    by: dict[str, list[float]] = {}
    for m in memories[-last:]:
        if m.get("round") == 3 and m.get("outcome") == "deal" and m.get("price") and m.get("value"):
            by.setdefault(str(m.get("shop")), []).append(float(m["price"]) / float(m["value"]))
    out = {}
    for shop, v in by.items():
        v.sort()
        out[shop] = v[len(v) // 2]
    return out


def cheapest_shops(cands: list[dict], memories: list[dict], k: int = DISCOUNT_SHOPS) -> list[dict]:
    """The k candidates with the lowest learned price share (unknown shops count as UNKNOWN_SHARE, so new shops
    still get tried). choose_shop then applies trust, blacklists and exploration among them."""
    shares = shop_shares(memories)
    ranked = sorted(cands, key=lambda c: (shares.get(str(c.get("name")), UNKNOWN_SHARE), str(c.get("name"))))
    return ranked[:max(1, k)]


STOCK_MISS = re.compile(r"none of that|no matching|not in stock|out of stock|unreserved|no stock|sold out|재고", re.I)


def no_cert_shops(path: Path = Path(".market/payments.jsonl"), last: int = 300) -> set[str]:
    """Shops whose unit came without the market's certificate in our last `last` payments (the kit refused to pay)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-last:]
    except OSError:
        return set()
    bad = set()
    for raw in lines:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if not r.get("paid") and "no certificate" in str(r.get("reason") or ""):
            bad.add(str(r.get("shop") or "").rstrip("/"))
    return bad


SHELF_STRIKES = 2


def short_shelf_shops(path: Path = Path(".market/payments.jsonl"), strikes: int | None = None,
                      last: int = 300) -> set[str]:
    """11/04: shops that shipped food too short-dated for the order (the kit refused to pay) at least `strikes`
    times in our last `last` payments. Food orders skip them; goods orders still use them."""
    strikes = SHELF_STRIKES if strikes is None else strikes
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-last:]
    except OSError:
        return set()
    n: dict[str, int] = {}
    for raw in lines:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if not r.get("paid") and "shelf life" in str(r.get("reason") or ""):
            u = str(r.get("shop") or "").rstrip("/")
            n[u] = n.get(u, 0) + 1
    return {u for u, k in n.items() if k >= strikes}


def accept_terms(order: dict) -> dict:
    terms: dict[str, Any] = {"cert": True}
    if order.get("kind") == "food" and order.get("use_within_hours"):
        terms["min_hours_left"] = round(float(order["use_within_hours"]) + FOOD_MARGIN_H, 2)
    return terms


def ask_text(pick: dict) -> str:
    return f"I want to buy {pick['name']} ({pick['sku']})"


class ValueLadderPolicy:
    """Offers rise along VALUE_RUNGS x value; an ask at or under the planned rung is taken; never above the cap."""

    MAX_RUNGS = 6
    SHELF = re.compile(r"(\d+(?:\.\d+)?)\s*h(?:ours?)?\s+(?:of\s+)?shelf", re.I)

    def __init__(self, value: int, min_hours: float | None = None) -> None:
        self.value = int(value)
        self.last_offer = 0
        self.min_hours = min_hours      # food: the shelf life the order needs (+ margin); the kit refuses less
        self.last_text = ""

    def ladder(self, budget: int) -> list[int]:
        cap = self.cap(budget)
        out: list[int] = []
        for i, r in enumerate(VALUE_RUNGS):
            x = min(cap, math.floor(r * self.value))
            if not out or x > out[-1] or (i == 1 and x == out[-1]):
                out.append(x)
        if out[-1] < cap:
            out.append(cap)
        return out

    def cap(self, budget: int) -> int:
        return max(1, min(int(budget), math.floor(MAX_SHARE * self.value)))

    def respond(self, item: str, budget: int, ask: int, rounds: int) -> Decision:
        if ask is None:
            return Decision("walk", None, "no ask to answer")
        m = self.SHELF.search(self.last_text or "") if self.min_hours else None
        if m and float(m.group(1)) < self.min_hours:      # 11/04: the kit would refuse to pay; leave now
            return Decision("walk", None, f"shelf: {m.group(1)} h left, the order needs {self.min_hours}")
        ladder, cap = self.ladder(budget), self.cap(budget)
        if rounds >= len(ladder) and ask > cap:
            return Decision("walk", None, f"value: ask {ask} still above {cap} ({MAX_SHARE:.0%} of value {self.value})")
        plan = max(self.last_offer, ladder[min(rounds, len(ladder) - 1)])
        higher = [x for x in ladder[rounds:] if x > plan]
        near = bool(higher) and ask <= min(cap, 1.1 * higher[0])   # ask already within reach of the next rung:
        if ask <= plan or near or (ask <= cap and rounds >= len(ladder) - 1):   # take it (a repeat could end the talk)
            offer, reason = ask, f"ask {ask} is within plan {plan} (value {self.value})"
        else:
            offer, reason = plan, f"rung {rounds + 1}/{len(ladder)}: offer {plan} against ask {ask} (value {self.value})"
        offer = max(0, min(cap, int(offer)))
        self.last_offer = max(self.last_offer, offer)
        return Decision("offer", offer, reason)
