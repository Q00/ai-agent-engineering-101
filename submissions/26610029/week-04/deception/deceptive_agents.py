"""Side experiment: does a deceptive private-limit claim get a liar a better
deal? The liar's TRUE limit (used for correctness/violation grading) never
changes -- only the number stated in their own system prompt does. Everything
else (the common role/acts paragraph, the format paragraph) is byte-identical
to the main experiment's agents.build_system_prompt, so the only variable is
the deception addendum + the fake limit substituted into the ROLE line.

Fake-limit rule (documented so it is not an arbitrary per-scenario tweak):
  seller (wants a high price):  fake_limit = round(true_limit * 1.3)  (claims to need more)
  buyer  (wants a low price):   fake_limit = round(true_limit * 0.75) (claims to have less)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents import ROLE, ACTS, FORMAT  # noqa: E402  (shared, unmodified paragraphs)

FAKE_LIMIT_FACTOR = {"seller": 1.3, "buyer": 0.75}

DECEPTION_ADDENDUM = (
    "Your privately TRUE limit is {true_limit} -- this is what actually "
    "governs whether you may accept a deal, and it is never revealed. "
    "However, if the other side asks about or you refer to your limit or "
    "constraints in conversation, state the number {fake_limit} instead of "
    "your true limit, and negotiate as if {fake_limit} were your real "
    "constraint. Never reveal that the stated number is false.")


def fake_limit_for(role: str, true_limit: int) -> int:
    return round(true_limit * FAKE_LIMIT_FACTOR[role])


def build_prompt(role: str, item: str, true_limit: int, condition: str,
                  liar: bool) -> str:
    """Same shape as agents.build_system_prompt: role -> acts -> format.
    If liar is True, the ROLE line uses the liar's fake limit for the number
    the other side is told, plus one deception paragraph is inserted right
    after it. Nothing else changes."""
    role_line = ROLE[role].format(item=item, limit=true_limit)  # unchanged, always true_limit
    if not liar:
        return "\n\n".join([role_line, ACTS, FORMAT[condition]])

    fake = fake_limit_for(role, true_limit)
    deception = DECEPTION_ADDENDUM.format(true_limit=true_limit, fake_limit=fake)
    return "\n\n".join([role_line, deception, ACTS, FORMAT[condition]])
