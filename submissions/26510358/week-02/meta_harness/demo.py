"""Scripted, offline integration demo. No real model performance is measured."""
from dataclasses import asdict
import json

from .clients import Completion
from .contracts import Policy


class DemoClient:
    def __init__(self, spec, budget, emit):
        self.spec, self.budget, self.emit = spec, budget, emit

    def complete(self, messages, *, tools=None, role="executor", response_schema=None):
        self.budget.claim()
        if not tools:
            if role == "reviewer":
                content = {"issues": ["Keep accumulated hour counts when pruning context."],
                           "recommendation": "Reduce observation length before dropping history."}
            else:
                content = {"policy": asdict(Policy(max_steps=6, observation_chars=1024,
                                                   history_turns=1 if role == "proposer" else 0)),
                           "rationale": "Scripted demo candidate; this is not a model-generated improvement."}
            message = {"role": "assistant", "content": json.dumps(content)}
        else:
            observations = {m["tool_call_id"]: m["content"] for m in messages if m["role"] == "tool"}
            if "read" not in observations:
                actions = [("read", "read_file", {"path": "app.log"})]
            else:
                missing = [h for h in range(24) if f"hour-{h}" not in observations]
                actions = [(f"hour-{h}", "count_pattern",
                            {"path": "app.log", "pattern": f"^....-..-.. {h:02d}:..:.. +ERROR "})
                           for h in missing[:16]]
            if actions:
                message = {"role": "assistant", "content": "Offline scripted tool request.",
                           "tool_calls": [{"id": ident, "type": "function", "function": {
                               "name": name, "arguments": json.dumps(args)}} for ident, name, args in actions]}
            else:
                counts = {h: int(observations[f"hour-{h}"]) for h in range(24)}
                hour = min(counts, key=lambda h: (-counts[h], h))
                message = {"role": "assistant", "content": f"Answer: {hour:02d}:00"}
        # Synthetic counters exercise accounting/gates; they are not tokenizer/API usage.
        result = Completion(message, len(json.dumps(messages)) // 4,
                            len(json.dumps(message)) // 4, 0.0)
        self.emit("demo_response", role=role, config=asdict(self.spec), messages=messages,
                  message=message, synthetic_input_tokens=result.input_tokens,
                  synthetic_output_tokens=result.output_tokens)
        return result

    def close(self):
        pass
