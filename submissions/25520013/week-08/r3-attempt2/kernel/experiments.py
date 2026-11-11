"""Experiment ledger, verdicts and automatic revert or adoption for operator policy edits (human code, not editable by
the model).

DESIGN 14.4 step 1. start() is the one entry point for an operator policy item: it checks, writes and registers as
one step and never raises. Two modes:

- sequential: the edit goes into policy.json and the experiment compares the metric over the window_h hours after it
  with the window_h hours before it (shop side: the before value scaled by the market's revenue rate in the same
  scope; buyer side: by the other buyers' deal rate), then keeps the edit, puts the section's raw state back
  (policy_edit.kernel_restore) or extends the window once. One running experiment per scope (blocked()).
- split (v2.12 arms): for sale-side keys the seller reads after the unit pick (SPLIT_KEYS), policy.json keeps the old
  value and <root>/policy_arms.json gives the new value to the sales whose unit id hashes into arm "b" (attempt 1
  policy.arm_pick, reimplemented here as arm_b). The verdict compares the two arms over the same hours, so no before
  window and no market adjustment; "better" writes the new value (policy_edit.kernel_set) and removes the arm.

Ledger: <root>/a2/experiments.json. Plain code, no model calls.
"""

import copy
import hashlib
import json
import math
import os
import statistics as st
import tempfile
from collections import defaultdict

from kernel import brief
from kernel import common as c
from kernel import policy_edit as pe

LEDGER = os.path.join("a2", "experiments.json")
MIN_WINDOW_H, MAX_WINDOW_H = 2, 8
SLOW_KEYS = (
    "stock_target",
    "qty_per_buy",
)  # restock keys act through new stock, so they get a longer window
DEFAULT_WINDOW_H = {"shop": 3, "slow": 4, "buyer": 2}
MATURITY_H = {
    "shop": 2,
    "buyer": 0,
}  # breaks arrive a median 1.1 h after a sale; buyer deals are final at settle
JOIN_LOOKBACK_H = 12  # sales read before the before window, so a break joins the right sale of the same buyer and sku
METRIC = {"shop": "net_profit_per_h", "buyer": "saved_gain_per_h"}
SCORE_METRIC = "score_proxy_per_h"  # shop side with rev_weight > 0: net profit + rev_weight x revenue, per hour
STARS_MIN_N = 10  # reviews needed on both sides for the stars guard
STARS_DROP = 0.15
THIN_N = 5  # sales (deals) needed in the after window for a verdict
REL_MIN = 0.10  # an effect must also exceed this share of max(|before_adj|, FLOOR)
CLEAR_SE = 2  # "clear" needs |effect| > CLEAR_SE x se
# FLOOR: judgment values, not measured: the smallest per-hour change worth acting on when the before value is near 0
FLOOR = {"shop": 100_000, "buyer": 0.05}
R_CLAMP = (0.5, 2.0)
RETRY_S = (
    600  # a failed revert or a failed evaluation is retried at most once per 10 min
)
MAX_FAILS = (
    6  # then the experiment ends as voided (eval_error), the value left as it is
)
COST_KNOWN_MIN = 0.8  # share of sales with a known unit cost needed on a side with >= COST_N_MIN sales
COST_N_MIN = 5
BASELINE_SLACK_S = (
    600  # the events read must reach back to the before window's start + this
)
MIN_BEFORE_H = 1  # a before window clipped shorter than this has no baseline
CONTAM_S = (
    2 * 3600
)  # an overlapping experiment that ended reverted/dropped/voided blocks new sequential ones this long
BLOCK_AFTER = ("reverted", "dropped", "voided")
GONE = (
    "reverted",
    "dropped",
    "voided",
    "adopted",
)  # a value that was live and is no longer: before windows stop there

# split mode
ARMS_FILE = "policy_arms.json"
ARMS_MAX = 8  # attempt 1 policy.ARMS_MAX: a file with more arms is ignored whole, so a 9th start is refused
ARM_SHARE = 0.5
ARM_GRACE_S = 1800  # an arm lives until eval_ts + this
SPLIT_KEYS = (
    "price_mult",
    "min_margin",
    "concession",
    "max_rounds",
    "warranty_uplift",
    "fresh_discount",  # .<grade>
    "warranty_hours",  # .<grade>
    "grade_floor_list",  # .<grade>
)
SPLIT_THIN_N = 10  # sales needed over both arms
NO_UNIT_MAX = (
    0.2  # share of sales without a unit id above which the split verdict is thin
)

STATUS_KO = {
    "running": "진행",
    "kept": "유지",
    "reverted": "되돌림",
    "voided": "무효",
    "adopted": "채택",
    "dropped": "버림",
}
REASON_KO = {
    "stars": "별점 하락",
    "thin": "표본 적음",
    "clear": "잡음 넘음",
    "noise": "잡음 안",
    "point_estimate": "점추정(약한 판정)",
    "no_activity": "양쪽 거래 없음",
    "value_changed": "값이 따로 바뀜",
    "no_baseline": "비교 기준 없음",
    "cost_unknown": "원가 모름",
    "no_unit": "유닛 모름",
    "eval_error": "평가 실패, 값은 그대로 둠",
}
EXPECT_CHARS = 40
LINES_RUN, LINES_ENDED = 6, 5
ROLES = ("shop", "buyer")  # the role operators (kernel.operator --role)
OTHER = " (다른 운영자)"  # a role's state card marks the other role's lines


def ledger_path(root):
    return os.path.join(root, LEDGER)


def load(root):
    """The whole ledger list; [] for a missing or unreadable file."""
    rows = c.load_json(ledger_path(root), [])
    return rows if isinstance(rows, list) else []


def _atomic_json(p, obj, prefix):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=prefix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save(root, rows):
    _atomic_json(ledger_path(root), rows, ".experiments.tmp-")


def scope_of(section, key=None):
    """-> (side, scope): "buyer:<kind>" for buyer.defaults.<kind>, "kind:<kind>" for defaults.<kind>, "shop:all" for
    shop, else the category leaf name (brief._leaf). key does not change the scope."""
    if section.startswith("buyer."):
        return "buyer", "buyer:" + section.rsplit(".", 1)[1]
    if section in pe.DEFAULTS:
        return "shop", "kind:" + section.split(".", 1)[1]
    if section == "shop":
        return "shop", "shop:all"
    return "shop", brief._leaf(section)


def _scope_kind(section, scope):
    """The kind a shop-side scope covers, None for shop:all."""
    if scope == "shop:all":
        return None
    return scope.split(":", 1)[1] if scope.startswith("kind:") else pe._kind(section)


def _overlap(sec_a, sec_b):
    (side_a, a), (side_b, b) = scope_of(sec_a), scope_of(sec_b)
    if side_a != side_b or a == b:
        return a == b
    if side_a == "buyer":
        return False
    if "shop:all" in (a, b):
        return True
    ka, kb = _scope_kind(sec_a, a), _scope_kind(sec_b, b)
    return (a.startswith("kind:") or b.startswith("kind:")) and ka == kb


def _num_ok(v):
    return pe._finite(v)


def splittable(key):
    """True for the sale-side keys the seller reads after the unit pick (SPLIT_KEYS; per-grade keys with their grade)."""
    if not isinstance(key, str) or pe._spec(key) is None:
        return False
    base, _, sub = key.partition(".")
    if base in pe.GRADE_KEYS:
        return bool(sub)
    return not sub and base in SPLIT_KEYS


def role_of(e):
    """The role operator an experiment belongs to: the role stored when it was proposed, else buyer for a buyer.*
    section and shop for any other."""
    if e.get("role") in ROLES:
        return e["role"]
    return "buyer" if str(e.get("section") or "").startswith("buyer.") else "shop"


def set_role(root, eid, role):
    """Store the proposing role on the ledger entry with this id."""
    rows = load(root)
    for e in rows:
        if isinstance(e, dict) and e.get("id") == eid:
            e["role"] = role
            save(root, rows)
            return


def running(root):
    """The experiments with status running (non-dict ledger entries skipped)."""
    return [
        e for e in load(root) if isinstance(e, dict) and e.get("status") == "running"
    ]


def blocked(root, section, key, now, mode="sequential"):
    """None when an edit of (section, key) may start an experiment now, else a Korean message: a running experiment
    (either mode) whose scope overlaps, or (sequential only) an overlapping one that ended reverted, dropped or voided
    less than CONTAM_S ago, since its value still colours the before window."""
    rows = [e for e in load(root) if isinstance(e, dict)]
    for e in rows:
        if e.get("status") != "running":
            continue
        try:
            if _overlap(section, e["section"]):
                return (
                    f"실험 #{e['id']} ({e['section']} {e['key']} {e['old']}→{e['new']}, 범위 {e['scope']})이 "
                    f"{c.kst(e['eval_ts'], '%H:%M')} 판정 전까지 진행 중이라 {section}은 지금 바꿀 수 없음"
                )
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            continue
    if mode != "sequential":
        return None
    for e in rows:
        try:
            t = e.get("ended_ts")
            if (
                e.get("status") in BLOCK_AFTER
                and _num_ok(t)
                and now < t + CONTAM_S
                and _overlap(section, e["section"])
            ):
                return (
                    f"실험 #{e['id']} ({e['section']} {e['key']})이 {c.kst(t, '%H:%M')}에 "
                    f"{STATUS_KO.get(e['status'], e['status'])}으로 끝나 그 영향이 남아 있어 {section}의 새 실험은 "
                    f"{c.kst(t + CONTAM_S, '%H:%M')}까지 시작할 수 없음"
                )
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            continue
    return None


def _before_from(rows, section):
    """Latest end time of an overlapping experiment whose value was live and is no longer (GONE), None if none."""
    out = None
    for e in rows:
        try:
            t = e.get("ended_ts")
            if (
                e.get("status") in GONE
                and _num_ok(t)
                and _overlap(section, e["section"])
            ):
                out = t if out is None else max(out, t)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def default_window_h(side, key):
    if side == "buyer":
        return DEFAULT_WINDOW_H["buyer"]
    return DEFAULT_WINDOW_H["slow" if key.split(".", 1)[0] in SLOW_KEYS else "shop"]


def _window(wh, side, key):
    """A whole number of hours in [MIN_WINDOW_H, MAX_WINDOW_H]; anything that is not a finite number takes the
    default (an int too big for a float is clamped without converting it)."""
    if isinstance(wh, bool) or not isinstance(wh, (int, float)):
        return default_window_h(side, key)
    if isinstance(wh, float) and not math.isfinite(wh):
        return default_window_h(side, key)
    return int(round(min(MAX_WINDOW_H, max(MIN_WINDOW_H, wh))))


def _eval_ts(exp):
    return exp["started_ts"] + exp["window_h"] * 3600 + exp["maturity_h"] * 3600


def _next_id(rows):
    return 1 + max(
        [
            e["id"]
            for e in rows
            if isinstance(e, dict)
            and isinstance(e.get("id"), int)
            and not isinstance(e.get("id"), bool)
        ]
        or [0]
    )


def board_saved(root, now, login=None):
    """Our buyer's board "saved" in the latest snapshot at or before now (else the earliest one), None if absent."""
    login = c.owner_login(root) if login is None else login
    snaps = brief.board_snapshots(root)
    past = [s for s in snaps if s[0] <= now] or snaps[:1]
    if not past:
        return None
    b = c.load_json(past[-1][1], {}) or {}
    me = next(
        (
            r
            for r in b.get("buyers") or []
            if isinstance(r, dict) and c.is_mine(r, login)
        ),
        None,
    )
    v = (me or {}).get("saved")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def register(
    root,
    row,
    expect,
    window_h,
    now,
    traj,
    rev_weight=0.0,
    mode="sequential",
    raw=None,
    eid=None,
    arm=None,
):
    """Record a successful policy edit (or split check) ({section, key, old, new}) as a running experiment. window_h
    None (or not a finite number) takes the default for the key; any value is clamped to [MIN_WINDOW_H, MAX_WINDOW_H]
    whole hours. rev_weight (shop side, the caller's board-derived worth of one won of revenue in net-profit won)
    turns the metric into score_proxy_per_h = net + rev_weight x revenue; it is kept for the whole experiment.
    mode "sequential" keeps raw ({base, present, value}: the section's own state before the edit, for the revert) and
    before_from (see _before_from); mode "split" keeps arm (the policy_arms.json entry). -> the experiment."""
    rev_weight = rev_weight if _num_ok(rev_weight) and rev_weight > 0 else 0.0
    side, scope = scope_of(row["section"], row["key"])
    rows = load(root)
    exp = {
        "id": _next_id(rows) if eid is None else eid,
        "mode": mode,
        "section": row["section"],
        "key": row["key"],
        "old": row.get("old"),
        "new": row.get("new"),
        "started_ts": now,
        "window_h": _window(window_h, side, row["key"]),
        "maturity_h": MATURITY_H[side],
        "scope": scope,
        "side": side,
        "metric": SCORE_METRIC if side == "shop" and rev_weight > 0 else METRIC[side],
        "rev_weight": float(rev_weight) if side == "shop" else 0.0,
        "expect": str(expect or "")[:500],
        "status": "running",
        "extended": False,
    }
    if mode == "split":
        exp["arm"] = arm
    else:
        if raw is not None:
            exp["raw"] = raw
        exp["before_from"] = _before_from(rows, row["section"])
    if side == "buyer":
        exp["s0"] = board_saved(root, now)
    exp["eval_ts"] = _eval_ts(exp)
    rows.append(exp)
    save(root, rows)
    traj.append(
        "operator",
        "experiment_start",
        **{
            k: exp[k]
            for k in (
                "id",
                "mode",
                "section",
                "key",
                "old",
                "new",
                "scope",
                "side",
                "metric",
                "rev_weight",
                "window_h",
                "eval_ts",
                "expect",
            )
        },
    )
    return exp


def _drop_entry(root, eid):
    """Remove the ledger entry with this id (a start that failed half way)."""
    try:
        rows = load(root)
        keep = [e for e in rows if not (isinstance(e, dict) and e.get("id") == eid)]
        if len(keep) != len(rows):
            save(root, keep)
    except Exception:  # noqa: BLE001 - best effort; the caller reports the original failure
        pass


# --- arms file (split mode)


def arms_path(root):
    return os.path.join(root, ARMS_FILE)


def load_arms(root):
    """The arms list of <root>/policy_arms.json as written ([] for a missing, corrupt or wrong-shaped file)."""
    raw = c.load_json(arms_path(root), None)
    arms = raw.get("arms") if isinstance(raw, dict) else None
    return arms if isinstance(arms, list) else []


def _write_arms(root, arms):
    _atomic_json(arms_path(root), {"arms": arms}, ".policy_arms.tmp-")


def _remove_arm(root, eid):
    arms = load_arms(root)
    keep = [a for a in arms if not (isinstance(a, dict) and a.get("id") == eid)]
    if len(keep) != len(arms):
        _write_arms(root, keep)


def _set_arm_until(root, eid, until):
    arms = load_arms(root)
    for a in arms:
        if isinstance(a, dict) and a.get("id") == eid:
            a["until"] = until
    _write_arms(root, arms)


def arm_target(pol, section, key, value):
    """(path, value) of the arm that makes attempt 1 read `value` for (section, key): ["defaults", kind, base],
    ["categories", <path>, base] or ["shop", base]; a per-grade key carries the whole dict attempt 1 reads for the
    section with that one grade changed (the policy_edit._put construction)."""
    base, _, sub = key.partition(".")
    if section in pe.DEFAULTS:
        path = ["defaults", section.split(".", 1)[1], base]
    elif section == "shop":
        path = ["shop", base]
    else:
        path = ["categories", section, base]
    if sub:
        cur = pol
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if isinstance(cur, dict):  # the dict is in the file here: arm only the grade, so a later change of another
            return path + [sub], copy.deepcopy(value)  # grade is not frozen into the arm
        value = {**(pe._resolved(pol, section).get(base) or {}), sub: value}
    return path, copy.deepcopy(value)


def seller_has_arms(root):
    """False when <root>/policy.py (the attempt 1 rule code the seller runs) is there and has no with_arms: a split
    started against such a seller would compare two identical arms."""
    try:
        with open(os.path.join(root, "policy.py"), encoding="utf-8") as f:
            return "def with_arms(" in f.read()
    except OSError:
        return True


def sync_arms(root, rows=None):
    """Make the arms file hold exactly the arms of the running split experiments of the ledger (the ledger is the
    source of truth): orphan, repeated-id and stale arms go, a lost arm comes back. -> the arms list."""
    rows = load(root) if rows is None else rows
    want = [
        {"id": e["id"], **{k: e["arm"][k] for k in ("path", "value", "share", "until")}}
        for e in rows
        if isinstance(e, dict) and e.get("status") == "running" and e.get("mode") == "split"
        and isinstance(e.get("arm"), dict) and all(k in e["arm"] for k in ("path", "value", "share", "until"))
    ]
    raw = c.load_json(arms_path(root), None) if os.path.exists(arms_path(root)) else {"arms": []}
    if not isinstance(raw, dict) or raw.get("arms") != want:
        if want or os.path.exists(arms_path(root)):
            _write_arms(root, want)
    return want


def arm_b(arm_id, unit, share=ARM_SHARE):
    """True when a sale of this unit got arm "b": attempt 1 policy.arm_pick(arm_id, arm_salt(unit), share)."""
    salt = str(unit)
    return (
        int(hashlib.sha256(f"{arm_id}:{salt}".encode()).hexdigest()[:8], 16) / 2**32
        < share
    )


# --- the one entry point


def _err(msg):
    return {"ok": False, "error": str(msg)[:500]}


def start(
    root,
    item,
    now,
    traj,
    rev_weight=0.0,
    mode="auto",
    categories=(),
    validate=None,
    split_ok=False,
):
    """Check, write and register one operator policy item {section, key, value, reason, expect, window_h} as an
    experiment. mode "auto": split when split_ok and the key is splittable on a shop-side section, else sequential;
    "split" / "sequential" force it. Never raises; on any failure nothing is left half done (a written edit whose
    registration fails is put back at once). -> {"ok": True, "experiment", "mode", "section", "key", "old", "new",
    "eval_ts"} (plus "note" when the edit has one) or {"ok": False, "error"}."""
    try:
        if not isinstance(item, dict):
            return _err("each policy item must be an object")
        sec, key = item.get("section"), item.get("key")
        if not isinstance(sec, str) or not isinstance(key, str):
            return _err("section and key must be strings")
        if mode not in ("auto", "split", "sequential"):
            return _err(f"mode must be auto, split or sequential, not {mode!r}")
        can_split = splittable(key) and scope_of(sec, key)[0] == "shop"
        if mode == "split" and not can_split:
            return _err(
                f"{key} cannot be compared concurrently (split keys: {', '.join(SPLIT_KEYS)})"
            )
        split = mode == "split" or (
            mode == "auto" and split_ok is True and can_split and seller_has_arms(root)
        )
        msg = blocked(root, sec, key, now, "split" if split else "sequential")
        if msg:
            return _err(msg)
        expect = item.get("expect")
        if not isinstance(expect, str) or not expect:
            expect = item.get("reason") if isinstance(item.get("reason"), str) else ""
        clean = {
            "section": sec,
            "key": key,
            "value": item.get("value"),
            "reason": item.get("reason") if isinstance(item.get("reason"), str) else "",
        }
        args = (
            root,
            clean,
            now,
            traj,
            rev_weight,
            item.get("window_h"),
            expect[:500],
            categories,
            validate,
        )
        return _start_split(*args) if split else _start_sequential(*args)
    except Exception as e:  # noqa: BLE001 - a hostile item is a refusal, never a crashed wake
        return _err(f"{type(e).__name__}: {e}")


def _ok(exp, r):
    out = {
        "ok": True,
        "experiment": exp["id"],
        "mode": exp["mode"],
        "section": exp["section"],
        "key": exp["key"],
        "old": exp["old"],
        "new": exp["new"],
        "eval_ts": exp["eval_ts"],
    }
    if r.get("note"):
        out["note"] = r["note"]
    return out


def _start_sequential(
    root, clean, now, traj, rev_weight, window_h, expect, categories, validate
):
    sec, key = clean["section"], clean["key"]
    try:
        pol = c.load_json(pe.path(root), None)
    except ValueError:
        pol = None
    present, value = pe.raw_state(pol, sec, key)
    raw = {"base": key.split(".", 1)[0], "present": present, "value": value}
    def undo(why):
        try:
            now_state = pe.raw_state(c.load_json(pe.path(root), None), sec, key)
        except Exception:  # noqa: BLE001
            now_state = None
        if now_state == (present, value):
            return "값은 그대로임"
        back = pe.kernel_restore(
            root, sec, raw["base"], present, value, traj, now, why, tag=pe.kernel_tag(now), key=key,
            validate=validate,
        )
        return "값은 원래대로 되돌림" if back.get("ok") else f"되돌리기도 실패: {back.get('error')}"

    try:
        eid = _next_id(load(root))
        r = pe.apply(root, [clean], traj, now, categories=categories, validate=validate)[0]
    except Exception as e:  # noqa: BLE001 - a write that failed half way must not leave an unregistered edit
        return _err(f"수정 실패 ({type(e).__name__}: {e}); " + undo(f"수정 실패로 되돌림 ({type(e).__name__})"))
    if not r.get("ok"):
        return _err(r.get("error"))
    try:
        exp = register(
            root,
            r,
            expect,
            window_h,
            now,
            traj,
            rev_weight,
            mode="sequential",
            raw=raw,
            eid=eid,
        )
    except Exception as e:  # noqa: BLE001 - an unregistered edit must not stay in the file
        _drop_entry(root, eid)
        back = pe.kernel_restore(
            root,
            sec,
            raw["base"],
            present,
            value,
            traj,
            now,
            f"실험 등록 실패로 되돌림 ({type(e).__name__})",
            tag=pe.kernel_tag(now, eid),
            key=key,
            validate=validate,
        )
        return _err(
            f"실험 등록 실패 ({type(e).__name__}: {e}); "
            + (
                "값은 원래대로 되돌림"
                if back.get("ok")
                else f"되돌리기도 실패: {back.get('error')}"
            )
        )
    return _ok(exp, r)


def _start_split(
    root, clean, now, traj, rev_weight, window_h, expect, categories, validate
):
    sec, key = clean["section"], clean["key"]
    r = pe.check(root, clean, traj, now, categories=categories, validate=validate)
    if not r.get("ok"):
        return _err(r.get("error"))
    try:
        arms = sync_arms(root)
    except Exception as e:  # noqa: BLE001
        return _err(f"비교 팔 파일을 맞추지 못함 ({type(e).__name__}: {e})")
    if len(arms) >= ARMS_MAX:
        return _err(f"동시 비교는 {ARMS_MAX}개까지 (지금 {len(arms)}개)")
    pol = c.load_json(pe.path(root), None)
    path, value = arm_target(pol, sec, key, r["new"])
    eid = _next_id(load(root))
    wh = _window(window_h, "shop", key)
    until = now + (wh + MATURITY_H["shop"]) * 3600 + ARM_GRACE_S
    arm = {"id": eid, "path": path, "value": value, "share": ARM_SHARE, "until": until}
    _write_arms(root, arms + [arm])
    try:
        exp = register(
            root,
            r,
            expect,
            wh,
            now,
            traj,
            rev_weight,
            mode="split",
            eid=eid,
            arm={k: arm[k] for k in ("path", "value", "share", "until")},
        )
        # the cooldown of policy_edit.recent() sees this row, so a split start starts the key's cooldown
        traj.append(
            "operator",
            "policy_edit",
            section=sec,
            key=key,
            old=r["old"],
            new=r["new"],
            reason=clean["reason"][:500],
            by="split_start",
            experiment=eid,
        )
    except Exception as e:  # noqa: BLE001 - no arm without its ledger entry
        _drop_entry(root, eid)
        try:
            _remove_arm(root, eid)
        except Exception as e2:  # noqa: BLE001
            return _err(
                f"실험 등록 실패 ({type(e).__name__}: {e}); 비교 팔 제거도 실패 ({e2}), {until}까지 남음"
            )
        return _err(f"실험 등록 실패 ({type(e).__name__}: {e}); 비교 팔은 지움")
    return _ok(exp, r)


# --- events and metrics


def _kind_of(item, d, category):
    k = (item or {}).get("kind") or d.get("kind")
    return k if k in ("food", "goods") else pe._kind(category or "")


def _buyer_deal(e, d, cat):
    """One of our buyer's purchases from a market settle row, or None. Shape confirmed on live data (11-10, data keys
    amount, buyer, category, grade, kind, list_price (sometimes missing), order, shop, sku, warranty_hours), as attempt 1
    ledger.py _market_entry/norm_settle writes it: type "settle", src "market", role "market" (not "buyer"), data.own_owner True and our
    login in data.buyer (common.settle_side), data.amount = price paid, data.list_price when the market sends it, else
    the catalog list price of data.sku. A deal with no list price cannot be valued and is skipped."""
    lp = d.get("list_price")
    lp = (
        lp
        if isinstance(lp, (int, float)) and not isinstance(lp, bool) and lp > 0
        else brief._list_price(cat, d.get("sku"))
    )
    if not lp or not isinstance(d.get("amount"), (int, float)):
        return None
    item = cat.get(d.get("sku")) or {}
    return {
        "ts": e["ts"],
        "amount": float(d["amount"]),
        "lp": float(lp),
        "kind": _kind_of(item, d, item.get("category") or d.get("category")),
    }


def read_events(root, lo, hi, login=None, cat=None, now=None):
    """Rows with ts in [lo, hi) from .r3/events.jsonl, joined as brief.net_profit_data joins them, but for any window:
    -> {"sales": [our shop sales with cost (the settle's own unit_cost, else the joined restock/unit_gone cost), leaf,
    kind, order, unit, warranty hours, broke_at], "market": [(ts, amount, leaf, kind) of every market settle],
    "others": [(ts, kind) of every market settle that is not our buyer's purchase], "deals": [our buyer's purchases],
    "reviews": {order: stars}, "review_ts": {order: ts}, "earliest": ts of the earliest row read (None when none)}.
    The file tail read covers [lo, max(hi, now)); "earliest" tells a caller whether it really reached back to lo."""
    login = c.owner_login(root) if login is None else login
    cat = brief._catalog(root) if cat is None else cat
    path = os.path.join(root, ".r3/events.jsonl")
    top = hi if now is None or not _num_ok(now) else max(hi, now)
    nb = int(brief.NET_BYTES_PER_H * (max(0.0, top - lo) / 3600 + 2))
    inside = lambda e: isinstance(e.get("ts"), (int, float)) and lo <= e["ts"] < hi
    earliest = [None]

    def rows(needle):
        for e in c.tail_json(path, nb, needle):
            if not isinstance(e, dict):
                continue
            t = e.get("ts")
            if _num_ok(t) and (earliest[0] is None or t < earliest[0]):
                earliest[0] = t
            yield e

    cost = {}
    for needle, mine in (
        (b'"restock"', True),
        (b'"unit_gone"', False),
    ):  # unit_gone wins, as in net_profit_data
        for e in rows(needle):
            d = e.get("data") or {}
            if (
                e.get("type") == needle.strip(b'"').decode()
                and (e.get("mine") or not mine)
                and d.get("unit")
                and isinstance(d.get("cost"), (int, float))
            ):
                cost[d["unit"]] = (float(d["cost"]), d.get("category"))
    sales, market, others, deals, by_key = [], [], [], [], defaultdict(list)
    for e in rows(b'"settle"'):
        d = e.get("data") or {}
        if e.get("type") != "settle" or e.get("src") != "market" or not inside(e):
            continue
        item = cat.get(d.get("sku")) or {}
        u = cost.get(d.get("unit"))
        path_ = item.get("category") or d.get("category") or (u[1] if u else None)
        leaf, kind, a = (
            brief._leaf(path_),
            _kind_of(item, d, path_),
            float(d.get("amount") or 0),
        )
        market.append((e["ts"], a, leaf, kind))
        side = c.settle_side(d, login)
        if side == "buyer":
            x = _buyer_deal(e, d, cat)
            if x:
                deals.append(x)
        else:
            others.append((e["ts"], kind))
        if side == "shop":
            uc = d.get("unit_cost")
            s = {
                "ts": e["ts"],
                "amount": a,
                "cost": float(uc) if _num_ok(uc) else (u[0] if u else None),
                "leaf": leaf,
                "kind": kind,
                "order": d.get("order"),
                "unit": d.get("unit"),
                "wh": d.get("warranty_hours") or 0,
                "broke_at": None,
            }
            sales.append(s)
            by_key[(json.dumps(d.get("buyer")), d.get("sku"))].append(s)
    breaks = []
    for e in rows(b'"broken"'):
        d = e.get("data") or {}
        if e.get("type") == "broken" and e.get("src") == "market" and e.get("mine"):
            at = (
                d.get("broke_at")
                if isinstance(d.get("broke_at"), (int, float))
                else e.get("ts")
            )
            if isinstance(at, (int, float)) and lo <= at < hi:
                breaks.append((at, json.dumps(d.get("buyer")), d.get("sku")))
    for at, buyer, sku in sorted(breaks):
        s = max(
            (
                x
                for x in by_key.get((buyer, sku), ())
                if x["ts"] <= at and x["broke_at"] is None
            ),
            key=lambda x: x["ts"],
            default=None,
        )
        if s is not None:
            s["broke_at"] = at
    reviews, review_ts = {}, {}
    for e in rows(b'"review"'):
        d = e.get("data") or {}
        if (
            e.get("type") == "review"
            and e.get("role") == "shop"
            and inside(e)
            and d.get("order")
            and isinstance(d.get("stars"), (int, float))
            and not isinstance(d.get("stars"), bool)
        ):
            reviews[d["order"]] = d["stars"]
            review_ts[d["order"]] = e["ts"]
    return {
        "sales": sales,
        "market": market,
        "others": others,
        "deals": deals,
        "reviews": reviews,
        "review_ts": review_ts,
        "earliest": earliest[0],
    }


def _in_scope(scope, leaf, kind):
    if scope == "shop:all":
        return True
    if scope.startswith(("kind:", "buyer:")):
        return kind == scope.split(":", 1)[1]
    return leaf == scope


def _buckets(t0, t1):
    n = max(1, int(round((t1 - t0) / 3600)))
    return n, (lambda ts: min(n - 1, int((ts - t0) // 3600)))


def refund(s):
    """The refund a sale counts in the metric: its amount when it broke within MATURITY_H["shop"] hours of the sale
    and within its warranty (+brief.NET_WARRANTY_SLACK_S, the net_profit_data rule), else 0."""
    if s["broke_at"] is None or not s["wh"]:
        return 0.0
    dt = s["broke_at"] - s["ts"]
    return (
        s["amount"]
        if dt <= MATURITY_H["shop"] * 3600
        and dt <= s["wh"] * 3600 + brief.NET_WARRANTY_SLACK_S
        else 0.0
    )


def _net(s):
    """Gross (amount - unit cost, 0 without a known cost) minus the matured refund."""
    return (s["amount"] - s["cost"] if s["cost"] is not None else 0.0) - refund(s)


def _stars(data, s):
    """The sale's review stars when the review arrived within MATURITY_H["shop"] hours of the sale, else None (a review
    without a recorded time, as in hand-built data, counts)."""
    o = s["order"]
    if o not in data["reviews"]:
        return None
    t = (data.get("review_ts") or {}).get(o)
    if t is not None and t - s["ts"] > MATURITY_H["shop"] * 3600:
        return None
    return data["reviews"][o]


def _empty_shop():
    return {
        "value": 0.0,
        "net": 0.0,
        "revenue": 0.0,
        "n": 0,
        "n_cost": 0,
        "cost_known": None,
        "market": 0.0,
        "stars": None,
        "n_reviews": 0,
        "buckets": [],
        "hours": 0.0,
    }


def shop_metric(data, scope, t0, t1, rev_weight=0.0):
    """Over our sales settled in [t0, t1) within scope: net = gross (amount - unit cost, 0 without a known cost) minus
    matured refunds, revenue = sum of amounts, both per hour; value = net + rev_weight x revenue (net alone when 0).
    With n, the share of sales with a known cost, market revenue in scope, matured review stars and hourly buckets."""
    if t1 <= t0:
        return _empty_shop()
    n_b, idx = _buckets(t0, t1)
    hours, buckets, n, n_cost, stars = (t1 - t0) / 3600, [0.0] * n_b, 0, 0, []
    net = rev = 0.0
    for s in data["sales"]:
        if t0 <= s["ts"] < t1 and _in_scope(scope, s["leaf"], s["kind"]):
            n += 1
            n_cost += s["cost"] is not None
            x = _net(s)
            net, rev = net + x, rev + s["amount"]
            buckets[idx(s["ts"])] += x + rev_weight * s["amount"]
            r = _stars(data, s)
            if r is not None:
                stars.append(r)
    mkt = sum(
        a
        for ts, a, leaf, kind in data["market"]
        if t0 <= ts < t1 and _in_scope(scope, leaf, kind)
    )
    return {
        "value": sum(buckets) / hours,
        "net": net / hours,
        "revenue": rev / hours,
        "n": n,
        "n_cost": n_cost,
        "cost_known": n_cost / n if n else None,
        "market": mkt,
        "stars": st.mean(stars) if stars else None,
        "n_reviews": len(stars),
        "buckets": buckets,
        "hours": hours,
    }


def buyer_metric(data, scope, t0, t1, s0):
    """saved_gain_per_h over our buyer's purchases settled in [t0, t1) of the scope's kind: sum of (surplus_frac - s0)
    per hour, surplus_frac = (list - paid) / list. The board's saved is sum(surplus_frac) / (deals + 100), so a deal
    raises it only when its surplus_frac is above the current value s0. "others": the other buyers' deals of the
    same kind in the window (market settles that are not our purchases)."""
    if t1 <= t0:
        return {"value": 0.0, "n": 0, "buckets": [], "others": 0, "hours": 0.0}
    n_b, idx = _buckets(t0, t1)
    hours, buckets, n, s0 = (t1 - t0) / 3600, [0.0] * n_b, 0, s0 or 0.0
    for x in data["deals"]:
        if t0 <= x["ts"] < t1 and _in_scope(scope, None, x["kind"]):
            n += 1
            buckets[idx(x["ts"])] += (x["lp"] - x["amount"]) / x["lp"] - s0
    others = sum(
        1
        for ts, kind in data.get("others") or ()
        if t0 <= ts < t1 and _in_scope(scope, None, kind)
    )
    return {
        "value": sum(buckets) / hours,
        "n": n,
        "buckets": buckets,
        "others": others,
        "hours": hours,
    }


def _ratio(a, ah, b, bh):
    """Rate ratio (a / ah) / (b / bh) clamped to R_CLAMP; 1 when either count is 0."""
    if not a or not b or not ah or not bh:
        return 1.0
    return min(R_CLAMP[1], max(R_CLAMP[0], (a / ah) / (b / bh)))


def evaluate(root, exp, now, data=None):
    """Sequential verdict as of now (no writes): the window_h hours after started_ts against the window_h hours before
    it, the before window clipped to start no earlier than the ledger's before_from. Events are read only up to the
    after window's end + maturity, so a late call gives the same verdict as an on-time one. -> dict with every number
    used and decision better | worse | thin | unclear | no_baseline and its reason."""
    side, scope, wh, t = exp["side"], exp["scope"], exp["window_h"], exp["started_ts"]
    w = wh * 3600
    mat = exp.get("maturity_h", MATURITY_H[side]) * 3600
    bf = exp.get("before_from")
    b0 = max(t - w, bf) if _num_ok(bf) else t - w
    b0 = min(b0, t)
    if data is None:
        data = read_events(root, b0 - JOIN_LOOKBACK_H * 3600, t + w + mat, now=now)
    if side == "buyer":
        b, a = (
            buyer_metric(data, scope, b0, t, exp.get("s0")),
            buyer_metric(data, scope, t, t + w, exp.get("s0")),
        )
        r = _ratio(a["others"], a["hours"], b["others"], b["hours"])
    else:
        rw = exp.get("rev_weight") or 0.0
        b, a = (
            shop_metric(data, scope, b0, t, rw),
            shop_metric(data, scope, t, t + w, rw),
        )
        r = _ratio(a["market"], a["hours"], b["market"], b["hours"])
    before_adj = b["value"] * r
    effect = a["value"] - before_adj
    ab, bb = a["buckets"], [x * r for x in b["buckets"]]
    se = (
        math.sqrt(st.variance(ab) / len(ab) + st.variance(bb) / len(bb))
        if len(ab) >= 2 and len(bb) >= 2
        else None
    )
    earliest = data.get(
        "earliest", b0
    )  # hand-built data without the key counts as covered
    baseline = (
        (t - b0) >= MIN_BEFORE_H * 3600
        and earliest is not None
        and earliest <= b0 + BASELINE_SLACK_S
    )
    v = {
        "metric": exp["metric"],
        "window_h": wh,
        "before_start": b0,
        "before_h": (t - b0) / 3600,
        "baseline": baseline,
        "before": b["value"],
        "before_adj": before_adj,
        "after": a["value"],
        "r": r,
        "effect": effect,
        "se": se,
        "n_before": b["n"],
        "n_after": a["n"],
    }
    if side == "buyer":
        v.update(s0=exp.get("s0"), others_before=b["others"], others_after=a["others"])
    else:
        v.update(
            rev_weight=exp.get("rev_weight") or 0.0,
            net_before=b["net"],
            net_after=a["net"],
            revenue_before=b["revenue"],
            revenue_after=a["revenue"],
            market_before=b["market"],
            market_after=a["market"],
            stars_before=b["stars"],
            stars_after=a["stars"],
            n_reviews_before=b["n_reviews"],
            n_reviews_after=a["n_reviews"],
            cost_known_before=b["cost_known"],
            cost_known_after=a["cost_known"],
        )
    cost_thin = side == "shop" and any(
        m["n"] >= COST_N_MIN and m["cost_known"] < COST_KNOWN_MIN for m in (b, a)
    )
    if not baseline:
        v.update(decision="no_baseline", reason="no_baseline")
    elif (
        side == "shop"
        and b["n_reviews"] >= STARS_MIN_N
        and a["n_reviews"] >= STARS_MIN_N
        and a["stars"] < b["stars"] - STARS_DROP
    ):
        v.update(decision="worse", reason="stars")
    elif cost_thin:
        v.update(decision="thin", reason="cost_unknown")
    elif a["n"] < THIN_N:
        v.update(decision="thin", reason="thin")
    elif (
        se is not None
        and abs(effect) > CLEAR_SE * se
        and abs(effect) > REL_MIN * max(abs(before_adj), FLOOR[side])
    ):
        v.update(decision="better" if effect > 0 else "worse", reason="clear")
    else:
        v.update(decision="unclear", reason="noise")
    return v


def _arm_stats():
    return {
        "buckets": None,
        "n": 0,
        "n_cost": 0,
        "stars": [],
        "net": 0.0,
        "revenue": 0.0,
        "sq": 0.0,
    }


def evaluate_split(root, exp, now, data=None):
    """Split verdict as of now (no writes): our sales in scope settled in [started_ts, started_ts + window), each put
    in arm a or b by arm_b(experiment id, settle unit). Per arm: the shop metric value per hour, n, matured stars;
    effect = mean of the hourly differences b - a, se = their sd / sqrt(hours). Events are read only up to the window
    end + maturity. -> dict with decision better | worse | thin | unclear and its reason."""
    t, wh = exp["started_ts"], exp["window_h"]
    w, mat = wh * 3600, exp.get("maturity_h", MATURITY_H["shop"]) * 3600
    if data is None:
        data = read_events(root, t - JOIN_LOOKBACK_H * 3600, t + w + mat, now=now)
    rw = exp.get("rev_weight") or 0.0
    share = (exp.get("arm") or {}).get("share", ARM_SHARE)
    n_b, idx = _buckets(t, t + w)
    hours = w / 3600
    arm = {"a": _arm_stats(), "b": _arm_stats()}
    for x in arm.values():
        x["buckets"] = [0.0] * n_b
    no_unit = 0
    for s in data["sales"]:
        if not (t <= s["ts"] < t + w and _in_scope(exp["scope"], s["leaf"], s["kind"])):
            continue
        u = s.get("unit")
        if u is None or u == "":
            no_unit += 1
            continue
        x = arm["b" if arm_b(exp["id"], u, share) else "a"]
        x["n"] += 1
        x["n_cost"] += s["cost"] is not None
        g = _net(s)
        x["net"] += g
        x["revenue"] += s["amount"]
        x["buckets"][idx(s["ts"])] += g + rw * s["amount"]
        x["sq"] += (g + rw * s["amount"]) ** 2
        r = _stars(data, s)
        if r is not None:
            x["stars"].append(r)
    A, B = arm["a"], arm["b"]
    d = [bb - aa for aa, bb in zip(A["buckets"], B["buckets"])]
    effect = st.mean(d)
    # se of the random assignment: with no true difference each sale lands in a or b by the unit hash, so the
    # variance of (sum b - sum a) is the sum of squared sale values. Three hourly buckets gave an sd too noisy to trust.
    se = math.sqrt(A["sq"] + B["sq"]) / hours if A["n"] + B["n"] >= 2 else None
    va, vb = sum(A["buckets"]) / hours, sum(B["buckets"]) / hours
    total = A["n"] + B["n"]
    ck = lambda x: x["n_cost"] / x["n"] if x["n"] else None
    sm = lambda x: st.mean(x["stars"]) if x["stars"] else None
    v = {
        "metric": exp.get("metric"),
        "window_h": wh,
        "a": va,
        "b": vb,
        "effect": effect,
        "se": se,
        "hours": len(d),
        "n_a": A["n"],
        "n_b": B["n"],
        "no_unit": no_unit,
        "rev_weight": rw,
        "net_a": A["net"] / hours,
        "net_b": B["net"] / hours,
        "revenue_a": A["revenue"] / hours,
        "revenue_b": B["revenue"] / hours,
        "stars_a": sm(A),
        "stars_b": sm(B),
        "n_reviews_a": len(A["stars"]),
        "n_reviews_b": len(B["stars"]),
        "cost_known_a": ck(A),
        "cost_known_b": ck(B),
    }
    if (
        len(A["stars"]) >= STARS_MIN_N
        and len(B["stars"]) >= STARS_MIN_N
        and v["stars_b"] < v["stars_a"] - STARS_DROP
    ):
        v.update(decision="worse", reason="stars")
    elif no_unit and no_unit > NO_UNIT_MAX * (total + no_unit):
        v.update(decision="thin", reason="no_unit")
    elif any(x["n"] >= COST_N_MIN and ck(x) < COST_KNOWN_MIN for x in (A, B)):
        v.update(decision="thin", reason="cost_unknown")
    elif total < SPLIT_THIN_N or A["n"] < THIN_N or B["n"] < THIN_N:
        v.update(decision="thin", reason="thin")
    elif (
        se is not None
        and abs(effect) > CLEAR_SE * se
        and abs(effect) > REL_MIN * max(abs(va), FLOOR["shop"])
    ):
        v.update(decision="better" if effect > 0 else "worse", reason="clear")
    else:
        v.update(decision="unclear", reason="noise")
    return v


# --- tick


def _same(a, b):
    if (
        isinstance(a, (int, float))
        and isinstance(b, (int, float))
        and not isinstance(a, bool)
        and not isinstance(b, bool)
    ):
        return abs(a - b) < 1e-9
    return a == b


def _end(exp, status, v, now, traj):
    exp.update(status=status, verdict=v, ended_ts=now)
    exp.pop("last_error", None)
    traj.append(
        "operator",
        "experiment_end",
        id=exp.get("id"),
        mode=exp.get("mode", "sequential"),
        section=exp.get("section"),
        key=exp.get("key"),
        old=exp.get("old"),
        new=exp.get("new"),
        status=status,
        verdict=v,
        **({"role": exp["role"]} if exp.get("role") in ROLES else {}),
    )


def _extend(exp, v, traj):
    exp.update(
        window_h=min(MAX_WINDOW_H, exp["window_h"] * 2),
        extended=True,
        first_verdict=v,
    )
    exp["eval_ts"] = _eval_ts(exp)
    exp.pop("last_error", None)
    traj.append(
        "operator",
        "experiment_extend",
        id=exp["id"],
        section=exp["section"],
        key=exp["key"],
        window_h=exp["window_h"],
        eval_ts=exp["eval_ts"],
        verdict=v,
    )


def _policy(root):
    try:
        pol = c.load_json(pe.path(root), None)
    except ValueError:
        pol = None
    if not isinstance(pol, dict):
        raise ValueError("policy.json unreadable")
    return pol


def _step_sequential(root, exp, now, traj, categories, validate):
    """Decide one due sequential experiment. -> True when it ended or was extended; raises when the revert failed."""
    cur = pe.effective(_policy(root), exp["section"], exp["key"])
    if not _same(cur, exp["new"]):
        _end(
            exp,
            "voided",
            {"decision": "voided", "reason": "value_changed", "value_now": cur},
            now,
            traj,
        )
        return True
    v = evaluate(root, exp, now)
    if v["decision"] in ("better", "worse"):
        v["final"] = "decision"
    elif not exp.get("extended"):
        _extend(exp, v, traj)
        return True
    elif v["decision"] == "no_baseline":
        v.update(final="no_baseline")  # nothing to compare against: the value stays
    elif v["reason"] == "cost_unknown":
        v.update(
            final="cost_unknown"
        )  # profit of sales with unknown cost is not judged: the value stays
    elif v["n_before"] == 0 and v["n_after"] == 0:
        v.update(final="no_activity")
    else:
        v.update(final="point_estimate")
    keep = v["decision"] == "better" or (
        v["decision"] != "worse"
        and (
            v["final"] in ("no_activity", "no_baseline", "cost_unknown")
            or v["effect"] >= 0
        )
    )
    if keep:
        _end(exp, "kept", v, now, traj)
        return True
    why = (
        f"실험 #{exp['id']} 판정 {v['decision']} ({v['reason']}): {exp['old']}로 되돌림"
    )
    tag = pe.kernel_tag(now, exp["id"])
    raw = exp.get("raw")
    if isinstance(raw, dict) and "base" in raw:
        res = pe.kernel_restore(
            root,
            exp["section"],
            raw["base"],
            raw.get("present"),
            raw.get("value"),
            traj,
            now,
            why,
            tag=tag,
            key=exp["key"],
            validate=validate,
        )
    else:  # entries registered before raw states were kept
        res = pe.kernel_set(
            root,
            exp["section"],
            exp["key"],
            exp["old"],
            traj,
            now,
            why,
            categories,
            validate,
            tag=tag,
        )
    if not res.get("ok"):
        raise RuntimeError(f"revert failed: {res.get('error')}")
    _end(exp, "reverted", v, now, traj)
    return True


def _step_split(root, exp, now, traj, categories, validate):
    """Decide one due split experiment. -> True when it ended or was extended; raises when a write failed."""
    eid = exp["id"]
    cur = pe.effective(_policy(root), exp["section"], exp["key"])
    if not _same(cur, exp["old"]):
        _remove_arm(root, eid)
        _end(
            exp,
            "voided",
            {"decision": "voided", "reason": "value_changed", "value_now": cur},
            now,
            traj,
        )
        return True
    v = evaluate_split(root, exp, now)
    arm = exp.get("arm") or {}
    if v["decision"] in ("better", "worse"):
        v["final"] = "decision"
        adopt = v["decision"] == "better"
    elif (
        not exp.get("extended")
        and exp["window_h"] < MAX_WINDOW_H
        and now < (arm.get("until") or 0)
    ):
        _extend(exp, v, traj)
        until = exp["eval_ts"] + ARM_GRACE_S
        _set_arm_until(root, eid, until)
        exp["arm"] = dict(arm, until=until)
        return True
    else:
        # weak final: b only when its point estimate beats a by more than one se; the status quo wins a tie
        v["final"] = "point_estimate"
        adopt = (
            v["reason"] not in ("no_unit", "cost_unknown", "thin")
            and v["n_a"] >= THIN_N
            and v["n_b"] >= THIN_N
            and v["se"] is not None
            and v["effect"] > 0
            and v["effect"] > v["se"]
        )
    v["adopt"] = adopt
    _remove_arm(root, eid)
    if not adopt:
        _end(exp, "dropped", v, now, traj)
        return True
    res = pe.kernel_set(
        root,
        exp["section"],
        exp["key"],
        exp["new"],
        traj,
        now,
        f"실험 #{eid} 동시 비교 채택 ({v['reason']}): {exp['old']}→{exp['new']}",
        categories,
        validate,
        tag=pe.kernel_tag(now, eid),
    )
    if not res.get("ok"):
        raise RuntimeError(f"adopt failed: {res.get('error')}")
    _end(exp, "adopted", v, now, traj)
    return True


def _void_error(root, exp, now, traj):
    if exp.get("mode") == "split":
        try:
            _remove_arm(root, exp.get("id"))
        except Exception:  # noqa: BLE001 - the arm runs out at its until anyway
            pass
    _end(
        exp,
        "voided",
        {
            "decision": "voided",
            "reason": "eval_error",
            "error": exp.get("last_error"),
            "fails": exp.get("fails"),
            "note": "값은 그대로 둠",
        },
        now,
        traj,
    )


def tick(root, now, traj, categories=(), validate=None):
    """Decide every running experiment whose eval_ts has come. Never raises: a failure is kept as last_error on the
    experiment and retried after RETRY_S; after MAX_FAILS failures the experiment ends as voided (eval_error) with the
    value left as it is. -> the experiments that ended or were extended."""
    try:
        rows = load(root)
    except Exception:  # noqa: BLE001 - an unreadable ledger must not stop the operator wake
        return []
    out, dirty = [], False
    try:
        if isinstance(rows, list):
            sync_arms(root, rows)
    except Exception:  # noqa: BLE001
        pass
    for exp in rows:
        try:
            if not isinstance(exp, dict) or exp.get("status") != "running":
                continue
            if (
                exp.get("last_try_ts") is not None
                and now - exp["last_try_ts"] < RETRY_S
            ):
                continue
            if now < exp["eval_ts"]:
                continue
            dirty = True
            step = _step_split if exp.get("mode") == "split" else _step_sequential
            if step(root, exp, now, traj, categories, validate):
                out.append(exp)
        except Exception as e:  # noqa: BLE001 - one bad entry must not stop the others
            if isinstance(exp, dict):
                n = exp.get("fails")
                n = n if isinstance(n, int) and not isinstance(n, bool) else 0
                exp.update(
                    last_error=f"{type(e).__name__}: {e}"[:300],
                    last_try_ts=now,
                    fails=n + 1,
                )
                dirty = True
                if n + 1 >= MAX_FAILS:
                    try:
                        _void_error(root, exp, now, traj)
                        out.append(exp)
                    except Exception:  # noqa: BLE001
                        pass
    if dirty:
        try:
            save(root, rows)
        except Exception:  # noqa: BLE001
            pass
    return out


# --- briefing


def _num(v, side, rate=True, sign=False):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "-"
    p = "+" if sign and v > 0 else ""
    if side != "shop":
        return p + f"{v:.3f}" + ("/h" if rate else "")
    return (
        p
        + (f"{v / 1e6:.2f}M" if abs(v) >= 1e5 else f"{v:,.0f}")
        + ("/h" if rate else "")
    )


def _val(x):
    return f"{x:.4g}" if isinstance(x, float) else str(x)


def _label(section):
    short = {
        "defaults.food": "food",
        "defaults.goods": "goods",
        "buyer.defaults.food": "buyer.food",
        "buyer.defaults.goods": "buyer.goods",
    }
    return short.get(section) or brief._leaf(section)[-12:]


def _reason(v):
    f = v.get("final")
    return REASON_KO.get(f if f not in (None, "decision") else v.get("reason"), "")


def _run_line(e):
    exp = str(e.get("expect") or "")
    exp = exp[:EXPECT_CHARS] + ("…" if len(exp) > EXPECT_CHARS else "")
    when = c.kst(e["eval_ts"], "%H:%M") if _num_ok(e.get("eval_ts")) else "-"
    if e.get("mode") == "split":
        head = f"  진행 #{e['id']} 동시 비교 {_label(e.get('section'))} {e.get('key')} A {_val(e.get('old'))} / B {_val(e.get('new'))}"
    else:
        head = f"  진행 #{e['id']} {_label(e.get('section'))} {e.get('key')} {_val(e.get('old'))}→{_val(e.get('new'))}"
    return (
        head
        + f", 판정 {when}"
        + (" (연장)" if e.get("extended") else "")
        + (f", 오류 {str(e['last_error'])[:50]}" if e.get("last_error") else "")
        + (f", 가설: {exp}" if exp else "")
    )


def _ended_line(e):
    v, side = e.get("verdict") or {}, e.get("side")
    head = f"  #{e['id']} {e.get('key')} {_val(e.get('old'))}→{_val(e.get('new'))} {STATUS_KO.get(e['status'], e['status'])}"
    if e["status"] == "voided":
        return head + (f": {_reason(v)}" if _reason(v) else "")
    reason = _reason(v)
    if e.get("mode") == "split":
        se = f", se {_num(v['se'], 'shop', False)}" if v.get("se") is not None else ""
        return (
            head
            + f": 효과 {_num(v.get('effect'), 'shop', sign=True)} (A {_num(v.get('a'), 'shop')}, B {_num(v.get('b'), 'shop')}"
            + f"{se}, 판매 A {v.get('n_a', '-')} / B {v.get('n_b', '-')})"
            + (f", {reason}" if reason else "")
        )
    return (
        head
        + f": 효과 {_num(v.get('effect'), side, sign=True)} (전 보정 {_num(v.get('before_adj'), side)}, "
        f"후 {_num(v.get('after'), side)}"
        + (f", se {_num(v['se'], side, False)}" if v.get("se") is not None else "")
        + f", {'거래' if side == 'buyer' else '판매'} {v.get('n_before', '-')}→{v.get('n_after', '-')})"
        + (
            f", 순이익 {_num(v.get('net_before'), side)}→{_num(v.get('net_after'), side)}"
            if side == "shop" and v.get("rev_weight")
            else ""
        )
        + (f", {reason}" if reason else "")
    )


def lines(root, now, last=LINES_ENDED, role=None):
    """[실험] briefing lines: counts, up to LINES_RUN running experiments and the last `last` ended ones, one compact
    line each with its mode. With a role, the other role's lines end with OTHER."""

    def mark(e):
        return OTHER if role and role_of(e) != role else ""
    rows = [e for e in load(root) if isinstance(e, dict) and "id" in e]
    run = [e for e in rows if e.get("status") == "running"]
    ended = sorted(
        (
            e
            for e in rows
            if e.get("status") in STATUS_KO and e.get("status") != "running"
        ),
        key=lambda e: e.get("ended_ts") if _num_ok(e.get("ended_ts")) else 0,
    )[-last:]
    n = lambda s: sum(1 for e in ended if e["status"] == s)
    head = f"[실험] 진행 {len(run)}건, 최근 판정 {len(ended)}건 중 유지 {n('kept')}, 되돌림 {n('reverted')}"
    if n("adopted") or n("dropped"):
        head += f", 채택 {n('adopted')}, 버림 {n('dropped')}"
    out = [head]
    for e in run[:LINES_RUN]:
        try:
            out.append(_run_line(e) + mark(e))
        except Exception:  # noqa: BLE001 - a malformed entry is shown by id only
            out.append(f"  진행 #{e.get('id')}")
    if len(run) > LINES_RUN:
        out.append(f"  … 진행 {len(run) - LINES_RUN}건 더")
    for e in reversed(ended):
        try:
            out.append(_ended_line(e) + mark(e))
        except Exception:  # noqa: BLE001
            out.append(f"  #{e.get('id')} {STATUS_KO.get(e.get('status'), '')}")
    return out


def summary(root, now, hours=24):
    """Counts by status of experiments ended in the last `hours` (running: all now running) and the hit rate
    (kept + adopted) / (kept + reverted + adopted + dropped), None when none of those happened."""
    rows = [e for e in load(root) if isinstance(e, dict)]
    since = now - hours * 3600
    out = {s: 0 for s in STATUS_KO}
    for e in rows:
        s = e.get("status")
        t = e.get("ended_ts")
        if s == "running" or (s in out and _num_ok(t) and t >= since):
            out[s] += 1
    good = out["kept"] + out["adopted"]
    judged = good + out["reverted"] + out["dropped"]
    return {
        "hours": hours,
        "counts": out,
        "hit_rate": good / judged if judged else None,
    }
