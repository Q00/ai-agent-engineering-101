"""Logistics: stock at cost, the inventory file, perishable ('부패주의') bought / sold / wasted.
Disposal candidates and the logistics report are in dept_reports.py; the clearance and aging floors (when
stock is marked down) are the sales department's (depts/sales.py) with the logistics policy section's values."""
import json
import os
import time

from market_db import ME


def held_cost(inv):
    return sum(u.get("cost", 0) for u in inv.get("units", []) if u.get("grade") != "expired")


def load_inventory(state_dir=None):
    path = os.path.join(state_dir or os.environ.get("MARKET_DIR", ".market"), "inventory.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def perishable(c):
    """Food whose category name says '부패주의' (fresh fruit and vegetables)."""
    return c.get("kind") == "food" and "부패주의" in (c.get("category") or "")


def perishable_numbers(db, t0, t1, catalog, inv=None):
    """'부패주의' food in [t0, t1): units bought (supplied), sold by my shop, and wasted (expired unsold: shelf life
    ran out in the period, no settle for the unit, not in the inventory now) with their cost."""
    skus = {k for k, c in catalog.items() if perishable(c)}
    held = {u["unit"] for u in (inv or {}).get("units", [])}
    out = {"bought": 0, "sold": 0, "wasted": 0, "waste_cost": 0}
    for sku, n in db.execute("SELECT sku, COUNT(*) FROM my_stock WHERE supplied_at>=? AND supplied_at<? GROUP BY sku",
                             (t0, t1)):
        out["bought"] += n if sku in skus else 0
    for sku, n in db.execute("SELECT sku, COUNT(*) FROM settles WHERE shop=? AND ts>=? AND ts<? GROUP BY sku",
                             (ME, t0, t1)):
        out["sold"] += n if sku in skus else 0
    now = time.time()
    for unit, sku, cost, supplied in db.execute(
            "SELECT m.unit, m.sku, m.cost, m.supplied_at FROM my_stock m WHERE NOT EXISTS "
            "(SELECT 1 FROM settles s WHERE s.unit=m.unit)"):
        if sku not in skus or unit in held or not supplied:
            continue
        exp = supplied + (catalog[sku].get("shelf_hours") or 0) * 3600
        if t0 <= exp < t1 and exp <= now:
            out["wasted"] += 1
            out["waste_cost"] += cost or 0
    return out


def perishable_line(p):
    return (f"perishable ('부패주의') food: bought {p['bought']}, sold {p['sold']}, wasted {p['wasted']} "
            f"({p['waste_cost']:,} at cost)")
