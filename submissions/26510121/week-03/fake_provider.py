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
