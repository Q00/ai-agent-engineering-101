"""Seeded single-window comparison on identical inputs and future order traces."""
from __future__ import annotations
import argparse

import copy
import json
import math
import random
from pathlib import Path

import baseline_policy
from restock_policy import Config, load_json, plan, supply_window


def baseline(inv, catalog, demand, seed):
    # Private RNG, no global random state or network dependency.
    namespace = dict(baseline_policy.default_restock.__globals__, CATALOG=catalog,
                     random=random.Random(seed))
    import types
    fn = types.FunctionType(baseline_policy.default_restock.__code__, namespace)
    return fn(copy.deepcopy(inv), copy.deepcopy(demand))


def replay(inv, catalog, buy, orders, *, now, end, assumptions, cfg):
    """Replay the same trace and supplier rules for both policies, FIFO by expiry.

    Ending balance change is a cash measure; acquired stock is expensed at once
    per lecture ranking. Ending inventory cost is separately disclosed.
    Existing-stock carrying cost is included; historical acquisition is not.
    """
    cash = inv["balance"]
    stock = copy.deepcopy(inv["units"])
    initial = cash
    bought = cost = refunds = revenue = expired_cost = storage = sold = missed = 0
    accepted = []
    if supply_window(inv, now, cfg) == "open":
        for b in buy[:10]:
            sku, qty = b["sku"], b["qty"]
            product = catalog[sku]
            unit_cost = math.ceil(product["list_price"] * assumptions["cost_ratio"])
            if not 1 <= qty <= 20 or qty * unit_cost > cash:
                continue
            # Synthetic supplier: every accepted call is 10s after the previous.
            if now + 10 * len(accepted) >= inv["restock"]["closes_at"]:
                break
            accepted.append(b)
            for i in range(qty):
                lifetime = assumptions["food_life_hours_by_sku"].get(sku)
                stock.append({"unit": f"new-{sku}-{i}", "sku": sku, "kind": product["kind"],
                              "cost": unit_cost, "grade": "A", "category": product["category"],
                              "expires_at": now + lifetime * 3600 if lifetime is not None else None,
                              "supplied_at": now, "bought_now": True})
            cash -= qty * unit_cost
            cost += qty * unit_cost
            bought += qty

    last = now

    def advance(ts):
        nonlocal storage, cash, stock, expired_cost, last
        # Pro-rata storage is a simulation assumption; exact market charging cadence is unverified.
        fee = sum(u["cost"] for u in stock if u["kind"] == "goods") * .01 * (ts - last) / 86400
        cash -= fee
        storage += fee
        for u in stock:
            if u["kind"] == "food" and u["expires_at"] <= ts and u.get("bought_now"):
                expired_cost += u["cost"]
        stock = [u for u in stock if u["kind"] != "food" or u["expires_at"] > ts]
        last = ts

    for order in sorted(orders, key=lambda x: x["at"]):
        ts = now + order["at"]
        if not now <= ts <= end:
            raise ValueError("order outside replay interval")
        advance(ts)
        possible = [u for u in stock if u["sku"] == order["sku"] and
                    (u["kind"] != "food" or u["expires_at"] - ts >= order.get("min_hours_left", 0) * 3600)]
        if not possible:
            missed += 1
            continue
        u = min(possible, key=lambda x: (x.get("expires_at") or float("inf"), x.get("supplied_at", now), x["unit"]))
        stock.remove(u)
        sold += 1
        revenue += order["price"]
        cash += order["price"]
        refund = order.get("refund", 0)
        refunds += refund
        cash -= refund
    advance(end)
    return {"bought_units": bought, "purchase_cost": cost, "sold_units": sold, "missed_orders": missed,
            "revenue": revenue, "refunds": refunds, "storage_cost": round(storage, 4),
            "expired_new_stock_cost": expired_cost,
            "ending_new_inventory_cost": sum(u["cost"] for u in stock if u.get("bought_now")),
            "ending_inventory_units": len(stock), "cash_delta": round(cash - initial, 4),
            "accepted_buy": accepted}


def compare(fixture):
    cfg = Config(**fixture["config"])
    inv, catalog, demand, history = (fixture[k] for k in ("inventory", "catalog", "demand", "history"))
    now, end = fixture["now"], fixture["now"] + fixture["duration_seconds"]
    improved = plan(inv, catalog, demand, history, now=now, cfg=cfg)
    rows = []
    for seed in fixture["seeds"]:
        old = baseline(inv, catalog, demand, seed)
        common = dict(now=now, end=end, assumptions=fixture["assumptions"], cfg=cfg)
        a = replay(inv, catalog, old, fixture["future_orders"], **common)
        b = replay(inv, catalog, improved["buy"], fixture["future_orders"], **common)
        rows.append({"seed": seed, "baseline_buy": old, "improved_buy": improved["buy"],
                     "baseline": a, "improved": b, "cash_delta_difference": round(b["cash_delta"] - a["cash_delta"], 4)})
    return {"name": fixture["name"], "data_kind": "synthetic_scenario_not_market_measurement",
            "assumptions": fixture["assumptions"], "improved_plan": improved, "runs": rows,
            "mean_cash_delta_difference": round(sum(r["cash_delta_difference"] for r in rows) / len(rows), 4)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixtures", default=str(Path(__file__).with_name("fixtures")))
    p.add_argument("--output")
    a = p.parse_args()
    results = [compare(load_json(f)) for f in sorted(Path(a.fixtures).glob("*.json"))]
    output = {"scope": "single supply window; held-out synthetic future trace; seeds fixed",
              "limitations": ["No market profitability claim", "No actual market balances or histories supplied",
                              "No causal improvement estimate", "Cost=65% list and prorated storage are simulation assumptions",
                              "Simulation does not model grade-dependent buyer selection or multi-shop competition"],
              "scenarios": results}
    text = json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
