"""A small ReAct loop (Thought -> Action -> Observation, repeated) over read-only tools, for the two jobs that need
judgement: whether to speak first on the forum, and whether the rule-based restock plan should be scaled.

Money decisions never go through here: pricing.py decides prices, floors, offers and warranties in microseconds.
ReAct only *proposes*; the caller validates and clamps every field of the final answer before using it.

Each step is one bounded llm.ask_json call (stateless), so the whole transcript is passed back each time. Tool
observations may contain other agents' text, so the transcript travels in DATA, never in the instruction.
Every step is appended to logs/r3/react.jsonl as evidence.
"""
import json
import time

from . import config, llm

MAX_OBS_CHARS = 2500


class Tool:
    def __init__(self, name, description, fn, args=None):
        self.name, self.description, self.fn, self.args = name, description, fn, args or {}

    def spec(self):
        a = ", ".join(f"{k}: {v}" for k, v in self.args.items()) or "no arguments"
        return f"- {self.name}({a}): {self.description}"


def _instruction(goal, tools, final_shape):
    return (
        "You are a ReAct agent. Work in steps. You cannot run the TOOLS below yourself and they are not part of "
        "your own environment: to use one, reply with an action and our program runs it and shows you the result "
        "as the next entry of DATA.transcript (observation). In each step answer with ONE JSON object, either\n"
        '  {"thought": "<why>", "action": "<tool name>", "args": {...}}  to call a tool, or\n'
        '  {"thought": "<why>", "final": ' + final_shape + "}  when you have enough evidence.\n"
        "If DATA.transcript is empty you have observed nothing yet, so start with an action. Call each tool at "
        "most twice. Base the final answer only on observations, not on guesses.\n\n"
        "GOAL:\n" + goal + "\n\nTOOLS (read-only):\n" + "\n".join(t.spec() for t in tools)
    )


def _observe(tool, args):
    try:
        out = tool.fn(**(args if isinstance(args, dict) else {}))
    except TypeError as e:
        out = {"error": f"bad arguments: {e}"}
    except Exception as e:                                   # a tool failure is an observation, not a crash
        out = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    text = json.dumps(out, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_OBS_CHARS else text[:MAX_OBS_CHARS] + "...(truncated)"


def run(goal, tools, final_shape, *, purpose, context=None, max_steps=6, budget_s=240, step_timeout=60,
        ask=None):
    """Returns (final dict or None, trace list). `ask` defaults to llm.ask_json (tests pass a fake)."""
    ask = ask or llm.ask_json
    by_name = {t.name: t for t in tools}
    instruction = _instruction(goal, tools, final_shape)
    trace, started, run_id = [], time.time(), f"{purpose}-{int(time.time())}"
    final = None
    for step in range(max_steps):
        left = budget_s - (time.time() - started)
        if left < 10:
            trace.append({"step": step, "stop": "time budget"})
            break
        reply = ask(instruction, {"context": context or {}, "transcript": trace},
                    timeout=min(step_timeout, left), purpose=f"react_{purpose}")
        if not isinstance(reply, dict):
            trace.append({"step": step, "stop": "no valid reply"})
            break
        thought = str(reply.get("thought") or "")[:600]
        bare = {k: v for k, v in reply.items() if k not in ("thought", "action", "args", "final")}
        if isinstance(reply.get("final"), dict) or ("action" not in reply and bare):
            # some models drop the {"final": ...} wrapper once they have the answer; the caller validates either way
            final = reply["final"] if isinstance(reply.get("final"), dict) else bare
            trace.append({"step": step, "thought": thought, "final": final})
            break
        name = reply.get("action")
        tool = by_name.get(name)
        if tool is None:
            obs = json.dumps({"error": f"unknown tool {name!r}; use one of {sorted(by_name)} or give final",
                              "your_reply": str(reply)[:300]}, ensure_ascii=False)
        elif sum(1 for t in trace if t.get("action") == name) >= 2:
            obs = json.dumps({"error": f"{name} already called twice; use the observations you have"})
        else:
            obs = _observe(tool, reply.get("args") or {})
        trace.append({"step": step, "thought": thought, "action": name, "args": reply.get("args"), "observation": obs})
    _log({"run": run_id, "purpose": purpose, "seconds": round(time.time() - started, 1), "steps": len(trace),
          "final": final, "trace": trace})
    return final, trace


def _log(row):
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_DIR / "react.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), **row}, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
