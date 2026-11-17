"""Tunable policy (week 08, self-improvement loop): the numbers the 3-hourly loop may change, and their bounds.

improve/policy.json holds {"seller": {...}, "restock": {...}, "buyer": {...}}. Every reader clamps each value to
BOUNDS, so a bad file can never push a knob outside its safe range; unknown keys are ignored. The seller re-reads the
file (by mtime) at the start of each negotiation, restock and market_buyer at every run: a change takes effect without
a restart. improve/tune.py writes the file; improve/changes.jsonl keeps every change with its reason.
"""
from __future__ import annotations

import json
from pathlib import Path

POLICY_FILE = Path(__file__).resolve().parent / "improve" / "policy.json"

BOUNDS = {
    "seller": {
        "ask_of_list": (0.75, 0.99),        # opening ask, share of the list price
        "min_margin": (0.02, 0.20),         # lowest price = cost x (1 + min_margin + storage held)
        "max_rounds": (3, 8),
        "food_markdown_h": (0.5, 6.0),
        "food_markdown_share": (0.3, 1.0),
        "endgame_h": (6, 72),               # last hours of the round: stock is worth 0 at the end, sell it
        "endgame_floor": (0.3, 1.05),       # floor = cost x this in the endgame
        "value_floor": (0.0, 0.85),         # 11/05: floor >= this share of list price (endgame and food markdown excepted)
        "warranty_hours": (0, 6),           # 11/04 (2): warranty on A/B goods at or above warranty_min_list (0 = none)
        "warranty_min_list": (30_000, 5_000_000),
    },
    "restock": {
        "CASH_FLOOR": (0.15, 0.60),
        "PER_SKU_CAP": (0.01, 0.10),
        "OPENING_BUDGET": (0.05, 0.60),
        "TARGET_CATEGORIES": (10, 250),
        "HOT_LINES": (0, 8),
        "MAX_LIST_PRICE": (20_000, 3_000_000),   # do not stock SKUs above this list price
        "HOT_QTY": (1, 10),                      # units per hot market SKU
        "REORDER_MULT": (0.5, 4.0),              # stock kept per recent sale of a SKU
        "FOOD_MAX": (1, 10),
        "HIGH_TICKET": (50_000, 2_000_000),
        "STOCK_HOURS": (1.0, 12.0),
        "SURGE_MAX": (1.0, 1.5),
        "STOCK_FLOOR_SHARE": (0.0, 1.0),     # 11/06: the stock cap is at least this share of the cash base             # 11/06: skip SKUs whose market surge factor is above this
        "LAST_BUY_HOURS": (2, 48),           # 11/06: no new stock in the round's last N hours          # 11/05: stock at cost <= this many hours of our cost of goods sold
    },
    "buyer": {
        "rung_1": (0.30, 0.80),             # first rung (offered twice), share of value
        "rung_step": (0.03, 0.15),          # rise per rung after the first
        "max_share": (0.60, 0.97),          # never pay above this share of value (11/06: down to 0.60 for Saved)
        "est_price": (0.45, 0.90),          # expected price share used to pick the SKU
        "near_best": (0.50, 1.00),
        "shelf_strikes": (1, 5),            # food orders skip a shop after this many short-dated units (11/04)
    },
}
INTS = {"max_rounds", "TARGET_CATEGORIES", "HOT_LINES", "MAX_LIST_PRICE", "HOT_QTY", "FOOD_MAX", "endgame_h", "shelf_strikes", "HIGH_TICKET", "warranty_hours", "warranty_min_list", "LAST_BUY_HOURS"}

_cache: dict = {"mtime": None, "data": {}}


def clamp(section: str, key: str, value):
    lo, hi = BOUNDS[section][key]
    v = min(hi, max(lo, float(value)))
    return int(round(v)) if key in INTS else round(v, 4)


def load(section: str, path: Path | None = None) -> dict:
    """The clamped overrides for one section ({} when the file is missing or broken)."""
    path = POLICY_FILE if path is None else path       # looked up per call, so tests can point it elsewhere
    try:
        mtime = path.stat().st_mtime
        if _cache["mtime"] != (str(path), mtime):
            _cache.update(mtime=(str(path), mtime), data=json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}
    out = {}
    for k, v in (_cache["data"].get(section) or {}).items():
        if k in BOUNDS.get(section, {}) and isinstance(v, (int, float)):
            out[k] = clamp(section, k, v)
    return out
