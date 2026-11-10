"""Mid-price goods probe, approved by the user on 11-10 (directly, about 19:2x KST; relayed first by the Orchestrator at 18:25): two
named goods SKUs that the active accounts which never rated us buy elsewhere (G0667d440 mattress, G75f11c19 bedding) get a few units
in restock. Off unless mid_probe.json says so.

Supply is bought blind (a unit's cost is drawn on arrival, about .25-.75 of list, median .60), so "buy only when the supply cost is
under the public median" is applied as the user chose on 11-10: a SKU is bought only while its sampled public median is above our
typical goods cost (TYPICAL_COST_SHARE x list x surge), and it buys no more once any unit of it arrived at or above that median.
At most 3 units of each held plus planned, inside the total cap, the run's units and the ordinary lines, paid from the run budget
at worst cost (.75 x list x surge) with seven days of storage; held plus planned at worst cost within max_worst_cost_total (1M at
most). A SKU with a held unit at least 24 h old and none of our completed sales of it in the last 24 h buys no more. The seller's
goods floor is cost plus storage plus 5%; only its long-stock (7 days held) and round-end (last 24 h) clearances go below cost.
Read on every restock run; anything missing, unreadable or malformed means off, which is today's plan.
"""

from collections import Counter
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "mid_probe.json"
ALLOWED = ("G0667d440", "G75f11c19")
MAX_UNITS_PER_SKU = 3
MAX_WORST_COST = 1000000
TYPICAL_COST_SHARE = 0.60
WORST_COST_SHARE = 0.75
STALE_SECONDS = 86400


def number(x):
    return type(x) in (int, float) and math.isfinite(x)


def settings(path=None):
    try:
        value = json.loads(Path(CONFIG if path is None else path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return None
    skus, units, cap = value.get("skus"), value.get("units_per_sku"), value.get("max_worst_cost_total")
    if (not isinstance(skus, list) or not skus or len(set(skus)) != len(skus) or not all(s in ALLOWED for s in skus)
            or type(units) is not int or not 1 <= units <= MAX_UNITS_PER_SKU
            or not number(cap) or not 0 < cap <= MAX_WORST_COST):
        return None
    return {"skus": list(skus), "units_per_sku": units, "max_worst_cost_total": cap}


def arrived_costs(md, skus):
    """{sku: [unit costs]} from our signed offers (every unit we ever quoted), or None when an offer file does not parse."""
    out = {s: [] for s in skus}
    for path in (Path(md) / "signed-offers").glob("*.json"):
        try:
            offer = json.loads(path.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return None
        if isinstance(offer, dict) and offer.get("sku") in out and number(offer.get("cost")):
            out[offer["sku"]].append(offer["cost"])
    return out


def for_run(md, inventory, catalog, intelligence, now, path=None):
    """(picks, notes): one pick per unit, {"sku", "estimated_cost", "probe": True}; notes say why each SKU is or is not bought."""
    try:
        chosen = settings(path)
        if not chosen:
            return [], None
        return pick_units(md, inventory, catalog, intelligence, now, chosen)
    except Exception:
        return [], None


def pick_units(md, inventory, catalog, intelligence, now, chosen, skip_surged=False):
    """The probe's buying rules for `chosen` ({"skus", "units_per_sku", "max_worst_cost_total"}), shared with mid_band.py; with
    skip_surged a SKU on the surge list is not bought at all. Raises on unexpected data; callers turn that into off."""
    import median_bypass
    surge = {r.get("sku"): r.get("factor") for r in inventory.get("surge") or []
             if isinstance(r, dict) and number(r.get("factor")) and r["factor"] > 1}
    units = [u for u in inventory.get("units", []) if u.get("grade") != "expired" and u.get("sku") in chosen["skus"]]
    held = Counter(u["sku"] for u in units)
    def worst(sku):
        return (catalog.get(sku) or {}).get("list_price", 0) * WORST_COST_SHARE * surge.get(sku, 1)
    spent = sum(worst(u["sku"]) for u in units)
    costs = arrived_costs(md, chosen["skus"])
    sold = median_bypass.confirmed_counts(md, now, window=STALE_SECONDS)
    if costs is None or sold is None:
        return [], {s: "records_unreadable" for s in chosen["skus"]}
    picks, notes = [], {}
    for sku in chosen["skus"]:
        product = catalog.get(sku) or {}
        price = product.get("list_price")
        if product.get("kind") != "goods" or not number(price) or price <= 0:
            notes[sku] = "not_goods"
            continue
        if skip_surged and sku in surge:
            notes[sku] = "surged"
            continue
        median = (intelligence.get(sku) or {}).get("median_price")
        typical = price * TYPICAL_COST_SHARE * surge.get(sku, 1)
        if not number(median) or median <= typical:
            notes[sku] = "median_not_above_typical_cost"
            continue
        arrived = costs[sku] + [u["cost"] for u in units if u["sku"] == sku and number(u.get("cost"))]
        if any(c >= median for c in arrived):
            notes[sku] = "a_unit_arrived_at_or_above_the_median"
            continue
        old = [u for u in units if u["sku"] == sku and number(u.get("supplied_at")) and now - u["supplied_at"] >= STALE_SECONDS]
        if old and not (sold.get(sku) or (0, 0))[0]:
            notes[sku] = "held_24h_without_a_sale"
            continue
        notes[sku] = "held_full" if held[sku] >= chosen["units_per_sku"] else "ok"
        for _ in range(max(0, chosen["units_per_sku"] - held[sku])):
            if spent + worst(sku) > chosen["max_worst_cost_total"]:
                notes[sku] = "worst_cost_cap"
                break
            picks.append({"sku": sku, "estimated_cost": worst(sku), "probe": True})
            spent += worst(sku)
    return picks, notes
