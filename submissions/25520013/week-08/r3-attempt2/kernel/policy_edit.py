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
INT_KEYS = ("max_rounds", "stock_target", "qty_per_buy")
DEFAULTS = ("defaults.food", "defaults.goods")
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
    cats = pol.setdefault("categories", {})
    if section not in cats and create:
        cats[section] = {}
    return cats.get(section)


def effective(pol, section, key):
    """The value attempt 1 uses for this section: defaults[kind] overlaid by the category entry."""
    blk = _block(pol, section)
    if blk and key in blk:
        return blk[key]
    return (pol.get("defaults") or {}).get(_kind(section), {}).get(key)


def view(root, categories=()):
    """Current values of the editable keys, per section, for the operator's tool."""
    pol = c.load_json(path(root), {}) or {}
    secs = list(DEFAULTS) + sorted((pol.get("categories") or {}).keys())
    lines = [
        f"편집 가능 키 (최소, 최대, 한 번에 바꿀 수 있는 폭): {json.dumps(KEYS, ensure_ascii=False)}",
        f"같은 구역의 같은 키는 {COOLDOWN_S // 60}분에 한 번, 한 점검에 {MAX_PER_WAKE}개까지 바꿀 수 있음",
    ]
    for s in secs:
        vals = {k: effective(pol, s, k) for k in KEYS}
        own = sorted(k for k in KEYS if k in (_block(pol, s) or {}))
        lines.append(
            f"- {s}: {json.dumps(vals, ensure_ascii=False)}"
            + (
                f" (이 구역에 직접 적힌 키: {', '.join(own)})"
                if own and s not in DEFAULTS
                else ""
            )
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
    if key not in KEYS:
        return {"ok": False, "error": f"key must be one of {sorted(KEYS)}"}
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return {"ok": False, "error": "value must be a number"}
    if key in INT_KEYS:
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
    if not isinstance(sec, str) or (sec not in DEFAULTS and sec not in known):
        return {
            "ok": False,
            "error": "section must be defaults.food, defaults.goods or a full category path",
        }
    lo, hi, step = KEYS[key]
    blo, bhi = (pol.get("bounds") or {}).get(key, (lo, hi))
    lo, hi = max(lo, blo), min(hi, bhi)
    if not lo <= val <= hi:
        return {"ok": False, "error": f"{key}={val} outside [{lo}, {hi}]"}
    old = effective(pol, sec, key)
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
    _block(pol, sec, create=True)[key] = val
    os.makedirs(os.path.join(root, BAK_DIR), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".policy.tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(pol, f, ensure_ascii=False, indent=2)
            f.write("\n")
        (validate or attempt1_validate(root))(tmp)
        bak = os.path.join(root, BAK_DIR, f"policy.json.{int(now)}-{done_n}")
        shutil.copy2(path(root), bak)
        os.replace(tmp, path(root))
    except (
        Exception
    ) as e:  # attempt 1's PolicyError or a write error: the live file stays as it was
        if os.path.exists(tmp):
            os.unlink(tmp)
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
    return {"ok": True, "section": sec, "key": key, "old": old, "new": val}
