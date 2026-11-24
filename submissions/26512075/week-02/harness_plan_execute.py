""" Edited from Week 02 starter — the Plan-then-Execute harness.

One call produces the whole plan as a JSON list. Then each step is executed
in order with tools. If a step reports OFF_PLAN, the plan is rebuilt once
(max_replan=1): that number is the flexibility cap, and it is explicit.
"""

from __future__ import annotations

import json
import re
import sys


from typing import Callable
from tools_shared import Chat, Meter, Reply

SYSTEM_PLAN = """\
    You are a planner. Reply with a JSON list of 1 to {max_steps} short strings, one per step,
    one per goal-level step, and nothing else. Do not use prose or code fences

    Rules:
    - Keep the plan compact; do not create one step per repeated item.
    - Group independent calls of the same kind into one step. One step may request
    several tool calls in a single response.
    - Prefer count_pattern over reading an entire file when counting is sufficient.
    - Patterns must identify the intended field, not an ambiguous substring.
    - Do not include a step that merely returns or formats the final answer. The harness requests the final answer separately.
"""

SYSTEM_EXEC = """\
    You execute one step of a plan at a time with the tools you are given.


    Rules:
    - When a step needs several independent tool calls, issue all of them together
    in one response.
    - When count_pattern targets an hour in a timestamp, bind the pattern to the
    complete timestamp field. For example:
    '[0-9]{4}-[0-9]{2}-[0-9]{2} 14:[0-5][0-9]:[0-5][0-9] ERROR'
    Never use a bare pattern such as '14:.*ERROR', because it may match minutes.
    - After the tools return, state the step result concisely, including evidence
    needed by later steps.
    - If the step cannot be completed as planned, reply with a line beginning
    exactly with 'OFF_PLAN:' and explain the concrete reason.
    - Do not give the final answer until explicitly requested.
    - When asked for the final answer, reply with a line beginning 'Answer:'.
"""


def parse_plan(text: str, max_steps: int) ->list[str] | None:
    """Return a list of step strings, or None if the model did not give JSON."""
    
    text = text.strip()

    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if fenced:
        text = fenced.group(1).strip()

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    
    if not isinstance(value, list) or not 1 <= len(value) <= max_steps:
        return None
    
    if not all(isinstance(step, str) and step.strip() for step in value):
        return None
    
    return [step.strip() for step in value]

# added
def req_plan(
    planner: Chat,
    prompt: str,
    max_steps: int,
    log: Callable[[str], None]
) -> list[str] | None:
    planner.add_user(prompt)
    raw = planner.send().text

    plan = parse_plan(raw, max_steps=max_steps)

    if plan is not None:
        return plan
    
    log(f"[plan] invalid or too long: {raw.strip()[:300]!r}")




def create_executor(
    task: str,
    plan: list[str],
    completed: list[dict[str, str]],
    meter: Meter
) -> Chat:
    executor = Chat(SYSTEM_EXEC, meter)
    context = [
        f"Task: {task}",
        f"Current plan: {json.dumps(plan, ensure_ascii=False)}"
    ]

    if completed:
        context.append(
            "previously completed steps and outputs: " + json.dumps(completed, ensure_ascii=False)
        )
    

    executor.add_user("\n".join(context))

    return executor






def run_plan_execute(task: str, max_replan: int = 1,
                     max_tool_rounds: int = 3, max_plan_steps: int = 4, log=print):
    meter = Meter()

    # 1) PLAN: the whole plan in one call, no tools
    planner = Chat(SYSTEM_PLAN, meter, tools=False)
    planner.add_user(f"Task: {task}\nAvailable tools: read_file(path), "
                     f"count_pattern(path, pattern).")
    raw = planner.send().text
    plan = parse_plan(raw, max_steps = max_plan_steps)
    if plan is None:                              # a parse failure is one failure mode
        log(f"[plan] not valid JSON: {raw.strip()[:300]!r}")
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
        log(f"[step {i + 1}] {reply.text.strip()[:300]}")

        if reply.text.strip().startswith("OFF_PLAN") and replans < max_replan:
            replans += 1                          # flexibility cap
            planner.add_user(f"Step {i + 1} ({plan[i]}) failed: {reply.text.strip()[:300]}\n"
                             f"Reply with a JSON list of the remaining steps.")
            raw = planner.send().text
            new_steps = parse_plan(raw, max_steps = max_plan_steps)
            if new_steps is None:
                log(f"[replan] not valid JSON: {raw.strip()[:300]!r}")
                break
            plan = plan[:i] + new_steps
            log(f"[replan] {plan}")
            continue
        i += 1

    executor.add_user("Give the final answer now, starting with 'Answer:'.")
    final = executor.send()
    if final.tool_calls:                          # the model tried to keep going
        executor.run_tools(final, log)
        final = executor.send()
    return final.text, meter, replans

# added
def run_plan_execute_version_edited(task: str, max_replan: int = 1, max_tool_rounds: int = 3, max_plan_steps: int = 4,  inject_off_plan_once: bool = True, log: Callable[[str], None] = print):

    if max_replan < 0:
        raise ValueError("max replan must be non-negative")
    if max_tool_rounds < 1:
        raise ValueError("max_tool_rounds must be greater than 1")
    if max_plan_steps < 1:
        raise ValueError("max_plan_steps must be greater than 1")

    meter = Meter()

    # 1) PLAN
    planner = Chat(SYSTEM_PLAN.format(max_steps=max_plan_steps), meter, tools=False)


    planner_prompt = (
        f"Task: {task}\n"
        "Available tools:\n"
        "- read_file(path): returns file contents.\n"
        "- count_pattern(path, pattern): counts lines matching a Python regular "
        "expression. For structured logs, bind the regex to the timestamp field "
        "rather than using a bare hour substring."
    )


    plan = req_plan(planner, planner_prompt, max_plan_steps, log)

    log(f"[plan] {plan}")

    # 2) EXECUTE
    completed: list[dict[str, str]] = []
    replans = 0

    if plan is None:
        return "plan creation failed", meter, replans
            
    executor = create_executor(task, plan, completed, meter)
    # executor.add_user(f"Task: {task}\nPlan: {json.dumps(plan)}")

    i = 0
    while i < len(plan):
        step = plan[i]
        executor.add_user(f"Execute step {i + 1}: {step}")
        reply = executor.send()
        rounds = 0
        budget_exceeded = False

        while reply.tool_calls: # tool calls inside one step
            if rounds >= max_tool_rounds:
                budget_exceeded = True
                break                   
            executor.run_tools(reply, log)
            reply = executor.send()
            rounds += 1
        
        if budget_exceeded:
            step_text = (
                "OFF_PLAN: step exceeded the tool-call budget "
                f"({max_tool_rounds})"
            )
        else:
            step_text = reply.text.strip()


        # 테스트 전용 failure injection
        if inject_off_plan_once and replans == 0:
            step_text = "OFF_PLAN: injected failure for replan test"
            
        log(f"[step {i + 1}] {step_text[:300]}")

        if step_text.startswith("OFF_PLAN:"):
            if replans >= max_replan:
                return (
                    f"execution failed after {replans} replans: {step_text}",
                    meter,
                    replans
                )
            
            replans += 1

            replan_prompt = (
                f"Task: {task}\n"
                f"Complete evidence:{json.dumps(completed, ensure_ascii=False)}\n"
                f"failed step: {step}\n"
                f"failure: {step_text[:1000]}\n"
                "Reply with only a JSON list of compact steps for the remaining "
                "work. Recompute earlier evidence if the failure invalidated it. "
            )

            new_plan = req_plan(
                planner,
                replan_prompt,
                max_plan_steps,
                log
            )

            if new_plan is None:
                return "replan created failed", meter, replans

            plan = new_plan
            i = 0
            log(f"[replan {replans}] {plan}")
            
            executor = create_executor(task, plan, completed, meter)
            continue

        completed.append(
            {
                "step": step,
                "result": step_text[:4000]
            }
        )
        i += 1

    executor.add_user(
        "Give the final answer now, starting with 'Answer:'. Do not call tools "
    )

    final = executor.send()
    if final.tool_calls:
        return "execution failed: final answer attempted additional tools", meter, replans
    
    final_text = final.text.strip()

    if not final_text.startswith("Answer:"):
        final_text = f"Answer: {final_text}"
        
    return final_text, meter, replans


if __name__ == "__main__":
    default_task = (
        "In app.log, which hour (HH:00) has the most ERROR lines? "
        " Answer with the hour in HH:00 form."
    )
    task = sys.argv[1] if len(sys.argv) > 1 else default_task
    answer, meter, replans = run_plan_execute(task)
    print(answer)
    print(f"tokens={meter.tokens} iters={meter.iters} interventions={meter.interventions} replans={replans}")