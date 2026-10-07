"""Week 02 - the Plan-then-Execute arm.

Call 1 asks for the whole plan as a JSON list. From call 2 on, each step of that
plan is executed in order. The plan is written before any tool has run, so it is
written blind; when an observation contradicts it, the harness is allowed to
replan exactly once. That ceiling is the point of this arm.

Where the five axes are set, and how this differs from the ReAct arm:

  1 context management   each execute step sees the task, the plan, and a short
                         line per finished step - NOT the transcript. Tool
                         output is summarised into one line before it is
                         carried forward, so nothing accumulates.
  2 tool granularity     tools_shared.TOOLS - identical to the other arm.
  3 termination          the plan running out of steps. There is no per-step
                         "am I done yet" decision. MAX_PLAN_STEPS truncates a
                         runaway plan.
  4 error recovery       a failed step sets off_plan, and off_plan is allowed to
                         trigger a replan at most MAX_REPLAN times. After that
                         the harness keeps executing the plan it has, right or
                         wrong.
  5 human intervention   IRREVERSIBLE is empty, exactly as in the ReAct arm.
                         interventions stays 0.

Run one arm on its own:
    py harness_plan_execute.py                 # real model, spends requests
    py harness_plan_execute.py --dry-run       # scripted model, spends nothing
"""
import json
import os
import re
import sys

from tools_shared import (TOOLS, TOOL_SCHEMAS, Meter, call_model, use_fake_model,
                          utf8_console)

# [axis 3] The longest plan this arm will write and execute. Matches the ReAct
# arm's ceiling so neither side is handed more work turns than the other.
MAX_PLAN_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "8"))

MAX_REPLAN = 1          # [axis 4] the flexibility ceiling, stated up front

IRREVERSIBLE = frozenset()   # [axis 5] identical to the ReAct arm

PLAN_PROMPT = """Task: {task}

You have exactly two tools:
  read_file(path)              returns the text of a file
  count_pattern(path, pattern) returns how many times a Python regex matches in a file

Write the plan as a JSON list of strings and output NOTHING else - no prose, no
code fence. Each string is one step, and one step must be doable with at most
one tool call. Use at most {max_steps} steps."""

STEP_PROMPT = """Task: {task}

The plan:
{plan}

Results so far:
{context}

Now carry out step {n}: {step}

Make exactly one tool call for this step. If the step needs no tool, answer it
in one short sentence instead."""

SUMMARY_PROMPT = """Task: {task}

The plan that was executed:
{plan}

What each step returned:
{context}

Give the final answer now, in one sentence. It must contain the hour in HH:00
form."""


def _parse_plan(raw):
    """The lecture notes that a plan that will not parse is itself a failure
    mode. A markdown fence is stripped because it is a formatting artifact, but
    nothing else is repaired - a plan that is not a JSON list of strings fails.
    """
    text = (raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    plan = json.loads(text)                     # raises on failure, on purpose
    if not isinstance(plan, list) or not plan:
        raise ValueError("plan is not a non-empty JSON list")
    return [str(s) for s in plan][:MAX_PLAN_STEPS]


def _ask_for_plan(task, meter, log, stricter=False):
    prompt = PLAN_PROMPT.format(task=task, max_steps=MAX_PLAN_STEPS)
    if stricter:
        prompt += ('\n\nYour previous reply could not be parsed. Output only the '
                   'JSON array, starting with [ and ending with ]. Example: '
                   '["read the file", "count the matches"]')
    reply = call_model([{"role": "user", "content": prompt}], meter)
    if reply.truncated:
        log("[truncated] the plan reply was cut off at the token ceiling - "
            "whatever JSON was coming never arrived")
    log("PLAN (raw): " + reply.text.strip()[:600])
    return _parse_plan(reply.text)


def execute_step(task, plan, step, n, context, meter, log):
    """One step. Returns a dict; off_plan True means the step did not do what
    the plan said it would."""
    # [axis 1] the model sees the plan and one line per finished step. It does
    # not see the transcript, and it does not see raw tool output from earlier
    # steps.
    msg = STEP_PROMPT.format(
        task=task,
        plan="\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(plan)),
        context="\n".join(context) or "(nothing yet)",
        n=n, step=step)

    reply = call_model([{"role": "user", "content": msg}], meter, tools=TOOL_SCHEMAS)

    if reply.text.strip():
        log("  note: " + reply.text.strip()[:300])

    call = reply.tool_call
    if call is None:
        # No tool call. Not automatically wrong - some steps are pure reasoning.
        text = reply.text.strip()
        log("  (no tool call)")
        return {"line": "step %d (%s) -> %s" % (n, step, text[:200] or "no output"),
                "off_plan": not text, "answerish": text}

    log("  Action: " + str(call))

    if call.name in IRREVERSIBLE:               # [axis 5] never taken here
        meter.interventions += 1
        return {"line": "step %d -> denied by human" % n, "off_plan": True}

    try:
        obs = TOOLS[call.name](**call.args)
        off = False
    except KeyError:
        obs = "error: no such tool %r" % call.name
        off = True
    except Exception as e:                      # [axis 4] the step went off plan
        obs = "error: %s: %s" % (type(e).__name__, e)
        off = True

    log("  Observation: " + str(obs)[:600])

    # [axis 1] tool output is folded to one line before it is carried forward.
    flat = str(obs).replace("\n", " ")
    if len(flat) > 200:
        flat = flat[:200] + " ...(truncated, %d chars)" % len(str(obs))
    return {"line": "step %d (%s) -> %s = %s" % (n, step, call, flat), "off_plan": off}


def run_plan_execute(task, max_replan=MAX_REPLAN, log=print, meter=None):
    """Returns (final_answer_or_None, meter, note).

    `meter` can be passed in by the caller so that a run which crashes part way
    through still reports the tokens it had already spent.
    """
    meter = Meter() if meter is None else meter
    replans = 0

    # ---- 1) PLAN -------------------------------------------------------
    log("--- plan ---")
    try:
        plan = _ask_for_plan(task, meter, log)
    except Exception as e:
        log("plan did not parse: %s: %s" % (type(e).__name__, e))
        if replans >= max_replan:
            return None, meter, "plan parse failed, no replan budget"
        replans += 1
        log("--- replan %d/%d (parse) ---" % (replans, max_replan))
        try:
            plan = _ask_for_plan(task, meter, log, stricter=True)
        except Exception as e2:
            log("replan did not parse either: %s: %s" % (type(e2).__name__, e2))
            return None, meter, "plan parse failed twice (replans=%d)" % replans

    log("plan: %s" % json.dumps(plan, ensure_ascii=False))

    # ---- 2) EXECUTE ----------------------------------------------------
    # [axis 3] Termination is the plan running out. The only total here is the
    # natural bound of what this arm can execute at all - the original plan plus
    # the one replanned plan it is allowed - so a replan that restarts at step 1
    # cannot loop. Runs 1-12 capped the total at 6 instead, which was a
    # free-tier budget number rather than a design decision, and it is gone.
    hard_bound = MAX_PLAN_STEPS * (1 + max_replan)
    context = []
    i = 0
    steps_done = 0
    while i < len(plan) and steps_done < hard_bound:
        log("")
        log("--- step %d/%d (turn %d/%d) ---"
            % (i + 1, len(plan), steps_done + 1, hard_bound))
        result = execute_step(task, plan, plan[i], i + 1, context, meter, log)
        context.append(result["line"])
        steps_done += 1

        if result.get("off_plan") and replans < max_replan:
            replans += 1
            log("")
            log("--- replan %d/%d (off_plan at step %d) ---"
                % (replans, max_replan, i + 1))
            try:
                plan = _replan(task, plan, context, meter, log)
                i = 0           # the corrected plan is executed from its start
                continue
            except Exception as e:
                log("replan did not parse: %s: %s" % (type(e).__name__, e))
                # [axis 4] budget spent. Keep executing the plan already in hand.
        i += 1

    if steps_done >= hard_bound and i < len(plan):
        log("")
        log("step ceiling reached with %d plan step(s) unexecuted" % (len(plan) - i))

    # ---- 3) SUMMARISE --------------------------------------------------
    log("")
    log("--- summarise ---")
    reply = call_model([{"role": "user", "content": SUMMARY_PROMPT.format(
        task=task,
        plan="\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(plan)),
        context="\n".join(context) or "(nothing)")}], meter)
    answer = reply.text.strip()
    log("Final: " + answer)
    return answer, meter, "replans=%d steps=%d plan_len=%d" % (
        replans, steps_done, len(plan))


def _replan(task, old_plan, context, meter, log):
    """[axis 4] The one correction this arm is allowed."""
    prompt = PLAN_PROMPT.format(task=task, max_steps=MAX_PLAN_STEPS) + (
        "\n\nA previous plan went wrong. The previous plan was:\n%s\n\n"
        "What happened:\n%s\n\nWrite a corrected plan as a JSON list of strings "
        "and output nothing else." % (
            json.dumps(old_plan, ensure_ascii=False), "\n".join(context)))
    reply = call_model([{"role": "user", "content": prompt}], meter)
    log("REPLAN (raw): " + reply.text.strip()[:600])
    return _parse_plan(reply.text)


# --------------------------------------------------------------------------

DRY_RUN_SCRIPT = [
    ("```json\n[\"read app.log\", \"count ERROR lines for the busiest hour\", "
     "\"report the hour\"]\n```", []),
    ("Reading the log.", [("read_file", {"path": "app.log"})]),
    ("Counting with a broken regex.",
     [("count_pattern", {"path": "app.log", "pattern": "ERROR("})]),
    ("[\"read app.log\", \"count ERROR at 14\", \"report\"]", []),
    ("Reading again.", [("read_file", {"path": "app.log"})]),
    ("Counting 14:00.",
     [("count_pattern", {"path": "app.log", "pattern": r" 14:\d\d:\d\d ERROR"})]),
    ("14:00 had the most.", []),
    ("The hour with the most ERROR lines is 14:00.", []),
]


def main():
    utf8_console()
    if "--dry-run" in sys.argv:
        use_fake_model(DRY_RUN_SCRIPT)
    from run_ab import read_task
    task, expected = read_task()
    print("task    : " + task)
    print("expected: " + expected)
    answer, meter, note = run_plan_execute(task)
    print("")
    print("answer  : %r" % answer)
    print("meter   : %s" % meter)
    print("note    : %s" % note)


if __name__ == "__main__":
    main()
