"""The least discount off list a purchase must have, for the Saved score. There is no off switch: a missing or broken setting means the default floor.

Saved adds each deal's share saved off list; a useless item counts -1 and a refunded one 0. So a deal at discount d is worth, on average:
food d, goods under a warranty the kit enforces (1-p)d, other goods (1-p)d - p, where p is the fault rate of that grade.
Solving for the floor gives the discount each case needs. A grade nobody stated counts as the worst one the kit would accept.
"""

from fractions import Fraction
import json
import math
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "buyer_support.json"
DEFAULT_FLOOR = .25
# logs/rank-analysis/fault-rates-20261106.json: matched public ledger rates scaled by the board's complete count of breaks.
DEFAULT_RATES = {"no_warranty": {"A": .027, "B": .098, "C": .331}, "warranty": {"A": .025, "B": .111, "C": .271}}


def _share(value, top=1):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value < top


def _rates_usable(rates):
    return isinstance(rates, dict) and all(isinstance(rates.get(kind), dict) and all(_share(rates[kind].get(grade), .5) for grade in "ABC") for kind in DEFAULT_RATES)


def load(path=CONFIG):
    """{"floor", "rates"}. Each falls back to its default on its own, so a bad value here never lifts the floor."""
    try:
        section = json.loads(Path(path).read_text(encoding="utf-8")).get("discount_floor")
    except (OSError, ValueError, AttributeError):
        section = None
    section = section if isinstance(section, dict) else {}
    floor = section.get("floor")
    rates = section.get("fault_rates")
    return {"floor": floor if _share(floor) and floor > 0 else DEFAULT_FLOOR, "rates": rates if _rates_usable(rates) else DEFAULT_RATES}


def fault_rate(grade, covered, settings, allowed=None):
    table = settings["rates"]["warranty" if covered else "no_warranty"]
    if grade in table:
        return table[grade]
    return max(table[g] for g in (allowed or table) if g in table)


def food_penalty(grade, settings, allowed=None):
    """The grade-C food penalty (food_c_trial.py) when this quote is C, or states no grade while C is allowed; else None."""
    p = settings.get("food_c_penalty")
    if not _share(p, .5) or p <= 0:
        return None
    if grade == "C" or (grade not in ("A", "B", "C") and "C" in (allowed or ())):
        return p
    return None


def required(kind, grade, covered, settings, allowed=None):
    """The discount needed, exact. `kind` other than food counts as goods, which needs more."""
    floor = Fraction(str(settings["floor"]))
    if kind == "food":
        p = food_penalty(grade, settings, allowed)
        return floor if p is None else (floor+Fraction(str(p)))/(1-Fraction(str(p)))
    p = Fraction(str(fault_rate(grade, covered, settings, allowed)))
    return floor/(1-p) if covered else (floor+p)/(1-p)


def least_required(kind, covered, settings, allowed=None):
    """What the best grade the kit accepts would need, for use before any quote has stated a grade."""
    grades = [g for g in (allowed or "ABC") if g in settings["rates"]["no_warranty"]] or list("ABC")
    return min(required(kind, grade, covered, settings, allowed) for grade in grades)


def ceiling(list_price, discount):
    """The highest whole price that still gives this discount; 0 when none does."""
    return max(0, math.floor(Fraction(str(list_price))*(1-discount)))


def expected_discount(discount, kind, grade, covered, settings, allowed=None):
    """What a deal at this share off list adds to Saved on average."""
    if kind == "food":
        p = food_penalty(grade, settings, allowed)
        return discount if p is None else (1-p)*discount-p
    p = fault_rate(grade, covered, settings, allowed)
    return (1-p)*discount if covered else (1-p)*discount-p


def deal_limit(limit, grade, budget):
    """The limit for this turn: never above the current budget, whatever a quote says. Returns (budget, check), and check is safe to log."""
    settings = limit["settings"]
    need = required(limit.get("kind"), grade, bool(limit.get("covered")), settings, limit.get("grades"))
    top = ceiling(limit["list_price"], need)
    # required_down is the exact need rounded down to 6 places, for the counters' surplus check: "required" is rounded to the nearest
    # and can sit just above the exact need, which refused counters at the ceiling itself. The ceiling, not this figure, bounds the price.
    return min(budget, top), {"floor": settings["floor"], "required": round(float(need), 6), "required_down": math.floor(need * 10 ** 6) / 10 ** 6,
                              "grade": grade, "covered": bool(limit.get("covered")), "ceiling": top, "budget_before": budget,
                              "cap_without_floor": limit.get("cap_without_floor")}
