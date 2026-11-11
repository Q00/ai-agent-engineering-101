"""Operator edits to attempt 1's policy.json while the attempt-2 switch is off (human code, not editable by the model).

11-07 11시 성수님 결정: the codex subscription ran out (reset 11-11 17:36), so a2/LIVE is off and attempt 1's rules run
the shop. The operator does not decide sales; it improves the rules by changing numbers in policy.json inside the
ranges below (11-11 성수님: any number of keys at once, no step or cooldown; the strategy is the operator's). The file
is never rewritten from the model's text: the kernel copies the current file, changes the keys, checks the copy with attempt 1's own policy.load (its validate), keeps the old
file under a2/policy_bak/ and swaps the new one in with one rename. Attempt 1's seller re-reads policy.json when its
bytes change; its restock hook reads it on every run. Each edit writes one trajectory row of kind "policy_edit".
"""

import copy
import importlib.util
import json
import math
import os
import shutil
import tempfile

from kernel import common as c

POLICY = "policy.json"
BAK_DIR = os.path.join("a2", "policy_bak")
# key -> (lowest, highest, unused): the third number was the largest change in one edit and limits nothing since 11-11
# (strategy belongs to the operator); the policy file's own "bounds" apply as well
KEYS = {
    "price_mult": (0.5, 1.2, 0.1),
    "min_margin": (0.0, 0.5, 0.05),
    "concession": (0.0, 0.2, 0.03),
    "max_rounds": (1, 10, 2),
    "stock_target": (0, 60, 12),
    "qty_per_buy": (1, 20, 8),
    "warranty_uplift": (0.0, 0.2, 0.03),
}
# buyer.defaults.<kind> only; the shop-side keys above are refused there
# accept_c.max_pay_frac caps what our buyer pays for a grade C unit (attempt 1 market_buyer.grade_c_terms)
# open_frac and step_frac (11-10): 48 h of our purchases showed 571 of 1,518 struck at the opening offer (0.55 of list),
# while the top savers paid 0.46-0.55 of list; where the buyer opens and how fast it rises are the operator's to tune
BUYER_KEYS = {
    "max_pay_frac": (0.5, 1.0, 0.05),
    "open_frac": (0.3, 0.7, 0.05),
    "step_frac": (0.02, 0.15, 0.02),
    "accept_c.max_pay_frac": (0.4, 1.0, 0.05),
    "accept_c.open_frac": (0.2, 0.6, 0.05),
}
# "<key>.<grade>": one grade's number in a per-grade dict -> (grades, lowest, highest, unused)
GRADE_KEYS = {
    "fresh_discount": (
        ("B", "C"),
        0.0,
        0.5,
        0.06,
    ),  # food and goods sections (attempt 1 ask_price has no kind check)
    "warranty_hours": (
        ("A", "B", "C"),
        0,
        24,
        6,
    ),  # goods sections; also capped by max_warranty_hours
    "grade_floor_list": (
        ("A", "B", "C"),
        0.3,
        0.9,
        0.05,
    ),  # shop only; attempt 1 validate allows [0.3, 0.9]
}
# shop section only (11-11 성수님: 가게 운영에 관한 값은 모두 운영자가 정함). Restock and stock limits the rule code reads
# from policy.json "shop". Left to people: balance_floor, allow_bankrupt, liquidation_line (the bankruptcy line) and bounds.
SHOP_KEYS = {
    "max_inventory_cost_frac": (0.5, 6.0, 0.5),
    "max_spend_per_run": (300_000, 30_000_000, 3_000_000),
    "max_spend_frac": (0.05, 1.0, 0.15),
    "max_unit_cost": (100_000, 6_000_000, 1_000_000),
    "max_lines_per_run": (1, 30, 5),
    "max_total_units": (50, 1000, 100),
    "food_sell_through": (0.1, 1.0, 0.2),
    "food_cold_units": (0, 20, 4),
    "food_min_units": (0, 10, 2),
    "goods_cold_units": (0, 20, 4),
    "goods_min_units": (0, 10, 2),
    "goods_max_storage_frac": (0.001, 0.05, 0.01),
    "idle_pause_s": (0, 86_400, 21_600),
    "idle_low_stock_units": (0, 100, 20),
    "idle_low_stock_frac": (0.0, 1.0, 0.25),
    "own_velocity_tau_h": (1, 72, 12),
    "restock_stop_before_end_h": (0, 12, 2),
    "est_cost_frac": (0.3, 0.9, 0.1),
    "goods_cost_frac": (0.4, 0.9, 0.1),
    "market_floor_frac": (0.5, 1.0, 0.1),
    "food_min_left_frac": (0.0, 0.9, 0.2),
    "liquidation_margin": (0.0, 0.3, 0.05),
}
SHOP_INT = ("max_spend_per_run", "max_unit_cost", "max_lines_per_run", "max_total_units", "food_cold_units",
            "food_min_units", "goods_cold_units", "goods_min_units", "idle_pause_s", "idle_low_stock_units",
            "own_velocity_tau_h", "restock_stop_before_end_h")
LEAF_KEY = "c_first_leaves"  # "c_first_leaves.<leaf name>" in shop: 1 puts the leaf in the list, 0 takes it out
INT_KEYS = ("max_rounds", "stock_target", "qty_per_buy", "warranty_hours", LEAF_KEY) + SHOP_INT
DEFAULTS = ("defaults.food", "defaults.goods")
BUYER = ("buyer.defaults.food", "buyer.defaults.goods")
WHERE = {
    "shop": "shop",
    "buyer": "buyer.defaults.food or buyer.defaults.goods",
    "goods": "defaults.goods or a goods category path",
    "rules": "defaults.food, defaults.goods or a category path",
}


def path(root):
    return os.path.join(root, POLICY)


def attempt1_validate(root):
    """Attempt 1's own check: policy.load(path) parses and runs validate(), raising on a bad file."""
    spec = importlib.util.spec_from_file_location(
        "_r3a1_policy", os.path.join(root, "policy.py")
    )
    if spec is None or spec.loader is None:
        raise ValueError("attempt 1 policy.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return lambda p: mod.load(p)


def _kind(section):
    return (
        "food"
        if section in ("defaults.food",) or section.startswith("식품")
        else "goods"
    )


def _block(pol, section, create=False):
    if section in DEFAULTS:
        return pol["defaults"][section.split(".", 1)[1]]
    if section == "shop":
        return pol.setdefault("shop", {}) if create else pol.get("shop")
    if section in BUYER:
        return ((pol.get("buyer") or {}).get("defaults") or {}).get(
            section.rsplit(".", 1)[1]
        )
    cats = pol.setdefault("categories", {})
    if section not in cats and create:
        cats[section] = {}
    return cats.get(section)


def _spec(key):
    """(lowest, highest, unused) for an editable key, None for any other key."""
    base, _, sub = key.partition(".") if isinstance(key, str) else ("", "", "")
    if key in KEYS:
        return KEYS[key]
    if key in BUYER_KEYS:
        return BUYER_KEYS[key]
    if key in SHOP_KEYS:
        return SHOP_KEYS[key]
    if base in GRADE_KEYS and sub in GRADE_KEYS[base][0]:
        return GRADE_KEYS[base][1:]
    if base == LEAF_KEY and sub.strip() and ">" not in sub:
        return (0, 1, 1)
    return None


def _where(key):
    """Which sections take this key (a WHERE entry)."""
    base = key.split(".", 1)[0]
    if base in ("grade_floor_list", LEAF_KEY) or key in SHOP_KEYS:
        return "shop"
    if key in BUYER_KEYS:
        return "buyer"
    return "goods" if base == "warranty_hours" else "rules"


def _fits(section, key):
    w = _where(key)
    if section == "shop" or section in BUYER:
        return w == ("shop" if section == "shop" else "buyer")
    return w == "rules" or (w == "goods" and _kind(section) == "goods")


def _resolved(pol, section):
    """The block attempt 1 reads for this section, as its policy.resolve builds it: defaults[kind], then the
    categories entry under the leaf name, then the one under the full path. shop and buyer.defaults are read as is."""
    if section == "shop" or section in BUYER:
        return _block(pol, section) or {}
    defs = pol.get("defaults") or {}
    if section in DEFAULTS:
        return dict(defs.get(section.split(".", 1)[1]) or {})
    out = dict(defs.get(_kind(section)) or {})
    cats = pol.get("categories") or {}
    for k in (section.split(">")[-1], section):
        out.update(cats.get(k) or {})
    return out


def effective(pol, section, key):
    """The value attempt 1 uses for this section and key ("<key>.<grade>" reads one grade; c_first_leaves.<leaf> is
    1 when the leaf is listed, else 0). A grade missing from its dict reads 0 as attempt 1 does (policy.py ask_price,
    warranty_hours), except grade_floor_list, where a missing grade means no floor (None)."""
    blk = _resolved(pol, section)
    base, _, sub = key.partition(".")
    if not sub:
        return blk.get(key)
    if base == LEAF_KEY:
        return 1 if sub in (blk.get(base) or ()) else 0
    d = blk.get(base) or {}
    return d.get(sub, None if base == "grade_floor_list" else 0)


def _keys(section):
    """The editable keys of a section, as view lists them."""
    if section == "shop":
        return [f"grade_floor_list.{g}" for g in GRADE_KEYS["grade_floor_list"][0]] + list(SHOP_KEYS)
    if section in BUYER:
        return list(BUYER_KEYS)
    out = list(KEYS) + [f"fresh_discount.{g}" for g in GRADE_KEYS["fresh_discount"][0]]
    if _kind(section) == "goods":
        out += [f"warranty_hours.{g}" for g in GRADE_KEYS["warranty_hours"][0]]
    return out


def view(root, categories=()):
    """Current values of the editable keys, per section, for the operator's tool."""
    pol = c.load_json(path(root), {}) or {}
    secs = list(DEFAULTS) + sorted((pol.get("categories") or {}).keys())
    grades = {k: [list(v[0]), *v[1:3]] for k, v in GRADE_KEYS.items()}

    def rng(d):
        return json.dumps({k: list(v[:2]) for k, v in d.items()}, ensure_ascii=False)

    lines = [
        f"편집 가능 키 (최소, 최대): {rng(KEYS)}",
        f"등급 키 <키>.<등급> (등급, 최소, 최대): {json.dumps(grades, ensure_ascii=False)}. "
        "fresh_discount는 판매 구역 어디나, warranty_hours는 goods 구역, grade_floor_list는 shop. warranty_hours는 max_warranty_hours를 넘지 못함. "
        "grade_floor_list에 없는 등급은 범위 안 아무 값으로 새로 둘 수 있음",
        f"shop의 {LEAF_KEY}.<잎 이름>: 1이면 목록에 넣고 0이면 뺌. "
        f"shop의 재입고와 재고 한도 키 (최소, 최대): {rng(SHOP_KEYS)}. "
        f"buyer.defaults.food/goods (최소, 최대): {rng(BUYER_KEYS)}. bounds 구역은 사람만 고침",
    ]
    for s in secs:
        vals = {k: effective(pol, s, k) for k in _keys(s)}
        own = sorted({k.split(".", 1)[0] for k in vals} & set(_block(pol, s) or {}))
        lines.append(
            f"- {s}: {json.dumps(vals, ensure_ascii=False)}"
            + (
                f" (이 구역에 직접 적힌 키: {', '.join(own)})"
                if own and s not in DEFAULTS
                else ""
            )
        )
    shop = _resolved(pol, "shop")
    vals = {k: effective(pol, "shop", k) for k in _keys("shop")}
    vals[LEAF_KEY] = shop.get(LEAF_KEY) or []
    lines.append(f"- shop: {json.dumps(vals, ensure_ascii=False)}")
    for s in BUYER:
        if _block(pol, s) is not None:
            lines.append(
                f"- {s}: {json.dumps({k: effective(pol, s, k) for k in BUYER_KEYS}, ensure_ascii=False)}"
            )
    extra = sorted(set(categories) - set(secs))
    if extra:
        lines.append(
            "새로 값을 줄 수 있는 상품군(지금은 defaults 값을 씀): "
            + ", ".join(extra[:30])
        )
    return "\n".join(lines)


def recent(traj, now, hours=24):
    return [r for r in traj.since(now - hours * 3600) if r.get("kind") == "policy_edit"]


def apply(root, items, traj, now, categories=(), validate=None):
    """items: [{"section", "key", "value", "reason"}] -> [{"ok", ...}] one per item; a refused item changes nothing."""
    if not isinstance(items, list):
        return [{"ok": False, "error": "policy must be a list"}]
    out, done_n = [], 0
    for it in items:
        res = _one(root, it, traj, now, categories, validate, done_n)
        done_n += 1 if res.get("ok") else 0
        out.append(res)
    return out


def _put(pol, sec, key, val):
    """Write one typed value into pol (the in-memory policy) as attempt 1 reads it."""
    base = key.split(".", 1)[0]
    blk = _block(pol, sec, create=True)
    if base == LEAF_KEY:
        sub = key.split(".", 1)[1]
        blk[base] = [x for x in blk.get(base) or [] if x != sub] + (
            [sub] if val else []
        )
    elif key in BUYER_KEYS and "." in key:
        blk[base] = {**(blk.get(base) or {}), key.split(".", 1)[1]: val}
    elif base in GRADE_KEYS:
        # the dict attempt 1 reads now (maybe inherited from defaults or the leaf entry), written here whole
        # with one grade changed, so the other grades keep their values
        blk[base] = {**(_resolved(pol, sec).get(base) or {}), key.split(".", 1)[1]: val}
    else:
        blk[key] = val


def _checked_temp(root, pol, validate):
    """Write pol to a temp file beside policy.json and check it with attempt 1's validate. -> temp path (the caller
    renames or removes it). Raises on a failed check or write, with the temp file removed."""
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".policy.tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(pol, f, ensure_ascii=False, indent=2)
            f.write("\n")
        (validate or attempt1_validate(root))(tmp)
        return tmp
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _bak_name(root, tag):
    """a2/policy_bak/policy.json.<tag>, with -2, -3, ... added when that name is taken, so no backup is overwritten."""
    bak = os.path.join(root, BAK_DIR, f"policy.json.{tag}")
    n = 1
    while os.path.exists(bak):
        n += 1
        bak = os.path.join(root, BAK_DIR, f"policy.json.{tag}-{n}")
    return bak


def _swap(root, pol, validate, tag):
    """Write pol to a temp file, check it with attempt 1's validate, keep the live file as a2/policy_bak/policy.json.<tag>
    and rename the temp over it. -> backup path. Raises on a failed check or write; the live file stays as it was."""
    os.makedirs(os.path.join(root, BAK_DIR), exist_ok=True)
    tmp = _checked_temp(root, pol, validate)
    try:
        bak = _bak_name(root, tag)
        shutil.copy2(path(root), bak)
        os.replace(tmp, path(root))
        return bak
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _finite(v):
    """A real number that fits a float (an int like 10**400 does not)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def check(root, item, traj, now, categories=(), validate=None):
    """Every check of an operator edit (range, section and key rules, attempt 1 validate on a changed temp copy)
    without touching policy.json, no backup and no trajectory row. -> the success dict an edit returns
    ({"ok": True, section, key, old, new}) or {"ok": False, "error"}."""
    x = _prepare(root, item, now, categories)
    if not x.get("ok"):
        return x
    _put(x["pol"], x["section"], x["key"], x["new"])
    try:
        os.unlink(_checked_temp(root, x["pol"], validate))
    except Exception as e:  # attempt 1's PolicyError or a write error
        return {"ok": False, "error": f"check failed: {type(e).__name__}: {e}"}
    return _result(x)


def _result(x):
    res = {
        "ok": True,
        "section": x["section"],
        "key": x["key"],
        "old": x["old"],
        "new": x["new"],
    }
    if x["old"] is None and x["key"].split(".", 1)[0] == "grade_floor_list":
        # a first floor moves this grade from the market-price floor to a share of list (attempt 1 market_floor)
        res["note"] = "이 등급은 처음 두는 값임. 이 등급의 최저가가 시장가 기준에서 정가 비율로 바뀜"
    return res


def _one(root, it, traj, now, categories, validate, done_n):
    x = _prepare(root, it, now, categories)
    if not x.get("ok"):
        return x
    _put(x["pol"], x["section"], x["key"], x["new"])
    try:
        bak = _swap(root, x["pol"], validate, f"{int(now)}-{done_n}")
    except (
        Exception
    ) as e:  # attempt 1's PolicyError or a write error: the live file stays as it was
        return {"ok": False, "error": f"check failed: {type(e).__name__}: {e}"}
    traj.append(
        "operator",
        "policy_edit",
        section=x["section"],
        key=x["key"],
        old=x["old"],
        new=x["new"],
        reason=x["reason"],
        backup=os.path.relpath(bak, root),
    )
    return _result(x)


def _prepare(root, it, now, categories):
    """The checks of an operator edit that need no write: key and section rules, type, range (min and max, the file's
    bounds), a real change. No step, cooldown, count or reason rule (11-11 성수님: strategy belongs to the operator).
    -> {"ok": True, pol, section, key, old, new, reason} with pol the parsed live file (unchanged), or
    {"ok": False, "error"}."""
    if not isinstance(it, dict):
        return {"ok": False, "error": "each policy item must be an object"}
    sec, key, val, reason = (
        it.get("section"),
        it.get("key"),
        it.get("value"),
        str(it.get("reason") or "")[:500],
    )
    if sec == "bounds":
        return {"ok": False, "error": "bounds는 사람만 고침 (operator edits refused)"}
    if not isinstance(key, str) or _spec(key) is None:
        return {
            "ok": False,
            "error": f"key must be one of {sorted(KEYS) + sorted(BUYER_KEYS) + sorted(SHOP_KEYS)}, "
            f"<{'|'.join(GRADE_KEYS)}>.<grade> or {LEAF_KEY}.<leaf name>",
        }
    base = key.split(".", 1)[0]
    if not _finite(val):
        return {"ok": False, "error": "value must be a finite number"}
    if base in INT_KEYS:
        if val != int(val):
            return {"ok": False, "error": f"{key} must be a whole number"}
        val = int(val)
    try:
        pol = c.load_json(path(root), None)
    except ValueError:
        pol = None
    if not isinstance(pol, dict):
        return {"ok": False, "error": "policy.json unreadable"}
    known = set((pol.get("categories") or {}).keys()) | set(categories)
    fixed = DEFAULTS + ("shop",) + tuple(s for s in BUYER if _block(pol, s) is not None)
    if not isinstance(sec, str) or (sec not in fixed and sec not in known):
        return {
            "ok": False,
            "error": f"section must be one of {list(fixed)} or a full category path",
        }
    if sec not in fixed and ">" not in sec:
        # a leaf-name entry cannot tell food from goods here (_kind reads the path), so only full paths are edited
        return {
            "ok": False,
            "error": f"section must be a full category path (대분류>...>잎), not the leaf name {sec}",
        }
    if not _fits(sec, key):
        return {
            "ok": False,
            "error": f"{key} cannot be edited in {sec}: only in {WHERE[_where(key)]}",
        }
    lo, hi = _spec(key)[:2]
    blo, bhi = (pol.get("bounds") or {}).get(base, (lo, hi))
    lo, hi = max(lo, blo), min(hi, bhi)
    if (
        base == "warranty_hours"
    ):  # attempt 1 caps the hours it offers at max_warranty_hours (policy.py warranty_hours)
        hi = min(hi, int(_resolved(pol, sec).get("max_warranty_hours", 6)))
    if not lo <= val <= hi:
        return {"ok": False, "error": f"{key}={val} outside [{lo}, {hi}]"}
    old = effective(pol, sec, key)
    if key.startswith("accept_c.") and not (
        _resolved(pol, sec).get("accept_c") or {}
    ).get("max_pay_frac"):
        return {
            "ok": False,
            "error": f"{sec} has no accept_c block: grade C buying is off there",
        }
    if val == 1 and base == LEAF_KEY and old == 0:
        sub = key.split(".", 1)[1]
        leaves = {
            k.split(">")[-1]
            for k in known | set((pol.get("shop") or {}).get("restock_priority") or [])
        }
        if sub not in leaves:
            return {
                "ok": False,
                "error": f"{sub} is not a leaf of a known category path",
            }
    if old == val:
        return {"ok": False, "error": f"{key} is already {val}"}
    return {
        "ok": True,
        "pol": pol,
        "section": sec,
        "key": key,
        "old": old,
        "new": val,
        "reason": reason,
    }


def apply_many(root, items, traj, now, categories=(), validate=None, tag=None, by=None):
    """Write any number of operator edits as one change: every item gets the checks of an edit (_prepare), the items
    that pass go into one copy of policy.json, which attempt 1's validate checks once before one backup and one
    rename. A refused item is left out; the same section and key twice refuses the later one; a copy that fails
    validate writes nothing and refuses every item. One trajectory row of kind "policy_edit" per written item with
    by (one label for all, or a list aligned with items; None writes no by). -> [result per item, aligned]."""
    items = list(items) if isinstance(items, list) else []
    bys = by if isinstance(by, list) else [by] * len(items)
    out, seen = [], set()
    for it in items:
        x = _prepare(root, it, now, categories)
        if x.get("ok"):
            k = (x["section"], x["key"])
            if k in seen:
                x = {"ok": False, "error": f"{x['section']} {x['key']}가 이 목록에 두 번 있음"}
            seen.add(k)
        out.append(x)
    xs = [(i, x) for i, x in enumerate(out) if x.get("ok")]
    if not xs:
        return out
    pol = xs[0][1]["pol"]
    for _, x in xs:
        _put(pol, x["section"], x["key"], x["new"])
    try:
        bak = _swap(root, pol, validate, tag or kernel_tag(now))
    except Exception as e:  # attempt 1's PolicyError or a write error: the live file stays as it was
        err = {"ok": False, "error": f"check failed: {type(e).__name__}: {e}"}
        return [dict(err) if x.get("ok") else x for x in out]
    for i, x in xs:
        traj.append(
            "operator",
            "policy_edit",
            section=x["section"],
            key=x["key"],
            old=x["old"],
            new=x["new"],
            reason=x["reason"],
            backup=os.path.relpath(bak, root),
            **({"by": bys[i]} if bys[i] is not None else {}),
        )
    return [_result(x) if x.get("ok") else x for x in out]


_SEQ = [
    0
]  # per-process counter: kernel writes in one tick with the same `now` still get distinct backup names


def kernel_tag(now, eid=None):
    """Backup tag of a kernel write: <now>-k<experiment id>-<per-process counter>."""
    _SEQ[0] += 1
    return f"{int(now)}-k{'' if eid is None else eid}-{_SEQ[0]}"


def kernel_set(
    root,
    section,
    key,
    value,
    traj,
    now,
    reason,
    categories=(),
    validate=None,
    tag=None,
):
    """Kernel-only write of a typed value (kernel.experiments: an adopted split value, or a revert of an experiment
    registered before raw states were kept): the same typed write, attempt 1 validate, backup and rename as an operator
    edit, without the range check.
    value None removes the key: a grade_floor_list (or other per-grade) grade, a c_first_leaves leaf or a scalar key
    the section did not set; a dict or category entry left empty goes too. tag names the backup (kernel_tag(now) when
    None). -> {"ok": True/False, ...}"""
    if _spec(key) is None or not isinstance(section, str) or section == "bounds":
        return {"ok": False, "error": f"{section} {key} is not an editable key"}
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (int, float))
    ):
        return {"ok": False, "error": "value must be a number or None"}
    base, _, sub = key.partition(".")
    if value is not None and base in INT_KEYS:
        value = int(value)
    try:
        pol = c.load_json(path(root), None)
    except ValueError:
        pol = None
    if not isinstance(pol, dict):
        return {"ok": False, "error": "policy.json unreadable"}
    known = set((pol.get("categories") or {}).keys()) | set(categories)
    fixed = DEFAULTS + ("shop",) + tuple(s for s in BUYER if _block(pol, s) is not None)
    if section not in fixed and section not in known or not _fits(section, key):
        return {"ok": False, "error": f"{key} cannot be set in {section}"}
    old = effective(pol, section, key)
    if value is None and base == LEAF_KEY:
        value = 0
    if value is None:
        blk = _block(pol, section) or {}
        if sub:
            d = blk.get(base)
            if isinstance(d, dict):
                d.pop(sub, None)
                if not d:
                    blk.pop(base)
        else:
            blk.pop(key, None)
        cats = pol.get("categories") or {}
        if section in cats and not cats[section]:
            del cats[section]
    else:
        _put(pol, section, key, value)
    try:
        bak = _swap(root, pol, validate, tag or kernel_tag(now))
    except (
        Exception
    ) as e:  # attempt 1's PolicyError or a write error: the live file stays as it was
        return {"ok": False, "error": f"check failed: {type(e).__name__}: {e}"}
    reason = str(reason or "")[:500]
    traj.append(
        "operator",
        "policy_edit",
        section=section,
        key=key,
        old=old,
        new=value,
        reason=reason,
        backup=os.path.relpath(bak, root),
        by="kernel",
    )
    return {"ok": True, "section": section, "key": key, "old": old, "new": value}


BASES = (
    set(KEYS) | {k.split(".", 1)[0] for k in BUYER_KEYS} | set(GRADE_KEYS) | {LEAF_KEY} | set(SHOP_KEYS)
)


def raw_state(pol, section, key):
    """What the section itself holds for the key's base ("fresh_discount" for fresh_discount.B, the c_first_leaves list
    for c_first_leaves.<leaf>, "accept_c" for accept_c.<x>): (present, deep copy of the raw value or None). A missing
    section entry reads as absent."""
    blk = _block(pol, section) if isinstance(pol, dict) else None
    base = key.split(".", 1)[0]
    if not isinstance(blk, dict) or base not in blk:
        return False, None
    return True, copy.deepcopy(blk[base])


def inherited_value(pol, section, base):
    """What a category section reads for base when it does not set base itself (defaults[kind], then the leaf-name
    entry, as _resolved builds it); None for defaults, shop and buyer sections, which inherit nothing."""
    if section in DEFAULTS or section == "shop" or section in BUYER or not isinstance(pol, dict):
        return None
    out = dict((pol.get("defaults") or {}).get(_kind(section)) or {})
    leaf = section.split(">")[-1]
    if leaf != section:
        out.update((pol.get("categories") or {}).get(leaf) or {})
    return copy.deepcopy(out.get(base))


def _restore_sub(pol, blk, section, base, sub, present, value, inherited=None):
    """Put back one grade of a per-grade dict, one accept_c field or one c_first_leaves leaf inside the section's own
    base, leaving every other part of that base as it is now (a later direct write or a kept sibling experiment stays).
    present/value: the section's raw state for base before the experiment (raw_state). When base was not set in the
    section, the operator write materialised {**inherited, sub: new} there (_put); the inverse removes base again when
    every other grade still equals what the section inherits now or inherited then (inherited), so no pinned copy of
    inherited values stays. -> False when the saved state does not have the base's shape (the caller restores the whole
    base)."""
    if not isinstance(blk, dict):
        return True  # no section entry and nothing saved: nothing to put back
    cur = blk.get(base)
    if base == LEAF_KEY:
        if (present and not isinstance(value, list)) or (cur is not None and not isinstance(cur, list)):
            return False
        was, now = (list(value) if present else []), list(cur or [])
        if [x for x in now if x != sub] == [x for x in was if x != sub]:  # nothing else in the list changed
            if present:
                blk[base] = copy.deepcopy(value)
            else:
                blk.pop(base, None)
        elif sub in was and sub not in now:
            now.insert(min(was.index(sub), len(now)), sub)
            blk[base] = now
        elif sub not in was:
            blk[base] = [x for x in now if x != sub]
        return True
    if (present and not isinstance(value, dict)) or (cur is not None and not isinstance(cur, dict)):
        return False
    parent = inherited_value(pol, section, base)
    parent = parent if isinstance(parent, dict) else {}
    d = dict(cur) if isinstance(cur, dict) else dict(parent)
    src = value if present else parent
    if sub in src:
        d[sub] = copy.deepcopy(src[sub])
    else:
        d.pop(sub, None)
    if present:
        blk[base] = d
        return True
    inh = inherited if isinstance(inherited, dict) else {}
    if all((g in parent and v == parent[g]) or (g in inh and v == inh[g]) for g, v in d.items()):
        blk.pop(base, None)  # only inherited values left: the section inherits again
    else:
        blk[base] = d
    return True


def kernel_restore(
    root,
    section,
    base,
    present,
    value,
    traj,
    now,
    reason,
    tag=None,
    key=None,
    validate=None,
    inherited=None,
):
    """Kernel-only write that puts the section's own raw state for `base` back as raw_state captured it. For a key
    with a part inside base (fresh_discount.B, accept_c.open_frac, c_first_leaves.<leaf>) only that part goes back
    (_restore_sub, inherited: what the section inherited for base when it did not set it); the result is the whole
    saved state when nothing else in base changed since. Any other key: present -> blk[base] = value, absent -> the
    key removed. A category entry left empty is removed, so a category never keeps a pinned copy of an inherited
    value. Attempt 1 validate, backup (tag, kernel_tag(now) when None) and rename as every edit; one trajectory row
    kind "policy_edit" by "kernel" (key: the experiment's key, else base). -> {"ok": True/False, ...}"""
    if (
        not isinstance(section, str)
        or section == "bounds"
        or base not in BASES
        or not (
            section in DEFAULTS
            or section in BUYER
            or section == "shop"
            or ">" in section
        )
    ):
        return {"ok": False, "error": f"{section} {base} is not an editable key"}
    try:
        pol = c.load_json(path(root), None)
    except ValueError:
        pol = None
    if not isinstance(pol, dict):
        return {"ok": False, "error": "policy.json unreadable"}
    shown = key if isinstance(key, str) and key.split(".", 1)[0] == base else base
    try:
        old = effective(pol, section, shown)
        blk = _block(pol, section, create=bool(present))
        sub = shown.split(".", 1)[1] if "." in shown else ""
        if sub and _restore_sub(pol, blk, section, base, sub, present, value, inherited):
            pass
        elif present:
            blk[base] = copy.deepcopy(value)
        elif isinstance(blk, dict):
            blk.pop(base, None)
        cats = pol.get("categories")
        if isinstance(cats, dict) and section in cats and not cats[section]:
            del cats[section]
        new = effective(pol, section, shown)
        bak = _swap(root, pol, validate, tag or kernel_tag(now))
    except (
        Exception
    ) as e:  # attempt 1's PolicyError, a write error or a section the file lacks
        return {"ok": False, "error": f"check failed: {type(e).__name__}: {e}"}
    traj.append(
        "operator",
        "policy_edit",
        section=section,
        key=shown,
        old=old,
        new=new,
        reason=str(reason or "")[:500],
        backup=os.path.relpath(bak, root),
        by="kernel",
    )
    return {"ok": True, "section": section, "key": shown, "old": old, "new": new}
