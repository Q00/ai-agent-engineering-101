"""Multi-shop, negotiated, time-stepped offline competition simulator."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from typing import Any

from mock_market import BASE_TS, STARTING_BALANCE, _catalog, _unit

from .strategies import BuyerStrategy, SellerOfferPolicy, SellerStrategy

HOUR = 3600
BUYER_OWNER = "student-owner"
SELLER_OWNER = "student-seller-owner"
MOCK_BREAK_PROBABILITY = 0.15  # experiment assumption; official probability is unpublished


def deterministic_rng(*parts: Any) -> random.Random:
    digest = hashlib.sha256(":".join(map(str, parts)).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def build_world(seed: int, attempts: int, stress: str = "base") -> list[dict[str, Any]]:
    """Pre-generate exogenous orders; every strategy receives an identical copy."""
    catalog = _catalog()
    skus = list(catalog)
    rows = []
    for tick in range(attempts):
        rng = deterministic_rng("world", seed, tick)
        # Fixed exogenous demand shift: later orders favor food somewhat more.
        weights = ((0.55, 0.28, 0.17) if stress == "short_food" else
                   (0.20, 0.48, 0.32) if tick < attempts // 2 else (0.35, 0.38, 0.27))
        sku = rng.choices(skus, weights=weights, k=1)[0]
        product = catalog[sku]
        budget = round(product["list_price"] * rng.choice((0.78, 0.86, 0.94, 1.02, 1.10)))
        rows.append({
            "order": f"sim-{seed}-{tick:04d}", "tick": tick,
            "at": BASE_TS + tick * HOUR * (3 if stress == "low_demand" else 1),
            "item": product["category"], "category": product["category"], "acceptable": [sku],
            "kind": product["kind"], "budget": budget,
            "use_within_hours": rng.choice((4, 8, 12)) if product["kind"] == "food" else None,
            "expires": BASE_TS + tick * HOUR * (3 if stress == "low_demand" else 1) + 1800, "round": 3,
        })
    return rows


def supply_draw(seed: int, tick: int, sku: str, ordinal: int, product: dict[str, Any], stress: str = "base") -> dict[str, Any]:
    """Official grade/cost ranges with deterministic Mock shelf-life placement."""
    rng = deterministic_rng("supply", seed, tick, sku, ordinal)
    if product["kind"] == "food":
        grade = rng.choices(("A", "B", "C"), weights=(0.25, 0.50, 0.25), k=1)[0]
    else:
        grade = rng.choices(("A", "B", "C"), weights=(0.70, 0.20, 0.10), k=1)[0]
    low, high = {"A": (0.55, 0.75), "B": (0.40, 0.60), "C": (0.25, 0.45)}[grade]
    cost = round(product["list_price"] * rng.uniform(low, high))
    now = BASE_TS + tick * HOUR
    if product["kind"] == "food":
        left = {"A": 0.90, "B": 0.62, "C": 0.32}[grade]
        expires = now + round(product["shelf_hours"] * left * HOUR * (0.60 if stress == "short_food" else 1))
    else:
        expires = None
    unit = _unit(_catalog(), sku, ordinal, cost, grade, now, expires)
    unit["unit"] = f"sim-{seed}-{sku.lower()}-{tick}-{ordinal}"
    unit["cert"] = {"mock": True, "unit": unit["unit"], "sku": sku}
    return unit


def initial_inventory(seed: int) -> dict[str, Any]:
    catalog = _catalog()
    units = [supply_draw(seed, 0, sku, 0, product) for sku, product in catalog.items()]
    cost = sum(unit["cost"] for unit in units)
    return {
        "round": 3, "balance": float(STARTING_BALANCE - cost), "start": STARTING_BALANCE, "injected": 0,
        "storage_per_day": 0.01, "units": units,
    }


def decay_and_charge(inventory: dict[str, Any], catalog: dict[str, Any], now: int, hours: float = 1) -> tuple[int, float]:
    goods_cost = sum(u["cost"] for u in inventory["units"] if u["kind"] == "goods")
    storage = goods_cost * inventory["storage_per_day"] * hours / 24
    inventory["balance"] -= storage
    expired = []
    for unit in inventory["units"]:
        if unit["kind"] != "food":
            continue
        if unit["expires_at"] <= now:
            unit["grade"] = "expired"
            expired.append(unit)
            continue
        fraction = (unit["expires_at"] - now) / (catalog[unit["sku"]]["shelf_hours"] * HOUR)
        unit["grade"] = "A" if fraction >= 0.80 else "B" if fraction >= 0.45 else "C"
    loss = sum(unit["cost"] for unit in expired)
    for unit in expired:
        inventory["units"].remove(unit)
    return loss, storage


def competitor_offers(seed: int, order: dict[str, Any], *, include_same_owner: bool, stress: str = "base") -> list[dict[str, Any]]:
    sku = order["acceptable"][0]
    product = _catalog()[sku]
    owners = [("Competitor North", "owner-north"), ("Competitor South", "owner-south")]
    if include_same_owner:
        owners.append(("Same Owner Shop", BUYER_OWNER))
    rows = []
    for index, (shop, owner) in enumerate(owners):
        rng = deterministic_rng("competitor", seed, order["tick"], index)
        grade = rng.choices(("A", "B", "C"), weights=(0.62, 0.28, 0.10), k=1)[0]
        ask = round(product["list_price"] * rng.uniform(0.78, 1.08))
        floor = round(product["list_price"] * rng.uniform(0.65, 0.82))
        if stress == "competitor_discount":
            ask, floor = round(ask * 0.80), round(floor * 0.80)
        cert_valid = rng.random() >= 0.08
        honest = rng.random() >= 0.06
        claim = grade if honest else ("A" if grade != "A" else "C")
        warranty = rng.choice((0, 0, 3, 6)) if product["kind"] == "goods" else 0
        expires = order["at"] + round(product.get("shelf_hours", 0) * {"A": .9, "B": .62, "C": .3}[grade] * HOUR) \
            if product["kind"] == "food" else None
        rows.append({
            "shop": shop, "owner": owner, "unit": f"competitor-{seed}-{order['tick']}-{index}",
            "sku": sku, "list_price": product["list_price"], "kind": product["kind"], "grade": grade,
            "expires_at": expires, "ask": ask, "floor": min(floor, ask), "concession": rng.uniform(.05, .13),
            "claim": claim, "cert_valid": cert_valid, "warranty_hours": warranty,
            "reliability": rng.uniform(.78, .99),
        })
    return rows


def safe_offer(order: dict[str, Any], offer: dict[str, Any], buyer_owner: str) -> str | None:
    if offer.get("owner") == buyer_owner:
        return "same_owner"
    if offer.get("sku") not in order.get("acceptable", []):
        return "unacceptable_sku"
    if not offer.get("unit"):
        return "missing_unit"
    if offer.get("grade") not in ("A", "B", "C"):
        return "invalid_grade"
    if offer.get("claim") is not None and offer["claim"] != offer["grade"]:
        return "dishonest_claim"
    if not offer.get("cert_valid"):
        return "invalid_certificate"
    if offer.get("kind") == "food":
        if not isinstance(offer.get("expires_at"), (int, float)) or offer["expires_at"] <= order["at"]:
            return "expired_food"
        if offer["expires_at"] < order["at"] + order["use_within_hours"] * HOUR:
            return "insufficient_shelf_life"
    if not isinstance(offer.get("ask"), int) or offer["ask"] < 0:
        return "invalid_price"
    return None


def negotiate(order: dict[str, Any], offer: dict[str, Any], buyer: BuyerStrategy, max_turns: int) -> tuple[int | None, int, str]:
    for turn in range(max_turns):
        bid = buyer.bid(order, offer, turn, max_turns)
        current_ask = max(offer["floor"], round(offer["ask"] * (1 - offer["concession"] * turn)))
        if current_ask > order["budget"] and turn == max_turns - 1:
            return None, turn + 1, "over_budget"
        if bid >= current_ask and current_ask <= order["budget"]:
            return current_ask, turn + 1, "agreed"
    return None, max_turns, "negotiation_failed"


@dataclass
class AttemptRow:
    role: str
    strategy: str
    seed: int
    attempt: int
    order: str
    shop: str | None
    selection_reason: str
    status: str
    reason: str
    price: int | None
    value: int
    surplus: int
    margin: int | None
    turns: int
    balance: float | None
    inventory_units: int | None
    safety_violations: int
    early_half: bool


def _focal_offer(strategy: SellerStrategy, order: dict[str, Any], inventory: dict[str, Any], catalog: dict[str, Any]) \
        -> tuple[dict[str, Any] | None, str]:
    sku = order["acceptable"][0]
    units = [u for u in inventory["units"] if u["sku"] == sku and u["grade"] != "expired"]
    if order["kind"] == "food":
        needed_until = order["at"] + order["use_within_hours"] * HOUR
        units = [u for u in units if isinstance(u.get("expires_at"), (int, float)) and u["expires_at"] >= needed_until]
    if not units:
        return None, "no_inventory"
    unit = min(units, key=lambda u: (u["expires_at"] or math.inf, u["supplied_at"], u["unit"]))
    terms: SellerOfferPolicy = strategy.offer(unit, catalog[sku], order["at"])
    if terms.claim is not None and terms.claim != unit["grade"]:
        return None, "dishonest_claim"
    if terms.floor < 0 or terms.ask < terms.floor or terms.warranty_hours < 0:
        return None, "invalid_terms"
    return {
        "shop": "Focal Shop", "owner": SELLER_OWNER, "unit": unit["unit"], "sku": sku,
        "list_price": unit["list_price"], "kind": unit["kind"], "grade": unit["grade"],
        "expires_at": unit["expires_at"], "ask": terms.ask, "floor": terms.floor,
        "concession": terms.concession, "claim": terms.claim,
        "cert_valid": terms.include_cert and unit["cert"].get("unit") == unit["unit"],
        "warranty_hours": terms.warranty_hours, "reliability": 0.96, "cost": unit["cost"],
    }, "quoted"


def _execute_restock(strategy: SellerStrategy, seed: int, tick: int, order: dict[str, Any], inventory: dict[str, Any],
                     catalog: dict[str, Any], supply_ordinal: dict[str, int], stress: str = "base") -> tuple[int, int]:
    sku = order["acceptable"][0]
    product = catalog[sku]
    stock = sum(1 for u in inventory["units"] if u["sku"] == sku)
    qty = strategy.restock(sku, stock, product, int(inventory["balance"]))
    if not isinstance(qty, int) or qty < 0 or qty > 20:
        return 0, 1
    bought = 0
    call_cap = inventory["balance"] * strategy.max_purchase_share
    stock_cap = STARTING_BALANCE * strategy.max_stock_share
    for _ in range(qty):
        ordinal = supply_ordinal.get(sku, 1)
        unit = supply_draw(seed, tick, sku, ordinal, product, stress)
        current_stock_cost = sum(row["cost"] for row in inventory["units"])
        if (unit["cost"] > inventory["balance"] or bought + unit["cost"] > call_cap
                or current_stock_cost + unit["cost"] > stock_cap):
            break
        inventory["balance"] -= unit["cost"]
        inventory["units"].append(unit)
        supply_ordinal[sku] = ordinal + 1
        bought += unit["cost"]
    return bought, 0


def run_seller_episode(strategy: SellerStrategy, seed: int, attempts: int, max_turns: int = 3, stress: str = "base") \
        -> tuple[list[AttemptRow], dict[str, Any]]:
    catalog = _catalog()
    world = build_world(seed, attempts, stress)
    inventory = initial_inventory(seed)
    initial_units = len(inventory["units"])
    initial_cost = STARTING_BALANCE - inventory["balance"]
    supply_ordinal: dict[str, int] = {}
    fixed_buyer = BuyerStrategy()
    rows: list[AttemptRow] = []
    revenue = margins = sold = expired_loss = storage_cost = warranty_refunds = safety = restock_cost = 0
    scheduled_breaks: list[dict[str, Any]] = []
    for index, order in enumerate(world):
        loss, storage = decay_and_charge(inventory, catalog, order["at"], 1)
        expired_loss += loss
        storage_cost += storage
        for event in [e for e in scheduled_breaks if e["tick"] <= index and not e["done"]]:
            if event["covered"]:
                inventory["balance"] -= event["price"]
                warranty_refunds += event["price"]
            event["done"] = True
        strategy.observe_order(order["acceptable"][0])
        bought, violation = _execute_restock(strategy, seed, index, order, inventory, catalog, supply_ordinal, stress)
        restock_cost += bought
        safety += violation
        focal, focal_reason = _focal_offer(strategy, order, inventory, catalog)
        offers = competitor_offers(seed, order, include_same_owner=False, stress=stress)
        if focal:
            offers.append(focal)
        valid, invalid = [], []
        for offer in offers:
            reason = safe_offer(order, offer, "house-buyer")
            (invalid if reason else valid).append((offer, reason))
        safety += int(focal is not None and any(o[0]["shop"] == "Focal Shop" for o in invalid))
        ranked = fixed_buyer.rank(order, [offer for offer, _ in valid])
        if not ranked:
            strategy.observe_result(order["acceptable"][0], False)
            rows.append(AttemptRow("seller", strategy.code, seed, index, order["order"], None, "none", "rejected",
                                   focal_reason if focal is None else "no_safe_shop", None, 0, 0, None, 0,
                                   inventory["balance"], len(inventory["units"]), safety, index < attempts / 2))
            continue
        _, chosen, select_reason = ranked[0]
        if chosen["shop"] != "Focal Shop":
            strategy.observe_result(order["acceptable"][0], False)
            rows.append(AttemptRow("seller", strategy.code, seed, index, order["order"], chosen["shop"], select_reason,
                                   "lost_competition", "buyer_selected_competitor", None, 0, 0, None, 0,
                                   inventory["balance"], len(inventory["units"]), safety, index < attempts / 2))
            continue
        price, turns, reason = negotiate(order, chosen, fixed_buyer, max_turns)
        if price is None:
            strategy.observe_result(chosen["sku"], False)
            rows.append(AttemptRow("seller", strategy.code, seed, index, order["order"], chosen["shop"], select_reason,
                                   "rejected", reason, None, 0, 0, None, turns, inventory["balance"],
                                   len(inventory["units"]), safety, index < attempts / 2))
            continue
        unit = next((u for u in inventory["units"] if u["unit"] == chosen["unit"]), None)
        if unit is None or chosen["unit"] in {e.get("unit") for e in scheduled_breaks}:
            safety += 1
            strategy.observe_result(chosen["sku"], False)
            continue
        inventory["units"].remove(unit)
        inventory["balance"] += price
        margin = price - unit["cost"]
        revenue += price
        margins += margin
        sold += 1
        strategy.observe_result(chosen["sku"], True, margin)
        break_probability = 0.35 if stress == "high_break" else MOCK_BREAK_PROBABILITY
        breaks = unit["kind"] == "goods" and deterministic_rng("break", seed, unit["unit"]).random() < break_probability
        if breaks:
            scheduled_breaks.append({"tick": index + 2, "unit": unit["unit"], "price": price,
                                     "covered": chosen["warranty_hours"] >= 2, "done": False})
        rows.append(AttemptRow("seller", strategy.code, seed, index, order["order"], chosen["shop"], select_reason,
                               "simulated_settled", "settled", price, unit["list_price"], unit["list_price"] - price,
                               margin, turns, inventory["balance"], len(inventory["units"]), safety,
                               index < attempts / 2))
    remaining_cost = sum(u["cost"] for u in inventory["units"])
    metrics = {
        "role": "seller", "strategy": strategy.code, "seed": seed, "stress": stress, "attempts": attempts,
        "settled": sold, "success_rate": sold / attempts, "revenue": revenue,
        "balance_profit": inventory["balance"] - STARTING_BALANCE,
        "sale_margin": margins, "inventory_purchase_cost": initial_cost + restock_cost,
        "remaining_units": len(inventory["units"]), "remaining_stock_cost": remaining_cost,
        "inventory_turnover": sold / (sold + len(inventory["units"])) if sold + len(inventory["units"]) else 0,
        "expired_loss": expired_loss, "storage_cost": storage_cost, "warranty_refunds": warranty_refunds,
        "stockout_failures": sum(row.reason == "no_inventory" for row in rows),
        "safety_violations": safety, "mock_stars": None, "official_percentile": None,
        "early_success_rate": sum(r.status == "simulated_settled" for r in rows if r.early_half) / max(1, sum(r.early_half for r in rows)),
        "late_success_rate": sum(r.status == "simulated_settled" for r in rows if not r.early_half) / max(1, sum(not r.early_half for r in rows)),
    }
    return rows, metrics


def run_buyer_episode(strategy: BuyerStrategy, seed: int, attempts: int, max_turns: int = 3, stress: str = "base") \
        -> tuple[list[AttemptRow], dict[str, Any]]:
    world = build_world(seed, attempts, stress)
    rows: list[AttemptRow] = []
    settled = paid = value_total = refunds = quality_failures = safety = 0
    selected: dict[str, int] = {}
    for index, order in enumerate(world):
        offers = competitor_offers(seed, order, include_same_owner=True, stress=stress)
        valid = []
        reasons = []
        for offer in offers:
            reason = safe_offer(order, offer, BUYER_OWNER)
            if reason:
                reasons.append(reason)
                if reason not in ("same_owner",):
                    quality_failures += 1
            else:
                valid.append(offer)
        ranked = strategy.rank(order, valid)
        if not ranked:
            reason = reasons[0] if reasons else "no_safe_shop"
            rows.append(AttemptRow("buyer", strategy.code, seed, index, order["order"], None, "none", "rejected",
                                   reason, None, 0, 0, None, 0, None, None, safety, index < attempts / 2))
            continue
        _, offer, select_reason = ranked[0]
        selected[offer["shop"]] = selected.get(offer["shop"], 0) + 1
        price, turns, reason = negotiate(order, offer, strategy, max_turns)
        if price is None:
            strategy.observe(offer["shop"], False, 0, reason)
            rows.append(AttemptRow("buyer", strategy.code, seed, index, order["order"], offer["shop"], select_reason,
                                   "rejected", reason, None, 0, 0, None, turns, None, None, safety,
                                   index < attempts / 2))
            continue
        # Re-run safety at settlement. No strategy can bypass it.
        violation = safe_offer(order, offer, BUYER_OWNER)
        if violation or price > order["budget"]:
            safety += 1
            strategy.observe(offer["shop"], False, 0, violation or "over_budget")
            continue
        product_value = offer["list_price"]
        refund = 0
        break_probability = 0.35 if stress == "high_break" else MOCK_BREAK_PROBABILITY
        broke = offer["kind"] == "goods" and deterministic_rng("buyer-break", seed, offer["unit"]).random() < break_probability
        if broke:
            product_value = 0
            if offer["warranty_hours"] >= 2:
                refund = price
        surplus = product_value - price + refund
        settled += 1
        paid += price
        value_total += product_value
        refunds += refund
        strategy.observe(offer["shop"], True, surplus, "settled")
        rows.append(AttemptRow("buyer", strategy.code, seed, index, order["order"], offer["shop"], select_reason,
                               "simulated_settled", "settled", price, product_value, surplus, None, turns,
                               None, None, safety, index < attempts / 2))
    metrics = {
        "role": "buyer", "strategy": strategy.code, "seed": seed, "stress": stress, "attempts": attempts,
        "settled": settled, "success_rate": settled / attempts, "paid": paid,
        "average_price": paid / settled if settled else 0, "value": value_total,
        "refunds": refunds, "surplus": value_total - paid + refunds,
        "quality_failures": quality_failures, "safety_violations": safety,
        "shop_selection": dict(sorted(selected.items())), "mock_forum_reputation": None,
        "official_percentile": None,
        "early_success_rate": sum(r.status == "simulated_settled" for r in rows if r.early_half) / max(1, sum(r.early_half for r in rows)),
        "late_success_rate": sum(r.status == "simulated_settled" for r in rows if not r.early_half) / max(1, sum(not r.early_half for r in rows)),
    }
    return rows, metrics


def rows_as_dicts(rows: list[AttemptRow]) -> list[dict[str, Any]]:
    return [asdict(row) for row in rows]


def world_fingerprint(seed: int, attempts: int) -> str:
    payload = json.dumps(build_world(seed, attempts), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()

