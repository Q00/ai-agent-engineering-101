"""Leased, strictly bounded runtime controls. Safety policy is not editable here."""

import copy
import math
import os
from pathlib import Path
import time

from market_state import read

ROOT = Path(__file__).resolve().parent
BASE = {"quote_multipliers": {}, "restock_budget_rate": 0.05, "restock_paused": False}


def validate(controls):
    if not isinstance(controls, dict) or set(controls) != set(BASE):
        raise ValueError("Only quote multipliers, restock rate and pause are allowed")
    quotes = controls["quote_multipliers"]
    if not isinstance(quotes, dict) or len(quotes) > 20:
        raise ValueError("Invalid SKU controls")
    for sku, value in quotes.items():
        if (not isinstance(sku, str) or not 1 <= len(sku) <= 100 or type(value) not in (int, float)
                or not math.isfinite(value) or not .94 <= value <= 1.03):
            raise ValueError("SKU quote multiplier outside bounds")
    rate = controls["restock_budget_rate"]
    if type(rate) not in (int, float) or not math.isfinite(rate) or not .02 <= rate <= .05:
        raise ValueError("Restock rate outside bounds")
    if type(controls["restock_paused"]) is not bool:
        raise ValueError("Pause must be boolean")
    return copy.deepcopy(controls)


def effective(md=None, now=None):
    md = Path(md or os.environ.get("MARKET_DIR", ROOT/".market"))
    now = time.time() if now is None else now
    try:
        state = read(md/"strategy-policy.json", {})
        if (state.get("schema") != 1 or state.get("round") != 3
                or not state.get("published_at", now+1) <= now < state.get("valid_until", 0)):
            return {"controls": copy.deepcopy(BASE), "version": "base", "experiment": None}
        return {"controls": validate(state["controls"]), "version": state["version"],
                "experiment": state.get("experiment")}
    except (OSError, ValueError, TypeError, KeyError):
        return {"controls": copy.deepcopy(BASE), "version": "base", "experiment": None}
