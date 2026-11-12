"""Experiment ledger and verdicts for operator policy edits (human code, not editable by the model).

DESIGN 14.4 step 1. apply_policy() (and start(), its one-item wrapper) checks, writes and registers as
one step and never raises. Since 11-12 (성수님: agents decide all strategy) a verdict never changes a policy value:
it reports its numbers and ends the experiment as "measured"; the operator decides what to write. Modes:

- sequential: the edit goes into policy.json and the experiment compares the metric over the window_h hours after it
  with the window_h hours before it (shop side: the before value scaled by the market's revenue rate in the same
  scope; buyer side: by the other buyers' deal rate), then reports the verdict (the value stays) or extends the
  window once.
- split (v2.12 arms): for sale-side keys the seller reads after the unit pick (SPLIT_KEYS), policy.json keeps the old
  value and <root>/policy_arms.json gives the new value to the sales whose unit id hashes into arm "b" (attempt 1
  policy.arm_pick, reimplemented here as arm_b). The verdict compares the two arms over the same hours, so no before
  window and no market adjustment; at the verdict the arm is removed and policy.json keeps the old value.
- group (11-11): the before/after items of one done with the same "group" label, judged as one sequential experiment
  on the role's whole side (scope "shop:all" or "buyer:all"); the verdict is reported, every item's value stays.
  Ledger entries of mode "bet" (the same thing under its first name) are judged the same way.
Until 11-12 a verdict put the old value back (reverted), kept it, or adopted or dropped a split's new value; those
statuses stay in the ledger as history.

Since 11-11 (성수님: the strategy, including how to measure, belongs to the operator) apply_policy takes a whole
done.policy list: each item is applied directly (no experiment) unless it asks for a test, everything is written in one
write, and a change ends the running experiments of its role whose own value it writes (voided, "overwritten"); one
that only touches an experiment's scope is named on that experiment's verdict (mixed_by). Nothing is
refused because an experiment runs or recently ended. Entries started before end report-only too.

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
MIN_WINDOW_H, MAX_WINDOW_H = 1, 48
EXT_MAX_WINDOW_H = 8  # the kernel's own extension of an undecided window stops here
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
    600  # a failed evaluation is retried at most once per 10 min
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
# a new before/after experiment that starts less than this after an overlapping one ended keeps its full before window
# and names that experiment on its verdict (until 11-11 such a start was refused)
CONTAM_S = 2 * 3600
GONE = (
    "reverted",
    "dropped",
    "voided",
    "adopted",
)  # a value that was live and is no longer: before windows stop there (also a measured split: its arm ended)

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
    "measured": "판정만 (값 그대로)",
}
DECISION_KO = {"better": "좋아짐", "worse": "나빠짐"}  # a measured verdict's direction (the status no longer says it)
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
    "overwritten": "운영자가 덮어씀",
}
EXPECT_CHARS = 40
LINES_RUN, LINES_ENDED = 6, 5
INTERIM_MIN_H = 0.25  # a running experiment younger than this gets no interim line
ROLES = ("shop", "buyer")  # the role operators (kernel.operator --role)
OTHER = " (다른 운영자)"  # a role's state card marks the other role's lines
TESTS = ("none", "before_after", "split")  # what a policy item may ask for (apply_policy)
GROUP_MODES = ("group", "bet")  # "bet": the first name of a group, still read from the ledger
GROUP_WINDOW_H = 4  # default window of a group
GROUP_LABEL_CHARS = 40


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


def _overlap(sec_a, sec_b, key_a=None, key_b=None):
    # a shop-wide restock or stock limit (pe.SHOP_KEYS) only overlaps the same key: with "shop:all" it would wait for
    # every running shop experiment (14 on 11-11), so the limit that stopped restock could not be tried for hours
    for s1, k1, s2, k2 in ((sec_a, key_a, sec_b, key_b), (sec_b, key_b, sec_a, key_a)):
        if s1 == "shop" and k1 in pe.SHOP_KEYS:
            return s2 == "shop" and k2 == k1
    # buyer keys only overlap the same key of the same kind (11-11: two experiments held buyer:food and buyer:goods
    # whole, so the buyer operator could not try a second key for hours); the verdict lists the overlapping ids instead
    if sec_a.startswith("buyer.") and sec_b.startswith("buyer."):
        return sec_a == sec_b and key_a == key_b
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


def _parts(e):
    """The (section, key) pairs an entry holds for overlap checks: a group's items, else its own section and key."""
    if e.get("mode") in GROUP_MODES:
        return [(x["section"], x.get("key")) for x in e.get("items") or []]
    return [(e["section"], e.get("key"))]


def _hits(section, key, e):
    return any(_overlap(section, s, key, k) for s, k in _parts(e))


def _own(section, key, e):
    """True when a write of (section, key) writes a value the experiment set: its own (section, key), a group member's,
    or for a split also the key its arm overrides in that section (an arm that carries a whole per-grade dict covers
    every grade of it)."""
    if (section, key) in _parts(e):
        return True
    if e.get("mode") != "split" or section != e.get("section") or not isinstance(key, str):
        return False
    path = (e.get("arm") or {}).get("path") or []
    base = str(e.get("key") or "").split(".", 1)[0]
    return bool(path) and path[-1] == base and key.split(".", 1)[0] == base


def overlaps(root, section, key, role=None):
    """The running experiments (of role when given, else of any role) whose scope a change of (section, key) touches:
    such a change voids the ones whose own key it writes (_own) and is named on the others' verdicts (void_overlaps).
    Nothing is refused because of them."""
    out = []
    for e in load(root):
        try:
            if (isinstance(e, dict) and e.get("status") == "running" and (role is None or role_of(e) == role)
                    and _hits(section, key, e)):
                out.append(e)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def _what(e):
    if e.get("mode") in GROUP_MODES:
        return f"묶음 '{e.get('key')}' 키 {len(e.get('items') or [])}개"
    return f"{e.get('section')} {e.get('key')}"


def void_overlaps(root, pairs, role, now, traj, keep=()):
    """End as voided (reason "overwritten") every running experiment of the role (any role when None) whose own value a
    new change of one of pairs [(section, key)] writes (_own); a split's arm is removed. A change that only touches an
    experiment's scope (_hits) does not end it: it is added to the experiment's "mixed_by" list, which its verdict
    carries, so the operator reads that the comparison is mixed. The other role's experiments are never touched. keep:
    ids left alone. -> [{"id", "what"}] of the ended ones."""
    if not pairs:
        return []
    rows, out, dirty = load(root), [], False
    for e in rows:
        try:
            if (not isinstance(e, dict) or e.get("status") != "running" or e.get("id") in keep
                    or (role is not None and role_of(e) != role)):
                continue
            hit = next(((s, k) for s, k in pairs if _own(s, k, e)), None)
            if hit is None:
                for s, k in pairs:
                    if _hits(s, k, e) and f"{s} {k}" not in (e.get("mixed_by") or []):
                        e["mixed_by"] = (e.get("mixed_by") or []) + [f"{s} {k}"]
                        dirty = True
                continue
            if e.get("mode") == "split":
                _remove_arm(root, e.get("id"))
            _end(e, "voided", {"decision": "voided", "reason": "overwritten", "by": f"{hit[0]} {hit[1]}"}, now, traj)
            out.append({"id": e.get("id"), "what": _what(e)})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    if out or dirty:
        save(root, rows)
    return out


def _before(rows, pairs, now):
    """(before_from, mixed) for a new before/after experiment over pairs [(section, key)]: before_from is the latest end
    of an overlapping experiment whose value was live and is no longer (GONE), mixed the ids of such experiments that
    ended less than CONTAM_S before now. A recent end does not clip the before window (it would leave no baseline);
    the verdict names it instead (11-11: CONTAM_S used to refuse the start)."""
    bf, mixed = None, []
    for e in rows:
        try:
            t = e.get("ended_ts")
            gone = e.get("status") in GONE or (e.get("status") == "measured" and e.get("mode") == "split")
            if (isinstance(e, dict) and gone and _num_ok(t)
                    and any(_hits(s, k, e) for s, k in pairs)):
                if now - t < CONTAM_S:
                    mixed.append(e["id"])
                else:
                    bf = t if bf is None else max(bf, t)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return bf, mixed


def default_window_h(side, key):
    if side == "buyer":
        return DEFAULT_WINDOW_H["buyer"]
    return DEFAULT_WINDOW_H["slow" if key.split(".", 1)[0] in SLOW_KEYS or key in pe.SHOP_KEYS else "shop"]


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
    role=None,
):
    """Record a successful policy edit (or split check) ({section, key, old, new}) as a running experiment. window_h
    None (or not a finite number) takes the default for the key; any value is clamped to [MIN_WINDOW_H, MAX_WINDOW_H]
    whole hours. rev_weight (shop side, the caller's board-derived worth of one won of revenue in net-profit won)
    turns the metric into score_proxy_per_h = net + rev_weight x revenue; it is kept for the whole experiment.
    mode "sequential" keeps raw ({base, present, value}: the section's own state before the edit, for the revert) and
    before_from and before_mixed (see _before); mode "split" keeps arm (the policy_arms.json entry). role (a role
    operator) is stored. -> the experiment."""
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
        exp["before_from"], mixed = _before(rows, [(row["section"], row.get("key"))], now)
        if mixed:
            exp["before_mixed"] = mixed
    if role in ROLES:
        exp["role"] = role
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


def _test_of(item):
    """The test the operator chose for a policy item: "none" when absent or empty, else the value as given."""
    t = item.get("test")
    return "none" if t in (None, "", "none") else t


def _group_label(v):
    return " ".join(v.split())[:GROUP_LABEL_CHARS] if isinstance(v, str) else ""


def _clean(it):
    return {
        "section": it["section"],
        "key": it["key"],
        "value": it.get("value"),
        "reason": it.get("reason") if isinstance(it.get("reason"), str) else "",
    }


def _expect(it):
    e = it.get("expect")
    if not isinstance(e, str) or not e:
        e = it.get("reason") if isinstance(it.get("reason"), str) else ""
    return e[:500]


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
    role=None,
):
    """One operator policy item {section, key, value, reason, expect, window_h} as an experiment (the entry point
    before 11-11, now a wrapper of apply_policy): mode "auto" is split when split_ok, the key is splittable on a
    shop-side section and the seller reads arms, else before/after; "split" / "sequential" force it. Never raises.
    -> apply_policy's result for the item ({"ok": True, "experiment", "mode", "section", "key", "old", "new",
    "eval_ts", "test"} or {"ok": False, "error"}) with "voided"."""
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
        it = {k: v for k, v in item.items() if k != "group"}
        it["test"] = "split" if split else "before_after"
        out = apply_policy(root, [it], now, traj, role=role, rev_weight=rev_weight, categories=categories,
                           validate=validate, split_ok=True)
        return dict(out["results"][0], voided=out["voided"])
    except Exception as e:  # noqa: BLE001 - a hostile item is a refusal, never a crashed wake
        return _err(f"{type(e).__name__}: {e}")


def apply_policy(root, items, now, traj, role=None, rev_weight=0.0, categories=(), validate=None, split_ok=False):
    """Apply one done.policy list (11-11 성수님: the operator decides what to change, how much, how many and how to
    measure it). Any number of items; each may carry "test":
    - "none" (default, or absent): the value is written; no experiment, no verdict, no revert (trajectory row by
      "direct").
    - "before_after": written, then measured as a sequential experiment (the verdict is reported, the value stays);
      items with the same "group" label are one experiment on their side, measured together.
    - "split": sell-side keys (SPLIT_KEYS) only, when split_ok and the seller reads arms: policy.json keeps the old
      value, half the units get the new one until the verdict, which ends the arm and only reports.
    Every written item goes into policy.json in one write (policy_edit.apply_many). A change ends as voided every
    running experiment of the same role whose own value it writes (void_overlaps; a scope-only touch is noted on the
    experiment's verdict instead); the other role's never. Nothing is
    refused because an experiment runs. Never raises. -> {"results": [per item, aligned with items], "voided":
    [{"id", "what"}]}."""
    items = items if isinstance(items, list) else []
    try:
        return _apply_policy(root, items, now, traj, role, rev_weight, categories, validate, split_ok)
    except Exception as e:  # noqa: BLE001 - a hostile list is a refusal, never a crashed wake
        return {"results": [_err(f"{type(e).__name__}: {e}") for _ in items], "voided": []}


def _apply_policy(root, items, now, traj, role, rev_weight, categories, validate, split_ok):
    res, plan, seen = [None] * len(items), [], set()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            res[i] = _err("each policy item must be an object")
            continue
        sec, key = it.get("section"), it.get("key")
        if not isinstance(sec, str) or not isinstance(key, str):
            res[i] = _err("section and key must be strings")
            continue
        test = _test_of(it)
        if test not in TESTS:
            res[i] = _err(f"test는 none, before_after, split 중 하나임 (낸 값 {str(test)[:30]})")
            continue
        if test == "split" and not (splittable(key) and scope_of(sec, key)[0] == "shop"):
            res[i] = _err(f"{key}는 split으로 잴 수 없음. split이 되는 키는 판매 구역의 {', '.join(SPLIT_KEYS)}")
            continue
        if test == "split" and not (split_ok is True and seller_has_arms(root)):
            res[i] = _err("split은 지금 쓸 수 없음 (판매자가 비교 팔을 읽지 않음). before_after와 none은 됨")
            continue
        x = pe._prepare(root, it, now, categories)
        if not x.get("ok"):
            res[i] = _err(x.get("error"))
            continue
        # only an item that passed every check takes its key, so a refused first item never blocks a valid later one
        if (sec, key) in seen:
            res[i] = _err(f"{sec} {key}가 이 목록에 두 번 있음")
            continue
        seen.add((sec, key))
        plan.append((i, it, test, _group_label(it.get("group")) if test == "before_after" else ""))
    # a refused item (above) is refused alone: the rest of its group goes ahead (one bad item never blocks the others).
    # A group mixing shop and buyer values is refused whole: its verdict is per side
    members = {}
    for i, it, test, g in plan:
        if g:
            members.setdefault(g, []).append(it)
    bad = {}
    for g, its in members.items():
        if len({scope_of(it["section"])[0] for it in its}) > 1:
            bad[g] = "가게 쪽 값과 구매 쪽 값은 한 묶음에 담지 못함 (판정이 쪽마다 따로임)"
    for i, it, test, g in plan:
        if g in bad:
            res[i] = _err(f"묶음 '{g}' 전체를 적용하지 않음: {bad[g]}")
    plan = [p for p in plan if p[3] not in bad]
    writes = [p for p in plan if p[2] != "split"]
    raws, pre = {}, []
    if writes:
        # each item's own state before this done (other items of the done not in it): (present, raw value, what a
        # category inherited for a per-grade or accept_c base it did not set)
        pol, ledger0 = _policy(root), load(root)
        for i, it, _, _ in writes:
            present, value = pe.raw_state(pol, it["section"], it["key"])
            base, _, sub = it["key"].partition(".")
            inh = pe.inherited_value(pol, it["section"], base) if sub and not present and base != pe.LEAF_KEY else None
            if sub and present and base != pe.LEAF_KEY:
                # the base is only there because a running experiment of this section copied the inherited dict in:
                # this item started from the same inherited state, so both reverts end with the category inheriting
                org = _inherited_origin(ledger0, it["section"], base)
                if org is not None:
                    present, value, inh = False, None, org
            raws[i] = (present, value, inh)
        # a split whose own value is about to be written loses its arm first, so the seller never reads the new base
        # with the old arm; sync_arms below puts back the arm of one the write did not end
        pre = [e for e in running(root) if e.get("mode") == "split" and (role is None or role_of(e) == role)
               and any(_own(it["section"], it["key"], e) for _, it, _, _ in writes)]
        for e in pre:
            _remove_arm(root, e.get("id"))
    got = pe.apply_many(
        root, [_clean(it) for _, it, _, _ in writes], traj, now, categories, validate, tag=pe.kernel_tag(now),
        by=["direct" if t == "none" else "experiment" for _, _, t, _ in writes],
    )
    applied = []
    for (i, it, test, g), r in zip(writes, got):
        if r.get("ok"):
            applied.append((i, it, test, g, r))
        else:
            res[i] = _err(r.get("error"))
    splits = []
    for i, it, test, _ in plan:
        if test == "split":
            r = pe.check(root, _clean(it), traj, now, categories=categories, validate=validate)
            if r.get("ok"):
                splits.append((i, it))
            else:
                res[i] = _err(r.get("error"))
    touched = [(it["section"], it["key"]) for _, it, *_ in applied] + [(it["section"], it["key"]) for _, it in splits]
    voided = void_overlaps(root, touched, role, now, traj)
    if pre:
        sync_arms(root)
    groups = {}
    for i, it, test, g, r in applied:
        if test == "none":
            res[i] = dict(r, test="none")
        elif g:
            groups.setdefault(g, []).append((i, it, r))
        else:
            res[i] = _register_one(root, it, r, raws[i], now, traj, role, rev_weight, validate)
    for g, xs in groups.items():
        out = _register_group(root, g, xs, raws, now, traj, role, rev_weight, validate)
        for i, _, _ in xs:
            res[i] = out
    for i, it in splits:
        res[i] = dict(
            _start_split(root, _clean(it), now, traj, rev_weight, it.get("window_h"), _expect(it), categories, validate,
                         role=role),
            test="split",
        )
    return {"results": res, "voided": voided}


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


def _inherited_origin(rows, section, base):
    """The inherited dict a running experiment of this section recorded for base when the section did not set it
    (raw present False with "inherited"), else None."""
    for e in rows:
        if not isinstance(e, dict) or e.get("status") != "running":
            continue
        for x in (e.get("items") if isinstance(e.get("items"), list) else [e]):
            raw = x.get("raw") if isinstance(x, dict) else None
            if (isinstance(raw, dict) and x.get("section") == section and raw.get("base") == base
                    and raw.get("present") is False and isinstance(raw.get("inherited"), dict)):
                return copy.deepcopy(raw["inherited"])
    return None


def _raw(key, state):
    """The ledger's raw state of an item {base, present, value} (the format older rows have too), plus "inherited"
    when the section inherited the per-grade or accept_c dict it did not set (read by the key-level revert)."""
    present, value, inh = state
    raw = {"base": key.split(".", 1)[0], "present": present, "value": value}
    if inh is not None:
        raw["inherited"] = inh
    return raw


def _restore(root, section, key, raw, traj, now, why, tag, validate):
    """Put the experiment's own key back from its raw state (policy_edit.kernel_restore, key level)."""
    return pe.kernel_restore(root, section, raw["base"], raw.get("present"), raw.get("value"), traj, now, why, tag=tag,
                             key=key, validate=validate, inherited=raw.get("inherited"))


def _register_one(root, it, r, raw_state, now, traj, role, rev_weight, validate):
    """Register one written before/after item as a sequential experiment; a failed registration puts the value back."""
    raw = _raw(r["key"], raw_state)
    try:
        exp = register(root, r, _expect(it), it.get("window_h"), now, traj, rev_weight, mode="sequential", raw=raw,
                       role=role)
    except Exception as e:  # noqa: BLE001 - an unregistered edit must not stay in the file
        back = _restore(root, r["section"], r["key"], raw, traj, now, f"실험 등록 실패로 되돌림 ({type(e).__name__})",
                        pe.kernel_tag(now), validate)
        return _err(
            f"실험 등록 실패 ({type(e).__name__}: {e}); "
            + ("값은 원래대로 되돌림" if back.get("ok") else f"되돌리기도 실패: {back.get('error')}")
        )
    return dict(_ok(exp, r), test="before_after")


def _start_split(
    root, clean, now, traj, rev_weight, window_h, expect, categories, validate, role=None
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
            role=role,
        )
        # the policy tool's history shows the proposal (policy.json keeps the old value until the verdict)
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


def _register_group(root, label, xs, raws, now, traj, role, rev_weight, validate):
    """Register the written items xs [(index, item, edit result)] of one group as ONE experiment of mode "group" on
    their side's whole scope ("shop:all" or "buyer:all"); a worse verdict puts every item's raw state back. A failed
    registration puts the items back at once. -> the result each item of the group gets."""
    items = [it for _, it, _ in xs]
    got = [
        {**{k: r[k] for k in ("section", "key", "old", "new")},
         "raw": _raw(r["key"], raws[i])}
        for i, _, r in xs
    ]
    side = scope_of(got[0]["section"])[0]
    role = role if role in ROLES else side
    wh = next((it["window_h"] for it in items if "window_h" in it), None)
    expect = next((_expect(it) for it in items if _expect(it)), "")
    try:
        exp = _register_group_entry(root, label, got, role, side, rev_weight, wh, expect, now, traj)
    except Exception as e:  # noqa: BLE001 - an unregistered group must not stay in the file
        why = f"묶음 등록 실패로 되돌림 ({type(e).__name__})"
        bad = [x for x in reversed(got) if not _restore(
            root, x["section"], x["key"], x["raw"], traj, now, why, pe.kernel_tag(now), validate).get("ok")]
        return _err(f"묶음 등록 실패 ({type(e).__name__}: {e}); "
                    + ("값은 모두 원래대로 되돌림" if not bad else f"되돌리기 실패 {len(bad)}개"))
    return {
        "ok": True,
        "test": "before_after",
        "experiment": exp["id"],
        "mode": "group",
        "group": label,
        "items": [{k: x[k] for k in ("section", "key", "old", "new")} for x in got],
        "eval_ts": exp["eval_ts"],
    }


def _register_group_entry(root, label, got, role, side, rev_weight, window_h, expect, now, traj):
    rows = load(root)
    rw = rev_weight if side == "shop" and _num_ok(rev_weight) and rev_weight > 0 else 0.0
    bf, mixed = _before(rows, [(x["section"], x["key"]) for x in got], now)
    exp = {
        "id": _next_id(rows),
        "mode": "group",
        "section": "group",
        "key": label,
        "old": None,
        "new": None,
        "items": got,
        "role": role,
        "started_ts": now,
        "window_h": _window(window_h, side, "") if _num_ok(window_h) else GROUP_WINDOW_H,
        "maturity_h": MATURITY_H[side],
        "scope": side + ":all",
        "side": side,
        "metric": SCORE_METRIC if rw > 0 else METRIC[side],
        "rev_weight": float(rw),
        "expect": str(expect or "")[:500],
        "status": "running",
        "extended": False,
        "before_from": bf,
    }
    if mixed:
        exp["before_mixed"] = mixed
    if side == "buyer":
        exp["s0"] = board_saved(root, now)
    exp["eval_ts"] = _eval_ts(exp)
    rows.append(exp)
    save(root, rows)
    traj.append(
        "operator",
        "experiment_start",
        items=[{k: x[k] for k in ("section", "key", "old", "new")} for x in got],
        **{k: exp[k] for k in ("id", "mode", "section", "key", "scope", "side", "metric", "rev_weight", "window_h",
                               "eval_ts", "expect", "role")},
    )
    return exp


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
    if not _num_ok(lp) or lp <= 0 or not _num_ok(d.get("amount")):  # NaN or inf would read as a whole margin kept
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
    if scope in ("shop:all", "buyer:all"):
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


# Patch 3.6 (11-12): a deal's saved is (list - paid) / (list - the shop's unit cost), at most 1. The buyer does not
# see the cost, so the market's average cost share of list (RULES: goods about 61%, food about 52%) stands in for it.
# On our ledger this gives 0.729 against the board's 0.721 (the old share of list: 0.346 against 0.334).
COST_FRAC = {"food": 0.52, "goods": 0.61}
# The first board snapshot on the patch 3.6 scale (11-12 17:47 KST); an s0 read before it is on the old scale, so
# those runs compare against S0_V36, our board saved in that snapshot.
SAVED_V36_TS = 1_794_473_247
S0_V36 = 0.7212


def margin_kept(lp, paid, kind):
    """The share of the shop's margin a deal kept, at most 1 (Patch 3.6), with COST_FRAC standing in for the cost."""
    return min(1.0, (lp - paid) / (lp * (1 - COST_FRAC.get(kind, COST_FRAC["goods"]))))


def s0_of(e):
    """The s0 a run's buyer metric compares against: its own when it was read on the patch 3.6 scale, else S0_V36.
    s0_ts is when s0 was read, when that differs from the start (a code change restarted after it was proposed)."""
    t = e.get("s0_ts", e.get("started_ts"))
    return e.get("s0") if _num_ok(t) and t >= SAVED_V36_TS else S0_V36


def buyer_metric(data, scope, t0, t1, s0):
    """saved_gain_per_h over our buyer's purchases settled in [t0, t1) of the scope's kind: sum of (kept - s0) per
    hour, kept = margin_kept(list, paid, kind). The board's saved is sum(kept) / (deals + 100), so a deal raises it
    only when its kept share is above the current value s0. "others": the other buyers' deals of the same kind in the
    window (market settles that are not our purchases)."""
    if t1 <= t0:
        return {"value": 0.0, "n": 0, "buckets": [], "others": 0, "hours": 0.0}
    n_b, idx = _buckets(t0, t1)
    hours, buckets, n, s0 = (t1 - t0) / 3600, [0.0] * n_b, 0, s0 or 0.0
    for x in data["deals"]:
        if t0 <= x["ts"] < t1 and _in_scope(scope, None, x["kind"]):
            n += 1
            buckets[idx(x["ts"])] += margin_kept(x["lp"], x["amount"], x.get("cost_kind", x["kind"])) - s0
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
            buyer_metric(data, scope, b0, t, s0_of(exp)),
            buyer_metric(data, scope, t, t + w, s0_of(exp)),
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
        v.update(s0=s0_of(exp), others_before=b["others"], others_after=a["others"])
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
        window_h=min(EXT_MAX_WINDOW_H, exp["window_h"] * 2),
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


def overlapping(rows, exp):
    """Ids of the other experiments of exp's side whose live time [started_ts, ended_ts) overlaps exp's after window
    [started_ts, started_ts + window_h): their changes are inside a before/after comparison of that side (a bet, a
    buyer key), so its verdict is confounded by them."""
    t = exp["started_ts"]
    end = t + exp["window_h"] * 3600
    out = []
    for e in rows:
        try:
            if not isinstance(e, dict) or e.get("id") == exp.get("id") or e.get("side") != exp.get("side"):
                continue
            t_end = e.get("ended_ts")
            if e["started_ts"] < end and (not _num_ok(t_end) or t_end > t):
                out.append(e["id"])
        except (KeyError, TypeError):
            continue
    return out


def _judge(exp, v, traj):
    """The before/after rule of sequential experiments and bets on verdict v (sets v["final"]): -> None when the window
    was extended, True when v is the final verdict (reported only; the value is never touched)."""
    if v["decision"] in ("better", "worse"):
        v["final"] = "decision"
    elif not exp.get("extended"):
        _extend(exp, v, traj)
        return None
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
    return True


def _measured(exp, v, now, traj, note):
    """End exp report-only: the verdict numbers with note (what happened to the value) and status "measured"."""
    v["note"] = note
    _end(exp, "measured", v, now, traj)


VALUE_STAYS = "값은 운영자가 둔 그대로 (커널은 재기만 함)"


def _step_sequential(root, exp, now, traj, categories, validate):
    """Measure one due sequential experiment. -> True when it ended or was extended. The value is never written."""
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
    ov = overlapping(load(root), exp) if exp.get("side") == "buyer" else []
    if ov:  # only when there is one: HEAD's buyer verdicts carry no overlap key
        v["overlap"] = ov
    if exp.get("before_mixed"):
        v["before_mixed"] = exp["before_mixed"]
    if exp.get("mixed_by"):
        v["mixed_by"] = exp["mixed_by"]
    if _judge(exp, v, traj) is None:
        return True
    _measured(exp, v, now, traj, VALUE_STAYS)
    return True


def _step_group(root, exp, now, traj, categories, validate):
    """Measure one due group (mode "group", or "bet" from before) like a sequential experiment (evaluate on the whole
    side, _judge), with the ids of the other experiments of the side in its window (overlapping) on the verdict. No item
    is written. A group left "reverting" by the code before 11-12 (a worse verdict whose restore failed) ends measured
    on that verdict, without restoring the rest; the items it had already put back are named on it (restored).
    -> True when it ended or was extended."""
    items = exp.get("items") or []
    v = exp.pop("reverting", None)
    if isinstance(v, dict):
        done = [f"{x['section']} {x['key']}" for x in items if x.get("restored")]
        if done:
            v["restored"] = done
        _measured(exp, v, now, traj, VALUE_STAYS + (f"; 예전 커널이 이미 되돌린 키 {len(done)}개는 되돌린 값 그대로"
                                                    if done else ""))
        return True
    pol, moved = _policy(root), {}
    for x in items:
        cur = pe.effective(pol, x["section"], x["key"])
        if not _same(cur, x["new"]):
            moved[f"{x['section']} {x['key']}"] = cur
    if moved:
        _end(exp, "voided", {"decision": "voided", "reason": "value_changed", "value_now": moved}, now, traj)
        return True
    v = evaluate(root, exp, now)
    ov = overlapping(load(root), exp)
    if ov:
        v["overlap"] = ov
    if exp.get("before_mixed"):
        v["before_mixed"] = exp["before_mixed"]
    if exp.get("mixed_by"):
        v["mixed_by"] = exp["mixed_by"]
    if _judge(exp, v, traj) is None:
        return True
    _measured(exp, v, now, traj, VALUE_STAYS)
    return True


def _step_split(root, exp, now, traj, categories, validate):
    """Measure one due split experiment: the arm ends, policy.json keeps its value (the old one: a split never wrote
    the new one). -> True when it ended or was extended."""
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
    if exp.get("mixed_by"):
        v["mixed_by"] = exp["mixed_by"]
    arm = exp.get("arm") or {}
    if v["decision"] in ("better", "worse"):
        v["final"] = "decision"
    elif (
        not exp.get("extended")
        and exp["window_h"] < EXT_MAX_WINDOW_H
        and now < (arm.get("until") or 0)
    ):
        _extend(exp, v, traj)
        until = exp["eval_ts"] + ARM_GRACE_S
        _set_arm_until(root, eid, until)
        exp["arm"] = dict(arm, until=until)
        return True
    else:
        v["final"] = "point_estimate"
    _remove_arm(root, eid)
    _measured(exp, v, now, traj, f"반쪽 적용 끝남, policy.json은 기존 값 {_val(exp['old'])} 그대로. "
                                 f"새 값 {_val(exp['new'])}은 운영자가 쓰지 않으면 적용되지 않음")
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
            mode = exp.get("mode")
            step = _step_split if mode == "split" else _step_group if mode in GROUP_MODES else _step_sequential
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
    if e.get("mode") in GROUP_MODES:
        head = f"  진행 #{e['id']} 묶음 '{e.get('key')}' 키 {len(e.get('items') or [])}개"
    elif e.get("mode") == "split":
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


def interim_data(root, run, now):
    """One events read that covers the before and after windows, up to now, of every experiment in `run` (None when
    none has a start and a window)."""
    lo = []
    for e in run:
        t, wh = e.get("started_ts"), e.get("window_h")
        if _num_ok(t) and _num_ok(wh) and wh > 0:
            lo.append((t if e.get("mode") == "split" else t - wh * 3600) - JOIN_LOOKBACK_H * 3600)
    return read_events(root, min(lo), now, now=now) if lo else None


def _after_undecided(e, now):
    """What tick does with an undecided verdict on e's present window (mirrors _judge / _step_split): ("extend", h)
    when the window becomes h hours and is judged once more, ("last", window_h) when the weak rule decides on this
    window. A split is extended only while its arm lives (now < arm.until); a before/after window above the extension
    limit is cut to the limit (h below window_h), and one at the limit is judged again on the same hours ("last")."""
    wh = e["window_h"]
    h = min(EXT_MAX_WINDOW_H, wh * 2)
    if e.get("extended") or h == wh:
        return "last", wh
    if e.get("mode") == "split" and not (wh < EXT_MAX_WINDOW_H and now < ((e.get("arm") or {}).get("until") or 0)):
        return "last", wh
    return "extend", h


def interim_line(e, now, data):
    """What a running experiment holds so far, as one card line (no writes, no verdict): hours run of the window, the
    sample (per arm, or before and after) with the pace projected to the verdict time next to the sample the verdict
    needs, the values and their difference, the smallest difference a clear verdict needs (the larger of CLEAR_SE x se
    at the verdict time, when sales keep coming as they have, and REL_MIN of the base value or of FLOOR) with the
    stars guard that overrides it, and what the kernel does below that (_after_undecided): one more look on another
    window, or on the last window a weak verdict (point estimate) that is only reported; the value is never touched
    (a split's arm ends at the verdict, policy.json keeps A). A split also says whether the present
    difference would clear the noise inside the last window at this pace (se of a split falls with 1 / sqrt(hours)).
    "" when too early or without data.
    Live 11-12: experiments ended on 3 against 5 sales; nothing on the card showed that before the verdict."""
    t, wh = e.get("started_ts"), e.get("window_h")
    if data is None or not (_num_ok(t) and _num_ok(wh) and wh > 0) or e.get("status") != "running":
        return ""
    done = now >= t + wh * 3600  # the window is over; the verdict waits for late breaks (maturity)
    el = (min(now, t + wh * 3600) - t) / 3600
    if el < INTERIM_MIN_H:
        return ""
    k = wh / el  # the verdict window over the hours run
    nxt, h_nxt = _after_undecided(e, now)
    last = max(wh, h_nxt)  # the longest window this experiment is still judged on
    head = (f"    지금까지 {wh:g}/{wh:g}시간 (구간 끝남, 판정 대기): " if done
            else f"    지금까지 {math.floor(el * 10 + 1e-6) / 10:.1f}/{wh:g}시간: ")
    noise = f"잡음({CLEAR_SE}se)"
    ext = ("" if nxt == "last"  # this window is the last
           else f"커널이 구간을 {h_nxt:g}시간으로 늘려 한 번 더 봄" if h_nxt > wh
           else f"커널이 처음 {h_nxt:g}시간만으로 다시 판정함")
    stars = f"; 리뷰가 양쪽 {STARS_MIN_N}건 이상이고 별점이 {STARS_DROP:g} 넘게 낮아지면 차이와 상관없이 악화 판정"
    if e.get("mode") == "split":
        v = evaluate_split(None, dict(e, window_h=el), now, data)
        diff = v["b"] - v["a"]
        out = (
            head
            + f"판매 A {v['n_a']} / B {v['n_b']} ("
            + ("" if done else f"이 속도면 판정 때 약 {v['n_a'] * k:.0f} / {v['n_b'] * k:.0f}, ")
            + f"판정에 필요한 표본 각 {THIN_N} 합 {SPLIT_THIN_N}), 값 A {_num(v['a'], 'shop')}, B {_num(v['b'], 'shop')}, "
            f"차이 {_num(diff, 'shop', sign=True)}"
        )
        if v["se"]:
            rel = REL_MIN * max(abs(v["a"]), FLOOR["shop"])  # the verdict's second condition
            rel_name = f"A 값의 {REL_MIN:.0%} 기준" if abs(v["a"]) >= FLOOR["shop"] else "최소 기준"
            se_end = v["se"] / math.sqrt(k)
            out += (
                ". " + ("" if done else "판매가 이대로면 ")
                + f"뚜렷한 판정에 필요한 차이는 ±{_num(max(CLEAR_SE * se_end, rel), 'shop')}부터 ({noise}과 {rel_name} 중 큰 쪽{stars})"
                + (f", 못 미치면 {ext}" if ext
                   else f", 마지막 구간이라 못 미치면 점추정으로 판정만 냄 (1se ±{_num(se_end, 'shop')}); 판정 때 "
                        "반쪽 적용이 끝나고 값은 A 그대로이며 B는 네가 쓸 때만 적용됨")
            )
            if diff:
                h = el * (CLEAR_SE * v["se"] / abs(diff)) ** 2
                out += ". 지금 차이는 " + (
                    f"{rel_name}(±{_num(rel, 'shop')})에 못 미침" if abs(diff) <= rel
                    else "이미 지금의 잡음을 넘음" if h < el
                    else f"이어지면 시작부터 약 {h:.0f}시간에 잡음을 넘음" if h <= last
                    else f"이어져도 마지막 구간({last:g}시간) 안에 잡음을 넘지 못함"
                )
        return out
    side, scope, w = e.get("side"), e["scope"], wh * 3600
    bf = e.get("before_from")
    b0 = min(max(t - w, bf) if _num_ok(bf) else t - w, t)
    t1 = t + el * 3600
    if side == "buyer":
        b, a = buyer_metric(data, scope, b0, t, s0_of(e)), buyer_metric(data, scope, t, t1, s0_of(e))
        r = _ratio(a["others"], a["hours"], b["others"], b["hours"])
    else:
        rw = e.get("rev_weight") or 0.0
        b, a = shop_metric(data, scope, b0, t, rw), shop_metric(data, scope, t, t1, rw)
        r = _ratio(a["market"], a["hours"], b["market"], b["hours"])
    adj = b["value"] * r
    unit = "거래" if side == "buyer" else "판매"
    pace = "" if done else f"이 속도면 판정 때 후 약 {a['n'] * k:.0f}"
    # the two ways evaluate() has no baseline, whatever the after window holds: a clipped before window, or an events
    # read that does not reach its start
    earliest = data.get("earliest", b0)
    short = t - b0 < MIN_BEFORE_H * 3600
    if short or earliest is None or earliest > b0 + BASELINE_SLACK_S:
        return (head + f"{unit} 후 {a['n']}" + (f" ({pace})" if pace else "") + f", 후 {_num(a['value'], side)}. "
                + (f"전 구간이 {int((t - b0) // 60)}분이라 비교 기준이 없음 (기준 {MIN_BEFORE_H * 60}분)" if short
                   else "거래 기록이 전 구간의 시작까지 닿지 않아 비교 기준이 없음")
                + (f", {ext}" if ext and h_nxt < wh else ", 비교 없이 값이 유지됨"))
    out = (
        head
        + f"{unit} 전 {b['n']} → 후 {a['n']} (" + (pace + ", " if pace else "")
        + f"판정에 필요한 표본 {THIN_N}), 전 보정 {_num(adj, side)}, 후 {_num(a['value'], side)}, "
        f"차이 {_num(a['value'] - adj, side, sign=True)}"
    )
    sign = "점추정으로 판정만 내고 값은 네가 둔 그대로임"
    if side == "buyer":
        stars = ""  # the stars guard is the shop side's
    bb = [x * r for x in b["buckets"]]
    if len(bb) < 2:
        return (out + ". 전 구간이 1시간 구간 하나라 잡음을 셀 수 없어 이 구간에서는 차이로 뚜렷한 판정이 나오지 않음, "
                + (ext or f"마지막 구간이라 {sign}"))
    # the after variance over whole elapsed hours only: a partial last hour would read as a swing of its own
    whole = int(el + 1e-9)
    ab = (buyer_metric(data, scope, t, t + whole * 3600, s0_of(e)) if side == "buyer"
          else shop_metric(data, scope, t, t + whole * 3600, e.get("rev_weight") or 0.0))["buckets"] if whole >= 2 else []
    own = len(ab) >= 2
    va = st.variance(ab) if own else st.variance(bb)
    need = max(CLEAR_SE * math.sqrt(va / max(1, int(round(wh))) + st.variance(bb) / len(bb)),
               REL_MIN * max(abs(adj), FLOOR[side]))
    rel_name = f"전 보정 값의 {REL_MIN:.0%} 기준" if abs(adj) >= FLOOR[side] else "최소 기준"
    return (out + ". " + ("" if done else "시간별 흔들림이 이대로면 ")
            + f"뚜렷한 판정에 필요한 차이는 ±{_num(need, side)}부터 ({noise}과 {rel_name} 중 큰 쪽"
            + ("" if own else ", 후 구간이 온전한 2시간 미만이라 전 구간의 흔들림으로 셈") + stars + ")"
            + (f", 못 미치면 {ext}" if ext else f", 마지막 구간이라 못 미치면 {sign}"))


def _ended_line(e):
    v, side = e.get("verdict") or {}, e.get("side")
    status = STATUS_KO.get(e["status"], e["status"])
    if e["status"] == "measured" and v.get("decision") in DECISION_KO:
        status += " " + DECISION_KO[v["decision"]]
    if e.get("mode") in GROUP_MODES:
        head = f"  #{e['id']} 묶음 '{e.get('key')}' 키 {len(e.get('items') or [])}개 {status}"
    else:
        head = f"  #{e['id']} {e.get('key')} {_val(e.get('old'))}→{_val(e.get('new'))} {status}"
    if e["status"] == "voided":
        return head + (f": {_reason(v)}" if _reason(v) else "")
    reason = _reason(v)
    mb = v.get("mixed_by") if isinstance(v.get("mixed_by"), list) else []
    mixed = (f", 진행 중 같은 범위의 다른 변경 {', '.join(map(str, mb[:3]))}{' 외' if len(mb) > 3 else ''} (비교가 섞임)"
             if mb else "")
    if e.get("mode") == "split":
        se = f", se {_num(v['se'], 'shop', False)}" if v.get("se") is not None else ""
        return (
            head
            + f": 효과 {_num(v.get('effect'), 'shop', sign=True)} (A {_num(v.get('a'), 'shop')}, B {_num(v.get('b'), 'shop')}"
            + f"{se}, 판매 A {v.get('n_a', '-')} / B {v.get('n_b', '-')})"
            + (f", {reason}" if reason else "")
            + mixed
            + (", 새 값은 네가 쓰지 않으면 적용 안 됨" if e["status"] == "measured" else "")
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
        + (
            f", 같은 구간의 다른 실험 {', '.join(f'#{i}' for i in v['overlap'])} (비교가 섞임)"
            if v.get("overlap")
            else ""
        )
        + (
            f", 전 구간에 앞선 실험 {', '.join(f'#{i}' for i in v['before_mixed'])}의 값이 섞임"
            if v.get("before_mixed")
            else ""
        )
        + mixed
    )


def lines(root, now, last=LINES_ENDED, role=None, interim=False):
    """[실험] briefing lines: counts, up to LINES_RUN running experiments and the last `last` ended ones, one compact
    line each with its mode. With a role, the other role's lines end with OTHER. interim: each running experiment
    (with a role: of that role) is followed by its interim_line."""

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
    head = f"[실험] 진행 {len(run)}건, 최근 판정 {len(ended)}건 중 {STATUS_KO['measured']} {n('measured')}"
    if n("kept") or n("reverted"):  # verdicts before 11-12, when the kernel still kept or put values back
        head += f", 유지 {n('kept')}, 되돌림 {n('reverted')}"
    if n("adopted") or n("dropped"):
        head += f", 채택 {n('adopted')}, 버림 {n('dropped')}"
    out = [head]
    mine = [e for e in run[:LINES_RUN] if not role or role_of(e) == role] if interim else []
    try:
        data = interim_data(root, mine, now)
    except Exception:  # noqa: BLE001 - the interim lines are an addition; the ledger lines still go out
        data = None
    for e in run[:LINES_RUN]:
        try:
            out.append(_run_line(e) + mark(e))
        except Exception:  # noqa: BLE001 - a malformed entry is shown by id only
            out.append(f"  진행 #{e.get('id')}")
        try:
            more = interim_line(e, now, data) if any(e is m for m in mine) else ""
        except Exception:  # noqa: BLE001
            more = ""
        if more:
            out.append(more)
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
    (kept + adopted + measured better) / (kept + reverted + adopted + dropped + measured better or worse), None when
    none of those happened."""
    rows = [e for e in load(root) if isinstance(e, dict)]
    since = now - hours * 3600
    out = {s: 0 for s in STATUS_KO}
    better = worse = 0
    for e in rows:
        s = e.get("status")
        t = e.get("ended_ts")
        if s == "running" or (s in out and _num_ok(t) and t >= since):
            out[s] += 1
            if s == "measured":
                d = e["verdict"].get("decision") if isinstance(e.get("verdict"), dict) else None
                better += d == "better"
                worse += d == "worse"
    good = out["kept"] + out["adopted"] + better
    judged = good + out["reverted"] + out["dropped"] + worse
    return {
        "hours": hours,
        "counts": out,
        "hit_rate": good / judged if judged else None,
    }
