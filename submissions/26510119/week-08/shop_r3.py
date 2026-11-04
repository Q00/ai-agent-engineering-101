"""Round 3 shop logic: find a unit in stock, a floor price the code enforces, and the shop's own sales history.

The kit keeps $MARKET_DIR/inventory.json fresh (units with cost, grade, expiry). Everything here is code;
the harness only picks prices inside the floor these functions set.
"""
import json
import os
import re
import time

import policy
from datetime import datetime, timezone
from pathlib import Path

MARKET_DIR = Path(os.environ.get("MARKET_DIR", Path(__file__).with_name(".market")))
SALES = Path(os.environ.get("SHOP_SALES_R3") or Path(__file__).with_name("sales_r3.jsonl"))
WINDOW = 80                                  # recent negotiations per category
SKU = re.compile(r"\bG[0-9a-f]{8}\b")


def _load(name):
    try:
        return json.loads((MARKET_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def inventory():
    return _load("inventory.json") or {}


_CATALOG = {}


def shelf_hours(sku):
    """A food SKU's full shelf life, from the catalog the kit writes once."""
    global _CATALOG
    if not _CATALOG:
        c = _load("catalog.json") or {}
        _CATALOG = c if isinstance(c, dict) else {x["sku"]: x for x in c}
    return (_CATALOG.get(sku) or {}).get("shelf_hours")


def round3():
    return inventory().get("round", 0) >= 3


def hours_left(unit, now=None):
    if not unit.get("expires_at"):
        return None
    return (unit["expires_at"] - (now or time.time())) / 3600


def find_unit(text, taken=()):
    """The unit the buyer asked for: a named SKU first, else a category or name match. Earliest expiry first."""
    want = text.lower()
    units = [u for u in inventory().get("units", []) if u.get("grade") != "expired" and u["unit"] not in taken]
    skus = {s.lower() for s in SKU.findall(text)}
    fit = [u for u in units if u["sku"].lower() in skus]
    if not fit:
        fit = [u for u in units if (u.get("category") or "").split(">")[-1].lower() in want
               or (u.get("name") or "").lower() in want]
    return min(fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0), default=None)


def floor_price(unit, shelf_hours=None):
    """Lowest price the code allows. Fresh stock: cost plus 5%. Food close to its date would be thrown away
    at cost, so any sale beats none: the floor falls with the shelf life left."""
    p = policy.get("shop")
    cost = unit["cost"]
    full = int(cost * (1 + p["margin"])) + 1
    h = hours_left(unit)
    if h is None:
        return full
    share = h / shelf_hours if shelf_hours else 1
    if h < p["salvage_hours"] or share < p["salvage_share"]:
        return max(1, int(cost * p["salvage_floor"]))
    if share < 0.45:                          # grade C food
        return int(cost * p["grade_c_floor"])
    return full


def target_price(unit, floor, past, shelf_hours=None, p=None):
    """The price at which the shop takes an offer at once, without asking the model (part C).
    list price x a share: the policy's share until this category has enough of our own deals, then the share
    our deals actually sold at. Food with little shelf life left aims lower, since waiting risks the whole cost."""
    p = p or policy.get("shop")
    share, source = p["target_share"], "policy"
    if past and past["deals"] >= p["target_min_deals"] and past["price"]:
        share, source = past["price"] * p.get("target_scale", 1.0), f"our last {past['deals']} deals x {p.get('target_scale', 1.0)}"
    h = hours_left(unit)
    if h is not None and shelf_hours and h / shelf_hours < 0.45:
        share *= p["short_life_target_factor"]
        source += ", short shelf life"
    return max(floor, int(unit["list_price"] * share)), f"{share:.0%} of list ({source})"


def record(row):
    row = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **row}
    with open(SALES, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rows():
    if not SALES.exists():
        return []
    out = []
    for line in SALES.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def stats(category):
    """Prices as a share of list price, so SKUs of different prices in one category can be compared."""
    rows = _rows()
    opens = [r for r in rows if r.get("kind") == "open" and r.get("category") == category][-WINDOW:]
    if not opens:
        return None
    tasks = {r["task"]: r for r in opens}
    ends = [r for r in rows if r.get("kind") == "end" and r.get("task") in tasks]
    deals = [e["price"] / tasks[e["task"]]["list_price"] for e in ends if e.get("result") == "deal"]
    firsts = [e["offers"][0] / tasks[e["task"]]["list_price"] for e in ends if e.get("offers")]
    asks = [r["ask"] / r["list_price"] for r in opens]
    return {"n": len(opens), "deals": len(deals), "rate": len(deals) / len(opens),
            "ask": sum(asks) / len(asks), "price": sum(deals) / len(deals) if deals else None,
            "first_offer": sum(firsts) / len(firsts) if firsts else None}


def describe(category, s):
    if s is None:
        return "You have no sales history in this category yet."
    out = (f"Your last {s['n']} negotiations in this category: {s['deals']} deals ({s['rate']:.0%}); "
           f"opening asks averaged {s['ask']:.0%} of list price")
    if s["price"] is not None:
        out += f"; sale prices averaged {s['price']:.0%} of list price"
    if s["first_offer"] is not None:
        out += f"; buyers' first offers averaged {s['first_offer']:.0%} of list price"
    return out + "."


def quality_line(unit, shelf_hours=None):
    """What the shop tells the buyer about the unit. True facts only: the claim is checked on the ledger."""
    g = unit.get("grade")
    if unit.get("kind") == "food":
        h = hours_left(unit)
        return f"grade {g}, {max(0, int(h or 0))} h shelf life left, market certificate included"
    w = policy.get("shop")["warranty_hours"].get(g, 0)
    cond = {"A": "new", "B": "damaged box", "C": "refurbished"}.get(g, g)
    return f"grade {g} ({cond}), market certificate included" + (f", {w} h warranty" if w else ", no warranty")


def receipt_extras(unit):
    extra = {"unit": unit["unit"], "claim": unit.get("grade")}
    w = policy.get("shop")["warranty_hours"].get(unit.get("grade"), 0) if unit.get("kind") == "goods" else 0
    if w:
        extra["warranty_hours"] = w
    return extra
