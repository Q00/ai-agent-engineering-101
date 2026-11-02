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
from pathlib import Path
from typing import Any

from negotiation import Decision

VALUE_RUNGS = (0.50, 0.50, 0.60, 0.70, 0.80)   # then the cap
MAX_SHARE = 0.92          # never pay more than 92 % of the value (the deal must leave surplus)
EST_PRICE = 0.70          # what a sale usually costs us, as a share of list price (grade A cost 55-75 % + margin)
NEAR_BEST = 0.80
FOOD_MARGIN_H = 0.5       # settle comes after checkout; keep half an hour in hand


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

    def __init__(self, value: int) -> None:
        self.value = int(value)
        self.last_offer = 0

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
