"""One contractor = one system prompt + one model call per announcement.

The three conditions differ here and nowhere else:

  baseline       A, B, C get three different skill strings
  homogeneous    all three get the same generalist skill string
  overconfident  baseline, plus one extra sentence on C's system prompt

Everything else in the file is shared by all three, which is what makes the
comparison a comparison.
"""
import json
import re
from dataclasses import dataclass

# ---------------------------------------------------------------- the team

SKILLS = {
    "A": "arithmetic and numeric computation",
    "B": "writing and rewriting text in plain language",
    "C": "programming, writing and debugging code",
}
GENERALIST = "general problem solving"

# the independent variable of the overconfident condition: this one sentence
OVERCONFIDENT = (" You are certain you can do any task well. "
                 "Always bid, with confidence 95 or higher.")


@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False


def build_team(condition: str):
    if condition == "baseline":
        return [Contractor(n, SKILLS[n]) for n in ("A", "B", "C")]
    if condition == "homogeneous":
        return [Contractor(n, GENERALIST) for n in ("A", "B", "C")]
    if condition == "overconfident":
        return [Contractor(n, SKILLS[n], overconfident=(n == "C"))
                for n in ("A", "B", "C")]
    raise ValueError(f"unknown condition {condition!r}")


# ---------------------------------------------------------------- messages

BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}. '
    "No code block, no text before or after the object."
)

# the four fields of Smith 1980 Fig. 1
ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now"
)


def system_prompt(c: Contractor) -> str:
    system = BID_SYSTEM.format(name=c.name, skill=c.skill)
    if c.overconfident:
        system += OVERCONFIDENT
    return system


# ---------------------------------------------------------------- parsing

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def _valid(obj):
    """A bid the manager can act on: the three fields, the right types, and a
    confidence inside the range the announcement asked for."""
    if not isinstance(obj, dict) or "bid" not in obj:
        return None
    if not isinstance(obj["bid"], bool):
        return None
    conf = obj.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        return None
    if not 0 <= conf <= 100:
        return None
    return {"bid": obj["bid"], "confidence": float(conf),
            "reason": str(obj.get("reason", ""))[:300]}


def parse_bid(raw: str):
    """Strict first: the whole reply must be one JSON object, because that is
    what the announcement asked for. A reply that only becomes JSON after a
    code fence is stripped is still a format violation and is counted as a
    parse failure, but the recovery is recorded so the report can say how many
    failures were formatting rather than refusal.

    Returns (bid_or_None, recovered_flag).
    """
    text = (raw or "").strip()
    try:
        strict = _valid(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        strict = None
    if strict is not None:
        return strict, False

    for candidate in _lenient_candidates(text):
        try:
            loose = _valid(json.loads(candidate))
        except (json.JSONDecodeError, TypeError):
            continue
        if loose is not None:
            return None, True
    return None, False


def _lenient_candidates(text: str):
    fence = _FENCE.search(text)
    if fence:
        yield fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        yield text[start:end + 1]
