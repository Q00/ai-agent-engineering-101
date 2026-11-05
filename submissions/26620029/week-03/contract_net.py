"""Contract net: one manager, three LLM contractors, one bid per task.

Reproduces Smith (1980)'s task-announcement / bidding / award protocol, with
the contractor's bid produced by a judged LLM call instead of a fixed rule.
"""
import json
import random
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

def _make_overconfident(profiles: dict, who: str) -> dict:
    """profiles with who's honest persona overridden to bid on everything at
    forced high confidence -- everyone else stays on their honest baseline.
    Generalized so the same override text can be pinned to any one of the
    three names, to test whether its effect is specific to that identity."""
    out = dict(profiles)
    out[who] = (
        profiles[who]
        + " Overriding the above: bid true on every task you see, and always "
          "report a confidence of at least 90, regardless of whether it is "
          "actually your area."
    )
    return out


OVERCONFIDENT_PROFILES = _make_overconfident(BASELINE_PROFILES, "alex")
OVERCONFIDENT_BROOKE_PROFILES = _make_overconfident(BASELINE_PROFILES, "brooke")
OVERCONFIDENT_CASEY_PROFILES = _make_overconfident(BASELINE_PROFILES, "casey")

CONDITIONS = {
    "baseline": BASELINE_PROFILES,
    "homogeneous": HOMOGENEOUS_PROFILES,
    "overconfident": OVERCONFIDENT_PROFILES,
    # Same profiles as homogeneous (where ties are common) but with the
    # announcement order shuffled per task instead of fixed -- isolates
    # whether a tied confidence always going to the same name is a real
    # signal or just "announced first, wins ties" (see run_task's tie-break).
    "homogeneous_shuffled": HOMOGENEOUS_PROFILES,
    # Same override as "overconfident", pinned to brooke/casey instead of
    # alex -- isolates whether the overconfidence effect is specific to
    # alex's identity/persona or shows up for whoever gets the override.
    "overconfident_brooke": OVERCONFIDENT_BROOKE_PROFILES,
    "overconfident_casey": OVERCONFIDENT_CASEY_PROFILES,
}

# Graded conditions only -- what scripts/check_week03.py's CI contract and
# the root results.csv/logs/ expect. Extra conditions (e.g.
# homogeneous_shuffled) must never be written there; see run_experiments.py.
GRADED_CONDITIONS = ("baseline", "homogeneous", "overconfident")

CONTRACTOR_ORDER = ["alex", "brooke", "casey"]

# Conditions whose per-task announcement order is randomized instead of the
# fixed CONTRACTOR_ORDER.
SHUFFLED_ORDER_CONDITIONS = {"homogeneous_shuffled"}

# Fields a contractor is allowed to see. gold is deliberately absent: it is
# split off before a task ever reaches announcement-building, so a bug that
# later dumps "the task dict" into a prompt or a log meant for the model
# still can't leak it -- there is no gold key left in that dict to dump.
PUBLIC_TASK_FIELDS = ("id", "desc")


def public_view(task: dict) -> dict:
    """The announcement-safe half of a task record: id + desc, no gold."""
    return {k: task[k] for k in PUBLIC_TASK_FIELDS}


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


def run_task(announcement_task: dict, gold: str, profiles: dict, meter: Meter, log,
             order: list = CONTRACTOR_ORDER) -> dict:
    """Announce to all three, collect bids, award. Returns this task's counts
    plus a per-bid token breakdown.

    announcement_task must be the *public* view of a task (id + desc only --
    see public_view()). gold is threaded through as a separate argument
    purely for scoring after the award, never folded into the dict that
    reaches ANNOUNCE_TEMPLATE or call_model. order is the sequence contractors
    are announced in this task -- CONTRACTOR_ORDER (fixed) unless the caller
    is running a SHUFFLED_ORDER_CONDITIONS condition, in which case it's a
    fresh per-task permutation. Tie-break uses this same order, so "who wins
    a tie" tracks who was announced first *for this task*, not a global name.
    """
    assert "gold" not in announcement_task, (
        "gold leaked into the announcement-building path -- pass public_view(task), "
        "not the raw task record")
    announcement = ANNOUNCE_TEMPLATE.format(
        task_id=announcement_task["id"], desc=announcement_task["desc"])
    messages = 0
    bids = {}
    bid_records = []
    for position, name in enumerate(order):
        log(f"  [announce] -> {name}: task {announcement_task['id']}")
        messages += 1
        reply = call_model(profiles[name], announcement, meter)
        messages += 1
        bid = parse_bid(reply)
        bid["input_tokens"] = meter.last_input
        bid["output_tokens"] = meter.last_output
        bid["total_tokens"] = meter.last_input + meter.last_output
        bids[name] = bid
        bid_records.append({
            "task_id": announcement_task["id"], "contractor": name,
            "announce_position": position,
            "bid": bid["bid"], "confidence": bid["confidence"],
            "input_tokens": bid["input_tokens"], "output_tokens": bid["output_tokens"],
            "total_tokens": bid["total_tokens"],
        })
        log(f"  [bid] {name}: bid={bid['bid']} confidence={bid['confidence']} "
            f"tokens={bid['total_tokens']} (in={bid['input_tokens']} "
            f"out={bid['output_tokens']}) reason={bid['reason']!r}")

    bidders = [n for n in order if bids[n]["bid"]]
    awarded = None
    if bidders:
        awarded = max(bidders, key=lambda n: (bids[n]["confidence"], -order.index(n)))
        messages += 1

    # gold only enters scoring here, after the award is already decided --
    # nothing above this line has read it.
    correct = int(awarded == gold)
    misaward = int(awarded is not None and awarded != gold)
    unassigned = int(awarded is None)

    if awarded:
        log(f"  [award] {announcement_task['id']} -> {awarded} (gold={gold}) "
            f"{'CORRECT' if correct else 'MISAWARD'}")
    else:
        log(f"  [award] {announcement_task['id']} -> none bid (gold={gold}) UNASSIGNED")

    return {"messages": messages, "correct": correct, "misaward": misaward,
            "unassigned": unassigned, "bid_records": bid_records}


def run_condition(condition: str, tasks: list, log) -> tuple:
    """Run every task once under one condition.

    Returns (totals, bid_records): totals is the run's aggregate counts,
    bid_records is one row per (task, contractor) bid with its token cost,
    for callers that want to write it out (e.g. bids.csv).
    """
    profiles = CONDITIONS[condition]
    shuffle_order = condition in SHUFFLED_ORDER_CONDITIONS
    meter = Meter()
    totals = {"tasks": 0, "correct": 0, "messages": 0, "unassigned": 0, "misawards": 0}
    all_bid_records = []
    for task in tasks:
        log(f"task {task['id']}: {task['desc']}")
        if shuffle_order:
            order = random.sample(CONTRACTOR_ORDER, k=len(CONTRACTOR_ORDER))
            log(f"  [order] announcement order shuffled to: {order}")
        else:
            order = CONTRACTOR_ORDER
        result = run_task(public_view(task), task["gold"], profiles, meter, log, order=order)
        totals["tasks"] += 1
        totals["correct"] += result["correct"]
        totals["messages"] += result["messages"]
        totals["unassigned"] += result["unassigned"]
        totals["misawards"] += result["misaward"]
        all_bid_records.extend(result["bid_records"])
    log(f"  [meter] tokens={meter.tokens} calls={meter.iters}")
    return totals, all_bid_records
