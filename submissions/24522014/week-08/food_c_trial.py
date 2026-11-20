"""Buyer option 3, approved 11-09: grade C food, behind r3_policy.json buying.c_trial_enabled.

Food orders in the trial arm (its own order hash, independent of the other tests) also accept grade C, which the kit gives a food
unit with little of its shelf life left. The order's shelf-life rule (use period + delivery + safety hours) and the certificate are
unchanged, and goods orders never take C. A C quote, or a quote with no grade once C is allowed, must meet the floor with a food-C
penalty p: (floor + p) / (1 - p). p = max(3 x the food-C broken rate in the ledger samples, .05); 0 of 12,131 food-C settles broke,
so p = .05. Read on every run; anything missing, unreadable or malformed means off.
"""

import json
import math
from pathlib import Path

import first_message_ab as ab

CONFIG = Path(__file__).resolve().parent / "r3_policy.json"
OFF = {"enabled": False, "share": None, "salt": None, "penalty": None}


def load(path=CONFIG):
    """{"enabled", "share", "salt", "penalty"}; off unless c_trial_enabled is true and every trial field is valid."""
    try:
        buying = json.loads(Path(path).read_text(encoding="utf-8")).get("buying")
    except (OSError, ValueError, AttributeError):
        return dict(OFF)
    if not isinstance(buying, dict):
        return dict(OFF)
    section = {"enabled": buying.get("c_trial_enabled"), "share": buying.get("c_trial_share"), "salt": buying.get("c_trial_salt")}
    penalty = buying.get("c_trial_food_penalty")
    if not ab._valid(section) or type(penalty) is not float or not math.isfinite(penalty) or not .05 <= penalty < .5:
        return dict(OFF)
    return {**section, "penalty": penalty}


def arm(order_id, settings=None):
    """"grade_c", "control", or None when the trial is off."""
    chosen = settings if settings is not None else load()
    if not chosen.get("enabled"):
        return None
    return {"conditions": "grade_c", "control": "control"}.get(ab.arm(order_id, chosen))
