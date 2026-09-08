"""Week 02 — ReAct harness, v2.

v1 (the starter, kept in v1_unmodified/) ended the run the moment the model
answered without a tool call. v2 changes ONE axis:

  [axis 3] termination: an Answer is not accepted on the model's word alone.
  The harness sends one verification turn asking the model to check its Answer
  against the Observations already in the transcript.
    'VERIFIED'          -> stop, keep the Answer
    a corrected Answer  -> stop, keep the correction
    tool calls          -> the model withdrew its Answer; keep looping
  max_verify=1 caps the extra cost, and the cap is explicit.

v2.1 (after run 25): a rejected verification made the model resume, re-check
with tools, and then reply with the bare word 'VERIFIED'. v2 treated that reply
as the final Answer, so a correct '14:00' was lost. v2.1 remembers the last
'Answer:' line and returns it when a post-budget reply is just 'VERIFIED'.

System prompt, tools, context handling, error recovery, intervention hook and
max_steps are identical to v1.
"""
import sys

from tools_shared import Chat, Meter

SYSTEM = (
    "You solve tasks with the tools you are given. Before every tool call, "
    "write one line that starts with 'Thought:' saying what you know and what "
    "you will do next. When the task is complete, reply with a line that starts "
    "with 'Answer:' and make no tool call."
)

# [axis 3] the verification turn. Deliberately generic: it names no task detail.
VERIFY = (
    "Before finishing, check your Answer against the Observations above. If "
    "every claim in it is supported by the tool results, reply with exactly "
    "'VERIFIED' and nothing else. If anything is inconsistent - for example a "
    "count of 0 for a pattern that the raw file you read clearly contains - do "
    "NOT reply VERIFIED: continue the task, call tools if needed, and give a "
    "corrected Answer."
)

# [axis 5] calls that must be approved by a human before they run.
# The starter tools are read-only, so this set is empty and interventions
# stay at 0. Add a tool that writes or deletes, and put its name here.
IRREVERSIBLE = set()


def ask_human(call) -> bool:
    answer = input(f"approve {call.name}({call.args})? [y/N] ").strip().lower()
    return answer == "y"


def run_react(task: str, max_steps: int = 8, max_verify: int = 1, log=print):
    meter = Meter()
    chat = Chat(SYSTEM, meter)                    # [axis 1] context: full history, every call
    chat.add_user(task)
    verifies, outcome, last_answer = 0, "not-reached", ""

    for step in range(max_steps):                 # [axis 3] termination: iteration cap
        reply = chat.send()
        if reply.text.strip():
            log(f"[step {step + 1}] {reply.text.strip()}")
        if "Answer:" in reply.text:
            last_answer = reply.text                # v2.1: remember the last real Answer

        if not reply.tool_calls:                  # the model says it is done
            if verifies >= max_verify:            # [axis 3] verification budget spent: accept
                if reply.text.strip().upper().startswith("VERIFIED") and last_answer:
                    log("[verify] re-confirmed after resuming; returning the last Answer")   # v2.1
                    return last_answer, meter, f"verify={outcome}-then-reconfirmed"
                return reply.text, meter, f"verify={outcome}-then-accepted"
            verifies += 1
            chat.add_user(VERIFY)                 # [axis 3] v2: one verification turn
            check = chat.send()
            if check.tool_calls:                  # model withdrew its Answer and resumed
                outcome = "rejected"
                log(f"[verify] rejected: model resumed with {len(check.tool_calls)} tool call(s)")
                reply = check                     # fall through and run those calls
            elif check.text.strip().upper().startswith("VERIFIED"):
                outcome = "confirmed"
                log("[verify] confirmed")
                return reply.text, meter, f"verify={outcome}"
            else:                                 # model gave a corrected Answer in text
                outcome = "revised"
                log(f"[verify] revised: {check.text.strip()[:300]}")
                return check.text, meter, f"verify={outcome}"

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

    return "MAX_STEPS reached: incomplete", meter, f"verify={outcome}"


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form."
    answer, m, info = run_react(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions} {info}")
