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
    if "reinvestment" in cfg:
        rules = cfg["reinvestment"]
        if not isinstance(rules, dict) or type(rules.get("enabled")) is not bool:
            raise ValueError("Invalid inventory reinvestment policy")
        for field, low, high in (("cash_profit_share_percent", 1, 50),
                                 ("max_capital_percent", 100, 200)):
            if rules["enabled"] or field in rules:
                value = rules.get(field)
                if type(value) is not int or not low <= value <= high:
                    raise ValueError("Invalid inventory reinvestment policy: " + field)


def capital_basis(inventory, cfg):
    """Allocate a bounded share of cash profit, never unsold stock or injections.

    Unknown profit inputs disable only the optional increase. Initial-capital
    caps and the planner's independent cash reserve continue to apply.
    """
    initial = inventory.get("start")
    if not number(initial) or initial <= 0:
        raise ValueError("Unknown starting capital")
    basis = {"initial": initial, "effective": initial, "cash_profit": None,
             "retained_profit_allocated": 0, "status": "disabled"}
    rules = cfg.get("reinvestment", {})
    if not rules.get("enabled", False):
        return basis
    if "injected" not in inventory:
        return {**basis, "status": "unavailable_injected"}
    injected, balance = inventory["injected"], inventory.get("balance")
    if not number(injected) or injected < 0:
        return {**basis, "status": "invalid_injected"}
    if not number(balance):
        return {**basis, "status": "invalid_balance"}
    profit = balance - initial - injected
    if not number(profit):
        return {**basis, "status": "invalid_balance"}
    if profit <= 0:
        return {**basis, "cash_profit": profit, "status": "no_positive_cash_profit"}
    growth = min(initial * (rules["max_capital_percent"] - 100) / 100,
                 profit * rules["cash_profit_share_percent"] / 100)
    effective = max(initial, math.floor(initial + growth))
    return {**basis, "effective": effective, "cash_profit": profit,
            "retained_profit_allocated": effective - initial,
            "status": "cash_profit_reinvested" if effective > initial else "capital_limit_reached"}


class StockBudget:
    """Held actual cost + this plan's worst-case costs, with bounded growth.

    Existing stock above a cap is retained; only additional buys are refused.
    The separate cash floor remains authoritative and is not lowered here.
    """
    def __init__(self, inventory, cfg):
        validate(cfg)
        self.capital = capital_basis(inventory, cfg)
        capital = self.capital["effective"]
        self.limits = {label: math.floor((self.capital["initial"] if label == "food" else capital)
                                      * cfg[key] / 100) for key, label in FIELDS.items()}
        self.focus = cfg.get("focused_goods", {})
        if self.focus.get("enabled") is True:
            self.limits["secondary_sku"] = math.floor(capital * self.focus["max_secondary_sku_cost_percent"] / 100)
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
        sku_limit = (self.limits["secondary_sku"] if self.focus.get("enabled") is True
                     and sku in self.focus.get("secondary_skus", [])
                     and product.get("secondary_eligible") is True else self.limits["sku"])
        if self.skus[sku] + cost > sku_limit:
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
                "basis": ("starting capital plus bounded cash profit; food remains based on starting capital; "
                          "held actual cost plus planned worst-case supply cost"
                          if self.capital["effective"] > self.capital["initial"] else
                          "starting capital; held actual cost plus planned worst-case supply cost"),
                "capital_basis": dict(self.capital)}
