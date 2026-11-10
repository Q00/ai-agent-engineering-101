"""One decision of an inner agent: the model looks things up with tools, then ends its turn with an act.* tool.

The system prompt is the same for every decision of a role (C1: what never changes comes first, so prompt
caching can hit): the card assembled for that role and the strategy in force, then the role's tools. The
prompt is the decision at hand plus this turn's steps.

Each step the model answers with one JSON object {"thought": "...", "tool": "<name>", "args": {...}}.
- A look-up tool runs through tools.registry.call and its result is the next observation.
- A final tool (act.*) runs the same way. If it refuses, the reason and hint are the next observation and
  the model chooses again; if it passes, its result is the decision.
- An answer that is not JSON, or names a tool the role does not have, comes back as an observation.

decide() returns (decision or None, trace). None means no valid decision within the steps or the time;
the entry point then falls back to something safe and records it as a guard.
"""
import json
import time

from agents import model as models
from core import card
from tools import act, calc, check, evaluate, harness_tools, query, record, search  # noqa: F401  (registers the tools)
from tools.registry import TOOLS, call

# HARNESS.md 3.4: what each L1 role sees (look-ups) and how it can end its turn (finals).
ROLES = {
    "seller": {"look": ["me.counterpart", "me.inventory", "market.sku", "market.buyer_prefs", "calc.margin", "calc.shelf_risk",
                        "calc.guess_counterpart", "check.price", "check.claim", "rec.label", "memory.card_section"],
               "final": ["act.counter", "act.accept", "act.reject"], "steps": 3},
    "buyer": {"look": ["me.counterpart", "market.sku", "calc.surplus", "check.offer", "check.receipt", "rec.label",
                       "memory.card_section"],
              "final": ["act.pick", "act.offer", "act.walk_away", "act.finish"], "steps": 3},
    "restock": {"look": ["me.inventory", "me.missed", "market.sku", "market.selling", "market.buyer_prefs", "calc.holding_cost",
                         "calc.shelf_risk",
                         "check.buy", "memory.card_section"],
                "final": ["act.buy"], "steps": 7, "timeout": 45},
    "review": {"look": ["me.counterpart", "memory.card_section"], "final": ["act.review"], "steps": 2},
    "forum": {"look": ["market.forum", "market.sku", "market.selling", "market.buyer_prefs", "me.missed", "me.counterpart",
                       "act.vote",
                       "memory.card_section"],
              "final": ["act.post", "act.skip"], "steps": 5},
    # outer runtime (HARNESS.md 2.3): seat = the model seat in agents.model, reader = how the card frames the intent
    "market analyst": {"look": ["market.selling", "market.sku", "market.forum", "market.buyer_prefs", "me.counterpart",
                                "memory.profiles",
                                "memory.profile", "memory.summary", "memory.search", "memory.raw", "eval.forum",
                                "rec.profile", "memory.card_section"],
                       "final": ["rec.summary"], "steps": 12, "seat": "analyst", "reader": "strategist", "timeout": 240},
    "self analyst": {"look": ["eval.funnel", "eval.unit_economics", "eval.losses", "eval.stock_health", "me.missed",
                              "eval.decision_quality", "eval.integrity", "eval.forum", "eval.guess_accuracy",
                              "eval.experiment", "eval.score", "me.inventory", "memory.summary", "memory.search",
                              "memory.raw", "memory.card_section"],
                     "final": ["rec.summary"], "steps": 12, "seat": "analyst", "reader": "strategist", "timeout": 240},
    "strategy": {"look": ["eval.score", "eval.experiment", "eval.unit_economics", "eval.losses", "eval.proposals",
                          "eval.audits", "memory.summary", "memory.search", "memory.raw", "market.buyer_prefs",
                          "memory.profiles",
                          "memory.profile", "me.counterpart",
                          "rec.stance", "memory.card_section"],
                 "final": ["rec.brief"], "steps": 8, "seat": "strategy", "reader": "strategist", "timeout": 240},
    # design 16-2, 20: the meta-harness looks the raw records up itself and ends with a checked proposal
    "meta": {"look": ["trajectory.get", "trajectory.search", "summary.hourly", "score.class", "report.metrics",
                      "participant.get", "knowledge.get", "capability.list", "experiment.result", "wisdom.get",
                      "approval.status", "model.usage", "market.selling", "market.sku", "market.benchmark"],
             "final": ["meta.propose"], "steps": 10, "seat": "strategy", "reader": "strategist", "timeout": 240},
    "audit": {"look": ["eval.score", "eval.experiment", "eval.unit_economics", "eval.losses", "eval.audits",
                       "memory.summary", "memory.search", "memory.raw", "market.selling", "market.sku",
                       "memory.card_section"],
              "final": ["rec.audit"], "steps": 5, "seat": "audit", "reader": "auditor", "timeout": 240},
}
FORMAT = ('Answer with ONE JSON object and nothing else: {"thought": "<one short sentence>", "tool": "<tool name>", '
          '"args": {...}}')
OBS_MAX = 1500


def _type(v):
    """A compact type: enums as a|b, arrays as [item], objects as {k: type, optional?: type}."""
    if "enum" in v:
        return "|".join(map(str, v["enum"]))
    t = v.get("type")
    if t == "array" and v.get("items"):
        return f"[{_type(v['items'])}]"
    if t == "object" and v.get("properties"):
        req = set(v.get("required", []))
        return "{" + ", ".join(f"{k}{'' if k in req else '?'}: {_type(x)}" for k, x in v["properties"].items()) + "}"
    return "|".join(t) if isinstance(t, list) else str(t)


def _schema_line(s):
    req = set(s.get("required", []))
    return "(" + ", ".join(f"{k}{'' if k in req else '?'}: {_type(v)}" for k, v in s.get("properties", {}).items()) + ")"


def tools_text(names):
    return "\n".join(f"- {n}{_schema_line(TOOLS[n].input_schema)}: {TOOLS[n].description}" for n in names)


def system_prompt(role, strategy, allowed=None):
    r = ROLES[role]
    look = [n for n in r["look"] if allowed is None or n in allowed]
    final = [n for n in r["final"] if allowed is None or n in allowed]
    _, text = card.assemble(role, r.get("reader", "executor"), strategy=strategy)
    return (f"{text}\n\n# your tools\nLook things up (then keep going):\n{tools_text(look)}\n\n"
            f"End your turn with one of:\n{tools_text(final)}\n\n{FORMAT}"), look, final


def parse(answer):
    start = answer.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer")
    obj, _ = json.JSONDecoder().raw_decode(answer[start:])
    if not isinstance(obj, dict) or not isinstance(obj.get("tool"), str):
        raise ValueError('the JSON object has no "tool" string')
    if not isinstance(obj.get("args", {}), dict):
        raise ValueError('"args" must be an object')
    return obj


def decide(role, ctx, situation_text, deadline=None, allowed=None, model_call=None, steps=None):
    """(decision dict or None, trace). ctx.situation must already hold what the act.* tools act on."""
    system, look, final = system_prompt(role, ctx.strategy, allowed)
    seat = ROLES[role].get("seat", role)
    model_call = model_call or (lambda prompt, system, timeout: models.call(seat, prompt, system=system, timeout=timeout))
    trace = []
    for step in range(1, (steps or ROLES[role]["steps"]) + 1):
        left = None if deadline is None else deadline - time.time()
        if left is not None and left < 8:
            trace.append({"step": step, "answer": None, "observation": "out of time"})
            break
        prompt = situation_text
        if trace:
            prompt += "\n\n# your steps so far in this turn\n" + "\n".join(
                f"step {t['step']}: {t['answer']}\nobservation: {t['observation']}" for t in trace)
        if step == (steps or ROLES[role]["steps"]):
            prompt += f"\n\nThis is your last step: end your turn now with one of {final}."
        prompt += f"\n\n{FORMAT}"
        per_call = ROLES[role].get("timeout", models.CALL_TIMEOUT)
        timeout = per_call if left is None else max(5, min(per_call, left - 3))
        try:
            answer = model_call(prompt, system, timeout)
        except models.ModelError as e:
            trace.append({"step": step, "answer": None, "observation": f"model error: {e}"})
            continue
        try:
            obj = parse(answer)
        except ValueError as e:
            trace.append({"step": step, "answer": answer[:300], "observation": f"FORMAT ERROR: {e}. {FORMAT}"})
            continue
        name, args = obj["tool"], obj.get("args") or {}
        short = json.dumps({"tool": name, "args": args}, ensure_ascii=False)[:400]
        if name not in look and name not in final:
            trace.append({"step": step, "answer": short, "thought": obj.get("thought"),
                          "observation": f"you have no tool {name!r}; yours: {look + final}"})
            continue
        res = call(ctx, name, args)
        if name in final and res["ok"]:
            trace.append({"step": step, "answer": short, "thought": obj.get("thought"), "observation": "done"})
            return dict(res["result"], thought=obj.get("thought", "")), trace
        obs = res["result"] if res["ok"] else {"refused": res["reason"], "hint": res.get("hint")}
        trace.append({"step": step, "answer": short, "thought": obj.get("thought"),
                      "observation": json.dumps(obs, ensure_ascii=False, default=str)[:OBS_MAX]})
    return None, trace
