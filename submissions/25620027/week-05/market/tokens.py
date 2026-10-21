"""HMAC-signed bearer grants for negotiation-scoped authority."""

from __future__ import annotations

import base64
import hashlib
import hmac

from pydantic import ValidationError

from market.models import Grant

MIN_SECRET_BYTES = 16


class InvalidTokenError(ValueError):
    """A bearer token cannot be authenticated or parsed."""


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    try:
        return base64.urlsafe_b64decode(f"{raw}{padding}")
    except (ValueError, UnicodeEncodeError) as exc:
        message = "token payload is not base64url"
        raise InvalidTokenError(message) from exc


class TokenCodec:
    """Mint and verify compact HMAC grants without exposing claims to the model."""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < MIN_SECRET_BYTES:
            message = "token secret must contain at least 16 bytes"
            raise InvalidTokenError(message)
        self._secret: bytes = secret

    def mint(self, grant: Grant) -> str:
        """Return a signed token for one immutable grant."""
        payload = grant.model_dump_json().encode("utf-8")
        signature = hmac.digest(self._secret, payload, hashlib.sha256)
        return f"{_encode(payload)}.{_encode(signature)}"

    def decode(self, token: str) -> Grant:
        """Authenticate a token and parse its typed grant."""
        try:
            payload_part, signature_part = token.split(".", maxsplit=1)
        except ValueError as exc:
            message = "token must contain payload and signature"
            raise InvalidTokenError(message) from exc
        payload = _decode(payload_part)
        signature = _decode(signature_part)
        expected = hmac.digest(self._secret, payload, hashlib.sha256)
        if not hmac.compare_digest(signature, expected):
            message = "token signature is invalid"
            raise InvalidTokenError(message)
        try:
            return Grant.model_validate_json(payload)
        except ValidationError as exc:
            message = "token grant is invalid"
            raise InvalidTokenError(message) from exc
