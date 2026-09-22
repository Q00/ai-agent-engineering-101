"""Week 02 assignment — ReAct-style harness, reinterpreted from the starter.

The starter's termination condition (axis 3) is a text convention: the model
must literally write a line starting with 'Answer:' with no tool call, and
the harness (and, by extension, the model) has to get that formatting right
every time. Here termination is just "the model's reply carries no tool
calls" — that is already a structured, provider-checked signal, so there is
one fewer way for the harness to be wrong about the model's own decision to
stop. The system prompt still asks for a one-line rationale before each tool
call, but nothing parses that string; it is there for the human reading the
log, not for the harness's control flow.
"""
import sys

from tools_shared import Chat, Meter, TASK_TOOL_SPECS

SYSTEM = (
    "You solve tasks with the tools you are given. Before every tool call, "
    "say in one short sentence what you know and what you are about to do. "
    "Call a tool whenever you need information you do not already have. "
    "Stop calling tools and give your final answer directly once you can."
)

# [axis 5] tool names that need a human's yes/no before they run. The task
# tools here are both read-only, so this stays empty and interventions is 0.
IRREVERSIBLE = set()


def ask_human(call) -> bool:
    answer = input(f"approve {call.name}({call.args})? [y/N] ").strip().lower()
    return answer == "y"


def run_react(task: str, max_steps: int = 8, log=print):
    meter = Meter()
    chat = Chat(SYSTEM, meter)              # [axis 1] context: one growing transcript, every call sees it all
    chat.add_user(task)

    for step in range(max_steps):           # [axis 3] termination: iteration cap as the outer safety net
        reply = chat.send(tool_specs=TASK_TOOL_SPECS)
        if reply.text.strip():
            log(f"[step {step + 1}] {reply.text.strip()}")

        if not reply.tool_calls:            # [axis 3] termination: no tool call = model is done
            return reply.text, meter

        approved = []
        for call in reply.tool_calls:
            if call.name in IRREVERSIBLE and not ask_human(call):
                meter.interventions += 1    # [axis 5] intervention point
                chat.add_tool_result(call, "denied: human did not approve")
                continue
            approved.append(call)
        reply.tool_calls = approved
        chat.run_task_tools(reply, log)     # [axis 2] granularity lives in tools_shared
                                             # [axis 4] tool errors come back as Observations, not crashes

    return "MAX_STEPS reached: incomplete", meter


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form."
    answer, m = run_react(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions}")
