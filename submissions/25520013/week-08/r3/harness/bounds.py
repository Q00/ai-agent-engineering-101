"""Range lock for policy edits: check(base, proposal) -> [violations]; apply(base, proposal) -> new policy dict.

Proposal schema: {"edits": [{"path", "value"}, ...], "rationale", "evidence_seq", "kind_hint"}, 1..MAX_EDITS edits.
Paths: defaults.<kind>.<key> | categories.<cat>.<key> | buyer.defaults.<kind>.<key> | buyer.categories.<cat>.<key>,
kind in food/goods (a categories path takes its kind from kind_hint). Rules: key whitelisted (SHOP_KEYS / BUYER_KEYS)
and present in base["bounds"]; value a finite number (int for INT_KEYS) inside the bound; one edit moves the value
at most MAX_STEP_FRAC x (hi - lo) from the current value (category override, else the kind default); no path twice;
the edited policy passes policy.validate. dumps(pol) -> the exact bytes written to a policy file.
"""

import copy
import json
import math
import re

import policy

MAX_EDITS = 3
MAX_STEP_FRAC = 0.25
SHOP_KEYS = frozenset(
    {
        "price_mult",
        "min_margin",
        "concession",
        "max_rounds",
        "stock_target",
        "qty_per_buy",
    }
)
BUYER_KEYS = frozenset({"open_frac", "max_pay_frac", "step_frac", "max_rounds"})
INT_KEYS = frozenset({"max_rounds", "stock_target", "qty_per_buy"})
KINDS = ("food", "goods")
_PATH = re.compile(r"^(buyer\.)?(defaults|categories)\.([^.]+)\.([a-z_]+)$")


class EditRejected(ValueError):
    def __init__(self, violations):
        super().__init__("; ".join(violations))
        self.violations = list(violations)


def _current(base, side, scope, name, key, kind):
    src = (base.get("buyer") or {}) if side else base
    if scope == "categories":
        cat = (src.get("categories") or {}).get(name) or {}
        if key in cat:
            return cat[key]
    return ((src.get("defaults") or {}).get(kind) or {}).get(key)


def _edit_violations(base, ed, kind, i):
    if not isinstance(ed, dict) or set(ed) != {"path", "value"}:
        return [f"edit {i} must be {{path, value}}"]
    m = _PATH.match(ed["path"]) if isinstance(ed["path"], str) else None
    if not m:
        return [f"edit {i}: path {ed['path']!r} not editable"]
    side, scope, name, key = bool(m.group(1)), m.group(2), m.group(3), m.group(4)
    if scope == "defaults":
        if name not in KINDS:
            return [f"edit {i}: kind {name!r}"]
        if name != kind:
            return [f"edit {i}: {ed['path']} outside kind_hint {kind!r}"]
    if key not in (BUYER_KEYS if side else SHOP_KEYS):
        return [f"edit {i}: key {key!r} is not whitelisted"]
    b = (base.get("bounds") or {}).get(key)
    if not b:
        return [f"edit {i}: key {key!r} has no base bounds"]
    lo, hi = b
    v = ed["value"]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return [f"edit {i}: value must be a finite number"]
    if key in INT_KEYS and v != int(v):
        return [f"edit {i}: {key} must be an integer"]
    if not lo <= v <= hi:
        return [f"edit {i}: {key}={v} outside [{lo}, {hi}]"]
    cur = _current(base, side, scope, name, key, kind)
    if isinstance(cur, (int, float)) and not isinstance(cur, bool):
        step = MAX_STEP_FRAC * (hi - lo)
        if abs(v - cur) > step + 1e-9:
            return [
                f"edit {i}: {key} {cur} -> {v} moves more than {MAX_STEP_FRAC} x range ({step:g})"
            ]
    return []


def _set(pol, path, value, key):
    parts = path.split(".")
    d = pol
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = int(value) if key in INT_KEYS else value


def check(base, proposal):
    if not isinstance(proposal, dict):
        return ["proposal is not an object"]
    kind = proposal.get("kind_hint")
    if kind not in KINDS:
        return [f"kind_hint {kind!r} not in {KINDS}"]
    edits = proposal.get("edits")
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_EDITS:
        return [f"edits must be a list of 1..{MAX_EDITS}"]
    v = []
    for i, ed in enumerate(edits):
        v += _edit_violations(base, ed, kind, i)
    paths = [ed.get("path") for ed in edits if isinstance(ed, dict)]
    if len(set(map(str, paths))) != len(paths):
        v.append("the same path is edited twice")
    if v:
        return v
    new = copy.deepcopy(base)
    for ed in edits:
        _set(new, ed["path"], ed["value"], ed["path"].rsplit(".", 1)[-1])
    try:
        policy.validate(new)
    except (policy.PolicyError, TypeError, ValueError) as e:
        return [f"edited policy fails validate: {e}"]
    return []


def apply(base, proposal):
    v = check(base, proposal)
    if v:
        raise EditRejected(v)
    new = copy.deepcopy(base)
    for ed in proposal["edits"]:
        _set(new, ed["path"], ed["value"], ed["path"].rsplit(".", 1)[-1])
    return new


def dumps(pol):
    return (
        json.dumps(
            {k: v for k, v in pol.items() if k != "_hash"}, ensure_ascii=False, indent=1
        )
        + "\n"
    ).encode("utf-8")
