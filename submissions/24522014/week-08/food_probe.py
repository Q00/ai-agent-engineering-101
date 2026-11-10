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
WORST_COST_SHARE = .75
REQUIRED_FEATURE = "food_floor_108_v1"


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
    return {"skus": list(skus), "units_per_sku": units, "max_total_estimated_cost": cap}


def ready(md):
    """Whether the seller now running reports the cost x 1.08 food floor. It rewrites this file with its own code's list."""
    try:
        features = json.loads((Path(md) / "strategy-consumer.json").read_text(encoding="utf-8-sig")).get("features")
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(features, list) and REQUIRED_FEATURE in features


def for_run(md, inventory, catalog, path=None):
    """(picks, feature still waited for). Each pick is one unit: {"sku", "estimated_cost", "probe": True}."""
    try:
        chosen = settings(path)
        if not chosen:
            return [], None
        if not ready(md):
            return [], REQUIRED_FEATURE
        surge = {r.get("sku"): r.get("factor") for r in inventory.get("surge") or [] if isinstance(r, dict)}
        units = [u for u in inventory.get("units", []) if u.get("grade") != "expired" and u.get("sku") in chosen["skus"]]
        held = Counter(u["sku"] for u in units)
        spent = sum((catalog.get(u["sku"]) or {}).get("list_price", 0) * WORST_COST_SHARE for u in units)
        picks = []
        for sku in chosen["skus"]:
            product = catalog.get(sku) or {}
            price = product.get("list_price")
            if product.get("kind") != "food" or sku in surge or type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
                continue
            for _ in range(max(0, chosen["units_per_sku"] - held[sku])):
                cost = price * WORST_COST_SHARE
                if spent + cost > chosen["max_total_estimated_cost"]:
                    break
                picks.append({"sku": sku, "estimated_cost": cost, "probe": True})
                spent += cost
        return picks, None
    except Exception:
        return [], None
