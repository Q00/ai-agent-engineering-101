"""The strategy file strategy/SKILL.md as a validated policy.

The front matter's `policy` block holds every tunable value (decisions D1-D14, levers L1-L6).
Hooks call load() on every run and the seller calls it per request; a changed file is picked
up without restarting the kit. A file that fails validation is never used: the last good
policy (memory/policy.lastgood.json) is used instead, then DEFAULTS, and the error is logged.
Keys missing from the file (a key added to DEFAULTS after the file was written) take the DEFAULTS
value, and so does a whole section of ADDED_SECTIONS the file does not have; this is a warning in
logs/hooks/policy.log, not an error.

    python policy.py check                   validate and show the effective values
    python policy.py check --reason "..."    also record the change in logs/policy-changes.jsonl
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HOME = Path(os.environ.get("R3_HOME", Path(__file__).resolve().parent))
SKILL = HOME / "strategy" / "SKILL.md"
LASTGOOD = HOME / "memory" / "policy.lastgood.json"
CHANGES = HOME / "logs" / "policy-changes.jsonl"

# The schema and the last-resort fallback. Keep in step with strategy/SKILL.md.
DEFAULTS = {
    "seller": {
        "first_ask_list": 0.85, "first_ask_big_list": 0.78, "floor_cost_mult": 1.08, "floor_list": 0.60,
        "food_clear_uwh_factor": 2.0, "food_clear_min_obs": 10, "food_clear_left_share": 0.25,
        "goods_stale_days": 5,
        "warranty_on_request": True, "warranty_hours": 6, "warranty_grades": ["A", "B"],
        "warranty_premium": 0.15, "warranty_cap_6h": 500000,
        "fact_block": True, "claim_food_margin_h": 1.0, "promise_ttl_s": 180,
        "intro_price_enabled": False,
    },
    "stock": {
        "inventory_cap_share": 0.30, "cash_floor": 1000000, "worst_cost_share": 0.75,
        "food_shelf_hours": 720, "per_sku_qty": 2, "short_food_max_shelf_h": 168,
        "short_food_min_uwh_obs": 10, "goods_max_list": 600000,
        "supply_demand_priority": True, "demand_window_h": 6, "regular_min_stock": 1,
        "max_lines": 10, "max_qty": 20, "max_hold": 40, "min_line_qty": 4, "require_own_card": True,
        "bankrupt_min_hours_to_end": 48, "bankrupt_recovery_threshold": 500000, "bankrupt_zero_sales_h": 12,
        "end_ts": 1794927540, "taper_h": 0, "taper_factor": 0.5, "goods_stop_h": 6,
        "clear_cost_h": 48, "clear_70_h": 0, "clear_any_h": 0, "no_warranty_h": 8,
        "window_lead_s": 60, "cover_min": 25, "cover_safety": 1.5,
        "focus_skus": [], "focus_max": 8, "focus_cover_mult": 2.0, "focus_cap_share": 0.5, "focus_min_settles": 10,
        "trend_skus": [], "trend_max": 16, "trend_share": 0.08,
        "big_ticket_share": 0.40, "big_ticket_list": 40000, "bigticket_probe_qty": 3, "min_margin_share": 0.05,
    },
    "market": {"surge_free": 50, "surge_per_unit": 0.002, "surge_max": 1.5},
    "buyer": {
        "cap_goods_no_warranty": 0.70, "cap_goods_warranty": 0.94, "cap_food": 0.95,
        "min_surplus_list": 0.05, "min_kept": 0.0, "open_list": 0.60, "max_shops": 3, "food_margin_h": 0.2,
        "target_s": 20, "continue_p": 0.75, "msg_timeout_s": 12, "order_cap_s": 60,
        "binding": True, "accept_cert": True,
    },
    "llm": {"planner_enabled": False, "planner_every_min": 30, "planner_max_per_h": 4,
            "exception_enabled": True, "exception_max_per_h": 10, "exception_min_left_s": 30,
            "exception_timeout_s": 20},
    "forum": {"enabled": True, "max_per_day": 288, "min_gap_min": 5, "shop_replies": False, "unmet_demand": False},
}

# Bounds for values where a typo would be costly. (low, high), inclusive.
RANGES = {
    "seller.first_ask_list": (0.3, 1.5), "seller.first_ask_big_list": (0.3, 1.5),
    "seller.floor_cost_mult": (0.0, 2.0), "seller.floor_list": (0.0, 1.5),
    "seller.warranty_hours": (0, 6), "seller.warranty_premium": (0.0, 1.0), "seller.promise_ttl_s": (10, 3600),
    "seller.warranty_cap_6h": (0, 20000000),
    "stock.inventory_cap_share": (0.0, 1.0), "stock.cash_floor": (0, 5000000), "stock.worst_cost_share": (0.25, 1.0),
    "stock.per_sku_qty": (1, 20), "stock.max_lines": (0, 10), "stock.max_qty": (1, 20),
    "buyer.cap_goods_no_warranty": (0.0, 1.5), "buyer.cap_goods_warranty": (0.0, 1.5), "buyer.cap_food": (0.0, 1.5),
    "buyer.open_list": (0.0, 1.5), "buyer.max_shops": (1, 10), "buyer.target_s": (1, 240),
    "buyer.continue_p": (0.0, 1.0), "buyer.msg_timeout_s": (1, 120), "buyer.order_cap_s": (5, 280),
    "buyer.food_margin_h": (0.0, 24.0), "buyer.min_surplus_list": (0.0, 0.5), "buyer.min_kept": (0.0, 0.6),
    "stock.window_lead_s": (0, 60),         # the kit retries a closed-window line 3 x 30 s, then skips it
    "stock.cover_min": (5, 240), "stock.cover_safety": (0.5, 5.0),
    "llm.planner_max_per_h": (0, 20), "llm.exception_max_per_h": (0, 60), "forum.max_per_day": (0, 288),
    "forum.min_gap_min": (5, 1440),
    "stock.focus_max": (0, 20), "stock.focus_cover_mult": (1.0, 4.0), "stock.focus_cap_share": (0.0, 1.0),
    "stock.focus_min_settles": (1, 1000), "llm.exception_min_left_s": (0, 280), "llm.exception_timeout_s": (3, 60),
    "stock.max_hold": (1, 200), "stock.min_line_qty": (1, 20), "stock.trend_max": (0, 40),
    "stock.trend_share": (0.0, 0.5), "stock.big_ticket_share": (0.0, 1.0), "stock.bigticket_probe_qty": (1, 20),
    "stock.min_margin_share": (0.0, 0.3), "stock.goods_max_list": (10000, 2000000),
    "stock.big_ticket_list": (10000, 1000000),
    "market.surge_free": (0, 100000), "market.surge_per_unit": (0.0, 0.1), "market.surge_max": (1.0, 5.0),
}

# Sections added to DEFAULTS after files in use were written: filled whole when the file has none.
ADDED_SECTIONS = ("market",)


# --- a small YAML subset: nested maps by two-space indent, scalars, flow lists of scalars

def _scalar(s):
    s = s.strip()
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if s in ("null", "~", ""):
        return None
    if s[:1] == s[-1:] and s[:1] in ("'", '"') and len(s) >= 2:
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d*|-?\.\d+", s):
        return float(s)
    return s


def _strip_comment(line):
    out, quote = [], None
    for ch in line:
        if quote:
            quote = None if ch == quote else quote
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def parse_yaml(text):
    root = {}
    stack = [(-1, root)]
    for n, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if "\t" in line[:indent] or indent % 2:
            raise ValueError(f"line {n}: indent with two spaces")
        key, sep, rest = line.strip().partition(":")
        if not sep or not key:
            raise ValueError(f"line {n}: expected 'key: value'")
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if key in parent:
            raise ValueError(f"line {n}: duplicate key {key}")
        if rest.strip():
            parent[key] = _scalar(rest)
        else:
            parent[key] = {}
            stack.append((indent, parent[key]))
    return root


def split_skill(text):
    """(front matter dict, body text) of a SKILL.md-style file."""
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    if not m:
        raise ValueError("no front matter between --- lines")
    return parse_yaml(m.group(1)), m.group(2)


# --- validation

def validate(pol):
    errors = []

    def walk(d, ref, path):
        if not isinstance(d, dict):
            errors.append(f"{path or 'policy'}: expected a mapping")
            return
        for k in d.keys() - ref.keys():
            errors.append(f"{path + '.' if path else ''}{k}: unknown key")
        for k, want in ref.items():
            p = f"{path + '.' if path else ''}{k}"
            if k not in d:
                errors.append(f"{p}: missing")
                continue
            v = d[k]
            if isinstance(want, dict):
                walk(v, want, p)
            elif isinstance(want, bool):
                if not isinstance(v, bool):
                    errors.append(f"{p}: expected true or false")
            elif isinstance(want, (int, float)):
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    errors.append(f"{p}: expected a number")
                elif isinstance(want, int) and not isinstance(want, bool) and isinstance(v, float) and not v.is_integer():
                    errors.append(f"{p}: expected a whole number")
                elif p in RANGES and not RANGES[p][0] <= v <= RANGES[p][1]:
                    errors.append(f"{p}: {v} outside {RANGES[p]}")
            elif isinstance(want, list):
                if not isinstance(v, list):
                    errors.append(f"{p}: expected a list")
    walk(pol, DEFAULTS, "")
    if not errors:
        if pol["stock"]["cash_floor"] < 500000:
            errors.append("stock.cash_floor: below the bankruptcy line 500000")
        if pol["seller"]["floor_list"] > pol["seller"]["first_ask_list"]:
            errors.append("seller.floor_list: above seller.first_ask_list")
        if pol["buyer"]["target_s"] > pol["buyer"]["order_cap_s"]:
            errors.append("buyer.target_s: above buyer.order_cap_s")
        for name in ("focus", "trend"):
            skus = pol["stock"][f"{name}_skus"]
            if not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", x) for x in skus):
                errors.append(f"stock.{name}_skus: expected SKU strings")
            elif len(skus) > pol["stock"][f"{name}_max"] or len(set(skus)) != len(skus):
                errors.append(f"stock.{name}_skus: more than stock.{name}_max, or repeated")
    return errors


def fill_defaults(pol):
    """(copy of pol with keys missing from its sections taken from DEFAULTS, dotted keys filled).
    A missing or empty section is not filled (except a missing one of ADDED_SECTIONS), and unknown keys and bad
    values are left for validate()."""
    if not isinstance(pol, dict):
        return pol, []
    out, filled = json.loads(json.dumps(pol)), []

    def walk(d, ref, path):
        for k, want in ref.items():
            if k not in d:
                d[k] = json.loads(json.dumps(want))
                filled.append(path + k)
            elif isinstance(want, dict) and isinstance(d[k], dict):
                walk(d[k], want, f"{path}{k}.")
    for sec, ref in DEFAULTS.items():
        if isinstance(out.get(sec), dict) and out[sec]:
            walk(out[sec], ref, f"{sec}.")
        elif sec in ADDED_SECTIONS and sec not in out:
            out[sec] = {}
            walk(out[sec], ref, f"{sec}.")
    return out, filled


def _sha8(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:8]


_cache = {}


def load(path=None):
    """(policy, ver, errors). ver is 'version/sha8'; errors is empty when the file was used."""
    path = Path(path or SKILL)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    hit = _cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        front, _ = split_skill(path.read_text(encoding="utf-8"))
        pol, filled = fill_defaults(front.get("policy"))
        errors = validate(pol)
    except (OSError, ValueError) as e:
        front, pol, filled, errors = {}, None, [], [f"{path.name}: {e}"]
    if not errors:
        ver = f"{front.get('version', 0)}/{_sha8(front['policy'])}"     # the file's, as the strategist reports it
        if _save_lastgood(pol, ver, filled) and filled:                 # once per file version and filled values
            _warn(f"{path.name} {ver}: missing, using DEFAULTS: " +
                  ", ".join(f"{k}={json.dumps(get(DEFAULTS, k))}" for k in filled))
        result = (pol, ver, [])
    else:
        good = _read_lastgood()
        result = (good["policy"], good["ver"] + "+lastgood", errors) if good else \
                 (json.loads(json.dumps(DEFAULTS)), "defaults", errors)
    _cache[str(path)] = (mtime, result)
    return result


def body(path=None):
    """The strategy text the planner reads."""
    try:
        return split_skill(Path(path or SKILL).read_text(encoding="utf-8"))[1]
    except (OSError, ValueError):
        return ""


def get(pol, dotted):
    v = pol
    for k in dotted.split("."):
        v = v[k]
    return v


def _read_lastgood():
    try:
        good = json.loads(LASTGOOD.read_text(encoding="utf-8"))
        good["policy"] = fill_defaults(good.get("policy"))[0]    # saved before a key was added to DEFAULTS
        return good if not validate(good["policy"]) else None
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def _save_lastgood(pol, ver, filled=()):
    """True when the file was written (a new version, or new effective values, e.g. a changed DEFAULTS fill)."""
    good = _read_lastgood()
    if good and good.get("ver") == ver and good["policy"] == pol:
        return False
    try:
        LASTGOOD.parent.mkdir(parents=True, exist_ok=True)
        tmp = LASTGOOD.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps({"ver": ver, "policy": pol, "filled": list(filled)}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, LASTGOOD)
        return True
    except OSError:
        return False


def _warn(msg):
    try:
        from memory.store import hook_log
        hook_log("policy", msg)
    except Exception:                       # a warning must never stop a hook
        pass


def _flat(d, prefix=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--reason", help="why the values changed; recorded in logs/policy-changes.jsonl")
    a = ap.parse_args(argv)
    before = _read_lastgood()
    pol, ver, errors = load()
    if errors:
        print("INVALID, not used:", *errors, sep="\n  ")
        print(f"effective policy: {ver}")
        return 1
    old = _flat(before["policy"]) if before else {}
    new = _flat(pol)
    diff = {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v} if before else {}
    print(f"OK {ver}")
    for k in fill_defaults(split_skill(SKILL.read_text(encoding="utf-8"))[0].get("policy"))[1]:
        print(f"  {k}: missing from the file, using DEFAULTS {json.dumps(get(DEFAULTS, k))}")
    for k, (o, n) in sorted(diff.items()):
        print(f"  {k}: {o} -> {n}")
    if a.reason and (diff or not before):
        import time
        CHANGES.parent.mkdir(parents=True, exist_ok=True)
        with open(CHANGES, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "old_ver": before and before["ver"], "new_ver": ver,
                                "diff": diff, "reason": a.reason}, ensure_ascii=False) + "\n")
        print(f"recorded in {CHANGES.relative_to(HOME)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
