"""The CEO's policy: policy.json, one section per department. Departments read it; only ceo.py writes it.

    {"version": 7, "ts": ..., "by": "ceo", "reason": "...",
     "purchasing": {...}, "sales": {...}, "logistics": {...}, "cs": {...}, "buyer": {...}}

Reading never fails: the file is read again when it changes; a key that is missing, of the wrong type or out of
limits.POLICY_RANGES falls back to DEFAULTS or is clipped into the range (logged once per value in decisions.jsonl);
a file that does not parse keeps the last good one. Hard limits (limits.py) have no keys here.
write() replaces the file atomically (temp file + os.replace) with the next version and keeps a copy of every
version in logs/policy_history.jsonl.
"""
import json
import os
import time

from limits import POLICY_RANGES

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "policy.json")
HISTORY = os.path.join(HERE, "logs", "policy_history.jsonl")

# the values the code used before policy.json (11/06); the file starts as a copy of these
DEFAULTS = {
    "purchasing": {"spend_share": 0.4, "min_margin": 0.10, "my_share": 0.25, "wanted_qty": 3, "min_sold_30m": 3,
                   "food_min_shelf_h": 24, "max_break": 0.15, "perishable_trial_market_30m": 3,
                   "perishable_trial_qty": 2,
                   "sofa_trial_start": 0.0, "sofa_trial_end": 0.0},      # person's decision 11/06 (not a CEO key)
    "sales": {"warranty_enabled": True, "warranty_h": 48, "warranty_max_break": 0.05, "warranty_min_n": 5},
    "logistics": {"near_expiry_h": 12, "clearance_cut_per_h": 0.10, "clearance_max_cut": 0.90,
                  "aging_after_h": 24, "aging_step_h": 6, "aging_cut": 0.10, "aging_min_cost": 0.90},
    "cs": {"post_every_s": 300, "buyer_turns": ["purchase", "report", "callout"]},
    "buyer": {"top_k": 3, "min_offers": 3, "first_offer": 0.6},
}
# keys the policy may add that have no default: until the CEO sets them, the department keeps its old source
OPTIONAL = {"purchasing": {"high_share", "high_max"}}
# keys set by the person, not the CEO: reset() keeps their current values
USER_KEYS = {"purchasing": {"sofa_trial_start", "sofa_trial_end"}, "cs": {"buyer_turns"}}

_cache = {"mtime": None, "data": None, "clipped": set()}


def _check(section, key, value):
    """The value if it is usable, clipped into its range; None if it is not usable."""
    default = DEFAULTS.get(section, {}).get(key)
    if key in OPTIONAL.get(section, ()):
        default = 0.0
    if default is None or value is None:
        return None
    if isinstance(default, bool):
        return value if isinstance(value, bool) else None
    if isinstance(default, list):
        return value if isinstance(value, list) and all(isinstance(x, str) for x in value) and value else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    lo, hi = POLICY_RANGES.get(f"{section}.{key}", (None, None))
    v = value
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    if v != value and (section, key, value) not in _cache["clipped"]:
        _cache["clipped"].add((section, key, value))
        try:
            import decisions
            decisions.record("ceo", "policy_clip", f"{section}.{key}", v, f"{value} is outside {lo}..{hi}",
                             limit_hit=f"POLICY_RANGES {section}.{key}")
        except Exception:
            pass
    return type(default)(v) if isinstance(default, int) and not isinstance(default, bool) else v


def load():
    try:
        m = os.path.getmtime(PATH)
        if m != _cache["mtime"]:
            with open(PATH, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            _cache["mtime"], _cache["data"] = m, data
    except (OSError, ValueError):
        pass                                           # no file or a bad one: keep the last good one / defaults
    return _cache["data"] or {}


def get(section, key, default=None):
    v = _check(section, key, (load().get(section) or {}).get(key))
    if v is not None:
        return v
    return DEFAULTS.get(section, {}).get(key, default)


def values(section, *keys):
    return [get(section, k) for k in keys]


def version():
    return load().get("version")


def write(sections, reason, by="ceo", replace=False):
    """New version with these sections changed ({section: {key: value}}); values are checked like reads.
    replace=True: each given section is replaced whole (a key left out is removed; used by undo)."""
    cur = load()
    new = {k: v for k, v in cur.items() if k in DEFAULTS}
    for sec, kv in sections.items():
        checked = {k: _check(sec, k, v) for k, v in kv.items()}
        new[sec] = checked if replace else dict(new.get(sec) or {}, **checked)
    new.update(version=(cur.get("version") or 0) + 1, ts=round(time.time()), by=by, reason=reason)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(new, ensure_ascii=False) + "\n")
    return new


def reset(reason, by="ceo_guard"):
    """Back to DEFAULTS (version 1's values), dropping every key the CEO added; the version number still grows."""
    cur = load()
    new = {k: dict(v) for k, v in DEFAULTS.items()}
    for sec, keys in USER_KEYS.items():                 # the person's keys survive a CEO revert
        for k in keys:
            if (cur.get(sec) or {}).get(k) is not None:
                new[sec][k] = cur[sec][k]
    new.update(version=(cur.get("version") or 0) + 1, ts=round(time.time()), by=by, reason=reason)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(new, ensure_ascii=False) + "\n")
    return new


def revert_keys(section, values, reason, by="ceo_guard"):
    """New version with only these keys of one section set back ({key: value}; None removes the key, so the code's
    own fallback applies again). Every other key, the person's and the analyst's included, stays (person's
    decision 11/07: the CEO guard reverts the CEO's 4 keys only)."""
    cur = load()
    new = {k: v for k, v in cur.items() if k in DEFAULTS}
    sec = dict(new.get(section) or {})
    for k, v in values.items():
        if v is None:
            sec.pop(k, None)
        else:
            sec[k] = _check(section, k, v)
    new[section] = sec
    new.update(version=(cur.get("version") or 0) + 1, ts=round(time.time()), by=by, reason=reason)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(new, ensure_ascii=False) + "\n")
    return new


def init():
    """policy.json version 1 = DEFAULTS (the values in use before it)."""
    if not os.path.exists(PATH):
        return write(DEFAULTS, "start: the values the code used before policy.json", by="init")
    return load()
