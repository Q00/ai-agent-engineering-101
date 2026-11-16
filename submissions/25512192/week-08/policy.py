"""The policy: every number the shop and the buyer act on, in one file the strategist rewrites.

state/policy.json holds the current policy; load() always returns a complete, clamped one, so a missing
file, a half-written answer or a strategist that asks for something silly never reaches a deal.
The code (seller, restock, buyer) reads the policy; only strategist.py writes it.
"""
import json
import time

from market_memory import STATE, read_json, write_json

POLICY = STATE / "policy.json"

DEFAULT = {
    "shop": {
        "open_ratio": 0.95,             # opening ask as a share of the list price
        "floor_margin": 0.10,           # never sell below cost x (1 + this) ...
        "clearance_hours": 12,          # ... unless a food unit has fewer hours left than this
        "clearance_floor": 0.6,         # then the floor is cost x this (thrown away it is worth 0)
        "warranty_hours": {"A": 6, "B": 3, "C": 0},
        "stock_categories": [],         # empty: restock picks from the ledger's best sellers
        "units_per_sku": 2,
        "max_stock_share": 0.4,         # stock at cost at most this share of the balance
        "food_min_shelf_hours": 48,     # do not stock food that lasts less than this
    },
    "buyer": {
        "offer_open_ratio": 0.55,       # first offer as a share of the list price
        "max_paid_ratio": 0.92,         # never pay more than this share of the list price (surplus > 0)
        "goods_grades": ["A", "B"],
        "require_warranty_goods": False,
        "food_hours_margin": 1,         # food must last use_within_hours + this
    },
    "notes": "default policy, before the first strategist run",
}

LIMITS = {
    ("shop", "open_ratio"): (0.6, 1.3),
    ("shop", "floor_margin"): (0.0, 1.0),
    ("shop", "clearance_hours"): (0, 72),
    ("shop", "clearance_floor"): (0.0, 1.0),
    ("shop", "units_per_sku"): (1, 5),
    ("shop", "max_stock_share"): (0.05, 0.8),
    ("shop", "food_min_shelf_hours"): (6, 720),
    ("buyer", "offer_open_ratio"): (0.3, 0.9),
    ("buyer", "max_paid_ratio"): (0.5, 1.0),
    ("buyer", "food_hours_margin"): (0, 2),      # checked only at payment and settle follows at once; 11-07..11-16 the
                                                   # strategist raised it to 16 h, and every step refused more deals
}
GRADES = ("A", "B", "C")


def clamp(policy):
    """A complete policy from whatever `policy` holds: unknown keys dropped, numbers clamped, types fixed."""
    out = json.loads(json.dumps(DEFAULT))
    for side in ("shop", "buyer"):
        got = (policy or {}).get(side) or {}
        for k, v in got.items():
            if k not in out[side]:
                continue
            lim = LIMITS.get((side, k))
            if lim and isinstance(v, (int, float)) and not isinstance(v, bool):
                out[side][k] = type(out[side][k])(min(max(v, lim[0]), lim[1]))
            elif k == "warranty_hours" and isinstance(v, dict):    # goods break within 6 h of the sale
                w = out[side][k]
                out[side][k] = {g: min(max(v[g], 0), 6) if isinstance(v.get(g), (int, float)) else w[g] for g in GRADES}
            elif k == "goods_grades" and isinstance(v, list):
                out[side][k] = [g for g in GRADES if g in v] or ["A"]
            elif k == "stock_categories" and isinstance(v, list):
                out[side][k] = [str(c) for c in v][:8]
            elif isinstance(out[side][k], bool) and isinstance(v, bool):
                out[side][k] = v
    if isinstance((policy or {}).get("notes"), str):
        out["notes"] = policy["notes"][:600]
    out["updated"] = (policy or {}).get("updated", 0)
    return out


def load():
    return clamp(read_json(POLICY, None))


def save(policy):
    policy = clamp(dict(policy, updated=time.time()))
    write_json(POLICY, policy)
    return policy


def age(policy):
    return time.time() - (policy.get("updated") or 0)
