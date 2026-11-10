"""Operator edits to attempt 1's policy.json while the attempt-2 switch is off (human code, not editable by the model).

11-07 11시 성수님 결정: the codex subscription ran out (reset 11-11 17:36), so a2/LIVE is off and attempt 1's rules run
the shop. The operator (now on Haiku) does not decide sales; it improves the rules by changing one number in
policy.json at a time, inside the ranges below. The file is never rewritten from the model's text: the kernel copies
the current file, changes the one key, checks the copy with attempt 1's own policy.load (its validate), keeps the old
file under a2/policy_bak/ and swaps the new one in with one rename. Attempt 1's seller re-reads policy.json when its
bytes change; its restock hook reads it on every run. Each edit writes one trajectory row of kind "policy_edit".
"""

import importlib.util
import json
import math
import os
import shutil
import tempfile

from kernel import common as c

POLICY = "policy.json"
BAK_DIR = os.path.join("a2", "policy_bak")
# key -> (lowest, highest, largest change in one edit); the policy file's own "bounds" apply as well
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
# "<key>.<grade>": one grade's number in a per-grade dict -> (grades, lowest, highest, largest change in one edit)
GRADE_KEYS = {
    "fresh_discount": (("B", "C"), 0.0, 0.5, 0.06),  # food and goods sections (attempt 1 ask_price has no kind check)
    "warranty_hours": (("A", "B", "C"), 0, 24, 6),  # goods sections; also capped by max_warranty_hours
    "grade_floor_list": (("A", "B", "C"), 0.3, 0.9, 0.05),  # shop only; attempt 1 validate allows [0.3, 0.9]
}
LEAF_KEY = "c_first_leaves"  # "c_first_leaves.<leaf name>" in shop: 1 puts the leaf in the list, 0 takes it out
INT_KEYS = ("max_rounds", "stock_target", "qty_per_buy", "warranty_hours", LEAF_KEY)
DEFAULTS = ("defaults.food", "defaults.goods")
BUYER = ("buyer.defaults.food", "buyer.defaults.goods")
WHERE = {
    "shop": "shop",
    "buyer": "buyer.defaults.food or buyer.defaults.goods",
    "goods": "defaults.goods or a goods category path",
    "rules": "defaults.food, defaults.goods or a category path",
}
COOLDOWN_S = 1800  # one edit per (section, key) in 30 min, so a change reaches the settle numbers before the next
MAX_PER_WAKE = 3


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
        return ((pol.get("buyer") or {}).get("defaults") or {}).get(section.rsplit(".", 1)[1])
    cats = pol.setdefault("categories", {})
    if section not in cats and create:
        cats[section] = {}
    return cats.get(section)


def _spec(key):
    """(lowest, highest, largest change) for an editable key, None for any other key."""
    base, _, sub = key.partition(".") if isinstance(key, str) else ("", "", "")
    if key in KEYS:
        return KEYS[key]
    if key in BUYER_KEYS:
        return BUYER_KEYS[key]
    if base in GRADE_KEYS and sub in GRADE_KEYS[base][0]:
        return GRADE_KEYS[base][1:]
    if base == LEAF_KEY and sub.strip() and ">" not in sub:
        return (0, 1, 1)
    return None


def _where(key):
    """Which sections take this key (a WHERE entry)."""
    base = key.split(".", 1)[0]
    if base in ("grade_floor_list", LEAF_KEY):
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
        return [f"grade_floor_list.{g}" for g in GRADE_KEYS["grade_floor_list"][0]]
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
    grades = {k: [list(v[0]), *v[1:]] for k, v in GRADE_KEYS.items()}
    lines = [
        f"편집 가능 키 (최소, 최대, 한 번에 바꿀 수 있는 폭): {json.dumps(KEYS, ensure_ascii=False)}",
        f"등급 키 <키>.<등급> (등급, 최소, 최대, 폭): {json.dumps(grades, ensure_ascii=False)}. "
        "fresh_discount는 판매 구역 어디나, warranty_hours는 goods 구역, grade_floor_list는 shop. warranty_hours는 max_warranty_hours를 넘지 못함. "
        "grade_floor_list에 없는 등급은 범위 안 아무 값으로 새로 둘 수 있음",
        f"shop의 {LEAF_KEY}.<잎 이름>: 1이면 목록에 넣고 0이면 뺌. "
        f"buyer.defaults.food/goods: {json.dumps(BUYER_KEYS, ensure_ascii=False)}. bounds 구역은 사람만 고침",
        f"같은 구역의 같은 키는 {COOLDOWN_S // 60}분에 한 번, 한 점검에 {MAX_PER_WAKE}개까지 바꿀 수 있음",
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
            lines.append(f"- {s}: {json.dumps({k: effective(pol, s, k) for k in BUYER_KEYS}, ensure_ascii=False)}")
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
        blk[base] = [x for x in blk.get(base) or [] if x != sub] + ([sub] if val else [])
    elif key in BUYER_KEYS and "." in key:
        blk[base] = {**(blk.get(base) or {}), key.split(".", 1)[1]: val}
    elif base in GRADE_KEYS:
        # the dict attempt 1 reads now (maybe inherited from defaults or the leaf entry), written here whole
        # with one grade changed, so the other grades keep their values
        blk[base] = {**(_resolved(pol, sec).get(base) or {}), key.split(".", 1)[1]: val}
    else:
        blk[key] = val


def _swap(root, pol, validate, tag):
    """Write pol to a temp file, check it with attempt 1's validate, keep the live file as a2/policy_bak/policy.json.<tag>
    and rename the temp over it. -> backup path. Raises on a failed check or write; the live file stays as it was."""
    os.makedirs(os.path.join(root, BAK_DIR), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".policy.tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(pol, f, ensure_ascii=False, indent=2)
            f.write("\n")
        (validate or attempt1_validate(root))(tmp)
        bak = os.path.join(root, BAK_DIR, f"policy.json.{tag}")
        shutil.copy2(path(root), bak)
        os.replace(tmp, path(root))
        return bak
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _one(root, it, traj, now, categories, validate, done_n):
    if not isinstance(it, dict):
        return {"ok": False, "error": "each policy item must be an object"}
    sec, key, val, reason = (
        it.get("section"),
        it.get("key"),
        it.get("value"),
        str(it.get("reason") or "")[:500],
    )
    if done_n >= MAX_PER_WAKE:
        return {"ok": False, "error": f"한 점검에 {MAX_PER_WAKE}개까지"}
    if sec == "bounds":
        return {"ok": False, "error": "bounds는 사람만 고침 (operator edits refused)"}
    if not isinstance(key, str) or _spec(key) is None:
        return {
            "ok": False,
            "error": f"key must be one of {sorted(KEYS) + sorted(BUYER_KEYS)}, "
            f"<{'|'.join(GRADE_KEYS)}>.<grade> or {LEAF_KEY}.<leaf name>",
        }
    base = key.split(".", 1)[0]
    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
        return {"ok": False, "error": "value must be a finite number"}
    if base in INT_KEYS:
        if val != int(val):
            return {"ok": False, "error": f"{key} must be a whole number"}
        val = int(val)
    if not reason:
        return {"ok": False, "error": "reason is required"}
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
        return {"ok": False, "error": f"section must be a full category path (대분류>...>잎), not the leaf name {sec}"}
    if not _fits(sec, key):
        return {"ok": False, "error": f"{key} cannot be edited in {sec}: only in {WHERE[_where(key)]}"}
    lo, hi, step = _spec(key)
    blo, bhi = (pol.get("bounds") or {}).get(base, (lo, hi))
    lo, hi = max(lo, blo), min(hi, bhi)
    if base == "warranty_hours":  # attempt 1 caps the hours it offers at max_warranty_hours (policy.py warranty_hours)
        hi = min(hi, int(_resolved(pol, sec).get("max_warranty_hours", 6)))
    if not lo <= val <= hi:
        return {"ok": False, "error": f"{key}={val} outside [{lo}, {hi}]"}
    old = effective(pol, sec, key)
    if key.startswith("accept_c.") and not (_resolved(pol, sec).get("accept_c") or {}).get("max_pay_frac"):
        return {"ok": False, "error": f"{sec} has no accept_c block: grade C buying is off there"}
    if val == 1 and base == LEAF_KEY and old == 0:
        sub = key.split(".", 1)[1]
        leaves = {k.split(">")[-1] for k in known | set((pol.get("shop") or {}).get("restock_priority") or [])}
        if sub not in leaves:
            return {"ok": False, "error": f"{sub} is not a leaf of a known category path"}
    if (
        isinstance(old, (int, float))
        and not isinstance(old, bool)
        and abs(val - old) > step + 1e-9
    ):
        return {
            "ok": False,
            "error": f"{key} can move at most {step} per edit (now {old})",
        }
    if old == val:
        return {"ok": False, "error": f"{key} is already {val}"}
    last = [
        r
        for r in recent(traj, now, COOLDOWN_S / 3600)
        if r.get("section") == sec and r.get("key") == key
    ]
    if last:
        return {
            "ok": False,
            "error": f"{sec} {key} was changed less than {COOLDOWN_S // 60} min ago",
        }
    _put(pol, sec, key, val)
    try:
        bak = _swap(root, pol, validate, f"{int(now)}-{done_n}")
    except (
        Exception
    ) as e:  # attempt 1's PolicyError or a write error: the live file stays as it was
        return {"ok": False, "error": f"check failed: {type(e).__name__}: {e}"}
    traj.append(
        "operator",
        "policy_edit",
        section=sec,
        key=key,
        old=old,
        new=val,
        reason=reason,
        backup=os.path.relpath(bak, root),
    )
    res = {"ok": True, "section": sec, "key": key, "old": old, "new": val}
    if old is None and base == "grade_floor_list":
        # 2026-11-10 성수님: the operator decides strategy; a first floor moves this grade from the market-price floor
        # to a share of list (attempt 1 market_floor), so the result says there was no step limit
        res["note"] = "이 등급은 처음 두는 값이라 한 번에 바꾸는 폭 제한을 적용하지 않음. 이 등급의 최저가가 시장가 기준에서 정가 비율로 바뀜"
    return res


def kernel_set(root, section, key, value, traj, now, reason, categories=(), validate=None):
    """Kernel-only write that restores a value the file held before (kernel.experiments reverts): the same typed write,
    attempt 1 validate, backup and rename as an operator edit, without the range, step, cooldown and per-wake checks.
    value None removes the key: a grade_floor_list (or other per-grade) grade, a c_first_leaves leaf or a scalar key
    the section did not set; a dict or category entry left empty goes too. -> {"ok": True/False, ...}"""
    if _spec(key) is None or not isinstance(section, str) or section == "bounds":
        return {"ok": False, "error": f"{section} {key} is not an editable key"}
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
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
        bak = _swap(root, pol, validate, f"{int(now)}-k")
    except Exception as e:  # attempt 1's PolicyError or a write error: the live file stays as it was
        return {"ok": False, "error": f"check failed: {type(e).__name__}: {e}"}
    reason = str(reason or "")[:500]
    traj.append("operator", "policy_edit", section=section, key=key, old=old, new=value, reason=reason,
                backup=os.path.relpath(bak, root), by="kernel")
    return {"ok": True, "section": section, "key": key, "old": old, "new": value}
