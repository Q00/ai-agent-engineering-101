"""The audit of one brief, in its own process (HARNESS.md 2.3, D29):

    py -m outer.audit 2026-11-05T10

outer.slice starts this after the strategy agent wrote its brief and does not wait for it. The audit reads
only the finished brief (never the strategy agent's reasoning), checks it with its own tools on another
vendor's model (seat audit in agents.model), and adds its opinion to the brief. If it fails or hangs, the
brief stands without it and the outer loop is not touched.
"""
import json
import sys
import time

from agents import loop
from core import atomic, bus, paths
from core.events import EventWriter
from outer import brief
from tools.registry import Ctx

TIMEOUT = 600


def audit_text(doc):
    return "\n".join(["# the audit at hand",
                      "The strategy agent's brief (options are changes to the strategy in force):",
                      json.dumps({k: v for k, v in doc.items() if k not in ("strategies", "audit")},
                                 ensure_ascii=False, default=str)[:5000],
                      "Check it against the scoring formula and the data (tools), then end with rec.audit."])


def run(pid, model_call=None):
    path = paths.memory() / "strategy" / "proposals" / f"{pid}.json"
    doc = atomic.read_json(path)
    if doc is None:
        raise ValueError(f"no brief {pid}")
    tool_w, w = EventWriter("audit", source="tools"), EventWriter("audit", source="outer")
    try:
        ctx = Ctx(caller="audit", _writer=tool_w)
        ctx.situation = {"proposal_id": pid, "slice": doc.get("slice")}
        w.context(strategy_version=ctx.strategy.get("version"), epoch=ctx.strategy.get("epoch"))
        t0 = time.time()
        d, trace = loop.decide("audit", ctx, audit_text(doc), deadline=t0 + TIMEOUT, model_call=model_call)
        w.emit("slice.step", {"role": "audit", "ok": d is not None, "s": round(time.time() - t0), "steps": len(trace)})
        if d is None:
            bus.alert("audit", "info", "no_audit", f"brief {pid}: the audit gave no opinion",
                      {"last": str(trace[-1]["observation"])[:200] if trace else None})
            return None
        doc = atomic.read_json(path)
        w.emit("audit.opinion", {"proposal_id": pid, "agrees": doc["audit"]["agrees"]})
        pub = atomic.read_json(brief.briefs_dir() / f"{pid}.json", default=None)
        if pub:
            brief.write_markdown(pub)
        return doc["audit"]
    finally:
        w.close()
        tool_w.close()


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1]), ensure_ascii=False))
