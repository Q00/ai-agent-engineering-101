"""Week 02 assignment — Plan-then-Execute harness (compact-ledger context).

**Axis 1 (context management): keep only a ledger.** The harness plans once,
then runs each step in a FRESH minimal context that carries only (a) the plan
and (b) a ledger of one-line findings from earlier steps. A step's raw tool
exchanges are thrown away once the step ends; only its distilled `FINDING:`
line survives. So context stays flat instead of growing. Contrast:
harness_react.py resends the entire transcript every call.

Other axes: [3] each step gets a bounded tool-call budget (max_tool_rounds);
[4] a step that reports OFF_PLAN triggers at most one replan (flexibility cap);
[5] read-only tools, so interventions stay 0.

The trade-off this design puts under test: dropping raw history saves tokens,
but the final answer is built from the ledger alone, so anything a step fails
to distill into its FINDING line is lost.
"""
import json
import re
import sys

from tools_shared import Meter, call_model, run_tool_calls

SYSTEM_PLAN = (
    "You are a planner. Output the plan as a short list of steps, one per line, "
    "each line starting with '- '. Output only the list and no other text."
)
SYSTEM_STEP = (
    "You execute ONE step of a plan using the given tools. You see the plan and "
    "a ledger of findings from earlier steps, but not their raw tool output. Do "
    "the current step, then end with a line starting 'FINDING:' that states the "
    "concrete result of this step in one sentence, including any exact counts "
    "you found. If the step cannot be done, reply with a line starting "
    "'OFF_PLAN:' explaining why."
)
SYSTEM_ANSWER = (
    "You are given the task and a ledger of findings. Reply with a single line "
    "starting 'Answer:' giving the final answer to the task."
)


def parse_plan(text):
    """Return a list of step strings. Accept a JSON array anywhere in the reply
    OR a bulleted/numbered list — free models often wrap the plan in prose, so a
    stray preamble should not sink the whole run. None if neither is present."""
    text = re.sub(r"```(?:json)?|```", "", text).strip()
    m = re.search(r"\[.*\]", text, re.S)              # JSON array, even after preamble
    if m:
        try:
            plan = json.loads(m.group(0))
            if isinstance(plan, list) and all(isinstance(s, str) for s in plan):
                return [s.strip() for s in plan if s.strip()]
        except json.JSONDecodeError:
            pass
    steps = []                                        # fallback: bullet/numbered lines
    for ln in text.splitlines():
        mm = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)", ln)
        if mm and mm.group(1).strip():
            steps.append(mm.group(1).strip())
    return steps or None


def run_plan_execute(task, max_replan=1, max_tool_rounds=4, log=print):
    meter = Meter()

    # 1) PLAN — one call, no tools
    _, raw, _ = call_model(
        [{"role": "system", "content": SYSTEM_PLAN},
         {"role": "user", "content":
             f"Task: {task}\nTools available: read_file(path), "
             f"count_pattern(path, pattern)."}],
        meter, tools=False)
    plan = parse_plan(raw)
    if plan is None:                             # a parse failure is one failure mode
        log(f"[plan] parse failed: {raw.strip()[:150]!r}")
        return "plan parse failed", meter, 0
    log(f"[plan] {plan}")

    ledger = []                                  # [axis 1] the only carried state
    replans = 0
    i = 0
    while i < len(plan):
        # Fresh minimal context: plan + ledger only. No raw history from
        # earlier steps enters here.
        step_msgs = [
            {"role": "system", "content": SYSTEM_STEP},
            {"role": "user", "content":
                f"Task: {task}\nPlan: {json.dumps(plan)}\n"
                f"Findings so far:\n"
                + ("\n".join(ledger) if ledger else "(none)")
                + f"\n\nExecute step {i + 1}: {plan[i]}"},
        ]
        msg, text, calls = call_model(step_msgs, meter)
        rounds = 0
        while calls:                             # [axis 3] bounded tool rounds
            run_tool_calls(msg, calls, step_msgs, log)
            msg, text, calls = call_model(step_msgs, meter)
            rounds += 1
            if rounds >= max_tool_rounds and calls:
                run_tool_calls(msg, calls, step_msgs, log)
                text, calls = "OFF_PLAN: step exceeded the tool-call budget", []
        log(f"[step {i + 1}] {text.strip()[:200]}")

        if text.strip().startswith("OFF_PLAN") and replans < max_replan:
            replans += 1                         # [axis 4] flexibility cap
            _, raw, _ = call_model(
                [{"role": "system", "content": SYSTEM_PLAN},
                 {"role": "user", "content":
                     f"Task: {task}\nStep {i + 1} ({plan[i]}) failed: "
                     f"{text.strip()[:150]}\nReply with the remaining steps as "
                     f"a list, one per line starting with '- '."}],
                meter, tools=False)
            new = parse_plan(raw)
            if new is None:
                log("[replan] parse failed")
                break
            plan = plan[:i] + new
            log(f"[replan] {plan}")
            continue

        # Distill the step into ONE ledger line; its raw tool exchange is dropped.
        m = re.search(r"FINDING:\s*(.+)", text.strip())
        ledger.append(f"- step {i + 1}: {(m.group(1) if m else text.strip())[:160]}")
        i += 1

    # 2) ANSWER — from the ledger alone (no tools, no raw history)
    _, ans, _ = call_model(
        [{"role": "system", "content": SYSTEM_ANSWER},
         {"role": "user", "content":
             f"Task: {task}\nFindings:\n" + "\n".join(ledger)
             + "\n\nGive the final answer now."}],
        meter, tools=False)
    return ans, meter, replans


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer in HH:00 form."
    answer, m, replans = run_plan_execute(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions} replans={replans}")
