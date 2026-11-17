"""Round 3 read-only planning hook; stdout is exactly one market-kit JSON object.

Uses seconds since Unix epoch. Prices are credits. It never requests supply,
opens private keys, updates inventory, or treats negotiations as settled sales.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path


class InputError(ValueError):
    pass


def number(value, label, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise InputError(f"{label}: expected finite number >= {minimum}")
    return value


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Config:
    reserve_floor: int = 500_000
    reserve_fraction: float = 0.5
    spend_fraction: float = 0.20
    exploration_fraction: float = 0.05
    max_units: int = 20
    max_sku_units: int = 4
    max_category_units: int = 6
    max_lines: int = 3
    horizon_hours: float = 25 / 60
    lookback_hours: float = 6
    safety_factor: float = 1.25
    min_food_hours_left: float = 3
    # Explicit conservative *assumptions* for a NEW unit, never factual TTLs.
    food_life_hours_by_sku: dict = field(default_factory=dict)
    storage_days: float = 1
    refund_risk_fraction: float = 0.10
    max_snapshot_age: float = 60
    close_guard_seconds: float = 3

    def __post_init__(self):
        for key in ("reserve_fraction", "spend_fraction", "exploration_fraction", "refund_risk_fraction"):
            if not 0 <= number(getattr(self, key), key) <= 1:
                raise InputError(f"{key}: expected fraction in [0, 1]")
        for key in ("reserve_floor", "max_units", "max_sku_units", "max_category_units", "max_lines"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 0:
                raise InputError(f"{key}: expected nonnegative integer")
        if self.max_lines > 10:
            raise InputError("max_lines must be <= kit limit 10")
        for key in ("horizon_hours", "lookback_hours", "safety_factor", "max_snapshot_age"):
            number(getattr(self, key), key, 0.000001)
        for key in ("min_food_hours_left", "storage_days", "close_guard_seconds"):
            number(getattr(self, key), key)
        if not isinstance(self.food_life_hours_by_sku, dict):
            raise InputError("food_life_hours_by_sku must be an object")
        for sku, hours in self.food_life_hours_by_sku.items():
            if not isinstance(sku, str):
                raise InputError("food life SKU must be a string")
            number(hours, f"food life {sku}", 0.000001)


def supply_window(inv, now, cfg):
    w = inv.get("restock")
    if not isinstance(w, dict) or type(w.get("open")) is not bool:
        raise InputError("inventory.restock.open missing or invalid")
    opens = number(w.get("opens_at"), "restock.opens_at")
    closes = number(w.get("closes_at"), "restock.closes_at")
    if closes <= opens:
        raise InputError("invalid supply interval")
    if not w["open"] or now < opens:
        return "supply_closed"
    if now + cfg.close_guard_seconds >= closes:
        return "supply_closing"
    return "open"


def validate(inv, catalog, demand, now, cfg):
    if not isinstance(inv, dict) or type(inv.get("round")) is not int or inv["round"] != 3:
        raise InputError("explicit round=3 required")
    number(inv.get("balance"), "balance")
    if not isinstance(inv.get("units"), list):
        raise InputError("inventory.units must be a list")
    if not isinstance(catalog, dict) or not isinstance(demand, list):
        raise InputError("catalog object and demand list required")
    seen = set()
    for u in inv["units"]:
        if not isinstance(u, dict) or not isinstance(u.get("unit"), str) or u["unit"] in seen:
            raise InputError("unit identity missing or duplicate")
        seen.add(u["unit"])
        if not isinstance(u.get("sku"), str) or u.get("kind") not in ("goods", "food"):
            raise InputError("invalid stock SKU/kind")
        number(u.get("cost"), "unit.cost")
        if u["kind"] == "food":
            number(u.get("expires_at"), "unit.expires_at")
    for sku, p in catalog.items():
        if not isinstance(sku, str) or not isinstance(p, dict):
            raise InputError("catalog must map SKU strings to objects")
    if any(not isinstance(d, dict) or not isinstance(d.get("category"), str) for d in demand):
        raise InputError("demand rows need category strings")


def sale_stats(history, now, cfg):
    """Accept only reconciled Round 3 seller settlements in the current market run.

    Repeated identical event ids deduplicate. Conflicting ids fail closed.
    Refunds retain demand evidence but reduce observed realized selling price.
    """
    if history is None:
        return {}, {}, {}, 0
    if not isinstance(history, dict) or not isinstance(history.get("events"), list):
        raise InputError("history must contain events")
    run = history.get("market_run")
    if not isinstance(run, str) or not run:
        raise InputError("history.market_run required")
    start = number(history.get("observed_since"), "observed_since")
    end = number(history.get("as_of"), "as_of")
    if not start <= end <= now + 5:
        raise InputError("invalid observation interval")
    # Never extrapolate a stale history as if collection had continued.
    if now - end > cfg.max_snapshot_age:
        raise InputError("sale history heartbeat is stale")
    hours = max(0.5, (end - max(start, now - cfg.lookback_hours * 3600)) / 3600)
    unique = {}
    for e in history["events"]:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str):
            raise InputError("sale event id required")
        if e["id"] in unique and unique[e["id"]] != e:
            raise InputError("conflicting sale event id")
        unique[e["id"]] = e
    counts, prices, refunds = Counter(), defaultdict(list), Counter()
    for e in unique.values():
        if e.get("market_run") != run or e.get("role") != "seller" or e.get("round") != 3:
            continue
        if e.get("settled") is not True:
            continue
        ts = number(e.get("ts"), "sale.ts")
        if ts < max(start, now - cfg.lookback_hours * 3600) or ts > end:
            continue
        sku = e.get("sku")
        if not isinstance(sku, str):
            continue
        qty = e.get("qty", 1)
        if type(qty) is not int or not 1 <= qty <= 20:
            raise InputError("sale.qty invalid")
        price = number(e.get("price"), "sale.price")  # per-unit price
        refund = number(e.get("refund", 0), "sale.refund")
        if refund > price:
            raise InputError("refund exceeds unit price")
        counts[sku] += qty
        prices[sku].extend([price - refund] * qty)
        refunds[sku] += qty if refund else 0
    return {k: n / hours for k, n in counts.items()}, dict(prices), dict(refunds), sum(counts.values())


def plan(inv, catalog, demand, history=None, *, now, cfg=None, window_remaining=None):
    cfg = cfg or Config()
    number(now, "now")
    validate(inv, catalog, demand, now, cfg)
    state = supply_window(inv, now, cfg)
    out = {"buy": [], "reason": state, "budget": 0, "estimated_cost_upper": 0,
           "shortages": [], "overstock": [], "skipped": [], "unit_commitment_upper": {},
           "evidence": {"sales": "missing", "demand": "eligible_categories_only"}}
    if state != "open":
        return out
    rates, prices, refunds, n_sales = sale_stats(history, now, cfg)
    out["evidence"]["sales"] = "observed_settlements" if n_sales else "no_eligible_sales"
    out["evidence"]["eligible_sale_units"] = n_sales
    out["evidence"]["cost"] = "upper bound ceil(0.75 * list_price), not an actual quote"
    out["evidence"]["food_life"] = "operator assumptions only; unknown TTL skips new food"
    balance = inv["balance"]
    reserve = max(cfg.reserve_floor, math.ceil(balance * cfg.reserve_fraction))
    fraction = cfg.spend_fraction if n_sales else cfg.exploration_fraction
    budget = max(0, math.floor(min(balance * fraction, balance - reserve)))
    if window_remaining is not None:
        budget = min(budget, max(0, math.floor(number(window_remaining, "window_remaining"))))
    out.update(budget=budget, cash_reserve=reserve)
    live = [u for u in inv["units"] if u.get("grade") != "expired" and
            (u["kind"] != "food" or u["expires_at"] > now)]
    # Goods carrying cost is paid from remaining cash; food costs already left balance.
    carrying = math.ceil(sum(u["cost"] for u in live if u["kind"] == "goods") * 0.01 * cfg.storage_days)
    budget = max(0, budget - carrying)
    out.update(budget=budget, existing_storage_buffer=carrying,
               expired_units=len(inv["units"]) - len(live),
               aging_food=[{"unit": u["unit"], "sku": u["sku"],
                            "hours_left": round((u["expires_at"] - now) / 3600, 4)}
                           for u in live if u["kind"] == "food"
                           and u["expires_at"] - now < cfg.min_food_hours_left * 3600])
    stock = Counter(u["sku"] for u in live)
    category_stock = Counter(u.get("category", catalog.get(u["sku"], {}).get("category")) for u in live)
    eligible = {d["category"] for d in demand}
    cat_rates = Counter()
    for sku, rate in rates.items():
        if sku in catalog:
            cat_rates[catalog[sku].get("category")] += rate
    candidates = []
    for sku, p in sorted(catalog.items()):
        cat, kind = p.get("category"), p.get("kind")
        if cat not in eligible or kind not in ("food", "goods"):
            continue
        try:
            list_price = number(p.get("list_price"), "list_price", 1)
        except InputError:
            out["skipped"].append({"sku": sku, "reason": "invalid_price"})
            continue
        upper = math.ceil(list_price * 0.75)
        rate = rates.get(sku, 0)
        if not rate and cat_rates[cat] > 0:
            out["skipped"].append({"sku": sku, "reason": "category_has_observed_selling_sku"})
            continue
        target = min(cfg.max_sku_units, max(1, math.ceil(rate * cfg.horizon_hours * cfg.safety_factor)))
        if kind == "food":
            life = cfg.food_life_hours_by_sku.get(sku)
            if life is None or life < cfg.min_food_hours_left + cfg.horizon_hours or not rate:
                out["skipped"].append({"sku": sku, "reason": "unknown_or_short_food_life_or_no_sales"})
                continue
            target = min(target, math.floor(rate * max(0, life - cfg.min_food_hours_left)))
        target = max(0, target)
        have = stock[sku]  # also counts near-expiry live food: clear old stock before buying more
        if have > target:
            out["overstock"].append({"sku": sku, "have": have, "target": target})
        need = max(0, target - have)
        cat_target = min(cfg.max_category_units, max(1, math.ceil(cat_rates[cat] * cfg.horizon_hours * cfg.safety_factor)))
        need = min(need, max(0, cat_target - category_stock[cat]))
        if not need:
            continue
        hold_hours = min(cfg.storage_days * 24, 1 / rate) if rate else cfg.storage_days * 24
        storage = upper * 0.01 * hold_hours / 24 if kind == "goods" else 0
        # Unobserved sale prices use a deliberately labeled prior; no profit assertion.
        sale_price = statistics.median(prices[sku]) if sku in prices else list_price * 0.9
        risk = sale_price * cfg.refund_risk_fraction if kind == "goods" else 0
        margin = sale_price - upper - storage - risk
        if margin <= 0:
            out["skipped"].append({"sku": sku, "reason": "nonpositive_risk_adjusted_margin"})
            continue
        out["shortages"].append({"sku": sku, "have": have, "target": target, "need": need})
        # Observed sale velocity takes precedence, then margin per cash committed.
        score = (1 if rate else 0, rate, margin / upper, -upper)
        candidates.append((score, sku, cat, need, upper, math.ceil(storage + risk), cat_target))
    available_slots = max(0, cfg.max_units - len(live))
    used = 0
    for _, sku, cat, need, upper, buffer, cat_target in sorted(candidates, key=lambda x: (x[0], x[1]), reverse=True):
        if len(out["buy"]) >= cfg.max_lines:
            break
        qty = min(need, available_slots, max(0, cat_target - category_stock[cat]),
                  (budget - used) // (upper + buffer))
        if qty <= 0:
            continue
        out["buy"].append({"sku": sku, "qty": int(qty)})
        out["unit_commitment_upper"][sku] = upper + buffer
        used += qty * (upper + buffer)
        category_stock[cat] += qty
        available_slots -= qty
    out["estimated_cost_upper"] = sum(b["qty"] * math.ceil(catalog[b["sku"]]["list_price"] * .75) for b in out["buy"])
    out["cash_commitment_upper"] = used
    out["reason"] = ("restock_shortage" if out["buy"] else "hold_overstock" if out["overstock"]
                     or len(live) >= cfg.max_units else "hold_budget" if not budget else "hold_no_eligible_shortage")
    return out


def read_inputs(state, *, now, cfg, history_path=None):
    state = Path(state)
    inventory_path = state / "inventory.json"
    age = now - inventory_path.stat().st_mtime
    if age > cfg.max_snapshot_age or age < -5:
        raise InputError("inventory snapshot is stale or timestamp is in the future")
    history_path = Path(history_path) if history_path else state / "restock_sales.json"
    history = load_json(history_path) if history_path.exists() else None
    return (load_json(inventory_path), load_json(state / "catalog.json"),
            load_json(state / "demand.json"), history)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", required=True)
    p.add_argument("--config")
    p.add_argument("--history", help="reconciled history JSON; never raw Round 2 logs")
    p.add_argument("--now", type=float, help="fixed epoch seconds for offline replay")
    p.add_argument("--explain", action="store_true", help="diagnostics on stderr")
    a = p.parse_args()
    now = a.now if a.now is not None else time.time()
    try:
        cfg = Config(**load_json(a.config)) if a.config else Config()
        result = plan(*read_inputs(a.state, now=now, cfg=cfg, history_path=a.history), now=now, cfg=cfg)
    except (OSError, ValueError, TypeError) as exc:
        result = {"buy": [], "reason": "invalid_input", "error": str(exc)}
    if a.explain:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), file=sys.stderr)
    print(json.dumps({"buy": result["buy"]}, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()










