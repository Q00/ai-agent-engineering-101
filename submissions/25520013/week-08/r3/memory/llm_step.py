# v121 (성수님 결정 CoS 전달 17:40): the LLM judges memory ops (ADD / UPDATE / DELETE / NOOP) for the evidence memos.
# memory.run POST_INGEST step, default OFF (MEMORY_LLM = False). One call per run through RoleLLM(live, "memory")
# (llm.live_v120c -> caps_v4 shared breaker auto-OFF, no count cap -> codex live path).
# Guards in code:
#   - memo_proposer.parse: strict JSON array, <= 5 items, fixed fields; the ledger window is fenced and defused
#   - subject allowlist (shop: / buyer: / sku: / category:) and at most MAX_DELETES DELETE proposals per run
#   - Graph.propose rule gate: evidence must be ledger seqs in the window that mention the subject; the rule op wins
#   - soft delete only: Graph DELETE / UPDATE set "to" on the open version, nothing is removed. Invariant check after
#     apply (no edge key lost, no version lost, no closed version changed, deletes <= MAX_DELETES); a violation
#     restores the in-memory graph snapshot taken before the step (automatic rollback) before anything is written
#   - decision log <mem>/llm_decisions.jsonl: statuses, op counts, shas; never prompt / reply / evidence text
# LLM OFF / failure / bad reply -> no-op (the rule-only memory keeps running).
import copy
import hashlib
import os
from pathlib import Path

import memo_proposer as MP

MEMORY_LLM = False
SUBJECT_KINDS = ("shop", "buyer", "sku", "category")
MAX_DELETES = 2
WINDOW = 40
MAX_MEMOS = 30
HERE = Path(__file__).resolve().parent.parent
_LIVE = None


def _sha(s):
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()[:16]


def _live():
    global _LIVE
    if _LIVE is None:
        from llm import live_v120c
        _LIVE = live_v120c.Live(HERE)
    _LIVE.refresh()
    return _LIVE


class _Client:
    # memo_proposer client interface: complete(prompt, *, max_tokens, timeout_s) -> text
    def __init__(self, llm):
        self.llm = llm

    def complete(self, prompt, max_tokens=MP.MAX_OUTPUT_TOKENS, timeout_s=MP.TIMEOUT_S):
        return self.llm.complete(prompt, max_tokens)["text"]


class _PLog(list):
    pass


def subject_ok(s):
    k, sep, name = s.partition(":") if isinstance(s, str) else ("", "", "")
    return bool(sep) and k in SUBJECT_KINDS and 0 < len(name) <= 80


def filter_items(items):
    out, dels, dropped = [], 0, 0
    for it in items:
        if not subject_ok(it.get("subject")):
            dropped += 1
            continue
        if it.get("op") == "DELETE":
            if dels >= MAX_DELETES:
                dropped += 1
                continue
            dels += 1
        out.append(it)
    return out, dropped


def _versions(G):
    return {(k, key): [(v.get("from"), v.get("to")) for v in e.get("v", [])]
            for k, d in G.edges.items() for key, e in d.items()}


def invariant_violations(before, after, rows):
    why = []
    for ek, vs in before.items():
        nv = after.get(ek)
        if nv is None:
            why.append("edge removed")
            continue
        if len(nv) < len(vs):
            why.append("version removed")
            continue
        for (f0, t0), (f1, t1) in zip(vs, nv):
            if f0 != f1 or (t0 is not None and t0 != t1):
                why.append("closed version changed")
                break
    if sum(1 for r in rows if (r or {}).get("op") == "DELETE") > MAX_DELETES:
        why.append("too many deletes")
    return sorted(set(why))


def _memos(G):
    out = []
    for key, e in sorted((G.edges.get("memo") or {}).items()):
        v = e["v"][-1] if e.get("v") else None
        if v is not None and v.get("to") is None:
            out.append({"subject": e.get("a"), "predicate": e.get("b"), "value": (v.get("attrs") or {}).get("value")})
    return out[-MAX_MEMOS:]


def step(ctx, live=None, enabled=None, log=None):
    # v121c (CoS 결정 18:48): code default OFF; the deploy turns it ON with env R3_MEMORY_LLM=1 (runner env only)
    on = (MEMORY_LLM or os.environ.get("R3_MEMORY_LLM") == "1") if enabled is None else enabled
    if not on:
        return None
    from llm.ops_llm import DecisionLog, RoleLLM
    log = log or DecisionLog(Path(ctx["mem"]) / "llm_decisions.jsonl")
    row = {"stage": "memory_llm", "t": ctx.get("now")}
    G = (ctx.get("layers") or {}).get("graph")
    if G is None:
        log.append(dict(row, status="no_graph"))
        return "memory_llm: no graph"
    try:
        llm = RoleLLM(live if live is not None else _live(), "memory")
    except Exception as e:                               # noqa: BLE001
        log.append(dict(row, status="llm_off", error=type(e).__name__))
        return "memory_llm off"
    if not llm.available():
        log.append(dict(row, status="llm_off"))
        return "memory_llm off"
    events = [e for e in (ctx.get("events") or []) if isinstance(e, dict)][-WINDOW:]
    mctx = {"memos": _memos(G), "ledger_window": [{"seq": e.get("seq"), "type": e.get("type"), "data": e.get("data")}
                                                  for e in events]}
    plog = _PLog()
    items = MP.propose(mctx, _Client(llm), enabled=True, log=plog)
    st = (plog[-1] if plog else {}).get("status")
    if st != "ok":
        log.append(dict(row, status="llm_" + str(st or "none"), error=str((plog[-1] if plog else {}).get("error"))[:60]))
        return f"memory_llm {st}"
    items, dropped = filter_items(items)
    before = _versions(G)
    snap = copy.deepcopy(G.__dict__)
    by_seq = {e.get("seq"): e for e in events}
    try:
        rows = MP.apply(G, items, by_seq.get, ctx.get("now"))
        why = invariant_violations(before, _versions(G), rows)
    except Exception as e:                               # noqa: BLE001
        rows, why = [], ["apply error " + type(e).__name__]
    if why:
        G.__dict__.clear()
        G.__dict__.update(snap)
        log.append(dict(row, status="rolled_back", violations=why, n_items=len(items), dropped=dropped))
        return f"memory_llm rolled back: {why}"
    ops = {}
    for it, r in zip(items, rows):
        ops.setdefault(r.get("op"), 0)
        ops[r.get("op")] += 1
    log.append(dict(row, status="applied", n_items=len(items), dropped=dropped, ops=ops,
                    overridden=sum(1 for r in rows if r.get("overridden")),
                    proposed=[it.get("op") for it in items], final=[r.get("op") for r in rows],
                    keys=[_sha(r.get("key")) for r in rows]))
    return f"memory_llm {len(items)} items {ops}"
