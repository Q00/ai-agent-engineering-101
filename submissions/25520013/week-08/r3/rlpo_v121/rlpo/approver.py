# L2 approver (CR REVIEW_M2r2_L 485bb16c). LLM approver inside the harness: 성수님 16:28 decision (CoS 전달 16:29), which
# replaces the 15:59 decision (approver CoS, auto apply permanently OFF) for the approver part. Default OFF.
# Safety = code guards (G1 range, guard.applicable, guard.check, G2 replay win) + G3 rollback through the C8 watch;
# the approver LLM can only ADD a rejection (CR 4.1), it is not an independent check.
# Order in run(): L7 switches -> L5 live entry -> B1 LIVE_OBS_READY, L-C5 freeze (T_rem < 3 h), B3 L gate + file lock,
#   L-C3 approver model -> L1 preflight -> C2 live == registered -> L2 pending (consumed) + in-flight marker
#   -> fresh replay -> guards (LLM not asked when they fail) -> approver LLM with STRUCTURED NUMBERS ONLY (no proposer
#   text, CoS 17:05) -> _apply (internal to run; every check again, then safe_rb.apply + open_watch, G4 log, summary).
# B2: a Verdict binds pid, policy_sha, sha(proposal), sha(replay), t_issue, nonce, decision, resp_sha, by under an HMAC
#   with a per-process key. This is IN-PROCESS INTEGRITY, not forgery protection or a security boundary: any code in
#   the same process can call _mac, and other agents on the box can edit files. Single use (nonce stored in the state
#   before the write), TTL C.VERDICT_TTL_S, and only for the in-flight proposal of the current run.
#   The write-time guard re-check uses the SAME replay object the approver saw (not new evidence; documented).
import hashlib
import hmac
import json
import math
import os
import re
import time

from rlpo import approval as AP
from rlpo import breaker as BK
from rlpo import config as C
from rlpo import guard, l_lock, live_entry, reward, safe_rb

_KEY = os.urandom(32)
FIELDS = ("pid", "policy_sha", "a_sha", "rep_sha", "t_issue", "nonce", "decision", "resp_sha", "by")
REP_KEYS = ("point", "lo_adj", "n", "n_blocks", "verdict", "money_frac", "data_hours")
PERF_KEYS = ("gp_per_h_24h", "gp_sd_h_24h", "sales_24h", "last_row_age_s")
PROMPT_KEYS = ("horizon", "proposal", "recent_live", "replay", "same_key_losses", "task")
STATUSES = frozenset({"proposed", "rejected", "applied", "rolled_back"})
PATH_RE = re.compile(r"^[a-z_]+(\.[a-z_]+)*$")
WORD_RE = re.compile(r"^[a-z_]{1,20}$")
NONCES_KEPT = 1000


class Verdict:
    __slots__ = FIELDS + ("reason", "mac")


def csha(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False, default=str,
                                     separators=(",", ":")).encode()).hexdigest()


def _mac(*vals):
    return hmac.new(_KEY, json.dumps(list(vals), default=str).encode(), hashlib.sha256).hexdigest()


def genuine(v):
    # L3: minted by _ask in this process (in-process integrity only, see header)
    return (type(v) is Verdict and v.by == C.APPROVER_LLM
            and hmac.compare_digest(str(getattr(v, "mac", "")), _mac(*[getattr(v, f, None) for f in FIELDS])))


def range_violations(a):
    # G1: every edited key must be in C.L_APPLY_RANGES and the value a real number inside its code-fixed range
    out = []
    edits = (a or {}).get("edits") or []
    if not edits:
        out.append("G1: no edits")
    for ed in edits:
        if not isinstance(ed, dict):
            out.append("G1: bad edit")
            continue
        k = str(ed.get("path", "")).rsplit(".", 1)[-1]
        rg = C.L_APPLY_RANGES.get(k)
        v = ed.get("value")
        if rg is None:
            out.append(f"G1: key {k!r} not in the L apply allowlist {sorted(C.L_APPLY_RANGES)}")
        elif isinstance(v, bool) or not isinstance(v, (int, float)) or not (rg[0] <= v <= rg[1]):
            out.append(f"G1: {k}={v!r} outside {list(rg)}")
    return out


def guards(cx, st, a, live_raw, rep, t, t_rem):
    base = json.loads(live_raw)
    out = range_violations(a) + guard.applicable(a, rep)
    out += guard.check(base, a, st.get("anchor"), cx.log.rows(), t, t_rem, st.get("open_watch"))
    if not guard.wins(rep):
        out.append("G2: no confirmed replay gain under C7 (P2)")
    return out


def _t_rem(t, t_rem):
    return (C.T_END - t) if t_rem is None else t_rem


def l_preflight(st, t, t_rem):
    # B1, L-C5, B3: [] or reasons
    out = []
    if C.LIVE_OBS_READY is not True:
        out.append("B1: LIVE_OBS_READY is False (C8 live observation not passed CR): no apply")
    tr = _t_rem(t, t_rem)
    if not isinstance(tr, (int, float)) or tr < C.L_FREEZE_S:
        out.append(f"L-C5: T_rem {tr} s < {C.L_FREEZE_S} s: applies are frozen")
    miss = [g for g in C.L_START_GATES if not (st.get("gates") or {}).get(g)]
    if miss:
        out.append(f"B3: L start gate not passed: {miss}")
    out += l_lock.violations()
    return out


def model_violations(llm):
    # L-C3: the approver model is configured and differs from the proposer model
    m = getattr(llm, "model", None)
    if m is None or C.APPROVER_MODEL is None or m != C.APPROVER_MODEL:
        return [f"L-C3: approver model {m!r} is not the configured approver model {C.APPROVER_MODEL!r}"]
    if C.APPROVER_SAME_MODEL_OK is True and C.PROPOSER_MODEL is not None and m == C.PROPOSER_MODEL:
        return same_model_violations(llm)     # v121b (CoS 결정 18:02): same model only inside the code guards
    if C.PROPOSER_MODEL is None or m == C.PROPOSER_MODEL:
        return [f"L-C3: approver model must differ from the proposer model {C.PROPOSER_MODEL!r}"]
    return []


# v121b (CoS 결정 18:02): same-model approver (gpt-6.1-terra for proposer and approver) is allowed ONLY when
#   (1) the approver input is independent: PROMPT_KEYS unchanged and every prompt value is a code-made number / short
#       code word (no proposer text, no evidence source text) - checked on the real prompt right before the call;
#   (2) a new session per call: llm.fresh_session is True (codex exec --ephemeral, a new process per call);
#   (3) a separate role: llm.role == C.APPROVER_ROLE (own counter / breaker, never the proposer client);
#   (4) range check (G1 range_violations) and replay gate (G2 guard.wins) run BEFORE the approver in run() - unchanged.
SAME_MODEL_PROMPT_KEYS = ("horizon", "proposal", "recent_live", "replay", "same_key_losses", "task")
_FREE_TEXT_MAX = 64


def same_model_violations(llm):
    out = []
    if tuple(PROMPT_KEYS) != SAME_MODEL_PROMPT_KEYS:
        out.append("L-C3s: approver PROMPT_KEYS changed")
    if getattr(llm, "fresh_session", None) is not True:
        out.append("L-C3s: same-model approver needs a new session per call (fresh_session True)")
    if getattr(llm, "role", None) != C.APPROVER_ROLE:
        out.append(f"L-C3s: same-model approver must use role {C.APPROVER_ROLE!r}")
    return out


def _task_text():
    return json.loads(_prompt(None, {}))["task"]


def prompt_violations(prompt):
    # v121b: the real approver prompt holds only PROMPT_KEYS, the fixed task line and code-made numbers / short words
    try:
        d = json.loads(prompt)
    except (TypeError, ValueError):
        return ["L-C3s: approver prompt is not JSON"]
    if not isinstance(d, dict) or tuple(sorted(d)) != tuple(sorted(SAME_MODEL_PROMPT_KEYS)):
        return ["L-C3s: approver prompt keys differ from PROMPT_KEYS"]
    if d.get("task") != _task_text():
        return ["L-C3s: approver task line changed"]
    bad = []

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            if len(x) > _FREE_TEXT_MAX or not (PATH_RE.match(x) or WORD_RE.match(x)):   # paths <= 64, words <= 20
                bad.append(x[:12])
        elif not (x is None or isinstance(x, (bool, int, float))):
            bad.append(type(x).__name__)
    walk({k: v for k, v in d.items() if k != "task"})
    return [f"L-C3s: approver prompt has free text ({len(bad)})"] if bad else []


def same_model(llm):
    return (C.APPROVER_SAME_MODEL_OK is True and C.PROPOSER_MODEL is not None
            and getattr(llm, "model", None) == C.PROPOSER_MODEL)


# ---------------- structured approver input (CoS 17:05): numbers from code only, never proposer text
def _num(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return x if math.isfinite(x) else None


def _word(x):
    return x if isinstance(x, str) and WORD_RE.match(x) else None


def _keys(a):
    return {str(e.get("path", "")).rsplit(".", 1)[-1] for e in (a or {}).get("edits") or [] if isinstance(e, dict)}


def _edits(a, base):
    kind = (a or {}).get("kind_hint")
    out = []
    for ed in (a or {}).get("edits") or []:
        if not isinstance(ed, dict):
            continue
        p = ed.get("path")
        p = p if isinstance(p, str) and PATH_RE.match(p) else None
        cur = guard._base_val(base, p, kind) if (p and kind in ("food", "goods")) else None
        out.append({"path": p, "value": _num(ed.get("value")), "current": _num(cur)})
    return out


def same_key_losses(rows, keys, t, limit=10):
    # past losing records for the same key: rolled back applies, or proposals / decisions whose replay lost
    applied = {}
    for r in rows:
        rp = r.get("replay") if isinstance(r.get("replay"), dict) else {}
        if r.get("a_status") == "applied" and rp.get("version") is not None:
            applied[rp["version"]] = r.get("a")
    out = []
    for r in rows:
        rp = r.get("replay") if isinstance(r.get("replay"), dict) else {}
        a = r.get("a")
        if r.get("a_status") == "rolled_back":
            a = applied.get(rp.get("rolled_back"))
        if not (_keys(a) & keys):
            continue
        inner = rp.get("replay") if isinstance(rp.get("replay"), dict) else rp
        point, lo = _num(inner.get("point")), _num(inner.get("lo_adj"))
        if r.get("a_status") == "rolled_back" or (point is not None and point < 0) or (lo is not None and lo <= 0):
            out.append({"age_s": _num(t - r["t"]) if _num(r.get("t")) is not None else None,
                        "status": r.get("a_status") if r.get("a_status") in STATUSES else None,
                        "point": point, "lo_adj": lo})
    return out[-limit:]


def _prompt(a, rep, base=None, rows=(), t=0.0, t_rem=None, live_perf=None):
    tr = _t_rem(t, t_rem)
    kind = (a or {}).get("kind_hint")
    perf = None
    if isinstance(live_perf, dict):
        perf = {k: _num(live_perf.get(k)) for k in PERF_KEYS}
    return json.dumps({
        "task": "approver: answer strict JSON {decision: approve|reject, reason}. Every input below is a number made "
                "by code (no proposer text). The code guards already passed; reject when unsure.",
        "proposal": {"kind_hint": kind if kind in ("food", "goods") else None, "edits": _edits(a, base or {})},
        "replay": {k: (_word(rep.get(k)) if k == "verdict" else _num(rep.get(k))) for k in REP_KEYS},
        "recent_live": perf,
        "same_key_losses": same_key_losses(list(rows), _keys(a), t),
        "horizon": {"gamma_t": _num(reward.gamma(tr)), "t_rem_s": _num(tr),
                    "horizon_cycles": _num(tr / C.GAMMA_DT_S) if _num(tr) is not None else None},
    }, sort_keys=True, ensure_ascii=False)


def _ask(cx, llm, pid, a, rep, policy_sha, t, prompt=None):
    # one approver LLM call through the breaker; returns (Verdict or None, meta, error)
    st = cx.store.load()
    ok, why = BK.allow(st.get("breaker") or {}, t)
    if not ok:
        return None, {}, why
    t0 = time.monotonic()
    try:
        out = llm.complete(_prompt(a, rep, t=t) if prompt is None else prompt)
    except Exception as e:
        st = cx.store.load()
        kind = BK.record_error(st.setdefault("breaker", {}), t, e)
        cx.store.save(st)
        return None, {}, f"approver llm error [{kind}] {type(e).__name__}: {e}"[:300]
    st = cx.store.load()
    BK.record_ok(st.setdefault("breaker", {}), t, out.get("tokens_in"), out.get("tokens_out"))
    cx.store.save(st)
    meta = {"tokens_in": out.get("tokens_in"), "tokens_out": out.get("tokens_out"),
            "latency_ms": int((time.monotonic() - t0) * 1000), "model": getattr(llm, "model", None)}
    try:
        d = json.loads(out["text"])
        decision, reason = d["decision"], str(d.get("reason", ""))[:1000]
    except (ValueError, TypeError, KeyError) as e:
        return None, meta, f"approver reply unparseable: {type(e).__name__}"
    if decision not in ("approve", "reject"):
        return None, meta, f"approver decision {decision!r} not approve/reject"
    v = Verdict()
    v.pid, v.policy_sha, v.a_sha, v.rep_sha = pid, policy_sha, csha(a), csha(rep)
    v.t_issue, v.nonce, v.decision, v.reason, v.by = t, os.urandom(16).hex(), decision, reason, C.APPROVER_LLM
    v.resp_sha = hashlib.sha256(str(out["text"]).encode()).hexdigest()
    v.mac = _mac(*[getattr(v, f) for f in FIELDS])
    return v, meta, None


def _log(cx, t, a, status, reason, **kw):
    return AP._row(cx, t, a, status, reason, **kw)


def _outbox(cx, t, body):
    p = cx.store.path.with_name("cos_outbox.jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict({"to": C.APPROVER, "to_id": C.APPROVER_ID, "kind": "apply_summary", "t": t}, **body),
                           ensure_ascii=False, default=str) + "\n")


def _clear_inflight(cx, pid):
    st = cx.store.load()
    if not st.get("_bad") and (st.get("l_inflight") or {}).get("pid") == pid:
        st["l_inflight"] = None
        cx.store.save(st)


def run(cx, pid, llm, replay, t, t_rem=None, deps_lock=None, live_perf=None):
    # the only L-mode apply path. Returns the experience row.
    if not (C.AUTO_APPLY is True and C.L_APPROVER_ENABLED is True):
        return _log(cx, t, None, "rejected", "L7: approver disabled (AUTO_APPLY and L_APPROVER_ENABLED must both be True)")
    live_entry.check(replay)
    st = cx.store.load()
    why = l_preflight(st, t, t_rem) + model_violations(llm)
    if why:
        return _log(cx, t, None, "rejected", ["L2 preflight"] + why)
    why = AP.preflight(st, t, deps_lock)
    if why:
        return _log(cx, t, None, "rejected", ["L1"] + why)
    live_raw = cx.live.read_bytes()
    n_reg = cx.runs.latest()
    if n_reg is None or cx.runs.policy_bytes(n_reg) != live_raw:
        return AP._alarm(cx, t, (st.get("pending") or {}).get("a"), f"C2 external edit before apply: live != registered v{n_reg}; nothing written, RLPO OFF, human")
    why = AP.pending_ok(st, pid, live_raw, t)
    p = st.get("pending") or {}
    a = p.get("a")
    if why:
        if p.get("id") == pid:
            st["pending"] = None
            cx.store.save(st)
        return _log(cx, t, a, "rejected", ["L2"] + why)
    st["pending"] = None                      # one at a time: the pending slot is consumed whatever happens next
    st["l_inflight"] = {"pid": pid, "a_sha": csha(a), "t": t}
    cx.store.save(st)
    try:
        from harness import bounds
        base = json.loads(live_raw)
        rep = replay(base, bounds.apply(base, a), t)
        sha0 = safe_rb.sha(live_raw)
        why = guards(cx, st, a, live_raw, rep, t, t_rem)
        rec = {"proposal": a, "replay": rep, "policy_sha_before": sha0}
        if why:                               # guards first: the approver LLM is not even asked
            return _log(cx, t, a, "rejected", why, replay=rec)
        prompt = _prompt(a, rep, base, cx.log.rows(), t, t_rem, live_perf)
        why = prompt_violations(prompt) if same_model(llm) else []     # v121b: same-model input check, no LLM call
        if why:
            return _log(cx, t, a, "rejected", why, replay=rec)
        v, meta, err = _ask(cx, llm, pid, a, rep, sha0, t, prompt)
        st = cx.store.load()
        if not BK.allow(st.get("breaker") or {}, t)[0] and st.get("breaker", {}).get("latched"):
            cx.store.latch_off(f"approver llm breaker latched: {err}", t)
        if v is None:
            return _log(cx, t, a, "rejected", f"approver failed, nothing applied: {err}", replay=rec, **meta)
        rec["approver"] = {"decision": v.decision, "reason": v.reason, "resp_sha": v.resp_sha, "by": v.by,
                           "model": meta.get("model"), "prompt_sha": hashlib.sha256(prompt.encode()).hexdigest()}
        if v.decision != "approve":
            return _log(cx, t, a, "rejected", f"approver LLM rejected: {v.reason}"[:500], replay=rec, **meta)
        return _apply(cx, v, a, rep, t, t_rem, rec, meta, deps_lock)
    finally:
        _clear_inflight(cx, pid)


def _apply(cx, v, a, rep, t, t_rem, rec, meta, deps_lock=None):
    # internal to run(): needs the in-flight marker that only run() writes (B2)
    if not genuine(v) or v.decision != "approve":
        return _log(cx, t, a, "rejected", "L3: verdict not from the approver LLM (MAC check failed)", replay=rec)
    if not (C.AUTO_APPLY is True and C.L_APPROVER_ENABLED is True):
        return _log(cx, t, a, "rejected", "L7: approver disabled", replay=rec)
    st = cx.store.load()
    live_raw = cx.live.read_bytes()
    n_reg = cx.runs.latest()
    if (safe_rb.sha(live_raw) != v.policy_sha or n_reg is None or cx.runs.policy_bytes(n_reg) != live_raw):
        return AP._alarm(cx, t, a, "C2 (L-C4) live policy changed between the approver decision and the write; "
                                   "nothing written, RLPO OFF, human")
    why = AP.preflight(st, t, deps_lock) + l_preflight(st, t, t_rem)
    inf = st.get("l_inflight") or {}
    if inf.get("pid") != v.pid or inf.get("a_sha") != v.a_sha:
        why.append("B2: verdict is not for the in-flight proposal of this run")
    if csha(a) != v.a_sha:
        why.append("B2: proposal differs from the one the approver saw")
    if csha(rep) != v.rep_sha:
        why.append("B2: replay differs from the one the approver saw")
    if not isinstance(v.t_issue, (int, float)) or not 0 <= t - v.t_issue <= C.VERDICT_TTL_S:
        why.append(f"B2: verdict expired (ttl {C.VERDICT_TTL_S} s)")
    if v.nonce in (st.get("used_nonces") or []):
        why.append("B2: verdict already used (nonce)")
    why += guards(cx, st, a, live_raw, rep, t, t_rem)          # same replay object as the decision (not new evidence)
    if why:
        return _log(cx, t, a, "rejected", ["approved by the approver LLM but refused by the guards"] + why, replay=rec)
    st["used_nonces"] = ((st.get("used_nonces") or []) + [v.nonce])[-NONCES_KEPT:]
    st["l_inflight"] = None
    cx.store.save(st)
    parent = st["verified"][-1]
    try:
        ver = safe_rb.apply(cx.runs, a, cx.live, t, note=f"rlpo #{v.pid} approved by approver LLM {v.resp_sha[:12]}")
    except safe_rb.ExternalEdit as e:
        return AP._alarm(cx, t, a, f"C2 external edit before apply: {e}; nothing written, RLPO OFF, human")
    except safe_rb.WriteFailed as e:
        return AP._alarm(cx, t, a, f"C1 apply write failed: {e}; RLPO OFF, human", reconcile=e.info)
    st = cx.store.load()
    st["open_watch"] = {"version": ver, "t": t, "parent_verified": parent, "pid": v.pid}
    cx.store.save(st)
    sha1 = safe_rb.sha(cx.live.read_bytes())
    rec.update(version=ver, parent_verified=parent, policy_sha_after=sha1)
    _outbox(cx, t, {"proposal_id": v.pid, "a": a, "version": ver, "policy_sha_before": rec["policy_sha_before"],
                    "policy_sha_after": sha1, "replay": {k: rep.get(k) for k in ("point", "lo_adj", "n", "n_blocks")},
                    "approver_reason": v.reason, "watch_s": C.WATCH_S})
    return _log(cx, t, a, "applied", f"approver LLM {v.resp_sha[:12]}", replay=rec,
                policy_sha_before=rec["policy_sha_before"], policy_sha_after=sha1, **meta)
