"""The new layers as tools (design 16-2): what the meta-harness looks up for itself instead of being handed a summary
(W02: the proposer did better with the raw trajectories than with summaries, 34.9 -> 50.0).

Look-ups: trajectory.get / trajectory.search (raw trades), summary.hourly, score.class, report.metrics,
participant.get, knowledge.get, capability.list, experiment.result, wisdom.get, approval.status, model.usage.
End of the meta-harness turn: meta.propose - checked by code (one playbook number per request, inside the capability
list, the 17-3 fields) before anything reaches the user. Same contract as every tool (tools/registry.py).
"""
import time

from harness import (capability, experiment, knowledge, metrics, ops, participant, score_class, trajectory,
                     wisdom)
from tools.registry import Refused, tool

_DAY = {"type": "string", "pattern": "^\\d{8}$", "description": "YYYYMMDD, default today"}


def _day(day):
    return day or time.strftime("%Y%m%d")


def _compact(t, steps=8):
    """One trajectory for a model: everything but the long step list cut to its last steps."""
    return {**{k: v for k, v in t.items() if k != "steps"}, "steps": t["steps"][-steps:], "steps_total": len(t["steps"])}


@tool("trajectory.get", "One trade from its first event to its after-effects (raw, design 17-1): order id (buyer), "
      "task id (seller) or restock plan id.",
      {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}, "day": _DAY}})
def trajectory_get(ctx, id, day=None):
    t = trajectory.get_one(id, _day(day))
    if t is None:
        raise Refused(f"no trade {id} on {_day(day)}", "the id is an order (o-...), a seller task or a restock plan id; "
                                                       "try trajectory.search")
    return _compact(t, steps=30)


@tool("trajectory.search", "Raw trades of one day by role, failure cause (limit, counterpart, network, ours, "
      "negotiation) and/or counterpart name - the newest first.",
      {"type": "object", "properties": {"role": {"enum": ["buyer", "seller", "restock"]},
                                        "cause": {"enum": ["limit", "counterpart", "network", "ours", "negotiation"]},
                                        "counterpart": {"type": "string"}, "day": _DAY,
                                        "limit": {"type": "integer", "minimum": 1, "maximum": 10}}})
def trajectory_search(ctx, role=None, cause=None, counterpart=None, day=None, limit=5):
    found = trajectory.search(_day(day), role, cause, counterpart, limit=500)
    return [_compact(t, steps=4) for t in sorted(found, key=lambda t: -t["start"])[:limit]]


@tool("summary.hourly", "The fixed metrics (design 17-2) for each of the last hours, by code.",
      {"type": "object", "properties": {"hours": {"type": "integer", "minimum": 1, "maximum": 24}}})
def summary_hourly(ctx, hours=6):
    now = ctx.now()
    days = sorted({time.strftime("%Y%m%d", time.localtime(now - hours * 3600)), time.strftime("%Y%m%d", time.localtime(now))})
    h = {}
    for d in days:
        h.update(metrics.hourly(d))
    done = [(k, v) for k, v in sorted(h.items()) if v["until"] <= now]       # finished hours only
    return dict(done[-hours:])


@tool("score.class", "Our place among the students (the board's Class view): rank, gaps, and each score part's "
      "percentile, room and the row just above.", {"type": "object", "properties": {}})
def score_class_tool(ctx):
    v = score_class.latest(ctx.now())
    if v is None:
        raise Refused("no board snapshot in the last 3 h", "board_history keeps them every 10 min")
    return v


@tool("report.metrics", "A role's self-report: its fixed metrics over the last 1 h and 24 h (design R6), by code.",
      {"type": "object", "required": ["role"], "properties": {"role": {"enum": ["buyer", "shop"]}}})
def report_metrics(ctx, role):
    return metrics.report(role, ctx.now())


@tool("participant.get", "What we know of another player, by GitHub login or a shop / buyer name: as a shop our "
      "buyer met, as a buyer of our shop (observed, 24 h, with n).",
      {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}})
def participant_get(ctx, name):
    return participant.get(name, ctx.now()) or {"known": False}


@tool("knowledge.get", "Knowledge items with their grade (stated / observed / hypothesis), source and expiry.",
      {"type": "object", "properties": {"about": {"type": "string"}, "prefix": {"type": "string"},
                                        "grade": {"enum": ["stated", "observed", "hypothesis"]},
                                        "limit": {"type": "integer", "minimum": 1, "maximum": 20}}})
def knowledge_get(ctx, about=None, prefix=None, grade=None, limit=10):
    return knowledge.get(about=about, prefix=prefix, grade_=grade, now=ctx.now())[:limit]


@tool("capability.list", "What each role's code reads and how far it may move: the playbook numbers (now, schema "
      "range, the user's approved range) and the tools per role.",
      {"type": "object", "properties": {"role": {"enum": ["seller", "buyer", "restock", "review", "hold"]}}})
def capability_list(ctx, role=None):
    c = capability.capability_list()
    if role:
        return {"numbers": {k: v for k, v in c["numbers"][role]["rows"].items() if v["read_by_code"]},
                "tools": c["tools"].get(role)}
    return {r: sorted(k for k, v in n["rows"].items() if v["read_by_code"]) for r, n in c["numbers"].items()}


@tool("experiment.result", "The running experiments and their verdict so far, computed by code from the trades.",
      {"type": "object", "properties": {"id": {"type": "string"}}})
def experiment_result(ctx, id=None):
    ids = [id] if id else [e["id"] for e in experiment.running(now=ctx.now())]
    return [experiment.result(i, ctx.now()) for i in ids]


@tool("wisdom.get", "The wisdom in force: strategy version and approval, playbook, limits, approved ranges, running "
      "experiments and the last decisions.", {"type": "object", "properties": {}})
def wisdom_get(ctx):
    return wisdom.wisdom_get()


@tool("approval.status", "The approval requests waiting for the user.", {"type": "object", "properties": {}})
def approval_status(ctx):
    return [{k: d.get(k) for k in ("id", "title", "urgency", "change", "ts")} for d in wisdom.status()]


@tool("model.usage", "Model calls per seat in the last hour against the budget (design 17-5).",
      {"type": "object", "properties": {}})
def model_usage(ctx):
    return ops.model_usage(ctx.now())


_REQUEST = {"type": "object", "required": list(wisdom.REQUIRED), "properties": {
    "title": {"type": "string", "minLength": 1, "maxLength": 120}, "urgency": {"enum": ["now", "page"]},
    "change": {"type": "object"}, "why": {"type": "string", "minLength": 1, "maxLength": 1200},
    "expected": {"type": "object", "required": ["metric", "direction", "size"]},
    "evaluation": {"type": "object", "required": ["method", "period_h", "success_if"]},
    "risk": {"type": "string", "maxLength": 400}, "undo": {"type": "string", "maxLength": 300}}}


@tool("meta.propose", "End the meta-harness turn: a short report and 0-2 approval requests, each changing ONE "
      "playbook number the code reads (see capability.list), with the fixed metric it should move, how it is "
      "measured (ab preferred) and the success line. Requests go to the user; nothing is installed here.",
      {"type": "object", "required": ["report", "requests"],
       "properties": {"report": {"type": "string", "minLength": 1, "maxLength": 2000},
                      "requests": {"type": "array", "maxItems": 2, "items": _REQUEST}}})
def meta_propose(ctx, report, requests):
    if len(requests) > 2:                      # core.schema does not check maxItems
        raise Refused(f"{len(requests)} requests; at most 2", "keep the two the evidence supports best")
    for r in requests:
        leaves = list(wisdom._leaves(r["change"]))
        if len(leaves) != 1 or leaves[0][0][0] != "playbook":
            raise Refused(f"'{r['title']}' changes {len(leaves)} values; one playbook number per request",
                          'change: {"playbook": {"<role>": {"<number>": <value>}}}')
        bad = [n for n in wisdom.check_change(r["change"]) if "does not read" in n or "not in the schema" in n]
        if bad:
            raise Refused(f"'{r['title']}': {bad[0]}", "pick a number from capability.list")
        if r["expected"]["metric"] not in experiment.ROLE_OF_METRIC:
            raise Refused(f"{r['expected']['metric']} is not a fixed metric",
                          f"one of {sorted(experiment.ROLE_OF_METRIC)}")
    return {"report": report, "requests": requests}
