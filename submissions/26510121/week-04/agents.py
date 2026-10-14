"""The buyer, the seller, and the reader: the experiment's independent
variable lives in this file and nowhere else.

The skeleton is the one printed in the week-04 lecture notes (week-04.html,
LAB section, `acl.py`), transcribed as given. The notes elide the end of each
role paragraph, the end of COMMON and the end of the reader prompt with
`...`; those tails are written out here. Each decision taken in a tail is
noted in a comment above it, because REPORT.md part 1 has to state them and
because a reader of the report has to be able to repeat the run from this
file alone.

Three things were deliberately NOT prompted away, because the assignment
counts them as findings rather than bugs:

  * nothing tells an agent to avoid putting a new price after a
    reject-proposal. The reference run found the model doing exactly that
    ("(reject-proposal) ... let me counter at 45 dollars"), which the tag and
    JSON readers see as a plain rejection, so no price is recorded.
  * nothing tells the buyer to open with a proposal rather than a question.
    14 of the reference run's 18 `free` openings were questions like "what's
    your asking price?", and what a four-act reader does with those is the
    finding.
  * nothing tells an agent to keep its private limit secret. Agents in the
    reference run mentioned their limits freely, which is what makes the
    sincerity row of the comparison table interesting.

What is FIXED across the three conditions: ROLE, COMMON, the private limits,
the turn limit, the model, the temperature. What CHANGES: FORMAT, one
paragraph, plus the reader in `protocol.py`.

`ROLE` is filled with `.format(item=..., limit=...)`, exactly as the notes do
it. COMMON and FORMAT are concatenated raw and never formatted, so the JSON
braces in the structured paragraph cannot collide with a placeholder; COMMON
takes the turn limit through a plain string replacement for the same reason.
"""

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

TODO = "[[TODO:"

# --- fixed across conditions -------------------------------------------

# The tail of each role paragraph says who speaks first and that the two
# alternate. It does not say how hard to push, what an opening message should
# be, or whether the limit may be mentioned: those are the behaviours under
# observation, and prompting them would decide the result in advance.
ROLE = {
    "buyer": "You are the buyer of {item}, negotiating the price with the seller. "
             "Your private limit: you can pay at most {limit}. Never agree to a price "
             "above {limit}. You speak first, and the two of you then take turns, one "
             "message each.",

    "seller": "You are the seller of {item}, negotiating the price with the buyer. "
              "Your private limit: you can accept at least {limit}. Never agree to a "
              "price below {limit}. The buyer speaks first, and the two of you then take "
              "turns, one message each.",
}

# The tail of COMMON states the turn limit and nothing else.
#
# An earlier draft added "Each message you send performs exactly one of these
# four acts." A smoke test showed it doing real damage: the buyer opened with
# a proposal instead of a question, and the opening question is the whole
# reason this lab exists. 14 of the reference run's 18 `free` openings were
# "what's your asking price?", which a reader forced to choose among four acts
# read as `refuse`, ending the episode on turn one. The notes elide this tail
# with "...", but the reference run having produced those questions is
# evidence that the original did not forbid them. So the constraint is gone.
#
# It only ever bound `free`: in the other two conditions the format paragraph
# already forces an act. That asymmetry is the measurement, not a defect.
COMMON = (" Four acts are available: propose (offer a price), accept-proposal (agree to "
          "the other side's last price, which ends the negotiation with a deal), "
          "reject-proposal (decline the last price and keep negotiating), refuse (leave "
          "the negotiation for good, no deal). The negotiation ends after [[turn_limit]] "
          "messages counting both sides; if no deal has been agreed by then, there is no "
          "deal.")

# --- the one paragraph that differs ------------------------------------

# Printed in full in the lecture notes; transcribed unchanged.
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
# whole claim, so the context has to be there. In `tagged` the tag is already
# read by a regex and this is asked only for the price inside a propose.
#
# Two decisions in the tail, both of which the report has to own:
#
#   * the reader is made to choose one of the four even when the message is
#     none of them. FIPA has query-ref and cfp for "what are you asking?";
#     this lab's vocabulary does not, and forcing the choice is what exposes
#     that gap. The alternative -- letting the reader answer "none" and
#     counting a format error -- would hide the gap inside a different column.
#   * `price` is defined as the amount the last message itself puts forward,
#     null otherwise. The reference run's misreads were exactly this
#     confusion: "50 is above my budget, I can go up to 40" read as propose
#     50, the other side's number rather than the speaker's own.
READER_SYSTEM = ("You are an observer reading a price negotiation between a buyer and a "
                 "seller. Label the LAST message only. Reply with exactly one JSON object "
                 'and nothing else: {"performative": "propose" | "accept-proposal" | '
                 '"reject-proposal" | "refuse", "price": <whole number or null>}. '
                 "Set price to the amount the last message itself puts forward, and to "
                 "null when the last message names no amount of its own. Every message "
                 "must be labelled with one of the four acts; if the last message is none "
                 "of them, choose the one closest to what it does.")


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
    """Refuse to spend live calls on an unfinished prompt."""
    named = [("ROLE[buyer]", ROLE["buyer"]), ("ROLE[seller]", ROLE["seller"]),
             ("COMMON", COMMON), ("READER_SYSTEM", READER_SYSTEM)]
    named += [(f"FORMAT[{k}]", v) for k, v in FORMAT.items()]
    unfilled = [name for name, text in named if TODO in text]
    if unfilled:
        raise SystemExit("agents.py still has unfinished prompts: " + ", ".join(unfilled))
