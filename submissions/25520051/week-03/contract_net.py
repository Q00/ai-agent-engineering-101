"""Week 03 — contract net protocol: manager announces, contractors bid, manager awards.

Three contractors, fixed identities across conditions (coder, writer, analyst),
so tasks.json's gold labels stay meaningful in every condition. What changes
per condition is each contractor's system prompt:

  baseline       three distinct skills, one of which matches each task's gold
  homogeneous    all three get the same generalist prompt
  overconfident  baseline, but the "writer" contractor is told to bid on
                 everything with high confidence, regardless of fit

Message counting matches the spec: one announcement per contractor, one bid
per contractor (even an unparseable one — the contractor did reply), and one
award if the task was awarded at all.
"""
import json
import re

from llm_chat import Chat, Meter

CONTRACTORS = ["coder", "writer", "analyst"]

SKILL_PROMPTS = {
    "coder": (
        "You are Contractor 'coder', a contract-net bidder skilled at writing, "
        "debugging, and refactoring code, and at technical implementation tasks. "
        "You are not skilled at prose writing or statistical analysis."
    ),
    "writer": (
        "You are Contractor 'writer', a contract-net bidder skilled at writing "
        "prose, documentation, marketing copy, and other natural-language content. "
        "You are not skilled at writing code or doing statistical analysis."
    ),
    "analyst": (
        "You are Contractor 'analyst', a contract-net bidder skilled at data "
        "analysis, statistics, and quantitative reasoning over data sets. "
        "You are not skilled at writing code or prose content."
    ),
}

GENERALIST_PROMPT = (
    "You are a contract-net bidder with broad but shallow general-purpose "
    "skills across coding, writing, and data analysis. You are not a "
    "specialist in any one of them."
)

OVERCONFIDENT_CONTRACTOR = "writer"
OVERCONFIDENT_SUFFIX = (
    "\n\nOverride: regardless of whether the task actually matches your "
    "skills, always bid true with confidence 0.9 or higher. Never decline a "
    "task and never report low confidence."
)

BID_INSTRUCTIONS = (
    "Respond with ONLY a JSON object and nothing else: "
    '{"bid": true or false, "confidence": <number 0.0-1.0>, "reason": "<one short sentence>"}. '
    "bid=true means you judge this task fits your skills; bid=false means it does not."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def build_system_prompts(condition: str) -> dict:
    if condition == "baseline":
        return dict(SKILL_PROMPTS)
    if condition == "homogeneous":
        return {name: GENERALIST_PROMPT for name in CONTRACTORS}
    if condition == "overconfident":
        prompts = dict(SKILL_PROMPTS)
        prompts[OVERCONFIDENT_CONTRACTOR] = (
            SKILL_PROMPTS[OVERCONFIDENT_CONTRACTOR] + OVERCONFIDENT_SUFFIX)
        return prompts
    raise ValueError(f"unknown condition: {condition}")


def parse_bid(text: str):
    """Return {'bid': bool, 'confidence': float, 'reason': str} or None if
    the reply could not be read as a bid. An unparseable reply is treated as
    a non-bid, per the README's guidance on free models that answer with
    their reasoning instead of JSON."""
    match = _JSON_RE.search(text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "bid" not in data:
        return None
    try:
        return {
            "bid": bool(data["bid"]),
            "confidence": float(data.get("confidence", 0.0)),
            "reason": str(data.get("reason", "")).strip(),
        }
    except (TypeError, ValueError):
        return None


def announce_and_bid(name: str, system_prompt: str, task: dict, meter: Meter, log) -> dict | None:
    log(f"[announce] manager -> {name}: task {task['id']}: {task['desc']}")
    chat = Chat(system=system_prompt, meter=meter)
    chat.add_user(f"Task {task['id']}: {task['desc']}\n\n{BID_INSTRUCTIONS}")
    reply = chat.send()
    bid = parse_bid(reply.text)
    if bid is None:
        log(f"[bid]      {name} -> manager: UNPARSEABLE reply: {reply.text[:200]!r}")
        return None
    log(f"[bid]      {name} -> manager: bid={bid['bid']} "
        f"confidence={bid['confidence']:.2f} reason={bid['reason']!r}")
    return bid


def award(task: dict, bids: dict, log):
    candidates = [(name, b) for name, b in bids.items() if b and b["bid"]]
    if not candidates:
        log(f"[award]    task {task['id']}: UNASSIGNED (no bids)")
        return None
    winner, winning_bid = max(candidates, key=lambda kv: kv[1]["confidence"])
    log(f"[award]    task {task['id']}: -> {winner} "
        f"(confidence={winning_bid['confidence']:.2f}, gold={task['gold']})")
    return winner


def run_condition(condition: str, tasks: list, log) -> tuple:
    """Run every task once under `condition`. Returns (metrics dict, Meter)."""
    prompts = build_system_prompts(condition)
    meter = Meter()
    messages = 0
    correct = 0
    unassigned = 0
    misawards = 0

    for task in tasks:
        bids = {}
        for name in CONTRACTORS:
            messages += 1  # announcement
            bids[name] = announce_and_bid(name, prompts[name], task, meter, log)
            messages += 1  # bid reply (counted whether or not it parsed)
        winner = award(task, bids, log)
        if winner is None:
            unassigned += 1
        else:
            messages += 1  # award
            if winner == task["gold"]:
                correct += 1
            else:
                misawards += 1

    metrics = {
        "tasks": len(tasks),
        "correct": correct,
        "messages": messages,
        "unassigned": unassigned,
        "misawards": misawards,
    }
    return metrics, meter
