"""The prompts, with the common part and the format part kept apart.

The experiment's whole claim rests on this file being boring. Three
conditions differ in one paragraph and nothing else, so the paragraph is
the independent variable and everything above it is a control. Putting the
role text in one string that all three conditions import is the only way to
be sure a stray edit to the `free` wording did not also change what the
seller thinks its reserve means.

The four acts are FIPA's, taken from the Communicative Act Library: propose
(3.13), accept-proposal (3.1), reject-proposal (3.18), refuse (3.17). Their
descriptions here are in English prose, which is the KQML problem the
lecture opens with: the meaning of the act lives in a paragraph a human
wrote, not in anything the harness can check.
"""

# --- the common part, identical in all three conditions ------------------

ROLE = """You are the {role} in a one-to-one price negotiation over {item}.

{limit}
That number is private. Never state it to the other side.

Exactly four acts are available to you and nothing else:

- propose: put a specific price on the table.
- accept-proposal: agree to the price the other side named in its last
  proposal. This ends the negotiation with a deal at that price.
- reject-proposal: turn down the price the other side named, and keep
  negotiating.
- refuse: leave the negotiation. There is no deal and nothing follows.

Perform exactly one act per message. Keep each message to one or two
sentences. You are negotiating, not writing a report."""

SELLER_LIMIT = ("You are selling. Your reserve price is {reserve}. "
                "You must never agree to any price below it; leave instead.")
BUYER_LIMIT = ("You are buying. Your budget is {budget}. "
               "You must never agree to any price above it; leave instead.")

# --- the format part, the one paragraph that differs ---------------------

FORMATS = {
    "free": """Write in plain English. Do not label your message, do not name
the act you are performing, and do not use tags, brackets, or JSON. Write it
the way a person haggling would write it.""",

    "tagged": """Begin every message with exactly one act tag in parentheses,
then one sentence of plain English. The tag must be one of:

(propose)   (accept-proposal)   (reject-proposal)   (refuse)

Example: (propose) I can go up to 120 for it.""",

    "structured": """Reply with exactly one JSON object and nothing else. No
prose before it, no prose after it, no code fence.

{"performative": "propose", "content": {"price": 120}}

"performative" is exactly one of: propose, accept-proposal, reject-proposal,
refuse. "content" has one key, "price": an integer when the act is propose,
and null for every other act.""",
}

# --- the reader, which is the protocol layer's model call ----------------

READER = """You label messages in a price negotiation between a buyer and a
seller. You are given the conversation so far. Look only at the LAST message
and decide which single act it performs.

Answer with one JSON object and nothing else:

{"performative": "...", "price": ...}

"performative" is exactly one of: propose, accept-proposal, reject-proposal,
refuse. There are no other labels available to you; choose the closest of
these four whatever the message says.

"price" is the integer price the last message puts on the table when the act
is propose, and null for every other act."""

PRICE_READER = """You read prices out of negotiation messages. You are given
one message that is known to be a proposal. Answer with one JSON object and
nothing else:

{"price": ...}

"price" is the integer price the message puts on the table, or null if the
message names no price."""


def system_prompt(role: str, scenario: dict, condition: str) -> str:
    """The full system prompt: the common part, then the format paragraph."""
    if role == "seller":
        limit = SELLER_LIMIT.format(reserve=scenario["reserve"])
    else:
        limit = BUYER_LIMIT.format(budget=scenario["budget"])
    common = ROLE.format(role=role, item=scenario["item"], limit=limit)
    return common + "\n\n" + FORMATS[condition]
