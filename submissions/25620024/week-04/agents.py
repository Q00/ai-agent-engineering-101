"""Week 04 — agents.py: buyer/seller/broker system prompts, one message
format per condition (free/tagged/structured).

Base FIPA-ACL lab has two agents (buyer, seller). This submission adds a
third role, broker, sitting between them (own design, see REPORT.md):
- every ordinary message (propose/reject-proposal) is relayed as-is --
  a mediator doesn't rewrite routine messages, so this needs no model call.
- on deadlock (two consecutive reject-proposals), the broker LLM is asked
  to phrase one compromise (propose) message -- this is the one place a
  broker's judgment actually earns its own model call.
- on accept-proposal, the broker checks the deal price against BOTH
  private limits (it knows both, unlike buyer/seller) with plain code, not
  a model call -- a number comparison should not be left to an LLM to get
  right (see week 03's parse_bid: verification with a knowable ground
  truth belongs in code).
- the broker earns a fixed commission on any deal it closes; tracked as a
  metric, not something it reasons about.
"""
from model import Meter, call_model

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")
COMMISSION_PCT = 5

ROLE = {
    "buyer": (
        "You are a procurement officer for a government ministry, negotiating "
        "the construction of {item} through a broker. Your maximum budget is "
        "{budget} (unit: 100M KRW). You must never state this number directly "
        "to the seller. You want the lowest price that still gets the project "
        "built. You open the negotiation."
    ),
    "seller": (
        "You are a representative of a construction company, negotiating to "
        "build {item} through a broker. Your reserve price is {reserve} (unit: "
        "100M KRW) -- the lowest you can accept without a loss. You must never "
        "state this number directly to the buyer. You want the highest price "
        "you can get."
    ),
}

COMMON = (
    " Four acts are available: propose (offer a price), accept-proposal "
    "(agree to the other side's last price, which ends the negotiation with "
    "a deal), reject-proposal (decline the last price and keep negotiating), "
    "refuse (leave the negotiation for good, no deal)."
)

FORMAT = {
    "free": " Write your message as one or two plain English sentences.",
    "tagged": (
        " Start your message with exactly one performative tag in "
        "parentheses, one of (propose), (accept-proposal), (reject-proposal), "
        "(refuse), then write one plain English sentence."
    ),
    "structured": (
        ' Reply with exactly one JSON object and nothing else: '
        '{"performative": "propose" | "accept-proposal" | "reject-proposal" | '
        '"refuse", "content": {"price": <whole number or null>}}.'
    ),
}

# The broker only ever generates one kind of message (a mediating propose),
# so its prompt is built directly, not through ROLE/COMMON like buyer/seller.
BROKER_MEDIATE_SYSTEM = (
    "You are a broker mediating a price negotiation for {item}. You know "
    "BOTH private limits, unlike the two parties: the buyer's maximum "
    "budget is {budget} and the seller's minimum reserve is {reserve} "
    "(unit: 100M KRW each). The two sides are deadlocked -- they have just "
    "rejected each other's prices. Propose ONE fair compromise price "
    "strictly between {reserve} and {budget} to break the deadlock."
    "{format_instruction}"
)


def system_prompt(role, scenario, condition):
    return (ROLE[role].format(item=scenario["item"], budget=scenario["budget"],
                               reserve=scenario["reserve"])
            + COMMON + FORMAT[condition])


def broker_mediate_system(scenario, condition):
    return BROKER_MEDIATE_SYSTEM.format(
        item=scenario["item"], budget=scenario["budget"], reserve=scenario["reserve"],
        format_instruction=FORMAT[condition])


def broker_mediate(scenario, condition, meter: Meter) -> str:
    """One model call: broker proposes a compromise price. Used only when
    buyer and seller are deadlocked (see negotiate.py)."""
    return call_model(broker_mediate_system(scenario, condition),
                       "Both sides just rejected. Propose your compromise now.", meter)


def broker_commission(price: int) -> float:
    return round(price * COMMISSION_PCT / 100, 2)


if __name__ == "__main__":
    # smallest test: buyer opens, seller replies once, free format -- same
    # check as before, now via the ROLE/COMMON/FORMAT builder instead of
    # separate BUYER_SYSTEM/SELLER_SYSTEM templates.
    from model import call_conversation

    scenario = {"id": 1, "item": "Regional edge data center construction",
                "reserve": 300, "budget": 380}
    m = Meter()
    opening = call_conversation(
        system_prompt("buyer", scenario, "free"),
        [{"role": "user", "content": "Begin the negotiation with your opening move."}], m)
    print(f"[buyer] {opening}")
    reply = call_conversation(
        system_prompt("seller", scenario, "free"),
        [{"role": "user", "content": opening}], m)
    print(f"[seller] {reply}")
    print(f"tokens={m.tokens} calls={m.calls}")
