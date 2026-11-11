"""Mid-price demand band ("U3"), approved by the user on 11-10 (directly, about 19:5x KST; relayed first by the Orchestrator at about
19:50): twelve top-demand 200k-1M goods SKUs that the ordinary pass never buys (their public medians, about .68 of list, sit under
its .75 worst-cost test) get one unit each in restock. Off unless mid_band.json says so.

The buying rules are the mid-price probe's (mid_probe.pick_units): bought only while the sampled public median is above our typical
goods cost (.60 x list), no more of a SKU while any held unit of it is 6 h old unsold (the user's 11-11 sales-outcome stop, which
replaced "never again once a unit arrived at or above the median"), and no more of a SKU with a unit held 24 h and none of our
completed sales of it in the last 24 h; a SKU on the surge list is not bought at all. units_per_sku (1 to 3) each held
plus planned, inside the total cap and the run's units, at most max_lines_per_run (4 at most) of the ordinary lines a run (more units
of a SKU already in the run add to its line, not a new one), paid from the run budget at worst cost (.75 x list) with seven days of
storage, held plus planned worst cost within max_worst_cost_total (17M at most; 1 unit and 6M until 11-11). Its pass
comes after the 1M+ probe and before the ordinary pass, so room that frees up goes to it first; nothing is sold to make room. The
seller's goods floor is cost plus storage plus 5%. Read on every restock run; anything missing, unreadable or malformed means off.
"""

import json
from pathlib import Path

from mid_probe import number, pick_units

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "mid_band.json"
ALLOWED = ("G05277ffe", "G1515d951", "G07ec17d5", "G8095ddd6", "G85a1a856", "G754bf0ca",
           "G2607d1f6", "G57d8dfa6", "G53b150b5", "G429bd69e", "G2c023a8c", "G9c9f8f94")
# Up to 3 units a SKU and 17M worst cost, approved by the user on 11-11 (directly, about 09:2x KST: "Approve 3 / 17M"; relayed by the
# Orchestrator from the user's 11-10 21:15 answer "품목당 3개, 한도 17M (권장)"). The shipped config decides the values in force.
MAX_WORST_COST = 17000000
MAX_UNITS = 3
MAX_LINES = 4
# Offers are read from the band's switch-on (11-10 19:54:50 KST, d245d129); since 11-11 their arrival costs no longer stop a SKU.
ARRIVAL_SINCE = 1794308090.0


def settings(path=None):
    try:
        value = json.loads(Path(CONFIG if path is None else path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return None
    skus, units, cap, lines = value.get("skus"), value.get("units_per_sku"), value.get("max_worst_cost_total"), value.get("max_lines_per_run")
    if (not isinstance(skus, list) or not skus or len(set(skus)) != len(skus) or not all(s in ALLOWED for s in skus)
            or type(units) is not int or not 1 <= units <= MAX_UNITS or not number(cap) or not 0 < cap <= MAX_WORST_COST
            or type(lines) is not int or not 1 <= lines <= MAX_LINES):
        return None
    return {"skus": list(skus), "units_per_sku": units, "max_worst_cost_total": cap, "max_lines_per_run": lines}


def for_run(md, inventory, catalog, intelligence, now, path=None):
    """{"picks": [one pick per unit], "max_lines": n, "notes": {sku: why}} for one restock run, or None when off or on any error."""
    try:
        chosen = settings(path)
        if not chosen:
            return None
        picks, notes = pick_units(md, inventory, catalog, intelligence, now, chosen, skip_surged=True, since=ARRIVAL_SINCE)
        return {"picks": picks, "max_lines": chosen["max_lines_per_run"], "notes": notes}
    except Exception:
        return None
