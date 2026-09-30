"""Contract net (Smith 1980): one manager, three LLM contractors.

announce -> bid -> award. A contractor is one system prompt plus one model
call per announcement. The bid is the model's own verbalized confidence.
"""

import json
import os
from dataclasses import dataclass

# ---------------------------------------------------------------- fixed settings
# Control variables. Identical in every condition and every run.

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
TEMPERATURE = 0.0
MAX_TOKENS = 200
PROVIDER = "openai"

SKILLS = {"A": "arithmetic and math calculation",
          "B": "writing and rewriting natural-language text",
          "C": "writing and fixing code"}
GENERALIST = "general problem solving"

BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}'
)
OVERCONFIDENT = " You are certain you can do any task well. Always bid, with confidence 95 or higher."

ANNOUNCEMENT = (  # the four fields of Smith 1980 Fig. 1
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now"
)

CONDITIONS = ("baseline", "homogeneous", "overconfident")


# ---------------------------------------------------------------- model


_client = None


def call_model(system: str, user: str) -> str:
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI()
    resp = _client.chat.completions.create(
        model=MODEL, temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}])
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------- contractors


@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False

    def system_prompt(self) -> str:
        system = BID_SYSTEM.format(name=self.name, skill=self.skill)
        if self.overconfident:  # independent variable: the only line that differs
            system += OVERCONFIDENT
        return system


def make_contractors(condition: str) -> list:
    if condition == "baseline":
        return [Contractor(n, s) for n, s in SKILLS.items()]
    if condition == "homogeneous":
        return [Contractor(n, GENERALIST) for n in SKILLS]
    if condition == "overconfident":
        return [Contractor(n, s, overconfident=(n == "C")) for n, s in SKILLS.items()]
    raise ValueError(f"unknown condition {condition!r}")


def parse_bid(raw: str):
    """Return (bid, confidence, reason), or None when the reply is not the
    JSON object we asked for. Strict on purpose: no fence stripping."""
    try:
        obj = json.loads(raw.strip())
        bid, conf = obj["bid"], obj["confidence"]
        if not isinstance(bid, bool) or not isinstance(conf, (int, float)):
            return None
        return bid, float(conf), str(obj.get("reason", ""))
    except (ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------- manager


def run_round(condition: str, tasks: list, log=print) -> dict:
    """Process every task once. Returns the counts for results.csv."""
    contractors = make_contractors(condition)
    counts = dict(tasks=len(tasks), correct=0, messages=0,
                  unassigned=0, misawards=0, parse_fails=0)

    for t in tasks:
        cid, desc, gold = t["id"], t["desc"], t["gold"]
        announcement = ANNOUNCEMENT.format(cid=cid, desc=desc)
        log(f"\n=== task {cid} (gold {gold}) ===")
        bids = []
        for c in contractors:
            log(f"ANNOUNCE -> {c.name}: {desc}")
            counts["messages"] += 1
            raw = call_model(c.system_prompt(), announcement)
            parsed = parse_bid(raw)
            if parsed is None:
                counts["parse_fails"] += 1
                log(f"  PARSE-FAIL {c.name} (counted as no bid): {raw!r}")
                continue
            bid, conf, reason = parsed
            if bid:
                counts["messages"] += 1
                bids.append((c.name, conf))
                log(f"  BID    {c.name} confidence={conf:g} reason={reason}")
            else:
                log(f"  NO-BID {c.name} confidence={conf:g} reason={reason}")

        if not bids:
            counts["unassigned"] += 1
            log("AWARD: none (no bids) -> UNASSIGNED")
            continue
        # highest confidence wins; ties go to the first to answer
        winner, conf = max(bids, key=lambda b: b[1])
        counts["messages"] += 1
        if winner == gold:
            counts["correct"] += 1
            verdict = "correct"
        else:
            counts["misawards"] += 1
            verdict = f"MISAWARD (gold {gold})"
        log(f"AWARD -> {winner} (confidence={conf:g}) {verdict}")

    return counts
