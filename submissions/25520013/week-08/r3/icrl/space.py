"""icrl action space: the allowlist (action_space.json), policy path get/set, guards and byte-stable dumps.

Guards in code (DESIGN.md sec 8): allowlisted path, fixed keys untouchable, type / range / max step, at most 3 edits
of one side, one restock_priority op, new category overrides only for categories in the market stats, no supply
increase from 24 h before the supply stop, and the edited policy passes policy.validate.
"""

import copy
import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
KINDS = ("food", "goods")
MAX_EDITS = 3
FIXED = frozenset(
    {
        "shop.balance_floor",
        "shop.max_inventory_cost_frac",
        "shop.max_total_units",
        "shop.restock_stop_before_end_h",
        "shop.liquidation_line",
        "shop.liquidation_margin",
    }
)
FIXED_RX = re.compile(
    r"(^|\.)(liquidat\w*|bounds|balance_floor|max_inventory_cost_frac|max_total_units|"
    r"restock_stop_before_end_h)(\.|$)"
)
SUPPLY_UP_KEYS = frozenset(
    {
        "stock_target",
        "qty_per_buy",
        "breadth_qty",
        "skus_per_category",
        "max_spend_per_run",
        "max_unit_cost",
        "max_lines_per_run",
    }
)


def load(path=None):
    d = json.loads(Path(path or HERE / "action_space.json").read_text(encoding="utf-8"))
    out = []
    for e in d["keys"]:
        fam = dict(d["families"][e["family"]])
        for kind in KINDS if "{kind}" in e["path"] else (None,):
            x = dict(fam, **{k: v for k, v in e.items() if k != "by_kind"})
            if kind:
                x.update((e.get("by_kind") or {}).get(kind, {}))
                x["path"] = e["path"].replace("{kind}", kind)
                if x.get("default_from"):
                    x["default_from"] = x["default_from"].replace("{kind}", kind)
                x["kind"] = kind
            out.append(x)
    return out


def split(path):
    if path.startswith("categories."):
        cat, _, key = path[len("categories.") :].rpartition(".")
        return ["categories", cat, key]
    return path.split(".")


def get(pol, path):
    d = pol
    for p in split(path):
        if not isinstance(d, dict) or p not in d:
            return None, False
        d = d[p]
    return d, True


def put(pol, path, value):
    parts = split(path)
    d = pol
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def entry(space, path):
    for e in space:
        if e["path"] == path:
            return e
    if path.startswith("categories."):
        _, cat, key = split(path)
        for e in space:
            if e["path"] == f"categories.*.{key}" and cat:
                return dict(e, path=path, category=cat)
    return None


def current(pol, path, e, kind_of=None):
    v, ok = get(pol, path)
    if ok:
        return v
    if e.get("category"):
        kind = (kind_of or {}).get(e["category"], "goods")
        v, ok = get(pol, f"defaults.{kind}.{split(path)[-1]}")
        if ok:
            return v
    if e.get("default_from"):
        v, ok = get(pol, e["default_from"])
        if ok:
            return v
    return copy.deepcopy(e.get("default"))


def fixed(path):
    return path in FIXED or bool(FIXED_RX.search(path))


def _num_ok(e, cur, v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return "value must be a finite number"
    if e["type"] == "int" and v != int(v):
        return "must be an integer"
    if not e["min"] - 1e-9 <= v <= e["max"] + 1e-9:
        return f"{v} outside [{e['min']}, {e['max']}]"
    if isinstance(cur, (int, float)) and not isinstance(cur, bool):
        up = max(e.get("max_step", 0), cur * (e.get("max_ratio", 1) - 1))
        dn = max(e.get("max_step", 0), cur * (1 - 1 / e.get("max_ratio", 1)))
        if v - cur > up + 1e-9 or cur - v > dn + 1e-9:
            return f"step {cur} -> {v} exceeds max step (+{up:g} / -{dn:g})"
    return None


def list_op(old, new):
    """-> ('insert', x) | ('remove', x) | ('move', x) | None for one edit of a list of names."""
    old, new = list(old or []), list(new or [])
    if len(new) == len(old) + 1:
        for i, x in enumerate(new):
            if x not in old and new[:i] + new[i + 1 :] == old:
                return "insert", x
    if len(new) == len(old) - 1:
        for i, x in enumerate(old):
            if old[:i] + old[i + 1 :] == new:
                return "remove", x
    if len(new) == len(old) and sorted(new) == sorted(old) and new != old:
        for i, x in enumerate(old):
            rest = old[:i] + old[i + 1 :]
            if [y for y in new if y != x] == rest:
                return "move", x
    return None


def direction(e, cur, v):
    if e["type"] == "list":
        op = list_op(cur, v)
        return op[0] if op else None
    if e["type"] == "enum":
        vals = e["values"]
        return "up" if vals.index(v) > vals.index(cur) else "down"
    return "up" if v > cur else "down"


def step_value(e, cur, d):
    """The value one full max step away from cur in direction d ('up' / 'down'), clipped; None when at a bound."""
    if e["type"] == "enum":
        i = e["values"].index(cur) if cur in e["values"] else 0
        j = i + (1 if d == "up" else -1)
        return e["values"][j] if 0 <= j < len(e["values"]) else None
    if not isinstance(cur, (int, float)):
        return None
    up = max(e.get("max_step", 0), cur * (e.get("max_ratio", 1) - 1))
    dn = max(e.get("max_step", 0), cur * (1 - 1 / e.get("max_ratio", 1)))
    v = min(e["max"], cur + up) if d == "up" else max(e["min"], cur - dn)
    v = (
        int(math.floor(v) if d == "up" else math.ceil(v))
        if e["type"] == "int"
        else round(v, 4)
    )
    return None if v == cur else v


def supply_up(e, cur, v):
    return (
        e["family"] == "supply"
        and split(e["path"])[-1] in SUPPLY_UP_KEYS
        and direction(e, cur, v) == "up"
    )


def check(edits, pol, space, market_cats=(), kind_of=None, supply_ban=False):
    """-> (violations, rows) where rows = [{path, current, value, entry, dir}] for the valid edits."""
    import policy

    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_EDITS:
        return [f"edits must be a list of 1..{MAX_EDITS}"], []
    bad, rows, seen = [], [], set()
    cats = set(market_cats)
    for i, ed in enumerate(edits):
        if (
            not isinstance(ed, dict)
            or not isinstance(ed.get("path"), str)
            or "value" not in ed
        ):
            bad.append(f"edit {i}: needs path and value")
            continue
        path, v = ed["path"], ed["value"]
        if path in seen:
            bad.append(f"edit {i}: {path} edited twice")
            continue
        seen.add(path)
        if fixed(path):
            bad.append(f"edit {i}: {path} is a fixed key")
            continue
        e = entry(space, path)
        if e is None:
            bad.append(f"edit {i}: {path} not in the allowlist")
            continue
        if (
            e.get("category")
            and not get(pol, f"categories.{e['category']}")[1]
            and e["category"] not in cats
        ):
            bad.append(f"edit {i}: new override for a category not in the market stats")
            continue
        cur = current(pol, path, e, kind_of)
        if e["type"] == "enum":
            if v not in e["values"] or (
                cur in e["values"]
                and abs(e["values"].index(v) - e["values"].index(cur)) > e["max_step"]
            ):
                bad.append(
                    f"edit {i}: {path}={v!r} not an allowed step of {e['values']}"
                )
                continue
        elif e["type"] == "list":
            op = (
                list_op(cur, v)
                if isinstance(v, list) and all(isinstance(x, str) and x for x in v)
                else None
            )
            if op is None:
                bad.append(
                    f"edit {i}: {path} must change by one insert, remove or move"
                )
                continue
            if op[0] == "insert" and op[1] not in cats:
                bad.append(f"edit {i}: {op[1][:40]} not in the market stats")
                continue
        else:
            why = _num_ok(e, cur, v)
            if why:
                bad.append(f"edit {i}: {path} {why}")
                continue
            v = int(v) if e["type"] == "int" else v
        if v == cur:
            bad.append(f"edit {i}: {path} unchanged")
            continue
        if supply_ban and supply_up(e, cur, v):
            bad.append(f"edit {i}: {path} increases supply inside the pre-stop ban")
            continue
        rows.append(
            {
                "path": path,
                "current": cur,
                "value": v,
                "entry": e,
                "dir": direction(e, cur, v),
            }
        )
    if not bad and len({r["entry"]["side"] for r in rows}) > 1:
        bad.append("a bundle may not mix shop and buyer keys")
    if not bad:
        try:
            policy.validate(apply(pol, rows))
        except Exception as ex:  # noqa: BLE001 - policy.PolicyError and malformed values
            bad.append(f"edited policy fails validate: {str(ex)[:120]}")
    return bad, (rows if not bad else [])


def apply(pol, rows):
    new = copy.deepcopy(pol)
    for r in rows:
        put(new, r["path"], r["value"])
    return new


def fmt(raw):
    """(indent, ensure_ascii, trailing newline) of an existing JSON file's bytes."""
    text = raw.decode("utf-8")
    m = re.search(r"^\{\n( +)\"", text)
    indent = len(m.group(1)) if m else None
    return indent, all(ord(c) < 128 for c in text), text.endswith("\n")


def dumps_like(raw, obj):
    indent, ascii_, nl = fmt(raw)
    return (
        json.dumps(obj, indent=indent, ensure_ascii=ascii_) + ("\n" if nl else "")
    ).encode("utf-8")
