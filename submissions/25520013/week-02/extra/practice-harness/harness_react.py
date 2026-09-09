"""Week 02 lab — the ReAct-style harness, written by the student.

Built from the lecture skeleton to the choices in decisions.md, so it differs
from the starter on two axes: termination is only an explicit finish(answer)
tool call (R2), and a reply that calls tools without a 'Thought:' line is
refused instead of executed (R4). The instructor's version is kept unchanged
in reference/harness_react.py for comparison.
"""
import re
import sys

from tools_shared import Chat, Meter

SYSTEM = (
    "You solve tasks with the tools you are given. Before every tool call, "
    "write one line that starts with 'Thought:' saying what you know and what "
    "you will do next. When you have the final answer, call the finish tool "
    "with it; never answer in plain text."
)

# [axis 3] termination: the model ends the loop only by calling this.
# finish is not in TOOLS_IMPL, so it never reaches run_tools; the loop below
# intercepts it and returns the answer it carries.
FINISH_SPEC = {
    "name": "finish",
    "description": "Submit the final answer and stop.",
    "parameters": {"type": "object",
                   "properties": {"answer": {"type": "string"}},
                   "required": ["answer"]},
}

THOUGHT_RE = re.compile(r"^\s*Thought:", re.M)
NUDGE = ("Do not answer in plain text. Call the finish tool with your final "
         "answer, or call a tool.")
DENY_NO_THOUGHT = ("denied: write a line starting with 'Thought:' before "
                   "calling tools, then call again")

# [axis 5] calls that must be approved by a human before they run.
# The starter tools are read-only, so this set is empty and interventions
# stay at 0. Add a tool that writes or deletes, and put its name here.
IRREVERSIBLE = set()


def ask_human(call) -> bool:
    answer = input(f"approve {call.name}({call.args})? [y/N] ").strip().lower()
    return answer == "y"


def run_react(task: str, max_steps: int = 8, log=print):
    """Run the loop. Returns (answer, meter, info)."""
    meter = Meter()
    # [axis 1] context: full history every call; finish is advertised as a tool
    chat = Chat(SYSTEM, meter, extra_tools=[FINISH_SPEC])
    chat.add_user(task)
    info = {"thought_violations": 0, "plain_text_answers": 0}

    for step in range(max_steps):                 # [axis 3] termination: iteration cap
        reply = chat.send()
        if reply.text.strip():
            log(f"[step {step + 1}] {reply.text.strip()}")

        finish = next((c for c in reply.tool_calls if c.name == "finish"), None)
        if finish is not None:                    # [axis 3] the only clean exit
            answer = str(finish.args.get("answer", ""))
            return answer, meter, info            # any other call in the reply is ignored

        if not reply.tool_calls:                  # [axis 3] plain text is NOT an exit
            info["plain_text_answers"] += 1
            log("  [reject] plain text without a finish call")
            chat.add_user(NUDGE)                  # costs one step, on purpose
            continue

        if not THOUGHT_RE.search(reply.text):     # [axis 4] format recovery
            info["thought_violations"] += 1
            log("  [reject] tool calls with no 'Thought:' line")
            for call in reply.tool_calls:         # answer every call, else the
                chat.add_tool_result(call, DENY_NO_THOUGHT)   # transcript breaks
            continue

        approved = []
        for call in reply.tool_calls:
            if call.name in IRREVERSIBLE and not ask_human(call):
                meter.interventions += 1          # [axis 5] intervention point
                chat.add_tool_result(call, "denied: human did not approve")
                continue
            approved.append(call)
        reply.tool_calls = approved
        chat.run_tools(reply, log)                # [axis 2] granularity lives in tools_shared
                                                  # [axis 4] errors come back as Observations

    return "MAX_STEPS reached: incomplete", meter, info


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form."
    answer, m, info = run_react(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions} "
          f"prompt_chars={m.prompt_chars}")
    print(info)
