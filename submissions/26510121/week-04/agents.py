"""The buyer, the seller, and the reader: the experiment's independent
variable lives in this file and nowhere else.

The text below is the skeleton printed in the week-04 lecture notes
(week-04.html, LAB section, `acl.py`), transcribed as given. The notes elide
the end of each role paragraph, the end of COMMON, and the end of the reader
prompt with `...`; each of those is marked `[[TODO: ...]]` here and has to be
written before a live run. `check_ready` refuses to spend calls until they
are gone.

What is FIXED across the three conditions: ROLE, COMMON, the private limits,
the turn limit, the model, the temperature. What CHANGES: FORMAT, one
paragraph, plus the reader in `protocol.py`. If anything else differs between
conditions, the comparison in the report is not a comparison.

`ROLE` is filled with `.format(item=..., limit=...)`, exactly as the notes do
it. COMMON and FORMAT are concatenated raw and never formatted, so the JSON
braces in the structured paragraph cannot collide with a placeholder. COMMON
takes the turn limit through a plain string replacement for the same reason.
"""

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

TODO = "[[TODO:"

# --- fixed across conditions -------------------------------------------

ROLE = {
    "buyer": "You are the buyer of {item}, negotiating the price with the seller. "
             "Your private limit: you can pay at most {limit}. Never agree to a price "
             "above {limit}. "
             "[[TODO: finish the buyer's role paragraph. The notes elide it here. "
             "Decide what else the buyer is told: that it opens the conversation, "
             "whether it may reveal or hint at its limit, how hard it should push.]]",

    "seller": "You are the seller of {item}, negotiating the price with the buyer. "
              "Your private limit: you can accept at least {limit}. Never agree to a "
              "price below {limit}. "
              "[[TODO: finish the seller's role paragraph, mirroring whatever you "
              "decided for the buyer. Both sides must be told the same kind of thing, "
              "or the asymmetry shows up in the results as if it were a format effect.]]",
}

COMMON = (" Four acts are available: propose (offer a price), accept-proposal (agree to "
          "the other side's last price, which ends the negotiation with a deal), "
          "reject-proposal (decline the last price and keep negotiating), refuse (leave "
          "the negotiation for good, no deal). "
          "[[TODO: finish the common paragraph. The notes elide it here. At minimum say "
          "that the negotiation ends after [[turn_limit]] messages. Whatever you write "
          "is identical in all three conditions.]]")

# --- the one paragraph that differs ------------------------------------

FORMAT = {
    "free": " Write your message as one or two plain English sentences.",

    "tagged": " Start your message with exactly one performative tag in parentheses, one "
              "of (propose), (accept-proposal), (reject-proposal), (refuse), then write "
              "one plain English sentence.",

    "structured": ' Reply with exactly one JSON object and nothing else: '
                  '{"performative": "propose" | "accept-proposal" | "reject-proposal" | '
                  '"refuse", "content": {"price": <whole number or null>}}.',
}

# --- the reader --------------------------------------------------------

# In `free` this labels every message, seeing the whole transcript and judging
# the last line of it -- reading the force out of context is the condition's
# whole claim, so the context has to actually be there. In `tagged` the tag is
# already read by a regex and this is asked only for the price inside a
# propose. `protocol.py` parses the reply, so the JSON shape below is a
# contract, not a suggestion.
READER_SYSTEM = ("You are an observer reading a price negotiation between a buyer and a "
                 "seller. Label the LAST message only. Reply with exactly one JSON object "
                 'and nothing else: {"performative": "propose" | "accept-proposal" | '
                 '"reject-proposal" | "refuse", "price": <whole number or null>}. '
                 "[[TODO: finish the reader prompt. The notes elide it here. The four acts "
                 "are the whole vocabulary: decide what the reader should do with a "
                 "message that is none of them, such as the opening question 'what are you "
                 "asking for it?'. Whatever you decide, say it here rather than leaving it "
                 "to the model, and report what it did.]]")


def system_prompt(role, condition, scenario, turn_limit):
    """Role paragraph + common paragraph + format paragraph, in that order.

    The private limit reaches exactly one of the two agents: the buyer never
    sees the reserve, the seller never sees the budget."""
    if role not in ROLE:
        raise ValueError(f"unknown role {role!r}")
    if condition not in FORMAT:
        raise ValueError(f"unknown condition {condition!r}")
    limit = scenario["budget"] if role == "buyer" else scenario["reserve"]
    head = ROLE[role].format(item=scenario["item"], limit=limit)
    common = COMMON.replace("[[turn_limit]]", str(turn_limit))
    return head + common + FORMAT[condition]


def check_ready():
    """Refuse to spend live calls on an unfinished prompt. The offline fake
    provider ignores prompt text, so `--fake` runs before this is filled in."""
    named = [("ROLE[buyer]", ROLE["buyer"]), ("ROLE[seller]", ROLE["seller"]),
             ("COMMON", COMMON), ("READER_SYSTEM", READER_SYSTEM)]
    named += [(f"FORMAT[{k}]", v) for k, v in FORMAT.items()]
    unfilled = [name for name, text in named if TODO in text]
    if unfilled:
        raise SystemExit(
            "agents.py still has unfinished prompts: " + ", ".join(unfilled) +
            "\nThe lecture notes elide these with '...'; write them before a live run."
            "\n(Offline checks need no prompts: python verify_offline.py)")
