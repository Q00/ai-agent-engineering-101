"""Food probe, approved by the user on 11-10: up to 5 named food SKUs, up to 3 units of each held at a time, never sold below cost x 1.08.
Off unless food_probe.json says so.

It is a stars probe for buyer accounts that buy food elsewhere and never reached us; Book is near zero by design. Units go through the
same restock run as the food trial: they count toward the run's units, the total cap and the ordinary lines, and pay from the run budget
at the contract's worst cost (.75 x list x surge; food pays no storage). Held plus planned probe stock stays within
max_total_estimated_cost at worst cost. Surged SKUs are skipped. Nothing is bought until the running seller reports the cost x 1.08
floor (REQUIRED_FEATURE in strategy-consumer.json): a seller on older code could sell a probe unit nearer cost.
Read on every restock run; anything missing, unreadable or malformed means off, which is today's plan.
"""

from collections import Counter
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "food_probe.json"
SKU = re.compile(r"^G[0-9a-f]{8}$")
MAX_SKUS, MAX_UNITS_PER_SKU, MAX_COST = 5, 3, 400000
# Optional "max_units_per_run" (11-11 about 20:2x KST, an Orchestrator tightening within the user's food approval): at most that many
# probe picks a run, taken one SKU at a time in the file's order (1 of each, then a second of each, ...), so a window's first run no
# longer spends 10 of its 12 units on probe food. Absent means today's picks; present but not an int from 1 to 12 means off.
MAX_UNITS_PER_RUN = 12
# Optional "pick_order": "most_room_first" (11-11 about 22:3x KST, an Orchestrator tightening within the user's food approval): the SKU
# with the most room (units_per_sku minus held) is filled first, ties in the file's order, each SKU filled before the next, within the
# cost limit and max_units_per_run. Under a 4-a-run cap that buys 3+1 units on 2 lines where one SKU at a time takes 4 lines, and the
# probe's lines come out of the run's shared ordinary lines. Absent means the orders above; any other value means off.
PICK_ORDERS = ("most_room_first",)
WORST_COST_SHARE = .75
REQUIRED_FEATURE = "food_floor_108_v1"
FLOOR_MULTIPLE = 1.08


def settings(path=None):
    try:
        value = json.loads(Path(CONFIG if path is None else path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return None
    skus, units, cap = value.get("skus"), value.get("units_per_sku"), value.get("max_total_estimated_cost")
    if (not isinstance(skus, list) or not 1 <= len(skus) <= MAX_SKUS or len(set(skus)) != len(skus)
            or not all(isinstance(s, str) and SKU.match(s) for s in skus)
            or type(units) is not int or not 1 <= units <= MAX_UNITS_PER_SKU
            or type(cap) not in (int, float) or not math.isfinite(cap) or not 0 < cap <= MAX_COST):
        return None
    chosen = {"skus": list(skus), "units_per_sku": units, "max_total_estimated_cost": cap}
    if "max_units_per_run" in value:
        per_run = value["max_units_per_run"]
        if type(per_run) is not int or not 1 <= per_run <= MAX_UNITS_PER_RUN:
            return None
        chosen["max_units_per_run"] = per_run
    if "pick_order" in value:
        if type(value["pick_order"]) is not str or value["pick_order"] not in PICK_ORDERS:
            return None
        chosen["pick_order"] = value["pick_order"]
    return chosen


def floor_on(policy_path=None):
    """Whether r3_policy.json sets the food floor at cost x 1.08 or more (food_pricing_v2.floor_cost_multiple)."""
    try:
        rule = json.loads(Path(policy_path or ROOT / "r3_policy.json").read_text(encoding="utf-8"))["selling"]["food_pricing_v2"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    multiple = rule.get("floor_cost_multiple") if isinstance(rule, dict) else None
    return type(multiple) in (int, float) and math.isfinite(multiple) and FLOOR_MULTIPLE <= multiple <= 1.5


def ready(md, policy_path=None):
    """Whether the seller now running reports the food floor code (it rewrites strategy-consumer.json with its own code's list) and
    the floor is switched on at cost x 1.08 or more. Both are needed: the user's approval says no probe food sells below cost x 1.08."""
    try:
        features = json.loads((Path(md) / "strategy-consumer.json").read_text(encoding="utf-8-sig")).get("features")
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(features, list) and REQUIRED_FEATURE in features and floor_on(policy_path)


def for_run(md, inventory, catalog, path=None, policy_path=None):
    """(picks, what is still waited for). Each pick is one unit: {"sku", "estimated_cost", "probe": True}."""
    try:
        chosen = settings(path)
        if not chosen:
            return [], None
        if not ready(md, policy_path):
            return [], REQUIRED_FEATURE
        surge = {r.get("sku"): r.get("factor") for r in inventory.get("surge") or [] if isinstance(r, dict)}
        units = [u for u in inventory.get("units", []) if u.get("grade") != "expired" and u.get("sku") in chosen["skus"]]
        held = Counter(u["sku"] for u in units)
        spent = sum((catalog.get(u["sku"]) or {}).get("list_price", 0) * WORST_COST_SHARE for u in units)
        picks = []
        eligible = []
        for sku in chosen["skus"]:
            product = catalog.get(sku) or {}
            price = product.get("list_price")
            if product.get("kind") != "food" or sku in surge or type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
                continue
            eligible.append((sku, price * WORST_COST_SHARE, max(0, chosen["units_per_sku"] - held[sku])))
        if chosen.get("pick_order") == "most_room_first":
            # sorted() is stable, so SKUs with equal room keep the file's order. A SKU whose next unit does not fit the cost limit
            # stops there and the next SKU is tried, as in the loop below.
            per_run = chosen.get("max_units_per_run")
            for sku, cost, want in sorted(eligible, key=lambda e: -e[2]):
                for _ in range(want):
                    if per_run is not None and len(picks) >= per_run:
                        return picks, None
                    if spent + cost > chosen["max_total_estimated_cost"]:
                        break
                    picks.append({"sku": sku, "estimated_cost": cost, "probe": True})
                    spent += cost
            return picks, None
        if "max_units_per_run" not in chosen:
            for sku, cost, want in eligible:
                for _ in range(want):
                    if spent + cost > chosen["max_total_estimated_cost"]:
                        break
                    picks.append({"sku": sku, "estimated_cost": cost, "probe": True})
                    spent += cost
            return picks, None
        # One SKU at a time: a SKU whose next unit does not fit the cost limit stops there, as in the loop above.
        stopped = set()
        for round_ in range(chosen["units_per_sku"]):
            for sku, cost, want in eligible:
                if len(picks) >= chosen["max_units_per_run"]:
                    return picks, None
                if sku in stopped or round_ >= want:
                    continue
                if spent + cost > chosen["max_total_estimated_cost"]:
                    stopped.add(sku)
                    continue
                picks.append({"sku": sku, "estimated_cost": cost, "probe": True})
                spent += cost
        return picks, None
    except Exception:
        return [], None
