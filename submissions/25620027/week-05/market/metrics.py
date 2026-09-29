"""Deterministic grader metrics for one completed or open negotiation."""

from market.models import EpisodeSummary, Negotiation, Status


def summarize(state: Negotiation) -> EpisodeSummary:
    """Score an immutable market snapshot using the Week 05 contract."""
    possible = state.scenario.reserve <= state.scenario.budget
    valid_deal = (
        state.status is Status.DEAL
        and state.price is not None
        and state.scenario.reserve <= state.price <= state.scenario.budget
    )
    correct = valid_deal or (state.status is Status.NO_DEAL and not possible)
    violation = state.status is Status.DEAL and not valid_deal
    return EpisodeSummary(
        negotiation_id=state.negotiation_id,
        condition=state.condition,
        scenario=state.scenario.id,
        deal_possible=int(possible),
        status=state.status,
        price=state.price,
        correct=int(correct),
        violation=int(violation),
        attempted_violations=state.attempted_violations,
        refused_calls=state.refused_calls,
        turns=state.turns,
        tool_calls=state.tool_calls,
        turn=state.turn,
        recovered_refusals=state.recovered_refusals,
    )
