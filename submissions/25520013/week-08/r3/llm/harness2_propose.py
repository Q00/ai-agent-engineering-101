"""v120c-B Harness2 propose stage + v121 LLM policy editor (default OFF: HARNESS2_PROPOSE = False).

v121 (성수님 결정 CoS 전달 17:40): the meta-harness policy editor gets the LLM, inside guards enforced in code.
  propose_cycle(live, base, prompt, replay)       one proposal, no write (v120c-B interface, kept)
    live.editor(prompt) -> strict JSON -> H2 allowlist + range + step (guard_violations) -> harness.bounds.check
    -> replay.score(base, cand) -> n >= MIN_REPLAY_N and gain gate (cand - base >= MIN_GAIN_FRAC x |base|, > 0)
  run_cycle(live, runs, target, replay, now, ...)  propose_cycle on the registered target policy; a "win" is applied
    only through ops_llm.policy_gate (writer check + audit) and harness.rollback.apply_proposal (registers a version,
    atomic write), then a watch is opened. The writer "harness2" may NOT write the live policy.json (policy writers =
    RLPO path + approved deploy): on live this stage is propose-only; it applies to a sandbox / shadow policy file.
  watch_cycle(runs, target, observe, now, ...)     automatic rollback: after H2_WATCH_S the observed metric after the
    apply is compared with the same-length window before; no gain, too little data after H2_WATCH_MAX_S, or an
    observe error -> harness.rollback.rollback to the parent version.
Every step writes one decision-log row (ops_llm.DecisionLog: statuses, shas, numbers; never prompt / reply text).
LLM OFF / failure / caps breaker -> "llm_off" / "skip" (no proposal, nothing written). No call-count limit here; the
caps_v4 auto-OFF (usage limit, 429, auth, model, 3x error) is the only stop.
A plugs in any object with  score(base_policy: dict, candidate_policy: dict) -> {"base": float, "cand": float, "n": int}."""
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Protocol

from harness import bounds

HARNESS2_PROPOSE = False
MIN_REPLAY_N = 30
# ---- v121 code guards (stricter than harness.bounds: subset of keys, narrower ranges, smaller steps)
H2_ALLOW = {"min_margin": (0.0, 0.5), "concession": (0.0, 0.15), "max_rounds": (2, 8), "price_mult": (0.8, 1.3)}
H2_MAX_EDITS = 2
H2_MAX_STEP_FRAC = 0.10                # of the H2 range per edit (bounds: 0.25 of the base range)
MIN_GAIN_FRAC = 0.02                   # replay gain gate: cand must beat base by >= 2 % of |base| (and be > base)
H2_WATCH_S = 3 * 3600                  # watch window after an apply
H2_WATCH_MAX_S = 9 * 3600              # still too little data after this -> rollback (conservative)
H2_MIN_OBS_N = 10                      # observed events needed in each window
WRITER = "harness2"
STATE = Path(__file__).resolve().parent.parent / "state"


class ReplayProbe(Protocol):
    def score(self, base_policy: dict, candidate_policy: dict) -> dict: ...


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _sha(b):
    return hashlib.sha256(b if isinstance(b, bytes) else str(b).encode("utf-8")).hexdigest()


def guard_violations(base, proposal):
    # v121 G-allowlist: shop side only, key in H2_ALLOW, finite number inside its H2 range, step <= H2_MAX_STEP_FRAC
    if not isinstance(proposal, dict) or not isinstance(proposal.get("edits"), list):
        return ["proposal has no edits list"]
    eds = proposal["edits"]
    out = [] if 1 <= len(eds) <= H2_MAX_EDITS else [f"H2: 1..{H2_MAX_EDITS} edits"]
    kind = proposal.get("kind_hint")
    for i, e in enumerate(eds):
        if not isinstance(e, dict) or not isinstance(e.get("path"), str):
            out.append(f"H2: edit {i} malformed")
            continue
        p = e["path"]
        key = p.rsplit(".", 1)[-1]
        if p.startswith("buyer.") or key not in H2_ALLOW:
            out.append(f"H2: edit {i} key {key!r} not in the H2 allowlist")
            continue
        lo, hi = H2_ALLOW[key]
        v = _num(e.get("value"))
        if v is None or not lo <= v <= hi:
            out.append(f"H2: edit {i} {key} value outside [{lo}, {hi}]")
            continue
        parts = p.split(".")
        cur = None
        if len(parts) == 3 and isinstance(base, dict):
            cur = bounds._current(base, False, parts[0], parts[1], key, kind)
        cur = _num(cur)
        if cur is not None and abs(v - cur) > H2_MAX_STEP_FRAC * (hi - lo) + 1e-9:
            out.append(f"H2: edit {i} {key} step {abs(v - cur):g} > {H2_MAX_STEP_FRAC} x H2 range")
    return out


def build_prompt(base, metrics=None):
    # structured numbers only: current values of the allowlisted keys, H2 ranges, numeric metrics. No evidence text,
    # no forum / message text, no earlier LLM output.
    cur = {}
    for kind in bounds.KINDS:
        d = ((base or {}).get("defaults") or {}).get(kind) or {}
        cur[kind] = {k: _num(d.get(k)) for k in H2_ALLOW}
    met = {str(k)[:40]: _num(v) for k, v in (metrics or {}).items() if _num(v) is not None}
    return json.dumps({
        "task": "policy editor: answer strict JSON {kind_hint: food|goods, edits: [{path: defaults.<kind>.<key>, "
                "value: number}]} with 1..%d edits, or {edits: []} when no change is warranted. Inputs are numbers "
                "made by code." % H2_MAX_EDITS,
        "allowed": {k: list(v) for k, v in H2_ALLOW.items()}, "max_step_frac_of_range": H2_MAX_STEP_FRAC,
        "current": cur, "metrics": met}, sort_keys=True, ensure_ascii=False)


def propose_cycle(live, base_policy, prompt, replay=None, enabled=None, max_tokens=800):
    on = HARNESS2_PROPOSE if enabled is None else enabled
    if not on:
        return {"status": "off"}
    if live is None or not getattr(live, "on", False):
        return {"status": "llm_off"}
    if replay is None:
        return {"status": "no_replay"}
    out, st = live.editor(prompt, max_tokens)
    if out is None:
        return {"status": "skip", "reason": st}
    try:
        proposal = json.loads(out)
    except Exception:                                    # noqa: BLE001
        return {"status": "bad_format"}
    if not isinstance(proposal, dict) or not proposal.get("edits"):
        return {"status": "no_edit"}
    proposal = {"kind_hint": proposal.get("kind_hint"), "edits": proposal.get("edits")}   # drop free text
    g = guard_violations(base_policy, proposal)
    if g:
        return {"status": "not_allowed", "violations": g[:5]}
    v = bounds.check(base_policy, proposal)
    if v:
        return {"status": "out_of_bounds", "violations": v[:5]}
    cand = bounds.apply(base_policy, proposal)
    try:
        r = replay.score(base_policy, cand)
        b, c, n = float(r["base"]), float(r["cand"]), int(r["n"])
    except Exception as e:                               # noqa: BLE001
        return {"status": "replay_error", "error": type(e).__name__}
    if not (math.isfinite(b) and math.isfinite(c)):
        return {"status": "replay_error", "error": "non_finite"}
    if n < MIN_REPLAY_N:
        return {"status": "too_few", "n": n}
    win = c > b and (c - b) >= MIN_GAIN_FRAC * abs(b)
    return {"status": "win" if win else "lose", "base": b, "cand": c, "n": n, "proposal": proposal}


# ---------------- v121 apply / watch / rollback (all through harness.rollback, writer-gated, logged)
def _log(log, row):
    if log is not None:
        log.append(row)
    return row


def _load(state_path):
    try:
        return json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(state_path, d):
    p = Path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def run_cycle(live, runs, target, replay, now, metrics=None, enabled=None, log=None, state_path=None,
              writer=WRITER, live_policy=None, audit=None):
    from harness import rollback as RB
    from llm.ops_llm import PolicyWriteRefused, policy_gate
    on = HARNESS2_PROPOSE if enabled is None else enabled
    state_path = state_path or STATE / "h2_state.json"
    row = {"stage": "h2", "t": now, "target": str(target)}
    if not on:
        return _log(log, dict(row, status="off"))
    if _load(state_path).get("watch"):
        return _log(log, dict(row, status="watch_open"))   # one change at a time
    try:
        n = RB._check_live(runs, target, False)
    except Exception as e:                               # noqa: BLE001 - unregistered edit -> no-op, human
        return _log(log, dict(row, status="external_edit", error=type(e).__name__))
    raw = runs.policy_bytes(n)
    base = json.loads(raw)
    prompt = build_prompt(base, metrics)
    r = propose_cycle(live, base, prompt, replay, enabled=True)
    row.update({"status": r["status"], "version": n, "policy_sha": _sha(raw), "prompt_sha": _sha(prompt),
                "base": r.get("base"), "cand": r.get("cand"), "n": r.get("n"),
                "violations": r.get("violations"), "reason": r.get("reason")})
    if r["status"] != "win":
        return _log(log, row)
    prop = r["proposal"]
    row["edits"] = [{"path": e["path"], "value": e["value"]} for e in prop["edits"]]
    try:
        policy_gate.check_writer(writer, target, live_policy=live_policy, audit=audit, action="h2_apply",
                                 sha_before=_sha(raw))
    except PolicyWriteRefused:
        return _log(log, dict(row, status="write_refused"))
    try:
        v, new = RB.apply_proposal(runs, prop, target, now, note="h2 llm win base=%.6g cand=%.6g" % (r["base"], r["cand"]))
    except Exception as e:                               # noqa: BLE001
        return _log(log, dict(row, status="apply_error", error=type(e).__name__))
    _save(state_path, {"watch": {"version": v, "parent": n, "t": now, "base": r["base"], "cand": r["cand"]}})
    return _log(log, dict(row, status="applied", new_version=v, policy_sha_after=_sha(new)))


def watch_cycle(runs, target, observe, now, log=None, state_path=None, writer=WRITER, live_policy=None, audit=None):
    # observe(t_from, t_to) -> {"value": float, "n": int} realized metric of the target policy in [t_from, t_to)
    from harness import rollback as RB
    from llm.ops_llm import PolicyWriteRefused, policy_gate
    state_path = state_path or STATE / "h2_state.json"
    st = _load(state_path)
    w = st.get("watch")
    row = {"stage": "h2_watch", "t": now, "target": str(target)}
    if not w:
        return _log(log, dict(row, status="no_watch"))
    age = now - w["t"]
    if age < H2_WATCH_S:
        return _log(log, dict(row, status="watching", version=w["version"], age_s=age))
    why = None
    try:
        before = observe(w["t"] - age, w["t"])
        after = observe(w["t"], now)
        bv, av = _num(float(before["value"])), _num(float(after["value"]))
        bn, an = int(before["n"]), int(after["n"])
        row.update({"before": bv, "after": av, "n_before": bn, "n_after": an})
        if bv is None or av is None:
            why = "non_finite"
        elif min(bn, an) < H2_MIN_OBS_N:
            if age < H2_WATCH_MAX_S:
                return _log(log, dict(row, status="watching", version=w["version"], age_s=age))
            why = "too_little_data"
        elif av <= bv:
            why = "no_gain"
    except Exception as e:                               # noqa: BLE001 - cannot observe -> roll back
        why = "observe_error:" + type(e).__name__
    if why is None:
        _save(state_path, {})
        return _log(log, dict(row, status="verified", version=w["version"]))
    try:
        policy_gate.check_writer(writer, target, live_policy=live_policy, audit=audit, action="h2_rollback")
        rv, _ = RB.rollback(runs, w["parent"], target, now, note=f"h2 auto rollback: {why}")
    except PolicyWriteRefused:
        return _log(log, dict(row, status="rollback_refused", reason=why))
    except Exception as e:                               # noqa: BLE001 - keep the watch open, alarm in the log
        return _log(log, dict(row, status="rollback_error", reason=why, error=type(e).__name__))
    _save(state_path, {})
    return _log(log, dict(row, status="rolled_back", reason=why, version=w["version"], rollback_version=rv))
