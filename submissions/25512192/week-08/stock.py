"""Round 3 stock: which unit to sell, the lowest price for it, and the terms the receipt carries.

All code, no model: the harness negotiates between the opening ask and the floor computed here.
  - floor: cost x (1 + policy.floor_margin); a food unit about to expire may go to cost x clearance_floor,
    because thrown away it is worth nothing;
  - opening ask: list x policy.open_ratio, but not far above what the category settles at on the ledger;
  - claim: the grade the unit will still have at settle. Food grade is the share of shelf life left
    (A >= 0.8, B >= 0.45, the market's cuts); near a cut we claim the lower grade, so the claim stays true.
    Claims are checked on the ledger after the sale; an honest one costs nothing and buyers can see it;
  - warranty: policy.warranty_hours per goods grade.
"""
import json
import os
import time
from pathlib import Path

import market_memory as mm

MARKET_DIR = Path(os.environ.get("MARKET_DIR", Path(__file__).resolve().parent / ".market"))
FOOD_CUTS = (("A", 0.8), ("B", 0.45))
CLAIM_SLACK_H = 0.25            # claim the lower grade when within 15 minutes of a cut
SHIP_MIN_HOURS = 12             # ship the oldest food unit that still has this many hours, if any
OPEN_OVER_MARKET = 0.10         # open at most this much above the category's median paid / list


def inventory():
    return mm.read_json(MARKET_DIR / "inventory.json", {})


def round3(inv=None):
    return (inv or inventory()).get("round", 0) >= mm.ROUND


def hours_left(u, now=None):
    return (u["expires_at"] - (now or time.time())) / 3600 if u.get("expires_at") else None


def food_grade(u, shelf_hours, now=None, slack=0.0):
    h = hours_left(u, now)
    if h is None or not shelf_hours:
        return u.get("grade")
    share = (h - slack) / shelf_hours
    return next((g for g, cut in FOOD_CUTS if share >= cut), "C")


def find_unit(text, taken=(), inv=None):
    """The unit to sell for what the buyer asked: a SKU named in the text first, then a name or category match.
    Goods ship oldest first; food ships the oldest unit that still lasts SHIP_MIN_HOURS, else the oldest."""
    inv = inv or inventory()
    want = (text or "").lower()
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired" and u["unit"] not in taken]
    named = [u for u in units if u["sku"].lower() in want]
    fit = named or [u for u in units if (u.get("name") or "").lower() in want
                    or (u.get("category") or "").split(">")[-1].lower() in want]
    if not fit:
        return None
    fresh = [u for u in fit if hours_left(u) is None or hours_left(u) >= SHIP_MIN_HOURS]
    return min(fresh or fit, key=lambda u: (u.get("supplied_at") or 0))


def terms(u, policy, stats, catalog):
    """{"floor", "ask", "claim", "warranty_hours"} for one unit."""
    sp = policy["shop"]
    info = catalog.get(u["sku"]) or {}
    lp, cost = u.get("list_price") or info.get("list_price") or 0, u.get("cost") or 0
    floor = cost * (1 + sp["floor_margin"])
    h = hours_left(u)
    if u.get("kind") == "food" and h is not None and h < sp["clearance_hours"]:
        floor = cost * sp["clearance_floor"]
    ask = lp * sp["open_ratio"]
    r = mm.median_ratio(((stats.get("categories") or {}).get(u.get("category")) or {}).get("ratios") or [])
    if r:
        ask = min(ask, lp * (r + OPEN_OVER_MARKET))
    ask = max(ask, floor * 1.05)
    if u.get("kind") == "food":
        claim = food_grade(u, info.get("shelf_hours"), slack=CLAIM_SLACK_H)
        warranty = 0
    else:
        claim = u.get("grade")
        warranty = sp["warranty_hours"].get(claim, 0)
    return {"floor": int(round(floor)), "ask": int(round(ask)), "claim": claim if claim in ("A", "B", "C") else None,
            "warranty_hours": warranty, "list_price": lp, "cost": cost, "hours_left": h}


def describe(u, t):
    """One line for the buyer: what the unit is, with the facts a careful buyer checks before paying."""
    bits = [f"{u.get('name')} ({u['sku']}), grade {t['claim']}" if t["claim"] else f"{u.get('name')} ({u['sku']})"]
    if t["hours_left"] is not None:
        bits.append(f"about {int(t['hours_left'])} h of shelf life left")
    if t["warranty_hours"]:
        bits.append(f"{t['warranty_hours']:g} h warranty")
    bits.append("market certificate included")
    return ", ".join(bits)


def load_catalog():
    return mm.read_json(MARKET_DIR / "catalog.json", {})


if __name__ == "__main__":
    import policy
    inv = inventory()
    for u in inv.get("units", [])[:10]:
        print(json.dumps(terms(u, policy.load(), mm.read_json(mm.STATS, mm.empty()), load_catalog()), ensure_ascii=False))
