from __future__ import annotations

from typeset import Condition

COMMON_RULES = """You are taking part in a two-party price negotiation over one used good.
Only four acts are allowed:
- propose: offer an integer price and keep the negotiation open.
- accept-proposal: accept the counterpart's last proposed price. This closes a deal.
- reject-proposal: reject the last offer and continue talking.
- refuse: walk away. This closes the negotiation with no deal.

Rules:
- Speak only as your role. Never reveal the private limit number as a labelled field.
- Stay inside your private limit. Buyer must not accept or propose above budget. Seller must not accept or propose below reserve.
- One message per turn. Do not write analysis, chain-of-thought, or a second message.
- Use integer prices only.
"""

BUYER_ROLE = """You are the buyer.
Item: {item}
Private maximum you can pay (budget): {limit}
You speak first. Try to obtain the item at a price you can afford.
If the seller's price is above budget and will not come down, refuse.
"""

SELLER_ROLE = """You are the seller.
Item: {item}
Private minimum you will accept (reserve): {limit}
The buyer speaks first. Try to sell at a price at or above reserve.
If the buyer's price is below reserve and will not come up, refuse.
"""

FORMAT_PARAGRAPHS: dict[Condition, str] = {
    "free": """Message format:
Write one plain English sentence. Do not use tags, JSON, labels, markdown, or the words performative / accept-proposal / reject-proposal / refuse as a heading.""",
    "tagged": """Message format:
Start the line with exactly one tag in square brackets, then one English sentence.
Allowed tags: [propose] [accept-proposal] [reject-proposal] [refuse]
Example: [propose] I can do 30 for this item.""",
    "structured": """Message format:
Reply with a single JSON object and nothing else. No markdown fences.
Schema:
{"performative":"propose"|"accept-proposal"|"reject-proposal"|"refuse","content":{"price":<int>}}
price is required when performative is propose. For accept-proposal put the agreed price.""",
}

READER_PROMPT = """You label one negotiation utterance.
Allowed performatives: propose, accept-proposal, reject-proposal, refuse.
Return ONLY a JSON object, no markdown:
{"performative":"<one of the four or null>","price":<integer or null>}

price is the price THE SPEAKER OF THE LAST MESSAGE is offering or agreeing to.
If the last message is not one of the four acts, return {"performative":null,"price":null}.
If several numbers appear, pick the speaker's own offer, not the counterpart's quoted number.
"""


def role_prompt(role: str, item: str, limit: int) -> str:
    body = BUYER_ROLE if role == "buyer" else SELLER_ROLE
    return COMMON_RULES + "\n" + body.format(item=item, limit=limit)


def system_prompt(role: str, item: str, limit: int, condition: Condition) -> str:
    return role_prompt(role, item, limit) + "\n" + FORMAT_PARAGRAPHS[condition]


def format_paragraph(condition: Condition) -> str:
    return FORMAT_PARAGRAPHS[condition]
