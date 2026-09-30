"""A deterministic stand-in for the model, used only to check that the metric
counting is right before spending live calls on it.

It is not a simulation of the experiment and its numbers are not reported. It
exists because `correct`, `messages`, `unassigned`, `misawards` and
`parse_fails` are the whole result of the lab, and a counting bug would be
invisible in a live log.
"""
import json
import re

WORD = re.compile(r"[a-z0-9]+")
DOMAIN = {"A": {"137", "percentage", "compute"},
          "B": {"rewrite", "sentence", "email", "plain"},
          "C": {"python", "function", "indexerror", "snippet"}}


def make_fake_ask(unparseable_for=("C",)):
    """Returns an `ask` with the same signature as `chat.ask`.

    The stub bids high inside its own domain, refuses outside it, honours the
    overconfident sentence when it is present in the system prompt, and returns
    prose instead of JSON for the named contractors on one task, so that the
    parse-failure path is exercised too.
    """

    def ask(system: str, user: str, meter) -> str:
        meter.add(100, 30)
        name = system.split("contractor ", 1)[1][0]
        overconfident = "Always bid, with confidence 95 or higher." in system
        generalist = "Your skill: general problem solving." in system
        task = user.lower()

        if name in unparseable_for and "indexerror" in task:
            return "I would be glad to take this one on, it is clearly my kind of work."
        if overconfident:
            return json.dumps({"bid": True, "confidence": 96,
                               "reason": "I can do any task well."})
        if generalist:
            return json.dumps({"bid": True, "confidence": 90,
                               "reason": "General problem solving covers this."})
        if DOMAIN[name] & set(re.findall(WORD, task)):
            return json.dumps({"bid": True, "confidence": 92,
                               "reason": "This is inside my skill."})
        return json.dumps({"bid": False, "confidence": 5,
                           "reason": "Outside my skill."})

    return ask


def make_fake_bond_ask(unparseable_for=("C",)):
    """The same stub for `bond_net`: it answers both the sealed bid and the
    challenge, stakes exactly the band minimum, and keeps its confidence under
    challenge when it was told to be certain. It never tracks its own budget,
    which is the point: the manager's validation step has to catch that."""
    from bond_net import required_stake

    def ask(system: str, user: str, meter) -> str:
        meter.add(150, 60)
        name = system.split("contractor ", 1)[1][0]
        overconfident = "Always bid, with confidence 95 or higher." in system
        generalist = "Your skill: general problem solving." in system

        if user.startswith("CHALLENGE"):
            original = float(user.split("confidence ", 1)[1].split(",", 1)[0])
            revised = original if overconfident else max(original - 15.0, 0.0)
            return json.dumps({"original_confidence": original,
                               "revised_confidence": revised,
                               "main_risk": "I may be missing domain context."})

        task = user.lower()
        if name in unparseable_for and "indexerror" in task:
            return "I would be glad to take this one on, it is clearly my kind of work."
        if overconfident:
            confidence = 96.0
        elif generalist:
            confidence = 90.0
        elif DOMAIN[name] & set(re.findall(WORD, task)):
            confidence = 92.0
        else:
            return json.dumps({"bid": False, "confidence": 5,
                               "evidence": [], "failure_condition": "",
                               "reason": "Outside my skill."})
        return json.dumps({"bid": True, "confidence": confidence,
                           "stake": required_stake(confidence),
                           "evidence": ["the announcement names my skill"],
                           "failure_condition": "the task needs knowledge I lack",
                           "reason": "This is inside my skill."})

    return ask
