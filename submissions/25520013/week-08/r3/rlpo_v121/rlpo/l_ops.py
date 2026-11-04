# L2 operating path (CR REVIEW_M2r2_L B1, L-C8). One cycle:
#   (1) approval.watch_cycle: C8 live observation of an open watch -> verify / roll back; any failure -> C8 alarm + OFF
#   (2) Loop.cycle: proposal (unchanged; the loop itself has no apply or rollback code, S1)
#   (3) L mode only: approver.run on the new pending proposal, with structured recent live performance
# Not wired to a scheduler or to a real LLM client: connecting a real approver client is a separate CR (L-C8).
from rlpo import approval as AP
from rlpo import approver, live_obs
from rlpo import config as C


def step(loop, approver_llm, source, s, t):
    cx = AP.Ctx(loop.runs, loop.live, loop.log, loop.store, loop.alarm_path)
    w = AP.watch_cycle(cx, t, source)
    out = {"watch": w, "proposal": None, "approver": None}
    if w is not None and w.get("a_status") in ("alarm", "rolled_back"):
        return out
    row = loop.cycle(s, t)
    out["proposal"] = row
    if C.L_APPROVER_ENABLED and row.get("a_status") == "proposed":
        try:
            perf = live_obs.recent(source()[0], t)
        except Exception:  # structured input only; a failing source gives the approver no live numbers (None)
            perf = None
        out["approver"] = approver.run(cx, row["id"], approver_llm, loop.replay, t, s.get("t_rem"), loop.deps_lock,
                                       live_perf=perf)
    return out
