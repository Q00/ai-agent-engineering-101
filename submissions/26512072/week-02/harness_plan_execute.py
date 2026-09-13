"""Week 02 starter — the Plan-then-Execute harness.

One call produces the whole plan as a JSON list. Then each step is executed
in order with tools. If a step reports OFF_PLAN, the plan is rebuilt once
(max_replan=1): that number is the flexibility cap, and it is explicit.

Condition A (runs 1-6) used SYSTEM_PLAN with the strict parser and failed at
the plan call in all three plan_exec runs. The planner prompt and the plan
parser are two separate candidate causes, so each is selectable here and each
is recorded in the conditions block of every log. Do not pool conditions.
"""
import json
import re
import sys

from tools_shared import Chat, Meter, Reply

SYSTEM_PLAN = (
    "You are a planner. Reply with a JSON list of short strings, one per step, "
    "and nothing else. No prose, no code fences."
)
# v2 states the output shape as a hard constraint and demonstrates it on a
# different task, so the shape is shown without naming this task's answer.
SYSTEM_PLAN_V2 = (
    "You are a planner. Output a JSON array of short step strings and nothing "
    "else. The first character of your reply must be '[' and the last must be "
    "']'. Do not restate the task, do not explain your reasoning, and do not "
    "use code fences.\n"
    "Example task: name the day with the most WARN lines in a log.\n"
    'Example reply: ["read_file to see the log format", '
    '"count_pattern for WARN on each day", "compare the counts and name the day"]'
)
SYSTEM_PLAN_V3 = (
    "You are a planner. Output exactly one valid JSON array containing three "
    "short step strings and nothing else. Do not output analysis, markdown, or "
    "the final answer. Make the steps fit this executor: the first step reads "
    "the input once, the second analyzes and counts from the returned text "
    "without calling another tool, and the third states the requested final "
    "answer. Each step may use at most one tool-call round.\n"
    "Example task: name the day with the most WARN lines in a log.\n"
    'Example reply: ["read_file the log once", '
    '"count WARN lines per day from the returned text without a tool", '
    '"state the day in the requested format"]'
)
PLAN_PROMPTS = {"v1": SYSTEM_PLAN, "v2": SYSTEM_PLAN_V2, "v3": SYSTEM_PLAN_V3}
SYSTEM_EXEC = (
    "Execute only the current step, using prior tool results already present "
    "in the conversation. Never repeat a completed tool call. A step may make "
    "at most one tool-call response; after its Observation, finish the step "
    "without another tool. For an analysis or counting step, inspect the "
    "read_file result already in context and do the counting yourself. Do not "
    "call count_pattern once per group. Before a tool call, write a short "
    "'Thought:' line. When a step is complete, start with 'STEP_DONE:'. "
    "If the step cannot be done as planned, reply with a line that starts with "
    "'OFF_PLAN:' and explain why. When asked for the final answer, reply with a "
    "line that starts with 'Answer:' and make no tool call."
)


def _as_plan(text: str):
    """Return the step list this text encodes, or None if it is not one."""
    try:
        plan = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(plan, list) and plan and all(isinstance(s, str) and s.strip() for s in plan):
        return plan
    return None


def iter_json_arrays(text: str):
    """Yield every balanced [...] substring, outermost only, left to right."""
    depth = 0
    start = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "[":
            if depth == 0:
                start = index
            depth += 1
        elif char == "]" and depth:
            depth -= 1
            if depth == 0:
                yield text[start:index + 1]


def parse_plan(text: str, tolerant: bool = False):
    """Return a list of step strings, or None if the model did not give a plan.

    strict    the whole reply must be the JSON array (conditions A and C)
    tolerant  the first embedded array that is a valid plan is accepted, so a
              plan wrapped in prose still executes (condition B). This can also
              accept an array the model wrote only as an aside; that outcome is
              reported rather than hidden.
    """
    stripped = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    plan = _as_plan(stripped)
    if plan is not None or not tolerant:
        return plan
    for candidate in iter_json_arrays(text):
        plan = _as_plan(candidate)
        if plan is not None:
            return plan
    return None


def run_plan_execute(task: str, max_replan: int = 1,
                     max_tool_rounds: int = 3, log=print, max_steps: int = 16,
                     meter=None, system_plan: str = SYSTEM_PLAN_V3,
                     plan_parser: str = "strict"):
    if max_replan not in (0, 1):
        raise ValueError("max_replan must be 0 or 1")
    if plan_parser not in ("strict", "tolerant"):
        raise ValueError("plan_parser must be 'strict' or 'tolerant'")
    tolerant = plan_parser == "tolerant"
    meter = meter if meter is not None else Meter(max_steps=max_steps, log=log)
    replans = 0

    # 1) PLAN: the whole plan in one call, no tools
    planner = Chat(system_plan, meter, tools=False)
    planner.add_user(f"Task: {task}\nAvailable tools: read_file(path), "
                     f"count_pattern(path, pattern).")
    raw = planner.send().text
    plan = parse_plan(raw, tolerant)
    if plan is None:
        log(f"[plan] not valid JSON: {raw!r}")
        if replans >= max_replan:
            return "plan parse failed", meter, replans
        replans += 1
        planner.add_user(
            "OFF_PLAN: the previous reply was not a valid JSON list. This is "
            "the one allowed replan. Reply with only a JSON list of the "
            "remaining steps; the first character must be '[' and the last ']'.")
        raw = planner.send().text
        plan = parse_plan(raw, tolerant)
        if plan is None:
            log(f"[replan] not valid JSON: {raw!r}")
            return "replan parse failed", meter, replans
        log(f"[replan] {plan}")
    else:
        log(f"[plan] {plan}")

    # 2) EXECUTE: each step in order
    executor = Chat(SYSTEM_EXEC, meter)
    executor.add_user(f"Task: {task}\nPlan: {json.dumps(plan)}")
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
            new_steps = parse_plan(raw, tolerant)
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
