# Reject checks and caps. check() -> [] or list of reasons. Never applies anything.
# M2r: proposal allowlist in code (C6) + action_space range/step check (C10) + watch-window overlap ban (C3).
# wins() = C7 rule (Bonferroni lower bound + block floor, no seed count); applicable() = apply gate (price_mult blocked).
import json
import re
from pathlib import Path

from rlpo import config as C
from harness import bounds

SPACE = json.loads((Path(__file__).with_name("action_space.json")).read_text())
KNOWN = json.loads((Path(__file__).with_name("known_categories.json")).read_text(encoding="utf-8"))["categories"]
_ALLOW_PATH = re.compile(r"^(defaults\.(food|goods)|categories\.([^.]+))\.([a-z_]+)$")


def _get(pol, path):
    d = pol
    for p in path.split("."):
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


def _base_val(pol, path, kind):
    v = _get(pol, path)
    if v is None:  # categories.X.key falls back to defaults.<kind>.key, as bounds._current
        side = "buyer." if path.startswith("buyer.") else ""
        v = _get(pol, side + "defaults." + kind + "." + path.rsplit(".", 1)[-1])
    return v


def allow_violations(base, proposal):
    # C6: only C.PROPOSE_KEYS on shop paths; category must exist (records or base policy) with kind = kind_hint
    out = []
    kind = proposal.get("kind_hint")
    for i, ed in enumerate(proposal.get("edits") or []):
        p = str(ed.get("path", "")) if isinstance(ed, dict) else ""
        m = _ALLOW_PATH.match(p)
        if not m:
            out.append(f"edit {i}: path {p!r} not in the RLPO allowlist (buyer.* and other forms are refused)")
            continue
        key, cat = m.group(4), m.group(3)
        if key not in C.PROPOSE_KEYS:
            out.append(f"edit {i}: key {key!r} not in the RLPO allowlist {sorted(C.PROPOSE_KEYS)}")
            continue
        if cat is not None:
            kinds = KNOWN.get(cat) or []
            if cat not in KNOWN and cat not in ((base or {}).get("categories") or {}):
                out.append(f"edit {i}: category {cat!r} does not exist")
            elif kinds and kind not in kinds:
                out.append(f"edit {i}: category {cat!r} is {kinds}, not kind_hint {kind!r}")
        spec = (SPACE.get("allow") or {}).get(key)
        if not spec:
            out.append(f"edit {i}: key {key!r} has no action_space allow entry")
            continue
        v = ed.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            lo, hi = spec["range"]
            if not lo <= v <= hi:
                out.append(f"edit {i}: {key}={v} outside action_space range [{lo}, {hi}]")
            cur = _base_val(base, p, kind) if kind in ("food", "goods") else None
            step = spec["max_step_frac_of_range"] * (hi - lo)
            if isinstance(cur, (int, float)) and abs(v - cur) > step + 1e-9:
                out.append(f"edit {i}: {key} {cur} -> {v} exceeds action_space step {step:g}")
    return out


def check(base, proposal, anchor=None, history=(), t_now=0.0, t_rem=None, open_watch=None):
    # base = live policy, anchor = persisted anchor policy (cumulative cap), history = experience rows,
    # open_watch = persisted {version, t} of an apply whose watch window has not closed (C3)
    if not isinstance(proposal, dict):
        return ["proposal is not an object"]
    names = {ph["name"] for ph in SPACE.get("placeholders", []) if not ph.get("enabled")}
    for ed in proposal.get("edits") or []:
        p0 = str(ed.get("path", "")) if isinstance(ed, dict) else ""
        if p0.rsplit(".", 1)[-1] in names:
            return [f"path {p0} is a disabled placeholder"]
        p = str((ed or {}).get("path", "")) if isinstance(ed, dict) else ""
        if any(s in p for s in SPACE["excluded_substr"]):
            return [f"path {p} is excluded (warranty)"]
    v = bounds.check(base, proposal)
    if v:
        return v
    v = allow_violations(base, proposal)
    if v:
        return v
    out = []
    anchor = anchor or base
    kind = proposal["kind_hint"]
    for ed in proposal["edits"]:
        key = ed["path"].rsplit(".", 1)[-1]
        lo, hi = base["bounds"][key]
        a = _base_val(anchor, ed["path"], kind)
        if isinstance(a, (int, float)) and abs(ed["value"] - a) > C.CUM_FRAC * (hi - lo) + 1e-9:
            out.append(f"{ed.get('path')}: cumulative move from anchor {a} exceeds {C.CUM_FRAC} x range")
        cur = _base_val(base, ed["path"], kind)
        if (key in ("stock_target", "qty_per_buy") and t_rem is not None and t_rem < C.ENDGAME_S
                and isinstance(cur, (int, float)) and ed["value"] > cur):
            out.append(f"{ed.get('path')}: supply increase (stock_target/qty_per_buy) blocked in the last {C.ENDGAME_S // 3600} h")
        for r in history:
            if (r.get("a_status") == "rolled_back" and t_now - r["t"] < C.COOLDOWN_S
                    and ed["path"] in [e.get("path") for e in (r.get("a") or {}).get("edits", [])]):
                out.append(f"{ed.get('path')}: in rollback cooldown")
                break
    applied = [r for r in history if r.get("a_status") == "applied" and t_now - r["t"] < C.APPLY_WINDOW_S]
    if len(applied) >= C.MAX_APPLY_PER_WINDOW:
        out.append(f"apply rate cap: {len(applied)} applied in the last {C.APPLY_WINDOW_S // 3600} h")
    if open_watch:
        out.append(f"watch window of v{open_watch.get('version')} is still open (C3: no overlapping applies)")
    return out


def wins_stat(rep, min_blocks=None):
    # C7 statistical rule only: one-sided Bonferroni lower bound > 0, n, block floor, money metric
    mb = C.MIN_BLOCKS if min_blocks is None else min_blocks
    try:
        return (rep["lo_adj"] is not None and rep["lo_adj"] > 0 and rep["n"] >= C.MIN_REPLAY_N
                and rep["n_blocks"] >= mb and rep["money_frac"] is not None and rep["money_frac"] >= C.MONEY_TOL)
    except (KeyError, TypeError):
        return False


def wins(rep):
    # a replay win needs a causal judge, verdict ok and the C7 statistical rule
    try:
        return rep.get("causal") is True and rep.get("verdict") == "ok" and not rep.get("unsupported") and wins_stat(rep)
    except AttributeError:
        return False


def applicable(proposal, rep=None):
    # apply gate (every path): [] or reasons. price_mult and any key in C.APPLY_BLOCKED_KEYS is never applied.
    keys = sorted({str(ed.get("path", "")).rsplit(".", 1)[-1] for ed in (proposal or {}).get("edits") or []
                   if isinstance(ed, dict)})
    out = [f"apply blocked for key {k!r} (replay cannot judge it)" for k in keys if k in C.APPLY_BLOCKED_KEYS]
    if rep is not None and rep.get("apply_blocked"):
        out.append("replay marked apply_blocked")
    out += [f"key {k!r} not in the RLPO allowlist" for k in keys if k not in C.PROPOSE_KEYS]
    out += [f"key {k!r} not in the APPLY allowlist {sorted(C.APPLY_KEYS)} (C11)" for k in keys if k not in C.APPLY_KEYS]
    if not keys:
        out.append("no edits")
    return out
