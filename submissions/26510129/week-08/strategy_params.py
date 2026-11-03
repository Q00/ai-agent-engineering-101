"""The few strategy knobs the nightly tuner may turn, with their ranges.

    load() -> {name: value}     defaults, overridden by .market/strategy_params.json when a value is inside its range
    validate(changes) -> (accepted, rejected)

Everything else is fixed in code: the mandate guardian (budget), receipt checks, and signing are deliberately
not on this list, so a model proposal can never move the AP2 boundary. The buyer and the seller read the file
on every order, so a change written at midnight is live without a restart.
"""
import json
import os
from pathlib import Path

PARAMS = {   # name: (default, low, high)
    "explore_rate": (0.15, 0.05, 0.30),           # buyer: share of orders that try the least-tried safe shop
    "first_offer_discount": (0.15, 0.05, 0.20),   # buyer: first offer this far below the ask (told to the harness)
    "seller_margin_volume": (1.15, 1.05, 1.25),   # seller: target = cost x this on the volume arm
    "seller_margin_margin": (1.30, 1.15, 1.40),   # seller: target = cost x this on the margin arm
    "grade_cap_B": (0.85, 0.70, 0.95),            # seller: grade B never asks more than list price x this
}
DEFAULTS = {name: spec[0] for name, spec in PARAMS.items()}


def path():
    return Path(os.environ.get("MARKET_DIR", ".market")) / "strategy_params.json"


def validate(changes):
    """Keep only known names with a number inside the range; everything else is reported, not applied."""
    accepted, rejected = {}, {}
    for name, value in (changes or {}).items():
        spec = PARAMS.get(name)
        if spec is None:
            rejected[name] = "unknown parameter"
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            rejected[name] = "not a number"
            continue
        _, low, high = spec
        if not low <= value <= high:
            rejected[name] = f"outside {low}-{high}"
            continue
        accepted[name] = round(float(value), 3)
    return accepted, rejected


def load():
    try:
        stored = json.loads(path().read_text(encoding="utf-8")).get("params") or {}
    except (OSError, ValueError, AttributeError):
        stored = {}
    accepted, _ = validate(stored)
    return {**DEFAULTS, **accepted}
