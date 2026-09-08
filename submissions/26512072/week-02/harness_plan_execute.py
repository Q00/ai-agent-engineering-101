"""Week 02 starter — the Plan-then-Execute harness.

One call produces the whole plan as a JSON list. Then each step is executed
in order with tools. If a step reports OFF_PLAN, the plan is rebuilt once
(max_replan=1): that number is the flexibility cap, and it is explicit.
"""
import json
import re
import sys

from tools_shared import Chat, Meter, Reply

SYSTEM_PLAN = (
    "You are a planner. Reply with a JSON list of short strings, one per step, "
    "and nothing else. No prose, no code fences."
)
SYSTEM_EXEC = (
    "You execute one step of a plan at a time with the tools you are given. "
    "Before each tool call, write a short 'Thought:' line about the next action. "
    "If the step cannot be done as planned, reply with a line that starts with "
    "'OFF_PLAN:' and explain why. When asked for the final answer, reply with a "
    "line that starts with 'Answer:'."
)


def parse_plan(text: str):
    """Return a list of step strings, or None if the model did not give JSON."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        plan = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(plan, list) and plan and all(isinstance(s, str) and s.strip() for s in plan):
        return plan
    return None


def run_plan_execute(task: str, max_replan: int = 1,
                     max_tool_rounds: int = 3, log=print, max_steps: int = 16,
                     meter=None):
    if max_replan not in (0, 1):
        raise ValueError("max_replan must be 0 or 1")
    meter = meter if meter is not None else Meter(max_steps=max_steps, log=log)

    # 1) PLAN: the whole plan in one call, no tools
    planner = Chat(SYSTEM_PLAN, meter, tools=False)
    planner.add_user(f"Task: {task}\nAvailable tools: read_file(path), "
                     f"count_pattern(path, pattern).")
    raw = planner.send().text
    plan = parse_plan(raw)
    if plan is None:                              # a parse failure is one failure mode
        log(f"[plan] not valid JSON: {raw!r}")
        return "plan parse failed", meter, 0
    log(f"[plan] {plan}")

    # 2) EXECUTE: each step in order
    executor = Chat(SYSTEM_EXEC, meter)
    executor.add_user(f"Task: {task}\nPlan: {json.dumps(plan)}")
    replans = 0
    i = 0
    while i < len(plan):
        executor.add_user(f"Execute step {i + 1}: {plan[i]}")
        reply = executor.send()
        rounds = 0
        while reply.tool_calls:                   # tool calls inside one step
            executor.run_tools(reply, log)
            reply = executor.send()
            rounds += 1
            if rounds >= max_tool_rounds and reply.tool_calls:
                executor.run_tools(reply, log)    # answer every call so the transcript stays valid
                reply = Reply("OFF_PLAN: step exceeded the tool-call budget", [])
                break
        log(f"[step {i + 1}] {reply.text}")

        if reply.text.strip().startswith("OFF_PLAN") and replans >= max_replan:
            return "OFF_PLAN: replan budget exhausted; incomplete", meter, replans

        if reply.text.strip().startswith("OFF_PLAN") and replans < max_replan:
            replans += 1                          # flexibility cap
            planner.add_user(f"Step {i + 1} ({plan[i]}) failed: {reply.text}\n"
                             f"Reply with a JSON list of the remaining steps.")
            raw = planner.send().text
            new_steps = parse_plan(raw)
            if new_steps is None:
                log(f"[replan] not valid JSON: {raw!r}")
                return "replan parse failed", meter, replans
            plan = plan[:i] + new_steps
            log(f"[replan] {plan}")
            continue
        i += 1

    executor.add_user("Give the final answer now, starting with 'Answer:'.")
    final = executor.send()
    if final.tool_calls:                          # the model tried to keep going
        executor.run_tools(final, log)
        final = executor.send()
    if final.tool_calls:
        return "final answer still requested tools: incomplete", meter, replans
    return final.text, meter, replans


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form."
    answer, m, replans = run_plan_execute(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions} replans={replans}")
