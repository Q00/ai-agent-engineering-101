"""Week 02 assignment — Plan-then-Execute harness, reinterpreted from the starter.

Every control signal the starter asks the model to spell out in free text
(a bare JSON list for the plan, an 'OFF_PLAN:' prefix, an 'Answer:' prefix)
is a forced or offered tool call here instead: submit_plan, report_off_plan,
submit_answer. The lab's own log caught the starter's failure mode directly —
a run where the planner replied with a plain string instead of JSON and the
whole run aborted before a single tool ran (see ../lab/logs/plan_exec-05.txt).
The hypothesis under this rewrite is that forcing the shape through the
provider's own tool-call schema, instead of asking nicely in a system prompt
and parsing free text afterward, moves that failure from "every run, silently
until it isn't" to "visible in the tool schema, provider-enforced." Whether
that actually holds for a free-tier model is exactly what REPORT.md checks
against results.csv.
"""
import sys

from tools_shared import Chat, Meter, Reply, TASK_TOOL_SPECS

SYSTEM_PLAN = "You are a planner. Break the task into short, ordered steps."
SYSTEM_EXEC = (
    "You execute one step of a plan at a time, using the tools you are given "
    "when you need information. If a step cannot be done as planned, call "
    "report_off_plan instead of guessing. When asked for the final answer, "
    "call submit_answer."
)

PLAN_TOOL = {"type": "function", "function": {
    "name": "submit_plan",
    "description": "Submit the full plan as an ordered list of short step descriptions.",
    "parameters": {"type": "object",
                   "properties": {"steps": {"type": "array", "items": {"type": "string"}}},
                   "required": ["steps"]}}}

OFF_PLAN_TOOL = {"type": "function", "function": {
    "name": "report_off_plan",
    "description": "Call instead of continuing if the current step cannot be completed as planned.",
    "parameters": {"type": "object",
                   "properties": {"reason": {"type": "string"}},
                   "required": ["reason"]}}}

ANSWER_TOOL = {"type": "function", "function": {
    "name": "submit_answer",
    "description": "Submit the final answer to the task.",
    "parameters": {"type": "object",
                   "properties": {"answer": {"type": "string"}},
                   "required": ["answer"]}}}

EXEC_TOOLS = TASK_TOOL_SPECS + [OFF_PLAN_TOOL, ANSWER_TOOL]


def get_plan(chat: Chat, prompt: str, log):
    """Force a submit_plan call. Returns a list[str], or None on failure —
    mirrors the starter's parse_plan() returning None, but the failure mode
    is now 'model didn't call the tool / gave a malformed one', not 'model's
    free text wasn't valid JSON'."""
    chat.add_user(prompt)
    reply = chat.send(tool_specs=[PLAN_TOOL], force_tool="submit_plan")
    for call in reply.tool_calls:
        if call.name == "submit_plan":
            steps = call.args.get("steps")
            if isinstance(steps, list) and all(isinstance(s, str) for s in steps):
                chat.add_tool_result(call, "plan received")
                return steps
            log(f"[plan] submit_plan called with a bad shape: {call.args!r}")
            chat.add_tool_result(call, "error: steps must be a list of strings")
            return None
    log(f"[plan] model did not call submit_plan; got text instead: {reply.text[:300]!r}")
    return None


def run_plan_execute(task: str, max_replan: int = 1, max_tool_rounds: int = 3, log=print):
    meter = Meter()

    planner = Chat(SYSTEM_PLAN, meter)
    plan = get_plan(planner, f"Task: {task}", log)
    if plan is None:
        return "plan generation failed", meter, 0
    log(f"[plan] {plan}")

    executor = Chat(SYSTEM_EXEC, meter)
    executor.add_user(f"Task: {task}\nPlan: {plan}")
    replans = 0
    i = 0
    while i < len(plan):
        executor.add_user(f"Execute step {i + 1}: {plan[i]}")
        reply = executor.send(tool_specs=EXEC_TOOLS)

        off_plan_reason = None
        rounds = 0
        while True:
            control = [c for c in reply.tool_calls if c.name in ("report_off_plan", "submit_answer")]
            if control:
                call = control[0]
                if call.name == "report_off_plan":
                    off_plan_reason = call.args.get("reason", "")
                    executor.add_tool_result(call, "noted")
                else:  # submit_answer called early, inside a step
                    executor.add_tool_result(call, "noted early")
                    return call.args.get("answer", ""), meter, replans
                break
            if not reply.tool_calls:                 # step's own turn is done, no control call
                break
            executor.run_task_tools(reply, log)       # [axis 2] granularity lives in tools_shared
            rounds += 1                                # [axis 4] tool errors come back as Observations
            if rounds >= max_tool_rounds:
                off_plan_reason = "exceeded the tool-call budget for this step"
                break
            reply = executor.send(tool_specs=EXEC_TOOLS)

        log(f"[step {i + 1}] {reply.text.strip()[:300] or '(tool calls only)'}"
            + (f"  OFF_PLAN: {off_plan_reason}" if off_plan_reason else ""))

        if off_plan_reason and replans < max_replan:   # [axis 4] recovery: one bounded replan
            replans += 1
            new_plan = get_plan(
                planner,
                f"Step {i + 1} ({plan[i]}) failed: {off_plan_reason}\n"
                f"Submit the remaining steps as a new plan.",
                log)
            if new_plan is None:
                break
            plan = plan[:i] + new_plan
            log(f"[replan] {plan}")
            continue
        i += 1

    executor.add_user("Give the final answer now by calling submit_answer.")
    final = executor.send(tool_specs=[ANSWER_TOOL], force_tool="submit_answer")
    for call in final.tool_calls:
        if call.name == "submit_answer":
            return call.args.get("answer", ""), meter, replans
    return final.text, meter, replans


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form."
    answer, m, replans = run_plan_execute(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions} replans={replans}")
