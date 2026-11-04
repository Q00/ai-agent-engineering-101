# M2r CoS approval path (owner 15:59 via CoS: approver = CoS, proposals one at a time) and the post-apply watch.
# decide(): the ONLY place that may apply, and only a pending proposal approved by the CoS whose keys are all in
#   the APPLY allowlist C.APPLY_KEYS (C11; empty in M2r, so every approval is refused at the gate and recorded).
# watch(): rollback target = the last VERIFIED version, never a rolled-back one (C3); live obs only (C8);
#   a failed write / external edit -> OFF latch + alarm, no ghost version (C1, C2); counters persisted (C4).
import json

from rlpo import breaker as BK
from rlpo import config as C
from rlpo import deps, guard, safe_rb
from rlpo import state as S


class Ctx:
    def __init__(self, runs, live, log, store, alarm_path):
        self.runs, self.live, self.log, self.store, self.alarm_path = runs, live, log, store, alarm_path


def _row(cx, t, a, status, reason=None, **kw):
    s = S.summarize(t, None, {}, {}, {}, policy_sha=safe_rb.sha(cx.live.read_bytes()) if cx.live.exists() else "")
    row = {"id": cx.log.next_id(), "t": t, "s_sha": S.sha(s), "s": s, "a": a, "a_status": status,
           "reject_reason": reason, "replay": None, "r_proxy": None, "r_total": None, "gamma_t": None,
           "policy_sha_before": None, "policy_sha_after": None, "model": None, "tokens_in": None,
           "tokens_out": None, "latency_ms": None}
    row.update(kw)
    return cx.log.append(row)


def _alarm(cx, t, a, reason, **kw):
    with open(cx.alarm_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": t, "reason": reason, **kw}, ensure_ascii=False, default=str) + "\n")
    cx.store.latch_off(reason, t)
    return _row(cx, t, a, "alarm", reason, replay=kw or None)


def preflight(st, t, deps_lock=None):
    # L1: [] or reasons; shared by the CoS path (decide) and the LLM approver (rlpo.approver)
    if st.get("_bad"):
        reason = st.get("off_reason")
        return [f"state fail-closed: {reason}"]
    out = []
    if not st.get("enabled"):
        reason = st.get("off_reason")
        out.append(f"rlpo off: {reason}")
    miss = [g for g in C.START_GATES if not (st.get("gates") or {}).get(g)]
    if miss:
        out.append(f"start gates not passed: {miss}")
    try:
        deps.verify(C.DEPS_LOCK if deps_lock is None else deps_lock)
    except deps.DepsMismatch as e:
        out.append(f"C9 deps: {e}")
    ok, why = BK.allow(st.get("breaker") or {}, t)
    if not ok:
        out.append(why)
    return out


def pending_ok(st, pid, live_raw, t):
    # L2: the pending proposal must be this pid, made on the live bytes, and not older than APPROVAL_TTL_S
    p = st.get("pending")
    if not p or p.get("id") != pid:
        return [f"proposal #{pid} is not the pending proposal"]
    out = []
    if p.get("policy_sha") != safe_rb.sha(live_raw):
        out.append("L2: live policy changed since the proposal (policy_sha mismatch)")
    if not isinstance(p.get("t"), (int, float)) or t - p["t"] > C.APPROVAL_TTL_S or t < p["t"]:
        out.append(f"L2: approval expired (ttl {C.APPROVAL_TTL_S}s)")
    return out


def decide(cx, pid, decision, by, channel, ref, t, t_rem=None):
    if by != C.APPROVER or channel not in C.APPROVAL_CHANNELS or not ref:
        raise PermissionError(f"decision needs approver {C.APPROVER} via {sorted(C.APPROVAL_CHANNELS)} with a ref")
    st = cx.store.load()
    if st.get("_bad"):
        raise RuntimeError(st["off_reason"])
    p = st.get("pending")
    if not p or p["id"] != pid:
        raise ValueError(f"proposal #{pid} is not the pending proposal")
    a = p["a"]
    st["pending"] = None
    st.setdefault("approvals", []).append({"t": t, "pid": pid, "decision": decision, "by": by, "channel": channel,
                                           "ref": ref})
    cx.store.save(st)
    if decision != "approve":
        return _row(cx, t, a, "rejected", f"CoS rejected #{pid} ({ref})")
    if C.L_APPROVER_ENABLED:
        return _row(cx, t, a, "rejected", ["L3: in L mode approvals come from the approver LLM, not a human"])
    live_raw = cx.live.read_bytes()
    n_reg = cx.runs.latest()
    if n_reg is None or cx.runs.policy_bytes(n_reg) != live_raw:
        return _alarm(cx, t, a, f"C2 external edit before apply: live != registered v{n_reg}; nothing written, RLPO OFF, human")
    why = preflight(st, t) + [w for w in pending_ok(dict(st, pending=p), pid, live_raw, t)]
    if why:
        return _row(cx, t, a, "rejected", ["approved by CoS but refused (L1/L2)"] + why)
    base = json.loads(live_raw)
    why = guard.applicable(a) + guard.check(base, a, st.get("anchor"), cx.log.rows(), t, t_rem, st.get("open_watch"))
    if why:
        return _row(cx, t, a, "rejected", ["approved by CoS but refused by the apply gate"] + why)
    parent = st["verified"][-1]
    try:
        v = safe_rb.apply(cx.runs, a, cx.live, t, note=f"rlpo #{pid} approved by CoS {ref}")
    except safe_rb.ExternalEdit as e:
        return _alarm(cx, t, a, f"C2 external edit before apply: {e}; nothing written, RLPO OFF, human")
    except safe_rb.WriteFailed as e:
        return _alarm(cx, t, a, f"C1 apply write failed: {e}; RLPO OFF, human", reconcile=e.info)
    st = cx.store.load()
    st["open_watch"] = {"version": v, "t": t, "parent_verified": parent, "pid": pid}
    cx.store.save(st)
    return _row(cx, t, a, "applied", f"CoS {ref}", replay={"version": v, "parent_verified": parent})


def watch(cx, t, obs):
    # obs = {source: live, point, lo, hi, n, loss, base_balance, concurrent}
    if not C.LIVE_OBS_READY or (obs or {}).get("source") != "live":
        raise ValueError("C8: watch needs live observations (LIVE_OBS_READY and obs source live)")
    st = cx.store.load()
    ow = st.get("open_watch")
    if not ow:
        return None
    from experiments import prereg
    reason = prereg.over_cap(obs.get("loss", 0.0), obs["base_balance"], obs.get("concurrent", ()))
    if reason is None and obs.get("hi") is not None and obs["hi"] < 0 and obs.get("n", 0) >= C.MIN_REPLAY_N:
        reason = "proxy CI hi < 0"
    if reason is None:
        if t - ow["t"] >= C.WATCH_S:
            st["verified"].append(ow["version"])
            st["open_watch"] = None
            st["consec_rb"] = 0
            cx.store.save(st)
        return None
    target = next((v for v in reversed(st["verified"]) if v not in st.get("bad", [])), None)
    if target is None:
        return _alarm(cx, t, None, "no verified version to roll back to; RLPO OFF, human")
    try:
        rv = safe_rb.rollback(cx.runs, target, cx.live, t, note=f"rlpo: {reason}")
    except safe_rb.ExternalEdit as e:
        return _alarm(cx, t, None, f"C2 external edit during watch of v{ow['version']}: {e}; no rollback written "
                                   f"(external edit kept), RLPO OFF, human")
    except safe_rb.WriteFailed as e:
        return _alarm(cx, t, None, f"C1 rollback write failed for v{ow['version']}: {e}; bad policy may be live, "
                                   f"RLPO OFF, human", reconcile=e.info)
    st = cx.store.load()
    st.setdefault("bad", []).append(ow["version"])
    st["open_watch"] = None
    st["consec_rb"] = st.get("consec_rb", 0) + 1
    cx.store.save(st)
    if st["consec_rb"] >= C.MAX_CONSEC_ROLLBACKS:
        cx.store.latch_off(f"{st['consec_rb']} consecutive rollbacks", t)
    # L2 (found by test_ca10[cooldown]): the rolled_back row carries the proposal that was rolled back, so the
    # guard.check rollback cooldown (which matches edit paths of rolled_back rows) applies to it. Before: a=None.
    a_rb = next((r.get("a") for r in reversed(cx.log.rows()) if r.get("a_status") == "applied"
                 and isinstance(r.get("replay"), dict) and r["replay"].get("version") == ow["version"]), None)
    return _row(cx, t, a_rb, "rolled_back", reason, replay={"rolled_back": ow["version"], "target": target,
                                                            "rollback_version": rv})


def watch_cycle(cx, t, source):
    # B1 operating path: called once per cycle (rlpo.l_ops.step) BEFORE any new proposal. source() -> (sales, balance)
    # from the live record source. No open watch -> None. Open watch without live observation -> C8 alarm + OFF
    # (fail-closed: a watch that cannot run must not stay open silently).
    st = cx.store.load()
    if st.get("_bad"):
        return None
    ow = st.get("open_watch")
    if not ow:
        return None
    if t - ow["t"] < C.PERIOD_S:
        return None                                   # first complete hour not reached yet
    if not C.LIVE_OBS_READY:
        return _alarm(cx, t, None, f"C8: watch of v{ow['version']} open but LIVE_OBS_READY is False; RLPO OFF, human")
    from rlpo import live_obs
    try:
        sales, balance = source()
        obs, why = live_obs.observe(sales, ow["t"], t, balance)
    except Exception as e:  # any failure of the live source -> OFF, never a silent pass
        obs, why = None, f"C8 source error {type(e).__name__}: {e}"[:300]
    if obs is None:
        return _alarm(cx, t, None, f"C8 live observation failed during watch of v{ow['version']}: {why}; RLPO OFF, human")
    return watch(cx, t, obs)
