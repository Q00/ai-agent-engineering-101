"""Week 02 — Plan-then-Execute harness, v2.

v1 (the starter, kept in v1_unmodified/) executed whatever plan the planner
returned. In the v1 runs 8 of 9 gpt-4o-mini plans contained steps that called
tools that do not exist (`extract_hour_from_timestamp(...)`) or called
count_pattern with three arguments; the executor then burned its per-step
budget trying to honour them. v2 changes ONE axis:

  [axis 4] error recovery: the plan is checked BEFORE execution. A step written
  as a call to a tool that does not exist, or with the wrong number of
  arguments, is an error. The planner is shown the offending steps and the real
  tool signatures and asked for a corrected list. max_plan_fixes=1 caps this,
  and the cap is explicit. Prose steps ("count ERROR lines by hour") are not
  flagged: deciding how to do them is the executor's job.
  The same check is applied to a replanned tail.

Planner/executor prompts, tools, per-step budget (max_tool_rounds), replan cap
(max_replan) and the forced final Answer are identical to v1.
"""
import json
import re
import sys

from tools_shared import Chat, Meter, Reply, TOOL_SPECS

SYSTEM_PLAN = (
    "You are a planner. Reply with a JSON list of short strings, one per step, "
    "and nothing else. No prose, no code fences."
)
SYSTEM_EXEC = (
    "You execute one step of a plan at a time with the tools you are given. "
    "If the step cannot be done as planned, reply with a line that starts with "
    "'OFF_PLAN:' and explain why. When asked for the final answer, reply with a "
    "line that starts with 'Answer:'."
)

# ---- [axis 4] plan check: built from the real tool schemas, no task knowledge
TOOL_ARITY = {t["name"]: len(t["parameters"]["properties"]) for t in TOOL_SPECS}
TOOL_SIGNATURES = "\n".join(
    f"- {t['name']}({', '.join(t['parameters']['properties'])}): {t['description']}"
    for t in TOOL_SPECS)
CALL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*\.?\s*$")
PLAN_FIX = (
    "These plan steps call tools that do not exist or pass the wrong number of "
    "arguments:\n{bad}\n\nThe only tools are:\n{tools}\n\nReply with a corrected "
    "JSON list of steps and nothing else."
)


def _split_args(s: str):
    return [a for a in re.findall(r"""'[^']*'|"[^"]*"|[^,]+""", s) if a.strip()]


def invalid_steps(plan):
    """Steps written as tool calls that cannot be executed as written."""
    bad = []
    for s in plan:
        m = CALL_RE.match(s)
        if not m:
            continue                              # prose step: left to the executor
        name, args = m.group(1), _split_args(m.group(2))
        if name not in TOOL_ARITY:
            bad.append(f"{s!r}: there is no tool named {name}")
        elif len(args) != TOOL_ARITY[name]:
            bad.append(f"{s!r}: {name} takes {TOOL_ARITY[name]} argument(s), got {len(args)}")
    return bad


def check_plan(planner, plan, budget: int, log):
    """[axis 4] validate before executing; ask the planner to fix, at most `budget` times.
    Returns (plan, fixes_used, invalid_steps_left)."""
    fixes = 0
    bad = invalid_steps(plan)
    while bad and fixes < budget:
        log(f"[plan-check] {len(bad)} invalid step(s): " + " | ".join(bad)[:300])
        planner.add_user(PLAN_FIX.format(bad="\n".join(bad), tools=TOOL_SIGNATURES))
        raw = planner.send().text
        fixes += 1
        fixed = parse_plan(raw)
        if fixed is None:
            log(f"[plan-check] fix was not valid JSON: {raw.strip()[:200]!r}")
            break
        plan, bad = fixed, invalid_steps(fixed)
        log(f"[plan-check] fixed plan: {plan}")
    if bad:
        log(f"[plan-check] executing anyway with {len(bad)} invalid step(s) left")
    elif fixes == 0:
        log("[plan-check] ok")
    return plan, fixes, len(bad)


def parse_plan(text: str):
    """Return a list of step strings, or None if the model did not give JSON."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        plan = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(plan, list) and all(isinstance(s, str) for s in plan):
        return plan
    return None


def run_plan_execute(task: str, max_replan: int = 1, max_tool_rounds: int = 3,
                     max_plan_fixes: int = 1, log=print):
    meter = Meter()
    fixes_total, invalid_left = 0, 0

    # 1) PLAN: the whole plan in one call, no tools
    planner = Chat(SYSTEM_PLAN, meter, tools=False)
    planner.add_user(f"Task: {task}\nAvailable tools: read_file(path), "
                     f"count_pattern(path, pattern).")
    raw = planner.send().text
    plan = parse_plan(raw)
    if plan is None:                              # a parse failure is one failure mode
        log(f"[plan] not valid JSON: {raw.strip()[:300]!r}")
        return "plan parse failed", meter, "replans=0;planfix=0;invalid_left=-1"
    log(f"[plan] {plan}")
    plan, f, invalid_left = check_plan(planner, plan, max_plan_fixes - fixes_total, log)   # [axis 4] v2
    fixes_total += f

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
        log(f"[step {i + 1}] {reply.text.strip()[:300]}")

        if reply.text.strip().startswith("OFF_PLAN") and replans < max_replan:
            replans += 1                          # flexibility cap
            planner.add_user(f"Step {i + 1} ({plan[i]}) failed: {reply.text.strip()[:300]}\n"
                             f"Reply with a JSON list of the remaining steps.")
            raw = planner.send().text
            new_steps = parse_plan(raw)
            if new_steps is None:
                log(f"[replan] not valid JSON: {raw.strip()[:300]!r}")
                break
            new_steps, f, invalid_left = check_plan(planner, new_steps, max_plan_fixes - fixes_total, log)  # [axis 4] v2
            fixes_total += f
            plan = plan[:i] + new_steps
            log(f"[replan] {plan}")
            continue
        i += 1

    executor.add_user("Give the final answer now, starting with 'Answer:'.")
    final = executor.send()
    if final.tool_calls:                          # the model tried to keep going
        executor.run_tools(final, log)
        final = executor.send()
    return final.text, meter, f"replans={replans};planfix={fixes_total};invalid_left={invalid_left}"


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form."
    answer, m, info = run_plan_execute(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions} {info}")
