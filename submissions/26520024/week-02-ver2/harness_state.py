"""Provider-neutral observation memory; no model calls or answer computation."""
from collections import deque
import json


class ObservationState:
    def __init__(self, max_batches=None):
        self.batches = deque(maxlen=max_batches)

    def observe(self, records):
        self.batches.append(records)

    def records(self):
        return [record for batch in self.batches for record in batch]

    def has_evidence(self):
        return any(record["ok"] for record in self.records())

    def final_answer(self, reply):
        text = reply.text.strip()
        return (not reply.tool_calls and self.has_evidence()
                and text.startswith("Answer:") and bool(text[len("Answer:"):].strip()))

    def prompt(self, task, plan=None, step=None, feedback=""):
        data = {"task": task, "observations": self.records()}
        if plan is not None:
            data.update(plan=plan, current_step=step)
        if feedback:
            data["harness_feedback"] = feedback
        return json.dumps(data, ensure_ascii=True)
