"""Grounded bargaining guidance shared by all three message formats."""
from collections.abc import Mapping


NEGOTIATION_GUIDANCE = (
    'Bargain like a person, not a limit-checking script. '
    'Support your price with a specific supplied item fact or your own circumstance. '
    'Respond to the other party\'s latest argument, not just its number. '
    'When rejecting a price but willing to continue, consider a reasoned counteroffer '
    'using propose rather than repeatedly sending rejection without an alternative. '
    'Use reject-proposal when you are only declining without a new price. '
    'If the other party offers a useful convenience or makes a convincing argument, '
    'consider a concession and explain what changed your position. '
    'You may revise your asking or offered price, even upward, with a concrete reason; '
    'an asking price is not the same as your fixed private limit. '
    'Do not invent product features, defects, market prices, other buyers, or deadlines. '
    'Use only supplied facts, your own circumstances, and information actually stated '
    'by the other party. Do not claim to know its unstated private circumstances. '
    'Do not promise payment, pickup, warranty, or delivery terms that contradict the setup. '
    'Keep the supplied normal limit, 20-percent exceptional boundary, and quota rules unchanged. '
    'Concessions and discretion are optional; do not force a deal. '
    'If you accept the other party\'s last proposal, choose accept-proposal; '
    'if you suggest a different price, choose propose, even if you say you agree. '
    'If you judge further bargaining pointless, consider refuse rather than endless repetition. '
    'Your short Korean Reason must summarize the concrete bargaining reason, not merely '
    'repeat that a price is above or below a limit. '
    'For free and tagged, include the bargaining argument in the English sentence too. '
    'For structured, express the concrete argument in content.reason; do not add a message field. '
)


def context_paragraph(role: str, scenario: Mapping[str, object]) -> str:
    if role not in ('buyer', 'seller'):
        raise ValueError('unknown negotiation role')
    # Validate both private lists, but serialize only the caller's list.
    for key in ('public_facts', 'buyer_context', 'seller_context'):
        values = scenario.get(key)
        if not isinstance(values, list) or not values or not all(
            isinstance(value, str) and value.strip() for value in values
        ):
            raise ValueError(f'{key} must be a nonempty list of nonempty strings')
    common = '\n'.join('- ' + value for value in scenario['public_facts'])
    own = '\n'.join('- ' + value for value in scenario[role + '_context'])
    return ('\nShared item facts (known to both parties):\n' + common
            + '\nYour own private circumstances (not automatically shared):\n' + own
            + '\nBargaining guidance:\n' + NEGOTIATION_GUIDANCE + '\n')
