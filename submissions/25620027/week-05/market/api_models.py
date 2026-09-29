"""Typed contracts shared by the local runner and server admin surface."""

from market.models import Condition, FrozenModel, NegotiationId, Scenario


class OpenNegotiation(FrozenModel):
    """Admin request that creates one isolated experiment episode."""

    scenario: Scenario
    condition: Condition


class OpenedNegotiation(FrozenModel):
    """Explicit handle and party tokens returned only to the runner."""

    negotiation_id: NegotiationId
    buyer_token: str
    seller_token: str


class NegotiationPath(FrozenModel):
    """Typed Starlette path parameters for one negotiation."""

    negotiation_id: str
