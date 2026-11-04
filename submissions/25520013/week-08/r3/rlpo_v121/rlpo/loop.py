# M2r Harness^2 loop - PROPOSAL-ONLY (성수님 15:59 결정, CoS 전달: the automatic apply path stays OFF permanently).
# s -> LLM proposal -> guard -> causal replay (info) -> "proposed" row + one pending proposal for the CoS.
# This module has NO apply or rollback code path (S1): it never imports harness.rollback or rlpo.safe_rb writers.
# A CoS decision on the pending proposal is handled by rlpo.approval (one at a time; apply allowlist C11).
# Persistent state (rlpo.rstate): OFF latch, anchor, breaker, gates, pending - fail-closed (C4).
import hashlib
import json
import math
import re
import time
from pathlib import Path

from rlpo import breaker as BK
from rlpo import config as C
from rlpo import deps, guard, l_lock, live_entry, reward, rstate, state as S


_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
_PATH_RE = re.compile(r"^[a-z_]+(\.[a-z_]+)*$")
_REP_NUM = ("point", "lo_adj", "hi", "n", "n_blocks", "money_frac", "data_hours", "version", "rolled_back")
_REP_WORD = ("verdict",)


def _n(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _brief_a(a):
    # v121: only code-checkable structure of a past proposal (rationale dropped)
    if not isinstance(a, dict):
        return None
    k = a.get("kind_hint")
    eds = []
    for e in (a.get("edits") or [])[:3]:
        if isinstance(e, dict):
            p = e.get("path")
            eds.append({"path": p if isinstance(p, str) and _PATH_RE.match(p) else None, "value": _n(e.get("value"))})
    return {"kind_hint": k if k in ("food", "goods") else None, "edits": eds}


def _brief_rep(rp):
    if not isinstance(rp, dict):
        return None
    out = {k: _n(rp.get(k)) for k in _REP_NUM if k in rp}
    out.update({k: rp.get(k) if isinstance(rp.get(k), str) and re.match(r"^[a-z_]{1,20}$", rp.get(k)) else None
                for k in _REP_WORD if k in rp})
    if isinstance(rp.get("replay"), dict):
        out["replay"] = _brief_rep(rp["replay"])
    return out


def _safe_state(s):
    # v121: memory rows keep kind / key and a / b only when they are short identifiers (no evidence text)
    if not isinstance(s, dict):
        return s
    s = dict(s)
    mem = []
    for m in s.get("memory") or []:
        if isinstance(m, dict):
            mem.append({k: (m.get(k) if isinstance(m.get(k), str) and _ID_RE.match(m.get(k)) else
                            (_n(m.get(k)) if not isinstance(m.get(k), str) else None)) for k in ("kind", "key", "a", "b")})
    s["memory"] = mem
    return s


class Loop:
    def __init__(self, llm, runs, live_path, log, replay, store, alarm_path=None, outbox_path=None, t_total=None,
                 t_end=None, deps_lock=None):
        self.llm, self.runs, self.live, self.log, self.replay = llm, runs, Path(live_path), log, replay
        self.store = store if isinstance(store, rstate.Store) else rstate.Store(store)
        self.llm_day = None                # P1: last daily LLM report (set each cycle)
        self.alarm_path = Path(alarm_path) if alarm_path else self.store.path.with_name("rlpo_alarms.jsonl")
        self.outbox = Path(outbox_path) if outbox_path else self.store.path.with_name("cos_outbox.jsonl")
        self.t_total = t_total if t_total is not None else C.T_TOTAL_S
        self.t_end = t_end if t_end is not None else C.T_END
        self.deps_lock = deps_lock if deps_lock is not None else C.DEPS_LOCK
        live_entry.check(replay)           # L5: LIVE_OK must be True (no fail-open on a missing attribute)
        reward.check_constants()           # RL constants (gamma, weights, lags) are code-pinned (DESIGN_v3 sec 3)
        if C.L_APPROVER_ENABLED:
            bad = l_lock.violations()         # B3: config / approver / guard shas must match l_lock.json in L mode
            if bad:
                raise RuntimeError(bad)

    @property
    def enabled(self):
        return bool(self.store.load().get("enabled"))

    def _sha(self):
        try:
            return hashlib.sha256(self.live.read_bytes()).hexdigest()
        except OSError:
            return None

    def _row(self, t, s, a, status, **kw):
        row = {"id": self.log.next_id(), "t": t, "s_sha": S.sha(s), "s": s, "a": a, "a_status": status,
               "reject_reason": None, "replay": None, "r_proxy": None, "r_total": None,
               "gamma_t": reward.gamma_at(t), "horizon_cycles": reward.horizon_cycles(t),
               "policy_sha_before": self._sha(), "policy_sha_after": None, "model": getattr(self.llm, "model", None),
               "tokens_in": None, "tokens_out": None, "latency_ms": None}
        row.update(kw)
        if row["policy_sha_after"] is None:
            row["policy_sha_after"] = self._sha()
        return self.log.append(row)

    def _alarm(self, t, s, reason):
        with open(self.alarm_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": t, "reason": reason}, ensure_ascii=False) + "\n")
        self.store.latch_off(reason, t)
        return self._row(t, s, None, "alarm", reject_reason=reason)

    def prompt(self, s, base):
        # v121 (성수님 결정 CoS 전달 17:40): no evidence source text and no earlier LLM free text in the prompt.
        # history = kind_hint + edits (path, number) + status + replay numbers (no rationale / reasons / notes);
        # state memory a / b = short identifiers only (free text from memory evidence becomes None).
        hist = self.log.rows()[-20:]
        brief = [{"a": _brief_a(r.get("a")), "status": r.get("a_status") if isinstance(r.get("a_status"), str) else None,
                  "replay": _brief_rep(r.get("replay"))} for r in hist]
        return json.dumps({"task": "propose <=3 policy edits as strict JSON {kind_hint, edits:[{path,value}], rationale}",
                           "allowed_keys": sorted(C.PROPOSE_KEYS), "state": _safe_state(s), "bounds": base.get("bounds"),
                           "history": brief}, sort_keys=True, ensure_ascii=False)

    def cycle(self, s, t_now):
        st = self.store.load()
        try:
            deps.verify(self.deps_lock)
        except deps.DepsMismatch as e:
            return self._alarm(t_now, s, f"C9 dependency sha mismatch: {e}")
        if not st.get("enabled"):
            return self._row(t_now, s, None, "off", reject_reason=f"rlpo off: {st.get('off_reason')}")
        miss = [g for g in C.START_GATES if not (st.get("gates") or {}).get(g)]
        if miss:
            return self._row(t_now, s, None, "off", reject_reason=f"start gates not passed: {miss}")
        ok, why = BK.allow(st.get("breaker") or {}, t_now)
        if not ok:
            return self._row(t_now, s, None, "off", reject_reason=why)
        if st.get("pending"):
            return self._row(t_now, s, None, "rejected",
                             reject_reason=f"pending CoS decision on proposal #{st['pending']['id']} (one at a time)")
        n = self.runs.latest()
        raw = self.live.read_bytes()
        if n is None or self.runs.policy_bytes(n) != raw:
            return self._alarm(t_now, s, f"C2 external edit: live sha {hashlib.sha256(raw).hexdigest()[:12]} != "
                                         f"registered v{n}; RLPO OFF, handed to a human (no write)")
        base = json.loads(raw)
        t0 = time.monotonic()
        try:
            out = self.llm.complete(self.prompt(s, base))
        except Exception as e:  # breaker decides how long; the policy is untouched
            st = self.store.load()
            kind = BK.record_error(st.setdefault("breaker", {}), t_now, e)
            self.llm_day = BK.day_report(st["breaker"], t_now, C.LLM_WARN_TOKENS_DAY)
            self.store.save(st)
            return self._row(t_now, s, None, "off", reject_reason=f"llm error [{kind}] {type(e).__name__}: {e}"[:300])
        st = self.store.load()
        BK.record_ok(st.setdefault("breaker", {}), t_now, out.get("tokens_in"), out.get("tokens_out"))
        rep_day = BK.day_report(st["breaker"], t_now, C.LLM_WARN_TOKENS_DAY)
        if rep_day["warn"] and not rep_day["warned"]:
            # P1: warning only (no OFF, no cap) - 성수님 14:38 무제한 결정, CoS 16:27
            with open(self.alarm_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": t_now, "level": "warn", "reason": "llm tokens/day >= warn", **rep_day},
                                   ensure_ascii=False) + "\n")
            BK.mark_warned(st["breaker"], t_now)
        self.store.save(st)
        self.llm_day = rep_day
        meta = {"tokens_in": out.get("tokens_in"), "tokens_out": out.get("tokens_out"),
                "latency_ms": int((time.monotonic() - t0) * 1000)}
        try:
            a = json.loads(out["text"])
        except (ValueError, TypeError, KeyError) as e:
            return self._row(t_now, s, None, "parse_error", reject_reason=str(e)[:300], **meta)
        t_rem = s.get("t_rem")
        if t_rem is None and C.T_END:
            t_rem = C.T_END - t_now
        why = guard.check(base, a, st.get("anchor"), self.log.rows(), t_now, t_rem, st.get("open_watch"))
        if why:
            return self._row(t_now, s, a, "rejected", reject_reason=why, **meta)
        from harness import bounds
        rep = self.replay(base, bounds.apply(base, a), t_now)
        rep = dict(rep, would_win=guard.wins(rep), apply_refusal=guard.applicable(a, rep), llm_day=self.llm_day)
        row = self._row(t_now, s, a, "proposed", replay=rep, r_proxy=rep.get("point"), **meta)
        st = self.store.load()
        st["pending"] = {"id": row["id"], "t": t_now, "a": a, "policy_sha": hashlib.sha256(raw).hexdigest()}
        self.store.save(st)
        if C.L_APPROVER_ENABLED:
            return row                     # L mode: no approval request; rlpo.approver writes an apply summary
        with open(self.outbox, "a", encoding="utf-8") as f:
            f.write(json.dumps({"to": C.APPROVER, "to_id": C.APPROVER_ID, "proposal_id": row["id"], "t": t_now, "a": a,
                                "replay": {k: rep.get(k) for k in ("verdict", "point", "lo_adj", "n", "n_blocks",
                                                                   "would_win", "apply_refusal")}},
                               ensure_ascii=False, default=str) + "\n")
        return row
