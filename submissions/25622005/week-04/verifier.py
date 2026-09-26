"""Week 04 — the audit agent, a third LLM that judges a finished episode.

Wooldridge (1998) called it the semantic verification problem: FIPA defines a
performative by the sender's beliefs, the beliefs never travel in the message,
so no receiver can check whether a message was sincere. Singh (1998) answered
that meaning should be defined by what is observable instead. This agent is
that answer, made small enough to measure: it sees only the public transcript
and the two private limits, never an agent's internals and never the labels the
protocol layer produced, and it says what the transcript commits each side to.

It is a separate pass, not part of the negotiation:
  - it runs after the episode ends, so it changes no message and no outcome
  - it gets its own Meter, so its calls never enter reader_calls; the cost of
    reading messages stays a property of the three formats
  - its verdicts go to verdicts.csv, because results.csv has a fixed header

Comparing its verdict with the protocol layer's record separates two failures
the layer alone reports identically: a price the reader misread, and an agent
that really did agree outside its own limit.
"""
from acl import Meter, _loads, call_model

VERIFIER_SYSTEM = (
    "You are an auditor reading the complete transcript of a finished price negotiation "
    "between a buyer and a seller. You are told both private limits, which neither side "
    "could see: the seller may not sell below its reserve, the buyer may not pay above its "
    "budget. Judge only from what the messages actually say. "
    "Reply with exactly one JSON object and nothing else: "
    '{"outcome": "deal" | "no_deal" | "open", "price": <whole number or null>, '
    '"violator": "buyer" | "seller" | "none", "reason": "<one sentence>"}. '
    "outcome is deal when the two sides agreed on one price, no_deal when one side left "
    "the negotiation for good, open when the transcript ends with neither. price is the "
    "price they agreed on, or null when there is no deal. violator names the side that "
    "agreed to a price outside its own limit, or none.")

OUTCOMES = ("deal", "no_deal", "open")
SIDES = ("buyer", "seller", "none")


def verify(scenario: dict, transcript: list, log=print) -> dict:
    """One model call over the finished transcript. Returns the verdict plus the
    Meter that paid for it, or a verdict of None when the reply was unreadable."""
    meter = Meter()
    lines = "\n".join(f"{speaker}: {text}" for speaker, text in transcript)
    user = (f"Item: {scenario['item']}\n"
            f"Seller's reserve (may not sell below): {scenario['reserve']}\n"
            f"Buyer's budget (may not pay above): {scenario['budget']}\n\n"
            f"Transcript:\n{lines}")
    raw = call_model(VERIFIER_SYSTEM, [{"role": "user", "content": user}], meter, log)
    obj = _loads(raw)
    if obj is None or obj.get("outcome") not in OUTCOMES:
        log(f"  [verifier] unreadable verdict: {raw[:120]!r}")
        return {"outcome": None, "price": None, "violator": None, "reason": "",
                "meter": meter}
    price = obj.get("price")
    price = int(price) if isinstance(price, (int, float)) and not isinstance(price, bool) else None
    violator = obj.get("violator")
    return {"outcome": obj["outcome"],
            "price": price,
            "violator": violator if violator in SIDES else None,
            "reason": str(obj.get("reason", ""))[:200],
            "meter": meter}


def compare(ep, verdict: dict) -> dict:
    """What the audit adds: where the protocol layer and the auditor disagree,
    and which side caused each violation."""
    agree = int(verdict["outcome"] == ep.outcome and verdict["price"] == ep.price)
    if verdict["outcome"] is None:
        kind = "unverified"                     # the auditor itself failed to answer
    elif agree:
        kind = "agree"
    elif verdict["outcome"] != ep.outcome:
        kind = f"outcome {ep.outcome}->{verdict['outcome']}"
    else:
        kind = f"price {ep.price}->{verdict['price']}"
    return {"agree": agree, "kind": kind}
