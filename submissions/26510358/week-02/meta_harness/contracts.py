"""The search space and acceptance rule are owned by code, not the models."""
from collections import Counter
from dataclasses import asdict, dataclass
import json


@dataclass(frozen=True)
class Policy:
    max_steps: int = 8
    history_turns: int = 0  # zero keeps all complete assistant/tool turn groups
    observation_chars: int = 4000
    error_recovery: str = "feedback"

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != set(asdict(cls())):
            raise ValueError("Policy must have exactly the four documented fields")
        for key, low, high in (("max_steps", 2, 12), ("history_turns", 0, 8),
                               ("observation_chars", 256, 4000)):
            if type(value[key]) is not int or not low <= value[key] <= high:
                raise ValueError(f"Invalid policy range: {key}")
        if value["error_recovery"] not in ("feedback", "stop"):
            raise ValueError("error_recovery must be feedback or stop")
        return cls(**value)


def json_object(text):
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def gate(baseline, candidate, min_saving=0.10):
    """No case may lose successes. Equal accuracy needs measured token savings."""
    def identities(rows):
        return Counter((r["case"], r["repeat"]) for r in rows)
    if not baseline or identities(baseline) != identities(candidate):
        return False, "incomplete_or_mismatched_evaluation"
    if any(n != 1 for n in identities(baseline).values()):
        return False, "duplicate_evaluation"
    if any(r["tokens"] is None for r in baseline + candidate):
        return False, "unmeasured_usage"
    if not any(r["success"] for r in candidate):
        return False, "no_successful_runs"
    cases = {r["case"] for r in baseline}
    wins = lambda rows, case: sum(r["success"] for r in rows if r["case"] == case)
    if any(wins(candidate, c) < wins(baseline, c) for c in cases):
        return False, "case_accuracy_regression"
    if sum(r["success"] for r in candidate) > sum(r["success"] for r in baseline):
        return True, "more_successes_without_case_regression"
    old_tokens = sum(r["tokens"] for r in baseline)
    new_tokens = sum(r["tokens"] for r in candidate)
    if old_tokens > 0 and new_tokens <= old_tokens * (1 - min_saving):
        return True, "same_accuracy_and_at_least_10_percent_fewer_tokens"
    return False, "no_measured_improvement"
