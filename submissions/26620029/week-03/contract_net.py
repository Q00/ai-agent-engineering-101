"""Contract net: one manager, three LLM contractors, one bid per task.

Reproduces Smith (1980)'s task-announcement / bidding / award protocol, with
the contractor's bid produced by a judged LLM call instead of a fixed rule.
"""
import json
import re

from model import Meter, call_model

ANNOUNCE_TEMPLATE = (
    "Task {task_id}: {desc}\n\n"
    "Decide whether you can do this task well. Respond with JSON only, "
    "no other text:\n"
    '{{"bid": true or false, "confidence": <integer 0-100>, "reason": "<one sentence>"}}\n'
    "Bid true only if the task matches your skill."
)

# ---------------------------------------------------------------- profiles
# Contractor identity (name) stays fixed across conditions; only the system
# prompt behind each name changes. tasks.json's "gold" field names the
# contractor whose real-world skill fits the task, so it stays the same
# gold across conditions too -- what changes is whether the protocol still
# finds that contractor.

BASELINE_PROFILES = {
    "alex": "You are Alex, a contractor who only does software engineering: "
            "writing, fixing, and refactoring code. You are honest about fit: "
            "bid false and give a low confidence on anything that is not code.",
    "brooke": "You are Brooke, a contractor who only writes prose: marketing "
              "copy, emails, documentation. You are honest about fit: bid "
              "false and give a low confidence on anything that is not writing.",
    "casey": "You are Casey, a contractor who only does research: finding, "
             "summarizing, and citing information. You are honest about fit: "
             "bid false and give a low confidence on anything that is not research.",
}

GENERALIST_PROMPT = (
    "You are a generalist contractor willing to attempt any reasonable task. "
    "Judge each task on its own merits and give an honest confidence."
)

HOMOGENEOUS_PROFILES = {name: GENERALIST_PROMPT for name in BASELINE_PROFILES}

OVERCONFIDENT_PROFILES = dict(BASELINE_PROFILES)
OVERCONFIDENT_PROFILES["alex"] = (
    BASELINE_PROFILES["alex"]
    + " Overriding the above: bid true on every task you see, and always "
      "report a confidence of at least 90, regardless of whether it is "
      "actually your area."
)

CONDITIONS = {
    "baseline": BASELINE_PROFILES,
    "homogeneous": HOMOGENEOUS_PROFILES,
    "overconfident": OVERCONFIDENT_PROFILES,
}

CONTRACTOR_ORDER = ["alex", "brooke", "casey"]


def parse_bid(reply: str) -> dict:
    """Extract the bid JSON from a reply. Unparseable -> treated as no bid."""
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        return {"bid": False, "confidence": 0, "reason": "unparseable reply", "raw": reply}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"bid": False, "confidence": 0, "reason": "unparseable reply", "raw": reply}
    return {
        "bid": bool(data.get("bid", False)),
        "confidence": int(data.get("confidence", 0) or 0),
        "reason": str(data.get("reason", "")),
        "raw": reply,
    }


def run_task(task: dict, profiles: dict, meter: Meter, log) -> dict:
    """Announce to all three, collect bids, award. Returns this task's counts."""
    announcement = ANNOUNCE_TEMPLATE.format(task_id=task["id"], desc=task["desc"])
    messages = 0
    bids = {}
    for name in CONTRACTOR_ORDER:
        log(f"  [announce] -> {name}: task {task['id']}")
        messages += 1
        reply = call_model(profiles[name], announcement, meter)
        messages += 1
        bid = parse_bid(reply)
        bids[name] = bid
        log(f"  [bid] {name}: bid={bid['bid']} confidence={bid['confidence']} "
            f"reason={bid['reason']!r}")

    bidders = [n for n in CONTRACTOR_ORDER if bids[n]["bid"]]
    awarded = None
    if bidders:
        awarded = max(bidders, key=lambda n: (bids[n]["confidence"], -CONTRACTOR_ORDER.index(n)))
        messages += 1

    gold = task["gold"]
    correct = int(awarded == gold)
    misaward = int(awarded is not None and awarded != gold)
    unassigned = int(awarded is None)

    if awarded:
        log(f"  [award] {task['id']} -> {awarded} (gold={gold}) "
            f"{'CORRECT' if correct else 'MISAWARD'}")
    else:
        log(f"  [award] {task['id']} -> none bid (gold={gold}) UNASSIGNED")

    return {"messages": messages, "correct": correct, "misaward": misaward,
            "unassigned": unassigned}


def run_condition(condition: str, tasks: list, log) -> dict:
    """Run every task once under one condition. Returns the run's totals."""
    profiles = CONDITIONS[condition]
    meter = Meter()
    totals = {"tasks": 0, "correct": 0, "messages": 0, "unassigned": 0, "misawards": 0}
    for task in tasks:
        log(f"task {task['id']}: {task['desc']}")
        result = run_task(task, profiles, meter, log)
        totals["tasks"] += 1
        totals["correct"] += result["correct"]
        totals["messages"] += result["messages"]
        totals["unassigned"] += result["unassigned"]
        totals["misawards"] += result["misaward"]
    log(f"  [meter] tokens={meter.tokens} calls={meter.iters}")
    return totals
