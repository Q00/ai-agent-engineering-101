"""Round 3: the shop's stock, read from the .market/inventory.json the kit keeps (every 10 s and after each supply).

The shop runs on Render with --deployed: the kit in the same service refreshes the file but serves no checkout, so
the seller (which unit to promise) and acp_shop (which unit to ship) share one StockBook in this process.
The file can be 10 s old, so the book also remembers what it has promised and sold since.
"""
import json
import math
import os
import time
from pathlib import Path

ECON_ROUND = 3
HOLD_SECONDS = 900     # a buyer who walks away without cancelling frees the unit after this long


def market_dir():
    return Path(os.environ.get("MARKET_DIR", ".market"))


def load_inventory():
    try:
        inv = json.loads((market_dir() / "inventory.json").read_text(encoding="utf-8"))
        return inv if isinstance(inv, dict) else {}
    except (OSError, ValueError):
        return {}


def last_segment(category):
    return (category or "").split(">")[-1].lower()


def expired(u, now=None):
    return u.get("grade") == "expired" or bool(u.get("expires_at") and u["expires_at"] <= (now or time.time()))


MARKDOWN = {"clear_hours": 24, "min_floor_share": 0.1, "stale_days": 3, "stale_per_day": 0.1, "end_hours": 48,
            "below_cost": ["food_expiry", "stale_goods", "round_end"]}


def markdown(u, cfg=None, ends_at=None, now=None):
    """(lowest price for this unit, share of cost it keeps, why). Selling below cost beats what waiting brings: food past
    its date is lost at cost, and stock left when the round ends counts as a loss. The numbers are starting values in
    pricing.json "markdown"; Round 3's own data has to judge them. "below_cost" lists the reasons allowed to go under
    cost (food_expiry, stale_goods, round_end): the shop starts with food near its date only."""
    cfg, now = {**MARKDOWN, **(cfg or {})}, now or time.time()
    allowed = set(cfg["below_cost"])
    cost, factors = math.ceil(u["cost"]), [(1.0, "")]
    supplied = u.get("supplied_at") or now
    if u.get("expires_at") and "food_expiry" in allowed:
        left, life = (u["expires_at"] - now) / 3600, (u["expires_at"] - supplied) / 3600
        clear = min(cfg["clear_hours"], max(life, 1) * 0.5)
        if left < clear:
            factors.append((max(0.0, left / clear), f"{left:.1f} h of shelf life left"))
    elif not u.get("expires_at") and "stale_goods" in allowed:
        over = (now - supplied) / 86400 - cfg["stale_days"]
        if over > 0:
            factors.append((max(0.0, 1 - over * cfg["stale_per_day"]), f"{over + cfg['stale_days']:.1f} days in stock"))
    if ends_at and "round_end" in allowed:
        left = (ends_at - now) / 3600
        if left < cfg["end_hours"]:
            factors.append((max(0.0, left / cfg["end_hours"]), f"{left:.1f} h to the end of the round"))
    keep, why = min(factors, key=lambda f: f[0])
    least = min(cost, math.ceil((u.get("list_price") or 0) * cfg["min_floor_share"]))
    return max(least, round(cost * keep)), keep, why


class StockBook:
    def __init__(self, load=load_inventory):
        self.load = load
        self.held = {}      # unit -> time promised
        self.sold = set()

    def economy(self):
        """True from Round 3 on. Before that the shop keeps selling from catalog.json."""
        return self.load().get("round", 0) >= ECON_ROUND

    def grade(self, unit):
        """The unit's grade now (a food unit's grade drops while it waits), or None if it is gone."""
        return next((u.get("grade") for u in self.load().get("units", []) if u["unit"] == unit), None)

    def units(self):
        """Units free to promise: in stock, not expired, not promised, not sold since the file was written."""
        now = time.time()
        self.held = {u: t for u, t in self.held.items() if now - t < HOLD_SECONDS}
        return [u for u in self.load().get("units", [])
                if not expired(u, now) and u["unit"] not in self.held and u["unit"] not in self.sold]

    def find(self, text):
        """The unit for what the buyer asked (SKU first, then name, then category) that expires first."""
        want = text.lower()
        units = self.units()
        for matches in (lambda u: u["sku"].lower() in want,
                        lambda u: (u.get("name") or "\0").lower() in want,
                        lambda u: (last_segment(u.get("category")) or "\0") in want):
            fit = [u for u in units if matches(u)]
            if fit:
                return min(fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0))
        return None

    def hold(self, unit):
        self.held[unit] = time.time()

    def release(self, unit):
        self.held.pop(unit, None)

    def mark_sold(self, unit):
        self.held.pop(unit, None)
        self.sold.add(unit)

    def ship(self, receipt, acceptable=None):
        """(unit dict, None) to ship for a receipt, or (None, reason). Same rule as the kit's AcpShop.pick_unit:
        the seller's unit if it still fits, else the first-expiring unit of a fitting SKU."""
        want = receipt.get("item")
        fits = lambda u: (u["sku"] in acceptable if acceptable else
                          u["sku"] == want or last_segment(u.get("category")) == str(want).lower())
        promised = receipt.get("unit")
        if promised:
            now = time.time()
            u = next((u for u in self.load().get("units", []) if u["unit"] == promised), None)
            if u and not expired(u, now) and promised not in self.sold and fits(u):
                return u, None
            return None, f"unit {promised} is not in stock for this order"
        fit = [u for u in self.units() if fits(u)]
        if not fit:
            return None, f"no {want} in stock"
        return min(fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0)), None
