"""Deterministic contract-net manager; only the contractors use a model."""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONDITIONS = ("baseline", "homogeneous", "overconfident")
NAMES = ("A", "B", "C")
HEADER = ("run", "condition", "tasks", "correct", "messages", "unassigned",
          "misawards", "note")


def load_json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def reject_constant(value):
    raise ValueError("non-finite JSON number: " + value)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def parse_bid(raw):
    try:
        obj = json.loads(raw, parse_constant=reject_constant,
                         object_pairs_hook=unique_object)
        if not isinstance(obj, dict) or set(obj) != {"bid", "confidence", "reason"}:
            raise ValueError("expected exactly bid, confidence, reason")
        if type(obj["bid"]) is not bool:
            raise ValueError("bid must be boolean")
        score = obj["confidence"]
        if (type(score) not in (int, float) or not math.isfinite(score)
                or not 0 <= score <= 100):
            raise ValueError("confidence must be a finite number in [0, 100]")
        if not isinstance(obj["reason"], str) or not obj["reason"].strip():
            raise ValueError("reason must be a nonempty string")
        return obj, None
    except (ValueError, TypeError, OverflowError) as exc:
        return None, str(exc)


def system_prompt(condition, name, prompts):
    if condition not in CONDITIONS or name not in NAMES:
        raise ValueError("unknown condition or contractor")
    skill = (prompts["generalist"] if condition == "homogeneous"
             else prompts["skills"][name])
    result = prompts["system"].format(name=name, skill=skill)
    if condition == "overconfident" and name == "C":
        result += prompts["overconfident"]
    return result


def choose_winner(bids):
    eligible = [(name, bid) for name, bid in bids
                if bid is not None and bid["bid"]]
    return max(eligible, key=lambda pair: pair[1]["confidence"])[0] if eligible else None


def run_round(tasks, condition, prompts, call_model, emit):
    counts = dict(tasks=len(tasks), correct=0, messages=0, unassigned=0, misawards=0)
    stats = dict(calls=0, parse_fails=0, declines=0, input_tokens=0, output_tokens=0)
    for task in tasks:
        bids = []
        announcement = prompts["announcement"].format(id=task["id"], desc=task["desc"])
        for name in NAMES:
            counts["messages"] += 1
            emit("announcement", task=task["id"], contractor=name, text=announcement)
        for name in NAMES:
            system = system_prompt(condition, name, prompts)
            stats["calls"] += 1
            raw, usage = call_model(system, announcement, emit)
            stats["input_tokens"] += usage["input_tokens"]
            stats["output_tokens"] += usage["output_tokens"]
            bid, error = parse_bid(raw)
            emit("bid", task=task["id"], contractor=name, raw=raw,
                 parsed=bid, error=error)
            bids.append((name, bid))
            if bid is None:
                stats["parse_fails"] += 1
            elif bid["bid"]:
                counts["messages"] += 1
            else:
                stats["declines"] += 1
        winner = choose_winner(bids)
        if winner is None:
            counts["unassigned"] += 1
            emit("unassigned", task=task["id"])
        else:
            counts["messages"] += 1
            emit("award", task=task["id"], contractor=winner)
        # Gold enters only the evaluator, after the gold-blind award decision.
        outcome = "unassigned" if winner is None else (
            "correct" if winner == task["gold"] else "misawards")
        if winner is not None:
            counts[outcome] += 1
        emit("evaluation", task=task["id"], gold=task["gold"], winner=winner,
             outcome=outcome)
    emit("summary", counts=counts, stats=stats)
    return counts, stats
