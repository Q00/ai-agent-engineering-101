# v121 (성수님 결정 CoS 전달 17:40): real LLM for the RLPO proposer and approver.
# Both go through the deployed v120c-B LLM path of the work tree (llm.live_v120c.Live.call -> caps_v4 OpsCaps with the
# shared breaker auto-OFF, no count cap -> codex_client_v4 live counter, codex exec --ephemeral, model from
# llm_config.json). Roles: rlpo_prop (proposer), rlpo_appr (approver: separate role, separate fresh codex session,
# structured numbers only via approver._prompt). The adapter error text is code-made (no CLI output).
# LLM OFF / caps breaker open -> no proposal and no approver call (no-op); the C8 watch (code only) still runs, so an
# applied change can still be rolled back. Everything else (gates, guards, replay, rollback) is the unchanged L path.
import sys
from pathlib import Path

from rlpo import approval as AP
from rlpo import config as C
from rlpo import l_ops


def _ops(work):
    w = str(Path(work))
    if w not in sys.path:
        sys.path.append(w)                    # append: the pinned v120c harness / experiments keep precedence
    from llm import ops_llm                   # noqa: E402
    return ops_llm


def make(work=None, live=None):
    # -> {"live", "proposer", "approver"} or None when no work tree is configured (RLPO_LLM_WORK unset)
    work = work if work is not None else C.LLM_WORK
    if work is None and live is None:
        return None
    if work is not None:
        O = _ops(work)
    else:
        from llm import ops_llm as O          # noqa: E402 - caller put the work tree on sys.path (tests)
    if live is None:
        from llm import live_v120c            # noqa: E402
        live = live_v120c.Live(Path(work))
    return {"live": live, "proposer": O.RoleLLM(live, "rlpo_prop"), "approver": O.RoleLLM(live, "rlpo_appr")}


def step(loop, llms, source, s, t):
    # one L2 operating cycle with the real LLMs. Returns the l_ops.step dict plus "llm": on | off.
    if llms is None:
        cx = AP.Ctx(loop.runs, loop.live, loop.log, loop.store, loop.alarm_path)
        return {"watch": AP.watch_cycle(cx, t, source), "proposal": None, "approver": None, "llm": "off"}
    live = llms["live"]
    try:
        live.refresh()
    except Exception:                         # noqa: BLE001 - refresh failure = OFF
        pass
    prop, appr = llms["proposer"], llms["approver"]
    if not prop.available():
        cx = AP.Ctx(loop.runs, loop.live, loop.log, loop.store, loop.alarm_path)
        return {"watch": AP.watch_cycle(cx, t, source), "proposal": None, "approver": None, "llm": "off"}
    prop.model = getattr(live, "model", None)
    appr.model = prop.model
    loop.llm = prop
    out = l_ops.step(loop, appr, source, s, t)
    out["llm"] = "on"
    return out
