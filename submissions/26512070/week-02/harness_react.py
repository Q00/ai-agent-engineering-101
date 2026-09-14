"""Week 02 - the ReAct arm.

Fills in the run_react skeleton from the lecture. Every step the model gets the
whole history back and decides again what to do; a tool result comes back as an
Observation and the next Thought reacts to it.

Where the five axes are set, and how this differs from the Plan-then-Execute arm:

  1 context management   the FULL history accumulates. Every Thought, every
                         Action and every Observation is resent on every turn.
                         Nothing is summarised or dropped.
  2 tool granularity     tools_shared.TOOLS - identical to the other arm.
  3 termination          the model declining to call a tool (it answers in
                         prose) OR the max_steps ceiling, whichever comes first.
  4 error recovery       a tool exception is turned into an Observation and fed
                         back, so the model can fix its own argument and retry.
  5 human intervention   IRREVERSIBLE is empty: reading and counting cannot be
                         undone wrongly, so this arm runs fully autonomous.
                         Held identical to the other arm on purpose.

Run one arm on its own:
    py harness_react.py                 # real model, spends requests
    py harness_react.py --dry-run       # scripted model, spends nothing
"""
import sys

from tools_shared import (TOOLS, TOOL_SCHEMAS, Meter, Reply, call_model,
                          observation, use_fake_model, utf8_console)

MAX_STEPS = 6

# [axis 5] Tools that would need a human to approve them before they run.
# This task reads and counts, so the set is empty and interventions stays 0.
# The Plan-then-Execute arm uses the same empty set.
IRREVERSIBLE = frozenset()

SYSTEM = """You are a log analyst working one step at a time.

You have two tools: read_file(path) and count_pattern(path, pattern).
count_pattern takes a Python regular expression and returns how many times it
matches in the file.

Work in small steps. Before each tool call, say in one sentence what you are
about to check and why. After you see the result, decide the next step from it.

When, and only when, you are certain of the answer, reply in prose with no tool
call. That reply is your final answer and it must contain the hour in HH:00
form."""


def ask_human(tool_call):
    """[axis 5] Never reached: IRREVERSIBLE is empty. Kept so the axis is
    visible in the code rather than silently absent."""
    print("    [intervention] approve %s ? -> auto-deny (no policy defined)" % tool_call)
    return False


def run_react(task, max_steps=MAX_STEPS, log=print, meter=None):
    """Returns (final_answer_or_None, meter, note).

    `meter` can be passed in by the caller so that a run which crashes part way
    through still reports the tokens it had already spent.
    """
    # [axis 1] context management: one growing list, nothing evicted.
    history = [{"role": "system", "content": SYSTEM},
               {"role": "user", "content": task}]
    meter = Meter() if meter is None else meter
    note = ""

    for step in range(max_steps):                    # [axis 3] iteration ceiling
        log("")
        log("--- step %d/%d ---" % (step + 1, max_steps))

        reply = call_model(history, meter, tools=TOOL_SCHEMAS)
        history.append(reply.as_message())

        if reply.text.strip():
            log("Thought: " + reply.text.strip())

        if reply.tool_call is None:                  # [axis 3] the model finished
            log("Final: " + reply.text.strip())
            return reply.text.strip(), meter, note or "finished by model"

        call = reply.tool_call

        # Print the call BEFORE running it. A tool that raises must still leave
        # a record of what it was asked to do (week-01, run-11 was a bare
        # traceback with no way to tell which arguments caused it).
        log("Action: " + str(call))

        if call.name in IRREVERSIBLE:                # [axis 5] intervention point
            if not ask_human(call):
                meter.interventions += 1
                history.append(observation(call.id, "denied: a human did not approve"))
                continue

        try:
            fn = TOOLS[call.name]
        except KeyError:
            obs = "error: no such tool %r. Available: %s" % (
                call.name, ", ".join(sorted(TOOLS)))
        else:
            try:
                obs = fn(**call.args)
            except Exception as e:                   # [axis 4] error -> Observation
                obs = "error: %s: %s" % (type(e).__name__, e)

        log("Observation: " + str(obs)[:600])
        history.append(observation(call.id, obs))

    # [axis 3] hard stop. No answer is a failure, and it is recorded as one.
    log("")
    log("MAX_STEPS reached without a final answer")
    return None, meter, "hit max_steps=%d" % max_steps


# --------------------------------------------------------------------------

DRY_RUN_SCRIPT = [
    ("I need to see the shape of the log before counting anything.",
     [("read_file", {"path": "app.log"})]),
    ("ERROR lines exist. Let me get the total so I can check my per-hour counts.",
     [("count_pattern", {"path": "app.log", "pattern": "ERROR"})]),
    ("Now the 14:00 hour. I will use a deliberately broken regex first.",
     [("count_pattern", {"path": "app.log", "pattern": "ERROR("})]),
    ("That regex was unterminated; retrying with a correct one.",
     [("count_pattern", {"path": "app.log", "pattern": r" 14:\d\d:\d\d ERROR"})]),
    ("Six is the highest hour I found.", [("no_such_tool", {"x": 1})]),
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
    answer, meter, note = run_react(task)
    print("")
    print("answer  : %r" % answer)
    print("meter   : %s" % meter)
    print("note    : %s" % note)


if __name__ == "__main__":
    main()
