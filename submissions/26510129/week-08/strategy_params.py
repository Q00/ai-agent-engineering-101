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
    "first_offer_discount": (0.20, 0.05, 0.25),   # buyer: first offer this far below the ask (told to the harness)
    "seller_margin_volume": (1.15, 1.05, 1.25),   # seller: target = cost x this on the volume arm (no market price known)
    "seller_margin_margin": (1.30, 1.15, 1.40),   # seller: target = cost x this on the margin arm (no market price known)
    "grade_cap_B": (0.85, 0.70, 0.95),            # seller: grade B never asks more than list price x this
    # 11-04 17:40: a sofa drew three buyers in two minutes and still went on the volume arm at 0.97 x market. How far
    # the ask can rise is a close-rate question, so the arm split and the markup on the market price are the tuner's.
    "seller_volume_share": (0.60, 0.30, 0.80),    # seller: share of goods opens priced on the volume arm (food: +0.15)
    "seller_markup": (1.00, 0.95, 1.15),          # seller: multiplies the market-price target on both arms
    # 11-17: the buyer's price limits are no longer shares of the list price (0.70 and 0.64, written for the patch 3.4
    # score) but distances from its own running score under whatever rule is in force (objective.py, profiles.py):
    "buyer_kept_slack": (0.02, 0.00, 0.15),       # buyer: pays when a deal's estimated score >= my average - this
    "buyer_quick_gain": (0.12, 0.05, 0.25),       # buyer: takes an ask at once when its score >= my average + this
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
