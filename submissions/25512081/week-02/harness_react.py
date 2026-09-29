"""Week 02 assignment — ReAct harness (full-transcript context).

**Axis 1 (context management): keep everything.** The whole transcript — every
Thought, tool call, and Observation — is resent on every model call. The model
re-decides each step with all prior evidence in view. Information-complete, but
tokens grow with every step. Contrast: harness_plan_execute.py drops raw
step history and carries only a compact ledger.

Other axes: [3] terminates on the model's Answer (no tool call) or a max_steps
cap; [4] tool errors return as Observations; [5] tools are read-only, so there
is no human-intervention point and interventions stay 0.
"""
import sys

from tools_shared import Meter, call_model, run_tool_calls

SYSTEM = (
    "You solve a task using the given tools. Each turn, write one line starting "
    "'Thought:' about what to do next, then either call a tool, or, when the "
    "task is done, reply with a line starting 'Answer:' and make no tool call."
)


def run_react(task, max_steps=10, log=print):
    meter = Meter()
    messages = [{"role": "system", "content": SYSTEM},   # [axis 1] this list
                {"role": "user", "content": task}]        #          only grows

    for step in range(max_steps):                # [axis 3] termination: cap
        msg, text, calls = call_model(messages, meter)   # full history resent
        if text.strip():
            log(f"[step {step + 1}] {text.strip()[:200]}")
        if not calls:                            # [axis 3] model chose to finish
            return text, meter
        run_tool_calls(msg, calls, messages, log)  # [axis 4] errors -> Observation
                                                   # [axis 1] appended, never trimmed
    return "MAX_STEPS reached: incomplete", meter


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer in HH:00 form."
    answer, m = run_react(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions}")
