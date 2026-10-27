"""Week 02 lab — the Plan-then-Execute harness, written by the student.

One call produces the whole plan as a JSON list, then each step is executed
in order with tools, and a step that reports OFF_PLAN triggers one replan
(max_replan=1). Three things differ from the starter, per decisions.md: an
executor step that already produced 'Answer:' ends the run and the remaining
steps are skipped (P5), a plan that is not a JSON list is asked for once more
(P6), and the executor has the same human-approval hook as the ReAct harness
(P7). The instructor's version is kept unchanged in
reference/harness_plan_execute.py.
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
    "If the step cannot be done as planned, reply with a line that starts with "
    "'OFF_PLAN:' and explain why. When asked for the final answer, reply with a "
    "line that starts with 'Answer:'."
)

ANSWER_RE = re.compile(r"^\s*Answer:", re.M)
PLAN_RETRY = ("Your reply was not a JSON list of strings. Reply with ONLY a "
              "JSON list of short step strings, no prose, no code fences.")

# [axis 5] same hook as harness_react.py, so the two harnesses differ in
# control flow and not in how a dangerous call is handled. Read-only tools,
# so the set is empty and interventions stay at 0.
IRREVERSIBLE = set()


def ask_human(call) -> bool:
    answer = input(f"approve {call.name}({call.args})? [y/N] ").strip().lower()
    return answer == "y"


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


def request_plan(planner: Chat, tag: str, info: dict, log):
    """One planner call, parsed. [axis 4] on a format failure ask once more
    for a bare JSON list. Returns the plan, or None if it failed twice."""
    raw = planner.send().text
    plan = parse_plan(raw)
    if plan is None:
        log(f"[{tag}] not valid JSON: {raw.strip()[:300]!r}")
        info["format_retries"] += 1
        planner.add_user(PLAN_RETRY)
        plan = parse_plan(planner.send().text)
    return plan


def run_step_tools(executor: Chat, reply: Reply, meter: Meter, log):
    """Run one reply's tool calls, holding back the irreversible ones."""
    approved = []
    for call in reply.tool_calls:
        if call.name in IRREVERSIBLE and not ask_human(call):
            meter.interventions += 1              # [axis 5] intervention point
            executor.add_tool_result(call, "denied: human did not approve")
            continue
        approved.append(call)
    reply.tool_calls = approved
    executor.run_tools(reply, log)                # [axis 2] granularity lives in tools_shared
                                                  # [axis 4] errors come back as Observations


def run_plan_execute(task: str, max_replan: int = 1,
                     max_tool_rounds: int = 3, log=print):
    """Run the loop. Returns (answer, meter, info)."""
    meter = Meter()
    info = {"replans": 0, "early_stop": 0, "format_retries": 0}

    # 1) PLAN: the whole plan in one call, no tools
    planner = Chat(SYSTEM_PLAN, meter, tools=False)   # [axis 1] planner context
    planner.add_user(f"Task: {task}\nAvailable tools: read_file(path), "
                     f"count_pattern(path, pattern).")
    plan = request_plan(planner, "plan", info, log)
    if plan is None:                              # a parse failure is one failure mode
        log("[plan] parse failed twice; giving up")
        return "plan parse failed", meter, info
    log(f"[plan] {plan}")

    # 2) EXECUTE: each step in order
    executor = Chat(SYSTEM_EXEC, meter)           # [axis 1] separate executor context
    executor.add_user(f"Task: {task}\nPlan: {json.dumps(plan)}")
    answer = None
    i = 0
    while i < len(plan):
        executor.add_user(f"Execute step {i + 1}: {plan[i]}")
        reply = executor.send()
        rounds = 0
        while reply.tool_calls:                   # tool calls inside one step
            run_step_tools(executor, reply, meter, log)
            reply = executor.send()
            rounds += 1
            if rounds >= max_tool_rounds and reply.tool_calls:   # [axis 3] budget
                run_step_tools(executor, reply, meter, log)      # keep the transcript valid
                reply = Reply("OFF_PLAN: step exceeded the tool-call budget", [])
                break
        log(f"[step {i + 1}] {reply.text.strip()[:300]}")

        if ANSWER_RE.search(reply.text):          # [axis 3] the answer arrived early
            answer = reply.text
            info["early_stop"] = 1
            log(f"[early-stop] answer at step {i + 1}, "
                f"skipped {len(plan) - (i + 1)} step(s)")
            break

        if reply.text.strip().startswith("OFF_PLAN") and info["replans"] < max_replan:
            info["replans"] += 1                  # flexibility cap
            planner.add_user(f"Step {i + 1} ({plan[i]}) failed: {reply.text.strip()[:300]}\n"
                             f"Reply with a JSON list of the remaining steps.")
            new_steps = request_plan(planner, "replan", info, log)
            if new_steps is None:
                log("[replan] parse failed twice; continuing with the current plan")
                i += 1
                continue
            plan = plan[:i] + new_steps
            log(f"[replan] {plan}")
            continue
        i += 1

    if answer is None:                            # no early stop: ask for the answer
        executor.add_user("Give the final answer now, starting with 'Answer:'.")
        final = executor.send()
        if final.tool_calls:                      # the model tried to keep going
            run_step_tools(executor, final, meter, log)
            final = executor.send()
        answer = final.text
    return answer, meter, info


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form."
    answer, m, info = run_plan_execute(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions} "
          f"prompt_chars={m.prompt_chars}")
    print(info)
