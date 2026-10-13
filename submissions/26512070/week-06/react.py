"""A small ReAct loop shared by both brains: think -> act (tool or final answer) -> observe.

Each step the model answers with ONE JSON object:
    {"thought": "...", "action": "<tool or final action>", "args": {...}}

- a tool action runs, and its result comes back as the next Observation;
- a final action is checked by validate(); if the rules reject it, the reason
  comes back as an Observation ("REJECTED BY RULES: ...") and the model tries again;
- an answer that is not valid JSON, or names an unknown action, also comes back
  as an Observation.

At most max_steps model calls per decision. If no valid final action comes out,
run() returns None and the caller's guard takes over.
"""
import json
import time

from model import call_model, log

FORMAT = ('Answer with ONE JSON object and nothing else:\n'
          '{"thought": "<one short sentence>", "action": "<one action name>", "args": {...}}')


def parse(text):
    """The first JSON object in the model's answer (code fences and chatter around it are ignored)."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict) or not isinstance(obj.get("action"), str):
        raise ValueError('the JSON object has no "action" string')
    if not isinstance(obj.get("args", {}), dict):
        raise ValueError('"args" must be an object')
    return obj


def describe(actions):
    return "\n".join(f"- {name}{spec['args']}: {spec['description']}" for name, spec in actions.items())


def run(system, situation, tools, finals, validate, max_steps=3, deadline=None, model=call_model):
    """Return (final decision dict, trace list) or (None, trace) when the steps or the time run out.

    tools:  {name: {"args": "(price)", "description": ..., "fn": callable(args) -> str}}
    finals: {name: {"args": "(price, say)", "description": ...}}
    validate(action, args) -> (ok, reason)
    """
    trace = []
    for step in range(1, max_steps + 1):
        if deadline is not None and time.time() > deadline - 10:
            log(f"[react] out of time before step {step}")
            break
        prompt = (f"{situation}\n\nTools (look things up, then keep going):\n{describe(tools)}\n\n"
                  f"Final actions (end your turn):\n{describe(finals)}\n\n{FORMAT}\n")
        if trace:
            prompt += "\nYour steps so far in this turn:\n" + "\n".join(
                f"Step {t['step']}: {t['answer']}\nObservation: {t['observation']}" for t in trace) + "\n"
        try:
            answer = model(prompt, system=system)
        except Exception as e:  # harness failure: one more try is the next step
            trace.append({"step": step, "answer": "(no answer)", "observation": f"harness error: {e}"})
            continue
        try:
            obj = parse(answer)
        except ValueError as e:
            trace.append({"step": step, "answer": answer[:300], "observation": f"FORMAT ERROR: {e}. {FORMAT}"})
            continue
        action, args = obj["action"], obj.get("args") or {}
        short = json.dumps(obj, ensure_ascii=False)
        if action in tools:
            try:
                observation = str(tools[action]["fn"](args))
            except Exception as e:
                observation = f"tool error: {e!r}"
            trace.append({"step": step, "answer": short, "observation": observation})
        elif action in finals:
            ok, reason = validate(action, args)
            if ok:
                trace.append({"step": step, "answer": short, "observation": "accepted"})
                log(f"[react] final after {step} step(s): {short}")
                return {"action": action, **args, "thought": obj.get("thought", "")}, trace
            trace.append({"step": step, "answer": short, "observation": f"REJECTED BY RULES: {reason}. Choose again."})
        else:
            trace.append({"step": step, "answer": short,
                          "observation": f"unknown action {action!r}; use one of {list(tools) + list(finals)}"})
    log(f"[react] no valid final action in {len(trace)} step(s)")
    return None, trace
