"""Week 02 starter — the ReAct-style harness.

Every step: the model writes a Thought, calls a tool (Action), sees the result
(Observation), and decides again. The five axes from the lecture are marked.
"""
import sys

from tools_shared import Chat, Meter

SYSTEM = (
    "You solve tasks with the tools you are given. Before every tool call, "
    "write one line that starts with 'Thought:' saying what you know and what "
    "you will do next. When the task is complete, reply with a line that starts "
    "with 'Answer:' and make no tool call."
)

# [axis 5] calls that must be approved by a human before they run.
# The starter tools are read-only, so this set is empty and interventions
# stay at 0. Add a tool that writes or deletes, and put its name here.
IRREVERSIBLE = {"count_pattern"}
# [design change A] count_pattern is the tool that produces the graded
# answer, so every call to it needs a human's go-ahead before it runs.
# read_file stays automatic -- it only inspects, it never concludes.


def ask_human(call) -> bool:
    answer = input(f"approve {call.name}({call.args})? [y/N] ").strip().lower()
    return answer == "y"


def run_react(task: str, max_steps: int = 8, log=print):
    meter = Meter()
    chat = Chat(SYSTEM, meter)                    # [axis 1] context: full history, every call
    chat.add_user(task)

    for step in range(max_steps):                 # [axis 3] termination: iteration cap
        reply = chat.send()
        if reply.text.strip():
            log(f"[step {step + 1}] {reply.text.strip()}")

        if not reply.tool_calls:                  # [axis 3] the model chose to finish
            return reply.text, meter

        approved = []
        for call in reply.tool_calls:
            if call.name in IRREVERSIBLE:
                meter.interventions += 1          # [axis 5] intervention point: every consult counts
                if not ask_human(call):
                    chat.add_tool_result(call, "denied: human did not approve")
                    continue
            approved.append(call)
        reply.tool_calls = approved
        chat.run_tools(reply, log)                # [axis 2] granularity lives in tools_shared
                                                  # [axis 4] errors come back as Observations

    return "MAX_STEPS reached: incomplete", meter


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form."
    answer, m = run_react(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions}")
