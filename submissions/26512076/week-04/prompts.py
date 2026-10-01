COMMON_RULES = """
You are negotiating the price of {item}.

Four actions are allowed:
- propose: offer a price.
- accept-proposal: accept the other agent's last proposed price and end with a deal.
- reject-proposal: reject the offer and continue negotiating.
- refuse: leave the negotiation and end with no deal.

Keep each response concise.
"""

BUYER_ROLE = """
You are the buyer.
Your private maximum budget is {budget}.
Never accept a price above your budget.
Do not reveal your budget directly.
"""

SELLER_ROLE = """
You are the seller.
Your private minimum reserve price is {reserve}.
Never accept a price below your reserve.
Do not reveal your reserve directly.
"""

FORMAT_PROMPTS = {
    "free": """
Write one plain English negotiation message.
Do not use action tags or JSON.
""",

    "tagged": """
Write exactly one performative tag in parentheses at the beginning,
followed by one plain English negotiation message.
Use one of: (propose), (accept-proposal), (reject-proposal), (refuse).
""",

    "structured": """
Return exactly one JSON object and no additional text:
{"performative": "propose", "content": {"price": 40}}

The performative must be one of:
propose, accept-proposal, reject-proposal, refuse.
Use an integer price for propose. Otherwise, price may be null.
"""
}


def build_buyer_prompt(item, budget, condition):
    return (
        COMMON_RULES.format(item=item)
        + BUYER_ROLE.format(budget=budget)
        + FORMAT_PROMPTS[condition]
    )


def build_seller_prompt(item, reserve, condition):
    return (
        COMMON_RULES.format(item=item)
        + SELLER_ROLE.format(reserve=reserve)
        + FORMAT_PROMPTS[condition]
    )