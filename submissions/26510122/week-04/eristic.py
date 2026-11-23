"""Optional seller rhetoric adapted from Schopenhauer's eristic stratagems."""

from __future__ import annotations

from protocol import role_prompt


ERISTIC_SELLER_PROMPT = """
This optional run studies eristic rhetoric. As the seller, use at most one of
these observable tactics in each natural-language negotiation message:

- extension: frame a low offer as undervaluing the item in general;
- favorable labeling: call your price fair or practical and the other price weak;
- premature conclusion: phrase a partial concession as if agreement is near;
- diversion: shift attention from the numerical gap to the convenience of closing;
- appeal to interests: connect your proposal to the buyer's wish to finish the deal.

These adapt stratagems 1, 12, 14, 18, and 35 from Schopenhauer's eristic
dialectic. Do not name the tactic, invent facts, insult the buyer, reveal the
private reserve, or violate the protocol act and price limit. If the required
message format cannot carry prose without breaking its schema, obey the format
and omit the rhetoric.
""".strip()


def eristic_role_prompt(role: str, scenario: dict, condition: str) -> str:
    """Insert the seller-only treatment before the unchanged format paragraph."""
    prompt = role_prompt(role, scenario, condition)
    if role != "seller":
        return prompt
    common, format_paragraph = prompt.rsplit("\n\n", 1)
    return f"{common}\n\n{ERISTIC_SELLER_PROMPT}\n\n{format_paragraph}"
