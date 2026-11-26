import pytest

from market.models import Condition, Grant, NegotiationId, Role
from market.tokens import InvalidTokenError, TokenCodec


def test_signed_token_round_trip_and_tamper_rejection() -> None:
    codec = TokenCodec(b"test-secret-with-enough-entropy")
    grant = Grant(
        subject="buyer-1",
        role=Role.BUYER,
        negotiation_id=NegotiationId("neg-1"),
        limit=150,
        condition=Condition.SERVER_INJECT,
        policy_id="mirror-v1",
        nonce="nonce-1",
    )

    token = codec.mint(grant)

    assert codec.decode(token) == grant
    with pytest.raises(InvalidTokenError, match="signature"):
        codec.decode(f"{token[:-1]}A")
