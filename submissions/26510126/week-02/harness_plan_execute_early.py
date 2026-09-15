"""Week 02 extension — Plan-then-Execute with an early exit.

Identical to `harness_plan_execute.py` except for one branch: when a step's
reply already carries a line starting with 'Answer:', the loop returns there
instead of walking the rest of the plan and then asking for the answer again.

Only element 3 (termination condition) changes. Context management is the
same two-Chat arrangement, the tools are whatever `tools_shared` exposes,
error recovery is the same shared `run_tools`, and there is still no
intervention point. `parse_plan` is imported from the original module rather
than copied, so the plan-parsing behaviour cannot drift between the two.
"""
import json
import sys

from harness_plan_execute import parse_plan
from tools_shared import Chat, Meter, Reply

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


def answer_line(text: str):
    """The first line that starts with 'Answer:', or None."""
    for line in (text or "").splitlines():
        if line.strip().lower().startswith("answer:"):
            return line.strip()
    return None


def run_plan_execute_early(task: str, max_replan: int = 1,
                           max_tool_rounds: int = 3, log=print):
    meter = Meter()

    planner = Chat(SYSTEM_PLAN, meter, tools=False)
    planner.add_user(f"Task: {task}\nAvailable tools: read_file(path), "
                     f"count_pattern(path, pattern).")
    raw = planner.send().text
    plan = parse_plan(raw)
    if plan is None:
        log(f"[plan] not valid JSON: {raw.strip()[:300]!r}")
        return "plan parse failed", meter, 0
    log(f"[plan] {plan}")

    executor = Chat(SYSTEM_EXEC, meter)
    executor.add_user(f"Task: {task}\nPlan: {json.dumps(plan)}")
    replans = 0
    i = 0
    while i < len(plan):
        executor.add_user(f"Execute step {i + 1}: {plan[i]}")
        reply = executor.send()
        rounds = 0
        while reply.tool_calls:
            executor.run_tools(reply, log)
            reply = executor.send()
            rounds += 1
            if rounds >= max_tool_rounds and reply.tool_calls:
                executor.run_tools(reply, log)
                reply = Reply("OFF_PLAN: step exceeded the tool-call budget", [])
                break
        log(f"[step {i + 1}] {reply.text.strip()[:300]}")

        # --- the only difference from harness_plan_execute.py ---
        early = answer_line(reply.text)
        if early:
            skipped = len(plan) - (i + 1)
            log(f"[early exit] answered at step {i + 1}; {skipped} step(s) "
                f"skipped, no final call")
            return reply.text, meter, replans
        # --------------------------------------------------------

        if reply.text.strip().startswith("OFF_PLAN") and replans < max_replan:
            replans += 1
            planner.add_user(f"Step {i + 1} ({plan[i]}) failed: {reply.text.strip()[:300]}\n"
                             f"Reply with a JSON list of the remaining steps.")
            raw = planner.send().text
            new_steps = parse_plan(raw)
            if new_steps is None:
                log(f"[replan] not valid JSON: {raw.strip()[:300]!r}")
                break
            plan = plan[:i] + new_steps
            log(f"[replan] {plan}")
            continue
        i += 1

    executor.add_user("Give the final answer now, starting with 'Answer:'.")
    final = executor.send()
    if final.tool_calls:
        executor.run_tools(final, log)
        final = executor.send()
    return final.text, meter, replans


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form."
    answer, m, replans = run_plan_execute_early(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions} replans={replans}")
