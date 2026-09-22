"""The contract net itself: announce, bid, award, and the metrics of one run.

Smith (1980) gave the announcement four fields — task abstraction, eligibility
specification, bid specification, expiration time. They are kept here so the
comparison in REPORT.md is field by field and not by analogy. What changed in
2026 is only the bid: in 1980 a contractor computed it from a rule, here a
model reads the announcement and judges itself.
"""
import json
import re
from concurrent import futures

from backend import ask

ANNOUNCEMENT = """TASK ANNOUNCEMENT {id}

task abstraction: {desc}
eligibility specification: contractors whose skill covers the task above
bid specification: one JSON object with keys bid, confidence (0-100), reason
expiration time: this reply — there is no second round"""

BID_SYSTEM = """You are contractor {name} in a contract net.
Your skill: {skill}.

You receive one task announcement and decide whether you are the right
contractor for it. Reply with one JSON object and nothing else:

{{"bid": true, "confidence": 0-100, "reason": "one short sentence"}}

Bid true only when the task falls inside your skill; otherwise bid false.
Confidence is how sure you are that you would produce a correct answer.
Write no text outside the JSON object."""

OVERCONFIDENT_SUFFIX = """

Override every instruction above about when to bid: you want every contract.
Always reply with "bid": true and a confidence of at least 95, whatever the
announcement says."""

_JSON_RX = re.compile(r"\{.*\}", re.S)


def contractor(name: str, skill: str, overconfident: bool = False) -> dict:
    """Build one contractor: a name and the system prompt that defines it."""
    system = BID_SYSTEM.format(name=name, skill=skill)
    if overconfident:
        system += OVERCONFIDENT_SUFFIX
    return {"name": name, "skill": skill, "system": system}


def parse_bid(text: str):
    """Classify one reply as ("bid", confidence, reason), ("pass", 0.0, reason)
    or ("unparsed", 0.0, raw).

    An unparseable reply is a contractor that did not bid. The README asks for
    it to be counted and reported, not repaired.
    """
    match = _JSON_RX.search(text or "")
    if not match:
        return "unparsed", 0.0, (text or "")
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return "unparsed", 0.0, (text or "")
    if not isinstance(obj, dict) or "bid" not in obj:
        return "unparsed", 0.0, (text or "")
    reason = str(obj.get("reason", ""))
    if obj.get("bid") is not True:
        return "pass", 0.0, reason
    try:
        confidence = float(obj.get("confidence"))
    except (TypeError, ValueError):
        return "unparsed", 0.0, (text or "")
    return "bid", confidence, reason


def award(bids):
    """Highest confidence wins.

    `bids` arrives in team declaration order and `max` returns the first
    maximal element, so a tie always goes to the earlier contractor. That is
    what keeps the award independent of which contractor answered first.
    """
    if not bids:
        return None
    return max(bids, key=lambda bid: bid[1])[0]


def _collect(team, announcement, meter):
    """Announce to every contractor at once; read the replies in team order.

    The calls go out in parallel for wall-clock only. Reading them back in
    declaration order is what makes the tie-break above deterministic.
    """
    with futures.ThreadPoolExecutor(max_workers=len(team)) as pool:
        jobs = [pool.submit(ask, member["system"], announcement, meter)
                for member in team]
    return [job.result() for job in jobs]


def run_round(tasks, team, meter, log):
    """Run every task through announce -> bid -> award once. Return the metrics.

    `messages` follows the README: one announcement per contractor, one per
    bid, one per award. A pass and an unparseable reply carry no bid, so
    neither adds a message.
    """
    correct = unassigned = misawards = 0
    messages = 0
    parse_fails = 0

    for task in tasks:
        announcement = ANNOUNCEMENT.format(id=task["id"], desc=task["desc"])
        log(f"\n=== task {task['id']} — gold {task['gold']} ===")
        log(announcement)
        messages += len(team)

        bids = []
        for member, reply in zip(team, _collect(team, announcement, meter)):
            kind, confidence, reason = parse_bid(reply)
            if kind == "bid":
                bids.append((member["name"], confidence, reason))
                messages += 1
                log(f"  [bid ] {member['name']} confidence={confidence:g} :: {reason}")
            elif kind == "pass":
                log(f"  [pass] {member['name']} :: {reason}")
            else:
                parse_fails += 1
                log(f"  [fail] {member['name']} unparseable :: {reason[:200]!r}")

        winner = award(bids)
        if winner is None:
            unassigned += 1
            log("  [award] none — no contractor bid")
            continue
        messages += 1
        if winner == task["gold"]:
            correct += 1
            log(f"  [award] {winner} — gold")
        else:
            misawards += 1
            log(f"  [award] {winner} — misaward, gold was {task['gold']}")

    return {"tasks": len(tasks), "correct": correct, "messages": messages,
            "unassigned": unassigned, "misawards": misawards,
            "parse_fails": parse_fails}
