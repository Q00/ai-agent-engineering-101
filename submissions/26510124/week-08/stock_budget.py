"""Cost exposure limits for inventory buys; local arithmetic, no model calls."""
from collections import Counter
import math

from feedback import number


FIELDS = {
    "max_run_cost_percent": "run",
    "max_sku_cost_percent": "sku",
    "max_category_cost_percent": "category",
    "max_food_cost_percent": "food",
}


def validate(cfg):
    for field in FIELDS:
        if type(cfg.get(field)) is not int or not 1 <= cfg[field] <= 60:
            raise ValueError("Invalid inventory exposure policy: " + field)
    if cfg["max_sku_cost_percent"] > cfg["max_category_cost_percent"]:
        raise ValueError("SKU exposure limit cannot exceed category exposure limit")


class StockBudget:
    """Held actual cost + this plan's worst-case costs, based on start capital.

    Existing stock above a cap is retained; only additional buys are refused.
    The separate cash floor remains authoritative and is not lowered here.
    """
    def __init__(self, inventory, cfg):
        validate(cfg)
        capital = inventory.get("start")
        if not number(capital) or capital <= 0:
            raise ValueError("Unknown starting capital")
        self.limits = {label: math.floor(capital * cfg[key] / 100) for key, label in FIELDS.items()}
        self.skus, self.categories = Counter(), Counter()
        self.food, self.spent = 0, 0
        seen = set()
        for unit in inventory["units"]:
            if (not isinstance(unit, dict) or not isinstance(unit.get("unit"), str)
                    or not unit["unit"] or unit["unit"] in seen
                    or not all(isinstance(unit.get(k), str) and unit[k] for k in ("sku", "category"))
                    or unit.get("kind") not in ("food", "goods")
                    or type(unit.get("cost")) is not int or unit["cost"] <= 0):
                raise ValueError("Unknown/duplicate stock cannot bypass exposure limits")
            seen.add(unit["unit"])
            self.skus[unit["sku"]] += unit["cost"]
            self.categories[unit["category"]] += unit["cost"]
            if unit["kind"] == "food":
                self.food += unit["cost"]

    def reason(self, sku, product):
        cost = product["worst_cost"]
        if type(cost) is not int or cost <= 0:
            raise ValueError("Missing conservative unit cost")
        if self.spent + cost > self.limits["run"]:
            return "run_cost_cap"
        if self.skus[sku] + cost > self.limits["sku"]:
            return "sku_cost_cap"
        if self.categories[product["category"]] + cost > self.limits["category"]:
            return "category_cost_cap"
        if product["kind"] == "food" and self.food + cost > self.limits["food"]:
            return "food_cost_cap"
        return None

    def add(self, sku, product):
        reason = self.reason(sku, product)
        if reason:
            raise ValueError("Inventory plan exceeded " + reason)
        cost = product["worst_cost"]
        self.spent += cost
        self.skus[sku] += cost
        self.categories[product["category"]] += cost
        if product["kind"] == "food":
            self.food += cost

    def summary(self):
        return {"limits": self.limits, "planned_cost": self.spent,
                "food_cost_after_plan": self.food,
                "basis": "starting capital; held actual cost plus planned worst-case supply cost"}
